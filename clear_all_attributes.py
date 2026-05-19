import sqlite3

# 数据库路径
db_path = 'stock_master.db'

# 连接数据库
conn = sqlite3.connect(db_path)
cursor = conn.cursor()

print('开始删除所有股票的属性记录...')

# 统计删除前的属性数量
cursor.execute('SELECT COUNT(*) FROM stock_attributes')
before_count = cursor.fetchone()[0]
print(f'删除前属性记录数: {before_count}')

# 删除所有属性记录
cursor.execute('DELETE FROM stock_attributes')
conn.commit()

# 统计删除后的属性数量
cursor.execute('SELECT COUNT(*) FROM stock_attributes')
after_count = cursor.fetchone()[0]
print(f'删除后属性记录数: {after_count}')
print(f'成功删除 {before_count - after_count} 条属性记录')

# 关闭数据库连接
conn.close()
print('数据库连接已关闭')
print('所有股票的属性已删除完成')