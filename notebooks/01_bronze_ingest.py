# %% [markdown]
# # 01 - Bronze ingest: NYC TLC yellow + green taxi trips
# Downloads the monthly TLC Parquet files into `Files/landing/nyc_taxi/`, then appends each file to a Bronze Delta table with audit columns.
#
# * One Bronze table per taxi type (`bronze.yellow_trips_raw`, `bronze.green_trips_raw`) - source columns as received, names lower-cased, numeric types widened so months with drifting types can be appended together.
# * `bronze.ingestion_log` is the watermark: files already logged as `succeeded` are skipped unless `force_reload = True`.
# * Re-loading a file first deletes its previous rows (keyed by `source_file`), so reruns never duplicate data.

# %% [parameters]
year = 2024
months = "1,2,3,4,5,6,7,8,9,10,11,12"
taxi_types = "yellow,green"
force_reload = False

# %%
import os, uuid, json, shutil, datetime
import requests
from pyspark.sql import functions as F

for s in ("bronze", "silver", "gold"):
    spark.sql(f"CREATE SCHEMA IF NOT EXISTS {s}")

# Bronze is write-heavy: skip V-Order, let Delta auto-compact small files
spark.conf.set("spark.sql.parquet.vorder.default", "false")
spark.conf.set("spark.databricks.delta.autoCompact.enabled", "true")

BASE_URL = "https://d37ci6vzurychx.cloudfront.net"
LANDING_LOCAL = "/lakehouse/default/Files/landing/nyc_taxi"   # mounted default lakehouse
LANDING_SPARK = "Files/landing/nyc_taxi"
HTTP_HEADERS = {"User-Agent": "fabric-medallion-ingest/1.0"}
LOG_TABLE = "bronze.ingestion_log"

year = int(year)
month_list = [int(m) for m in str(months).split(",") if m.strip()]
type_list = [t.strip().lower() for t in str(taxi_types).split(",") if t.strip()]
force_reload = str(force_reload).lower() in ("true", "1", "yes")
batch_id = str(uuid.uuid4())
ingest_ts = datetime.datetime.utcnow().replace(microsecond=0)
print(f"batch_id={batch_id} year={year} months={month_list} types={type_list} force_reload={force_reload}")

# %%
def table_exists(name):
    try:
        spark.table(name).limit(0).collect()
        return True
    except Exception:
        return False

def download(url, local_path):
    """Stage a public file into the lakehouse Files area. Returns cached | downloaded | missing."""
    if os.path.exists(local_path) and os.path.getsize(local_path) > 0:
        return "cached"
    tmp = f"/tmp/{os.path.basename(local_path)}"
    with requests.get(url, headers=HTTP_HEADERS, stream=True, timeout=600) as r:
        if r.status_code in (403, 404):   # CloudFront answers 403 for files not yet published
            return "missing"
        r.raise_for_status()
        with open(tmp, "wb") as f:
            for chunk in r.iter_content(chunk_size=8 << 20):
                f.write(chunk)
    os.makedirs(os.path.dirname(local_path), exist_ok=True)
    shutil.copyfile(tmp, local_path)
    os.remove(tmp)
    return "downloaded"

INT_COLS = {"vendorid", "passenger_count", "ratecodeid", "pulocationid", "dolocationid", "payment_type", "trip_type"}
DBL_COLS = {"trip_distance", "fare_amount", "extra", "mta_tax", "tip_amount", "tolls_amount", "improvement_surcharge",
            "total_amount", "congestion_surcharge", "airport_fee", "ehail_fee", "cbd_congestion_fee"}

def conform(df):
    """Lower-case column names and widen types so monthly files with drifting schemas append cleanly."""
    out = []
    for c in df.columns:
        lc = c.lower()
        col = F.col(f"`{c}`")
        if lc in INT_COLS:
            out.append(col.cast("int").alias(lc))
        elif lc in DBL_COLS:
            out.append(col.cast("double").alias(lc))
        elif lc.endswith("_datetime"):
            out.append(col.cast("timestamp").alias(lc))
        else:
            out.append(col.cast("string").alias(lc))
    return df.select(*out)

LOG_SCHEMA = "source_file string, taxi_type string, year_month string, row_count long, batch_id string, ingested_at timestamp, status string"

