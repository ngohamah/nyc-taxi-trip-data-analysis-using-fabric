"""Run DAX checks against the deployed 'NYC Taxi' model: python checks/validate_model.py"""
import os, sys; sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))  # shared helpers live in src/
import semantic_model as sm

print("== Totals ==")
sm.query("""EVALUATE ROW(
  "Trips", [Trips], "Revenue", [Revenue], "Average Fare", [Average Fare], "Card Tip %", [Card Tip %],
  "Card Payment Share", [Card Payment Share], "Airport Trip Share", [Airport Trip Share],
  "Average Distance", [Average Distance (miles)], "Average Duration", [Average Duration (min)],
  "Revenue per Mile", [Revenue per Mile], "Hourly Trips", [Hourly Trips], "Average Speed", [Average Speed (mph)])""")

print("== By taxi type ==")
sm.query("""EVALUATE SUMMARIZECOLUMNS('Taxi Trips'[Taxi Type], "Trips", [Trips], "Revenue", [Revenue], "Average Fare", [Average Fare])""")

print("== Month over month (yellow) ==")
sm.query("""EVALUATE CALCULATETABLE(
  SUMMARIZECOLUMNS('Date'[Month Number], 'Date'[Month], "Trips", [Trips], "Previous", [Trips Previous Month], "MoM", [Trips MoM Change %]),
  'Taxi Trips'[Taxi Type] = "yellow") ORDER BY 'Date'[Month Number]""")

print("== By pickup borough ==")
sm.query("""EVALUATE SUMMARIZECOLUMNS('Pickup Zone'[Borough], "Trips", [Trips], "Average Fare", [Average Fare]) ORDER BY [Trips] DESC""")

print("== Remaining measures (executeQueries does not allow INFO.* functions, so evaluate them directly) ==")
sm.query("""EVALUATE ROW("Passengers", [Passengers], "Fare Revenue", [Fare Revenue],
  "Average Revenue per Trip", [Average Revenue per Trip], "Hourly Revenue", [Hourly Revenue])""")
