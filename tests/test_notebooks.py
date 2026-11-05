"""Helper functions inside the Fabric notebooks (notebooks/*.py).

The notebooks run in Fabric, so only the named functions/constants are loaded here (see conftest.load_notebook).
Pure-Python helpers run everywhere; Spark helpers use a local Spark 3.5 + Delta 3.2 session (skipped without Java).
"""
import datetime as dt, os, shutil, types
import pytest
import requests
from conftest import load_notebook, requires_spark

BRONZE, SILVER, GOLD = "01_bronze_ingest.py", "02_silver_transform.py", "03_gold_aggregate.py"


# ================================================================ Gold: US holiday calendar (pure Python)
@pytest.fixture(scope="module")
def cal():
    return load_notebook(GOLD, ["nth_weekday", "last_weekday", "us_holidays"], {"dt": dt})


@pytest.mark.parametrize("args,expected", [
    ((2024, 1, 0, 3), dt.date(2024, 1, 15)),    # MLK Day: 3rd Monday of January
    ((2024, 11, 3, 4), dt.date(2024, 11, 28)),  # Thanksgiving: 4th Thursday of November
    ((2024, 9, 0, 1), dt.date(2024, 9, 2)),     # Labor Day: 1st Monday of September
    ((2024, 4, 0, 1), dt.date(2024, 4, 1)),     # month that starts on the weekday itself
])
def test_nth_weekday(cal, args, expected):
    assert cal["nth_weekday"](*args) == expected


@pytest.mark.parametrize("args,expected", [
    ((2024, 5, 0), dt.date(2024, 5, 27)),       # Memorial Day: last Monday of May
    ((2024, 12, 1), dt.date(2024, 12, 31)),     # December branch: last Tuesday of 2024
    ((2024, 3, 6), dt.date(2024, 3, 31)),       # month ending on the weekday itself
])
def test_last_weekday(cal, args, expected):
    assert cal["last_weekday"](*args) == expected


def test_us_holidays_2024(cal):
    h = cal["us_holidays"](2024)
    assert len(h) == 11
    assert h[dt.date(2024, 12, 25)] == "Christmas Day" and h[dt.date(2024, 7, 4)] == "Independence Day"
    assert h[dt.date(2024, 2, 19)] == "Presidents' Day" and h[dt.date(2024, 10, 14)] == "Columbus Day"
    assert h[dt.date(2024, 11, 28)] == "Thanksgiving Day"


# ================================================================ Bronze: download (pure Python, no network)
class Stream:
    def __init__(self, status, chunks=(b"abc", b"def")):
        self.status_code, self.chunks = status, chunks

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(str(self.status_code))

    def iter_content(self, chunk_size):
        return iter(self.chunks)


@pytest.fixture
def download(tmp_path):
    """Load download() with /tmp redirected into pytest's tmp_path and a scripted HTTP response."""
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    redirect = lambda p: str(scratch / os.path.basename(p)) if str(p).startswith("/tmp/") else p
    state = {"response": Stream(200), "urls": []}
    fake_requests = types.SimpleNamespace(get=lambda url, **k: state["urls"].append((url, k)) or state["response"])
    fake_os = types.SimpleNamespace(path=os.path, makedirs=os.makedirs, remove=lambda p: os.remove(redirect(p)))
    fake_shutil = types.SimpleNamespace(copyfile=lambda a, b: shutil.copyfile(redirect(a), b))
    ns = load_notebook(BRONZE, ["HTTP_HEADERS", "download"], {
        "os": fake_os, "shutil": fake_shutil, "requests": fake_requests,
        "open": lambda p, *a, **k: open(redirect(p), *a, **k)})
    ns["state"], ns["dest"] = state, tmp_path / "landing" / "yellow" / "f.parquet"
    return ns


def test_download_writes_file(download):
    assert download["download"]("https://x/f.parquet", str(download["dest"])) == "downloaded"
    assert download["dest"].read_bytes() == b"abcdef"
    url, kwargs = download["state"]["urls"][0]
    assert kwargs["headers"]["User-Agent"].startswith("fabric-medallion-ingest") and kwargs["stream"] is True


def test_download_uses_cached_file(download):
    download["dest"].parent.mkdir(parents=True)
    download["dest"].write_bytes(b"already here")
    assert download["download"]("https://x/f.parquet", str(download["dest"])) == "cached"
    assert download["state"]["urls"] == []


