import sqlite3

# 连接数据库
conn = sqlite3.connect('stock_master.db')
cursor = conn.cursor()

# 检查股票名称是否包含五角星
try:
    # 使用参数化查询来避免语法问题
    cursor.execute("SELECT code, name FROM stocks WHERE name LIKE ?", ('%★%',))
    results = cursor.fetchall()
    
    print(f'Found {len(results)} stocks with star in name:')
    for code, name in results:
        print(f'{code}: {name}')
        
except Exception as e:
    print(f'Error: {e}')
finally:
    conn.close()