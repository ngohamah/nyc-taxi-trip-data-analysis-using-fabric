"""Generate + deploy the 'NYC Taxi' Direct Lake semantic model on the Gold schema.

Usage:
  python src/semantic_model.py write      # write TMDL files to powerbi/NYC Taxi.SemanticModel/
  python src/semantic_model.py deploy     # write + create (or update) the model in Fabric
  python src/semantic_model.py refresh    # reframe the Direct Lake model
  python src/semantic_model.py query "<DAX>"
"""
import base64, json, os, sys, time
import requests
import fabric

MODEL_NAME = "NYC Taxi"
EXPR = "DirectLake - NYC Taxi"
OUT = os.path.join(fabric.ROOT, "powerbi", f"{MODEL_NAME}.SemanticModel")
PBI = "https://api.powerbi.com/v1.0/myorg"


def T(n):
    return "\t" * n


def col(name, src, dtype, hidden=False, summarize=None, fmt=None, sort_by=None, desc=None, extra=()):
    lines = []
    if desc:
        lines.append(f"{T(1)}/// {desc}")
    lines.append(f"{T(1)}column {q(name)}")
    lines.append(f"{T(2)}dataType: {dtype}")
    if hidden:
        lines.append(f"{T(2)}isHidden")
    if fmt:
        lines.append(f"{T(2)}formatString: {fmt}")
    if summarize or dtype in ("string", "boolean", "dateTime"):
        lines.append(f"{T(2)}summarizeBy: {summarize or 'none'}")
    lines.append(f"{T(2)}sourceColumn: {src}")
    if sort_by:
        lines.append(f"{T(2)}sortByColumn: {q(sort_by)}")
    lines += [f"{T(2)}{e}" for e in extra]
    return "\n".join(lines) + "\n"


def measure(name, dax, fmt, desc, folder=None):
    lines = [f"{T(1)}/// {desc}"]
    if "\n" in dax:
        body = "\n".join(f"{T(3)}{l}" for l in dax.strip().split("\n"))
        lines.append(f"{T(1)}measure {q(name)} = ```\n{body}\n{T(3)}```")
    else:
        lines.append(f"{T(1)}measure {q(name)} = {dax}")
    lines.append(f"{T(2)}formatString: {fmt}")
    if folder:
        lines.append(f"{T(2)}displayFolder: {folder}")
    return "\n".join(lines) + "\n"


def q(name):
    return f"'{name}'" if any(ch in name for ch in " .=:'()%") else name


def partition(table, entity):
    return (f"{T(1)}partition {q(table)} = entity\n{T(2)}mode: directLake\n{T(2)}source\n"
            f"{T(3)}entityName: {entity}\n{T(3)}schemaName: gold\n{T(3)}expressionSource: '{EXPR}'\n")


def table(name, desc, body, entity, extra_header=()):
    head = f"/// {desc}\ntable {q(name)}\n" + "".join(f"{T(1)}{h}\n" for h in extra_header) + "\n"
    return head + "\n".join(body) + "\n" + partition(name, entity)


