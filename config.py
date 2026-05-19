# 加权系数配置文件
# 所有系数都在这里定义，可以直接修改

class Coefficients:
    # 股票权重配置
    STOCK_WEIGHT_FIRST = 1.8834000000000000      # 第一个股票的权重
    STOCK_WEIGHT_LAST = 0.6819000000000      # 最后一个股票的权重
    # 板数相关系数
    BOARD_WEIGHT = 75.01080000000        # 每板的权重（× normalized board_count 0.05~1）
    # 创业板系数
    GEM_FACTOR = 2.0              # 创业板股票系数（300开头）
    # 竞价抢筹整体系数（竞价抢筹总分 × 此系数）
    RUSHING_ATTR_WEIGHT = 0.0200000000000000     # 竞价抢筹整体系数
    # 竞价抢筹市值指数（0~1.5，<1 边际递减，=1 线性，>1 边际递增，由算法优化）
    RUSHING_MARKET_CAP_EXPONENT = 0.5000000000000000  # 竞价抢筹市值指数
    # 一字板相关系数
    YZ_OVERALL_WEIGHT = 0.1306000000000       # 一字板整体系数（归一化后每次命中约+0.5分）
    # 字母属性系数（拼音首字母，如Z、F、J、K等）
    LETTER_ATTR_WEIGHT = 2.4509000000000000      # 字母属性系数
    # 地区属性系数（如浙江、江苏、广东等）
    REGION_ATTR_WEIGHT = 0.2744000000000000      # 地区属性系数
    # 属性数量差距系数
    ATTR_COUNT_WEIGHT = 0.016700000000      # 属性数量差距系数（指数，0=无放大，1=线性）
    # 负反馈相关系数
    NEGATIVE_ATTR_COUNT_WEIGHT = 2.030200000000000000  # 负反馈属性数量差距系数（指数）
    NEGATIVE_OVERALL_WEIGHT = 196.7869000000000       # 负反馈整体系数（归一化后每次命中约-0.2分）
    # 节点相关系数
    BOARD_PRESS_WEIGHT = 118.2477000      # 同板压制系数（直接加分项）
    NODE_GUIDE_WEIGHT = 0.0336000000000      # 节点指引系数（直接加分项）
    # 股东持股比例权重系数
    HOLDER_RATIO_WEIGHT = 0.010000000000000    # 股东持股比例权重系数（× 归一化比例0~1）
    # 市值权重系数
    MARKET_CAP_WEIGHT = 0.021700000000000      # 市值权重系数（× 归一化市值^指数，市值单位为亿）
    MARKET_CAP_EXPONENT = 0.7347000000000000    # 市值指数（<1 边际递减，=1 线性，>1 边际递增）
    # 回测排名得分配置（第N名命中涨停梯队时的得分）
    BACKTEST_RANK_SCORES = {
        1: 32,   # 第1名命中得分
        2: 16,   # 第2名命中得分
        3: 8,   # 第3名命中得分
        4: 4,   # 第4名命中得分
        5: 2    # 第5名命中得分
    }

# 地区属性名称集合（用于识别地区类属性）
REGION_NAMES = {
    '北京', '上海', '天津', '重庆',  # 直辖市
    '广东', '浙江', '江苏', '山东', '福建', '安徽', '四川', '湖北', '湖南',
    '河南', '河北', '江西', '辽宁', '陕西', '山西', '广西', '云南', '贵州',
    '甘肃', '海南', '吉林', '黑龙江', '青海', '台湾',  # 省份
    '新疆', '内蒙古', '西藏', '宁夏',  # 自治区
    '深圳', '香港', '澳门',  # 特区/计划单列市
}

# 贝叶斯优化参数搜索范围
BAYESIAN_BOUNDS = {
    'stock_weight_first': (1.7834, 1.9834),  # 第一个股票的权重
    'stock_weight_last': (0.6369, 0.7269),  # 最后一个股票的权重
    'board_weight': (75.1235, 75.0358),  # 每板的权重
    'rushing_attr_weight': (0.0050, 0.1000),  # 竞价抢筹整体系数
    'rushing_market_cap_exponent': (0.1000, 1.5000),  # 竞价抢筹市值指数
    'yz_overall_weight': (0.1308, 0.1306),  # 一字板整体系数
    'letter_attr_weight': (2.4501, 2.4517),  # 字母属性系数
    'region_attr_weight': (0.2494, 0.2994),  # 地区属性系数
    'attr_count_weight': (0.0167, 0.0167),  # 属性数量差距系数
    'negative_attr_count_weight': (2.0294, 2.0309),  # 负反馈属性数量差距系数
    'negative_overall_weight': (197.0823, 196.8613),  # 负反馈整体系数
    'board_press_weight': (118.2321, 118.2633),  # 同板压制系数
    'node_guide_weight': (0.0337, 0.0336),  # 节点指引系数
    'holder_ratio_weight': (0.0100, 0.0100),  # 股东持股比例权重系数
    'market_cap_weight': (0.0100, 0.0612),  # 
    'market_cap_exponent': (0.7022, 0.7672),  # 
}

# 自适应区间收缩配置
SHRINK_FACTOR = 0.5       # 每轮收缩系数（0.5表示范围缩小一半）
SHRINK_ROUNDS = 3         # 默认收缩轮次
SHRINK_MIN_RATIO = 0.1    # 最小范围比例（相对于原始范围，避免过度收缩）

