import pandas as pd
import sqlite3
import os

# Excel文件路径
excel_path = r'C:\Users\86453\Desktop\2025年上半年上市公司行业分类结果（按行业排序）.xlsx'
# 数据库路径
db_path = 'stock_master.db'

# 检查文件是否存在
if not os.path.exists(excel_path):
    print(f"文件不存在: {excel_path}")
else:
    print(f"文件存在，开始导入...")
    
    # 读取Excel文件
    try:
        df = pd.read_excel(excel_path)
        print(f"成功读取Excel文件，共{len(df)}行数据")
        
        # 连接数据库
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        
        # 确保表结构存在
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS stocks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                code TEXT UNIQUE,
                name TEXT
            )
        ''')
        
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS stock_attributes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                stock_id INTEGER,
                attribute TEXT,
                FOREIGN KEY (stock_id) REFERENCES stocks(id),
                UNIQUE(stock_id, attribute)
            )
        ''')
        
        conn.commit()
        
        # 识别列位置
        code_col = 4  # E列
        name_col = 5  # F列
        attr_start_col = 6  # G列
        
        # 导入数据
        total_stocks = 0
        total_attributes = 0
        skipped_stocks = 0
        
        print("开始导入股票数据...")
        
        for i in range(len(df)):
            row = df.iloc[i]
            code = row.iloc[code_col]
            name = row.iloc[name_col]
            
            # 跳过标题行和空行
            if pd.isna(code) or not str(code).strip() or str(code) == '上市公司代码':
                continue
            
            # 处理股票代码
            code_str = str(code).strip()
            # 确保股票代码是6位数字
            if not code_str.isdigit() or len(code_str) != 6:
                skipped_stocks += 1
                continue
            
            # 处理股票名称
            name_str = str(name).strip() if pd.notna(name) else ''
            
            try:
                # 检查是否已存在相同股票代码
                cursor.execute('SELECT id, name FROM stocks WHERE code = ?', (code_str,))
                existing_by_code = cursor.fetchone()
                
                if existing_by_code:
                    # 如果代码存在但名称不同，更新名称
                    if existing_by_code[1] != name_str:
                        cursor.execute('UPDATE stocks SET name = ? WHERE code = ?', (name_str, code_str))
                        print(f"更新股票名称: {code_str} {existing_by_code[1]} -> {name_str}")
                    stock_id = existing_by_code[0]
                else:
                    # 检查是否已存在相同股票名称
                    cursor.execute('SELECT id, code FROM stocks WHERE name = ?', (name_str,))
                    existing_by_name = cursor.fetchone()
                    
                    if existing_by_name:
                        # 如果名称存在但代码不同，添加后缀以避免冲突
                        name_str = f"{name_str} ({code_str[:3]})"
                        print(f"股票名称重复，添加后缀: {name_str}")
                    
                    # 插入新股票
                    cursor.execute('INSERT INTO stocks (code, name) VALUES (?, ?)', (code_str, name_str))
                    # 获取股票ID
                    cursor.execute('SELECT id FROM stocks WHERE code = ?', (code_str,))
                    stock_id = cursor.fetchone()[0]
                
                # 读取属性
                attributes = []
                for j in range(attr_start_col, len(row)):
                    attr = row.iloc[j]
                    if pd.notna(attr) and str(attr).strip():
                        attr_str = str(attr).strip()
                        # 跳过无板块数据
                        if attr_str != '无板块数据':
                            attributes.append(attr_str)
                    else:
                        break
                
                # 插入属性
                for attr in attributes:
                    try:
                        # 获取当前日期作为属性的日期
                        import datetime
                        current_date = datetime.date.today().strftime('%Y-%m-%d')
                        
                        cursor.execute('''
                            INSERT OR IGNORE INTO stock_attributes (stock_id, date, attribute) 
                            VALUES (?, ?, ?)
                        ''', (stock_id, current_date, attr))
                        total_attributes += 1
                    except Exception as e:
                        print(f"插入属性时出错: {e}")
                
                total_stocks += 1
                
                # 每100只股票显示一次进度
                if total_stocks % 100 == 0:
                    print(f"已导入 {total_stocks} 只股票，{total_attributes} 个属性")
                
            except Exception as e:
                print(f"处理股票 {code_str} 时出错: {e}")
                skipped_stocks += 1
        
        conn.commit()
        conn.close()
        
        print("\n导入完成！")
        print(f"成功导入: {total_stocks} 只股票")
        print(f"成功导入: {total_attributes} 个属性")
        print(f"跳过: {skipped_stocks} 行数据")
        
    except Exception as e:
        print(f"导入过程中出错: {e}")
        import traceback
        traceback.print_exc()