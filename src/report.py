"""Generate + deploy the 'NYC Taxi Overview' PBIR report (5 pages) on the 'NYC Taxi' semantic model.

Usage: python src/report.py deploy
"""
import base64, json, os, sys
import fabric, semantic_model

# Overridable for snapshot copies: NYC_REPORT_NAME, and NYC_PRESELECT_BOROUGH (comma-separated) to pre-tick the borough slicer
REPORT_NAME = os.environ.get("NYC_REPORT_NAME", "NYC Taxi Overview")
PRESELECT_BOROUGH = [b for b in os.environ.get("NYC_PRESELECT_BOROUGH", "").split(",") if b]
SCHEMA = "https://developer.microsoft.com/json-schemas/fabric/item/report/definition"
CHAR, YELLOW, GREEN, BG, WHITE, LINE = "#263238", "#F7B500", "#2E9E44", "#EEF0F2", "#FFFFFF", "#D5DADF"
T, D, Z, H, L, R = "Taxi Trips", "Date", "Pickup Zone", "Hourly Demand", "Pipeline Layers", "Rejections"
W, PAGE_H, M = 1280, 720, 12          # page size and outer margin
TOP = 82                                # body starts below the header


# ---------- expression helpers ----------
def lit(v):
    if isinstance(v, bool):
        s = "true" if v else "false"
    elif isinstance(v, (int, float)):
        s = f"{v}D"
    else:
        s = "'" + str(v).replace("'", "''") + "'"
    return {"expr": {"Literal": {"Value": s}}}


def color(hex_):
    return {"solid": {"color": lit(hex_)}}


def props(**kw):
    return [{"properties": {k: (v if isinstance(v, dict) else lit(v)) for k, v in kw.items()}}]


def fcol(entity, prop):
    return {"Column": {"Expression": {"SourceRef": {"Entity": entity}}, "Property": prop}}


def fmea(entity, prop):
    return {"Measure": {"Expression": {"SourceRef": {"Entity": entity}}, "Property": prop}}


def column(entity, prop, label=None):
    p = {"field": fcol(entity, prop), "queryRef": f"{entity}.{prop}", "nativeQueryRef": prop}
    if label:
        p["displayName"] = label
    return p


def measure(entity, prop, label=None):
    p = {"field": fmea(entity, prop), "queryRef": f"{entity}.{prop}", "nativeQueryRef": prop}
    if label:
        p["displayName"] = label
    return p


def hide_blank(entity, prop):
    """Visual-level filter: exclude (Blank) from a slicer."""
    return {"filters": [{"name": f"noBlank{prop.replace(' ', '')}", "field": fcol(entity, prop), "type": "Categorical",
                         "filter": {"Version": 2, "From": [{"Name": "x", "Entity": entity, "Type": 0}],
                                    "Where": [{"Condition": {"Not": {"Expression": {"In": {
                                        "Expressions": [{"Column": {"Expression": {"SourceRef": {"Source": "x"}}, "Property": prop}}],
                                        "Values": [[{"Literal": {"Value": "null"}}]]}}}}}]},
                         "howCreated": "User"}]}


def taxi_colors(entity=T):
    """Pin series colours: yellow taxis in taxi yellow, green taxis in green."""
    return [{"properties": {"fill": color(c)},
             "selector": {"data": [{"scopeId": {"Comparison": {"ComparisonKind": 0, "Left": fcol(entity, "Taxi Type"),
                                                               "Right": {"Literal": {"Value": f"'{v}'"}}}}}]}}
            for v, c in (("yellow", YELLOW), ("green", GREEN))]


def heat(entity, prop, hi=YELLOW):
    """Matrix cell background: white -> yellow gradient on a measure."""
    return [{"properties": {"backColor": {"solid": {"color": {"expr": {"FillRule": {
        "Input": fmea(entity, prop),
        "FillRule": {"linearGradient2": {"min": {"color": {"Literal": {"Value": "'#FFFFFF'"}}},
                                         "max": {"color": {"Literal": {"Value": f"'{hi}'"}}},
                                         "nullColoringStrategy": {"strategy": {"Literal": {"Value": "'asZero'"}}}}}}}}}}},
             "selector": {"data": [{"dataViewWildcard": {"matchingOption": 1}}], "metadata": f"{entity}.{prop}"}}]


