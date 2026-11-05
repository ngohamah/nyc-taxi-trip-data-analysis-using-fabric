"""Draw the README diagrams as PNGs: python tools/make_diagrams.py

  docs/architecture.png        medallion flow with explanations alongside each stage
  docs/model_relationships.png semantic model tables and relationships, read from powerbi/NYC Taxi.SemanticModel
"""
import os, re
from PIL import Image, ImageDraw, ImageFont

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
DOCS = os.path.join(ROOT, "docs")
MODEL = os.path.join(ROOT, "powerbi", "NYC Taxi.SemanticModel", "definition")
S = 2  # draw at 2x for crisp text on GitHub

CHAR, YELLOW, GREEN, BG, WHITE, LINE = "#263238", "#F7B500", "#2E9E44", "#F6F7F8", "#FFFFFF", "#C9D0D6"
MUTED, BRONZE, SILVER, GOLD, BLUE = "#5F6B73", "#B87333", "#8E9AA3", "#D4A017", "#3F88C5"
FONTS = r"C:\Windows\Fonts"


def font(size, weight="regular"):
    name = {"regular": "segoeui.ttf", "bold": "segoeuib.ttf", "semi": "seguisb.ttf",
            "italic": "segoeuii.ttf", "mono": "consola.ttf"}[weight]
    return ImageFont.truetype(os.path.join(FONTS, name), size * S)


def wrap(text, f, width):
    """Greedy word wrap; '**' toggles are not supported, keep explanations plain."""
    lines, cur = [], ""
    for word in text.split():
        trial = f"{cur} {word}".strip()
        if f.getlength(trial) <= width * S or not cur:
            cur = trial
        else:
            lines.append(cur); cur = word
    return lines + ([cur] if cur else [])


class Canvas:
    def __init__(self, w, h):
        self.img = Image.new("RGB", (w * S, h * S), BG)
        self.d = ImageDraw.Draw(self.img)

    def rect(self, x, y, w, h, fill=WHITE, outline=LINE, r=10, width=1, dash=False):
        box = [x * S, y * S, (x + w) * S, (y + h) * S]
        if dash:
            self.dashed_rect(x, y, w, h, outline, r)
        else:
            self.d.rounded_rectangle(box, r * S, fill=fill, outline=outline, width=width * S)

    def dashed_rect(self, x, y, w, h, color, r):
        for (x1, y1, x2, y2) in ((x + r, y, x + w - r, y), (x + r, y + h, x + w - r, y + h),
                                 (x, y + r, x, y + h - r), (x + w, y + r, x + w, y + h - r)):
            self.dashed_line(x1, y1, x2, y2, color)
        for cx, cy, a in ((x + r, y + r, 180), (x + w - r, y + r, 270), (x + w - r, y + h - r, 0), (x + r, y + h - r, 90)):
            self.d.arc([(cx - r) * S, (cy - r) * S, (cx + r) * S, (cy + r) * S], a, a + 90, fill=color, width=2 * S)

    def dashed_line(self, x1, y1, x2, y2, color, dash=8, gap=6, width=2):
        length = ((x2 - x1) ** 2 + (y2 - y1) ** 2) ** 0.5
        if not length:
            return
        dx, dy, t = (x2 - x1) / length, (y2 - y1) / length, 0
        while t < length:
            e = min(t + dash, length)
            self.d.line([(x1 + dx * t) * S, (y1 + dy * t) * S, (x1 + dx * e) * S, (y1 + dy * e) * S], fill=color, width=width * S)
            t = e + gap

    def text(self, x, y, s, f, fill=CHAR, anchor="la"):
        self.d.text((x * S, y * S), s, font=f, fill=fill, anchor=anchor)

    def line(self, pts, fill=CHAR, width=2):
        self.d.line([(px * S, py * S) for px, py in pts], fill=fill, width=width * S, joint="curve")

    def arrow(self, x1, y1, x2, y2, fill=CHAR, width=2, head=9):
        self.line([(x1, y1), (x2, y2)], fill, width)
        length = ((x2 - x1) ** 2 + (y2 - y1) ** 2) ** 0.5
        ux, uy = (x2 - x1) / length, (y2 - y1) / length
        bx, by = x2 - ux * head, y2 - uy * head
        self.d.polygon([(x2 * S, y2 * S), ((bx - uy * head * 0.6) * S, (by + ux * head * 0.6) * S),
                        ((bx + uy * head * 0.6) * S, (by - ux * head * 0.6) * S)], fill=fill)

    def save(self, name):
        path = os.path.join(DOCS, name)
        self.img.save(path, optimize=True)
        print("wrote", os.path.relpath(path, ROOT))


