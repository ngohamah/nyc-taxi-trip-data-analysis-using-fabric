# %% [markdown]
# # 03 - Gold aggregate: star schema for Power BI
# Builds read-optimised Gold tables from `silver.taxi_trips`:
#
# | Table | Grain |
# |---|---|
# | `gold.dim_date` | one row per calendar day (with US federal holidays) |
# | `gold.dim_zone` | one row per TLC location id |
# | `gold.fact_trips_daily_zone` | date x taxi type x pickup zone x dropoff borough x payment type |
# | `gold.hourly_demand` | month x taxi type x weekday x hour x pickup borough |
# | `gold.monthly_kpis` | month x taxi type |
#
# Gold tables are small, so they are fully rebuilt each run.

# %%
import json, datetime as dt
from pyspark.sql import functions as F

# Gold is read-heavy: V-Order + Optimize Write for Direct Lake / SQL endpoint performance
spark.conf.set("spark.sql.parquet.vorder.default", "true")
spark.conf.set("spark.databricks.delta.optimizeWrite.enabled", "true")
spark.conf.set("spark.databricks.delta.optimizeWrite.binSize", "1g")
spark.conf.set("spark.sql.adaptive.enabled", "true")

def save(df, name):
    # Fixed-precision decimals (not floating point) so Direct Lake / Power BI totals are exact
    df = df.select(*[F.col(c).cast("decimal(18,2)").alias(c) if t == "double" else F.col(c) for c, t in df.dtypes])
    df.write.format("delta").mode("overwrite").option("overwriteSchema", "true").saveAsTable(name)
    print(f"{name}: {spark.table(name).count():,} rows")

s = spark.table("silver.taxi_trips")

# %%
# gold.dim_zone
save(spark.table("silver.taxi_zones").orderBy("location_id"), "gold.dim_zone")

# %%
# gold.dim_date - full calendar years covering the data, with US federal holidays
def nth_weekday(y, m, wd, n):          # wd: Monday = 0
    d = dt.date(y, m, 1)
    d += dt.timedelta((wd - d.weekday()) % 7)
    return d + dt.timedelta(weeks=n - 1)

def last_weekday(y, m, wd):
    d = (dt.date(y, m + 1, 1) if m < 12 else dt.date(y + 1, 1, 1)) - dt.timedelta(1)
    return d - dt.timedelta((d.weekday() - wd) % 7)

def us_holidays(y):
    return {dt.date(y, 1, 1): "New Year's Day", nth_weekday(y, 1, 0, 3): "Martin Luther King Jr. Day",
            nth_weekday(y, 2, 0, 3): "Presidents' Day", last_weekday(y, 5, 0): "Memorial Day",
            dt.date(y, 6, 19): "Juneteenth", dt.date(y, 7, 4): "Independence Day",
            nth_weekday(y, 9, 0, 1): "Labor Day", nth_weekday(y, 10, 0, 2): "Columbus Day",
            dt.date(y, 11, 11): "Veterans Day", nth_weekday(y, 11, 3, 4): "Thanksgiving Day",
            dt.date(y, 12, 25): "Christmas Day"}

b = s.agg(F.min("pickup_date").alias("lo"), F.max("pickup_date").alias("hi")).first()
holidays = {}
for y in range(b.lo.year, b.hi.year + 1):
    holidays.update(us_holidays(y))
rows, d = [], dt.date(b.lo.year, 1, 1)
while d <= dt.date(b.hi.year, 12, 31):
    rows.append((d, int(d.strftime("%Y%m%d")), d.year, (d.month - 1) // 3 + 1, d.month, d.strftime("%B"),
                 d.strftime("%b"), d.strftime("%Y-%m"), d.day, d.isoweekday(), d.strftime("%A"), d.strftime("%a"),
                 d.isoweekday() >= 6, d.isocalendar()[1], d in holidays, holidays.get(d)))
    d += dt.timedelta(1)
DATE_SCHEMA = ("date date, date_key int, year int, quarter int, month int, month_name string, month_short string, "
               "year_month string, day int, day_of_week int, day_name string, day_short string, is_weekend boolean, "
               "iso_week int, is_holiday boolean, holiday_name string")
save(spark.createDataFrame(rows, DATE_SCHEMA), "gold.dim_date")