# ---------- visual builders ----------
def box(title=None):
    """White rounded box with a charcoal title bar (the 'card' look)."""
    o = {"background": props(show=True, color=color(WHITE), transparency=0),
         "border": props(show=True, color=color(LINE), radius=8),
         "dropShadow": props(show=True),
         "padding": props(top=6, bottom=6, left=8, right=8)}
    o["title"] = (props(show=True, text=title, fontColor=color(WHITE), background=color(CHAR), fontSize=11, bold=True)
                  if title else props(show=False))
    return o


def visual(name, vtype, pos, roles, title=None, objects=None, sort=None, container=None, z=100):
    x, y, w, h = pos
    q = {"queryState": {role: {"projections": projs} for role, projs in roles.items()}}
    if sort:
        q["sortDefinition"] = {"sort": [{"field": f["field"], "direction": d} for f, d in sort], "isDefaultSort": True}
    v = {"$schema": f"{SCHEMA}/visualContainer/1.0.0/schema.json", "name": name,
         "position": {"x": x, "y": y, "z": z, "width": w, "height": h},
         "visual": {"visualType": vtype, "query": q, "drillFilterOtherVisuals": True,
                    "visualContainerObjects": container or box(title)}}
    if objects:
        v["visual"]["objects"] = objects
    return v


def card(name, pos, m, precision=None):
    labels = dict(fontSize=22, color=color(CHAR), labelDisplayUnits=0)
    if precision is not None:
        labels["labelPrecision"] = precision
    return visual(name, "card", pos, {"Values": [m]},
                  objects={"labels": props(**labels),
                           "categoryLabels": props(show=True, fontSize=10, color=color("#5F6B73"))})


def series_fill(entity, prop, hex_):
    """Colour one measure series (works for single-series line charts and combo charts)."""
    return {"properties": {"fill": color(hex_)}, "selector": {"metadata": f"{entity}.{prop}"}}


def table_style(totals=True, size=9):
    o = {"values": props(fontSize=size), "columnHeaders": props(fontSize=size, wordWrap=True)}
    if not totals:
        o["total"] = props(totals=False)
    return o


NO_AXIS_TITLES = {"categoryAxis": props(showAxisTitle=False), "valueAxis": props(showAxisTitle=False)}


def slicer(name, pos, c, title, horizontal=False, sync=None, selected=None):
    """Multi-select slicer (checkboxes / toggle tiles, no Ctrl needed). sync = group name shared across pages."""
    obj = {"data": props(mode="Basic" if horizontal else "Dropdown"),
           "header": props(show=False),
           "items": props(fontSize=10),
           "selection": props(singleSelect=False, selectAllCheckboxEnabled=not horizontal)}
    if horizontal:
        obj["general"] = props(orientation=1)
    if selected:
        ent, prop = c["field"]["Column"]["Expression"]["SourceRef"]["Entity"], c["field"]["Column"]["Property"]
        sel = {"filter": {"Version": 2, "From": [{"Name": "s", "Entity": ent, "Type": 0}],
                          "Where": [{"Condition": {"In": {"Expressions": [{"Column": {"Expression": {"SourceRef": {"Source": "s"}}, "Property": prop}}],
                                                          "Values": [[{"Literal": {"Value": f"'{x}'"}}] for x in selected]}}}]}}
        obj.setdefault("general", [{"properties": {}}])[0]["properties"]["filter"] = sel
    v = visual(name, "slicer", pos, {"Values": [c]}, title, objects=obj)
    v["filterConfig"] = hide_blank(c["field"]["Column"]["Expression"]["SourceRef"]["Entity"], c["field"]["Column"]["Property"])
    if sync:
        v["visual"]["syncGroup"] = {"groupName": sync, "fieldChanges": True, "filterChanges": True}
    return v


