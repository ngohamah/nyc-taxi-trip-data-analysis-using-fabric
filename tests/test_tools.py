"""tools/ and checks/: Livy debugging, README diagrams, project stats, DAX check helper, and script hygiene."""
import glob, json, os, py_compile
import pytest
from PIL import Image
from conftest import ROOT, load_notebook
import livy, make_diagrams, project_stats


class R:
    def __init__(self, body=None, headers=None):
        self._b, self.headers = body or {}, headers or {}

    def json(self):
        return self._b

    def raise_for_status(self):
        pass


# ---------------------------------------------------------------- livy
def test_livy_base_path(monkeypatch):
    monkeypatch.setattr(livy.fabric, "ids", lambda: ("ws", "lh"))
    assert livy.base() == "/workspaces/ws/lakehouses/lh/livyApi/versions/2023-12-01/sessions"


@pytest.fixture
def session_file(tmp_path, monkeypatch, no_sleep):
    f = tmp_path / ".livy_session"
    monkeypatch.setattr(livy, "SESSION_FILE", str(f))
    return f


def test_livy_session_reuses_live_session(session_file, monkeypatch):
    session_file.write_text("7")
    monkeypatch.setattr(livy.fabric, "raw", lambda m, p, b=None: R({"state": "idle"}))
    assert livy.session("/s") == "7"


def test_livy_session_starts_new_when_saved_one_is_dead(session_file, monkeypatch):
    session_file.write_text("7")
    states = iter([{"state": "dead"}, {"id": 8}, {"state": "starting"}, {"state": "idle"}])
    calls = []
    monkeypatch.setattr(livy.fabric, "raw", lambda m, p, b=None: calls.append((m, p)) or R(next(states)))
    assert livy.session("/s") == "8"
    assert ("POST", "/s") in calls and session_file.read_text() == "8"


def test_livy_session_survives_lookup_error_and_reports_failed_start(session_file, monkeypatch):
    session_file.write_text("7")

    def raw(m, p, b=None):
        if p == "/s/7":
            raise RuntimeError("HTTP 404")
        return R({"id": 9}) if m == "POST" else R({"state": "error"})
    monkeypatch.setattr(livy.fabric, "raw", raw)
    with pytest.raises(RuntimeError, match="session error"):
        livy.session("/s")


@pytest.mark.parametrize("text", ["done", ["do", "ne"]])
def test_livy_run_prints_output(monkeypatch, no_sleep, capsys, text):
    seq = iter([{"id": 1, "state": "running"}, {"id": 1, "state": "available",
                                                "output": {"status": "ok", "data": {"text/plain": text}}}])
    monkeypatch.setattr(livy.fabric, "raw", lambda m, p, b=None: R(next(seq)))
    assert livy.run("/s", "1", code="print(1)") is True
    assert "done" in capsys.readouterr().out


def test_livy_run_reattaches_and_reports_errors(monkeypatch, no_sleep, capsys):
    seen = []
    body = {"id": 4, "state": "error", "output": {"status": "error", "ename": "ValueError", "evalue": "bad", "traceback": ["line\n"]}}
    monkeypatch.setattr(livy.fabric, "raw", lambda m, p, b=None: seen.append((m, p)) or R(body))
    assert livy.run("/s", "1", stmt_id=4) is False
    assert seen == [("GET", "/s/1/statements/4")]
    assert "ERROR ValueError: bad" in capsys.readouterr().out


def test_livy_cells_skip_markdown_and_neutralise_exit(tmp_path):
    nb = tmp_path / "nb.py"
    nb.write_text("# %% [markdown]\n# Title\n\n# %%\nx = 1\n\n# %%\nnotebookutils.notebook.exit('done')\n", encoding="utf-8")
    cells = livy.cells(str(nb))
    assert len(cells) == 2 and cells[0].strip() == "x = 1" and "print('done')" in cells[1]


# ---------------------------------------------------------------- make_diagrams
def test_font_falls_back_when_windows_fonts_are_missing(monkeypatch):
    monkeypatch.setattr(make_diagrams, "FONTS", "/no/such/dir")
    monkeypatch.setattr(make_diagrams, "FALLBACK", {k: "missing.ttf" for k in make_diagrams.FALLBACK})
    f = make_diagrams.font(12, "bold")
    assert f.getlength("abc") > 0


