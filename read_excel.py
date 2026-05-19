import pandas as pd
import os

# Excel文件路径
excel_path = r'C:\Users\86453\Desktop\2025年上半年上市公司行业分类结果（按行业排序）.xlsx'

# 检查文件是否存在
if not os.path.exists(excel_path):
    print(f"文件不存在: {excel_path}")
else:
    print(f"文件存在，开始读取...")
    
    # 读取Excel文件
    try:
        # 跳过前2行，从第3行开始读取（索引从0开始，所以skiprows=2）
        df = pd.read_excel(excel_path, skiprows=2)
        
        print(f"成功读取Excel文件，共{len(df)}行数据")
        print("\n数据结构:")
        print(df.head())
        
        # 展示前10只股票的详细信息
        print("\n前10只股票的详细信息:")
        for i in range(min(10, len(df))):
            row = df.iloc[i]
            code = row.iloc[0]  # E列（第0列）
            name = row.iloc[1]  # F列（第1列）
            
            # 从G列开始读取属性，直到遇到空值
            attributes = []
            for j in range(2, len(row)):
                attr = row.iloc[j]
                if pd.notna(attr) and str(attr).strip():
                    attributes.append(str(attr).strip())
                else:
                    break
            
            print(f"\n股票 {i+1}:")
            print(f"  代码: {code}")
            print(f"  名称: {name}")
            print(f"  属性: {attributes}")
            print(f"  属性数量: {len(attributes)}")
            
    except Exception as e:
        print(f"读取Excel文件时出错: {e}")