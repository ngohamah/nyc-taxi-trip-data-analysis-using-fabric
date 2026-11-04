# %% [markdown]
# # 02 - Silver transform: cleaned, conformed taxi trips
# Reads `bronze.yellow_trips_raw` + `bronze.green_trips_raw`, conforms them to one schema, removes duplicates, applies quality rules and writes:
#
# * `silver.taxi_trips` - valid trips with derived columns and zone names, partitioned by `pickup_year_month`
# * `silver.taxi_trips_rejected` - failed rows with a `reject_reason` (nothing is dropped silently)
# * `silver.taxi_zones` - cleaned zone lookup
#
# Only the months in `months` are rewritten (partition-aware overwrite via `replaceWhere`).

# %% [parameters]
year = 2024
months = "1,2,3,4,5,6,7,8,9,10,11,12"

# %%
import json
from pyspark.sql import functions as F

# Silver is balanced read/write: V-Order on, adaptive execution
spark.conf.set("spark.sql.parquet.vorder.default", "true")
spark.conf.set("spark.sql.adaptive.enabled", "true")

year = int(year)
yms = [f"{year}-{int(m):02d}" for m in str(months).split(",") if m.strip()]
ym_in = ", ".join(f"'{ym}'" for ym in yms)
print("processing", yms)

def table_exists(name):
    try:
        spark.table(name).limit(0).collect()
        return True
    except Exception:
        return False

def write_partitions(df, table, part_col):
    """Overwrite only the processed months; create the table on first run."""
    w = df.write.format("delta").partitionBy(part_col)
    if table_exists(table):
        w.mode("overwrite").option("replaceWhere", f"{part_col} IN ({ym_in})").saveAsTable(table)
    else:
        w.mode("overwrite").saveAsTable(table)

# %%
# Zone lookup -> silver.taxi_zones
zones = (spark.table("bronze.taxi_zones_raw")
         .select(F.col("locationid").cast("int").alias("location_id"),
                 F.when(F.trim("borough").isin("N/A", "") | F.col("borough").isNull(), F.lit("Unknown"))
                  .otherwise(F.trim("borough")).alias("borough"),
                 F.coalesce(F.trim("zone"), F.lit("Unknown")).alias("zone"),
                 F.coalesce(F.trim("service_zone"), F.lit("Unknown")).alias("service_zone"))
         .where("location_id IS NOT NULL")
         .dropDuplicates(["location_id"]))
zones.write.format("delta").mode("overwrite").option("overwriteSchema", "true").saveAsTable("silver.taxi_zones")

# %%
AMOUNT_COLS = ["fare_amount", "extra", "mta_tax", "tip_amount", "tolls_amount", "improvement_surcharge",
               "congestion_surcharge", "airport_fee", "cbd_congestion_fee", "total_amount"]

def standardize(table, taxi_type, prefix):
    """Map one Bronze table onto the shared Silver schema; columns a taxi type lacks become NULL."""
    df = spark.table(table).where(F.col("source_year_month").isin(yms))
    def c(name, dtype):
        return F.col(name).cast(dtype) if name in df.columns else F.lit(None).cast(dtype)
    return df.select(
        F.lit(taxi_type).alias("taxi_type"),
        c("vendorid", "int").alias("vendor_id"),
        c(f"{prefix}_pickup_datetime", "timestamp").alias("pickup_datetime"),
        c(f"{prefix}_dropoff_datetime", "timestamp").alias("dropoff_datetime"),
        c("passenger_count", "int").alias("passenger_count"),
        c("trip_distance", "double").alias("trip_distance"),
        c("ratecodeid", "int").alias("ratecode_id"),
        c("store_and_fwd_flag", "string").alias("store_and_fwd_flag"),
        c("pulocationid", "int").alias("pu_location_id"),
        c("dolocationid", "int").alias("do_location_id"),
        c("payment_type", "int").alias("payment_type"),
        *[c(a, "double").alias(a) for a in AMOUNT_COLS],
        c("trip_type", "int").alias("trip_type"),
        "source_file", "source_year_month", "batch_id", "ingestion_timestamp")

parts = []
if table_exists("bronze.yellow_trips_raw"):
    parts.append(standardize("bronze.yellow_trips_raw", "yellow", "tpep"))
if table_exists("bronze.green_trips_raw"):
    parts.append(standardize("bronze.green_trips_raw", "green", "lpep"))
if not parts:
    raise RuntimeError("No Bronze trip tables found - run 01_bronze_ingest first")
trips = parts[0]
for p in parts[1:]:
    trips = trips.unionByName(p)

# %%
# Deduplicate (TLC has no trip id) and classify each row against the quality rules
KEY = ["taxi_type", "vendor_id", "pickup_datetime", "dropoff_datetime", "pu_location_id", "do_location_id", "total_amount"]
dur = (F.unix_timestamp("dropoff_datetime") - F.unix_timestamp("pickup_datetime")) / 60.0
loc_bad = lambda c: F.col(c).isNull() | ~F.col(c).between(1, 265)

classified = (trips.dropDuplicates(KEY)
    .withColumn("_dur", dur)
    .withColumn("reject_reason",
        F.when(F.col("pickup_datetime").isNull() | F.col("dropoff_datetime").isNull(), "missing_timestamp")
         .when(F.date_format("pickup_datetime", "yyyy-MM") != F.col("source_year_month"), "pickup_outside_file_month")
         .when(F.col("_dur") <= 0, "non_positive_duration")
         .when(F.col("_dur") > 24 * 60, "duration_over_24h")
         .when(F.col("trip_distance").isNull() | (F.col("trip_distance") < 0) | (F.col("trip_distance") > 200), "invalid_distance")
         .when((F.col("fare_amount") < 0) | (F.col("total_amount") < 0), "negative_amount")
         .when(loc_bad("pu_location_id") | loc_bad("do_location_id"), "invalid_location")))

