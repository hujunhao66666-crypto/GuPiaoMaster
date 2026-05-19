import sqlite3

# 连接数据库
conn = sqlite3.connect('stock_master.db')
cursor = conn.cursor()

print('检查重复股票代码:')
try:
    cursor.execute('SELECT code, COUNT(*) FROM stocks GROUP BY code HAVING COUNT(*) > 1')
    duplicates = cursor.fetchall()
    print(f'发现 {len(duplicates)} 个重复代码')
    for code, count in duplicates:
        print(f'{code}: {count}')
except Exception as e:
    print(f'检查重复代码时出错: {e}')

print('\n检查重复股票名称:')
try:
    cursor.execute('SELECT name, COUNT(*) FROM stocks GROUP BY name HAVING COUNT(*) > 1')
    duplicates = cursor.fetchall()
    print(f'发现 {len(duplicates)} 个重复名称')
    for name, count in duplicates:
        print(f'{name}: {count}')
except Exception as e:
    print(f'检查重复名称时出错: {e}')

# 检查属性是否正确存储
print('\n检查股票属性存储情况:')
try:
    cursor.execute('SELECT COUNT(*) FROM stock_attributes')
    attr_count = cursor.fetchone()[0]
    print(f'总属性数量: {attr_count}')
    
    # 检查前10只股票的属性
    cursor.execute('SELECT id, code, name FROM stocks LIMIT 10')
    stocks = cursor.fetchall()
    print('\n前10只股票的属性数量:')
    for stock_id, code, name in stocks:
        cursor.execute('SELECT COUNT(*) FROM stock_attributes WHERE stock_id = ?', (stock_id,))
        count = cursor.fetchone()[0]
        print(f'{code} {name}: {count} 个属性')
        
        # 显示前3个属性
        cursor.execute('SELECT attribute FROM stock_attributes WHERE stock_id = ? LIMIT 3', (stock_id,))
        attrs = cursor.fetchall()
        if attrs:
            attr_list = [attr[0] for attr in attrs]
            print(f'  前3个属性: {attr_list}')
            
except Exception as e:
    print(f'检查属性时出错: {e}')

conn.close()