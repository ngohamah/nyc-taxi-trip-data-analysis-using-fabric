import os, sys; sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))  # shared helpers live in src/
import semantic_model as sm
F = "'Hourly Demand'[Taxi Type] = \"green\", 'Hourly Demand'[Pickup Borough] = \"Bronx\""
print("== green + Bronx total ==")
sm.query(f"EVALUATE ROW(\"Trips\", CALCULATE([Hourly Trips], {F}))")
print("== by weekday (row totals) ==")
sm.query(f"EVALUATE CALCULATETABLE(SUMMARIZECOLUMNS('Hourly Demand'[Day of Week Number], 'Hourly Demand'[Day of Week], \"Trips\", [Hourly Trips]), {F}) ORDER BY 'Hourly Demand'[Day of Week Number]")
print("== Friday by hour (cells) ==")
sm.query(f"EVALUATE CALCULATETABLE(SUMMARIZECOLUMNS('Hourly Demand'[Hour], \"Trips\", [Hourly Trips]), {F}, 'Hourly Demand'[Day of Week] = \"Friday\") ORDER BY 'Hourly Demand'[Hour]")
print("== all days by hour (bar chart) ==")
sm.query(f"EVALUATE CALCULATETABLE(SUMMARIZECOLUMNS('Hourly Demand'[Hour], \"Trips\", [Hourly Trips], \"Speed\", [Average Speed (mph)]), {F}) ORDER BY 'Hourly Demand'[Hour]")
