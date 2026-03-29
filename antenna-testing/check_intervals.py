import csv
from datetime import datetime

TIME_COL = "timestamp"
CSV_FILE = "time_limit.csv"

with open(CSV_FILE) as f:
    rows = list(csv.DictReader(f))

# Parse timestamps and numeric values
data_cols = [c for c in rows[0].keys() if c != TIME_COL]

parsed = []
for row in rows:
    ts = datetime.fromisoformat(row[TIME_COL])
    vals = tuple(row[c] for c in data_cols)
    parsed.append((ts, vals))

# Find consecutive pairs where values actually changed
intervals = []
for i in range(1, len(parsed)):
    ts_prev, vals_prev = parsed[i - 1]
    ts_curr, vals_curr = parsed[i]
    if vals_curr != vals_prev:
        dt = (ts_curr - ts_prev).total_seconds()
        intervals.append(dt)

if not intervals:
    print("No consecutive rows with different values found.")
else:
    min_interval = min(intervals)
    count = intervals.count(min_interval)
    print(f"Shortest interval between different consecutive rows: {min_interval:.6f}s")
    print(f"Times this exact interval appears: {count}")
    print(f"Total consecutive-different pairs: {len(intervals)}")
