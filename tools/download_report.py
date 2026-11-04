"""Download the deployed report definition as a PBIP folder: python tools/download_report.py <dest_dir>"""
import os, sys; sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))  # shared helpers live in src/
import base64, json, os, sys
import fabric

dest = sys.argv[1]
ws, _ = fabric.ids()
rid = next(r["id"] for r in fabric.call("GET", f"/workspaces/{ws}/reports")["value"] if r["displayName"] == "NYC Taxi Overview")
d = fabric.call("POST", f"/workspaces/{ws}/reports/{rid}/getDefinition")
root = os.path.join(dest, "NYC Taxi Overview.Report")
n = 0
for p in d["definition"]["parts"]:
    data = base64.b64decode(p["payload"])
    if p["path"] == "definition.pbir":
        # point at the local model folder so Power BI Desktop can open the project
        data = json.dumps({"version": "4.0", "datasetReference": {"byPath": {"path": "../NYC Taxi.SemanticModel"}}}, indent=2).encode()
    out = os.path.join(root, *p["path"].split("/"))
    os.makedirs(os.path.dirname(out), exist_ok=True)
    open(out, "wb").write(data); n += 1
pbip = {"version": "1.0", "artifacts": [{"report": {"path": "NYC Taxi Overview.Report"}}], "settings": {"enableAutoRecovery": True}}
open(os.path.join(dest, "NYC Taxi Overview.pbip"), "w").write(json.dumps(pbip, indent=2))
print(f"wrote {n} report parts to {root}")
