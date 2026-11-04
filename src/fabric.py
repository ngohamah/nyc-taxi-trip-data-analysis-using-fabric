"""Fabric REST helper + deploy/run CLI for the NYC taxi medallion build.

Usage:
  python src/fabric.py deploy                 # create/update the 3 notebooks, bound to the lakehouse
  python src/fabric.py run <notebook_name>    # run a notebook and wait for completion
"""
import base64, json, os, re, subprocess, sys, time
import requests

API = "https://api.fabric.microsoft.com/v1"
HEADERS_EXTRA = {"x-ms-fabric-skill": "e2e-medallion-architecture"}
WORKSPACE = "ngoh-fabric-dev"
LAKEHOUSE = "nyc_taxi_lakehouse"
HERE = os.path.dirname(os.path.abspath(__file__))        # src/
ROOT = os.path.dirname(HERE)                              # repository root
NOTEBOOK_DIR = os.path.join(ROOT, "notebooks")
NOTEBOOKS = ["01_bronze_ingest", "02_silver_transform", "03_gold_aggregate"]

_tok = {"v": None, "t": 0}


def token():
    if not _tok["v"] or time.time() - _tok["t"] > 2400:
        _tok["v"] = subprocess.check_output(
            "az account get-access-token --resource https://api.fabric.microsoft.com --query accessToken -o tsv",
            shell=True, text=True).strip()
        _tok["t"] = time.time()
    return _tok["v"]


def raw(method, path, body=None):
    url = path if path.startswith("http") else API + path
    h = {"Authorization": f"Bearer {token()}", **HEADERS_EXTRA}
    if body is None and method == "POST":
        body = {}
    for attempt in range(6):  # ride out transient local DNS / network drops (only GETs and idempotent polls repeat)
        try:
            r = requests.request(method, url, headers=h, json=body, timeout=120)
            break
        except requests.exceptions.ConnectionError:
            if method != "GET" or attempt == 5:
                raise
            time.sleep(10 * (attempt + 1))
    if r.status_code >= 400:
        raise RuntimeError(f"HTTP {r.status_code} {method} {url}: {r.text}")
    return r


def call(method, path, body=None, want_result=True):
    """Call the API; poll long-running operations (202 + Location) until done."""
    r = raw(method, path, body)
    if r.status_code == 202 and r.headers.get("x-ms-operation-id") and r.headers.get("Location"):
        loc = r.headers["Location"]
        while True:
            time.sleep(min(int(r.headers.get("Retry-After", 3)), 10))
            r = raw("GET", loc)
            op = r.json()
            if op.get("status") == "Succeeded":
                if not want_result:
                    return op
                try:
                    return raw("GET", loc + "/result").json()
                except RuntimeError:
                    return op
            if op.get("status") in ("Failed", "Cancelled"):
                raise RuntimeError(f"LRO {op.get('status')}: {json.dumps(op)}")
    return r.json() if r.content else {}


def ids():
    ws = next(w for w in call("GET", "/workspaces")["value"] if w["displayName"] == WORKSPACE)
    lh = next(l for l in call("GET", f"/workspaces/{ws['id']}/lakehouses")["value"] if l["displayName"] == LAKEHOUSE)
    return ws["id"], lh["id"]