def log_file(fname, taxi_type, ym, n, status):
    (spark.createDataFrame([(fname, taxi_type, ym, n, batch_id, ingest_ts, status)], LOG_SCHEMA)
          .write.format("delta").mode("append").saveAsTable(LOG_TABLE))

# %%
done = set()
if table_exists(LOG_TABLE):
    done = {r.source_file for r in spark.table(LOG_TABLE).where("status = 'succeeded'").select("source_file").distinct().collect()}

results = []
for t in type_list:
    target = f"bronze.{t}_trips_raw"
    for m in month_list:
        ym = f"{year}-{m:02d}"
        fname = f"{t}_tripdata_{ym}.parquet"
        if fname in done and not force_reload:
            results.append({"file": fname, "status": "skipped (already loaded)"})
            continue
        dl = download(f"{BASE_URL}/trip-data/{fname}", f"{LANDING_LOCAL}/{t}/year={year}/{fname}")
        if dl == "missing":
            print(f"WARN: {fname} is not published yet - skipping")
            results.append({"file": fname, "status": "not published"})
            continue
        df = (conform(spark.read.parquet(f"{LANDING_SPARK}/{t}/year={year}/{fname}"))
              .withColumn("source_file", F.lit(fname))
              .withColumn("source_year_month", F.lit(ym))
              .withColumn("batch_id", F.lit(batch_id))
              .withColumn("ingestion_timestamp", F.lit(ingest_ts).cast("timestamp"))
              .withColumn("ingestion_date", F.to_date("ingestion_timestamp")))
        n = df.count()
        if n == 0:
            log_file(fname, t, ym, 0, "failed")
            raise ValueError(f"{fname} contains 0 rows")
        if table_exists(target):
            spark.sql(f"DELETE FROM {target} WHERE source_file = '{fname}'")
        (df.write.format("delta").mode("append")
           .option("mergeSchema", "true")
           .partitionBy("ingestion_date")
           .saveAsTable(target))
        log_file(fname, t, ym, n, "succeeded")
        print(f"{fname}: {dl}, {n:,} rows -> {target}")
        results.append({"file": fname, "status": dl, "rows": n})

# %%
# Reference data: taxi zone lookup (small, always fully refreshed)
zone_local = f"{LANDING_LOCAL}/reference/taxi_zone_lookup.csv"
if force_reload and os.path.exists(zone_local):
    os.remove(zone_local)
if download(f"{BASE_URL}/misc/taxi_zone_lookup.csv", zone_local) == "missing":
    raise RuntimeError("taxi_zone_lookup.csv not reachable")
zones = spark.read.option("header", True).csv(f"{LANDING_SPARK}/reference/taxi_zone_lookup.csv")
zones = (zones.select(*[F.col(f"`{c}`").alias(c.lower()) for c in zones.columns])
              .withColumn("source_file", F.lit("taxi_zone_lookup.csv"))
              .withColumn("batch_id", F.lit(batch_id))
              .withColumn("ingestion_timestamp", F.lit(ingest_ts).cast("timestamp")))
zones.write.format("delta").mode("overwrite").option("overwriteSchema", "true").saveAsTable("bronze.taxi_zones_raw")
zone_n = spark.table("bronze.taxi_zones_raw").count()
print(f"bronze.taxi_zones_raw: {zone_n} rows")

# %%
# Validation: row counts per file and anomaly flag vs. the median month of the same taxi type
log = spark.table(LOG_TABLE).where("status = 'succeeded'")
latest = log.groupBy("source_file", "taxi_type", "year_month").agg(F.max_by("row_count", "ingested_at").alias("row_count"))
med = latest.groupBy("taxi_type").agg(F.percentile_approx("row_count", 0.5).alias("median_rows"))
check = (latest.join(med, "taxi_type")
               .withColumn("vs_median", F.round(F.col("row_count") / F.col("median_rows"), 2))
               .withColumn("anomaly", (F.col("vs_median") < 0.5) | (F.col("vs_median") > 2.0))
               .orderBy("taxi_type", "year_month"))
check.show(50, truncate=False)
anomalies = check.where("anomaly").count()
if anomalies:
    print(f"WARN: {anomalies} file(s) differ from the median month by more than 2x - review before trusting Silver")

summary = {"batch_id": batch_id, "files": results, "zone_rows": zone_n, "row_count_anomalies": anomalies}
print(json.dumps(summary, indent=2))
notebookutils.notebook.exit(json.dumps(summary))
