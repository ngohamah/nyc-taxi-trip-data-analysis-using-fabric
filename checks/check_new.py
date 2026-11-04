import os, sys; sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))  # shared helpers live in src/
import semantic_model as sm
print("== shared slicer tables ==")
sm.query("EVALUATE ROW(\"months\", COUNTROWS(Month), \"boroughs\", COUNTROWS(Borough), \"taxi types\", COUNTROWS('Taxi Type'))")
print("== Borough slicer filters trips AND hourly demand: Bronx + Queens ==")
sm.query("""EVALUATE CALCULATETABLE(ROW("Trips", [Trips], "Hourly Trips", [Hourly Trips], "Bronze rows (not borough-based)", [Bronze Rows]),
  TREATAS({"Bronx", "Queens"}, Borough[Borough]))""")
print("== Month slicer (Mar + Apr) filters trips, hourly and pipeline layers ==")
sm.query("""EVALUATE CALCULATETABLE(ROW("Trips", [Trips], "Hourly Trips", [Hourly Trips], "Silver rows", [Silver Rows]),
  TREATAS({"March", "April"}, Month[Month]))""")
print("== Taxi type = green across tables ==")
sm.query("""EVALUATE CALCULATETABLE(ROW("Trips", [Trips], "Hourly", [Hourly Trips], "Silver", [Silver Rows], "Green share", [Green Trip Share]),
  'Taxi Type'[Taxi Type] = "green")""")
print("== Revenue contribution (top 3 zones) and total ==")
sm.query("""EVALUATE TOPN(3, SUMMARIZECOLUMNS('Pickup Zone'[Zone], "Share", [Revenue Contribution %]), [Share], DESC) ORDER BY [Share] DESC""")
print("== Holidays: typical baseline unaffected by month slicer (December selected) ==")
sm.query("""EVALUATE CALCULATETABLE(SUMMARIZECOLUMNS('Date'[Holiday], "Trips", [Holiday Trips], "Typical", [Typical Same Weekday Trips], "vs", [Holiday vs Typical Weekday %]),
  TREATAS({"December"}, Month[Month]))""")
print("== MoM still works with month slicer (April) ==")
sm.query("""EVALUATE CALCULATETABLE(ROW("Trips", [Trips], "Prev", [Trips Previous Month], "MoM", [Trips MoM Change %]), TREATAS({"April"}, Month[Month]))""")
