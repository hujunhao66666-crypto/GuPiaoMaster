import sqlite3

# 连接数据库
conn = sqlite3.connect('stock_master.db')
cursor = conn.cursor()

print('查找冠石科技的记录:')
try:
    cursor.execute('SELECT id, code, name FROM stocks WHERE name LIKE ?', ('%冠石科技%',))
    stocks = cursor.fetchall()
    print(f'发现 {len(stocks)} 条记录:')
    for stock in stocks:
        stock_id, code, name = stock
        print(f'  ID: {stock_id}, 代码: {code}, 名称: {name}')
        
        # 查看该股票的属性
        cursor.execute('SELECT attribute FROM stock_attributes WHERE stock_id = ?', (stock_id,))
        attrs = cursor.fetchall()
        attr_list = [attr[0] for attr in attrs]
        print(f'  属性: {attr_list}')
        print(f'  属性数量: {len(attr_list)}')
        print()
        
except Exception as e:
    print(f'查询时出错: {e}')

conn.close()