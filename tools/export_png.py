"""Export the report to PDF for a visual check: python export_report.py"""
import os, sys; sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))  # shared helpers live in src/
import os, time
import fabric, semantic_model as sm

ws, _ = fabric.ids()
rid = next(x["id"] for x in fabric.call("GET", f"/workspaces/{ws}/reports")["value"] if x["displayName"] == "NYC Taxi Overview")
job = sm.pbi("POST", f"/groups/{ws}/reports/{rid}/ExportTo", {"format": "PNG"}).json()
while job["status"] not in ("Succeeded", "Failed"):
    time.sleep(5)
    job = sm.pbi("GET", f"/groups/{ws}/reports/{rid}/exports/{job['id']}").json()
    print(job["status"], job.get("percentComplete"), flush=True)
if job["status"] == "Failed":
    raise SystemExit(job)
out = os.path.join(os.getcwd(), "report_export.zip")
open(out, "wb").write(sm.pbi("GET", f"/groups/{ws}/reports/{rid}/exports/{job['id']}/file").content)
print("saved", out)