# Shared slicers (conformed dimension tables), synced across every page that shows them
TT, MO, BO = "Taxi Type", "Month", "Borough"


def taxi_slicer(prefix, x=M):
    return slicer(f"{prefix}Taxi", (x, TOP, 300, 62), column(TT, "Taxi Type"), "Taxi type", horizontal=True, sync="syncTaxi")


def month_slicer(prefix, x):
    return slicer(f"{prefix}Month", (x, TOP, 200, 62), column(MO, "Month"), "Months (multi-select)", sync="syncMonth")


def borough_slicer(prefix, x):
    return slicer(f"{prefix}Borough", (x, TOP, 220, 62), column(BO, "Borough"), "Pickup boroughs (multi-select)", sync="syncBorough",
                  selected=PRESELECT_BOROUGH)


def textbox(name, pos, paragraphs, container, z=0):
    x, y, w, h = pos
    return {"$schema": f"{SCHEMA}/visualContainer/1.0.0/schema.json", "name": name,
            "position": {"x": x, "y": y, "z": z, "width": w, "height": h},
            "visual": {"visualType": "textbox", "objects": {"general": [{"properties": {"paragraphs": paragraphs}}]},
                       "visualContainerObjects": container}}


def run(text, size=10, bold=False, color_=CHAR):
    style = {"fontSize": f"{size}pt", "color": color_}
    if bold:
        style["fontWeight"] = "bold"
    return {"value": text, "textStyle": style}


def header(subtitle):
    plain = {"background": props(show=True, color=color(CHAR), transparency=0), "border": props(show=False),
             "title": props(show=False), "dropShadow": props(show=False)}
    strip = dict(plain, background=props(show=True, color=color(YELLOW), transparency=0))
    return [
        textbox("hdrBar", (0, 0, W, 66), [
            {"textRuns": [run("NYC TAXI TRIPS 2024", 20, True, WHITE)]},
            {"textRuns": [run(subtitle, 10, False, YELLOW)]}], plain, z=0),
        textbox("hdrStrip", (0, 66, W, 5), [{"textRuns": [run(" ", 1)]}], strip, z=0),
        {"$schema": f"{SCHEMA}/visualContainer/1.0.0/schema.json", "name": "hdrNav",
         "position": {"x": 520, "y": 14, "z": 1000, "width": 748, "height": 40},
         "visual": {"visualType": "pageNavigator",
                    "objects": {"fill": [{"properties": {"show": lit(True), "fillColor": color(YELLOW), "transparency": lit(0)},
                                          "selector": {"id": "default"}},
                                         {"properties": {"fillColor": color(CHAR)}, "selector": {"id": "selected"}}],
                                "text": [{"properties": {"fontColor": color(CHAR), "fontSize": lit(10), "bold": lit(True)},
                                          "selector": {"id": "default"}},
                                         {"properties": {"fontColor": color(WHITE)}, "selector": {"id": "selected"}}],
                                "outline": [{"properties": {"show": lit(False)}}],
                                "pages": props(showHiddenPages=False, showTooltipPages=False)},
                    "visualContainerObjects": {"title": props(show=False), "background": props(show=False),
                                               "border": props(show=False)}}},
    ]


def rich(text, size=9.5, color_=CHAR):
    """'**bold**' markers -> text runs, so key numbers stand out."""
    parts = text.split("**")
    return [run(p, size, i % 2 == 1, color_) for i, p in enumerate(parts) if p]


def insights(name, pos, items):
    """Insights panel: a caption plus one boxed card per insight (yellow label bar, key numbers in bold)."""
    x, y, w, h = pos
    cap_h, gap = 50, 8
    plain = {"background": props(show=False), "border": props(show=False), "title": props(show=False),
             "dropShadow": props(show=False)}
    out = [textbox(f"{name}Cap", (x, y, w, cap_h), [
        {"textRuns": [run("KEY INSIGHTS", 12, True, CHAR)]},
        {"textRuns": [run("Whole-year 2024 figures; not affected by slicers.", 8, False, "#5F6B73")]}],
        plain, z=100)]
    card_h = (h - cap_h - gap * len(items)) / len(items)
    for i, (label, text) in enumerate(items):
        cy = y + cap_h + gap + i * (card_h + gap)
        c = box()
        c["title"] = props(show=True, text=label.upper(), fontColor=color(CHAR), background=color(YELLOW), fontSize=9, bold=True)
        c["padding"] = props(top=4, bottom=4, left=8, right=8)
        out.append(textbox(f"{name}{i}", (x, round(cy), w, round(card_h)), [{"textRuns": rich(text, 9)}], c, z=100))
    return out


