import pandas as pd
import sqlite3
import os

# 数据库路径
db_path = 'stock_master.db'

# Excel文件路径
excel_path = r'C:\Users\86453\Desktop\2025xiabangupiaodaima.xlsx'

# 连接数据库
conn = sqlite3.connect(db_path)
cursor = conn.cursor()

print('开始导入股票数据...')

# 读取Excel文件
try:
    df = pd.read_excel(excel_path)
    print(f'成功读取Excel文件，共 {len(df)} 行数据')
except Exception as e:
    print(f'读取Excel文件失败: {e}')
    conn.close()
    exit()

# 统计变量
new_stocks = 0
existing_stocks = 0
updated_stocks = 0
total_attributes = 0

# 处理每一行数据
for index, row in df.iterrows():
    # 显示进度
    if (index + 1) % 100 == 0:
        print(f'处理中... {index + 1}/{len(df)}')
    
    # 获取股票代码和名称
    code = str(int(row.iloc[0])).zfill(6)  # 格式化为6位数
    name = str(row.iloc[1]).strip()
    
    # 跳过无效数据
    if not code or not name:
        continue
    
    # 收集属性
    attributes = []
    for i in range(2, len(row)):
        attr = row.iloc[i]
        if pd.notna(attr):
            attr_str = str(attr).strip()
            if attr_str:
                attributes.append(attr_str)
    
    # 去重属性
    unique_attributes = list(set(attributes))
    
    # 检查数据库中是否存在相同的股票代码或名称
    cursor.execute('SELECT id FROM stocks WHERE code = ? OR name = ?', (code, name))
    existing_stock = cursor.fetchone()
    
    if existing_stock:
        # 股票已存在，合并属性
        stock_id = existing_stock[0]
        existing_stocks += 1
        
        # 获取现有属性
        cursor.execute('SELECT attribute FROM stock_attributes WHERE stock_id = ?', (stock_id,))
        existing_attrs = [row[0] for row in cursor.fetchall()]
        
        # 计算新属性
        new_attrs = [attr for attr in unique_attributes if attr not in existing_attrs]
        
        if new_attrs:
            # 添加新属性
            for attr in new_attrs:
                cursor.execute(
                    'INSERT INTO stock_attributes (stock_id, attribute, date) VALUES (?, ?, ?)',
                    (stock_id, attr, '2026-04-25')
                )
            updated_stocks += 1
            total_attributes += len(new_attrs)
    else:
        # 股票不存在，创建新记录
        cursor.execute('INSERT INTO stocks (code, name) VALUES (?, ?)', (code, name))
        stock_id = cursor.lastrowid
        new_stocks += 1
        
        # 添加属性
        for attr in unique_attributes:
            cursor.execute(
                'INSERT INTO stock_attributes (stock_id, attribute, date) VALUES (?, ?, ?)',
                (stock_id, attr, '2026-04-25')
            )
        total_attributes += len(unique_attributes)
    
    # 每处理1000行提交一次
    if (index + 1) % 1000 == 0:
        conn.commit()
        print(f'已提交 {index + 1} 行数据')

# 提交剩余数据
conn.commit()

# 统计数据库中的总股票数
cursor.execute('SELECT COUNT(*) FROM stocks')
total_stocks = cursor.fetchone()[0]

# 统计数据库中的总属性数
cursor.execute('SELECT COUNT(*) FROM stock_attributes')
total_db_attributes = cursor.fetchone()[0]

print('\n导入完成！')
print(f'新增股票: {new_stocks}')
print(f'现有股票: {existing_stocks}')
print(f'更新属性的股票: {updated_stocks}')
print(f'新增属性数量: {total_attributes}')
print(f'数据库中总股票数: {total_stocks}')
print(f'数据库中总属性数: {total_db_attributes}')

# 关闭数据库连接
conn.close()
print('数据库连接已关闭')