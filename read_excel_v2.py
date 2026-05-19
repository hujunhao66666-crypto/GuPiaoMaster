import pandas as pd
import os

# Excel文件路径
excel_path = r'C:\Users\86453\Desktop\2025年上半年上市公司行业分类结果（按行业排序）.xlsx'

# 检查文件是否存在
if not os.path.exists(excel_path):
    print(f"文件不存在: {excel_path}")
else:
    print(f"文件存在，开始读取...")
    
    # 读取Excel文件，不跳过任何行
    try:
        df = pd.read_excel(excel_path)
        
        print(f"成功读取Excel文件，共{len(df)}行，{len(df.columns)}列")
        print("\n列名:")
        for i, col in enumerate(df.columns):
            print(f"  列{i}: {col}")
        
        # 尝试找到股票代码和名称列
        print("\n前20行数据预览:")
        for i in range(min(20, len(df))):
            row = df.iloc[i]
            print(f"\n行{i+1}:")
            for j, val in enumerate(row):
                if pd.notna(val) and str(val).strip():
                    print(f"  列{j}: {val}")
        
        # 尝试根据内容识别股票代码和名称列
        print("\n尝试识别股票代码和名称列...")
        
        # 遍历所有列，寻找可能的股票代码列（6位数字）
        code_col = -1
        name_col = -1
        attr_start_col = -1
        
        for j in range(len(df.columns)):
            column = df.iloc[:, j]
            # 检查是否有6位数字的股票代码
            code_count = 0
            for val in column:
                if pd.notna(val):
                    val_str = str(val)
                    if val_str.isdigit() and len(val_str) == 6:
                        code_count += 1
            
            if code_count > 10:  # 如果超过10个6位数字，认为是股票代码列
                code_col = j
                # 下一列可能是名称列
                if j + 1 < len(df.columns):
                    name_col = j + 1
                    # 再下一列开始是属性列
                    attr_start_col = j + 2
                break
        
        if code_col != -1:
            print(f"\n识别结果:")
            print(f"  股票代码列: 列{code_col} ({df.columns[code_col]})")
            print(f"  股票名称列: 列{name_col} ({df.columns[name_col]})")
            print(f"  属性开始列: 列{attr_start_col} ({df.columns[attr_start_col]})")
            
            # 展示前10只股票的详细信息
            print("\n前10只股票的详细信息:")
            stock_count = 0
            for i in range(len(df)):
                row = df.iloc[i]
                code = row.iloc[code_col]
                name = row.iloc[name_col]
                
                if pd.notna(code) and str(code).strip():
                    # 从属性开始列读取属性，直到遇到空值
                    attributes = []
                    for j in range(attr_start_col, len(row)):
                        attr = row.iloc[j]
                        if pd.notna(attr) and str(attr).strip():
                            attributes.append(str(attr).strip())
                        else:
                            break
                    
                    print(f"\n股票 {stock_count+1}:")
                    print(f"  代码: {code}")
                    print(f"  名称: {name}")
                    print(f"  属性: {attributes}")
                    print(f"  属性数量: {len(attributes)}")
                    
                    stock_count += 1
                    if stock_count >= 10:
                        break
        else:
            print("\n未识别到股票代码列，请检查Excel文件结构")
            
    except Exception as e:
        print(f"读取Excel文件时出错: {e}")