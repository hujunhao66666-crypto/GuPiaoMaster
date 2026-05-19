import sqlite3

# 连接数据库
conn = sqlite3.connect('stock_master.db')
cursor = conn.cursor()

# 清理股票名称中的五角星
try:
    # 首先检查需要更新的股票
    cursor.execute("SELECT code, name FROM stocks WHERE name LIKE ?", ('%★%',))
    stocks_to_update = cursor.fetchall()
    
    print(f'Found {len(stocks_to_update)} stocks with star in name:')
    for code, name in stocks_to_update:
        print(f'{code}: {name} -> {name.replace(" ★", "")}')
    
    # 更新股票名称，移除五角星
    cursor.execute("UPDATE stocks SET name = REPLACE(name, ' ★', '') WHERE name LIKE ?", ('%★%',))
    conn.commit()
    
    print(f'\nUpdated {cursor.rowcount} stocks successfully!')
    
    # 验证更新结果
    cursor.execute("SELECT code, name FROM stocks WHERE name LIKE ?", ('%★%',))
    remaining = cursor.fetchall()
    print(f'\nRemaining stocks with star in name: {len(remaining)}')
    for code, name in remaining:
        print(f'{code}: {name}')
        
except Exception as e:
    print(f'Error: {e}')
    conn.rollback()
finally:
    conn.close()