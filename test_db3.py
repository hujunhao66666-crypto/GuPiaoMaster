import sqlite3
from datetime import datetime, timedelta

db_path = 'stock_master.db'

yesterday = datetime.now().date() - timedelta(days=1)
yesterday_str = yesterday.strftime('%Y-%m-%d')

conn = sqlite3.connect(db_path)
cursor = conn.cursor()

# Check ladder_nodes for yesterday
print(f"=== Ladder Nodes for {yesterday_str} ===")
cursor.execute('''
    SELECT id, date, node_level, node_name
    FROM ladder_nodes
    WHERE date = ?
''', (yesterday_str,))
nodes = cursor.fetchall()
print(f"Nodes: {nodes}")

# Check ladder_stocks using JOIN
print(f"\n=== Ladder Stocks JOIN Query for {yesterday_str} ===")
cursor.execute('''
    SELECT s.code, s.name
    FROM ladder_stocks ls
    JOIN ladder_nodes ln ON ls.ladder_node_id = ln.id
    JOIN stocks s ON ls.stock_id = s.id
    WHERE ln.date = ?
''', (yesterday_str,))
stocks = cursor.fetchall()
print(f"Stocks: {stocks}")

# Check if ladder_node_id 108 exists
print(f"\n=== Check ladder_node_id 108 ===")
cursor.execute('SELECT id, date, node_level FROM ladder_nodes WHERE id = 108')
node108 = cursor.fetchall()
print(f"Node 108: {node108}")

conn.close()