def cards_row(prefix, y, measures, x0=M, width=W - 2 * M, h=96, gap=10, precision=None):
    """precision: {index: decimals} for cards whose auto units would round too hard (e.g. 41M -> 41.08M)."""
    w = (width - gap * (len(measures) - 1)) / len(measures)
    precision = precision or {}
    return [card(f"{prefix}{i}", (round(x0 + i * (w + gap)), y, round(w), h), m, precision.get(i))
            for i, m in enumerate(measures)]


LEGEND_TOP = props(show=True, position="Top")
LABELS_ON = props(show=True)
MONTH_ASC = [(column(D, "Month Number"), "Ascending")]


# ---------- pages ----------
def page_overview(ins):
    v = header("Medallion lakehouse on Microsoft Fabric  |  yellow + green taxis  |  Overview")
    v += [taxi_slicer("ov"), month_slicer("ov", 322), borough_slicer("ov", 532)]
    v += cards_row("ovCard", 154, [measure(T, "Trips"), measure(T, "Revenue"), measure(T, "Average Fare"),
                                   measure(T, "Average Distance (miles)"), measure(T, "Card Tip %"), measure(T, "Airport Trip Share")],
                   precision={0: 2, 1: 2})
    v += [visual("ovCombo", "lineClusteredColumnComboChart", (M, 260, 620, 220),
                 {"Category": [column(D, "Month Short")], "Y": [measure(T, "Trips")], "Y2": [measure(T, "Average Fare")]},
                 "Trips (columns) and average fare (line) by month",
                 objects={"dataPoint": [series_fill(T, "Trips", YELLOW), series_fill(T, "Average Fare", CHAR)],
                          "legend": LEGEND_TOP, "categoryAxis": props(showAxisTitle=False)},
                 sort=[(column(D, "Month Short"), "Ascending")]),
          visual("ovBoroughBar", "clusteredBarChart", (642, 260, 300, 220),
                 {"Category": [column(Z, "Borough")], "Y": [measure(T, "Trips")]}, "Trips by pickup borough",
                 objects={"dataPoint": props(fill=color(CHAR)), "labels": props(show=True, labelPrecision=1),
                          "categoryAxis": props(showAxisTitle=False), "valueAxis": props(show=False)},
                 sort=[(measure(T, "Trips"), "Descending")]),
          visual("ovZones", "tableEx", (M, 490, 620, 218),
                 {"Values": [column(Z, "Zone"), column(Z, "Borough"), measure(T, "Trips"),
                             measure(T, "Average Fare", "Avg fare"), measure(T, "Card Tip %"),
                             measure(T, "Revenue Contribution %", "Revenue share")]}, "Pickup zones",
                 objects=table_style(), sort=[(measure(T, "Trips"), "Descending")]),
          visual("ovDaily", "lineChart", (642, 490, 300, 218),
                 {"Category": [column(D, "Date")], "Y": [measure(T, "Trips")]}, "Daily trips",
                 objects={"dataPoint": [series_fill(T, "Trips", CHAR)], **NO_AXIS_TITLES}),
          *insights("ovInsights", (952, 260, 316, 448), ins)]
    return v