def test_wrap_respects_width():
    f = make_diagrams.font(15)
    lines = make_diagrams.wrap("one two three four five six seven eight nine ten " * 3, f, 120)
    assert len(lines) > 1
    assert all(f.getlength(l) <= 120 * make_diagrams.S or " " not in l for l in lines)
    assert make_diagrams.wrap("", f, 100) == []


def test_canvas_primitives_draw(tmp_path, monkeypatch):
    monkeypatch.setattr(make_diagrams, "DOCS", str(tmp_path))
    c = make_diagrams.Canvas(200, 120)
    c.rect(5, 5, 80, 40)
    c.rect(100, 5, 80, 40, dash=True)
    c.dashed_line(0, 100, 0, 100, "#000000")       # zero length is a no-op
    c.arrow(10, 60, 150, 100)
    c.text(10, 10, "x", make_diagrams.font(10))
    c.save("canvas.png")
    img = Image.open(tmp_path / "canvas.png")
    assert img.size == (200 * make_diagrams.S, 120 * make_diagrams.S)
    assert len(img.getcolors(maxcolors=1 << 20)) > 2


def test_q_strips_quotes():
    assert make_diagrams.q(" 'Taxi Trips' ") == "Taxi Trips"


def test_parse_model_reads_tables_and_relationships():
    tables, rels = make_diagrams.parse_model()
    assert len(tables) == 9 and len(rels) == 11
    assert tables["Taxi Trips"]["measures"] == 20 and tables["Taxi Trips"]["entity"] == "fact_trips_daily_zone"
    assert "Pickup Date" in tables["Taxi Trips"]["hidden"]
    assert (("Rejections", "Month Type Key"), ("Pipeline Layers", "Month Type Key")) in rels
    assert set(tables) == set(make_diagrams.LAYOUT)


def test_diagrams_render(tmp_path, monkeypatch):
    monkeypatch.setattr(make_diagrams, "DOCS", str(tmp_path))
    make_diagrams.architecture()
    make_diagrams.model_diagram()
    for name in ("architecture.png", "model_relationships.png"):
        w, h = Image.open(tmp_path / name).size
        assert w > 2000 and h > 1500


def test_model_diagram_rejects_unplaced_tables(monkeypatch):
    layout = dict(make_diagrams.LAYOUT)
    layout.pop("Rejections")
    monkeypatch.setattr(make_diagrams, "LAYOUT", layout)
    with pytest.raises(AssertionError, match="Rejections"):
        make_diagrams.model_diagram()


# ---------------------------------------------------------------- project_stats
def test_storage_token(monkeypatch):
    monkeypatch.setattr(project_stats.subprocess, "check_output", lambda *a, **k: "tok\n")
    assert project_stats.storage_token() == "tok"


def test_list_paths_follows_continuation_and_skips_directories(monkeypatch):
    pages = iter([R({"paths": [{"name": "a", "isDirectory": "true"}, {"name": "b", "contentLength": "5"}]},
                    {"x-ms-continuation": "next"}),
                  R({"paths": [{"name": "c", "contentLength": "7"}]})])
    seen = []
    monkeypatch.setattr(project_stats.requests, "get", lambda url, params, headers, timeout: seen.append(params) or next(pages))
    assert [p["name"] for p in project_stats.list_paths("ws", "lh/Tables", "tok")] == ["b", "c"]
    assert "continuation" not in seen[0] and seen[1]["continuation"] == "next"


def test_summarize_storage_groups_by_schema():
    paths = [{"name": "lh/Tables/bronze/t/1.parquet", "contentLength": "10"},
             {"name": "lh/Tables/bronze/t/2.parquet", "contentLength": "5"},
             {"name": "lh/Tables/gold/t/1.parquet"}]
    assert project_stats.summarize_storage(paths, 2) == {"bronze": {"files": 2, "bytes": 15}, "gold": {"files": 1, "bytes": 0}}


def test_model_and_report_counts_match_the_code():
    import report, semantic_model
    assert project_stats.model_counts(semantic_model.build("w", "l")) == {"tables": 9, "relationships": 11, "measures": 32}
    ins = json.load(open(os.path.join(ROOT, "src", "insights.json"), encoding="utf-8"))
    counts = project_stats.report_counts(report.build("w", "m", ins))
    assert counts["pages"] == 5 and counts["tooltip_pages"] == 1 and counts["visuals"] > 80


