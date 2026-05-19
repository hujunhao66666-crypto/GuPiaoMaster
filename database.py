import sqlite3
import os

def init_database():
    # 确保数据库目录存在
    db_dir = os.path.dirname(os.path.abspath(__file__))
    db_path = os.path.join(db_dir, 'stock_master.db')
    
    # 连接数据库
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    # 创建股票基本信息表
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS stocks (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        code TEXT UNIQUE NOT NULL,
        name TEXT NOT NULL,
        market TEXT,
        industry TEXT,
        create_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    ''')
    
    # 创建连扳数记录表
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS board_counts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        stock_id INTEGER,
        date TEXT NOT NULL,
        board_count INTEGER NOT NULL,
        is_limit_up INTEGER DEFAULT 0,
        FOREIGN KEY (stock_id) REFERENCES stocks(id)
    )
    ''')
    
    # 创建股票属性表
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS stock_attributes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        stock_id INTEGER,
        date TEXT NOT NULL,
        attribute_name TEXT NOT NULL,
        attribute_value TEXT NOT NULL,
        FOREIGN KEY (stock_id) REFERENCES stocks(id)
    )
    ''')
    
    # 创建竞价记录表
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS bidding_records (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        date TEXT NOT NULL,
        stock_id INTEGER,
        bidding_price REAL,
        bidding_volume INTEGER,
        create_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (stock_id) REFERENCES stocks(id)
    )
    ''')
    
    # 创建涨停股票历史表
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS limit_up_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        stock_id INTEGER,
        date TEXT NOT NULL,
        close_price REAL,
        open_price REAL,
        high_price REAL,
        low_price REAL,
        volume INTEGER,
        FOREIGN KEY (stock_id) REFERENCES stocks(id)
    )
    ''')
    
    # 提交并关闭
    conn.commit()
    conn.close()
    
    return db_path

if __name__ == '__main__':
    db_path = init_database()
    print(f"数据库初始化成功，路径：{db_path}")
