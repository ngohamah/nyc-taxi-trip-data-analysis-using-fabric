"""src/pipeline.py (orchestration) and src/sql_views.py (analyst views)."""
import base64, json, os, re, struct
import pytest
import pipeline, sql_views


# ---------------------------------------------------------------- pipeline
def test_nb_activity_first_step_has_no_dependency():
    a = pipeline.nb_activity("Bronze ingest", "nb1", "ws")
    assert a["type"] == "TridentNotebook" and a["dependsOn"] == []
    assert a["typeProperties"]["notebookId"] == "nb1" and a["typeProperties"]["workspaceId"] == "ws"
    assert a["policy"]["retry"] == 2
    assert set(a["typeProperties"]["parameters"]) == {"year", "months"}


def test_nb_activity_runs_only_after_upstream_succeeds():
    a = pipeline.nb_activity("Gold aggregate", "nb3", "ws", depends_on="Silver transform", params=False)
    assert a["dependsOn"] == [{"activity": "Silver transform", "dependencyConditions": ["Succeeded"]}]
    assert "parameters" not in a["typeProperties"]


@pytest.mark.parametrize("existing", [True, False])
def test_pipeline_deploy(monkeypatch, existing, capsys):
    monkeypatch.setattr(pipeline.fabric, "ids", lambda: ("ws", "lh"))
    calls = []

    def call(method, path, body=None, want_result=True):
        calls.append((method, path, body))
        if path.endswith("/notebooks"):
            return {"value": [{"displayName": n, "id": f"id-{n}"} for n in pipeline.fabric.NOTEBOOKS]}
        if path.endswith("/dataPipelines"):
            return {"value": [{"displayName": pipeline.PIPELINE_NAME, "id": "p1"}] if existing else []}
        return {"id": "p2"}
    monkeypatch.setattr(pipeline.fabric, "call", call)
    pipeline.deploy()
    method, path, body = calls[-1]
    assert path == ("/workspaces/ws/items/p1/updateDefinition" if existing else "/workspaces/ws/items")
    content = json.loads(base64.b64decode(body["definition"]["parts"][0]["payload"]))
    acts = content["properties"]["activities"]
    assert [a["name"] for a in acts] == ["Bronze ingest", "Silver transform", "Gold aggregate"]
    assert [a["typeProperties"]["notebookId"] for a in acts] == [f"id-{n}" for n in pipeline.fabric.NOTEBOOKS]
    assert content["properties"]["parameters"]["year"]["defaultValue"] == 2024
    assert ("updated pipeline p1" if existing else "created pipeline p2") in capsys.readouterr().out


# ---------------------------------------------------------------- sql views
GOLD_NOTEBOOK = os.path.join(pipeline.fabric.NOTEBOOK_DIR, "03_gold_aggregate.py")


def test_six_views_are_defined():
    assert sorted(sql_views.VIEWS) == ["vw_holiday_impact", "vw_hourly_demand", "vw_monthly_kpis",
                                       "vw_pipeline_quality", "vw_trips_daily_zone", "vw_zone_summary"]


@pytest.mark.parametrize("name", sorted(sql_views.VIEWS))
def test_views_only_read_tables_the_gold_notebook_writes(name):
    written = set(re.findall(r'"(gold\.\w+)"', open(GOLD_NOTEBOOK, encoding="utf-8").read()))
    used = set(re.findall(r"\b(gold\.\w+)", sql_views.VIEWS[name]))
    assert used and used <= written, used - written


def test_views_are_select_only():
    for body in sql_views.VIEWS.values():
        assert body.lstrip().upper().startswith(("SELECT", "WITH"))
        assert not re.search(r"\b(INSERT|UPDATE|DELETE|DROP|MERGE)\b", body, re.I)


def test_connect_uses_sql_endpoint_and_access_token(monkeypatch):
    monkeypatch.setattr(sql_views.fabric, "ids", lambda: ("ws", "lh"))
    monkeypatch.setattr(sql_views.fabric, "call", lambda m, p, b=None, want_result=True:
                        {"properties": {"sqlEndpointProperties": {"connectionString": "x.datawarehouse.fabric.microsoft.com"}}})
    monkeypatch.setattr(sql_views.subprocess, "check_output", lambda *a, **k: "abc\n")
    seen = {}
    monkeypatch.setattr(sql_views.mssql_python, "connect", lambda cs, attrs_before: seen.update(cs=cs, attrs=attrs_before) or "conn")
    assert sql_views.connect() == "conn"
    assert seen["cs"] == f"Server=x.datawarehouse.fabric.microsoft.com;Database={sql_views.fabric.LAKEHOUSE};Encrypt=yes;"
    token = "abc".encode("utf-16-le")
    assert seen["attrs"] == {1256: struct.pack(f"<I{len(token)}s", len(token), token)}