# %%
# gold.fact_trips_daily_zone
card = F.col("payment_type") == 1
fact = (s.withColumn("do_borough", F.coalesce("do_borough", F.lit("Unknown")))
    .groupBy("pickup_date", "taxi_type", "pu_location_id", "do_borough", "payment_type_desc")
    .agg(F.count(F.lit(1)).alias("trip_count"),
         F.sum(F.coalesce("passenger_count", F.lit(0))).cast("long").alias("passenger_count"),
         F.round(F.sum("trip_distance"), 2).alias("total_distance_miles"),
         F.round(F.sum("trip_duration_min"), 2).alias("total_duration_min"),
         F.round(F.sum("fare_amount"), 2).alias("fare_amount"),
         F.round(F.sum("tip_amount"), 2).alias("tip_amount"),
         F.round(F.sum("tolls_amount"), 2).alias("tolls_amount"),
         F.round(F.sum("total_amount"), 2).alias("total_amount"),
         F.sum(card.cast("int")).alias("card_trip_count"),
         F.round(F.coalesce(F.sum(F.when(card, F.col("fare_amount"))), F.lit(0.0)), 2).alias("card_fare_amount"),
         F.round(F.coalesce(F.sum(F.when(card, F.col("tip_amount"))), F.lit(0.0)), 2).alias("card_tip_amount"),
         F.sum(F.col("is_airport_trip").cast("int")).alias("airport_trip_count"))
    .withColumn("date_key", F.date_format("pickup_date", "yyyyMMdd").cast("int"))
    .withColumn("year_month", F.date_format("pickup_date", "yyyy-MM"))
    .withColumnRenamed("payment_type_desc", "payment_type"))
save(fact, "gold.fact_trips_daily_zone")
spark.sql("OPTIMIZE gold.fact_trips_daily_zone ZORDER BY (date_key, pu_location_id)")

# %%
# gold.hourly_demand
hourly = (s.withColumn("pu_borough", F.coalesce("pu_borough", F.lit("Unknown")))
    .groupBy(F.col("pickup_year_month").alias("year_month"), "taxi_type", "pickup_day_of_week", "pickup_day_name",
             "pickup_hour", "pu_borough")
    .agg(F.count(F.lit(1)).alias("trip_count"),
         F.round(F.sum("total_amount"), 2).alias("total_amount"),
         F.round(F.sum("trip_distance"), 2).alias("total_distance_miles"),
         F.round(F.sum("trip_duration_min"), 2).alias("total_duration_min"),
         F.round(F.avg("fare_amount"), 2).alias("avg_fare"),
         F.round(F.avg("trip_duration_min"), 2).alias("avg_duration_min"))
    .withColumn("avg_speed_mph", F.round(F.col("total_distance_miles") / (F.col("total_duration_min") / 60), 2)))
save(hourly, "gold.hourly_demand")

# %%
# gold.monthly_kpis
monthly = (s.groupBy(F.col("pickup_year_month").alias("year_month"), "taxi_type")
    .agg(F.count(F.lit(1)).alias("trip_count"),
         F.round(F.sum("total_amount"), 2).alias("total_amount"),
         F.round(F.sum("fare_amount"), 2).alias("fare_amount"),
         F.round(F.avg("fare_amount"), 2).alias("avg_fare"),
         F.round(F.avg("trip_distance"), 2).alias("avg_distance_miles"),
         F.round(F.avg("trip_duration_min"), 2).alias("avg_duration_min"),
         F.sum(card.cast("int")).alias("card_trip_count"),
         F.sum(F.when(card, F.col("tip_amount"))).alias("_card_tip"),
         F.sum(F.when(card, F.col("fare_amount"))).alias("_card_fare"),
         F.sum(F.col("is_airport_trip").cast("int")).alias("airport_trip_count"))
    .withColumn("avg_tip_pct_card", F.round(F.col("_card_tip") / F.col("_card_fare") * 100, 2))
    .withColumn("card_share_pct", F.round(F.col("card_trip_count") / F.col("trip_count") * 100, 2))
    .withColumn("airport_share_pct", F.round(F.col("airport_trip_count") / F.col("trip_count") * 100, 2))
    .drop("_card_tip", "_card_fare")
    .orderBy("year_month", "taxi_type"))
save(monthly, "gold.monthly_kpis")

