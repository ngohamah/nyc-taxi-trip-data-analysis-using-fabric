"""Interactive Spark via the Fabric Livy API, for debugging notebooks cell by cell.

Usage:
  python tools/livy.py cells <notebook.py> [--from N]   # run code cells in order, stop at first error
  python tools/livy.py code <file.py>                   # run one snippet file
  python tools/livy.py stop                             # delete the session
"""
import os, sys; sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))  # shared helpers live in src/
import re, time
import fabric

SESSION_FILE = os.path.join(fabric.HERE, ".livy_session")


def base():
    ws, lh = fabric.ids()
    return f"/workspaces/{ws}/lakehouses/{lh}/livyApi/versions/2023-12-01/sessions"


def session(b):
    if os.path.exists(SESSION_FILE):
        sid = open(SESSION_FILE).read().strip()
        try:
            if fabric.raw("GET", f"{b}/{sid}").json().get("state") in ("idle", "busy", "starting", "not_started"):
                return sid
        except RuntimeError:
            pass
    s = fabric.raw("POST", b, {"name": "medallion-debug", "kind": "pyspark"}).json()
    sid = str(s["id"])
    open(SESSION_FILE, "w").write(sid)
    print(f"started Livy session {sid}", flush=True)
    while True:
        time.sleep(10)
        st = fabric.raw("GET", f"{b}/{sid}").json()["state"]
        if st == "idle":
            return sid
        if st in ("dead", "killed", "error"):
            raise RuntimeError(f"session {st}")


def run(b, sid, code=None, stmt_id=None):
    if stmt_id is not None:
        st = fabric.raw("GET", f"{b}/{sid}/statements/{stmt_id}").json()
    else:
        st = fabric.raw("POST", f"{b}/{sid}/statements", {"code": code, "kind": "pyspark"}).json()
    while st["state"] not in ("available", "error", "cancelled"):
        time.sleep(3)
        st = fabric.raw("GET", f"{b}/{sid}/statements/{st['id']}").json()
    out = st.get("output") or {}
    if out.get("status") == "ok":
        print("".join(out.get("data", {}).get("text/plain", "")) if isinstance(out.get("data", {}).get("text/plain"), list)
              else out.get("data", {}).get("text/plain", ""), flush=True)
        return True
    print(f"ERROR {out.get('ename')}: {out.get('evalue')}\n" + "".join(out.get("traceback", [])[-15:]), flush=True)
    return False


def cells(path):
    text = open(path, encoding="utf-8").read().replace("\r\n", "\n")
    out = []
    for chunk in re.split(r"(?m)^# %%", text):
        header, _, body = chunk.partition("\n")
        if chunk.strip() and header.strip() != "[markdown]":
            out.append(body.replace("notebookutils.notebook.exit(", "print("))
    return out


if __name__ == "__main__":
    b = base()
    cmd = sys.argv[1]
    if cmd == "stop":
        if os.path.exists(SESSION_FILE):
            fabric.raw("DELETE", f"{b}/{open(SESSION_FILE).read().strip()}")
            os.remove(SESSION_FILE)
        print("stopped")
        sys.exit(0)
    sid = session(b)
    if cmd == "wait":   # re-attach to a statement already submitted
        sys.exit(0 if run(b, sid, stmt_id=sys.argv[2]) else 1)
    if cmd == "code":
        sys.exit(0 if run(b, sid, open(sys.argv[2], encoding="utf-8").read()) else 1)
    start = int(sys.argv[4]) if len(sys.argv) > 4 and sys.argv[3] == "--from" else 0
    for i, c in enumerate(cells(sys.argv[2])):
        if i < start:
            continue
        print(f"--- cell {i} ---", flush=True)
        if not run(b, sid, c):
            sys.exit(1)