def build(ws_id, lh_id):
    files = {}
    files["definition.pbism"] = json.dumps({
        "$schema": "https://developer.microsoft.com/json-schemas/fabric/item/semanticModel/definitionProperties/1.0.0/schema.json",
        "version": "4.2", "settings": {"qnaEnabled": True}}, indent=2)
    files["definition/database.tmdl"] = f"database\n{T(1)}compatibilityLevel: 1702\n{T(1)}compatibilityMode: powerBI\n{T(1)}language: 1033\n"
    files["definition/model.tmdl"] = (
        f"model Model\n{T(1)}culture: en-US\n{T(1)}defaultPowerBIDataSourceVersion: powerBI_V3\n"
        f"{T(1)}discourageImplicitMeasures\n{T(1)}sourceQueryCulture: en-US\n\n"
        f"ref table 'Taxi Trips'\nref table Date\nref table 'Pickup Zone'\nref table 'Hourly Demand'\n"
        f"ref table 'Pipeline Layers'\nref table Rejections\nref table Month\nref table Borough\nref table 'Taxi Type'\n")
    files["definition/expressions.tmdl"] = (
        f"/// OneLake connection to the nyc_taxi_lakehouse (Direct Lake on OneLake)\n"
        f"expression '{EXPR}' =\n{T(2)}let\n"
        f"{T(3)}Source = AzureStorage.DataLake(\"https://onelake.dfs.fabric.microsoft.com/{ws_id}/{lh_id}\", [HierarchicalNavigation=true])\n"
        f"{T(2)}in\n{T(3)}Source\n")
    files["definition/relationships.tmdl"] = (
        f"relationship 'Taxi Trips to Date'\n{T(1)}fromColumn: 'Taxi Trips'.'Pickup Date'\n{T(1)}toColumn: Date.Date\n\n"
        f"relationship 'Taxi Trips to Pickup Zone'\n{T(1)}fromColumn: 'Taxi Trips'.'Pickup Location ID'\n{T(1)}toColumn: 'Pickup Zone'.'Location ID'\n\n"
        f"relationship 'Rejections to Pipeline Layers'\n{T(1)}fromColumn: Rejections.'Month Type Key'\n{T(1)}toColumn: 'Pipeline Layers'.'Month Type Key'\n"
        + "".join(f"\nrelationship '{a} to {b}'\n{T(1)}fromColumn: {fa}\n{T(1)}toColumn: {tb}\n" for a, b, fa, tb in [
            ("Date", "Month", "Date.'Year Month'", "Month.'Year Month'"),
            ("Hourly Demand", "Month", "'Hourly Demand'.'Year Month'", "Month.'Year Month'"),
            ("Pipeline Layers", "Month", "'Pipeline Layers'.'Year Month'", "Month.'Year Month'"),
            ("Pickup Zone", "Borough", "'Pickup Zone'.Borough", "Borough.Borough"),
            ("Hourly Demand", "Borough", "'Hourly Demand'.'Pickup Borough'", "Borough.Borough"),
            ("Taxi Trips", "Taxi Type", "'Taxi Trips'.'Taxi Type'", "'Taxi Type'.'Taxi Type'"),
            ("Hourly Demand", "Taxi Type", "'Hourly Demand'.'Taxi Type'", "'Taxi Type'.'Taxi Type'"),
            ("Pipeline Layers", "Taxi Type", "'Pipeline Layers'.'Taxi Type'", "'Taxi Type'.'Taxi Type'"),
        ]))

    trips = [
        measure("Trips", "SUM('Taxi Trips'[Trip Count])", "#,##0", "Number of valid taxi trips (yellow + green) after Silver quality rules.", "Volume"),
        measure("Passengers", "SUM('Taxi Trips'[Passenger Count])", "#,##0", "Passengers carried; trips with a missing passenger count contribute 0.", "Volume"),
        measure("Trips Previous Month", "CALCULATE([Trips], DATEADD('Date'[Date], -1, MONTH), REMOVEFILTERS(Month))", "#,##0",
                "Trips in the same period shifted back one month, for month-over-month comparison.", "Volume"),
        measure("Trips MoM Change %", "DIVIDE([Trips] - [Trips Previous Month], [Trips Previous Month])", "0.0%",
                "Month-over-month (MoM) change in trips versus the previous month.", "Volume"),
        measure("Revenue", "SUM('Taxi Trips'[Total Amount])", "$#,##0", "Total amount charged to passengers: fare, surcharges, taxes, tolls and tips. Cash tips are not recorded by TLC.", "Revenue"),
        measure("Fare Revenue", "SUM('Taxi Trips'[Fare Amount])", "$#,##0", "Metered time-and-distance fare only, excluding surcharges, tolls and tips.", "Revenue"),
        measure("Average Fare", "DIVIDE([Fare Revenue], [Trips])", "$#,##0.00", "Average metered fare per trip.", "Revenue"),
        measure("Average Revenue per Trip", "DIVIDE([Revenue], [Trips])", "$#,##0.00", "Average total amount charged per trip.", "Revenue"),
        measure("Revenue per Mile", "DIVIDE([Revenue], SUM('Taxi Trips'[Total Distance Miles]))", "$#,##0.00", "Total amount charged divided by trip miles.", "Revenue"),
        measure("Average Distance (miles)", "DIVIDE(SUM('Taxi Trips'[Total Distance Miles]), [Trips])", "#,##0.00", "Average trip distance in miles as reported by the taximeter.", "Trip profile"),
        measure("Average Duration (min)", "DIVIDE(SUM('Taxi Trips'[Total Duration Min]), [Trips])", "#,##0.0", "Average minutes from pickup to dropoff.", "Trip profile"),
        measure("Card Payment Share", "DIVIDE(SUM('Taxi Trips'[Card Trip Count]), [Trips])", "0.0%", "Share of trips paid by credit card.", "Payments"),
        measure("Card Tip %", "DIVIDE(SUM('Taxi Trips'[Card Tip Amount]), SUM('Taxi Trips'[Card Fare Amount]))", "0.0%",
                "Tips as a percentage of fare for credit-card trips only, because TLC does not record cash tips.", "Payments"),
        measure("Airport Trip Share", "DIVIDE(SUM('Taxi Trips'[Airport Trip Count]), [Trips])", "0.0%",
                "Share of trips with a JFK/Newark rate code or a pickup or dropoff at JFK, LaGuardia or Newark.", "Volume"),
        measure("Average Daily Trips", "AVERAGEX(VALUES('Date'[Date]), [Trips])", "#,##0",
                "Average trips per calendar day in the current selection (days without trips are ignored).", "Seasonality"),
        measure("Revenue Contribution %", "DIVIDE([Revenue], CALCULATE([Revenue], ALLSELECTED('Pickup Zone')))", "0.00%",
                "Share of the selection's revenue earned by this zone or borough (100% across all rows of a zone table).", "Revenue"),
        measure("Green Trip Share", "DIVIDE(CALCULATE([Trips], 'Taxi Trips'[Taxi Type] = \"green\", REMOVEFILTERS('Taxi Type')), CALCULATE([Trips], REMOVEFILTERS('Taxi Trips'[Taxi Type]), REMOVEFILTERS('Taxi Type')))",
                "0.0%", "Green (Boro) taxi trips as a share of all taxi trips, ignoring the taxi type filter.", "Comparison"),
        measure("Holiday Trips", "IF(SELECTEDVALUE('Date'[Is Holiday]) = TRUE(), [Trips])", "#,##0",
                "Trips on a US federal holiday; blank on other days, so tables show holidays only.", "Seasonality"),
        measure("Typical Same Weekday Trips", """VAR DayNumber = SELECTEDVALUE('Date'[Day of Week Number])
RETURN
    IF(
        NOT ISBLANK([Holiday Trips]),
        CALCULATE(
            [Average Daily Trips],
            REMOVEFILTERS('Date'),
            REMOVEFILTERS(Month),
            'Date'[Day of Week Number] = DayNumber,
            'Date'[Is Holiday] = FALSE()
        )
    )""", "#,##0",
                "The 'typical day' baseline: average trips on all 2024 non-holiday days with the same weekday as the holiday "
                "(e.g. Christmas 2024 was a Wednesday, so it is compared with the average 2024 Wednesday). Whole year, so seasonality is included.",
                "Seasonality"),
        measure("Holiday vs Typical Weekday %", "IF(NOT ISBLANK([Holiday Trips]), DIVIDE([Holiday Trips], [Typical Same Weekday Trips]) - 1)",
                "+0.0%;-0.0%;0.0%",
                "Holiday trips compared with the typical same-weekday baseline (Typical Same Weekday Trips). -55% means 55% fewer trips than a normal day.",
                "Seasonality"),
        col("Pickup Date", "pickup_date", "dateTime", hidden=True, fmt="yyyy-mm-dd"),
        col("Pickup Location ID", "pu_location_id", "int64", hidden=True, summarize="none"),
        col("Taxi Type", "taxi_type", "string", desc="yellow = medallion taxi (street hails anywhere); green = Boro taxi (street hails outside central Manhattan and the airports)."),
        col("Dropoff Borough", "do_borough", "string", desc="Borough of the dropoff zone."),
        col("Payment Type", "payment_type", "string", desc="Payment method recorded by the meter (Credit card, Cash, No charge, Dispute, ...)."),
        col("Trip Count", "trip_count", "int64", hidden=True, summarize="sum"),
        col("Passenger Count", "passenger_count", "int64", hidden=True, summarize="sum"),
        col("Total Distance Miles", "total_distance_miles", "decimal", hidden=True, summarize="sum"),
        col("Total Duration Min", "total_duration_min", "decimal", hidden=True, summarize="sum"),
        col("Fare Amount", "fare_amount", "decimal", hidden=True, summarize="sum"),
        col("Total Amount", "total_amount", "decimal", hidden=True, summarize="sum"),
        col("Card Trip Count", "card_trip_count", "int64", hidden=True, summarize="sum"),
        col("Card Fare Amount", "card_fare_amount", "decimal", hidden=True, summarize="sum"),
        col("Card Tip Amount", "card_tip_amount", "decimal", hidden=True, summarize="sum"),
        col("Airport Trip Count", "airport_trip_count", "int64", hidden=True, summarize="sum"),
    ]
    files["definition/tables/Taxi Trips.tmdl"] = table(
        "Taxi Trips", "Daily trip totals. Grain: pickup date x taxi type x pickup zone x dropoff borough x payment type.",
        trips, "fact_trips_daily_zone")

    date = [
        col("Date", "date", "dateTime", fmt="yyyy-mm-dd", desc="Calendar date; covers full calendar years of the data."),
        col("Year", "year", "int64", summarize="none", fmt="0"),
        col("Quarter", "quarter", "int64", summarize="none", fmt="0"),
        col("Month Number", "month", "int64", hidden=True, summarize="none"),
        col("Month", "month_name", "string", sort_by="Month Number"),
        col("Month Short", "month_short", "string", sort_by="Month Number", desc="Three-letter month name for chart axes."),
        col("Year Month", "year_month", "string", desc="Year and month as yyyy-MM."),
        col("Day", "day", "int64", summarize="none"),
        col("Day of Week Number", "day_of_week", "int64", hidden=True, summarize="none"),
        col("Day of Week", "day_name", "string", sort_by="Day of Week Number", desc="Weekday name, sorted Monday to Sunday."),
        col("Is Weekend", "is_weekend", "boolean"),
        col("Is Holiday", "is_holiday", "boolean", desc="True on US federal holidays (actual date, not the observed day off)."),
        col("Holiday", "holiday_name", "string"),
        f"{T(1)}hierarchy Calendar\n\n{T(2)}level Year\n{T(3)}column: Year\n\n{T(2)}level Quarter\n{T(3)}column: Quarter\n\n"
        f"{T(2)}level Month\n{T(3)}column: Month\n\n{T(2)}level Date\n{T(3)}column: Date\n",
    ]
    files["definition/tables/Date.tmdl"] = table("Date", "Calendar table, one row per day.", date, "dim_date",
                                                 extra_header=("dataCategory: Time",))

    zone = [
        col("Location ID", "location_id", "int64", hidden=True, summarize="none"),
        col("Borough", "borough", "string", desc="Pickup borough (EWR = Newark Airport)."),
        col("Zone", "zone", "string", desc="TLC taxi zone of the pickup."),
        col("Service Zone", "service_zone", "string", desc="TLC service area: Yellow Zone (central Manhattan), Boro Zone, Airports or EWR."),
    ]
    files["definition/tables/Pickup Zone.tmdl"] = table("Pickup Zone", "TLC taxi zones, joined to trips on the pickup location.", zone, "dim_zone")

    hourly = [
        measure("Hourly Trips", "SUM('Hourly Demand'[Trip Count])", "#,##0",
                "Trips from the hourly demand table; use with its own Year Month / Taxi Type / Day of Week / Hour fields.", "Demand"),
        measure("Share of Selected Trips", """VAR SelectedTrips =
    CALCULATE(
        [Hourly Trips],
        ALLSELECTED('Hourly Demand'[Day of Week], 'Hourly Demand'[Day of Week Number], 'Hourly Demand'[Hour])
    )
RETURN
    DIVIDE([Hourly Trips], SelectedTrips)""", "0.0%",
                "Trips in this weekday/hour as a share of all trips in the current slicer selection; "
                "comparable across boroughs, months and taxi types regardless of volume.", "Demand"),
        measure("Selected Weekday and Hour", """VAR DayName = SELECTEDVALUE('Hourly Demand'[Day of Week])
VAR HourNumber = SELECTEDVALUE('Hourly Demand'[Hour])
RETURN
    IF(
        NOT ISBLANK(DayName) && NOT ISBLANK(HourNumber),
        DayName & ", " & FORMAT(HourNumber, "00") & ":00-" & FORMAT(HourNumber + 1, "00") & ":00",
        "Several weekdays / hours"
    )""", "General", "Label for the weekday and hour currently in context; used as the heatmap tooltip title.", "Demand"),
        measure("Hourly Revenue", "SUM('Hourly Demand'[Total Amount])", "$#,##0", "Total amount charged, from the hourly demand table.", "Demand"),
        measure("Average Speed (mph)", "DIVIDE(SUM('Hourly Demand'[Total Distance Miles]), SUM('Hourly Demand'[Total Duration Min]) / 60)", "#,##0.0",
                "Average trip speed: total miles divided by total hours in motion.", "Demand"),
        col("Year Month", "year_month", "string", desc="Pickup month as yyyy-MM."),
        col("Taxi Type", "taxi_type", "string"),
        col("Day of Week Number", "pickup_day_of_week", "int64", hidden=True, summarize="none"),
        col("Day of Week", "pickup_day_name", "string", sort_by="Day of Week Number"),
        col("Hour", "pickup_hour", "int64", summarize="none", fmt="0", desc="Pickup hour of day, 0-23 (New York local time)."),
        col("Pickup Borough", "pu_borough", "string"),
        col("Trip Count", "trip_count", "int64", hidden=True, summarize="sum"),
        col("Total Amount", "total_amount", "decimal", hidden=True, summarize="sum"),
        col("Total Distance Miles", "total_distance_miles", "decimal", hidden=True, summarize="sum"),
        col("Total Duration Min", "total_duration_min", "decimal", hidden=True, summarize="sum"),
    ]
    files["definition/tables/Hourly Demand.tmdl"] = table(
        "Hourly Demand", "Demand pattern aggregate (month x taxi type x weekday x hour x pickup borough). Standalone by design: "
        "its grain has no date, so filter it with its own fields rather than the Date table.", hourly, "hourly_demand")

    layers = [
        measure("Bronze Rows", "SUM('Pipeline Layers'[Bronze Row Count])", "#,##0", "Rows landed in Bronze from the TLC source files.", "Data quality"),
        measure("Silver Rows", "SUM('Pipeline Layers'[Silver Row Count])", "#,##0", "Rows that passed all Silver quality rules.", "Data quality"),
        measure("Duplicate Rows", "SUM('Pipeline Layers'[Duplicate Row Count])", "#,##0",
                "Rows removed as exact duplicates on vendor, pickup/dropoff time and location, and total amount.", "Data quality"),
        measure("Rejection Rate", "DIVIDE([Rejected Rows], [Bronze Rows])", "0.00%", "Rejected rows as a share of Bronze rows.", "Data quality"),
        measure("Rows Kept %", "DIVIDE([Silver Rows], [Bronze Rows])", "0.00%", "Share of Bronze rows that reached Silver.", "Data quality"),
        measure("Gold Reconciliation", """VAR SilverTotal = CALCULATE([Silver Rows], REMOVEFILTERS())
VAR GoldTotal = CALCULATE([Trips], REMOVEFILTERS())
RETURN
    IF(SilverTotal = GoldTotal, "Reconciled", "Mismatch: " & FORMAT(GoldTotal - SilverTotal, "#,##0"))""", "General",
                "Checks that Gold trip totals equal Silver valid rows across the whole dataset.", "Data quality"),
        col("Month Type Key", "month_type_key", "string", hidden=True),
        col("Year Month", "year_month", "string", desc="Source file month as yyyy-MM."),
        col("Taxi Type", "taxi_type", "string"),
        col("Bronze Row Count", "bronze_rows", "int64", hidden=True, summarize="sum"),
        col("Silver Row Count", "silver_rows", "int64", hidden=True, summarize="sum"),
        col("Duplicate Row Count", "duplicate_rows", "int64", hidden=True, summarize="sum"),
    ]
    files["definition/tables/Pipeline Layers.tmdl"] = table(
        "Pipeline Layers", "Row counts per medallion layer. Grain: source month x taxi type. Standalone from the trip tables by design.",
        layers, "dq_layer_counts")

    rejections = [
        measure("Rejected Rows", "SUM(Rejections[Rejected Row Count])", "#,##0",
                "Rows that failed a Silver quality rule and were written to the rejected table.", "Data quality"),
        col("Month Type Key", "month_type_key", "string", hidden=True),
        col("Year Month", "year_month", "string", hidden=True),
        col("Taxi Type", "taxi_type", "string", hidden=True),
        col("Reject Reason", "reject_reason", "string",
            desc="First Silver rule the row failed, e.g. negative_amount (refunds/voids) or non_positive_duration."),
        col("Rejected Row Count", "rejected_rows", "int64", hidden=True, summarize="sum"),
    ]
    files["definition/tables/Month.tmdl"] = table(
        "Month", "Shared month slicer table; filters trips (via Date), hourly demand and pipeline layers.", [
            col("Year Month", "year_month", "string", desc="yyyy-MM"),
            col("Year", "year", "int64", summarize="none", fmt="0"),
            col("Month Number", "month", "int64", hidden=True, summarize="none"),
            col("Month", "month_name", "string", sort_by="Month Number"),
            col("Month Short", "month_short", "string", sort_by="Month Number"),
        ], "dim_month")
    files["definition/tables/Borough.tmdl"] = table(
        "Borough", "Shared pickup-borough slicer table; filters trips (via Pickup Zone) and hourly demand.", [
            col("Borough", "borough", "string", desc="NYC borough of the pickup (EWR = Newark Airport)."),
        ], "dim_borough")
    files["definition/tables/Taxi Type.tmdl"] = table(
        "Taxi Type", "Shared taxi-type slicer table; filters trips, hourly demand and pipeline layers.", [
            col("Taxi Type", "taxi_type", "string", desc="yellow = medallion taxi; green = Boro taxi."),
        ], "dim_taxi_type")
    files["definition/tables/Rejections.tmdl"] = table(
        "Rejections", "Rejected rows by quality rule. Grain: source month x taxi type x reject reason.", rejections, "dq_rejections")
    return files


