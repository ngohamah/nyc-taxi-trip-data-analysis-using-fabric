"""src/fabric.py: auth, REST calls with retries, long-running operations, notebook packaging, deploy and run."""
import base64, itertools, json, os
import pytest
import requests
import fabric


class Resp:
    def __init__(self, status=200, body=None, headers=None, content=True):
        self.status_code, self._body, self.headers = status, body or {}, headers or {}
        self.content = b"x" if content else b""
        self.text = json.dumps(self._body)

    def json(self):
        return self._body


@pytest.fixture(autouse=True)
def fixed_token(monkeypatch):
    monkeypatch.setattr(fabric, "token", lambda: "tok")


# ---------------------------------------------------------------- token
def test_token_is_fetched_once_and_cached(monkeypatch):
    monkeypatch.undo()  # use the real token()
    calls = []
    monkeypatch.setattr(fabric.subprocess, "check_output", lambda *a, **k: calls.append(a) or "abc\n")
    monkeypatch.setitem(fabric._tok, "v", None)
    assert fabric.token() == "abc"
    assert fabric.token() == "abc"
    assert len(calls) == 1


def test_token_refreshes_after_40_minutes(monkeypatch):
    monkeypatch.undo()
    monkeypatch.setattr(fabric.subprocess, "check_output", lambda *a, **k: "new")
    monkeypatch.setitem(fabric._tok, "v", "old")
    monkeypatch.setitem(fabric._tok, "t", fabric.time.time() - 2401)
    assert fabric.token() == "new"


# ---------------------------------------------------------------- raw
def test_raw_builds_url_headers_and_default_post_body(monkeypatch):
    seen = {}

    def fake(method, url, headers, json, timeout):
        seen.update(method=method, url=url, headers=headers, json=json)
        return Resp()
    monkeypatch.setattr(fabric.requests, "request", fake)
    fabric.raw("POST", "/workspaces")
    assert seen["url"] == fabric.API + "/workspaces"
    assert seen["headers"]["Authorization"] == "Bearer tok"
    assert seen["headers"]["x-ms-fabric-skill"] == "e2e-medallion-architecture"
    assert seen["json"] == {}


def test_raw_passes_absolute_urls_through(monkeypatch):
    seen = {}
    monkeypatch.setattr(fabric.requests, "request", lambda m, url, **k: seen.update(url=url) or Resp())
    fabric.raw("GET", "https://example.test/op/1")
    assert seen["url"] == "https://example.test/op/1"


def test_raw_retries_get_on_connection_error(monkeypatch, no_sleep):
    attempts = []

    def flaky(*a, **k):
        attempts.append(1)
        if len(attempts) < 3:
            raise requests.exceptions.ConnectionError("dns")
        return Resp(body={"ok": True})
    monkeypatch.setattr(fabric.requests, "request", flaky)
    assert fabric.raw("GET", "/x").json() == {"ok": True}
    assert len(attempts) == 3


def test_raw_does_not_retry_post(monkeypatch, no_sleep):
    def boom(*a, **k):
        raise requests.exceptions.ConnectionError("dns")
    monkeypatch.setattr(fabric.requests, "request", boom)
    with pytest.raises(requests.exceptions.ConnectionError):
        fabric.raw("POST", "/x", {"a": 1})


def test_raw_gives_up_after_six_get_attempts(monkeypatch, no_sleep):
    attempts = []

    def boom(*a, **k):
        attempts.append(1)
        raise requests.exceptions.ConnectionError("dns")
    monkeypatch.setattr(fabric.requests, "request", boom)
    with pytest.raises(requests.exceptions.ConnectionError):
        fabric.raw("GET", "/x")
    assert len(attempts) == 6


def test_raw_raises_on_http_error(monkeypatch):
    monkeypatch.setattr(fabric.requests, "request", lambda *a, **k: Resp(404, {"error": "nope"}))
    with pytest.raises(RuntimeError, match="HTTP 404"):
        fabric.raw("GET", "/x")


