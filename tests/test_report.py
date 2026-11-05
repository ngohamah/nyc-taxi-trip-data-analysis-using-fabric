"""src/report.py: PBIR building blocks, page layouts, model field references and deployment."""
import base64, importlib, json, os, re
import pytest
import report, semantic_model

INSIGHTS = json.load(open(os.path.join(report.fabric.HERE, "insights.json"), encoding="utf-8"))
COMMITTED = os.path.join(report.fabric.ROOT, "powerbi", "NYC Taxi Overview.Report")


@pytest.fixture(scope="module")
def files():
    return report.build("my-workspace", "model-id", INSIGHTS)


def visuals(files):
    return {p: v for p, v in files.items() if p.endswith("/visual.json")}


# ---------------------------------------------------------------- expression helpers
@pytest.mark.parametrize("value,expected", [(True, "true"), (False, "false"), (3, "3D"), (9.5, "9.5D"),
                                            ("Top", "'Top'"), ("it's", "'it''s'")])
def test_lit_encodes_literals(value, expected):
    assert report.lit(value) == {"expr": {"Literal": {"Value": expected}}}


def test_color_and_props():
    assert report.color("#FFFFFF") == {"solid": {"color": report.lit("#FFFFFF")}}
    p = report.props(show=True, color=report.color("#000000"))
    assert p == [{"properties": {"show": report.lit(True), "color": report.color("#000000")}}]


def test_field_references():
    assert report.fcol("Date", "Month") == {"Column": {"Expression": {"SourceRef": {"Entity": "Date"}}, "Property": "Month"}}
    assert report.fmea("Taxi Trips", "Trips")["Measure"]["Property"] == "Trips"
    c = report.column("Date", "Month")
    assert c["queryRef"] == "Date.Month" and c["nativeQueryRef"] == "Month" and "displayName" not in c
    m = report.measure("Taxi Trips", "Revenue Contribution %", "Revenue share")
    assert m["displayName"] == "Revenue share" and m["field"] == report.fmea("Taxi Trips", "Revenue Contribution %")


def test_hide_blank_excludes_null():
    f = report.hide_blank("Borough", "Borough")["filters"][0]
    assert f["name"] == "noBlankBorough" and f["type"] == "Categorical"
    cond = f["filter"]["Where"][0]["Condition"]["Not"]["Expression"]["In"]
    assert cond["Values"] == [[{"Literal": {"Value": "null"}}]]


def test_taxi_colors_pin_yellow_and_green():
    rules = report.taxi_colors()
    pairs = {r["selector"]["data"][0]["scopeId"]["Comparison"]["Right"]["Literal"]["Value"]: r["properties"]["fill"] for r in rules}
    assert pairs == {"'yellow'": report.color(report.YELLOW), "'green'": report.color(report.GREEN)}


def test_heat_is_white_to_colour_gradient_on_measure():
    rule = report.heat("Hourly Demand", "Share of Selected Trips")[0]
    fr = rule["properties"]["backColor"]["solid"]["color"]["expr"]["FillRule"]
    assert fr["Input"] == report.fmea("Hourly Demand", "Share of Selected Trips")
    assert fr["FillRule"]["linearGradient2"]["max"]["color"]["Literal"]["Value"] == f"'{report.YELLOW}'"
    assert rule["selector"]["metadata"] == "Hourly Demand.Share of Selected Trips"


# ---------------------------------------------------------------- visual builders
def test_box_with_and_without_title():
    titled = report.box("Pickup zones")["title"][0]["properties"]
    assert titled["text"] == report.lit("Pickup zones") and titled["background"] == report.color(report.CHAR)
    assert report.box()["title"] == report.props(show=False)


