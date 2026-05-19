import sqlite3
import os

db_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'stock_master.db')

print(f"数据库路径: {db_path}")
print(f"数据库文件是否存在: {os.path.exists(db_path)}")

if os.path.exists(db_path):
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    print("\n数据库表结构:")
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
    tables = cursor.fetchall()
    for table in tables:
        table_name = table[0]
        print(f"\n表: {table_name}")
        cursor.execute(f"PRAGMA table_info({table_name});")
        columns = cursor.fetchall()
        for column in columns:
            print(f"  {column[1]} ({column[2]})")
    
    conn.close()
else:
    print("数据库文件不存在，将重新创建")