# L2正则化配置（防止单个因子权重过大导致过拟合）
L2_LAMBDA = 0.001         # 正则化强度（值越大惩罚越重）
L2_SCALE = 100.0          # 缩放因子，将L2惩罚调整到与回测得分同一量级

# 因子归一化配置（将所有因子归一化到[0,1]或[-1,1]区间，防止量级不同导致权重失衡）
NORM_YZ_SCORE = 10.0       # 一字板属性得分归一化最大值
NORM_QC_SCORE = 10.0       # 抢筹属性得分归一化最大值
NORM_FF_SCORE = 500.0      # 负反馈属性得分归一化最大值（绝对值）
NORM_BOARD_COUNT = 20.0    # 连板数归一化最大值
NORM_HOLDER_RATIO = 1.0    # 股东持股比例归一化最大值（已为分数）
NORM_BOARD_PRESS = 100.0    # 同板压制加分归一化最大值
NORM_NODE_GUIDE = 100.0     # 节点指引加分归一化最大值

# 地区属性归一化配置
NORM_REGION_SCORE = 10.0    # 地区属性得分归一化最大值

# 竞价抢筹归一化配置
NORM_RUSH_MARKET_CAP = 200.0  # 竞价抢筹市值归一化最大值（200亿对应归一化值1）
NORM_RUSH_STOCKS = 10.0       # 竞价抢筹股票数量归一化（10只对应归一化值1）

# 市值归一化配置
NORM_MARKET_CAP = 100.0     # 市值归一化最大值（100亿对应归一化值1）

# 属性名称重命名映射（将原始属性名统一为标准名称）
ATTR_RENAME_MAP = {
    '飞行汽车(eVTOL)': '低空经济',
    '机器人概念': '机器人',
    '人形机器人': '机器人',
    '江西板块': '江西',
    '算力租赁': '算力',
    '小金属概念': '小金属',
    'CPO/MPO': 'CPO',
    '房地产': '地产链',
    '光芯片': '光通信',
    '光纤概念': '光纤',
    '石油石化': '石化',
    '数字芯片': '芯片',
    '磷酸铁锂': '锂',
    '影视院线': '影视',
    '酿酒': '酿酒概念',
    'DeepSeek': 'DeepSeek概念',
    'CPU': '芯片',
    'OLED': '面板',
    '第三代半导体': '半导体',
    '金属钨': '钨',
    '金属铝': '铝',
    '金属锌': '锌',
    '机场航空': '航空',
    '玻纤': '玻璃玻纤',
    '航运': '航运港口',
}

# 需要剔除的属性集合（不保存这些属性）
ATTR_REMOVE_SET = {
    '创业板综',
    '次新股',
    '专精特新',
    '融资融券',
}

# 系数名称映射（用于显示）
COEFFICIENT_NAMES = {
    'STOCK_WEIGHT_FIRST': '第一个股票的权重',
    'STOCK_WEIGHT_LAST': '最后一个股票的权重',
    'BOARD_WEIGHT': '每板权重',
    'RUSHING_WEIGHT': '竞价抢筹权重',
    'RUSHING_ATTR_WEIGHT': '竞价抢筹整体系数',
    'RUSHING_MARKET_CAP_EXPONENT': '竞价抢筹市值指数',
    'YZ_OVERALL_WEIGHT': '一字板整体系数',
    'LETTER_ATTR_WEIGHT': '字母属性系数',
    'REGION_ATTR_WEIGHT': '地区属性系数',
    'ATTR_COUNT_WEIGHT': '属性数量差距系数',
    'NEGATIVE_ATTR_COUNT_WEIGHT': '负反馈属性数量差距系数',
    'NEGATIVE_OVERALL_WEIGHT': '负反馈整体系数',
    'BOARD_PRESS_WEIGHT': '同板压制系数',
    'NODE_GUIDE_WEIGHT': '节点指引系数',
    'HOLDER_RATIO_WEIGHT': '股东持股比例权重系数',
    'MARKET_CAP_WEIGHT': '市值权重系数',
    'MARKET_CAP_EXPONENT': '市值指数',
}

# 贝叶斯参数到系数名称的映射
BAYESIAN_TO_COEFFICIENT = {
    'stock_weight_first': 'STOCK_WEIGHT_FIRST',
    'stock_weight_last': 'STOCK_WEIGHT_LAST',
    'board_weight': 'BOARD_WEIGHT',
    'rushing_weight': 'RUSHING_WEIGHT',
    'rushing_attr_weight': 'RUSHING_ATTR_WEIGHT',
    'rushing_market_cap_exponent': 'RUSHING_MARKET_CAP_EXPONENT',
    'yz_overall_weight': 'YZ_OVERALL_WEIGHT',
    'letter_attr_weight': 'LETTER_ATTR_WEIGHT',
    'region_attr_weight': 'REGION_ATTR_WEIGHT',
    'attr_count_weight': 'ATTR_COUNT_WEIGHT',
    'negative_attr_count_weight': 'NEGATIVE_ATTR_COUNT_WEIGHT',
    'negative_overall_weight': 'NEGATIVE_OVERALL_WEIGHT',
    'board_press_weight': 'BOARD_PRESS_WEIGHT',
    'node_guide_weight': 'NODE_GUIDE_WEIGHT',
    'holder_ratio_weight': 'HOLDER_RATIO_WEIGHT',
    'market_cap_weight': 'MARKET_CAP_WEIGHT',
    'market_cap_exponent': 'MARKET_CAP_EXPONENT',
}