def page_compare(ins):
    v = header("Yellow medallion taxis vs green Boro taxis")
    v += [month_slicer("cm", M), borough_slicer("cm", 222)]   # no taxi-type slicer: this page compares both
    v += [visual("cmTable", "tableEx", (M, 154, 930, 134),
                 {"Values": [column(T, "Taxi Type"), measure(T, "Trips"), measure(T, "Average Fare", "Avg fare"),
                             measure(T, "Average Distance (miles)", "Avg miles"), measure(T, "Average Duration (min)", "Avg minutes"),
                             measure(T, "Revenue per Mile", "Revenue / mile"), measure(T, "Card Tip %"),
                             measure(T, "Card Payment Share", "Card share"), measure(T, "Airport Trip Share", "Airport share")]},
                 "Service profile side by side",
                 objects=table_style(totals=False, size=10)),
          card("cmGreenShare", (952, 154, 316, 134), measure(T, "Green Trip Share")),
          visual("cmBoroughMix", "hundredPercentStackedBarChart", (M, 298, 460, 204),
                 {"Category": [column(T, "Taxi Type")], "Y": [measure(T, "Trips")], "Series": [column(Z, "Borough")]},
                 "Where each service picks up (share of trips)", objects={"legend": LEGEND_TOP, **NO_AXIS_TITLES}),
          visual("cmPayMix", "hundredPercentStackedBarChart", (482, 298, 460, 204),
                 {"Category": [column(T, "Taxi Type")], "Y": [measure(T, "Trips")], "Series": [column(T, "Payment Type")]},
                 "How passengers pay (share of trips)", objects={"legend": LEGEND_TOP, **NO_AXIS_TITLES}),
          visual("cmFare", "lineChart", (M, 512, 460, 196),
                 {"Category": [column(D, "Month Short")], "Y": [measure(T, "Average Fare")], "Series": [column(T, "Taxi Type")]},
                 "Average fare by month", objects={"dataPoint": taxi_colors(), "legend": LEGEND_TOP, **NO_AXIS_TITLES},
                 sort=[(column(D, "Month Short"), "Ascending")]),
          visual("cmGreenTrend", "lineChart", (482, 512, 460, 196),
                 {"Category": [column(D, "Month Short")], "Y": [measure(T, "Green Trip Share")]}, "Green taxi share of all trips by month",
                 objects={"dataPoint": [series_fill(T, "Green Trip Share", GREEN)], **NO_AXIS_TITLES},
                 sort=[(column(D, "Month Short"), "Ascending")]),
          *insights("cmInsights", (952, 298, 316, 410), ins)]
    return v


def page_zones(ins):
    v = header("Where trips happen and how demand moves through the year")
    v += [taxi_slicer("zn"), month_slicer("zn", 322), borough_slicer("zn", 532)]
    v += [visual("znFlows", "pivotTable", (M, 154, 620, 240),
                 {"Rows": [column(Z, "Borough")], "Columns": [column(T, "Dropoff Borough")], "Values": [measure(T, "Trips")]},
                 "Borough flows: pickup (rows) to dropoff (columns)",
                 objects={"values": [{"properties": {"fontSize": lit(9)}}] + heat(T, "Trips"),
                          "columnHeaders": props(fontSize=9), "rowHeaders": props(fontSize=9),
                          "subTotals": props(rowSubtotals=False)}),
          visual("znWeekday", "clusteredColumnChart", (642, 154, 300, 240),
                 {"Category": [column(D, "Day of Week")], "Y": [measure(T, "Average Daily Trips")]}, "Average trips per day by weekday",
                 objects={"dataPoint": props(fill=color(YELLOW)), "labels": props(show=True, labelDisplayUnits=1000, labelPrecision=0),
                          **NO_AXIS_TITLES},
                 sort=[(column(D, "Day of Week"), "Ascending")]),
          visual("znTop", "tableEx", (M, 404, 566, 304),
                 {"Values": [column(Z, "Zone"), column(Z, "Borough"), measure(T, "Trips"), measure(T, "Revenue"),
                             measure(T, "Revenue Contribution %", "Revenue share")]},
                 "Zone league table (sort any column)",
                 objects=table_style(), sort=[(measure(T, "Trips"), "Descending")]),
          visual("znHolidays", "tableEx", (588, 404, 354, 214),
                 {"Values": [column(D, "Holiday"), measure(T, "Holiday Trips", "Trips"),
                             measure(T, "Typical Same Weekday Trips", "Typical day"),
                             measure(T, "Holiday vs Typical Weekday %", "vs typical")]}, "US holidays vs a typical same weekday",
                 objects={**table_style(totals=False), "values": [
                     {"properties": {"fontSize": lit(9)}},
                     {"properties": {"dataBars": {"positiveColor": color("#9AA5AD"), "negativeColor": color("#D32F2F"),
                                                  "axisColor": color(CHAR), "reverseDirection": lit(False), "hideText": lit(False)}},
                      "selector": {"data": [{"dataViewWildcard": {"matchingOption": 1}}],
                                   "metadata": f"{T}.Holiday vs Typical Weekday %"}}]},
                 sort=[(measure(T, "Holiday vs Typical Weekday %"), "Ascending")]),
          textbox("znHolidayNote", (588, 626, 354, 82), [
              {"textRuns": rich("**How to read 'vs typical':** each holiday is compared with an **average 2024 day of the same weekday**, "
                                "excluding holidays. Christmas (a Wednesday): **53,799** trips vs **119,223** on a typical "
                                "Wednesday = **-55%**. Whole-year baseline, so seasonal swings are included.", 8)}],
              box(), z=100),
          *insights("znInsights", (952, 154, 316, 554), ins)]
    return v