@pytest.mark.parametrize("status", [403, 404])
def test_download_reports_unpublished_files(download, status):
    download["state"]["response"] = Stream(status)
    assert download["download"]("https://x/f.parquet", str(download["dest"])) == "missing"
    assert not download["dest"].exists()


def test_download_raises_on_server_error(download):
    download["state"]["response"] = Stream(500)
    with pytest.raises(requests.HTTPError):
        download["download"]("https://x/f.parquet", str(download["dest"]))


# ================================================================ Spark helpers (local Spark + Delta)


def F():
    from pyspark.sql import functions
    return functions


@requires_spark
@pytest.mark.parametrize("nb", [BRONZE, SILVER])
def test_table_exists(spark, nb):
    ns = load_notebook(nb, ["table_exists"], {"spark": spark})
    spark.range(1).write.format("delta").mode("overwrite").saveAsTable("bronze.exists_check")
    assert ns["table_exists"]("bronze.exists_check") is True
    assert ns["table_exists"]("bronze.no_such_table") is False


@requires_spark
def test_conform_lowercases_and_widens_types(spark):
    ns = load_notebook(BRONZE, ["INT_COLS", "DBL_COLS", "conform"], {"F": F()})
    df = spark.createDataFrame([(1, 2.5, "2024-01-01 10:00:00", 7, "N")],
                               "VendorID long, Fare_Amount float, tpep_pickup_datetime string, PULocationID long, store_and_fwd_flag string")
    out = ns["conform"](df)
    assert dict(out.dtypes) == {"vendorid": "int", "fare_amount": "double", "tpep_pickup_datetime": "timestamp",
                                "pulocationid": "int", "store_and_fwd_flag": "string"}
    row = out.first()
    assert row.vendorid == 1 and row.fare_amount == 2.5 and row.tpep_pickup_datetime == dt.datetime(2024, 1, 1, 10)


@requires_spark
def test_log_file_appends_to_ingestion_log(spark):
    ts = dt.datetime(2024, 1, 1)
    ns = load_notebook(BRONZE, ["LOG_SCHEMA", "log_file"], {"spark": spark, "batch_id": "b1", "ingest_ts": ts,
                                                            "LOG_TABLE": "bronze.test_ingestion_log"})
    ns["log_file"]("yellow_tripdata_2024-01.parquet", "yellow", "2024-01", 10, "succeeded")
    ns["log_file"]("yellow_tripdata_2024-02.parquet", "yellow", "2024-02", 0, "failed")
    rows = spark.table("bronze.test_ingestion_log").orderBy("year_month").collect()
    assert [(r.year_month, r.row_count, r.status, r.batch_id) for r in rows] == [
        ("2024-01", 10, "succeeded", "b1"), ("2024-02", 0, "failed", "b1")]


@requires_spark
def test_write_partitions_only_replaces_processed_months(spark):
    helpers = ["table_exists", "write_partitions"]
    first = load_notebook(SILVER, helpers, {"spark": spark, "ym_in": "'2024-01', '2024-02'"})
    df = spark.createDataFrame([("2024-01", 1), ("2024-02", 2)], "ym string, v int")
    first["write_partitions"](df, "silver.test_parts", "ym")               # creates the table
    rerun = load_notebook(SILVER, helpers, {"spark": spark, "ym_in": "'2024-02'"})
    rerun["write_partitions"](spark.createDataFrame([("2024-02", 20)], "ym string, v int"), "silver.test_parts", "ym")
    assert sorted((r.ym, r.v) for r in spark.table("silver.test_parts").collect()) == [("2024-01", 1), ("2024-02", 20)]


