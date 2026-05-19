import sqlite3

# 连接数据库
conn = sqlite3.connect('stock_master.db')
cursor = conn.cursor()

print('检查涨停梯队相关表结构:')
# 检查ladder_nodes表
try:
    cursor.execute('PRAGMA table_info(ladder_nodes);')
    columns = cursor.fetchall()
    print('ladder_nodes表结构:')
    for col in columns:
        print(f'  {col[1]} ({col[2]})')
except Exception as e:
    print(f'检查ladder_nodes表时出错: {e}')

# 检查ladder_stocks表
try:
    cursor.execute('PRAGMA table_info(ladder_stocks);')
    columns = cursor.fetchall()
    print('\nladder_stocks表结构:')
    for col in columns:
        print(f'  {col[1]} ({col[2]})')
except Exception as e:
    print(f'检查ladder_stocks表时出错: {e}')

# 检查ladder_settings表
try:
    cursor.execute('PRAGMA table_info(ladder_settings);')
    columns = cursor.fetchall()
    print('\nladder_settings表结构:')
    for col in columns:
        print(f'  {col[1]} ({col[2]})')
except Exception as e:
    print(f'检查ladder_settings表时出错: {e}')

# 检查数据
print('\n检查ladder_nodes数据:')
try:
    cursor.execute('SELECT COUNT(*) FROM ladder_nodes')
    count = cursor.fetchone()[0]
    print(f'ladder_nodes记录数: {count}')
    
    # 查看最近的几条记录
    cursor.execute('SELECT id, date, node_level, name FROM ladder_nodes ORDER BY date DESC LIMIT 10')
    nodes = cursor.fetchall()
    print('最近10条ladder_nodes记录:')
    for node in nodes:
        print(f'  ID: {node[0]}, 日期: {node[1]}, 节点级别: {node[2]}, 名称: {node[3]}')
except Exception as e:
    print(f'检查ladder_nodes数据时出错: {e}')

print('\n检查ladder_stocks数据:')
try:
    cursor.execute('SELECT COUNT(*) FROM ladder_stocks')
    count = cursor.fetchone()[0]
    print(f'ladder_stocks记录数: {count}')
    
    # 查看最近的几条记录
    cursor.execute('''
        SELECT ls.id, ls.stock_id, s.code, s.name, ln.date, ln.node_level
        FROM ladder_stocks ls
        JOIN stocks s ON ls.stock_id = s.id
        JOIN ladder_nodes ln ON ls.ladder_node_id = ln.id
        ORDER BY ln.date DESC LIMIT 10
    ''')
    stocks = cursor.fetchall()
    print('最近10条ladder_stocks记录:')
    for stock in stocks:
        print(f'  股票: {stock[2]} {stock[3]}, 日期: {stock[4]}, 节点级别: {stock[5]}')
except Exception as e:
    print(f'检查ladder_stocks数据时出错: {e}')

conn.close()