def page_demand(ins):
    v = header("When people ride: weekday x hour demand and traffic speed")
    v += [taxi_slicer("dm"), month_slicer("dm", 322), borough_slicer("dm", 532)]
    v += [visual("dmHeat", "pivotTable", (M, 154, 930, 330),
                 {"Rows": [column(H, "Day of Week")], "Columns": [column(H, "Hour")],
                  "Values": [measure(H, "Share of Selected Trips", "Share")]},
                 "Share of selected trips by weekday and hour (hover a cell for the trip count)",
                 objects={"values": [{"properties": {"fontSize": lit(8)}}] + heat(H, "Share of Selected Trips"),
                          "columnHeaders": props(fontSize=8), "rowHeaders": props(fontSize=9),
                          # keep the bottom "Total" row (each hour's share); drop the right-hand column that didn't fit
                          "subTotals": props(rowSubtotals=True, columnSubtotals=False)}),
          visual("dmHour", "clusteredColumnChart", (M, 494, 460, 214),
                 {"Category": [column(H, "Hour")], "Y": [measure(H, "Hourly Trips")]}, "Trips by hour of day (all weekdays combined)",
                 objects={"dataPoint": props(fill=color(YELLOW)), **NO_AXIS_TITLES}, sort=[(column(H, "Hour"), "Ascending")]),
          visual("dmSpeed", "lineChart", (482, 494, 460, 214),
                 {"Category": [column(H, "Hour")], "Y": [measure(H, "Average Speed (mph)")]}, "Average speed by hour (mph)",
                 objects={"dataPoint": [series_fill(H, "Average Speed (mph)", CHAR)], **NO_AXIS_TITLES},
                 sort=[(column(H, "Hour"), "Ascending")]),
          *insights("dmInsights", (952, 154, 316, 554), ins)]
    # Hovering a heatmap cell opens the hidden 'cellTooltip' page, filtered to that weekday x hour (+ slicers)
    heat_visual = next(x for x in v if x["name"] == "dmHeat")
    heat_visual["visual"]["visualContainerObjects"]["visualTooltip"] = props(show=True, type="ReportPage", section=TOOLTIP_PAGE)
    return v


TOOLTIP_PAGE = "cellTooltip"
TIP_W, TIP_H = 300, 296


