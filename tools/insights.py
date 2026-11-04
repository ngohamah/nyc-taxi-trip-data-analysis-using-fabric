"""Pull the figures used in the report's insight panels: python tools/insights.py"""
import os, sys; sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))  # shared helpers live in src/
import semantic_model as sm

Q = {
 "Months by trips": """EVALUATE SUMMARIZECOLUMNS('Date'[Month Number], 'Date'[Month], "Trips", [Trips], "Avg daily", [Average Daily Trips], "Avg fare", [Average Fare]) ORDER BY [Trips] DESC""",
 "Borough share": """EVALUATE SUMMARIZECOLUMNS('Pickup Zone'[Borough], "Trips", [Trips], "Share", DIVIDE([Trips], CALCULATE([Trips], REMOVEFILTERS('Pickup Zone'))), "Avg fare", [Average Fare], "Rev/mile", [Revenue per Mile]) ORDER BY [Trips] DESC""",
 "Taxi type profile": """EVALUATE SUMMARIZECOLUMNS('Taxi Trips'[Taxi Type], "Trips", [Trips], "Avg fare", [Average Fare], "Avg rev", [Average Revenue per Trip], "Dist", [Average Distance (miles)], "Dur", [Average Duration (min)], "Tip%", [Card Tip %], "Card%", [Card Payment Share], "Airport%", [Airport Trip Share], "Rev/mile", [Revenue per Mile])""",
 "Borough mix by type": """EVALUATE SUMMARIZECOLUMNS('Taxi Trips'[Taxi Type], 'Pickup Zone'[Borough], "Share", DIVIDE([Trips], CALCULATE([Trips], REMOVEFILTERS('Pickup Zone')))) ORDER BY 'Taxi Trips'[Taxi Type], [Share] DESC""",
 "Payment mix by type": """EVALUATE SUMMARIZECOLUMNS('Taxi Trips'[Taxi Type], 'Taxi Trips'[Payment Type], "Share", DIVIDE([Trips], CALCULATE([Trips], REMOVEFILTERS('Taxi Trips'[Payment Type])))) ORDER BY 'Taxi Trips'[Taxi Type], [Share] DESC""",
 "Green share by month": """EVALUATE SUMMARIZECOLUMNS('Date'[Month Number], "Green share", [Green Trip Share], "Green fare", CALCULATE([Average Fare], 'Taxi Trips'[Taxi Type]="green"), "Yellow fare", CALCULATE([Average Fare], 'Taxi Trips'[Taxi Type]="yellow")) ORDER BY 'Date'[Month Number]""",
 "Top zones": """EVALUATE TOPN(8, SUMMARIZECOLUMNS('Pickup Zone'[Zone], 'Pickup Zone'[Borough], "Trips", [Trips], "Avg fare", [Average Fare], "Tip%", [Card Tip %]), [Trips], DESC) ORDER BY [Trips] DESC""",
 "Zone concentration": """EVALUATE ROW("Zones with trips", COUNTROWS(FILTER(VALUES('Pickup Zone'[Location ID]), [Trips] > 0)), "Top 10 share", DIVIDE(SUMX(TOPN(10, VALUES('Pickup Zone'[Location ID]), [Trips], DESC), [Trips]), [Trips]))""",
 "Flows": """EVALUATE TOPN(6, SUMMARIZECOLUMNS('Pickup Zone'[Borough], 'Taxi Trips'[Dropoff Borough], "Trips", [Trips], "Share", DIVIDE([Trips], CALCULATE([Trips], REMOVEFILTERS('Pickup Zone'), REMOVEFILTERS('Taxi Trips'[Dropoff Borough])))), [Trips], DESC) ORDER BY [Trips] DESC""",
 "Weekdays": """EVALUATE SUMMARIZECOLUMNS('Date'[Day of Week Number], 'Date'[Day of Week], "Avg daily", [Average Daily Trips], "Avg fare", [Average Fare]) ORDER BY 'Date'[Day of Week Number]""",
 "Holidays": """EVALUATE SUMMARIZECOLUMNS('Date'[Date], 'Date'[Holiday], 'Date'[Day of Week], "Trips", [Holiday Trips], "vs typical", [Holiday vs Typical Weekday %]) ORDER BY [vs typical]""",
 "Peak hours": """EVALUATE TOPN(5, SUMMARIZECOLUMNS('Hourly Demand'[Day of Week], 'Hourly Demand'[Hour], "Trips", [Hourly Trips]), [Trips], DESC) ORDER BY [Trips] DESC""",
 "Speed by hour": """EVALUATE SUMMARIZECOLUMNS('Hourly Demand'[Hour], "Trips", [Hourly Trips], "Speed", [Average Speed (mph)]) ORDER BY 'Hourly Demand'[Hour]""",
 "Data quality": """EVALUATE SUMMARIZECOLUMNS('Pipeline Layers'[Taxi Type], "Bronze", [Bronze Rows], "Silver", [Silver Rows], "Rejected", [Rejected Rows], "Dupes", [Duplicate Rows], "Rate", [Rejection Rate], "Recon", [Gold Reconciliation])""",
 "Reject reasons": """EVALUATE SUMMARIZECOLUMNS(Rejections[Reject Reason], "Rejected", [Rejected Rows], "Share", DIVIDE([Rejected Rows], CALCULATE([Rejected Rows], REMOVEFILTERS(Rejections[Reject Reason])))) ORDER BY [Rejected] DESC""",
 "Rejection by month": """EVALUATE SUMMARIZECOLUMNS('Pipeline Layers'[Year Month], "Rate", [Rejection Rate]) ORDER BY 'Pipeline Layers'[Year Month]""",
}
for name, dax in Q.items():
    print(f"== {name} ==")
    sm.query(dax)
