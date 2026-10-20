import sqlite3
conn = sqlite3.connect('C:\\solenos\\backend\\solenos.db')
c = conn.cursor()
c.execute("SELECT name FROM sqlite_master WHERE type='table'")
rows = c.fetchall()
for row in rows:
    print(row)
conn.close()