# ---------------------------------------------------------------- call (LRO)
def script(monkeypatch, responses):
    """Feed fabric.raw a fixed sequence of responses and record the calls."""
    seen, it = [], iter(responses)
    monkeypatch.setattr(fabric, "raw", lambda m, p, b=None: seen.append((m, p, b)) or next(it))
    return seen


def test_call_returns_json_for_plain_responses(monkeypatch):
    script(monkeypatch, [Resp(body={"value": [1]})])
    assert fabric.call("GET", "/x") == {"value": [1]}


def test_call_returns_empty_dict_without_content(monkeypatch):
    script(monkeypatch, [Resp(content=False)])
    assert fabric.call("DELETE", "/x") == {}


LRO = {"x-ms-operation-id": "op", "Location": "https://api/op/1", "Retry-After": "1"}


def test_call_polls_lro_and_fetches_result(monkeypatch, no_sleep):
    seen = script(monkeypatch, [Resp(202, headers=LRO), Resp(body={"status": "Running"}),
                                Resp(body={"status": "Succeeded"}), Resp(body={"id": "new"})])
    assert fabric.call("POST", "/items", {"a": 1}) == {"id": "new"}
    assert seen[-1] == ("GET", "https://api/op/1/result", None)


def test_call_lro_without_result(monkeypatch, no_sleep):
    script(monkeypatch, [Resp(202, headers=LRO), Resp(body={"status": "Succeeded"})])
    assert fabric.call("POST", "/x", want_result=False) == {"status": "Succeeded"}


def test_call_lro_falls_back_to_operation_when_result_missing(monkeypatch, no_sleep):
    seen = []
    responses = iter([Resp(202, headers=LRO), Resp(body={"status": "Succeeded"})])

    def raw(m, p, b=None):
        seen.append(p)
        if p.endswith("/result"):
            raise RuntimeError("HTTP 404")
        return next(responses)
    monkeypatch.setattr(fabric, "raw", raw)
    assert fabric.call("POST", "/x") == {"status": "Succeeded"}


@pytest.mark.parametrize("status", ["Failed", "Cancelled"])
def test_call_lro_failure_raises(monkeypatch, no_sleep, status):
    script(monkeypatch, [Resp(202, headers=LRO), Resp(body={"status": status})])
    with pytest.raises(RuntimeError, match=status):
        fabric.call("POST", "/x")


# ---------------------------------------------------------------- ids
def test_ids_finds_workspace_and_lakehouse_by_name(monkeypatch):
    def call(method, path, body=None, want_result=True):
        if path == "/workspaces":
            return {"value": [{"displayName": "other", "id": "w0"}, {"displayName": fabric.WORKSPACE, "id": "w1"}]}
        assert path == "/workspaces/w1/lakehouses"
        return {"value": [{"displayName": fabric.LAKEHOUSE, "id": "l1"}]}
    monkeypatch.setattr(fabric, "call", call)
    assert fabric.ids() == ("w1", "l1")


# ---------------------------------------------------------------- build_ipynb / definition
SAMPLE = """# %% [markdown]
# # Title
# Some text

# %% [parameters]
year = 2024

# %%
print(year)
x = 1
"""


def test_build_ipynb_splits_cells_and_binds_lakehouse(tmp_path):
    src = tmp_path / "nb.py"
    src.write_text(SAMPLE, encoding="utf-8")
    nb = json.loads(fabric.build_ipynb(str(src), "ws", "lh"))
    kinds = [c["cell_type"] for c in nb["cells"]]
    assert kinds == ["markdown", "code", "code"]
    assert nb["cells"][0]["source"] == ["# Title\n", "Some text"]
    assert nb["cells"][1]["metadata"] == {"tags": ["parameters"]}
    assert nb["cells"][2]["source"] == ["print(year)\n", "x = 1"]
    for c in nb["cells"][1:]:
        assert c["outputs"] == [] and c["execution_count"] is None
    lh = nb["metadata"]["dependencies"]["lakehouse"]
    assert (lh["default_lakehouse"], lh["default_lakehouse_workspace_id"]) == ("lh", "ws")
    assert lh["default_lakehouse_name"] == fabric.LAKEHOUSE


