"""src/semantic_model.py: TMDL generation, deploy, refresh and DAX query helpers."""
import base64, json, os, re
import pytest
import semantic_model as sm

COMMITTED = os.path.join(sm.fabric.ROOT, "powerbi", "NYC Taxi.SemanticModel")


@pytest.fixture(scope="module")
def files():
    return sm.build("ws-id", "lh-id")


# ---------------------------------------------------------------- small TMDL helpers
def test_T_is_tabs():
    assert sm.T(0) == "" and sm.T(3) == "\t\t\t"


@pytest.mark.parametrize("name,expected", [
    ("Trips", "Trips"), ("Taxi Trips", "'Taxi Trips'"), ("Card Tip %", "'Card Tip %'"),
    ("Average Distance (miles)", "'Average Distance (miles)'"), ("a.b", "'a.b'"), ("k=v", "'k=v'")])
def test_q_quotes_names_that_need_it(name, expected):
    assert sm.q(name) == expected


def test_col_minimal_numeric_column():
    out = sm.col("Trip Count", "trip_count", "int64")
    assert out == "\tcolumn 'Trip Count'\n\t\tdataType: int64\n\t\tsourceColumn: trip_count\n"


def test_col_with_all_options():
    out = sm.col("Month", "month_name", "string", hidden=True, fmt="0", sort_by="Month Number",
                 desc="Month name.", extra=("isKey",))
    lines = out.splitlines()
    assert lines[0] == "\t/// Month name."
    assert "\t\tisHidden" in lines and "\t\tformatString: 0" in lines
    assert "\t\tsummarizeBy: none" in lines  # strings never summarize
    assert "\t\tsortByColumn: 'Month Number'" in lines and lines[-1] == "\t\tisKey"


def test_col_explicit_summarize():
    assert "\t\tsummarizeBy: sum" in sm.col("Fare", "fare", "decimal", summarize="sum")


def test_measure_single_line_with_folder():
    out = sm.measure("Trips", "SUM(x[y])", "#,##0", "Count of trips.", "Volume")
    assert out == "\t/// Count of trips.\n\tmeasure Trips = SUM(x[y])\n\t\tformatString: #,##0\n\t\tdisplayFolder: Volume\n"


def test_measure_multi_line_uses_triple_backticks():
    out = sm.measure("Typical", "VAR a = 1\nRETURN a", "0", "desc")
    assert "\tmeasure Typical = ```\n\t\t\tVAR a = 1\n\t\t\tRETURN a\n\t\t\t```" in out
    assert "displayFolder" not in out


def test_partition_is_direct_lake_on_gold():
    out = sm.partition("Taxi Trips", "fact_trips_daily_zone")
    assert "partition 'Taxi Trips' = entity" in out
    assert "mode: directLake" in out and "schemaName: gold" in out and "entityName: fact_trips_daily_zone" in out
    assert f"expressionSource: '{sm.EXPR}'" in out


def test_table_assembles_header_body_and_partition():
    out = sm.table("Borough", "Slicer table.", ["\tcolumn Borough\n"], "dim_borough", extra_header=("lineageTag: x",))
    assert out.startswith("/// Slicer table.\ntable Borough\n\tlineageTag: x\n\n\tcolumn Borough\n")
    assert out.rstrip().endswith("expressionSource: 'DirectLake - NYC Taxi'")


# ---------------------------------------------------------------- build
def test_build_produces_every_model_file(files):
    tables = sorted(p.split("/")[-1][:-5] for p in files if p.startswith("definition/tables/"))
    assert tables == ["Borough", "Date", "Hourly Demand", "Month", "Pickup Zone", "Pipeline Layers",
                      "Rejections", "Taxi Trips", "Taxi Type"]
    for p in ("definition.pbism", "definition/database.tmdl", "definition/model.tmdl",
              "definition/expressions.tmdl", "definition/relationships.tmdl"):
        assert p in files
    json.loads(files["definition.pbism"])
    assert "compatibilityLevel: 1702" in files["definition/database.tmdl"]


def test_build_points_direct_lake_at_the_lakehouse(files):
    assert "https://onelake.dfs.fabric.microsoft.com/ws-id/lh-id" in files["definition/expressions.tmdl"]


def test_model_references_every_table_file(files):
    refs = {sm.q(n.strip("'")) for n in re.findall(r"ref table ('[^']+'|\S+)", files["definition/model.tmdl"])}
    tables = {sm.q(p.split("/")[-1][:-5]) for p in files if p.startswith("definition/tables/")}
    assert refs == tables


def test_relationships_join_existing_columns(files):
    columns = {}
    for p, c in files.items():
        if p.startswith("definition/tables/"):
            name = p.split("/")[-1][:-5]
            columns[name] = {m.strip("'") for m in re.findall(r"^\tcolumn ('[^']+'|\S+)", c, re.M)}
    rels = re.findall(r"fromColumn: (.+)\n\ttoColumn: (.+)", files["definition/relationships.tmdl"])
    assert len(rels) == 11
    for ref in (r for pair in rels for r in pair):
        tbl, colname = re.match(r"('[^']+'|[^.]+)\.(.+)$", ref).groups()
        assert colname.strip("'") in columns[tbl.strip("'")], ref


def test_measure_names_are_unique_and_documented(files):
    tmdl = "".join(c for p, c in files.items() if p.endswith(".tmdl"))
    names = re.findall(r"^\tmeasure ('[^']+'|\S+) =", tmdl, re.M)
    assert len(names) == len(set(names)) == 32
    for m in re.finditer(r"(.*)\n\tmeasure ", tmdl):
        assert m.group(1).startswith("\t/// "), "every measure needs a description"


