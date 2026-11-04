# NYC Taxi Trips 2024: Medallion Lakehouse on Microsoft Fabric

An end-to-end analytics build on Microsoft Fabric. It ingests the 2024 NYC TLC yellow and green taxi trip records, cleans them through a Bronze → Silver → Gold medallion lakehouse, and presents them in a five-page Power BI report on a Direct Lake semantic model.

Everything is deployed from code. The scripts in this repository create the notebooks, run them, build the semantic model and report, and add SQL views, all through the Fabric and Power BI REST APIs.

## Architecture

```
NYC TLC parquet files (2024, yellow + green)
        │
        ▼
 bronze  raw trips + ingestion metadata (source file, batch id, load time)   notebooks/01_bronze_ingest.py
        │   dedupe, range and logic checks, rejected rows logged by rule
        ▼
 silver  validated, conformed trips                                          notebooks/02_silver_transform.py
        │   daily/zone, hourly, monthly aggregates; data-quality tables; slicer dimensions
        ▼
 gold    report-ready tables                                                 notebooks/03_gold_aggregate.py
        │
        ├──► Direct Lake semantic model "NYC Taxi"                           src/semantic_model.py
        │        └──► Power BI report "NYC Taxi Overview" (5 pages)          src/report.py
        └──► dbo.vw_* SQL views on the SQL analytics endpoint                src/sql_views.py
```

- **One schema-enabled lakehouse** (`nyc_taxi_lakehouse`) with `bronze`, `silver` and `gold` schemas, plus analyst views in `dbo`.
- **Data quality:** 41.83M Bronze rows produce 41.08M Silver rows (98.2% kept). Each rejected row is logged with the rule it failed. Gold totals are reconciled against Silver, and the Gold notebook fails the run if they don't match.
- **Gold tables** are written with V-Order and optimize-write, then ZORDERed for Direct Lake reads.
- **Shared slicers:** Month, Borough and Taxi Type tables filter every fact table, so the multi-select slicers stay in sync on every page.

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
tools/               export pages to PDF/PNG, pull the report back to PBIP, job history, Livy debugging
checks/              DAX and SQL verification scripts
powerbi/             Power BI project: open "NYC Taxi Overview.pbip" in Power BI Desktop
docs/snapshots/      page images shown above
```

## Running it

Requirements: Python 3.12, the Azure CLI and a Fabric capacity (a trial works). Install the packages with `pip install requests pypdfium2 pillow mssql-python azure-identity`. Run all commands from the repository root.

1. `az login --allow-no-subscriptions`
2. Set `WORKSPACE` in `src/fabric.py` to your workspace name.
3. `python src/fabric.py deploy` to upload the notebooks, then run them in order with `python src/fabric.py run 01_bronze_ingest` (then `02_silver_transform` and `03_gold_aggregate`). Alternatively, use `python src/pipeline.py deploy` to create a pipeline that runs all three.
4. `python src/semantic_model.py deploy`, then `python src/semantic_model.py refresh`.
5. `python src/report.py deploy`
6. `python src/sql_views.py`

Useful extras:
- `python tools/download_report.py powerbi` refreshes the PBIP report in `powerbi/` from the live report.
- `python tools/export_page.py <page> <out.pdf>` exports one page as a PDF and PNG.
- To snapshot the pages for one borough, run `src/report.py deploy` with `NYC_REPORT_NAME` set to a new report name and `NYC_PRESELECT_BOROUGH` set to the borough. Then run `tools/export_page.py` with the same `NYC_REPORT_NAME`, and delete the copy afterwards.

## Data source

[NYC Taxi & Limousine Commission trip record data](https://www.nyc.gov/site/tlc/about/tlc-trip-record-data.page), January to December 2024.

## License

[MIT](LICENSE)