def page_cell_tooltip():
    """Hidden tooltip page: trip count, revenue and speed for the hovered weekday x hour."""
    plain = {"background": props(show=True, color=color(CHAR), transparency=0), "border": props(show=False),
             "title": props(show=False), "dropShadow": props(show=False)}
    return [
        textbox("tipHeader", (0, 0, TIP_W, 40), [{"textRuns": [run("THIS WEEKDAY x HOUR", 11, True, YELLOW)]}], plain),
        visual("tipCell", "card", (8, 48, 284, 60), {"Values": [measure(H, "Selected Weekday and Hour")]},
               objects={"labels": props(fontSize=14, color=color(CHAR)), "categoryLabels": props(show=False)}),
        visual("tipTrips", "card", (8, 116, 138, 80), {"Values": [measure(H, "Hourly Trips", "Trips")]},
               objects={"labels": props(fontSize=20, color=color(CHAR), labelDisplayUnits=1),
                        "categoryLabels": props(show=True, fontSize=10, color=color("#5F6B73"))}),
        card("tipRevenue", (154, 116, 138, 80), measure(H, "Hourly Revenue", "Revenue")),
        card("tipSpeed", (8, 204, 284, 82), measure(H, "Average Speed (mph)", "Avg speed (mph)")),
    ]


def page_quality(ins):
    v = header("Data engineering: Bronze -> Silver -> Gold row counts and quality rules")
    v += [taxi_slicer("dq"), month_slicer("dq", 322)]   # no borough: pipeline counts are per source file, not per borough
    v += cards_row("dqCard", 154, [measure(L, "Bronze Rows"), measure(L, "Silver Rows"), measure(R, "Rejected Rows"),
                                   measure(L, "Duplicate Rows"), measure(L, "Rejection Rate"), measure(L, "Gold Reconciliation")],
                   precision={0: 2, 1: 2})
    v += [visual("dqByMonth", "columnChart", (M, 260, 540, 220),
                 {"Category": [column(L, "Year Month")], "Y": [measure(R, "Rejected Rows")], "Series": [column(R, "Reject Reason")]},
                 "Rejected rows by month and rule", objects={"legend": LEGEND_TOP, **NO_AXIS_TITLES},
                 sort=[(column(L, "Year Month"), "Ascending")]),
          visual("dqByReason", "clusteredBarChart", (562, 260, 380, 220),
                 {"Category": [column(R, "Reject Reason")], "Y": [measure(R, "Rejected Rows")]}, "Rejected rows by rule",
                 objects={"dataPoint": props(fill=color(CHAR)), "labels": props(show=True, labelDisplayUnits=1),
                          "categoryAxis": props(showAxisTitle=False, maxMarginFactor=50), "valueAxis": props(show=False)},
                 sort=[(measure(R, "Rejected Rows"), "Descending")]),
          visual("dqTable", "tableEx", (M, 490, 930, 218),
                 {"Values": [column(L, "Year Month"), column(L, "Taxi Type"), measure(L, "Bronze Rows"), measure(L, "Silver Rows"),
                             measure(R, "Rejected Rows"), measure(L, "Duplicate Rows"), measure(L, "Rejection Rate"),
                             measure(L, "Rows Kept %")]}, "Layer reconciliation by month",
                 objects=table_style(size=10), sort=[(column(L, "Year Month"), "Ascending")]),
          *insights("dqInsights", (952, 260, 316, 448), ins)]
    return v


THEME = {
    "name": "NYC Taxi",
    "dataColors": [YELLOW, CHAR, GREEN, "#5C7C8A", "#E07B39", "#8E6BBF", "#3F88C5", "#B0BEC5"],
    "foreground": CHAR, "background": WHITE, "tableAccent": YELLOW,
    "visualStyles": {"page": {"*": {"background": [{"color": {"solid": {"color": BG}}, "transparency": 0}],
                                    "outspace": [{"color": {"solid": {"color": BG}}}]}}},
}


