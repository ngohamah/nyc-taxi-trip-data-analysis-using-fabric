import os, sys; sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))  # shared helpers live in src/
import fabric, semantic_model as sm
ws, _ = fabric.ids()
r = next(x for x in fabric.call("GET", f"/workspaces/{ws}/reports")["value"] if x["displayName"] == "NYC Taxi Overview")
info = sm.pbi("GET", f"/groups/{ws}/reports/{r['id']}").json()
print("dataset:", info.get("datasetId"), "| url:", info.get("webUrl"))
for p in sm.pbi("GET", f"/groups/{ws}/reports/{r['id']}/pages").json()["value"]:
    print("page:", p["displayName"])
