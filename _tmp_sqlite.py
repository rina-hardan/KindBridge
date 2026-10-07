import sqlite3

conn = sqlite3.connect(r"C:\Users\User\AppData\Local\Temp\kindbridge_ui.db")
print("requests:")
for row in conn.execute(
    "SELECT id, category, status, preferred_date, length(description), created_at FROM help_requests"
):
    print(row)
print("events:")
for row in conn.execute(
    "SELECT event_type, version, created_at FROM event_store WHERE aggregate_type = 'HelpRequest'"
):
    print(row)
