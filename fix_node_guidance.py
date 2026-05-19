with open('main.py', 'r', encoding='utf-8') as f:
    lines = f.readlines()

new_lines = []
for i, line in enumerate(lines):
    # 替换 WHERE 子句中的条件
    if 'WHERE ln.date = ? AND ln.node_level = ?' in line:
        new_lines.append(line.replace('WHERE ln.date = ? AND ln.node_level = ?', 'WHERE ln.date = ? AND ln.node_level IN (1, 2)'))
    # 替换参数部分
    elif '(yesterday_str, max_node_level)' in line and 'cursor.execute' in lines[i-1]:
        new_lines.append(line.replace('(yesterday_str, max_node_level)', '(yesterday_str,)'))
    # 替换变量名
    elif 'yesterday_1board_stocks' in line:
        new_lines.append(line.replace('yesterday_1board_stocks', 'yesterday_12board_stocks'))
    # 替换注释
    elif '# 检查竞价记录中是否有股票在昨天1板梯队中' in line:
        new_lines.append('# 检查竞价记录中是否有股票在昨天1板或2板梯队中\n')
    else:
        new_lines.append(line)

with open('main.py', 'w', encoding='utf-8') as f:
    f.writelines(new_lines)

print('修复成功！')