class Cursor:
    """Answers the SQL that project_stats.main() sends, keyed by a fragment of each query."""
    ANSWERS = [("FROM sys.tables", [("bronze", "yellow_trips_raw"), ("silver", "taxi_trips")]),
               ("COUNT_BIG(*) FROM bronze", [(100,)]), ("COUNT_BIG(*) FROM silver", [(90,)]),
               ("FROM bronze.ingestion_log", [("yellow", 12, 100)]),
               ("FROM gold.dq_rejections", [("negative_amount", 9)]),
               ("FROM gold.dq_layer_counts", [(100, 90, 9, 1)]),
               ("SUM(trip_count)", [(90,)]), ("SUM(total_amount)", [(1234.5,)]), ("FROM sys.views", [(6,)])]

    def execute(self, sql):
        self.rows = next(rows for frag, rows in self.ANSWERS if frag in sql)
        return self

    def fetchall(self):
        return self.rows

    def fetchone(self):
        return self.rows[0]


def test_main_writes_stats_json(monkeypatch, tmp_path, capsys):
    import sql_views
    monkeypatch.setattr(project_stats.fabric, "ids", lambda: ("ws", "lh"))
    monkeypatch.setattr(project_stats.fabric, "ROOT", str(tmp_path))
    (tmp_path / "docs").mkdir()
    monkeypatch.setattr(project_stats, "storage_token", lambda: "tok")
    monkeypatch.setattr(project_stats, "list_paths", lambda ws, d, tok: [
        {"name": "lh/Files/landing/nyc_taxi/yellow/year=2024/y.parquet", "contentLength": "50"},
        {"name": "lh/Files/landing/nyc_taxi/reference/zones.csv", "contentLength": "1"}] if "Files" in d else
        [{"name": "lh/Tables/bronze/t/1.parquet", "contentLength": "70"}])
    monkeypatch.setattr(sql_views, "connect", lambda: type("C", (), {"cursor": lambda s: Cursor(), "close": lambda s: None})())
    project_stats.main()
    stats = json.load(open(tmp_path / "docs" / "project_stats.json"))
    assert stats["source_files"] == {"parquet_files": 1, "parquet_bytes": 50, "all_files": 2}
    assert stats["row_counts"] == {"bronze.yellow_trips_raw": 100, "silver.taxi_trips": 90}
    assert stats["layers"] == {"bronze_rows": 100, "silver_rows": 90, "rejected_rows": 9, "duplicate_rows": 1}
    assert stats["rejections_by_rule"] == {"negative_amount": 9} and stats["sql_views"] == 6
    assert stats["delta_storage_by_schema"] == {"bronze": {"files": 1, "bytes": 70}}


# ---------------------------------------------------------------- checks/check_share.py
def test_check_share_grid_builds_filtered_dax():
    sent = []
    sm = type("SM", (), {"query": staticmethod(sent.append)})
    ns = load_notebook(os.path.join(ROOT, "checks", "check_share.py"), ["grid"], {"sm": sm})
    ns["grid"]('("green", "Bronx")', "'Hourly Demand'[Hour] = 18")
    ns["grid"]("", "TRUE()")
    assert "TREATAS({(\"green\", \"Bronx\")}" in sent[0] and "[Share of Selected Trips]" in sent[0]
    assert "TREATAS" not in sent[1]


# ---------------------------------------------------------------- hygiene for scripts without functions
SCRIPTS = sorted(glob.glob(os.path.join(ROOT, "tools", "*.py")) + glob.glob(os.path.join(ROOT, "checks", "*.py")) +
                 glob.glob(os.path.join(ROOT, "src", "*.py")) + glob.glob(os.path.join(ROOT, "notebooks", "**", "*.py"), recursive=True))


@pytest.mark.parametrize("path", SCRIPTS, ids=lambda p: os.path.relpath(p, ROOT))
def test_every_script_compiles(path, tmp_path):
    py_compile.compile(path, cfile=str(tmp_path / "x.pyc"), doraise=True)


def test_committed_project_stats_are_consistent():
    """The numbers quoted in the README must reconcile: Bronze = Silver + rejected + duplicates."""
    s = json.load(open(os.path.join(ROOT, "docs", "project_stats.json"), encoding="utf-8"))
    L = s["layers"]
    assert L["bronze_rows"] == L["silver_rows"] + L["rejected_rows"] + L["duplicate_rows"]
    assert s["gold_trip_total"] == L["silver_rows"] == s["row_counts"]["silver.taxi_trips"]
    assert sum(s["rejections_by_rule"].values()) == L["rejected_rows"] == s["row_counts"]["silver.taxi_trips_rejected"]
    assert sum(r["rows"] for r in s["ingestion_log"]) == L["bronze_rows"]
