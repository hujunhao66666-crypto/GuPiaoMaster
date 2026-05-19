import pandas as pd
import os

# 读取Excel文件
excel_path = r'C:\Users\86453\Desktop\2025xiabangupiaodaima.xlsx'

try:
    # 读取Excel文件
    df = pd.read_excel(excel_path)
    
    print(f'Excel文件读取成功！')
    print(f'总行数: {len(df)}')
    print(f'总列数: {len(df.columns)}')
    print(f'列名: {list(df.columns)}')
    
    # 查看前10行数据
    print('\n前10行数据:')
    print(df.head(10))
    
    # 查看数据结构
    print('\n数据结构:')
    print(df.info())
    
    # 检查股票代码和名称列
    print('\n股票代码列:')
    print(df.iloc[:, 0].head(10))
    print('\n股票名称列:')
    print(df.iloc[:, 1].head(10))
    
    # 检查属性列
    print('\n属性列示例:')
    for i in range(2, min(5, len(df.columns))):
        print(f'第{i+1}列 (属性{i-1}):')
        print(df.iloc[:, i].head(10))
        print()
        
except Exception as e:
    print(f'读取Excel文件失败: {e}')