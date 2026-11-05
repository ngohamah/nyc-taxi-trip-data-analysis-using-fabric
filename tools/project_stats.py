"""Collect the headline numbers quoted in the README: python tools/project_stats.py

Reads OneLake storage sizes, table row counts and data-quality results from the live lakehouse,
plus model/report sizes from the code, and writes them to docs/project_stats.json.
"""
import os, sys; sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))  # shared helpers live in src/
import json, subprocess
import requests
import fabric, semantic_model, report

ONELAKE = "https://onelake.dfs.fabric.microsoft.com"


def storage_token():
    return subprocess.check_output("az account get-access-token --resource https://storage.azure.com --query accessToken -o tsv",
                                   shell=True, text=True).strip()


def list_paths(ws_id, directory, tok):
    """All files under a lakehouse directory (ADLS Gen2 list API, paged by continuation token)."""
    out, cont = [], None
    while True:
        params = {"resource": "filesystem", "recursive": "true", "directory": directory}
        if cont:
            params["continuation"] = cont
        r = requests.get(f"{ONELAKE}/{ws_id}", params=params, headers={"Authorization": f"Bearer {tok}"}, timeout=120)
        r.raise_for_status()
        out += [p for p in r.json().get("paths", []) if p.get("isDirectory") != "true"]
        cont = r.headers.get("x-ms-continuation")
        if not cont:
            return out


def summarize_storage(paths, prefix_len):
    """Group file sizes by the path component after the prefix, e.g. Tables/<schema>/..."""
    groups = {}
    for p in paths:
        key = p["name"].split("/")[prefix_len]
        g = groups.setdefault(key, {"files": 0, "bytes": 0})
        g["files"] += 1
        g["bytes"] += int(p.get("contentLength", 0))
    return groups


def model_counts(files):
    tmdl = "".join(c for p, c in files.items() if p.endswith(".tmdl"))
    return {"tables": sum(1 for p in files if p.startswith("definition/tables/")),
            "relationships": tmdl.count("\nrelationship ") + tmdl.startswith("relationship "),
            "measures": tmdl.count("\tmeasure ")}


def report_counts(files):
    pages = {p.split("/")[2] for p in files if p.startswith("definition/pages/") and p.count("/") >= 3}
    visuals = [p for p in files if p.endswith("/visual.json")]
    return {"pages": len(pages) - 1, "tooltip_pages": 1, "visuals": len(visuals)}


def main():
    ws_id, lh_id = fabric.ids()
    tok = storage_token()
    landing = list_paths(ws_id, f"{lh_id}/Files/landing/nyc_taxi", tok)
    tables = list_paths(ws_id, f"{lh_id}/Tables", tok)
    trip_files = [p for p in landing if p["name"].endswith(".parquet")]
    stats = {
        "source_files": {"parquet_files": len(trip_files),
                         "parquet_bytes": sum(int(p.get("contentLength", 0)) for p in trip_files),
                         "all_files": len(landing)},
        "delta_storage_by_schema": summarize_storage(tables, 2),
    }

    from sql_views import connect
    conn = connect()
    cur = conn.cursor()
    names = cur.execute("SELECT s.name, t.name FROM sys.tables t JOIN sys.schemas s ON s.schema_id = t.schema_id "
                        "WHERE s.name IN ('bronze','silver','gold') ORDER BY 1, 2").fetchall()
    stats["row_counts"] = {f"{s}.{t}": cur.execute(f"SELECT COUNT_BIG(*) FROM {s}.{t}").fetchone()[0] for s, t in names}
    stats["ingestion_log"] = [dict(zip(("taxi_type", "files", "rows"), r)) for r in cur.execute(
        "SELECT taxi_type, COUNT(DISTINCT source_file), SUM(row_count) FROM bronze.ingestion_log "
        "WHERE status = 'succeeded' AND taxi_type IN ('yellow','green') GROUP BY taxi_type").fetchall()]
    stats["rejections_by_rule"] = {r[0]: r[1] for r in cur.execute(
        "SELECT reject_reason, SUM(rejected_rows) FROM gold.dq_rejections GROUP BY reject_reason ORDER BY 2 DESC").fetchall()}
    totals = cur.execute("SELECT SUM(bronze_rows), SUM(silver_rows), SUM(rejected_rows), SUM(duplicate_rows) "
                         "FROM gold.dq_layer_counts").fetchone()
    stats["layers"] = dict(zip(("bronze_rows", "silver_rows", "rejected_rows", "duplicate_rows"), totals))
    stats["gold_trip_total"] = cur.execute("SELECT SUM(trip_count) FROM gold.fact_trips_daily_zone").fetchone()[0]
    stats["revenue_total"] = float(cur.execute("SELECT SUM(total_amount) FROM gold.fact_trips_daily_zone").fetchone()[0])
    stats["sql_views"] = cur.execute("SELECT COUNT(*) FROM sys.views v JOIN sys.schemas s ON s.schema_id = v.schema_id "
                                     "WHERE s.name = 'dbo' AND v.name LIKE 'vw[_]%'").fetchone()[0]
    conn.close()

    stats["semantic_model"] = model_counts(semantic_model.build("ws", "lh"))
    ins = json.load(open(os.path.join(fabric.HERE, "insights.json"), encoding="utf-8"))
    stats["report"] = report_counts(report.build("ws", "model", ins))

    out = os.path.join(fabric.ROOT, "docs", "project_stats.json")
    with open(out, "w", encoding="utf-8", newline="\n") as f:
        json.dump(stats, f, indent=2, default=int)
    print(json.dumps(stats, indent=2, default=int))


if __name__ == "__main__":
    main()
