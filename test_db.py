import sqlite3
from datetime import datetime, timedelta

db_path = 'stock_master.db'

today = datetime.now().date()
yesterday = today - timedelta(days=1)
today_str = today.strftime('%Y-%m-%d')
yesterday_str = yesterday.strftime('%Y-%m-%d')

print(f"Today: {today_str}")
print(f"Yesterday: {yesterday_str}")

conn = sqlite3.connect(db_path)
cursor = conn.cursor()

# Check bidding records
print("\n=== Bidding Records ===")
cursor.execute('''
    SELECT br.date, s.code, s.name
    FROM bidding_records br
    JOIN stocks s ON br.stock_id = s.id
    WHERE br.date = ?
''', (today_str,))
bidding_records = cursor.fetchall()
print(f"Records for today ({today_str}): {len(bidding_records)}")
for record in bidding_records:
    print(f"  {record}")

# Check stock attributes for bidding records
print("\n=== Stock Attributes for Bidding Records ===")
cursor.execute('''
    SELECT sa.attribute
    FROM bidding_records br
    JOIN stock_attributes sa ON br.stock_id = sa.stock_id
    WHERE br.date = ?
''', (today_str,))
attrs = cursor.fetchall()
print(f"Attributes count: {len(attrs)}")
for attr in attrs:
    print(f"  {attr[0]}")

# Check ladder nodes for yesterday
print(f"\n=== Ladder Nodes for {yesterday_str} ===")
cursor.execute('''
    SELECT id, node_level, node_name
    FROM ladder_nodes
    WHERE date = ?
''', (yesterday_str,))
nodes = cursor.fetchall()
print(f"Nodes count: {len(nodes)}")
for node in nodes:
    print(f"  Node {node[0]}: level={node[1]}, name={node[2]}")

# Check ladder stocks for yesterday
print(f"\n=== Ladder Stocks for {yesterday_str} ===")
cursor.execute('''
    SELECT s.code, s.name
    FROM ladder_stocks ls
    JOIN ladder_nodes ln ON ls.ladder_node_id = ln.id
    JOIN stocks s ON ls.stock_id = s.id
    WHERE ln.date = ?
''', (yesterday_str,))
ladder_stocks = cursor.fetchall()
print(f"Ladder stocks count: {len(ladder_stocks)}")
for stock in ladder_stocks:
    print(f"  {stock[0]} {stock[1]}")

conn.close()