# --------------------------------------------------------------------------- architecture

STAGES = [
    {"key": "source", "accent": BLUE, "title": "NYC TLC open data",
     "items": ["24 monthly Parquet files: yellow + green, 2024", "taxi_zone_lookup.csv: zone names and boroughs"],
     "label": "Source",
     "why": "Public trip records from the NYC Taxi & Limousine Commission. Fabric Spark can't read web URLs, "
            "so the Bronze notebook first downloads each file into the lakehouse folder Files/landing/nyc_taxi/."},
    {"key": "bronze", "accent": BRONZE, "title": "Bronze: raw, as received",
     "items": ["bronze.yellow_trips_raw", "bronze.green_trips_raw", "bronze.taxi_zones_raw", "bronze.ingestion_log"],
     "label": "01_bronze_ingest",
     "why": "Each file is appended unchanged, plus audit columns (source file, batch id, load time). "
            "ingestion_log is the watermark, so files already loaded are skipped. Reloading a file replaces its rows, "
            "so a rerun never duplicates data. 41.83M rows."},
    {"key": "silver", "accent": SILVER, "title": "Silver: cleaned and validated",
     "items": ["silver.taxi_trips  (partitioned by month)", "silver.taxi_trips_rejected", "silver.taxi_zones"],
     "label": "02_silver_transform",
     "why": "Yellow and green are conformed to one schema and exact duplicates are removed. Each row is checked "
            "against quality rules: timestamps, pickup in the file's month, duration, distance, negative amounts and "
            "location IDs. Failing rows are kept in taxi_trips_rejected with the reason, so nothing is dropped "
            "silently. 41.08M valid rows (98.2%)."},
    {"key": "gold", "accent": GOLD, "title": "Gold: star schema for reporting",
     "items": ["facts: fact_trips_daily_zone, hourly_demand, monthly_kpis",
               "dims: dim_date, dim_zone, dim_month, dim_borough, dim_taxi_type",
               "quality: dq_layer_counts, dq_rejections"],
     "label": "03_gold_aggregate",
     "why": "Trips are pre-aggregated by day, zone and taxi type, and by weekday and hour, so the report reads "
            "thousands of rows instead of 41M. Written with V-Order and optimize-write, then ZORDERed on date and "
            "zone. The run fails if Gold trip totals don't match Silver."},
]
CONSUMERS = [
    ("Direct Lake semantic model", "NYC Taxi: 9 tables, 11 relationships", YELLOW),
    ("SQL analytics endpoint", "dbo.vw_*: 6 analyst views", BLUE),
]
REPORT = ("Power BI report", "NYC Taxi Overview: 5 pages", YELLOW)
CONSUMER_WHY = ("Direct Lake reads the Gold Delta files straight from OneLake: no import copy and no scheduled data "
                "refresh. The report uses it, with Month, Borough and Taxi Type slicers synced across pages. "
                "The SQL views give analysts the same Gold numbers in T-SQL or Excel.")