@requires_spark
def test_standardize_maps_both_taxi_types_to_one_schema(spark):
    yellow = spark.createDataFrame(
        [(1, "2024-01-01 10:00:00", "2024-01-01 10:10:00", 1, 2.0, 1, "N", 10, 20, 1, 12.0, 15.0, "f.parquet", "2024-01", "b", "2024-02-01 00:00:00"),
         (1, "2023-12-31 23:00:00", "2023-12-31 23:10:00", 1, 2.0, 1, "N", 10, 20, 1, 12.0, 15.0, "f.parquet", "2023-12", "b", "2024-02-01 00:00:00")],
        "vendorid int, tpep_pickup_datetime string, tpep_dropoff_datetime string, passenger_count int, trip_distance double, "
        "ratecodeid int, store_and_fwd_flag string, pulocationid int, dolocationid int, payment_type int, fare_amount double, "
        "total_amount double, source_file string, source_year_month string, batch_id string, ingestion_timestamp string")
    yellow.write.format("delta").mode("overwrite").saveAsTable("bronze.test_yellow_raw")
    ns = load_notebook(SILVER, ["AMOUNT_COLS", "standardize"], {"spark": spark, "F": F(), "yms": ["2024-01"]})
    out = ns["standardize"]("bronze.test_yellow_raw", "yellow", "tpep")
    rows = out.collect()
    assert len(rows) == 1                                   # only the requested month
    r = rows[0]
    assert r.taxi_type == "yellow" and r.pickup_datetime == dt.datetime(2024, 1, 1, 10) and r.pu_location_id == 10
    assert r.trip_type is None and r.airport_fee is None    # columns yellow lacks become NULL
    assert out.columns[:4] == ["taxi_type", "vendor_id", "pickup_datetime", "dropoff_datetime"]


@requires_spark
def test_lookup_maps_codes_with_default(spark):
    ns = load_notebook(SILVER, ["lookup", "PAYMENT"], {"F": F()})
    df = spark.createDataFrame([(1,), (2,), (42,), (None,)], "payment_type int")
    got = [r.d for r in df.select(ns["lookup"](ns["PAYMENT"], "payment_type").alias("d")).collect()]
    assert got == ["Credit card", "Cash", "Unknown", "Unknown"]


@requires_spark
def test_quality_rules_classify_each_failure_and_drop_duplicates(spark):
    """Runs the Silver notebook's own dedupe + reject_reason expression on one row per rule."""
    cols = ("taxi_type string, vendor_id int, pickup_datetime timestamp, dropoff_datetime timestamp, trip_distance double, "
            "pu_location_id int, do_location_id int, fare_amount double, total_amount double, source_year_month string")
    t = lambda h, m=0, d=1, mo=1: dt.datetime(2024, mo, d, h, m)
    rows = [
        ("yellow", 1, t(10), t(10, 15), 2.0, 10, 20, 12.0, 15.0, "2024-01"),   # valid
        ("yellow", 1, t(10), t(10, 15), 2.0, 10, 20, 12.0, 15.0, "2024-01"),   # exact duplicate of the row above
        ("yellow", 2, None, t(10, 15), 2.0, 10, 20, 12.0, 15.0, "2024-01"),    # missing_timestamp
        ("yellow", 3, t(10, mo=2), t(10, 15, mo=2), 2.0, 10, 20, 12.0, 15.0, "2024-01"),  # pickup_outside_file_month
        ("yellow", 4, t(10), t(10), 2.0, 10, 20, 12.0, 15.0, "2024-01"),       # non_positive_duration
        ("yellow", 5, t(10, d=1), t(11, d=2), 2.0, 10, 20, 12.0, 15.0, "2024-01"),  # duration_over_24h
        ("yellow", 6, t(10), t(10, 15), 250.0, 10, 20, 12.0, 15.0, "2024-01"), # invalid_distance
        ("green", 7, t(10), t(10, 15), 2.0, 10, 20, -12.0, -15.0, "2024-01"),  # negative_amount (refund row)
        ("green", 8, t(10), t(10, 15), 2.0, 999, 20, 12.0, 15.0, "2024-01"),   # invalid_location
    ]
    trips = spark.createDataFrame(rows, cols)
    ns = load_notebook(SILVER, ["KEY", "dur", "loc_bad", "classified"], {"F": F(), "trips": trips})
    got = {r.vendor_id: r.reject_reason for r in ns["classified"].collect()}
    assert got == {1: None, 2: "missing_timestamp", 3: "pickup_outside_file_month", 4: "non_positive_duration",
                   5: "duration_over_24h", 6: "invalid_distance", 7: "negative_amount", 8: "invalid_location"}


@requires_spark
def test_gold_save_casts_doubles_to_exact_decimals(spark, capsys):
    ns = load_notebook(GOLD, ["save"], {"spark": spark, "F": F()})
    df = spark.createDataFrame([("a", 1.005, 3), ("b", 2.5, 4)], "k string, amount double, n int")
    ns["save"](df, "gold.test_save")
    saved = spark.table("gold.test_save")
    assert dict(saved.dtypes) == {"k": "string", "amount": "decimal(18,2)", "n": "int"}
    assert "gold.test_save: 2 rows" in capsys.readouterr().out
