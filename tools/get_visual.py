"""Print a deployed visual's JSON as Power BI stored it: python tools/get_visual.py <page> <visual>"""
import os, sys; sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))  # shared helpers live in src/
import base64, json, sys
import fabric

ws, _ = fabric.ids()
rid = next(r["id"] for r in fabric.call("GET", f"/workspaces/{ws}/reports")["value"] if r["displayName"] == "NYC Taxi Overview")
d = fabric.call("POST", f"/workspaces/{ws}/reports/{rid}/getDefinition")
path = f"definition/pages/{sys.argv[1]}/visuals/{sys.argv[2]}/visual.json"
part = next(p for p in d["definition"]["parts"] if p["path"] == path)
v = json.loads(base64.b64decode(part["payload"]))
print(json.dumps(v["visual"].get("objects", {}).get("values"), indent=1))