# %%
# Data quality tables for the report: rows per layer and rejections by reason (grain: month x taxi type [x reason])
key = lambda: F.concat_ws("|", "year_month", "taxi_type")
bronze_rows = (spark.table("bronze.ingestion_log").where("status = 'succeeded'")
    .groupBy("source_file", "taxi_type", "year_month").agg(F.max_by("row_count", "ingested_at").alias("bronze_rows"))
    .groupBy("year_month", "taxi_type").agg(F.sum("bronze_rows").alias("bronze_rows")))
silver_rows = s.groupBy(F.col("pickup_year_month").alias("year_month"), "taxi_type").agg(F.count(F.lit(1)).alias("silver_rows"))
rejections = (spark.table("silver.taxi_trips_rejected")
    .groupBy(F.col("source_year_month").alias("year_month"), "taxi_type", "reject_reason")
    .agg(F.count(F.lit(1)).alias("rejected_rows"))
    .withColumn("month_type_key", key()))
rejected_rows = rejections.groupBy("year_month", "taxi_type").agg(F.sum("rejected_rows").alias("rejected_rows"))
layers = (bronze_rows.join(silver_rows, ["year_month", "taxi_type"], "left")
    .join(rejected_rows, ["year_month", "taxi_type"], "left")
    .fillna(0, ["silver_rows", "rejected_rows"])
    .withColumn("duplicate_rows", F.col("bronze_rows") - F.col("silver_rows") - F.col("rejected_rows"))
    .withColumn("month_type_key", key())
    .orderBy("year_month", "taxi_type"))
save(layers, "gold.dq_layer_counts")
save(rejections, "gold.dq_rejections")
assert spark.table("gold.dq_layer_counts").where("duplicate_rows < 0").count() == 0, "Layer counts do not add up"

# %%
# Conformed slicer dimensions shared by every Gold table, so one Month / Borough / Taxi type slicer filters all report pages.
# Built from the union of values actually present, so no fact row is left without a match.
gt = lambda n: spark.table(f"gold.{n}")
months = (gt("dim_date").select("year_month", "year", "month", "month_name", "month_short").distinct()
          .unionByName(gt("hourly_demand").select("year_month").distinct()
                       .join(gt("dim_date").select("year_month").distinct(), "year_month", "left_anti")
                       .withColumn("year", F.substring("year_month", 1, 4).cast("int"))
                       .withColumn("month", F.substring("year_month", 6, 2).cast("int"))
                       .withColumn("month_name", F.lit(None).cast("string")).withColumn("month_short", F.lit(None).cast("string"))))
save(months.orderBy("year_month"), "gold.dim_month")
boroughs = (gt("dim_zone").select("borough").union(gt("hourly_demand").select(F.col("pu_borough").alias("borough")))
            .distinct().orderBy("borough"))
save(boroughs, "gold.dim_borough")
taxi_types = (gt("fact_trips_daily_zone").select("taxi_type").union(gt("hourly_demand").select("taxi_type"))
              .union(gt("dq_layer_counts").select("taxi_type")).distinct().orderBy("taxi_type"))
save(taxi_types, "gold.dim_taxi_type")

# %%
# Validation: Gold must reconcile with Silver
silver_n = s.count()
checks = {
    "silver_trips": silver_n,
    "fact_trips": spark.table("gold.fact_trips_daily_zone").agg(F.sum("trip_count")).first()[0],
    "hourly_trips": spark.table("gold.hourly_demand").agg(F.sum("trip_count")).first()[0],
    "monthly_trips": spark.table("gold.monthly_kpis").agg(F.sum("trip_count")).first()[0],
    "fact_dates_missing_from_dim_date": spark.table("gold.fact_trips_daily_zone").select("date_key").distinct()
        .join(spark.table("gold.dim_date"), "date_key", "left_anti").count(),
    "fact_zones_missing_from_dim_zone": spark.table("gold.fact_trips_daily_zone")
        .select(F.col("pu_location_id").alias("location_id")).distinct()
        .join(spark.table("gold.dim_zone"), "location_id", "left_anti").count(),
}
spark.table("gold.monthly_kpis").show(30, truncate=False)
print(json.dumps(checks, indent=2))
assert checks["fact_trips"] == checks["hourly_trips"] == checks["monthly_trips"] == silver_n, "Gold does not reconcile with Silver"
assert checks["fact_dates_missing_from_dim_date"] == 0 and checks["fact_zones_missing_from_dim_zone"] == 0, "Orphan keys in Gold"
notebookutils.notebook.exit(json.dumps(checks))
