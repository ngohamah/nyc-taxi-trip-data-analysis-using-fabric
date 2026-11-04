"""Export one report page to PDF, optionally with a report-level filter.
Usage: python tools/export_page.py <pageName> <out.pdf> [odata filter]"""
import os, sys; sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))  # shared helpers live in src/
import os, sys, time
import fabric, semantic_model as sm

page, out = sys.argv[1], sys.argv[2]
cfg = {"pages": [{"pageName": page}]}
if len(sys.argv) > 3:
    cfg["reportLevelFilters"] = [{"filter": sys.argv[3]}]
ws, _ = fabric.ids()
rid = next(x["id"] for x in fabric.call("GET", f"/workspaces/{ws}/reports")["value"] if x["displayName"] == os.environ.get("NYC_REPORT_NAME", "NYC Taxi Overview"))
job = sm.pbi("POST", f"/groups/{ws}/reports/{rid}/ExportTo", {"format": "PDF", "powerBIReportConfiguration": cfg}).json()
while job["status"] not in ("Succeeded", "Failed"):
    time.sleep(5)
    job = sm.pbi("GET", f"/groups/{ws}/reports/{rid}/exports/{job['id']}").json()
if job["status"] == "Failed":
    raise SystemExit(job)
open(out, "wb").write(sm.pbi("GET", f"/groups/{ws}/reports/{rid}/exports/{job['id']}/file").content)
import pypdfium2 as pdfium
pdfium.PdfDocument(out)[0].render(scale=1.6).to_pil().save(out.replace(".pdf", ".png"))
print("saved", out.replace(".pdf", ".png"))
