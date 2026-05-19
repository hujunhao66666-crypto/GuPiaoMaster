import sqlite3

# 连接数据库
conn = sqlite3.connect('stock_master.db')
cursor = conn.cursor()

# 检查stock_attributes表结构
print('stock_attributes表结构:')
try:
    cursor.execute('PRAGMA table_info(stock_attributes);')
    columns = cursor.fetchall()
    for col in columns:
        print(f'  {col[1]} ({col[2]})')
except Exception as e:
    print(f'检查表结构时出错: {e}')

# 检查stocks表结构
print('\nstocks表结构:')
try:
    cursor.execute('PRAGMA table_info(stocks);')
    columns = cursor.fetchall()
    for col in columns:
        print(f'  {col[1]} ({col[2]})')
except Exception as e:
    print(f'检查表结构时出错: {e}')

# 检查前10条stock_attributes记录
print('\n前10条stock_attributes记录:')
try:
    cursor.execute('SELECT * FROM stock_attributes LIMIT 10')
    records = cursor.fetchall()
    for record in records:
        print(f'  {record}')
except Exception as e:
    print(f'检查记录时出错: {e}')

conn.close()