def build(ws_name, model_id, ins):
    pages = {
        "overview": ("Overview", page_overview(ins["overview"])),
        "compare": ("Yellow vs Green", page_compare(ins["compare"])),
        "zones": ("Zones & Seasonality", page_zones(ins["zones"])),
        "demand": ("Demand Patterns", page_demand(ins["demand"])),
        "quality": ("Data Quality", page_quality(ins["quality"])),
    }
    files = {
        "definition.pbir": {"version": "4.0", "datasetReference": {"byConnection": {
            "connectionString": f"Data Source=powerbi://api.powerbi.com/v1.0/myorg/{ws_name};Initial Catalog={semantic_model.MODEL_NAME};Integrated Security=ClaimsToken",
            "pbiServiceModelId": None, "pbiModelVirtualServerName": "sobe_wowvirtualserver",
            "pbiModelDatabaseName": model_id, "name": "EntityDataSource", "connectionType": "pbiServiceXmlaStyleLive"}}},
        "definition/version.json": {"$schema": f"{SCHEMA}/versionMetadata/1.0.0/schema.json", "version": "2.0.0"},
        "definition/report.json": {
            "$schema": f"{SCHEMA}/report/1.0.0/schema.json",
            "themeCollection": {"baseTheme": {"name": "CY24SU10", "reportVersionAtImport": "5.61", "type": "SharedResources"},
                                "customTheme": {"name": "NYCTaxiTheme.json", "reportVersionAtImport": "5.61", "type": "RegisteredResources"}},
            "resourcePackages": [{"name": "RegisteredResources", "type": "RegisteredResources",
                                  "items": [{"name": "NYCTaxiTheme.json", "path": "NYCTaxiTheme.json", "type": "CustomTheme"}]}],
            "layoutOptimization": "None"},
        "definition/pages/pages.json": {"$schema": f"{SCHEMA}/pagesMetadata/1.0.0/schema.json",
                                        "pageOrder": list(pages) + [TOOLTIP_PAGE], "activePageName": "overview"},
        f"definition/pages/{TOOLTIP_PAGE}/page.json": {
            "$schema": f"{SCHEMA}/page/1.0.0/schema.json", "name": TOOLTIP_PAGE, "displayName": "Cell tooltip",
            "displayOption": "ActualSize", "height": TIP_H, "width": TIP_W, "visibility": "HiddenInViewMode",
            "pageBinding": {"name": "cellTooltipBinding", "type": "Tooltip", "parameters": []},
            "objects": {"background": props(color=color(WHITE), transparency=0)}},
        "StaticResources/RegisteredResources/NYCTaxiTheme.json": THEME,
    }
    for pid, (title, visuals) in pages.items():
        files[f"definition/pages/{pid}/page.json"] = {
            "$schema": f"{SCHEMA}/page/1.0.0/schema.json", "name": pid, "displayName": title,
            "displayOption": "FitToPage", "height": PAGE_H, "width": W,
            "objects": {"background": props(color=color(BG), transparency=0)}}
        for v in visuals:
            files[f"definition/pages/{pid}/visuals/{v['name']}/visual.json"] = v
    for v in page_cell_tooltip():
        files[f"definition/pages/{TOOLTIP_PAGE}/visuals/{v['name']}/visual.json"] = v
    return files


def deploy():
    ins = json.load(open(fabric.os.path.join(fabric.HERE, "insights.json"), encoding="utf-8"))
    ws_id, _ = fabric.ids()
    ws_name = next(w["displayName"] for w in fabric.call("GET", "/workspaces")["value"] if w["id"] == ws_id)
    files = build(ws_name, semantic_model.model_id(ws_id), ins)
    definition = {"format": "PBIR", "parts": [
        {"path": p, "payload": base64.b64encode(json.dumps(c, indent=2).encode()).decode(), "payloadType": "InlineBase64"}
        for p, c in files.items()]}
    existing = [r for r in fabric.call("GET", f"/workspaces/{ws_id}/reports")["value"] if r["displayName"] == REPORT_NAME]
    if existing:
        fabric.call("POST", f"/workspaces/{ws_id}/reports/{existing[0]['id']}/updateDefinition", {"definition": definition}, want_result=False)
        print(f"updated report {existing[0]['id']} ({len(files)} parts)")
    else:
        r = fabric.call("POST", f"/workspaces/{ws_id}/reports", {"displayName": REPORT_NAME, "definition": definition})
        print(f"created report {r.get('id')}")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "deploy":
        deploy()
    else:
        print(__doc__)