def architecture():
    W, LEFT, BOX_X, BOX_W, EXP_X, EXP_W = 1500, 30, 120, 600, 790, 680
    title_f, item_f, why_f, label_f, small_f = font(19, "bold"), font(14, "mono"), font(15), font(12, "italic"), font(13, "semi")
    # measure rows first
    rows, y = [], 70
    for st in STAGES:
        why = wrap(st["why"], why_f, EXP_W - 30)
        h = max(52 + 22 * len(st["items"]), 30 + 23 * len(why)) + 6
        rows.append((st, why, y, h)); y += h + (96 if st["key"] == "source" else 58)
    cons_y = y
    cons_why = wrap(CONSUMER_WHY, why_f, EXP_W - 30)
    total_h = cons_y + 76 + 50 + 76 + 70
    c = Canvas(W, total_h)
    c.text(LEFT, 22, "Medallion lakehouse on Microsoft Fabric", font(22, "bold"))
    c.text(LEFT + 470, 28, "workspace ngoh-fabric-dev  ·  lakehouse nyc_taxi_lakehouse (schema-enabled)", font(13), MUTED)

    # lakehouse boundary around bronze..gold, orchestration bar on the left
    lh_top, lh_bot = rows[1][2] - 26, rows[3][2] + rows[3][3] + 16
    c.rect(BOX_X - 16, lh_top, BOX_W + 32, lh_bot - lh_top, outline=MUTED, dash=True, r=14)
    c.text(BOX_X - 4, lh_top - 18, "OneLake: one lakehouse, three schemas", font(12, "semi"), MUTED)
    c.rect(LEFT, lh_top, 44, lh_bot - lh_top, fill=CHAR, outline=CHAR, r=8)
    bar = Image.new("RGBA", ((lh_bot - lh_top) * S, 44 * S), (0, 0, 0, 0))
    ImageDraw.Draw(bar).text(((lh_bot - lh_top) * S / 2, 22 * S), "Data pipeline runs the notebooks in order",
                             font=font(13, "semi"), fill=WHITE, anchor="mm")
    bar = bar.rotate(90, expand=True)
    c.img.paste(bar, (LEFT * S, lh_top * S), bar)

    for i, (st, why, y, h) in enumerate(rows):
        c.rect(BOX_X, y, BOX_W, h, r=10)
        c.d.rounded_rectangle([BOX_X * S, y * S, (BOX_X + 10) * S, (y + h) * S], 10 * S, fill=st["accent"])
        c.d.rectangle([(BOX_X + 5) * S, y * S, (BOX_X + 10) * S, (y + h) * S], fill=st["accent"])
        c.text(BOX_X + 26, y + 14, st["title"], title_f)
        for j, it in enumerate(st["items"]):
            c.text(BOX_X + 26, y + 48 + 22 * j, it, item_f, MUTED)
        # explanation panel joined by a dotted leader
        c.dashed_line(BOX_X + BOX_W + 4, y + 26, EXP_X - 4, y + 26, LINE, 4, 4, 2)
        c.rect(EXP_X, y, EXP_W, h, fill=WHITE, outline=LINE, r=10)
        c.d.rectangle([EXP_X * S, (y + 8) * S, (EXP_X + 4) * S, (y + h - 8) * S], fill=st["accent"])
        for j, ln in enumerate(why):
            c.text(EXP_X + 20, y + 14 + 23 * j, ln, why_f)
        if i < len(rows) - 1:
            ny = rows[i + 1][2]
            c.arrow(BOX_X + BOX_W / 2, y + h + 2, BOX_X + BOX_W / 2, ny - 3)
            ly = y + h + 14 if i == 0 else (y + h + ny) / 2 - 9
            c.text(BOX_X + BOX_W / 2 + 14, ly, f"notebook {rows[i + 1][0]['label']}", label_f, MUTED)

    # consumers: model + SQL endpoint side by side, report under the model
    gy, gh = rows[-1][2], rows[-1][3]
    half = (BOX_W - 20) / 2
    for k, (t, sub, acc) in enumerate(CONSUMERS):
        x = BOX_X + k * (half + 20)
        c.arrow(BOX_X + BOX_W / 2, gy + gh + 2, x + half / 2, cons_y - 3)
        c.rect(x, cons_y, half, 76, r=10, outline=acc, width=2)
        c.text(x + half / 2, cons_y + 24, t, font(16, "bold"), anchor="mm")
        c.text(x + half / 2, cons_y + 52, sub, small_f, MUTED, anchor="mm")
    ry = cons_y + 76 + 50
    c.arrow(BOX_X + half / 2, cons_y + 78, BOX_X + half / 2, ry - 3)
    c.text(BOX_X + half / 2 + 12, cons_y + 92, "Direct Lake", label_f, MUTED)
    c.rect(BOX_X, ry, half, 76, r=10, fill=CHAR, outline=CHAR)
    c.text(BOX_X + half / 2, ry + 24, REPORT[0], font(16, "bold"), WHITE, anchor="mm")
    c.text(BOX_X + half / 2, ry + 52, REPORT[1], small_f, YELLOW, anchor="mm")
    ex_h = ry + 76 - cons_y
    c.dashed_line(BOX_X + BOX_W + 4, cons_y + 26, EXP_X - 4, cons_y + 26, LINE, 4, 4, 2)
    c.rect(EXP_X, cons_y, EXP_W, ex_h, fill=WHITE, outline=LINE, r=10)
    c.d.rectangle([EXP_X * S, (cons_y + 8) * S, (EXP_X + 4) * S, (cons_y + ex_h - 8) * S], fill=YELLOW)
    c.text(EXP_X + 20, cons_y + 14, "Serving layer", font(15, "bold"))
    for j, ln in enumerate(cons_why):
        c.text(EXP_X + 20, cons_y + 40 + 23 * j, ln, why_f)
    c.save("architecture.png")