def build_ipynb(py_path, ws_id, lh_id):
    """Turn a .py with '# %%', '# %% [parameters]', '# %% [markdown]' markers into Fabric-ready .ipynb JSON."""
    text = open(py_path, encoding="utf-8").read().replace("\r\n", "\n")
    cells = []
    for chunk in re.split(r"(?m)^# %%", text):
        if not chunk.strip():
            continue
        header, _, body = chunk.partition("\n")
        header = header.strip()
        lines = body.rstrip().split("\n")
        if header == "[markdown]":
            lines = [re.sub(r"^# ?", "", l) for l in lines]
        src = [l + "\n" for l in lines[:-1]] + [lines[-1]]
        if header == "[markdown]":
            cells.append({"cell_type": "markdown", "metadata": {}, "source": src})
        else:
            meta = {"tags": ["parameters"]} if header == "[parameters]" else {}
            cells.append({"cell_type": "code", "execution_count": None, "metadata": meta, "outputs": [], "source": src})
    nb = {
        "nbformat": 4, "nbformat_minor": 5,
        "metadata": {
            "kernel_info": {"name": "synapse_pyspark"},
            "kernelspec": {"name": "synapse_pyspark", "display_name": "Synapse PySpark", "language": "Python"},
            "language_info": {"name": "python"},
            "dependencies": {"lakehouse": {
                "default_lakehouse": lh_id, "default_lakehouse_name": LAKEHOUSE,
                "default_lakehouse_workspace_id": ws_id, "known_lakehouses": [{"id": lh_id}]}},
        },
        "cells": cells,
    }
    for c in nb["cells"]:  # nbformat sanity checks (Fabric nuances)
        assert "metadata" in c and "source" in c
        if c["cell_type"] == "code":
            assert c["outputs"] == [] and c["execution_count"] is None
    return json.dumps(nb, indent=1)


def definition(ipynb):
    return {"format": "ipynb", "parts": [{"path": "notebook-content.ipynb",
            "payload": base64.b64encode(ipynb.encode("utf-8")).decode(), "payloadType": "InlineBase64"}]}


def deploy():
    ws_id, lh_id = ids()
    existing = {n["displayName"]: n["id"] for n in call("GET", f"/workspaces/{ws_id}/notebooks")["value"]}
    for name in NOTEBOOKS:
        nb = build_ipynb(os.path.join(NOTEBOOK_DIR, f"{name}.py"), ws_id, lh_id)
        if name in existing:
            call("POST", f"/workspaces/{ws_id}/notebooks/{existing[name]}/updateDefinition",
                 {"definition": definition(nb)}, want_result=False)
            print(f"updated  {name} ({existing[name]})")
        else:
            item = call("POST", f"/workspaces/{ws_id}/items",
                        {"displayName": name, "type": "Notebook", "definition": definition(nb)})
            print(f"created  {name} ({item.get('id')})")


def run(name, timeout_min=90):
    ws_id, lh_id = ids()
    nb_id = next(n["id"] for n in call("GET", f"/workspaces/{ws_id}/notebooks")["value"] if n["displayName"] == name)
    base = f"/workspaces/{ws_id}/items/{nb_id}/jobs/instances"
    # Guard against duplicate submissions: reuse an in-flight job if there is one
    active = [j for j in call("GET", base).get("value", []) if j["status"] in ("NotStarted", "InProgress", "Running")]
    if active:
        job_url = f"{base}/{active[0]['id']}"
        print(f"reusing in-flight job {active[0]['id']}")
    else:
        r = raw("POST", f"{base}?jobType=RunNotebook", {"executionData": {"configuration": {
            "defaultLakehouse": {"id": lh_id, "name": LAKEHOUSE, "workspaceId": ws_id}}}})
        job_url = r.headers["Location"]
        print(f"submitted {name}: {job_url.rsplit('/', 1)[-1]}")
    start = time.time()
    while True:
        time.sleep(20)
        mins = (time.time() - start) / 60
        try:
            j = raw("GET", job_url).json()
        except requests.exceptions.RequestException as e:  # transient local network/DNS issue: keep polling the same job
            print(f"  [{mins:5.1f} min] poll failed ({type(e).__name__}), retrying", flush=True)
            continue
        print(f"  [{mins:5.1f} min] {j['status']}", flush=True)
        if j["status"] in ("Completed", "Failed", "Cancelled", "Deduped"):
            if j.get("failureReason"):
                print(json.dumps(j["failureReason"], indent=2))
            return j["status"]
        if mins > timeout_min:
            print("timed out waiting (job keeps running in Fabric)")
            return "Timeout"


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "deploy":
        deploy()
    elif cmd == "run":
        status = run(sys.argv[2])
        sys.exit(0 if status == "Completed" else 1)
    else:
        print(__doc__)