@pytest.mark.parametrize("name", fabric.NOTEBOOKS)
def test_real_notebooks_build_valid_ipynb(name):
    nb = json.loads(fabric.build_ipynb(os.path.join(fabric.NOTEBOOK_DIR, f"{name}.py"), "ws", "lh"))
    assert nb["nbformat"] == 4
    assert nb["cells"][0]["cell_type"] == "markdown"
    code = "".join("".join(c["source"]) + "\n" for c in nb["cells"] if c["cell_type"] == "code")
    compile(code, name, "exec")  # the notebook's code cells are valid Python


def test_definition_wraps_ipynb_as_base64_part():
    d = fabric.definition('{"a": 1}')
    part = d["parts"][0]
    assert d["format"] == "ipynb" and part["path"] == "notebook-content.ipynb"
    assert base64.b64decode(part["payload"]).decode() == '{"a": 1}'


# ---------------------------------------------------------------- deploy
def test_deploy_updates_existing_and_creates_missing_notebooks(monkeypatch, capsys):
    monkeypatch.setattr(fabric, "ids", lambda: ("ws", "lh"))
    calls = []

    def call(method, path, body=None, want_result=True):
        calls.append((method, path))
        if path == "/workspaces/ws/notebooks":
            return {"value": [{"displayName": "01_bronze_ingest", "id": "n1"}]}
        return {"id": "created"}
    monkeypatch.setattr(fabric, "call", call)
    fabric.deploy()
    assert ("POST", "/workspaces/ws/notebooks/n1/updateDefinition") in calls
    assert calls.count(("POST", "/workspaces/ws/items")) == 2
    out = capsys.readouterr().out
    assert "updated  01_bronze_ingest" in out and "created  03_gold_aggregate" in out


# ---------------------------------------------------------------- run
@pytest.fixture
def run_env(monkeypatch, no_sleep):
    monkeypatch.setattr(fabric, "ids", lambda: ("ws", "lh"))

    def setup(active_jobs, polls):
        def call(method, path, body=None, want_result=True):
            if path.endswith("/notebooks"):
                return {"value": [{"displayName": "01_bronze_ingest", "id": "nb"}]}
            return {"value": active_jobs}
        monkeypatch.setattr(fabric, "call", call)
        posted, it = [], iter(polls)

        def raw(method, path, body=None):
            if method == "POST":
                posted.append(body)
                return Resp(202, headers={"Location": "https://api/jobs/j9"})
            p = next(it)
            if isinstance(p, Exception):
                raise p
            return Resp(body=p)
        monkeypatch.setattr(fabric, "raw", raw)
        return posted
    return setup


def test_run_submits_job_with_default_lakehouse_and_waits(run_env):
    posted = run_env([], [{"status": "InProgress"}, {"status": "Completed"}])
    assert fabric.run("01_bronze_ingest") == "Completed"
    cfg = posted[0]["executionData"]["configuration"]["defaultLakehouse"]
    assert cfg == {"id": "lh", "name": fabric.LAKEHOUSE, "workspaceId": "ws"}


def test_run_reuses_in_flight_job_instead_of_submitting(run_env, capsys):
    posted = run_env([{"id": "j1", "status": "InProgress"}], [{"status": "Completed"}])
    assert fabric.run("01_bronze_ingest") == "Completed"
    assert posted == [] and "reusing in-flight job j1" in capsys.readouterr().out


def test_run_keeps_polling_through_network_errors_and_reports_failure(run_env, capsys):
    run_env([], [requests.exceptions.ConnectionError("dns"),
                 {"status": "Failed", "failureReason": {"message": "boom"}}])
    assert fabric.run("01_bronze_ingest") == "Failed"
    out = capsys.readouterr().out
    assert "poll failed" in out and "boom" in out


def test_run_times_out(run_env, monkeypatch):
    run_env([], [{"status": "InProgress"}] * 3)
    clock = itertools.count(0, 10_000)
    monkeypatch.setattr(fabric.time, "time", lambda: next(clock))
    assert fabric.run("01_bronze_ingest", timeout_min=1) == "Timeout"
