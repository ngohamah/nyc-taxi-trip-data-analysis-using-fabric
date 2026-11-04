import os, sys; sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))  # shared helpers live in src/
import semantic_model as sm

def grid(filters, picks):
    flt = f"TREATAS({{{filters}}}, 'Hourly Demand'[Taxi Type], 'Hourly Demand'[Pickup Borough])," if filters else ""
    sm.query(f"""EVALUATE FILTER(
  SUMMARIZECOLUMNS('Hourly Demand'[Day of Week], 'Hourly Demand'[Hour], {flt}
                   "Share", [Share of Selected Trips], "Trips", [Hourly Trips]),
  {picks})""")

print("== green + Bronx (slicer filters), Friday 21-22h ==")
grid('("green", "Bronx")', "'Hourly Demand'[Day of Week] = \"Friday\" && 'Hourly Demand'[Hour] IN {21, 22}")
print("== all trips, Thursday 18h ==")
grid("", "'Hourly Demand'[Day of Week] = \"Thursday\" && 'Hourly Demand'[Hour] = 18")
