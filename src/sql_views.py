"""Create analyst-friendly views in the dbo schema of the lakehouse SQL endpoint: python src/sql_views.py"""
import struct, subprocess
import mssql_python
import fabric

VIEWS = {
"vw_trips_daily_zone": """
SELECT f.pickup_date, d.[year], d.[month], d.month_name, d.year_month, d.day_name, d.is_weekend, d.is_holiday, d.holiday_name,
       f.taxi_type, z.borough AS pickup_borough, z.[zone] AS pickup_zone, z.service_zone AS pickup_service_zone,
       f.do_borough AS dropoff_borough, f.payment_type,
       f.trip_count, f.passenger_count, f.total_distance_miles, f.total_duration_min,
       f.fare_amount, f.tip_amount, f.tolls_amount, f.total_amount,
       f.card_trip_count, f.card_fare_amount, f.card_tip_amount, f.airport_trip_count
FROM gold.fact_trips_daily_zone AS f
JOIN gold.dim_date AS d ON d.date_key = f.date_key
LEFT JOIN gold.dim_zone AS z ON z.location_id = f.pu_location_id""",

"vw_zone_summary": """
SELECT z.location_id, z.borough AS pickup_borough, z.[zone] AS pickup_zone, z.service_zone,
       SUM(f.trip_count) AS trips,
       SUM(f.total_amount) AS revenue,
       CAST(100.0 * SUM(f.total_amount) / SUM(SUM(f.total_amount)) OVER () AS decimal(9,3)) AS revenue_contribution_pct,
       CAST(SUM(f.fare_amount) / NULLIF(SUM(f.trip_count), 0) AS decimal(9,2)) AS avg_fare,
       CAST(SUM(f.total_distance_miles) / NULLIF(SUM(f.trip_count), 0) AS decimal(9,2)) AS avg_distance_miles,
       CAST(100.0 * SUM(f.card_tip_amount) / NULLIF(SUM(f.card_fare_amount), 0) AS decimal(9,2)) AS card_tip_pct
FROM gold.fact_trips_daily_zone AS f
JOIN gold.dim_zone AS z ON z.location_id = f.pu_location_id
GROUP BY z.location_id, z.borough, z.[zone], z.service_zone""",

"vw_monthly_kpis": """
SELECT year_month, taxi_type, trip_count, total_amount AS revenue, fare_amount, avg_fare, avg_distance_miles,
       avg_duration_min, avg_tip_pct_card, card_share_pct, airport_share_pct
FROM gold.monthly_kpis""",

"vw_hourly_demand": """
SELECT year_month, taxi_type, pickup_day_of_week AS day_of_week_number, pickup_day_name AS day_of_week,
       pickup_hour AS [hour], pu_borough AS pickup_borough, trip_count, total_amount AS revenue,
       avg_fare, avg_duration_min, avg_speed_mph
FROM gold.hourly_demand""",

"vw_holiday_impact": """
WITH daily AS (
    SELECT f.pickup_date, d.day_of_week, d.day_name, d.is_holiday, d.holiday_name, SUM(f.trip_count) AS trips
    FROM gold.fact_trips_daily_zone AS f
    JOIN gold.dim_date AS d ON d.date_key = f.date_key
    GROUP BY f.pickup_date, d.day_of_week, d.day_name, d.is_holiday, d.holiday_name
), typical AS (
    -- 'Typical' = average non-holiday day with the same weekday across the whole of 2024
    SELECT day_of_week, AVG(CAST(trips AS float)) AS typical_trips
    FROM daily WHERE is_holiday = 0
    GROUP BY day_of_week
)
SELECT h.holiday_name, h.pickup_date AS holiday_date, h.day_name, h.trips AS holiday_trips,
       CAST(ROUND(t.typical_trips, 0) AS bigint) AS typical_same_weekday_trips,
       CAST(100.0 * (h.trips / t.typical_trips - 1) AS decimal(6,1)) AS vs_typical_pct
FROM daily AS h
JOIN typical AS t ON t.day_of_week = h.day_of_week
WHERE h.is_holiday = 1""",

"vw_pipeline_quality": """
SELECT year_month, taxi_type, bronze_rows, silver_rows, rejected_rows, duplicate_rows,
       CAST(100.0 * rejected_rows / NULLIF(bronze_rows, 0) AS decimal(6,3)) AS rejection_rate_pct,
       CAST(100.0 * silver_rows / NULLIF(bronze_rows, 0) AS decimal(6,3)) AS rows_kept_pct
FROM gold.dq_layer_counts""",
}


def connect():
    ws, lh = fabric.ids()
    server = fabric.call("GET", f"/workspaces/{ws}/lakehouses/{lh}")["properties"]["sqlEndpointProperties"]["connectionString"]
    token = subprocess.check_output("az account get-access-token --resource https://database.windows.net/ --query accessToken -o tsv",
                                    shell=True, text=True).strip().encode("utf-16-le")
    return mssql_python.connect(f"Server={server};Database={fabric.LAKEHOUSE};Encrypt=yes;",
                                attrs_before={1256: struct.pack(f"<I{len(token)}s", len(token), token)})


if __name__ == "__main__":
    conn = connect()
    cur = conn.cursor()
    for name, body in VIEWS.items():
        cur.execute(f"CREATE OR ALTER VIEW dbo.{name} AS {body}")
        conn.commit()
        n = cur.execute(f"SELECT COUNT(*) FROM dbo.{name}").fetchone()[0]
        print(f"dbo.{name:22s} {n:>10,} rows")
    print("\nCheck: dbo.vw_holiday_impact (lowest 3)")
    for r in cur.execute("SELECT TOP 3 holiday_name, day_name, holiday_trips, typical_same_weekday_trips, vs_typical_pct "
                         "FROM dbo.vw_holiday_impact ORDER BY vs_typical_pct").fetchall():
        print("  ", tuple(r))
    print("Check: dbo.vw_zone_summary (top 3 by revenue contribution)")
    for r in cur.execute("SELECT TOP 3 pickup_zone, trips, revenue, revenue_contribution_pct "
                         "FROM dbo.vw_zone_summary ORDER BY revenue_contribution_pct DESC").fetchall():
        print("  ", tuple(r))
    conn.close()
