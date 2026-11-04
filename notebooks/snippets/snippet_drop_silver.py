spark.sql("DROP TABLE IF EXISTS silver.taxi_trips")
for t in ["bronze.yellow_trips_raw", "bronze.green_trips_raw", "bronze.taxi_zones_raw", "silver.taxi_trips_rejected", "silver.taxi_zones"]:
    print(t, f"{spark.table(t).count():,}")
spark.table("bronze.ingestion_log").groupBy("taxi_type", "status").count().show()
