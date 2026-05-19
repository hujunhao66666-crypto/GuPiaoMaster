import sqlite3

db_path = 'stock_master.db'

conn = sqlite3.connect(db_path)
cursor = conn.cursor()

# Check all ladder_stocks
print("=== All Ladder Stocks ===")
cursor.execute('''
    SELECT ls.id, ls.ladder_node_id, s.code, s.name
    FROM ladder_stocks ls
    JOIN stocks s ON ls.stock_id = s.id
''')
all_stocks = cursor.fetchall()
print(f"Total ladder stocks: {len(all_stocks)}")
for stock in all_stocks[:20]:  # Show first 20
    print(f"  {stock}")

# Check ladder_stocks with node info
print("\n=== Ladder Stocks with Node Info ===")
cursor.execute('''
    SELECT ln.date, ln.node_level, s.code, s.name
    FROM ladder_stocks ls
    JOIN ladder_nodes ln ON ls.ladder_node_id = ln.id
    JOIN stocks s ON ls.stock_id = s.id
    ORDER BY ln.date DESC, ln.node_level
''')
stocks_with_info = cursor.fetchall()
print(f"Total: {len(stocks_with_info)}")
for stock in stocks_with_info[:30]:  # Show first 30
    print(f"  Date: {stock[0]}, Level: {stock[1]}, Stock: {stock[2]} {stock[3]}")

conn.close()