# --------------------------------------------------------------------------- model relationships

def q(name):
    return name.strip().strip("'")


def parse_model():
    tables = {}
    for f in sorted(os.listdir(os.path.join(MODEL, "tables"))):
        cur, kind, name = None, None, None
        t = {"columns": [], "hidden": set(), "measures": 0, "entity": ""}
        for raw in open(os.path.join(MODEL, "tables", f), encoding="utf-8"):
            line = raw.rstrip("\n")
            if line.startswith("table "):
                name = q(line[6:])
            elif re.match(r"^\tcolumn ", line):
                kind, cur = "col", q(re.sub(r"\s*=.*$", "", line[8:]))
                t["columns"].append(cur)
            elif re.match(r"^\tmeasure ", line):
                kind = "measure"; t["measures"] += 1
            elif re.match(r"^\t\S", line):
                kind = None
            elif kind == "col" and line.strip() == "isHidden":
                t["hidden"].add(cur)
            m = re.search(r"entityName:\s*(\S+)", line)
            if m:
                t["entity"] = m.group(1)
        tables[name] = t
    rels, cur = [], {}
    for raw in open(os.path.join(MODEL, "relationships.tmdl"), encoding="utf-8"):
        m = re.match(r"\s*(fromColumn|toColumn):\s*(.+)$", raw)
        if m:
            tbl, col = re.match(r"('[^']+'|[^.]+)\.(.+)$", m.group(2).strip()).groups()
            cur[m.group(1)] = (q(tbl), q(col))
            if "fromColumn" in cur and "toColumn" in cur:
                rels.append((cur["fromColumn"], cur["toColumn"])); cur = {}
    return tables, rels


# grid position (column, row) for each table; dims in the middle row, facts above and below
LAYOUT = {"Taxi Trips": (2, 0), "Pipeline Layers": (4, 0), "Rejections": (5.35, 0),
          "Borough": (0, 1), "Pickup Zone": (1, 1), "Taxi Type": (2, 1), "Date": (3, 1), "Month": (4, 1),
          "Hourly Demand": (2, 2)}
DIMS = {"Borough", "Pickup Zone", "Taxi Type", "Date", "Month"}