def test_committed_model_matches_generator():
    """powerbi/ must be what build() produces (guards against hand edits drifting from the code)."""
    expr = open(os.path.join(COMMITTED, "definition", "expressions.tmdl"), encoding="utf-8").read()
    ws, lh = re.search(r"fabric\.microsoft\.com/([0-9a-f-]+)/([0-9a-f-]+)", expr).groups()
    for path, content in sm.build(ws, lh).items():
        committed = open(os.path.join(COMMITTED, *path.split("/")), encoding="utf-8").read().replace("\r\n", "\n")
        assert committed == content, f"{path} differs from src/semantic_model.py output - run: python src/semantic_model.py deploy"


# ---------------------------------------------------------------- write / parts
def test_write_creates_files(tmp_path, monkeypatch, files, capsys):
    monkeypatch.setattr(sm, "OUT", str(tmp_path))
    sm.write(files)
    assert (tmp_path / "definition" / "tables" / "Taxi Trips.tmdl").read_text(encoding="utf-8") == files["definition/tables/Taxi Trips.tmdl"]
    assert f"wrote {len(files)} files" in capsys.readouterr().out


def test_parts_base64_round_trip(files):
    d = sm.parts(files)
    assert d["format"] == "TMDL" and len(d["parts"]) == len(files)
    first = d["parts"][0]
    assert base64.b64decode(first["payload"]).decode() == files[first["path"]]


# ---------------------------------------------------------------- service calls
def models(monkeypatch, names):
    monkeypatch.setattr(sm.fabric, "call", lambda m, p, b=None, want_result=True:
                        {"value": [{"displayName": n, "id": f"id{i}"} for i, n in enumerate(names)]})


def test_model_id_found(monkeypatch):
    models(monkeypatch, ["other", sm.MODEL_NAME])
    assert sm.model_id("ws") == "id1"


def test_model_id_missing(monkeypatch):
    models(monkeypatch, ["other"])
    assert sm.model_id("ws") is None


def test_model_id_rejects_duplicates(monkeypatch):
    models(monkeypatch, [sm.MODEL_NAME, sm.MODEL_NAME])
    with pytest.raises(RuntimeError, match="duplicates"):
        sm.model_id("ws")


@pytest.mark.parametrize("existing", [True, False])
def test_deploy_updates_or_creates(monkeypatch, tmp_path, existing, capsys):
    monkeypatch.setattr(sm, "OUT", str(tmp_path))
    monkeypatch.setattr(sm.fabric, "ids", lambda: ("ws", "lh"))
    ids = iter(["m1", "m1"] if existing else [None, "m2"])
    monkeypatch.setattr(sm, "model_id", lambda ws: next(ids))
    calls = []
    monkeypatch.setattr(sm.fabric, "call", lambda m, p, b=None, want_result=True: calls.append((m, p, b)) or {})
    sm.deploy()
    method, path, body = calls[0]
    assert path == ("/workspaces/ws/semanticModels/m1/updateDefinition" if existing else "/workspaces/ws/semanticModels")
    assert body["definition"]["format"] == "TMDL"
    assert ("updated semantic model m1" if existing else "created semantic model m2") in capsys.readouterr().out


class R:
    def __init__(self, status=200, body=None):
        self.status_code, self._b, self.text = status, body or {}, json.dumps(body or {})

    def json(self):
        return self._b


def test_pbi_sends_bearer_token(monkeypatch):
    monkeypatch.setattr(sm.os, "popen", lambda cmd: type("P", (), {"read": lambda self: "tok\n"})())
    seen = {}
    monkeypatch.setattr(sm.requests, "request", lambda m, url, headers, json, timeout: seen.update(url=url, h=headers) or R())
    sm.pbi("GET", "/groups")
    assert seen["url"] == sm.PBI + "/groups" and seen["h"]["Authorization"] == "Bearer tok"


def test_pbi_raises_on_error(monkeypatch):
    monkeypatch.setattr(sm.os, "popen", lambda cmd: type("P", (), {"read": lambda self: "tok"})())
    monkeypatch.setattr(sm.requests, "request", lambda *a, **k: R(400, {"error": "bad"}))
    with pytest.raises(RuntimeError, match="HTTP 400"):
        sm.pbi("POST", "/x")


def service(monkeypatch, responses):
    monkeypatch.setattr(sm.fabric, "ids", lambda: ("ws", "lh"))
    monkeypatch.setattr(sm, "model_id", lambda ws: "m1")
    seen, it = [], iter(responses)
    monkeypatch.setattr(sm, "pbi", lambda m, p, b=None: seen.append((m, p, b)) or R(body=next(it)))
    return seen


@pytest.mark.parametrize("final", ["Completed", "Failed"])
def test_refresh_waits_for_final_status(monkeypatch, no_sleep, final):
    seen = service(monkeypatch, [{}, {"value": [{"status": "Unknown"}]}, {"value": [{"status": final}]}])
    assert sm.refresh() == final
    assert seen[0] == ("POST", "/groups/ws/datasets/m1/refreshes", {"type": "full"})


def test_query_prints_rows(monkeypatch, capsys):
    service(monkeypatch, [{"results": [{"tables": [{"rows": [{"[Trips]": 41077632}]}]}]}])
    sm.query("EVALUATE ROW(\"Trips\", [Trips])")
    assert json.loads(capsys.readouterr().out) == {"[Trips]": 41077632}


def test_query_raises_dax_errors(monkeypatch):
    service(monkeypatch, [{"results": [{"error": {"code": "DAX"}}]}])
    with pytest.raises(RuntimeError, match="DAX"):
        sm.query("bad")