# %%
def lookup(mapping, col, default="Unknown"):
    m = F.create_map(*[x for k, v in mapping.items() for x in (F.lit(k), F.lit(v))])
    return F.coalesce(m[F.col(col)], F.lit(default))

PAYMENT = {0: "Flex fare", 1: "Credit card", 2: "Cash", 3: "No charge", 4: "Dispute", 5: "Unknown", 6: "Voided trip"}
RATECODE = {1: "Standard", 2: "JFK", 3: "Newark", 4: "Nassau or Westchester", 5: "Negotiated fare", 6: "Group ride", 99: "Unknown"}
TRIP_TYPE = {1: "Street hail", 2: "Dispatch"}
AIRPORT_ZONES = [1, 132, 138]   # Newark, JFK, LaGuardia

pu = F.broadcast(zones.select(F.col("location_id").alias("pu_location_id"), F.col("borough").alias("pu_borough"),
                              F.col("zone").alias("pu_zone"), F.col("service_zone").alias("pu_service_zone")))
do = F.broadcast(zones.select(F.col("location_id").alias("do_location_id"), F.col("borough").alias("do_borough"),
                              F.col("zone").alias("do_zone"), F.col("service_zone").alias("do_service_zone")))

valid = (classified.where(F.col("reject_reason").isNull())
    .withColumn("trip_duration_min", F.round("_dur", 2))
    .withColumn("avg_speed_mph", F.when(F.col("_dur") > 0, F.round(F.col("trip_distance") / (F.col("_dur") / 60), 2)))
    .drop("reject_reason", "_dur")
    .join(pu, "pu_location_id", "left")
    .join(do, "do_location_id", "left")
    .withColumn("pickup_date", F.to_date("pickup_datetime"))
    .withColumn("pickup_year_month", F.date_format("pickup_datetime", "yyyy-MM"))
    .withColumn("pickup_hour", F.hour("pickup_datetime"))
    .withColumn("pickup_day_of_week", ((F.dayofweek("pickup_datetime") + 5) % 7) + 1)   # 1 = Monday
    .withColumn("pickup_day_name", F.date_format("pickup_datetime", "EEEE"))
    .withColumn("is_weekend", F.dayofweek("pickup_datetime").isin(1, 7))
    .withColumn("payment_type_desc", lookup(PAYMENT, "payment_type"))
    .withColumn("ratecode_desc", lookup(RATECODE, "ratecode_id"))
    .withColumn("trip_type_desc", F.when(F.col("taxi_type") == "yellow", F.lit("Not applicable"))
                                   .otherwise(lookup(TRIP_TYPE, "trip_type")))
    .withColumn("tip_pct", F.when((F.col("payment_type") == 1) & (F.col("fare_amount") > 0),
                                  F.round(F.col("tip_amount") / F.col("fare_amount") * 100, 2)))
    .withColumn("is_airport_trip", F.col("ratecode_id").isin(2, 3) | F.col("pu_location_id").isin(AIRPORT_ZONES)
                                   | F.col("do_location_id").isin(AIRPORT_ZONES))
    .withColumn("dq_passenger_count_missing", F.coalesce(F.col("passenger_count"), F.lit(0)) == 0)
    .withColumn("silver_processed_at", F.current_timestamp()))
# Delta collects min/max stats on the first 32 columns only: keep filter / Z-order columns up front
LEAD = ["pickup_year_month", "pickup_date", "taxi_type", "pu_location_id", "do_location_id", "pickup_datetime",
        "dropoff_datetime", "payment_type", "pickup_hour"]
valid = valid.select(*LEAD, *[c for c in valid.columns if c not in LEAD])

rejected = (classified.where(F.col("reject_reason").isNotNull())
    .withColumn("trip_duration_min", F.round("_dur", 2)).drop("_dur")
    .withColumn("silver_processed_at", F.current_timestamp()))

# %%
write_partitions(valid, "silver.taxi_trips", "pickup_year_month")
write_partitions(rejected, "silver.taxi_trips_rejected", "source_year_month")
spark.sql(f"OPTIMIZE silver.taxi_trips WHERE pickup_year_month IN ({ym_in}) ZORDER BY (pickup_date, pu_location_id)")

# %%
# Validation: before/after counts, duplicates removed, rejection rate per rule
raw_n = {r.taxi_type: r["count"] for r in trips.groupBy("taxi_type").count().collect()}
valid_n = {r.taxi_type: r["count"] for r in spark.table("silver.taxi_trips").where(f"pickup_year_month IN ({ym_in})").groupBy("taxi_type").count().collect()}
rej = spark.table("silver.taxi_trips_rejected").where(f"source_year_month IN ({ym_in})")
rej_n = {r.taxi_type: r["count"] for r in rej.groupBy("taxi_type").count().collect()}
rej.groupBy("taxi_type", "reject_reason").count().orderBy("taxi_type", F.desc("count")).show(truncate=False)

metrics = {}
for t in raw_n:
    v, r = valid_n.get(t, 0), rej_n.get(t, 0)
    metrics[t] = {"bronze_rows": raw_n[t], "duplicates_removed": raw_n[t] - v - r, "rejected": r, "valid": v,
                  "reject_rate_pct": round(100 * r / max(raw_n[t], 1), 2)}
    if v == 0:
        raise ValueError(f"No valid {t} rows written to Silver")
    if metrics[t]["reject_rate_pct"] > 10:
        print(f"WARN: {t} rejection rate {metrics[t]['reject_rate_pct']}% is above 10%")
print(json.dumps(metrics, indent=2))
notebookutils.notebook.exit(json.dumps(metrics))
