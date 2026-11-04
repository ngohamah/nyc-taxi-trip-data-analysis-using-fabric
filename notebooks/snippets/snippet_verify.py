from pyspark.sql import functions as F
for t in ["silver.taxi_trips", "silver.taxi_trips_rejected", "gold.dim_date", "gold.dim_zone",
          "gold.fact_trips_daily_zone", "gold.hourly_demand", "gold.monthly_kpis"]:
    print(f"{t:32s} {spark.table(t).count():>12,}")
spark.table("silver.taxi_trips").groupBy("taxi_type").count().show()
spark.table("silver.taxi_trips_rejected").groupBy("reject_reason").count().orderBy(F.desc("count")).show(truncate=False)
spark.table("gold.monthly_kpis").where("taxi_type = 'yellow'").select("year_month", "trip_count", "total_amount", "avg_fare", "avg_tip_pct_card").show(12)
spark.table("gold.fact_trips_daily_zone").printSchema()
