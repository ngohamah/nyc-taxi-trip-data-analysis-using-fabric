"""List recent notebook job runs: python tools/jobs.py"""
import os, sys; sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))  # shared helpers live in src/
import fabric

ws, _ = fabric.ids()
for n in fabric.call("GET", f"/workspaces/{ws}/notebooks")["value"]:
    for j in fabric.call("GET", f"/workspaces/{ws}/items/{n['id']}/jobs/instances")["value"]:
        fr = j.get("failureReason") or {}
        print(n["displayName"], j["id"], j["status"], j.get("startTimeUtc"), j.get("endTimeUtc"), fr.get("errorCode", ""), fr.get("message", ""))