def test_visual_container():
    sort = [(report.column("Date", "Month Number"), "Ascending")]
    v = report.visual("v1", "lineChart", (1, 2, 3, 4), {"Y": [report.measure("Taxi Trips", "Trips")]}, "T",
                      objects={"legend": report.props(show=True)}, sort=sort, z=5)
    assert v["name"] == "v1" and v["position"] == {"x": 1, "y": 2, "z": 5, "width": 3, "height": 4}
    assert v["visual"]["query"]["queryState"]["Y"]["projections"][0]["queryRef"] == "Taxi Trips.Trips"
    assert v["visual"]["query"]["sortDefinition"]["sort"][0]["direction"] == "Ascending"
    assert "legend" in v["visual"]["objects"]
    plain = report.visual("v2", "card", (0, 0, 1, 1), {})
    assert "objects" not in plain["visual"] and "sortDefinition" not in plain["visual"]["query"]


def test_card_precision_is_optional():
    m = report.measure("Taxi Trips", "Trips")
    assert "labelPrecision" not in report.card("c", (0, 0, 1, 1), m)["visual"]["objects"]["labels"][0]["properties"]
    assert report.card("c", (0, 0, 1, 1), m, precision=2)["visual"]["objects"]["labels"][0]["properties"]["labelPrecision"] == report.lit(2)


def test_series_fill_and_table_style():
    assert report.series_fill("Taxi Trips", "Trips", "#000000")["selector"] == {"metadata": "Taxi Trips.Trips"}
    assert "total" not in report.table_style()
    assert report.table_style(totals=False, size=10)["total"] == report.props(totals=False)


def test_slicer_dropdown_multi_select_synced():
    s = report.slicer("s", (0, 0, 1, 1), report.column("Month", "Month"), "Months", sync="syncMonth")
    o = s["visual"]["objects"]
    assert o["data"] == report.props(mode="Dropdown")
    assert o["selection"][0]["properties"]["singleSelect"] == report.lit(False)
    assert s["visual"]["syncGroup"] == {"groupName": "syncMonth", "fieldChanges": True, "filterChanges": True}
    assert s["filterConfig"]["filters"][0]["name"] == "noBlankMonth"
    assert "general" not in o


def test_slicer_horizontal_tiles_with_preselection():
    s = report.slicer("s", (0, 0, 1, 1), report.column("Borough", "Borough"), "B", horizontal=True,
                      selected=["Manhattan", "Queens"])
    general = s["visual"]["objects"]["general"][0]["properties"]
    assert general["orientation"] == report.lit(1)
    values = general["filter"]["filter"]["Where"][0]["Condition"]["In"]["Values"]
    assert values == [[{"Literal": {"Value": "'Manhattan'"}}], [{"Literal": {"Value": "'Queens'"}}]]
    assert "syncGroup" not in s["visual"]


def test_shared_slicers_use_the_conformed_tables():
    assert report.taxi_slicer("p")["visual"]["syncGroup"]["groupName"] == "syncTaxi"
    assert report.month_slicer("p", 10)["visual"]["query"]["queryState"]["Values"]["projections"][0]["queryRef"] == "Month.Month"
    b = report.borough_slicer("p", 20)
    assert b["name"] == "pBorough" and b["position"]["x"] == 20


def test_borough_preselect_from_environment(monkeypatch):
    monkeypatch.setenv("NYC_PRESELECT_BOROUGH", "Manhattan")
    monkeypatch.setenv("NYC_REPORT_NAME", "Snapshot copy")
    mod = importlib.reload(report)
    try:
        assert mod.REPORT_NAME == "Snapshot copy" and mod.PRESELECT_BOROUGH == ["Manhattan"]
        assert "filter" in mod.borough_slicer("p", 0)["visual"]["objects"]["general"][0]["properties"]
    finally:
        monkeypatch.delenv("NYC_PRESELECT_BOROUGH")
        monkeypatch.delenv("NYC_REPORT_NAME")
        importlib.reload(report)


