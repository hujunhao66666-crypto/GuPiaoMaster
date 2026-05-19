import sqlite3

# 连接数据库
conn = sqlite3.connect('stock_master.db')
cursor = conn.cursor()

print('合并冠石科技的记录...')
try:
    # 查找冠石科技的记录
    cursor.execute('SELECT id, code, name FROM stocks WHERE name LIKE ?', ('%冠石科技%',))
    stocks = cursor.fetchall()
    
    if len(stocks) == 2:
        # 确定保留的记录（ID较小的）
        stock1, stock2 = sorted(stocks, key=lambda x: x[0])
        keep_id, keep_code, keep_name = stock1
        delete_id, delete_code, delete_name = stock2
        
        print(f'保留记录: ID={keep_id}, 代码={keep_code}, 名称={keep_name}')
        print(f'删除记录: ID={delete_id}, 代码={delete_code}, 名称={delete_name}')
        
        # 获取要删除的记录的属性
        cursor.execute('SELECT date, attribute FROM stock_attributes WHERE stock_id = ?', (delete_id,))
        attributes = cursor.fetchall()
        print(f'要合并的属性数量: {len(attributes)}')
        
        # 合并属性到保留的记录
        merged_count = 0
        for date, attribute in attributes:
            try:
                cursor.execute('''
                    INSERT OR IGNORE INTO stock_attributes (stock_id, date, attribute) 
                    VALUES (?, ?, ?)
                ''', (keep_id, date, attribute))
                merged_count += 1
            except Exception as e:
                print(f'合并属性时出错: {e}')
        
        print(f'成功合并 {merged_count} 个属性')
        
        # 删除多余的记录
        cursor.execute('DELETE FROM stock_attributes WHERE stock_id = ?', (delete_id,))
        cursor.execute('DELETE FROM stocks WHERE id = ?', (delete_id,))
        
        # 提交更改
        conn.commit()
        print('合并完成！')
        
        # 验证合并结果
        print('\n合并后的结果:')
        cursor.execute('SELECT id, code, name FROM stocks WHERE name LIKE ?', ('%冠石科技%',))
        result = cursor.fetchone()
        print(f'剩余记录: ID={result[0]}, 代码={result[1]}, 名称={result[2]}')
        
        cursor.execute('SELECT COUNT(*) FROM stock_attributes WHERE stock_id = ?', (result[0],))
        attr_count = cursor.fetchone()[0]
        print(f'合并后的属性数量: {attr_count}')
        
    else:
        print(f'发现 {len(stocks)} 条记录，无法合并')
        
except Exception as e:
    print(f'合并时出错: {e}')
    import traceback
    traceback.print_exc()

conn.close()