def model_diagram():
    tables, rels = parse_model()
    missing = set(tables) - set(LAYOUT)
    assert not missing, f"add these tables to LAYOUT: {missing}"
    keys = {}
    for (ft, fc), (tt, tc) in rels:
        keys.setdefault(ft, set()).add(fc); keys.setdefault(tt, set()).add(tc)
    CW, GX, PAD, ROW_H, MAXF = 232, 268, 30, 24, 6
    cards = {}
    for name, t in tables.items():
        shown = [c for c in t["columns"] if c in keys.get(name, ())]
        shown += [c for c in t["columns"] if c not in shown and c not in t["hidden"]]
        extra = len(t["columns"]) - min(len(shown), MAXF)
        lines = shown[:MAXF]
        h = 44 + ROW_H * len(lines) + (ROW_H if extra > 0 else 0) + (ROW_H if t["measures"] else 0) + 34
        cards[name] = {"lines": lines, "extra": extra, "h": h, "t": t}
    row_h = {r: max(cards[n]["h"] for n, (_, rr) in LAYOUT.items() if rr == r) for r in range(3)}
    row_y = {0: 90}
    row_y[1] = row_y[0] + row_h[0] + 110
    row_y[2] = row_y[1] + row_h[1] + 110
    W = PAD * 2 + int(GX * max(c for c, _ in LAYOUT.values())) + CW
    H = row_y[2] + row_h[2] + 120
    for name, (col, row) in LAYOUT.items():
        cd = cards[name]
        cd["x"], cd["w"] = PAD + col * GX, CW
        cd["h"] = row_h[1] if row == 1 else cd["h"]  # equal heights in the dimension row keep lines clear of cards
        cd["y"] = row_y[row] if row != 0 else row_y[0] + row_h[0] - cd["h"]  # top row hangs from a common baseline
        cd["row"] = row
    c = Canvas(W, H)
    c.text(PAD, 24, "Semantic model: NYC Taxi (Direct Lake on gold)", font(22, "bold"))
    c.text(PAD, 56, f"{len(tables)} tables · {len(rels)} one-to-many relationships · filters flow from the 1 side to the * side",
           font(13), MUTED)

    def anchor(a, b):
        """Point on card a's edge facing card b."""
        if a["row"] == b["row"]:
            x = a["x"] + a["w"] if b["x"] > a["x"] else a["x"]
            return x, max(a["y"], b["y"]) + 18
        y = a["y"] + a["h"] if b["y"] > a["y"] else a["y"]
        cx = b["x"] + b["w"] / 2
        return min(max(cx, a["x"] + 30), a["x"] + a["w"] - 30), y

    mf = font(14, "bold")
    for (ft, fc), (tt, tc) in rels:
        a, b = cards[ft], cards[tt]
        p1, p2 = anchor(a, b), anchor(b, a)
        c.line([p1, p2], fill="#7D8A93", width=2)
        mx, my = (p1[0] + p2[0]) / 2, (p1[1] + p2[1]) / 2
        L = ((p2[0] - p1[0]) ** 2 + (p2[1] - p1[1]) ** 2) ** 0.5
        ux, uy = (p1[0] - p2[0]) / L, (p1[1] - p2[1]) / L  # direction 1 -> * (filter flow)
        c.arrow(mx - ux * 7, my - uy * 7, mx + ux * 7, my + uy * 7, fill="#7D8A93", width=2, head=8)

        def mark(p, toward, s):
            dx, dy = toward[0] - p[0], toward[1] - p[1]
            d = (dx * dx + dy * dy) ** 0.5
            px, py = p[0] + dx / d * 16, p[1] + dy / d * 16
            c.d.ellipse([(px - 9) * S, (py - 9) * S, (px + 9) * S, (py + 9) * S], fill=BG)
            c.text(px, py, s, mf, CHAR, anchor="mm")
        mark(p1, p2, "*"); mark(p2, p1, "1")

    for name, cd in cards.items():
        x, y, w, h, t = cd["x"], cd["y"], cd["w"], cd["h"], cd["t"]
        dim = name in DIMS
        c.rect(x, y, w, h, fill=WHITE, outline=LINE, r=8)
        c.d.rounded_rectangle([x * S, y * S, (x + w) * S, (y + 36) * S], 8 * S, fill=YELLOW if dim else CHAR)
        c.d.rectangle([x * S, (y + 28) * S, (x + w) * S, (y + 36) * S], fill=YELLOW if dim else CHAR)
        c.text(x + 12, y + 18, name, font(15, "bold"), CHAR if dim else WHITE, anchor="lm")
        yy = y + 46
        for col in cd["lines"]:
            is_key = col in keys.get(name, ())
            hidden = col in t["hidden"]
            if is_key:
                c.d.ellipse([(x + 12) * S, (yy + 7) * S, (x + 19) * S, (yy + 14) * S], fill=GOLD)
            c.text(x + 26, yy, col + ("  (hidden)" if hidden else ""), font(13, "semi" if is_key else "regular"),
                   MUTED if hidden and not is_key else CHAR)
            yy += ROW_H
        if cd["extra"] > 0:
            c.text(x + 26, yy, f"+ {cd['extra']} more columns", font(12, "italic"), MUTED); yy += ROW_H
        if t["measures"]:
            c.text(x + 12, yy, f"Σ  {t['measures']} measure" + ("s" if t["measures"] != 1 else ""), font(13, "semi"), GREEN); yy += ROW_H
        c.d.line([(x + 10) * S, (y + h - 28) * S, (x + w - 10) * S, (y + h - 28) * S], fill=LINE, width=S)
        c.text(x + 12, y + h - 22, f"gold.{t['entity']}", font(12, "mono"), MUTED)

    # legend
    ly = H - 50
    c.d.rectangle([PAD * S, ly * S, (PAD + 18) * S, (ly + 18) * S], fill=YELLOW)
    c.text(PAD + 26, ly - 1, "Dimension: lookup / shared slicer table", font(13))
    c.d.rectangle([(PAD + 330) * S, ly * S, (PAD + 348) * S, (ly + 18) * S], fill=CHAR)
    c.text(PAD + 356, ly - 1, "Fact / data table (holds the measures)", font(13))
    c.d.ellipse([(PAD + 650) * S, (ly + 5) * S, (PAD + 658) * S, (ly + 13) * S], fill=GOLD)
    c.text(PAD + 666, ly - 1, "Relationship key column", font(13))
    c.text(PAD + 880, ly - 1, "Arrow on each line = filter direction (single)", font(13))
    c.save("model_relationships.png")


if __name__ == "__main__":
    architecture()
    model_diagram()