def test_textbox_and_run():
    r = report.run("Hi", 12, True, "#111111")
    assert r == {"value": "Hi", "textStyle": {"fontSize": "12pt", "color": "#111111", "fontWeight": "bold"}}
    assert "fontWeight" not in report.run("x")["textStyle"]
    t = report.textbox("t", (1, 2, 3, 4), [{"textRuns": [r]}], {}, z=7)
    assert t["visual"]["visualType"] == "textbox" and t["position"]["z"] == 7


def test_header_has_bar_strip_and_navigator_without_tooltip_pages():
    bar, strip, nav = report.header("Subtitle")
    assert bar["visual"]["objects"]["general"][0]["properties"]["paragraphs"][1]["textRuns"][0]["value"] == "Subtitle"
    assert strip["position"]["y"] == 66
    pages = nav["visual"]["objects"]["pages"][0]["properties"]
    assert pages["showTooltipPages"] == report.lit(False) and pages["showHiddenPages"] == report.lit(False)


@pytest.mark.parametrize("text,bold", [("plain", []), ("**41.1M** trips", ["41.1M"]),
                                       ("a **b** c **d**", ["b", "d"])])
def test_rich_bolds_marked_numbers(text, bold):
    runs = report.rich(text)
    assert "".join(r["value"] for r in runs) == text.replace("**", "")
    assert [r["value"] for r in runs if r["textStyle"].get("fontWeight") == "bold"] == bold


def test_insights_cards_fill_the_panel():
    items = INSIGHTS["overview"]
    out = report.insights("ins", (10, 100, 300, 600), items)
    assert len(out) == len(items) + 1 and out[0]["name"] == "insCap"
    last = out[-1]["position"]
    assert last["y"] + last["height"] <= 100 + 600 + 1
    assert out[1]["visual"]["visualContainerObjects"]["title"][0]["properties"]["text"] == report.lit(items[0][0].upper())


def test_cards_row_spreads_cards_across_width():
    ms = [report.measure("Taxi Trips", "Trips")] * 4
    cards = report.cards_row("c", 50, ms, precision={1: 2})
    assert [c["name"] for c in cards] == ["c0", "c1", "c2", "c3"]
    assert cards[0]["position"]["x"] == report.M
    assert abs(cards[-1]["position"]["x"] + cards[-1]["position"]["width"] - (report.W - report.M)) <= 1
    assert "labelPrecision" in cards[1]["visual"]["objects"]["labels"][0]["properties"]


# ---------------------------------------------------------------- pages
PAGE_FUNCS = {"overview": report.page_overview, "compare": report.page_compare, "zones": report.page_zones,
              "demand": report.page_demand, "quality": report.page_quality}


@pytest.mark.parametrize("pid", PAGE_FUNCS)
def test_each_page_fits_the_canvas_with_unique_visual_names(pid):
    vs = PAGE_FUNCS[pid](INSIGHTS[pid])
    names = [v["name"] for v in vs]
    assert len(names) == len(set(names))
    for v in vs:
        p = v["position"]
        assert p["x"] >= 0 and p["y"] >= 0, v["name"]
        assert p["x"] + p["width"] <= report.W + 1 and p["y"] + p["height"] <= report.PAGE_H + 1, v["name"]


def test_slicer_rules_per_page():
    def slicers(pid):
        return {v["visual"].get("syncGroup", {}).get("groupName") for v in PAGE_FUNCS[pid](INSIGHTS[pid])
                if v["visual"].get("visualType") == "slicer"}
    assert slicers("overview") == {"syncTaxi", "syncMonth", "syncBorough"}
    assert "syncTaxi" not in slicers("compare")        # the page always compares both taxi types
    assert "syncBorough" not in slicers("quality")     # pipeline counts are per file, not per borough


def test_cell_tooltip_page_fits():
    for v in report.page_cell_tooltip():
        p = v["position"]
        assert p["x"] + p["width"] <= report.TIP_W and p["y"] + p["height"] <= report.TIP_H


