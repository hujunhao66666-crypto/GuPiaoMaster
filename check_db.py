import sqlite3
import os

db_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'stock_master.db')

conn = sqlite3.connect(db_path)
cursor = conn.cursor()

# 查找金富科技的股票信息
cursor.execute('SELECT id, code, name FROM stocks WHERE name LIKE ?', ('%金富科技%',))
stocks = cursor.fetchall()

print('金富科技股票信息:')
for stock in stocks:
    stock_id, code, name = stock
    print(f'ID: {stock_id}, 代码: {code}, 名称: {name}')
    
    # 查询该股票的属性
    cursor.execute('SELECT attribute FROM stock_attributes WHERE stock_id = ?', (stock_id,))
    attributes = cursor.fetchall()
    print(f'属性数量: {len(attributes)}')
    print('属性列表:')
    for attr in attributes:
        print(f'  - {attr[0]}')

conn.close()