def write(files):
    for path, content in files.items():
        p = os.path.join(OUT, *path.split("/"))
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8", newline="\n") as f:
            f.write(content)
    print(f"wrote {len(files)} files to {OUT}")


def parts(files):
    return {"format": "TMDL", "parts": [{"path": p, "payload": base64.b64encode(c.encode("utf-8")).decode(),
                                          "payloadType": "InlineBase64"} for p, c in files.items()]}


def model_id(ws_id):
    models = [m for m in fabric.call("GET", f"/workspaces/{ws_id}/semanticModels")["value"] if m["displayName"] == MODEL_NAME]
    if len(models) > 1:
        raise RuntimeError(f"{len(models)} models named {MODEL_NAME} - delete duplicates first")
    return models[0]["id"] if models else None


def deploy():
    ws_id, lh_id = fabric.ids()
    files = build(ws_id, lh_id)
    write(files)
    mid = model_id(ws_id)
    if mid:
        fabric.call("POST", f"/workspaces/{ws_id}/semanticModels/{mid}/updateDefinition", {"definition": parts(files)}, want_result=False)
        print(f"updated semantic model {mid}")
    else:
        fabric.call("POST", f"/workspaces/{ws_id}/semanticModels", {"displayName": MODEL_NAME, "definition": parts(files)})
        print(f"created semantic model {model_id(ws_id)}")