# ---------------------------------------------------------------- build
def test_build_has_pages_tooltip_theme_and_connection(files):
    order = files["definition/pages/pages.json"]["pageOrder"]
    assert order == ["overview", "compare", "zones", "demand", "quality", report.TOOLTIP_PAGE]
    tip = files[f"definition/pages/{report.TOOLTIP_PAGE}/page.json"]
    assert tip["pageBinding"]["type"] == "Tooltip" and tip["visibility"] == "HiddenInViewMode"
    conn = files["definition.pbir"]["datasetReference"]["byConnection"]
    assert "my-workspace" in conn["connectionString"] and conn["pbiModelDatabaseName"] == "model-id"
    assert files["StaticResources/RegisteredResources/NYCTaxiTheme.json"]["dataColors"][:2] == [report.YELLOW, report.CHAR]


def test_every_visual_field_exists_in_the_semantic_model(files):
    """A typo in a measure or column name would render as a broken visual; catch it before deploying."""
    model = semantic_model.build("ws", "lh")
    known = set()
    for p, c in model.items():
        if p.startswith("definition/tables/"):
            t = p.split("/")[-1][:-5]
            known |= {(t, n.strip("'")) for n in re.findall(r"^\t(?:column|measure) ('[^']+'|[^\s=]+)", c, re.M)}
    refs = set()

    def walk(o):
        if isinstance(o, dict):
            for kind in ("Column", "Measure"):
                f = o.get(kind)
                if isinstance(f, dict) and "Entity" in f.get("Expression", {}).get("SourceRef", {}):
                    refs.add((f["Expression"]["SourceRef"]["Entity"], f["Property"]))
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)
    walk(files)
    assert refs and refs <= known, sorted(refs - known)


def test_insights_json_has_text_for_every_page():
    assert set(INSIGHTS) == set(PAGE_FUNCS)
    for items in INSIGHTS.values():
        assert 3 <= len(items) <= 5
        for label, text in items:
            assert label and text.count("**") % 2 == 0


def test_committed_report_matches_generator(files):
    """powerbi/ holds the report as deployed; it must equal what build() generates."""
    for path, content in files.items():
        if path == "definition.pbir":   # the local copy points at the model folder instead of the service
            continue
        committed = json.load(open(os.path.join(COMMITTED, *path.split("/")), encoding="utf-8"))
        assert committed == content, f"{path} differs from src/report.py output"
    pbir = json.load(open(os.path.join(COMMITTED, "definition.pbir"), encoding="utf-8"))
    assert pbir["datasetReference"]["byPath"]["path"] == "../NYC Taxi.SemanticModel"


# ---------------------------------------------------------------- deploy
@pytest.mark.parametrize("existing", [True, False])
def test_deploy(monkeypatch, existing, capsys):
    monkeypatch.setattr(report.fabric, "ids", lambda: ("ws", "lh"))
    monkeypatch.setattr(report.semantic_model, "model_id", lambda ws: "m1")
    calls = []

    def call(method, path, body=None, want_result=True):
        calls.append((method, path, body))
        if path == "/workspaces":
            return {"value": [{"id": "ws", "displayName": "ngoh-fabric-dev"}]}
        if path.endswith("/reports") and method == "GET":
            return {"value": [{"displayName": report.REPORT_NAME, "id": "r1"}] if existing else []}
        return {"id": "r2"}
    monkeypatch.setattr(report.fabric, "call", call)
    report.deploy()
    method, path, body = calls[-1]
    assert path == ("/workspaces/ws/reports/r1/updateDefinition" if existing else "/workspaces/ws/reports")
    parts = {p["path"]: json.loads(base64.b64decode(p["payload"])) for p in body["definition"]["parts"]}
    assert body["definition"]["format"] == "PBIR" and "definition/pages/pages.json" in parts
    assert "ngoh-fabric-dev" in parts["definition.pbir"]["datasetReference"]["byConnection"]["connectionString"]
    assert ("updated report r1" if existing else "created report r2") in capsys.readouterr().out
