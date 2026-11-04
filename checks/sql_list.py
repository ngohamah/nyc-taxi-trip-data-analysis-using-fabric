import os, sys; sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))  # shared helpers live in src/
from sql_views import connect
cur = connect().cursor()
for r in cur.execute("SELECT s.name, t.name FROM sys.tables t JOIN sys.schemas s ON s.schema_id = t.schema_id ORDER BY 1, 2").fetchall():
    print(tuple(r))
print("schemas:", [r[0] for r in cur.execute("SELECT name FROM sys.schemas WHERE schema_id < 16384").fetchall()])