def pbi(method, path, body=None):
    tok = os.popen("az account get-access-token --resource https://analysis.windows.net/powerbi/api --query accessToken -o tsv").read().strip()
    r = requests.request(method, PBI + path, headers={"Authorization": f"Bearer {tok}"}, json=body, timeout=120)
    if r.status_code >= 400:
        raise RuntimeError(f"HTTP {r.status_code} {path}: {r.text}")
    return r


def refresh():
    ws_id, _ = fabric.ids()
    mid = model_id(ws_id)
    pbi("POST", f"/groups/{ws_id}/datasets/{mid}/refreshes", {"type": "full"})
    while True:
        time.sleep(10)
        last = pbi("GET", f"/groups/{ws_id}/datasets/{mid}/refreshes?$top=1").json()["value"][0]
        print("refresh:", last["status"])
        if last["status"] != "Unknown":
            if last["status"] != "Completed":
                print(json.dumps(last, indent=2))
            return last["status"]


def query(dax):
    ws_id, _ = fabric.ids()
    mid = model_id(ws_id)
    r = pbi("POST", f"/groups/{ws_id}/datasets/{mid}/executeQueries",
            {"queries": [{"query": dax}], "serializerSettings": {"includeNulls": True}})
    res = r.json()["results"][0]
    if "error" in res:
        raise RuntimeError(json.dumps(res["error"]))
    for row in res["tables"][0]["rows"]:
        print(json.dumps(row))


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "write":
        write(build("<workspace-id>", "<lakehouse-id>"))
    elif cmd == "deploy":
        deploy()
    elif cmd == "refresh":
        sys.exit(0 if refresh() == "Completed" else 1)
    elif cmd == "query":
        query(sys.argv[2])
    else:
        print(__doc__)
