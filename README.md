# NYC Taxi Trips 2024: Medallion Lakehouse on Microsoft Fabric

[![CI](https://github.com/ngohamah/nyc-taxi-trip-data-analysis-using-fabric/actions/workflows/ci.yml/badge.svg)](https://github.com/ngohamah/nyc-taxi-trip-data-analysis-using-fabric/actions/workflows/ci.yml)
[![Coverage](https://raw.githubusercontent.com/ngohamah/nyc-taxi-trip-data-analysis-using-fabric/badges/coverage.svg)](https://github.com/ngohamah/nyc-taxi-trip-data-analysis-using-fabric/actions/workflows/ci.yml)
![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-3776AB)
![Spark 3.5 · Delta 3.2](https://img.shields.io/badge/Spark%203.5-Delta%20Lake%203.2-E25A1C)
![Microsoft Fabric](https://img.shields.io/badge/Microsoft%20Fabric-Direct%20Lake-0F6CBD)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)

An end-to-end analytics build on Microsoft Fabric. It ingests the 2024 NYC TLC yellow and green taxi trip records, cleans them through a Bronze → Silver → Gold medallion lakehouse, and presents them in a five-page Power BI report on a Direct Lake semantic model.

The scripts in this repository create the notebooks, run them, build the semantic model and report, and add SQL views, all through the Fabric and Power BI REST APIs.

## At a glance

| | |
|---|---|
| **Source data** | 24 monthly Parquet files (12 yellow + 12 green taxi, Jan–Dec 2024) plus the zone lookup: **709 MB** downloaded and ingested |
| **Records ingested** | **41,829,938** raw trip records in Bronze (41,169,720 yellow + 660,218 green) |
| **Records validated** | **41,077,632** valid trips in Silver (98.2%); **752,299** rejected rows kept with their reason; **7** exact duplicates removed |
| **Reconciliation** | Bronze = Silver + rejected + duplicates, to the row. Gold trip totals equal Silver exactly, and the Gold run fails otherwise |
| **Business value analysed** | **$1.18 billion** in fares, surcharges, tolls and tips |
| **Read-optimised Gold** | 41M trips pre-aggregated to **764,873** day × zone × type rows (**54× fewer**) and 21,487 weekday × hour rows, so every report query stays small |
| **Lakehouse** | 17 Delta tables across `bronze` / `silver` / `gold` schemas, plus 6 SQL views in `dbo` |
| **Semantic model** | Direct Lake: 9 tables, 11 relationships, 32 documented DAX measures |
| **Report** | 5 pages plus a hover-tooltip page, 95 visuals, slicers synced across pages |
| **Quality gates** | 183 automated tests, 99% coverage of the deployment code, lint and drift checks on every push ([CI](#tests-and-ci)) |

Figures come from the live lakehouse via [`tools/project_stats.py`](tools/project_stats.py), saved in [`docs/project_stats.json`](docs/project_stats.json). A test checks that they reconcile.

## Architecture

![Medallion architecture: NYC TLC files flow through Bronze, Silver and Gold in one Fabric lakehouse, then to a Direct Lake semantic model, the Power BI report and SQL views](docs/architecture.png)

- **One schema-enabled lakehouse** (`nyc_taxi_lakehouse`) holds the `bronze`, `silver` and `gold` schemas, plus the analyst views in `dbo`.
- **Each layer is a notebook** in [`notebooks/`](notebooks), deployed and run through the Fabric REST API by [`src/fabric.py`](src/fabric.py) or the pipeline in [`src/pipeline.py`](src/pipeline.py).
- **Nothing is lost between layers:** Bronze row counts, Silver valid rows and rejected rows (with their reasons) are kept in `gold.dq_layer_counts` and `gold.dq_rejections`, and the Data Quality report page shows them.

## Semantic model

![Semantic model relationship diagram: Taxi Trips, Hourly Demand and Pipeline Layers fact tables joined to Month, Borough, Taxi Type, Date and Pickup Zone dimensions](docs/model_relationships.png)

The Direct Lake model `NYC Taxi` reads the Gold tables straight from OneLake. Defined in [`src/semantic_model.py`](src/semantic_model.py); the TMDL is in [`powerbi/NYC Taxi.SemanticModel`](powerbi/NYC%20Taxi.SemanticModel).

- **Fact tables** (charcoal) hold the measures. *Taxi Trips* has trips, revenue, fares, tips and the holiday comparison; *Hourly Demand* feeds the weekday × hour heatmap; *Pipeline Layers* and *Rejections* feed the Data Quality page.
- **Dimension tables** (yellow) are the filters. *Month*, *Borough* and *Taxi Type* are shared by several fact tables, so one slicer filters every visual on every page. *Date* and *Pickup Zone* roll up to *Month* and *Borough*.
- **All relationships are one-to-many with single-direction filtering** (from the 1 side to the * side), which keeps totals predictable.
- The diagram is generated from the model definition by [`tools/make_diagrams.py`](tools/make_diagrams.py), so it can be redrawn whenever the model changes.

## Data quality

Data quality is enforced in the Bronze → Silver step and measured all the way to the report.

**Quality rules.** Every Bronze row is checked against seven rules, in order. The first failing rule is recorded in `reject_reason`, and the row goes to `silver.taxi_trips_rejected` instead of being dropped silently.

| Rule | What it catches | Rows rejected (2024) |
|---|---|---:|
| `missing_timestamp` | pickup or dropoff time missing | 0 |
| `pickup_outside_file_month` | trip filed under the wrong month | 584 |
| `non_positive_duration` | dropoff at or before pickup | 14,170 |
| `duration_over_24h` | trips longer than a day (stuck meters) | 230 |
| `invalid_distance` | distance missing, negative or over 200 miles | 1,371 |
| `negative_amount` | negative fare or total: refunds and voids recorded as separate rows | 735,944 |
| `invalid_location` | pickup or dropoff zone outside the 265 TLC zones | 0 |
| **Total** | **1.8% of Bronze rows** | **752,299** |

**Other safeguards**
- **Deduplication:** the TLC files have no trip ID, so rows are deduplicated on a composite key (taxi type, vendor, pickup and dropoff time, both locations, total amount). 7 exact duplicates were removed.
- **Schema drift:** monthly files change column types over time. Bronze lower-cases names and widens types, so every month appends cleanly.
- **Idempotent loads:** `bronze.ingestion_log` is a watermark, so loaded files are skipped. Reloading a file deletes its earlier rows first, and Silver overwrites only the months being processed. Reruns never double-count.
- **Standardised values:** missing or `N/A` boroughs become `Unknown`, and payment, rate and trip-type codes are mapped to labels. Trips with no passenger count are flagged (`dq_passenger_count_missing`) instead of dropped.
- **Reconciliation gate:** the Gold notebook compares its trip total with Silver and fails the run on any difference, so the report never shows partial data.
- **Pipeline resilience:** each notebook activity retries twice with a 2-minute back-off. Downstream steps run only when upstream steps succeed, so Gold keeps serving the last good data.
- **Visible to users:** `gold.dq_layer_counts` and `gold.dq_rejections` drive the report's Data Quality page, which shows row counts per layer, rejection rate by month and rejections by rule.

## Report snapshots (pickup borough = Manhattan)

### Overview
![Overview](docs/snapshots/01_overview_manhattan.png)

### Yellow vs Green
![Yellow vs Green](docs/snapshots/02_yellow_vs_green_manhattan.png)

### Zones & Seasonality
![Zones & Seasonality](docs/snapshots/03_zones_seasonality_manhattan.png)

### Demand Patterns
![Demand Patterns](docs/snapshots/04_demand_patterns_manhattan.png)

### Data Quality
![Data Quality](docs/snapshots/05_data_quality_manhattan.png)

The Data Quality page has no borough slicer because pipeline row counts are tracked per source file, not per borough. The Key Insights cards show whole-year 2024 figures and don't change with the slicers.

## Repository layout

```
notebooks/           Fabric notebook sources (deployed as .ipynb)
  01_bronze_ingest.py, 02_silver_transform.py, 03_gold_aggregate.py
  snippets/          one-off Spark snippets used while debugging (via tools/livy.py)
src/                 deployment code
  fabric.py          Fabric REST helper: Azure CLI auth, long-running operations, notebook deploy/run
  pipeline.py        Data pipeline that runs Bronze → Silver → Gold
  semantic_model.py  TMDL generator for the Direct Lake model; deploy, refresh, DAX queries
  report.py          PBIR generator for the report (pages, visuals, theme, insight cards)
  insights.json      text of the Key Insights cards
  sql_views.py       dbo.vw_* views on the SQL analytics endpoint
tools/               export pages to PDF/PNG, pull the report back to PBIP, draw the README diagrams,
                     collect project stats, job history, Livy debugging
checks/              live DAX and SQL verification scripts
tests/               pytest suite (see "Tests and CI")
powerbi/             Power BI project: open "NYC Taxi Overview.pbip" in Power BI Desktop
docs/                architecture and model diagrams, project_stats.json; snapshots/ holds the page images
.github/workflows/   CI: lint + tests + coverage on every push and pull request
requirements.txt     all Python dependencies (tools and tests)
pyproject.toml       pytest, coverage and ruff settings
```

## Running it

Prerequisites: Python 3.11+, the Azure CLI and a Fabric capacity (a trial works). Run every command from the repository root.

```bash
# 1. Install the Python dependencies (deployment tools + tests)
pip install -r requirements.txt

# 2. Sign in to Azure (Fabric trial tenants often have no Azure subscription, hence the flag)
az login --allow-no-subscriptions

# 3. One-time setup in the Fabric portal:
#    - create a workspace on your Fabric capacity
#    - create a lakehouse named nyc_taxi_lakehouse with "Lakehouse schemas" enabled
#    - set WORKSPACE (and LAKEHOUSE, if you named it differently) at the top of src/fabric.py

# 4. Upload the three notebooks to Fabric, bound to the lakehouse
python src/fabric.py deploy

# 5. Run the medallion layers in order (each command waits for the run to finish)
python src/fabric.py run 01_bronze_ingest      # download the 24 TLC files -> bronze tables
python src/fabric.py run 02_silver_transform   # dedupe + quality rules -> silver (rejects kept)
python src/fabric.py run 03_gold_aggregate     # star schema + reconciliation check -> gold

#    Optional: a Fabric pipeline that runs step 5 end to end, with retries
python src/pipeline.py deploy

# 6. Create the Direct Lake semantic model, then refresh it to load the Gold tables
python src/semantic_model.py deploy
python src/semantic_model.py refresh

# 7. Publish the 5-page Power BI report on the model
python src/report.py deploy

# 8. Create the dbo.vw_* SQL views for analysts
python src/sql_views.py
```

Useful extras:

```bash
# Re-download the live report into powerbi/ (PBIP for Power BI Desktop)
python tools/download_report.py powerbi

# Export one report page as PDF + PNG
python tools/export_page.py overview overview.pdf

# Snapshot the pages for one borough, using a temporary copy of the report (delete the copy afterwards)
NYC_REPORT_NAME="NYC Taxi - Manhattan" NYC_PRESELECT_BOROUGH=Manhattan python src/report.py deploy
NYC_REPORT_NAME="NYC Taxi - Manhattan" python tools/export_page.py overview docs/snapshots/01_overview_manhattan.pdf

# Re-collect the README numbers from the live lakehouse, then redraw the diagrams
python tools/project_stats.py
python tools/make_diagrams.py
```

## Tests and CI

### What is tested

| Area | Tests | How |
|---|---|---|
| `src/fabric.py` | auth caching, retries on network drops, long-running-operation polling, notebook packaging, deploy, job runs and timeouts | REST calls mocked |
| `src/semantic_model.py` | every TMDL helper, the full model build, deploy, refresh and DAX queries; relationships point at real columns; every measure is documented | generated output |
| `src/report.py` | every visual builder, all 5 pages fit the canvas, slicer sync rules, **every field used by a visual exists in the model** | generated output |
| `src/pipeline.py`, `src/sql_views.py` | activity order, retries and dependencies; views read only Gold tables and are SELECT-only; SQL endpoint token handling | mocked service |
| Notebook helpers | US holiday calendar, file download (cached / missing / failed), type conforming, ingestion log, partition overwrite, Bronze→Silver mapping, code lookups, **each Silver quality rule**, Gold decimal casting | local **Spark 3.5 + Delta Lake 3.2** |
| `tools/` | Livy sessions, diagram rendering, OneLake listing and stats collection | mocked service |
| Drift checks | the committed `powerbi/` model and report must equal what the code generates, and the stats must reconcile | file comparison |

Notebook helpers are loaded straight from the notebook sources (`tests/conftest.py::load_notebook`), so the tests run the same code that runs in Fabric. Nothing in the test suite calls Azure.

### Running the tests

```bash
pip install -r requirements.txt     # tools, pytest, coverage, ruff, pyspark 3.5.3 and delta-spark 3.2.1
pytest                              # all tests
pytest --cov                        # with a coverage report
ruff check .                        # lint
```

The Spark tests need **Java 17** (`JAVA_HOME` set, or `java` on the `PATH`), the same Spark and Delta versions as Fabric Runtime 1.3. Without Java they are skipped locally and the rest of the suite still runs. They always run in CI. Spark on Windows also needs Hadoop's `winutils`, so Linux, macOS or WSL is easiest for running them locally.

### Continuous integration

[`.github/workflows/ci.yml`](.github/workflows/ci.yml) runs on every push to `main` and on every pull request:

1. **Lint:** `ruff check` (pyflakes rules: undefined names, unused imports).
2. **Tests:** Python 3.11 with Java 17. The full suite runs with `REQUIRE_SPARK=1`, so the Spark tests can't be skipped silently, and the build fails below **95% coverage**. Test results and `coverage.xml` are uploaded as build artifacts.
3. **Coverage badge:** on `main`, the coverage figure is rendered as the badge at the top of this README. The image is stored on the `badges` branch, because GitHub has no built-in coverage badge and keeping it there avoids bot commits on `main`.

Changes go through a pull request, and CI must pass before merging into `main`.

## Data source

[NYC Taxi & Limousine Commission trip record data](https://www.nyc.gov/site/tlc/about/tlc-trip-record-data.page), January to December 2024.

## License

[MIT](LICENSE)
