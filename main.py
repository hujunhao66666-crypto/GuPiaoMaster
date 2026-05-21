import sys
import warnings
import math
import random
warnings.filterwarnings('ignore', message='libpng warning: iCCP: known incorrect sRGB profile')
from PyQt5.QtWidgets import (QApplication, QMainWindow, QTabWidget, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QLineEdit, QTableWidget, QTableWidgetItem, QComboBox, QDateEdit, QCompleter, QTextEdit, QScrollArea, QGridLayout, QSizePolicy, QSpinBox, QFrame, QCheckBox, QFileDialog, QMessageBox, QGroupBox, QDoubleSpinBox, QDialog, QDialogButtonBox, QHeaderView)
from PyQt5.QtCore import Qt, QDate, QTimer, QThread, pyqtSignal
import sqlite3
import os
from datetime import datetime, timedelta
from pypinyin import pinyin, Style
import pandas as pd
import requests
import re
import json

# 导入配置文件
from config import Coefficients, BAYESIAN_BOUNDS, COEFFICIENT_NAMES, BAYESIAN_TO_COEFFICIENT, SHRINK_FACTOR, SHRINK_ROUNDS, SHRINK_MIN_RATIO, L2_LAMBDA, L2_SCALE, NORM_YZ_SCORE, NORM_QC_SCORE, NORM_FF_SCORE, NORM_BOARD_COUNT, NORM_HOLDER_RATIO, NORM_BOARD_PRESS, NORM_NODE_GUIDE, REGION_NAMES, NORM_REGION_SCORE, ATTR_RENAME_MAP, ATTR_REMOVE_SET, NORM_MARKET_CAP, clean_attr_name
# 尝试导入OCR库
try:
    from PIL import Image
    import pytesseract
    OCR_AVAILABLE = True
except ImportError:
    OCR_AVAILABLE = False

MAX_RUSH_ATTRS = 30  # 竞价抢筹属性每日最大条数

# 导入工具类和工作线程
from utils import FlowLayout, NumericTableItem
from workers import BayesianOptimizationThread, CorrelationAnalysisThread

class StockMasterApp(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle('炒股竞价思路大师')
        self.setGeometry(100, 100, 1200, 800)

        db_path = self.init_database()
        self.db_path = db_path
        self.is_loading_data = False
        
        # 检查是否需要修正历史数据中"科技"结尾股票的属性（只执行一次）
        self.check_and_update_keji_attributes()
        
        # 从配置文件加载加权系数
        self.STOCK_WEIGHT_FIRST = Coefficients.STOCK_WEIGHT_FIRST
        self.STOCK_WEIGHT_LAST = Coefficients.STOCK_WEIGHT_LAST
        self.BOARD_WEIGHT = Coefficients.BOARD_WEIGHT
        self.GEM_FACTOR = Coefficients.GEM_FACTOR  # 创业板系数

        self.RUSH_PCT_COEFFICIENT = Coefficients.RUSH_PCT_COEFFICIENT
        self.RUSH_LETTER_ATTR_WEIGHT = Coefficients.RUSH_LETTER_ATTR_WEIGHT
        self.RUSH_REGION_ATTR_WEIGHT = Coefficients.RUSH_REGION_ATTR_WEIGHT
        self.RUSH_MARKET_CAP_COEFFICIENT = Coefficients.RUSH_MARKET_CAP_COEFFICIENT
        self.YZ_OVERALL_WEIGHT = Coefficients.YZ_OVERALL_WEIGHT
        self.LETTER_ATTR_WEIGHT = Coefficients.LETTER_ATTR_WEIGHT
        self.REGION_ATTR_WEIGHT = Coefficients.REGION_ATTR_WEIGHT
        self.ATTR_COUNT_WEIGHT = Coefficients.ATTR_COUNT_WEIGHT
        self.NEGATIVE_ATTR_COUNT_WEIGHT = Coefficients.NEGATIVE_ATTR_COUNT_WEIGHT
        self.NEGATIVE_ATTR_WEIGHT = Coefficients.NEGATIVE_ATTR_WEIGHT
        self.NEGATIVE_LETTER_ATTR_WEIGHT = Coefficients.NEGATIVE_LETTER_ATTR_WEIGHT
        self.NEGATIVE_REGION_ATTR_WEIGHT = Coefficients.NEGATIVE_REGION_ATTR_WEIGHT
        self.BOARD_PRESS_WEIGHT = Coefficients.BOARD_PRESS_WEIGHT
        self.NODE_GUIDE_WEIGHT = Coefficients.NODE_GUIDE_WEIGHT
        self.HOLDER_RATIO_WEIGHT = Coefficients.HOLDER_RATIO_WEIGHT
        self.MARKET_CAP_WEIGHT = Coefficients.MARKET_CAP_WEIGHT
        self.MARKET_CAP_EXPONENT = Coefficients.MARKET_CAP_EXPONENT
        self.BACKTEST_RANK_SCORES = Coefficients.BACKTEST_RANK_SCORES  # 回测排名得分配置

        # 因子治理状态
        self._gov_dynamic_active = False
        self._factor_enabled = {}  # param_key -> bool
        self._load_factor_governance()

        # 从数据库加载 hexin-v
        self.hexin_v = 'A9EiP0gFX-ulsrP_2NuA_x-U4NZuPkCv77TpxLM7z7-3AP8I-45VgH8C-Q5A'
        self._load_hexin_v()

        # 保存得分详情数据
        self.score_details = {}
        
        # 贝叶斯优化线程和进度追踪
        self.bayesian_thread = None
        self.bayesian_progress = {'stage': '', 'pct': 0, 'logs': []}
        self.bayesian_running = False

        self.central_widget = QWidget()
        self.setCentralWidget(self.central_widget)
        self.main_layout = QVBoxLayout(self.central_widget)

        self.tab_widget = QTabWidget()
        self.tab_widget.currentChanged.connect(self.on_tab_changed)
        self.main_layout.addWidget(self.tab_widget)

        self.stock_input_tab = QWidget()
        self.limit_up_tab = QWidget()
        self.bidding_analysis_tab = QWidget()
        self.attribute_management_tab = QWidget()
        self.data_backtest_tab = QWidget()

        self.tab_widget.addTab(self.limit_up_tab, '涨停梯队')
        self.tab_widget.addTab(self.stock_input_tab, '股票列表')
        self.tab_widget.addTab(self.attribute_management_tab, '属性管理')
        self.tab_widget.addTab(self.data_backtest_tab, '数据回测')
        self.tab_widget.addTab(self.bidding_analysis_tab, '竞价分析')

        self.init_stock_input_tab()
        self.init_limit_up_tab()
        self.init_bidding_analysis_tab()
        self.init_attribute_management_tab()
        self.init_data_backtest_tab()

        self.add_stock_btn.clicked.connect(self.add_stock)
        self.import_excel_btn.clicked.connect(self.import_stocks_from_excel)
        self.add_attr_btn.clicked.connect(lambda: self.show_add_attribute_dialog(None))
        self.analyze_btn.clicked.connect(self.analyze_bidding)
        self.limit_up_date.dateChanged.connect(self.on_date_changed)

    def on_tab_changed(self, index):
        if index == 1:  # 涨停梯队标签页
            self.load_limit_up_data()

    def on_date_changed(self, date):
        self.load_limit_up_data()

    def on_prev_day_clicked(self):
        current_date = self.limit_up_date.date()
        prev_date = current_date.addDays(-1)
        self.limit_up_date.setDate(prev_date)

    def on_next_day_clicked(self):
        current_date = self.limit_up_date.date()
        today = QDate.currentDate()
        # 不能超过今天
        if current_date < today:
            next_date = current_date.addDays(1)
            self.limit_up_date.setDate(next_date)

    def on_today_clicked(self):
        today = QDate.currentDate()
        self.limit_up_date.setDate(today)

    def init_database(self):
        db_dir = os.path.dirname(os.path.abspath(__file__))
        db_path = os.path.join(db_dir, 'stock_master.db')

        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()

        cursor.execute('''
        CREATE TABLE IF NOT EXISTS stocks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            code TEXT UNIQUE NOT NULL,
            name TEXT NOT NULL,
            create_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        ''')
        
        cursor.execute("PRAGMA table_info(stocks)")
        columns = [row[1] for row in cursor.fetchall()]
        if 'holder_ratio' not in columns:
            cursor.execute('ALTER TABLE stocks ADD COLUMN holder_ratio REAL DEFAULT NULL')
        if 'total_market_cap' not in columns:
            cursor.execute('ALTER TABLE stocks ADD COLUMN total_market_cap REAL DEFAULT NULL')

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

        cursor.execute('''
        CREATE TABLE IF NOT EXISTS stock_attributes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            stock_id INTEGER,
            date TEXT NOT NULL,
            attribute TEXT NOT NULL,
            FOREIGN KEY (stock_id) REFERENCES stocks(id)
        )
        ''')

        cursor.execute('''
        CREATE TABLE IF NOT EXISTS bidding_records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL,
            stock_id INTEGER,
            create_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (stock_id) REFERENCES stocks(id)
        )
        ''')
        
        # 检查并添加is_additive字段（如果不存在）
        cursor.execute("PRAGMA table_info(bidding_records)")
        columns = [row[1] for row in cursor.fetchall()]
        if 'is_additive' not in columns:
            cursor.execute('ALTER TABLE bidding_records ADD COLUMN is_additive INTEGER DEFAULT 1')

        # 检查并添加type字段（如果不存在）
        if 'type' not in columns:
            cursor.execute('ALTER TABLE bidding_records ADD COLUMN type INTEGER DEFAULT 1')  # 1: 竞价一字板, 2: 竞价抢筹, 3: 竞价负反馈

        cursor.execute('''
        CREATE TABLE IF NOT EXISTS bidding_rush_attrs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL,
            attribute TEXT NOT NULL,
            intensity REAL NOT NULL DEFAULT 1.0,
            create_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(date, attribute)
        )
        ''')

        cursor.execute('''
        CREATE TABLE IF NOT EXISTS bidding_rush_stocks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL,
            stock_id INTEGER NOT NULL,
            pct_change REAL NOT NULL DEFAULT 0.0,
            create_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (stock_id) REFERENCES stocks(id)
        )
        ''')

        # 迁移：清空旧版竞价抢筹属性数据（改为股票维度）
        cursor.execute('SELECT COUNT(*) FROM bidding_rush_attrs')
        old_count = cursor.fetchone()[0]
        if old_count > 0:
            cursor.execute('DELETE FROM bidding_rush_attrs')
            print(f"Migration: cleared {old_count} old rush attr records from bidding_rush_attrs")

        cursor.execute('''
        CREATE TABLE IF NOT EXISTS stock_ratings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            stock_id INTEGER,
            rating INTEGER NOT NULL, -- 1: 好评, -1: 差评
            create_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (stock_id) REFERENCES stocks(id)
        )
        ''')

        cursor.execute('''
        CREATE UNIQUE INDEX IF NOT EXISTS idx_stock_rating ON stock_ratings(stock_id)
        ''')

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

        cursor.execute('''
        CREATE TABLE IF NOT EXISTS ladder_nodes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL,
            node_level INTEGER NOT NULL,
            node_name TEXT,
            create_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(date, node_level)
        )
        ''')

        cursor.execute('''
        CREATE TABLE IF NOT EXISTS ladder_stocks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ladder_node_id INTEGER,
            stock_id INTEGER,
            order_index INTEGER,
            FOREIGN KEY (ladder_node_id) REFERENCES ladder_nodes(id),
            FOREIGN KEY (stock_id) REFERENCES stocks(id)
        )
        ''')

        cursor.execute('''
        CREATE TABLE IF NOT EXISTS ladder_settings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL,
            ladder_count INTEGER NOT NULL,
            UNIQUE(date)
        )
        ''')

        cursor.execute('''
        CREATE TABLE IF NOT EXISTS bayesian_results (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL,
            train_score REAL NOT NULL,
            valid_score REAL NOT NULL,
            params_json TEXT NOT NULL,
            train_start TEXT,
            train_end TEXT,
            valid_start TEXT,
            valid_end TEXT,
            create_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        ''')

        for col in ('train_start', 'train_end', 'valid_start', 'valid_end'):
            try:
                cursor.execute(f"ALTER TABLE bayesian_results ADD COLUMN {col} TEXT")
            except:
                pass

        cursor.execute('''
        CREATE TABLE IF NOT EXISTS backtest_results (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            test_date TEXT NOT NULL,
            start_date TEXT NOT NULL,
            end_date TEXT NOT NULL,
            total_score REAL NOT NULL,
            valid_days INTEGER NOT NULL,
            total_valid_stocks INTEGER NOT NULL,
            top_n INTEGER NOT NULL,
            valid_score REAL NOT NULL,
            stock_weight_first REAL NOT NULL,
            stock_weight_last REAL NOT NULL,
            board_weight REAL NOT NULL,

            rush_pct_coefficient REAL NOT NULL DEFAULT 0.2,
            yz_overall_weight REAL NOT NULL DEFAULT 1.0,
            attr_count_weight REAL NOT NULL,
            negative_attr_count_weight REAL NOT NULL,
            board_press_weight REAL NOT NULL DEFAULT 0.5,
            negative_attr_weight REAL NOT NULL DEFAULT 1.0,
            negative_letter_attr_weight REAL NOT NULL DEFAULT 1.0,
            negative_region_attr_weight REAL NOT NULL DEFAULT 1.0,
            node_guide_weight REAL NOT NULL DEFAULT 1.0,
            detail_text TEXT,
            create_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        ''')

        # 为现有表添加缺失的列
        try:
            # 检查是否存在board_press_weight列
            cursor.execute("PRAGMA table_info(backtest_results)")
            columns = [row[1] for row in cursor.fetchall()]
            
            if 'board_press_weight' not in columns:
                cursor.execute('ALTER TABLE backtest_results ADD COLUMN board_press_weight REAL NOT NULL DEFAULT 0.5')
            
            if 'negative_attr_weight' not in columns:
                cursor.execute('ALTER TABLE backtest_results ADD COLUMN negative_attr_weight REAL NOT NULL DEFAULT 1.0')

            if 'negative_letter_attr_weight' not in columns:
                cursor.execute('ALTER TABLE backtest_results ADD COLUMN negative_letter_attr_weight REAL NOT NULL DEFAULT 1.0')

            if 'negative_region_attr_weight' not in columns:
                cursor.execute('ALTER TABLE backtest_results ADD COLUMN negative_region_attr_weight REAL NOT NULL DEFAULT 1.0')

            if 'yz_overall_weight' not in columns:
                cursor.execute('ALTER TABLE backtest_results ADD COLUMN yz_overall_weight REAL NOT NULL DEFAULT 1.0')
            
            if 'node_guide_weight' not in columns:
                cursor.execute('ALTER TABLE backtest_results ADD COLUMN node_guide_weight REAL NOT NULL DEFAULT 1.0')
            
            if 'holder_ratio_weight' not in columns:
                cursor.execute('ALTER TABLE backtest_results ADD COLUMN holder_ratio_weight REAL NOT NULL DEFAULT 0.1')

            if 'rush_letter_attr_weight' not in columns:
                cursor.execute('ALTER TABLE backtest_results ADD COLUMN rush_letter_attr_weight REAL NOT NULL DEFAULT 0.02')

            if 'rush_region_attr_weight' not in columns:
                cursor.execute('ALTER TABLE backtest_results ADD COLUMN rush_region_attr_weight REAL NOT NULL DEFAULT 0.02')

            if 'rush_market_cap_coefficient' not in columns:
                cursor.execute('ALTER TABLE backtest_results ADD COLUMN rush_market_cap_coefficient REAL NOT NULL DEFAULT 0.01')

            if 'rush_pct_coefficient' not in columns:
                cursor.execute('ALTER TABLE backtest_results ADD COLUMN rush_pct_coefficient REAL NOT NULL DEFAULT 0.2')

        except Exception as e:
            print(f"Error altering table: {e}")

        cursor.execute('''
        CREATE TABLE IF NOT EXISTS correlation_results (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            param_key TEXT UNIQUE NOT NULL,
            judgment TEXT NOT NULL,
            r REAL DEFAULT 0.0,
            abs_r REAL DEFAULT 0.0,
            mi REAL DEFAULT 0.0,
            slope REAL DEFAULT 0.0,
            r_squared REAL DEFAULT 0.0,
            create_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        ''')

        # 预置用户给定的参数属性判定结果（仅当表为空时插入）
        cursor.execute('SELECT COUNT(*) FROM correlation_results')
        if cursor.fetchone()[0] == 0:
            seed_judgments = {
                'stock_weight_first': '非线性相关',
                'stock_weight_last': '弱相关',
                'board_weight': '非线性相关',
                'rush_attr_coefficient': '非线性相关',
                'yz_overall_weight': '非线性相关',
                'letter_attr_weight': '非线性相关',
                'region_attr_weight': '非线性相关',
                'attr_count_weight': '非线性相关',
                'negative_attr_count_weight': '非线性相关',
                'negative_attr_weight': '弱相关',
                'negative_letter_attr_weight': '弱相关',
                'negative_region_attr_weight': '弱相关',
                'board_press_weight': '非线性相关',
                'node_guide_weight': '非线性相关',
                'holder_ratio_weight': '非线性相关',
                'market_cap_weight': '弱线性+非线性',
                'market_cap_exponent': '线性相关',
            }
            for param_key, judgment in seed_judgments.items():
                try:
                    cursor.execute(
                        'INSERT INTO correlation_results (param_key, judgment) VALUES (?, ?)',
                        (param_key, judgment)
                    )
                except sqlite3.IntegrityError:
                    pass
            print(f"[初始化] 已写入{len(seed_judgments)}个参数属性判定预置结果")

        cursor.execute('''
        CREATE TABLE IF NOT EXISTS factor_governance (
            param_key TEXT UNIQUE NOT NULL,
            enabled INTEGER NOT NULL DEFAULT 1,
            weight_scale REAL NOT NULL DEFAULT 1.0,
            updated_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        ''')

        cursor.execute('''
        CREATE TABLE IF NOT EXISTS app_settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
        ''')

        conn.commit()
        
        # 为历史股票数据添加拼音首字母属性（使用get_pinyin_initials方法自动去除"股份"）
        # 以"科技"结尾的股票不添加K、J属性，而是添加"科技"属性
        try:
            cursor.execute('SELECT id, name FROM stocks')
            stocks = cursor.fetchall()

            for stock_id, name in stocks:
                # 获取拼音首字母（以科技结尾的股票跳过K、J属性）
                initials = self.get_pinyin_initials(name, skip_keji=True)

                # 去重
                unique_initials = list(set(initials))
                
                # 检查该股票是否已经有拼音首字母属性
                cursor.execute('SELECT attribute FROM stock_attributes WHERE stock_id = ?', (stock_id,))
                existing_attrs = set([row[0] for row in cursor.fetchall()])
                
                # 只添加不存在的属性
                for initial in unique_initials:
                    if initial not in existing_attrs:
                        cursor.execute('INSERT INTO stock_attributes (stock_id, date, attribute) VALUES (?, ?, ?)',
                                      (stock_id, QDate.currentDate().toString('yyyy-MM-dd'), initial))
                
                # 如果股票名称以"科技"结尾，添加"科技"属性
                if name.endswith('科技') and '科技' not in existing_attrs:
                    cursor.execute('INSERT INTO stock_attributes (stock_id, date, attribute) VALUES (?, ?, ?)',
                                  (stock_id, QDate.currentDate().toString('yyyy-MM-dd'), '科技'))
            
            conn.commit()
        except Exception as e:
            print(f"Error adding pinyin attributes to existing stocks: {e}")
        
        # 清理重复的股票属性
        try:
            # 先查询重复记录的数量
            cursor.execute('''
                SELECT COUNT(*) - COUNT(DISTINCT id)
                FROM stock_attributes
            ''')
            total_count = cursor.fetchone()[0]
            
            cursor.execute('''
                SELECT COUNT(*) 
                FROM stock_attributes 
                WHERE id NOT IN (
                    SELECT MIN(id) 
                    FROM stock_attributes 
                    GROUP BY stock_id, attribute
                )
            ''')
            duplicate_count = cursor.fetchone()[0]
            
            if duplicate_count > 0:
                cursor.execute('''
                    DELETE FROM stock_attributes 
                    WHERE id NOT IN (
                        SELECT MIN(id) 
                        FROM stock_attributes 
                        GROUP BY stock_id, attribute
                    )
                ''')
                print(f"已清理 {duplicate_count} 条重复的股票属性记录")
                conn.commit()
        except Exception as e:
            print(f"Error cleaning duplicate attributes: {e}")
        finally:
            conn.close()

        return db_path

    def _trim_all_rush_attrs(self, db_path=None):
        conn = sqlite3.connect(db_path or self.db_path)
        cursor = conn.cursor()
        try:
            cursor.execute('SELECT DISTINCT date FROM bidding_rush_stocks')
            dates = [row[0] for row in cursor.fetchall()]
            total_deleted = 0
            for date in dates:
                cursor.execute('SELECT COUNT(*) FROM bidding_rush_stocks WHERE date = ?', (date,))
                count = cursor.fetchone()[0]
                if count > MAX_RUSH_ATTRS:
                    cursor.execute('''
                        DELETE FROM bidding_rush_stocks
                        WHERE date = ? AND id NOT IN (
                            SELECT id FROM bidding_rush_stocks
                            WHERE date = ?
                            ORDER BY pct_change DESC
                            LIMIT ?
                        )
                    ''', (date, date, MAX_RUSH_ATTRS))
                    deleted = cursor.rowcount
                    total_deleted += deleted
                    print(f"Trimmed {date}: removed {deleted} records (kept top {MAX_RUSH_ATTRS})")
            conn.commit()
            if total_deleted > 0:
                print(f"Trim complete: removed {total_deleted} records total across {len(dates)} dates")
        finally:
            conn.close()

    def init_stock_input_tab(self):
        layout = QVBoxLayout(self.stock_input_tab)

        stock_info_layout = QHBoxLayout()
        stock_info_layout.addWidget(QLabel('股票代码:'))
        self.code_input = QLineEdit()
        stock_info_layout.addWidget(self.code_input)

        stock_info_layout.addWidget(QLabel('股票名称:'))
        self.name_input = QLineEdit()
        stock_info_layout.addWidget(self.name_input)

        self.add_stock_btn = QPushButton('添加股票')
        self.add_stock_btn.setDefault(True)  # 设置为默认按钮，按下回车时触发
        self.add_stock_btn.setFocusPolicy(Qt.StrongFocus)  # 确保按钮可以获得焦点
        stock_info_layout.addWidget(self.add_stock_btn)

        self.add_stock_auto_btn = QPushButton('添加股票auto')
        self.add_stock_auto_btn.setStyleSheet('''
            QPushButton {
                background-color: #FF9800;
                color: white;
                border: none;
                padding: 5px 15px;
                border-radius: 3px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #F57C00;
            }
        ''')
        self.add_stock_auto_btn.clicked.connect(self.add_stock_auto)
        stock_info_layout.addWidget(self.add_stock_auto_btn)

        self.import_excel_btn = QPushButton('导入Excel')
        stock_info_layout.addWidget(self.import_excel_btn)

        self.get_holder_ratio_btn = QPushButton('获取股票股本')
        self.get_holder_ratio_btn.clicked.connect(self.get_holder_ratio_data)
        stock_info_layout.addWidget(self.get_holder_ratio_btn)

        self.get_market_cap_btn = QPushButton('获取股票总市值')
        self.get_market_cap_btn.clicked.connect(self.get_total_market_cap_data)
        stock_info_layout.addWidget(self.get_market_cap_btn)

        self.refresh_attr_btn = QPushButton('刷新属性')
        self.refresh_attr_btn.setStyleSheet('''
            QPushButton {
                background-color: #17a2b8; color: white; font-weight: bold;
                border: none; border-radius: 3px; padding: 5px 15px;
            }
            QPushButton:hover { background-color: #138496; }
        ''')
        self.refresh_attr_btn.clicked.connect(self.refresh_stock_attributes)
        stock_info_layout.addWidget(self.refresh_attr_btn)

        self.delete_stock_btn = QPushButton('删除股票')
        self.delete_stock_btn.setStyleSheet('''
            QPushButton {
                background-color: #dc3545; color: white; font-weight: bold;
                border: none; border-radius: 3px; padding: 5px 15px;
            }
            QPushButton:hover { background-color: #c82333; }
        ''')
        self.delete_stock_btn.clicked.connect(self.delete_stocks)
        stock_info_layout.addWidget(self.delete_stock_btn)

        layout.addLayout(stock_info_layout)

        self.add_attr_btn = QPushButton('添加属性')
        layout.addWidget(self.add_attr_btn)

        filter_layout = QHBoxLayout()
        filter_layout.addWidget(QLabel('筛选:'))
        self.filter_input = QLineEdit()
        self.filter_input.setPlaceholderText('输入股票代码、名称或属性进行筛选')
        self.filter_input.returnPressed.connect(self.search_stocks)  # 回车搜索
        filter_layout.addWidget(self.filter_input)

        self.search_btn = QPushButton('搜索')
        self.search_btn.clicked.connect(self.search_stocks)
        filter_layout.addWidget(self.search_btn)

        self.reset_btn = QPushButton('重置')
        self.reset_btn.clicked.connect(self.reset_search)
        filter_layout.addWidget(self.reset_btn)

        # 属性筛选下拉框
        filter_layout.addWidget(QLabel('属性筛选:'))
        
        # 创建属性筛选组件
        self.attr_filter_frame = QWidget()
        attr_filter_layout = QVBoxLayout(self.attr_filter_frame)
        attr_filter_layout.setContentsMargins(0, 0, 0, 0)
        attr_filter_layout.setSpacing(2)
        
        # 下拉按钮
        self.attr_filter_btn = QPushButton('选择属性')
        self.attr_filter_btn.setMaximumWidth(200)
        attr_filter_layout.addWidget(self.attr_filter_btn)
        
        # 选中的属性标签
        self.selected_attrs_layout = QHBoxLayout()
        self.selected_attrs_layout.setContentsMargins(0, 0, 0, 0)
        self.selected_attrs_layout.setSpacing(4)
        attr_filter_layout.addLayout(self.selected_attrs_layout)
        
        # 涨停筛选
        self.limit_up_filter = QCheckBox('只显示昨天涨停的股票')
        self.limit_up_filter.stateChanged.connect(self.load_stocks)
        attr_filter_layout.addWidget(self.limit_up_filter)
        
        # 评价筛选
        filter_layout.addWidget(QLabel('评价筛选:'))
        self.rating_filter = QComboBox()
        self.rating_filter.addItems(['全部', '好评', '差评'])
        self.rating_filter.currentIndexChanged.connect(self.load_stocks)
        filter_layout.addWidget(self.rating_filter)
        
        filter_layout.addWidget(self.attr_filter_frame)
        
        # 连接按钮点击事件
        self.attr_filter_btn.clicked.connect(self.show_attr_filter_dialog)

        layout.addLayout(filter_layout)

        self.stock_table = QTableWidget()
        self.stock_table.setColumnCount(7)
        self.stock_table.setHorizontalHeaderLabels(['ID', '代码', '名称', '昨天是否涨停', '昨天的连扳数', '股东持股比例', '总市值(亿)'])
        # 设置表格为可编辑模式
        self.stock_table.setEditTriggers(QTableWidget.DoubleClicked | QTableWidget.EditKeyPressed)
        # 连接编辑完成信号
        self.stock_table.itemChanged.connect(self.on_stock_item_changed)
        layout.addWidget(self.stock_table)

        attr_label = QLabel('属性展示:')
        layout.addWidget(attr_label)

        self.attr_scroll = QScrollArea()
        self.attr_scroll.setWidgetResizable(True)
        self.attr_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.attr_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOn)

        self.attr_widget = QWidget()
        self.attr_flow_layout = FlowLayout(self.attr_widget)
        self.attr_widget.setMinimumHeight(100)
        self.attr_widget.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Minimum)
        self.attr_scroll.setWidget(self.attr_widget)

        layout.addWidget(self.attr_scroll)

        # 快速添加属性功能
        quick_attr_layout = QHBoxLayout()
        quick_attr_layout.addWidget(QLabel('快速添加属性:'))
        self.quick_attr_input = QLineEdit()
        self.quick_attr_input.setPlaceholderText('输入属性，支持多种分隔符')
        self.quick_attr_input.returnPressed.connect(self.quick_add_attribute)  # 回车快速添加
        quick_attr_layout.addWidget(self.quick_attr_input)
        
        self.quick_add_attr_btn = QPushButton('添加')
        self.quick_add_attr_btn.clicked.connect(self.quick_add_attribute)
        quick_attr_layout.addWidget(self.quick_add_attr_btn)
        
        layout.addLayout(quick_attr_layout)

        # 评价功能
        rating_layout = QHBoxLayout()
        rating_layout.addWidget(QLabel('评价:'))
        
        self.good_rating_btn = QPushButton('好评')
        self.good_rating_btn.setStyleSheet('''
            background-color: #E8F5E9;
            border: 1px solid #4CAF50;
            border-radius: 3px;
            padding: 5px 10px;
        ''')
        self.good_rating_btn.clicked.connect(lambda: self.rate_stock(1))
        rating_layout.addWidget(self.good_rating_btn)
        
        self.bad_rating_btn = QPushButton('差评')
        self.bad_rating_btn.setStyleSheet('''
            background-color: #FFEBEE;
            border: 1px solid #F44336;
            border-radius: 3px;
            padding: 5px 10px;
        ''')
        self.bad_rating_btn.clicked.connect(lambda: self.rate_stock(-1))
        rating_layout.addWidget(self.bad_rating_btn)
        
        self.clear_rating_btn = QPushButton('清除评价')
        self.clear_rating_btn.setStyleSheet('''
            background-color: #f5f5f5;
            border: 1px solid #999;
            border-radius: 3px;
            padding: 5px 10px;
        ''')
        self.clear_rating_btn.clicked.connect(self.clear_stock_rating)
        rating_layout.addWidget(self.clear_rating_btn)
        
        layout.addLayout(rating_layout)
        
        # 分页控件
        pagination_layout = QHBoxLayout()
        pagination_layout.addStretch()
        
        self.page_size_combo = QComboBox()
        self.page_size_combo.addItems(['20', '50', '100', '200'])
        self.page_size_combo.setCurrentText('50')
        self.page_size_combo.currentTextChanged.connect(self.load_stocks)
        pagination_layout.addWidget(QLabel('每页:'))
        pagination_layout.addWidget(self.page_size_combo)
        
        self.page_label = QLabel('第 1 页，共 1 页')
        pagination_layout.addWidget(self.page_label)
        
        self.prev_page_btn = QPushButton('上一页')
        self.prev_page_btn.clicked.connect(lambda: self.change_page(-1))
        pagination_layout.addWidget(self.prev_page_btn)
        
        self.next_page_btn = QPushButton('下一页')
        self.next_page_btn.clicked.connect(lambda: self.change_page(1))
        pagination_layout.addWidget(self.next_page_btn)
        
        self.last_page_btn = QPushButton('最后一页')
        self.last_page_btn.clicked.connect(self.go_to_last_page)
        pagination_layout.addWidget(self.last_page_btn)
        
        pagination_layout.addStretch()
        layout.addLayout(pagination_layout)

        # 分页相关变量
        self.current_page = 1
        self.total_pages = 1
        self.total_stocks = 0

        self.stock_table.itemSelectionChanged.connect(self.on_stock_selection_changed)

        self.load_stocks('')

    def on_stock_selection_changed(self):
        self.attr_flow_layout.clear()

        selected_row = self.stock_table.currentRow()
        if selected_row < 0:
            return

        stock_id = int(self.stock_table.item(selected_row, 0).text())

        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        cursor.execute('SELECT attribute FROM stock_attributes WHERE stock_id = ?', (stock_id,))
        attributes = cursor.fetchall()

        for (attr,) in attributes:
            attr_widget = QWidget()
            attr_layout = QHBoxLayout(attr_widget)
            attr_layout.setContentsMargins(2, 2, 2, 2)
            attr_layout.setSpacing(2)

            attr_label = QLabel(attr)
            attr_label.setStyleSheet('''
                background-color: #E8F5E9;
                border: 1px solid #4CAF50;
                border-radius: 3px;
                padding: 5px;
                margin: 0;
            ''')
            attr_label.setSizePolicy(QSizePolicy.Minimum, QSizePolicy.Minimum)
            attr_layout.addWidget(attr_label)

            delete_btn = QPushButton('×')
            delete_btn.setStyleSheet('''
                background-color: #FFEBEE;
                border: 1px solid #F44336;
                border-radius: 3px;
                padding: 0 5px;
                font-weight: bold;
                color: #D32F2F;
            ''')
            delete_btn.setFixedSize(20, 20)
            delete_btn.clicked.connect(lambda checked, sid=stock_id, a=attr: self.delete_attribute(sid, a))
            attr_layout.addWidget(delete_btn)

            self.attr_flow_layout.addWidget(attr_widget)

        self.attr_widget.updateGeometry()
        self.attr_scroll.update()

    def quick_add_attribute(self):
        selected_row = self.stock_table.currentRow()
        if selected_row < 0:
            from PyQt5.QtWidgets import QMessageBox
            QMessageBox.warning(
                self,
                '提示',
                '请先选择一只股票！'
            )
            return

        stock_id = int(self.stock_table.item(selected_row, 0).text())
        attr_text = self.quick_attr_input.text().strip()

        if not attr_text:
            return

        # 处理多种分隔符
        attr_text = attr_text.replace('，', ',').replace(' ', ',').replace('、', ',').replace('。', ',')
        attributes = [attr.strip() for attr in attr_text.split(',') if attr.strip()]
        attributes = list(dict.fromkeys(attributes))  # 去重

        if not attributes:
            return

        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        date = QDate.currentDate().toString('yyyy-MM-dd')

        # 获取现有属性
        cursor.execute('SELECT attribute FROM stock_attributes WHERE stock_id = ? AND date = ?', (stock_id, date))
        existing_attrs = set([row[0] for row in cursor.fetchall()])

        # 添加新属性
        for attr in attributes:
            cleaned = clean_attr_name(attr)
            if cleaned is None:
                continue
            if cleaned not in existing_attrs:
                cursor.execute('INSERT INTO stock_attributes (stock_id, date, attribute) VALUES (?, ?, ?)', (stock_id, date, cleaned))
                existing_attrs.add(cleaned)

        conn.commit()
        conn.close()

        # 清空输入框
        self.quick_attr_input.clear()

        # 重新显示属性
        self.on_stock_selection_changed()

    def import_stocks_from_excel(self):
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            '选择Excel文件',
            '',
            'Excel Files (*.xlsx *.xls)'
        )

        if not file_path:
            return

        try:
            # 读取Excel文件
            df = pd.read_excel(file_path, header=None)
            
            # 检查数据格式
            if df.shape[1] < 2:
                from PyQt5.QtWidgets import QMessageBox
                QMessageBox.warning(self, '数据格式错误', 'Excel文件至少需要包含股票代码和股票名称两列！')
                return

            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()

            success_count = 0
            existing_count = 0
            error_count = 0
            existing_stocks = []
            error_records = []

            for idx, row in df.iterrows():
                try:
                    # 第一列：股票代码
                    code = str(row[0]).strip()
                    # 第二列：股票名称
                    name = str(row[1]).strip()
                    # 第三列：股票属性
                    attributes = str(row[2]) if len(row) > 2 else ''

                    if not code or not name:
                        error_count += 1
                        error_records.append({
                            'code': code,
                            'name': name,
                            'reason': '股票代码或名称为空'
                        })
                        continue

                    # 处理股票代码：去除可能的非数字字符，然后补零到6位
                    code = ''.join(filter(str.isdigit, code))
                    if not code:
                        error_count += 1
                        error_records.append({
                            'code': code,
                            'name': name,
                            'reason': '股票代码必须是数字'
                        })
                        continue
                    
                    # 补零到6位
                    code = code.zfill(6)

                    # 验证股票代码必须是6位数字
                    if not code.isdigit() or len(code) != 6:
                        error_count += 1
                        error_records.append({
                            'code': code,
                            'name': name,
                            'reason': '股票代码必须是6位数字'
                        })
                        continue

                    # 检查股票是否已存在
                    cursor.execute('SELECT id, name FROM stocks WHERE code = ?', (code,))
                    existing_by_code = cursor.fetchone()
                    
                    cursor.execute('SELECT id, code FROM stocks WHERE name = ?', (name,))
                    existing_by_name = cursor.fetchone()

                    if existing_by_code or existing_by_name:
                        existing_count += 1
                        existing_stocks.append(f"代码: {code}, 名称: {name}")
                        # 如果股票已存在，只添加属性
                        if existing_by_code:
                            stock_id = existing_by_code[0]
                        else:
                            stock_id = existing_by_name[0]
                    else:
                        # 添加新股票
                        cursor.execute('INSERT INTO stocks (code, name) VALUES (?, ?)', (code, name))
                        conn.commit()
                        cursor.execute('SELECT id FROM stocks WHERE code = ?', (code,))
                        stock_id = cursor.fetchone()[0]
                        success_count += 1

                        # 为新股票添加拼音首字母属性（使用get_pinyin_initials方法自动去除"股份"）
                        initials = self.get_pinyin_initials(name)
                        unique_initials = list(set(initials))
                        
                        # 检查该股票是否已经有拼音首字母属性
                        cursor.execute('SELECT attribute FROM stock_attributes WHERE stock_id = ?', (stock_id,))
                        existing_attrs = set([row[0] for row in cursor.fetchall()])
                        
                        # 只添加不存在的属性
                        for initial in unique_initials:
                            if initial not in existing_attrs:
                                cursor.execute('INSERT INTO stock_attributes (stock_id, date, attribute) VALUES (?, ?, ?)',
                                              (stock_id, QDate.currentDate().toString('yyyy-MM-dd'), initial))

                    # 处理属性（支持多种分隔符）
                    if attributes:
                        attr_text = attributes.replace('，', ',').replace(' ', ',').replace('、', ',').replace('。', ',')
                        attr_list = [attr.strip() for attr in attr_text.split(',') if attr.strip()]
                        attr_list = list(dict.fromkeys(attr_list))  # 去重
                        for attr in attr_list:
                            cursor.execute('INSERT OR IGNORE INTO stock_attributes (stock_id, date, attribute) VALUES (?, ?, ?)',
                                          (stock_id, QDate.currentDate().toString('yyyy-MM-dd'), attr))

                except Exception as e:
                    print(f"Error processing row {idx + 2}: {e}")
                    error_count += 1
                    try:
                        code = str(row[0]).strip() if len(row) > 0 else ''
                        name = str(row[1]).strip() if len(row) > 1 else ''
                        error_records.append({
                            'code': code,
                            'name': name,
                            'reason': f'处理错误: {str(e)}'
                        })
                    except:
                        error_records.append({
                            'code': '',
                            'name': '',
                            'reason': f'处理错误: {str(e)}'
                        })

            conn.commit()
            conn.close()

            # 显示导入结果
            from PyQt5.QtWidgets import QDialog, QVBoxLayout, QLabel, QTableWidget, QTableWidgetItem, QScrollArea, QHBoxLayout, QPushButton
            
            dialog = QDialog(self)
            dialog.setWindowTitle('导入结果')
            dialog.resize(800, 600)
            
            # 居中显示
            if self.isVisible():
                dialog.move(
                    self.x() + (self.width() - dialog.width()) // 2,
                    self.y() + (self.height() - dialog.height()) // 2
                )
            
            layout = QVBoxLayout(dialog)
            
            # 结果摘要
            summary_label = QLabel()
            result_message = f"导入完成！\n"
            result_message += f"成功添加: {success_count} 只股票\n"
            result_message += f"已存在: {existing_count} 只股票\n"
            result_message += f"导入失败: {error_count} 条记录\n"
            summary_label.setText(result_message)
            layout.addWidget(summary_label)
            
            # 已存在的股票
            if existing_stocks:
                existing_label = QLabel("已存在的股票：")
                layout.addWidget(existing_label)
                
                existing_scroll = QScrollArea()
                existing_content = QVBoxLayout()
                existing_widget = QWidget()
                
                for stock in existing_stocks[:10]:  # 最多显示10条
                    stock_label = QLabel(f"- {stock}")
                    existing_content.addWidget(stock_label)
                if len(existing_stocks) > 10:
                    more_label = QLabel(f"... 等{len(existing_stocks) - 10}条")
                    existing_content.addWidget(more_label)
                
                existing_widget.setLayout(existing_content)
                existing_scroll.setWidget(existing_widget)
                existing_scroll.setWidgetResizable(True)
                existing_scroll.setFixedHeight(100)
                layout.addWidget(existing_scroll)
            
            # 失败的记录
            if error_records:
                error_label = QLabel("失败的记录：")
                layout.addWidget(error_label)
                
                error_table = QTableWidget()
                error_table.setColumnCount(3)
                error_table.setHorizontalHeaderLabels(['股票代码', '股票名称', '失败原因'])
                error_table.setRowCount(len(error_records))
                
                for i, record in enumerate(error_records):
                    error_table.setItem(i, 0, QTableWidgetItem(record['code']))
                    error_table.setItem(i, 1, QTableWidgetItem(record['name']))
                    error_table.setItem(i, 2, QTableWidgetItem(record['reason']))
                
                error_table.horizontalHeader().setStretchLastSection(True)
                layout.addWidget(error_table)
            
            # 确定按钮
            button_layout = QHBoxLayout()
            ok_button = QPushButton('确定')
            ok_button.clicked.connect(dialog.accept)
            button_layout.addStretch()
            button_layout.addWidget(ok_button)
            button_layout.addStretch()
            layout.addLayout(button_layout)
            
            dialog.exec_()

            # 重新加载股票列表
            self.load_stocks(self.filter_input.text().strip())

        except Exception as e:
            print(f"Error importing Excel: {e}")
            from PyQt5.QtWidgets import QMessageBox
            QMessageBox.warning(self, '导入失败', f'导入时发生错误：\n{str(e)}')

    def rate_stock(self, rating):
        selected_row = self.stock_table.currentRow()
        if selected_row < 0:
            from PyQt5.QtWidgets import QMessageBox
            QMessageBox.warning(
                self,
                '提示',
                '请先选择一只股票！'
            )
            return

        stock_id = int(self.stock_table.item(selected_row, 0).text())

        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        try:
            # 插入或更新评价
            cursor.execute('''
                INSERT OR REPLACE INTO stock_ratings (stock_id, rating) 
                VALUES (?, ?)
            ''', (stock_id, rating))
            conn.commit()
            
            # 重新加载股票列表以更新颜色
            self.load_stocks(self.filter_input.text().strip())
        except Exception as e:
            print(f"Error rating stock: {e}")
        finally:
            conn.close()
    
    def clear_stock_rating(self):
        selected_row = self.stock_table.currentRow()
        if selected_row < 0:
            from PyQt5.QtWidgets import QMessageBox
            QMessageBox.warning(
                self,
                '提示',
                '请先选择一只股票！'
            )
            return
        
        stock_id = int(self.stock_table.item(selected_row, 0).text())
        
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        try:
            cursor.execute('DELETE FROM stock_ratings WHERE stock_id = ?', (stock_id,))
            conn.commit()
            
            self.load_stocks(self.filter_input.text().strip())
        except Exception as e:
            print(f"Error clearing stock rating: {e}")
        finally:
            conn.close()

    def on_stock_item_changed(self, item):
        row = item.row()
        column = item.column()
        new_value = item.text().strip()
        
        # 获取股票ID
        stock_id_item = self.stock_table.item(row, 0)
        if not stock_id_item:
            return
        
        stock_id = int(stock_id_item.text())
        
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        try:
            if column == 1:  # 代码列
                # 验证股票代码格式
                if not new_value.isdigit() or len(new_value) != 6:
                    from PyQt5.QtWidgets import QMessageBox
                    QMessageBox.warning(
                        self,
                        '股票代码格式错误',
                        '股票代码必须是6位数字！\n例如：600000'
                    )
                    # 恢复原始值
                    cursor.execute('SELECT code FROM stocks WHERE id = ?', (stock_id,))
                    original_code = cursor.fetchone()[0]
                    item.setText(original_code)
                    return
                
                # 检查代码是否重复
                cursor.execute('SELECT id FROM stocks WHERE code = ? AND id != ?', (new_value, stock_id))
                if cursor.fetchone():
                    from PyQt5.QtWidgets import QMessageBox
                    QMessageBox.warning(
                        self,
                        '股票代码重复',
                        f'股票代码 "{new_value}" 已存在！'
                    )
                    # 恢复原始值
                    cursor.execute('SELECT code FROM stocks WHERE id = ?', (stock_id,))
                    original_code = cursor.fetchone()[0]
                    item.setText(original_code)
                    return
                
                # 更新代码
                cursor.execute('UPDATE stocks SET code = ? WHERE id = ?', (new_value, stock_id))
                
            elif column == 2:  # 名称列
                if not new_value:
                    from PyQt5.QtWidgets import QMessageBox
                    QMessageBox.warning(
                        self,
                        '提示',
                        '股票名称不能为空！'
                    )
                    # 恢复原始值
                    cursor.execute('SELECT name FROM stocks WHERE id = ?', (stock_id,))
                    original_name = cursor.fetchone()[0]
                    item.setText(original_name)
                    return
                
                # 检查名称是否重复
                cursor.execute('SELECT id FROM stocks WHERE name = ? AND id != ?', (new_value, stock_id))
                if cursor.fetchone():
                    from PyQt5.QtWidgets import QMessageBox
                    QMessageBox.warning(
                        self,
                        '股票名称重复',
                        f'股票名称 "{new_value}" 已存在！'
                    )
                    # 恢复原始值
                    cursor.execute('SELECT name FROM stocks WHERE id = ?', (stock_id,))
                    original_name = cursor.fetchone()[0]
                    item.setText(original_name)
                    return
                
                # 更新名称
                cursor.execute('UPDATE stocks SET name = ? WHERE id = ?', (new_value, stock_id))
            
            conn.commit()
            
        except Exception as e:
            print(f"Error updating stock: {e}")
            # 恢复原始值
            cursor.execute('SELECT code, name FROM stocks WHERE id = ?', (stock_id,))
            original_code, original_name = cursor.fetchone()
            self.stock_table.item(row, 1).setText(original_code)
            self.stock_table.item(row, 2).setText(original_name)
        finally:
            conn.close()

    def delete_attribute(self, stock_id, attribute):
        from PyQt5.QtWidgets import QMessageBox

        reply = QMessageBox.question(
            self,
            '确认删除',
            f'确定要删除属性 "{attribute}" 吗？',
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No
        )

        if reply == QMessageBox.No:
            return

        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()

            cursor.execute('DELETE FROM stock_attributes WHERE stock_id = ? AND attribute = ?',
                          (stock_id, attribute))
            conn.commit()

            conn.close()

            # 重新显示属性
            self.on_stock_selection_changed()
            # 重新加载属性列表
            self.load_all_attributes()
            # 清除选中状态
            self.stock_table.clearSelection()
            # 更新按钮状态
            self.on_stock_selection_changed()
        except Exception as e:
            print(f"Error deleting attribute: {e}")

    def show_attr_filter_dialog(self):
        from PyQt5.QtWidgets import QDialog, QListWidget, QAbstractItemView, QVBoxLayout, QHBoxLayout, QPushButton

        dialog = QDialog(self)
        dialog.setWindowTitle('选择属性')
        dialog.resize(300, 400)

        # 居中显示
        if self.isVisible():
            dialog.move(
                self.x() + (self.width() - dialog.width()) // 2,
                self.y() + (self.height() - dialog.height()) // 2
            )

        layout = QVBoxLayout(dialog)

        layout.addWidget(QLabel('选择属性（可多选）:'))
        attr_list = QListWidget()
        attr_list.setSelectionMode(QAbstractItemView.MultiSelection)

        # 加载所有属性
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('SELECT DISTINCT attribute FROM stock_attributes ORDER BY attribute')
        attributes = [row[0] for row in cursor.fetchall()]
        conn.close()

        attr_list.addItems(attributes)
        layout.addWidget(attr_list)

        btn_layout = QHBoxLayout()
        ok_btn = QPushButton('确定')
        cancel_btn = QPushButton('取消')
        btn_layout.addWidget(ok_btn)
        btn_layout.addWidget(cancel_btn)
        layout.addLayout(btn_layout)

        def on_ok():
            selected_attrs = [item.text() for item in attr_list.selectedItems()]
            self.update_selected_attrs(selected_attrs)
            dialog.close()

        ok_btn.clicked.connect(on_ok)
        cancel_btn.clicked.connect(dialog.close)

        dialog.exec_()

    def update_selected_attrs(self, selected_attrs):
        # 清空之前的选中属性标签
        while self.selected_attrs_layout.count() > 0:
            item = self.selected_attrs_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        # 添加新的选中属性标签
        for attr in selected_attrs:
            attr_widget = QWidget()
            attr_layout = QHBoxLayout(attr_widget)
            attr_layout.setContentsMargins(2, 2, 2, 2)
            attr_layout.setSpacing(2)

            attr_label = QLabel(attr)
            attr_label.setStyleSheet('''
                background-color: #E3F2FD;
                border: 1px solid #2196F3;
                border-radius: 3px;
                padding: 2px 6px;
                margin: 0;
                font-size: 12px;
            ''')
            attr_layout.addWidget(attr_label)

            remove_btn = QPushButton('×')
            remove_btn.setStyleSheet('''
                background-color: #FFEBEE;
                border: 1px solid #F44336;
                border-radius: 3px;
                padding: 0 4px;
                font-weight: bold;
                color: #D32F2F;
                font-size: 12px;
            ''')
            remove_btn.setFixedSize(18, 18)
            remove_btn.clicked.connect(lambda checked, a=attr: self.remove_selected_attr(a))
            attr_layout.addWidget(remove_btn)

            self.selected_attrs_layout.addWidget(attr_widget)

        # 重新加载股票
        self.search_stocks()

    def remove_selected_attr(self, attr):
        # 移除指定属性
        for i in range(self.selected_attrs_layout.count()):
            widget = self.selected_attrs_layout.itemAt(i).widget()
            if widget:
                layout = widget.layout()
                if layout and layout.count() > 0:
                    label = layout.itemAt(0).widget()
                    if label and label.text() == attr:
                        widget.deleteLater()
                        break

        # 重新加载股票
        self.search_stocks()

    def search_stocks(self):
        keyword = self.filter_input.text().strip()
        
        # 获取选中的属性
        selected_attrs = []
        for i in range(self.selected_attrs_layout.count()):
            widget = self.selected_attrs_layout.itemAt(i).widget()
            if widget:
                layout = widget.layout()
                if layout and layout.count() > 0:
                    label = layout.itemAt(0).widget()
                    if label:
                        selected_attrs.append(label.text())
        
        self.load_stocks(keyword, selected_attrs)

    def reset_search(self):
        # 清空筛选输入
        self.filter_input.clear()
        
        # 清空选中的属性
        while self.selected_attrs_layout.count() > 0:
            item = self.selected_attrs_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        
        # 重新加载所有股票
        self.load_stocks('')

    def load_stocks(self, keyword='', selected_attrs=None):
        if selected_attrs is None:
            selected_attrs = []
        
        # 重置当前页码
        self.current_page = 1
        
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        # 构建查询条件
        conditions = []
        params = []

        if keyword:
            conditions.append('(s.code LIKE ? OR s.name LIKE ? OR sa.attribute LIKE ?)')
            params.extend([f'%{keyword}%', f'%{keyword}%', f'%{keyword}%'])

        if selected_attrs:
            # 使用更高效的方式处理属性筛选
            placeholders = ','.join(['?'] * len(selected_attrs))
            conditions.append(f's.id IN (SELECT stock_id FROM stock_attributes WHERE attribute IN ({placeholders}) GROUP BY stock_id HAVING COUNT(DISTINCT attribute) = ?)')
            params.extend(selected_attrs)
            params.append(len(selected_attrs))
        
        # 涨停筛选
        if hasattr(self, 'limit_up_filter') and self.limit_up_filter.isChecked():
            yesterday = QDate.currentDate().addDays(-1).toString('yyyy-MM-dd')
            conditions.append('EXISTS (SELECT 1 FROM ladder_stocks ls JOIN ladder_nodes ln ON ls.ladder_node_id = ln.id WHERE ls.stock_id = s.id AND ln.date = ?)')
            params.append(yesterday)
        
        # 评价筛选
        if hasattr(self, 'rating_filter'):
            rating_index = self.rating_filter.currentIndex()
            if rating_index == 1:  # 好评
                conditions.append('EXISTS (SELECT 1 FROM stock_ratings sr WHERE sr.stock_id = s.id AND sr.rating = 1)')
            elif rating_index == 2:  # 差评
                conditions.append('EXISTS (SELECT 1 FROM stock_ratings sr WHERE sr.stock_id = s.id AND sr.rating = -1)')

        # 获取总记录数
        if conditions:
            count_query = f'''
            SELECT COUNT(DISTINCT s.id)
            FROM stocks s
            LEFT JOIN stock_attributes sa ON s.id = sa.stock_id
            WHERE {' AND '.join(conditions)}
            '''
            cursor.execute(count_query, params)
        else:
            cursor.execute('SELECT COUNT(*) FROM stocks')

        self.total_stocks = cursor.fetchone()[0]
        
        # 计算总页数
        page_size = int(self.page_size_combo.currentText())
        self.total_pages = (self.total_stocks + page_size - 1) // page_size
        
        # 确保当前页码有效
        if self.current_page > self.total_pages:
            self.current_page = max(1, self.total_pages)
        
        # 获取分页数据
        offset = (self.current_page - 1) * page_size
        if conditions:
            query = f'''
            SELECT DISTINCT s.id, s.code, s.name
            FROM stocks s
            LEFT JOIN stock_attributes sa ON s.id = sa.stock_id
            WHERE {' AND '.join(conditions)}
            LIMIT ? OFFSET ?
            '''
            cursor.execute(query, params + [page_size, offset])
        else:
            cursor.execute('SELECT id, code, name FROM stocks LIMIT ? OFFSET ?', (page_size, offset))

        stocks = cursor.fetchall()

        # 获取昨天涨停梯队中的股票代码和板数
        yesterday = QDate.currentDate().addDays(-1).toString('yyyy-MM-dd')
        
        # 获取昨天的梯队数量
        cursor.execute('SELECT ladder_count FROM ladder_settings WHERE date = ?', (yesterday,))
        ladder_setting = cursor.fetchone()
        ladder_count = ladder_setting[0] if ladder_setting else 0
        
        # 如果没有设置，检查节点数量
        if ladder_count == 0:
            cursor.execute('SELECT COUNT(*) FROM ladder_nodes WHERE date = ?', (yesterday,))
            ladder_count = cursor.fetchone()[0]
        
        # 获取昨天涨停梯队中的股票代码和节点级别
        cursor.execute('''
            SELECT s.code, ln.node_level
            FROM ladder_stocks ls
            JOIN ladder_nodes ln ON ls.ladder_node_id = ln.id
            JOIN stocks s ON ls.stock_id = s.id
            WHERE ln.date = ?
        ''', (yesterday,))
        yesterday_ladder_data = {row[0]: row[1] for row in cursor.fetchall()}

        # 更新表格
        self.stock_table.setRowCount(len(stocks))
        for row, stock in enumerate(stocks):
            stock_id, code, name = stock
            
            self.stock_table.setItem(row, 0, QTableWidgetItem(str(stock_id)))
            self.stock_table.setItem(row, 1, QTableWidgetItem(code))
            self.stock_table.setItem(row, 2, QTableWidgetItem(name))
            
            # 如果股票在昨天涨停梯队中，显示对号和连板数
            if code in yesterday_ladder_data:
                node_level = yesterday_ladder_data[code]
                board_count = ladder_count - (node_level - 1)
                self.stock_table.setItem(row, 3, QTableWidgetItem('✓'))
                self.stock_table.setItem(row, 4, QTableWidgetItem(str(board_count)))
            else:
                self.stock_table.setItem(row, 3, QTableWidgetItem(''))
                self.stock_table.setItem(row, 4, QTableWidgetItem(''))
            
            # 获取股东持股比例并显示
            cursor.execute('SELECT holder_ratio, total_market_cap FROM stocks WHERE id = ?', (stock_id,))
            stock_data = cursor.fetchone()
            if stock_data:
                holder_ratio, total_market_cap = stock_data
                if holder_ratio is not None:
                    self.stock_table.setItem(row, 5, QTableWidgetItem(f'{holder_ratio:.2f}%'))
                else:
                    self.stock_table.setItem(row, 5, QTableWidgetItem(''))
                
                # 显示总市值（转换为亿并保留两位小数）
                if total_market_cap is not None:
                    market_cap_billion = total_market_cap / 100000000
                    self.stock_table.setItem(row, 6, QTableWidgetItem(f'{market_cap_billion:.2f}'))
                else:
                    self.stock_table.setItem(row, 6, QTableWidgetItem(''))
            else:
                self.stock_table.setItem(row, 5, QTableWidgetItem(''))
                self.stock_table.setItem(row, 6, QTableWidgetItem(''))
            
            # 获取股票评价并设置颜色
            cursor.execute('SELECT rating FROM stock_ratings WHERE stock_id = ?', (stock_id,))
            rating = cursor.fetchone()
            
            if rating:
                if rating[0] == 1:  # 好评
                    for col in range(7):
                        item = self.stock_table.item(row, col)
                        if item:
                            item.setBackground(Qt.green)
                elif rating[0] == -1:  # 差评
                    for col in range(7):
                        item = self.stock_table.item(row, col)
                        if item:
                            item.setBackground(Qt.red)

        # 更新分页信息
        self.page_label.setText(f'第 {self.current_page} 页，共 {self.total_pages} 页')
        self.prev_page_btn.setEnabled(self.current_page > 1)
        self.next_page_btn.setEnabled(self.current_page < self.total_pages)

        conn.close()

    def change_page(self, delta):
        """切换页面"""
        new_page = self.current_page + delta
        if new_page >= 1 and new_page <= self.total_pages:
            self.current_page = new_page
            # 重新加载当前页数据
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()

            # 构建查询条件
            keyword = self.filter_input.text().strip()
            selected_attrs = [label.text() for label in self.selected_attrs_layout.findChildren(QLabel)]
            
            conditions = []
            params = []

            if keyword:
                conditions.append('(s.code LIKE ? OR s.name LIKE ? OR sa.attribute LIKE ?)')
                params.extend([f'%{keyword}%', f'%{keyword}%', f'%{keyword}%'])

            if selected_attrs:
                # 使用更高效的方式处理属性筛选
                placeholders = ','.join(['?'] * len(selected_attrs))
                conditions.append(f's.id IN (SELECT stock_id FROM stock_attributes WHERE attribute IN ({placeholders}) GROUP BY stock_id HAVING COUNT(DISTINCT attribute) = ?)')
                params.extend(selected_attrs)
                params.append(len(selected_attrs))
            
            # 涨停筛选
            if hasattr(self, 'limit_up_filter') and self.limit_up_filter.isChecked():
                yesterday = QDate.currentDate().addDays(-1).toString('yyyy-MM-dd')
                conditions.append('EXISTS (SELECT 1 FROM ladder_stocks ls JOIN ladder_nodes ln ON ls.ladder_node_id = ln.id WHERE ls.stock_id = s.id AND ln.date = ?)')
                params.append(yesterday)

            # 获取分页数据
            page_size = int(self.page_size_combo.currentText())
            offset = (self.current_page - 1) * page_size
            if conditions:
                query = f'''
                SELECT DISTINCT s.id, s.code, s.name
                FROM stocks s
                LEFT JOIN stock_attributes sa ON s.id = sa.stock_id
                WHERE {' AND '.join(conditions)}
                LIMIT ? OFFSET ?
                '''
                cursor.execute(query, params + [page_size, offset])
            else:
                cursor.execute('SELECT id, code, name FROM stocks LIMIT ? OFFSET ?', (page_size, offset))

            stocks = cursor.fetchall()

            # 获取昨天涨停梯队中的股票代码和板数
            yesterday = QDate.currentDate().addDays(-1).toString('yyyy-MM-dd')
            
            # 获取昨天的梯队数量
            cursor.execute('SELECT ladder_count FROM ladder_settings WHERE date = ?', (yesterday,))
            ladder_setting = cursor.fetchone()
            ladder_count = ladder_setting[0] if ladder_setting else 0
            
            # 如果没有设置，检查节点数量
            if ladder_count == 0:
                cursor.execute('SELECT COUNT(*) FROM ladder_nodes WHERE date = ?', (yesterday,))
                ladder_count = cursor.fetchone()[0]
            
            # 获取昨天涨停梯队中的股票代码和节点级别
            cursor.execute('''
                SELECT s.code, ln.node_level
                FROM ladder_stocks ls
                JOIN ladder_nodes ln ON ls.ladder_node_id = ln.id
                JOIN stocks s ON ls.stock_id = s.id
                WHERE ln.date = ?
            ''', (yesterday,))
            yesterday_ladder_data = {row[0]: row[1] for row in cursor.fetchall()}

            # 更新表格
            self.stock_table.setRowCount(len(stocks))
            for row, stock in enumerate(stocks):
                stock_id, code, name = stock
                
                self.stock_table.setItem(row, 0, QTableWidgetItem(str(stock_id)))
                self.stock_table.setItem(row, 1, QTableWidgetItem(code))
                self.stock_table.setItem(row, 2, QTableWidgetItem(name))
                
                # 如果股票在昨天涨停梯队中，显示对号和连板数
                if code in yesterday_ladder_data:
                    node_level = yesterday_ladder_data[code]
                    board_count = ladder_count - (node_level - 1)
                    self.stock_table.setItem(row, 3, QTableWidgetItem('✓'))
                    self.stock_table.setItem(row, 4, QTableWidgetItem(str(board_count)))
                else:
                    self.stock_table.setItem(row, 3, QTableWidgetItem(''))
                    self.stock_table.setItem(row, 4, QTableWidgetItem(''))
                
                # 获取股东持股比例并显示
                cursor.execute('SELECT holder_ratio FROM stocks WHERE id = ?', (stock_id,))
                holder_result = cursor.fetchone()
                if holder_result and holder_result[0] is not None:
                    self.stock_table.setItem(row, 5, QTableWidgetItem(f'{holder_result[0]:.2f}%'))
                else:
                    self.stock_table.setItem(row, 5, QTableWidgetItem(''))
                
                # 获取股票评价并设置颜色
                cursor.execute('SELECT rating FROM stock_ratings WHERE stock_id = ?', (stock_id,))
                rating = cursor.fetchone()
                
                if rating:
                    if rating[0] == 1:  # 好评
                        for col in range(6):
                            item = self.stock_table.item(row, col)
                            if item:
                                item.setBackground(Qt.green)
                    elif rating[0] == -1:  # 差评
                        for col in range(6):
                            item = self.stock_table.item(row, col)
                            if item:
                                item.setBackground(Qt.red)

            # 更新分页信息
        self.page_label.setText(f'第 {self.current_page} 页，共 {self.total_pages} 页')
        self.prev_page_btn.setEnabled(self.current_page > 1)
        self.next_page_btn.setEnabled(self.current_page < self.total_pages)
        self.last_page_btn.setEnabled(self.current_page < self.total_pages)

        conn.close()

    def go_to_last_page(self):
        """跳转到最后一页"""
        if self.total_pages > self.current_page:
            self.current_page = self.total_pages
            # 重新加载当前页数据
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()

            # 构建查询条件
            keyword = self.filter_input.text().strip()
            selected_attrs = [label.text() for label in self.selected_attrs_layout.findChildren(QLabel)]
            
            conditions = []
            params = []

            if keyword:
                conditions.append('(s.code LIKE ? OR s.name LIKE ? OR sa.attribute LIKE ?)')
                params.extend([f'%{keyword}%', f'%{keyword}%', f'%{keyword}%'])

            if selected_attrs:
                # 使用更高效的方式处理属性筛选
                placeholders = ','.join(['?'] * len(selected_attrs))
                conditions.append(f's.id IN (SELECT stock_id FROM stock_attributes WHERE attribute IN ({placeholders}) GROUP BY stock_id HAVING COUNT(DISTINCT attribute) = ?)')
                params.extend(selected_attrs)
                params.append(len(selected_attrs))
            
            # 涨停筛选
            if hasattr(self, 'limit_up_filter') and self.limit_up_filter.isChecked():
                yesterday = QDate.currentDate().addDays(-1).toString('yyyy-MM-dd')
                conditions.append('EXISTS (SELECT 1 FROM ladder_stocks ls JOIN ladder_nodes ln ON ls.ladder_node_id = ln.id WHERE ls.stock_id = s.id AND ln.date = ?)')
                params.append(yesterday)

            # 获取分页数据
            page_size = int(self.page_size_combo.currentText())
            offset = (self.current_page - 1) * page_size
            if conditions:
                query = f'''
                SELECT DISTINCT s.id, s.code, s.name
                FROM stocks s
                LEFT JOIN stock_attributes sa ON s.id = sa.stock_id
                WHERE {' AND '.join(conditions)}
                LIMIT ? OFFSET ?
                '''
                cursor.execute(query, params + [page_size, offset])
            else:
                cursor.execute('SELECT id, code, name FROM stocks LIMIT ? OFFSET ?', (page_size, offset))

            stocks = cursor.fetchall()

            # 获取昨天涨停梯队中的股票代码和板数
            yesterday = QDate.currentDate().addDays(-1).toString('yyyy-MM-dd')
            
            # 获取昨天的梯队数量
            cursor.execute('SELECT ladder_count FROM ladder_settings WHERE date = ?', (yesterday,))
            ladder_setting = cursor.fetchone()
            ladder_count = ladder_setting[0] if ladder_setting else 0
            
            # 如果没有设置，检查节点数量
            if ladder_count == 0:
                cursor.execute('SELECT COUNT(*) FROM ladder_nodes WHERE date = ?', (yesterday,))
                ladder_count = cursor.fetchone()[0]
            
            # 获取昨天涨停梯队中的股票代码和节点级别
            cursor.execute('''
                SELECT s.code, ln.node_level
                FROM ladder_stocks ls
                JOIN ladder_nodes ln ON ls.ladder_node_id = ln.id
                JOIN stocks s ON ls.stock_id = s.id
                WHERE ln.date = ?
            ''', (yesterday,))
            yesterday_ladder_data = {row[0]: row[1] for row in cursor.fetchall()}

            # 更新表格
            self.stock_table.setRowCount(len(stocks))
            for row, stock in enumerate(stocks):
                stock_id, code, name = stock
                
                self.stock_table.setItem(row, 0, QTableWidgetItem(str(stock_id)))
                self.stock_table.setItem(row, 1, QTableWidgetItem(code))
                self.stock_table.setItem(row, 2, QTableWidgetItem(name))
                
                # 如果股票在昨天涨停梯队中，显示对号和连板数
                if code in yesterday_ladder_data:
                    node_level = yesterday_ladder_data[code]
                    board_count = ladder_count - (node_level - 1)
                    self.stock_table.setItem(row, 3, QTableWidgetItem('✓'))
                    self.stock_table.setItem(row, 4, QTableWidgetItem(str(board_count)))
                else:
                    self.stock_table.setItem(row, 3, QTableWidgetItem(''))
                    self.stock_table.setItem(row, 4, QTableWidgetItem(''))
                
                # 获取股东持股比例并显示
                cursor.execute('SELECT holder_ratio FROM stocks WHERE id = ?', (stock_id,))
                holder_result = cursor.fetchone()
                if holder_result and holder_result[0] is not None:
                    self.stock_table.setItem(row, 5, QTableWidgetItem(f'{holder_result[0]:.2f}%'))
                else:
                    self.stock_table.setItem(row, 5, QTableWidgetItem(''))
                
                # 获取股票评价并设置颜色
                cursor.execute('SELECT rating FROM stock_ratings WHERE stock_id = ?', (stock_id,))
                rating = cursor.fetchone()
                
                if rating:
                    if rating[0] == 1:  # 好评
                        for col in range(6):
                            item = self.stock_table.item(row, col)
                            if item:
                                item.setBackground(Qt.green)
                    elif rating[0] == -1:  # 差评
                        for col in range(6):
                            item = self.stock_table.item(row, col)
                            if item:
                                item.setBackground(Qt.red)

            # 更新分页信息
            self.page_label.setText(f'第 {self.current_page} 页，共 {self.total_pages} 页')
            self.prev_page_btn.setEnabled(self.current_page > 1)
            self.next_page_btn.setEnabled(self.current_page < self.total_pages)
            self.last_page_btn.setEnabled(self.current_page < self.total_pages)

            conn.close()

    def show_add_attribute_dialog(self, preselect_stock=None):
        from PyQt5.QtWidgets import QDialog

        dialog = QDialog(self)
        dialog.setWindowTitle('添加属性')
        dialog.resize(500, 400)

        # 居中显示
        if self.isVisible():
            dialog.move(
                self.x() + (self.width() - dialog.width()) // 2,
                self.y() + (self.height() - dialog.height()) // 2
            )

        layout = QVBoxLayout(dialog)

        layout.addWidget(QLabel('股票代码:'))
        stock_code_input = QComboBox()
        stock_code_input.setEditable(True)

        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('SELECT code, name FROM stocks')
        stocks = cursor.fetchall()
        stock_code_input.addItems([f'{code} {name}' for code, name in stocks])
        conn.close()

        if preselect_stock:
            for i in range(stock_code_input.count()):
                if stock_code_input.itemText(i).startswith(preselect_stock):
                    stock_code_input.setCurrentIndex(i)
                    break

        layout.addWidget(stock_code_input)

        layout.addWidget(QLabel('属性（逗号/中文逗号/空格分隔）:'))
        attr_text = QTextEdit()
        attr_text.setPlaceholderText('输入属性，用逗号、中文逗号或空格分隔')
        layout.addWidget(attr_text)

        confirm_btn = QPushButton('确定')
        confirm_btn.clicked.connect(lambda: self.save_attributes(dialog, stock_code_input.currentText(), attr_text.toPlainText()))
        layout.addWidget(confirm_btn)

        # 光标默认放在属性填写栏
        attr_text.setFocus()

        dialog.exec_()

    def save_attributes(self, dialog, stock_text, attr_text):
        if not stock_text or not attr_text:
            dialog.close()
            return

        stock_code = stock_text.split()[0] if stock_text else ''

        attr_text = attr_text.replace('，', ',').replace(' ', ',').replace('、', ',').replace('。', ',')
        attributes = [attr.strip() for attr in attr_text.split(',') if attr.strip()]
        attributes = list(dict.fromkeys(attributes))

        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        cursor.execute('SELECT id FROM stocks WHERE code = ?', (stock_code,))
        stock = cursor.fetchone()
        if not stock:
            conn.close()
            dialog.close()
            return

        stock_id = stock[0]
        date = QDate.currentDate().toString('yyyy-MM-dd')

        cursor.execute('SELECT attribute FROM stock_attributes WHERE stock_id = ? AND date = ?', (stock_id, date))
        existing_attrs = set([row[0] for row in cursor.fetchall()])

        for attr in attributes:
            cleaned = clean_attr_name(attr)
            if cleaned is None:
                continue
            if cleaned not in existing_attrs:
                cursor.execute('INSERT INTO stock_attributes (stock_id, date, attribute) VALUES (?, ?, ?)', (stock_id, date, cleaned))
                existing_attrs.add(cleaned)

        conn.commit()
        conn.close()

        self.load_stocks(self.filter_input.text().strip())
        dialog.close()

    def init_attribute_management_tab(self):
        """初始化属性管理页面"""
        layout = QVBoxLayout(self.attribute_management_tab)
        
        # 标题
        title = QLabel('属性管理')
        title.setStyleSheet('font-size: 16px; font-weight: bold; margin-bottom: 10px;')
        layout.addWidget(title)
        
        # 搜索和筛选区域
        search_layout = QHBoxLayout()
        
        self.attr_search_input = QLineEdit()
        self.attr_search_input.setPlaceholderText('搜索属性...')
        self.attr_search_input.textChanged.connect(lambda text: self.load_attributes(reset_page=True))
        search_layout.addWidget(self.attr_search_input)
        
        self.refresh_attr_btn = QPushButton('刷新')
        self.refresh_attr_btn.clicked.connect(lambda: self.load_attributes(reset_page=True))
        search_layout.addWidget(self.refresh_attr_btn)
        
        layout.addLayout(search_layout)
        
        # 操作按钮
        btn_layout = QHBoxLayout()
        
        self.add_attr_btn = QPushButton('添加属性')
        self.add_attr_btn.clicked.connect(self.show_add_attribute_dialog)
        btn_layout.addWidget(self.add_attr_btn)
        
        self.edit_attr_btn = QPushButton('编辑属性')
        self.edit_attr_btn.clicked.connect(self.show_edit_attribute_dialog)
        self.edit_attr_btn.setEnabled(False)
        btn_layout.addWidget(self.edit_attr_btn)
        
        self.delete_attr_btn = QPushButton('删除属性')
        self.delete_attr_btn.clicked.connect(self.delete_attributes)
        self.delete_attr_btn.setEnabled(False)
        btn_layout.addWidget(self.delete_attr_btn)
        
        layout.addLayout(btn_layout)
        
        # 属性表格
        self.attribute_table = QTableWidget()
        self.attribute_table.setColumnCount(3)
        self.attribute_table.setHorizontalHeaderLabels(['ID', '属性名称', '使用次数'])
        # 只允许双击编辑属性名称列
        self.attribute_table.setEditTriggers(QTableWidget.DoubleClicked)
        self.attribute_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.attribute_table.setSelectionMode(QTableWidget.MultiSelection)
        self.attribute_table.itemSelectionChanged.connect(self.on_attribute_selection_changed)
        # 连接编辑信号
        self.attribute_table.cellDoubleClicked.connect(self.on_attribute_cell_double_clicked)
        # 连接编辑完成信号
        self.attribute_table.itemChanged.connect(self.on_attribute_item_changed)
        # 存储原始值
        self.original_attribute_values = {}
        layout.addWidget(self.attribute_table)
        
        # 分页控件
        pagination_layout = QHBoxLayout()
        pagination_layout.addStretch()
        
        self.attr_page_size_combo = QComboBox()
        self.attr_page_size_combo.addItems(['20', '50', '100'])
        self.attr_page_size_combo.setCurrentText('50')
        self.attr_page_size_combo.currentTextChanged.connect(self.load_attributes)
        pagination_layout.addWidget(QLabel('每页:'))
        pagination_layout.addWidget(self.attr_page_size_combo)
        
        self.attr_page_label = QLabel('第 1 页，共 1 页')
        pagination_layout.addWidget(self.attr_page_label)
        
        self.attr_prev_page_btn = QPushButton('上一页')
        self.attr_prev_page_btn.clicked.connect(lambda: self.change_attribute_page(-1))
        pagination_layout.addWidget(self.attr_prev_page_btn)
        
        self.attr_next_page_btn = QPushButton('下一页')
        self.attr_next_page_btn.clicked.connect(lambda: self.change_attribute_page(1))
        pagination_layout.addWidget(self.attr_next_page_btn)
        
        layout.addLayout(pagination_layout)
        
        # 初始化属性数据
        self.current_attr_page = 1
        self.total_attr_pages = 1
        self.total_attributes = 0
        
        # 加载属性数据
        self.load_attributes(reset_page=True)
    
    def load_attributes(self, reset_page=True):
        """加载属性数据"""
        # 重置当前页码
        if reset_page:
            self.current_attr_page = 1
        
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        # 构建查询条件
        keyword = self.attr_search_input.text().strip()
        conditions = []
        params = []
        
        if keyword:
            conditions.append('attribute LIKE ?')
            params.append(f'%{keyword}%')
        
        # 获取总记录数
        if conditions:
            count_query = f'SELECT COUNT(*) FROM (SELECT DISTINCT attribute FROM stock_attributes WHERE {" AND ".join(conditions)})'
            cursor.execute(count_query, params)
        else:
            cursor.execute('SELECT COUNT(DISTINCT attribute) FROM stock_attributes')
        
        self.total_attributes = cursor.fetchone()[0]
        
        # 计算总页数
        page_size = int(self.attr_page_size_combo.currentText())
        self.total_attr_pages = (self.total_attributes + page_size - 1) // page_size
        
        # 确保当前页码有效
        if self.current_attr_page > self.total_attr_pages:
            self.current_attr_page = max(1, self.total_attr_pages)
        
        # 获取分页数据
        offset = (self.current_attr_page - 1) * page_size
        if conditions:
            query = f'''
            SELECT 
                ROW_NUMBER() OVER (ORDER BY attribute) as id, 
                attribute, 
                COUNT(*) as usage_count
            FROM stock_attributes 
            WHERE {" AND ".join(conditions)}
            GROUP BY attribute 
            ORDER BY attribute 
            LIMIT ? OFFSET ?
            '''
            cursor.execute(query, params + [page_size, offset])
        else:
            query = f'''
            SELECT 
                ROW_NUMBER() OVER (ORDER BY attribute) as id, 
                attribute, 
                COUNT(*) as usage_count
            FROM stock_attributes 
            GROUP BY attribute 
            ORDER BY attribute 
            LIMIT ? OFFSET ?
            '''
            cursor.execute(query, [page_size, offset])
        
        attributes = cursor.fetchall()
        conn.close()
        
        # 清除原始值字典，避免跨页数据混淆
        self.original_attribute_values = {}
        
        # 断开信号连接，避免重新设置数据时触发
        try:
            # 尝试断开信号连接
            self.attribute_table.itemChanged.disconnect(self.on_attribute_item_changed)
        except TypeError:
            # 信号未连接，忽略错误
            pass
        
        # 更新表格
        self.attribute_table.setRowCount(len(attributes))
        for row, attr in enumerate(attributes):
            self.attribute_table.setItem(row, 0, QTableWidgetItem(str(attr[0])))
            self.attribute_table.setItem(row, 1, QTableWidgetItem(attr[1]))
            self.attribute_table.setItem(row, 2, QTableWidgetItem(str(attr[2])))
        
        # 设置只读列
        for row in range(self.attribute_table.rowCount()):
            # ID列只读
            item = self.attribute_table.item(row, 0)
            if item:
                item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
            # 使用次数列只读
            item = self.attribute_table.item(row, 2)
            if item:
                item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
        
        # 重新连接信号
        try:
            # 尝试连接信号
            self.attribute_table.itemChanged.connect(self.on_attribute_item_changed)
        except TypeError:
            # 信号已连接，忽略错误
            pass
        
        # 更新分页信息
        self.attr_page_label.setText(f'第 {self.current_attr_page} 页，共 {self.total_attr_pages} 页')
        self.attr_prev_page_btn.setEnabled(self.current_attr_page > 1)
        self.attr_next_page_btn.setEnabled(self.current_attr_page < self.total_attr_pages)
    
    def change_attribute_page(self, delta):
        """切换属性页面"""
        new_page = self.current_attr_page + delta
        if new_page >= 1 and new_page <= self.total_attr_pages:
            self.current_attr_page = new_page
            
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            # 构建查询条件
            keyword = self.attr_search_input.text().strip()
            conditions = []
            params = []
            
            if keyword:
                conditions.append('attribute LIKE ?')
                params.append(f'%{keyword}%')
            
            # 获取分页数据
            page_size = int(self.attr_page_size_combo.currentText())
            offset = (self.current_attr_page - 1) * page_size
            
            if conditions:
                query = f'''
                SELECT 
                    ROW_NUMBER() OVER (ORDER BY attribute) as id, 
                    attribute, 
                    COUNT(*) as usage_count
                FROM stock_attributes 
                WHERE {" AND ".join(conditions)}
                GROUP BY attribute 
                ORDER BY attribute 
                LIMIT ? OFFSET ?
                '''
                cursor.execute(query, params + [page_size, offset])
            else:
                query = f'''
                SELECT 
                    ROW_NUMBER() OVER (ORDER BY attribute) as id, 
                    attribute, 
                    COUNT(*) as usage_count
                FROM stock_attributes 
                GROUP BY attribute 
                ORDER BY attribute 
                LIMIT ? OFFSET ?
                '''
                cursor.execute(query, [page_size, offset])
            
            attributes = cursor.fetchall()
            conn.close()
            
            # 更新表格
            self.attribute_table.setRowCount(len(attributes))
            for row, attr in enumerate(attributes):
                self.attribute_table.setItem(row, 0, QTableWidgetItem(str(attr[0])))
                self.attribute_table.setItem(row, 1, QTableWidgetItem(attr[1]))
                self.attribute_table.setItem(row, 2, QTableWidgetItem(str(attr[2])))
            
            # 设置只读列
            for row in range(self.attribute_table.rowCount()):
                # ID列只读
                item = self.attribute_table.item(row, 0)
                if item:
                    item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
                # 使用次数列只读
                item = self.attribute_table.item(row, 2)
                if item:
                    item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
            
            # 更新分页信息
            self.attr_page_label.setText(f'第 {self.current_attr_page} 页，共 {self.total_attr_pages} 页')
            self.attr_prev_page_btn.setEnabled(self.current_attr_page > 1)
            self.attr_next_page_btn.setEnabled(self.current_attr_page < self.total_attr_pages)
    
    def on_attribute_selection_changed(self):
        """处理属性选择变化"""
        selected_rows = self.attribute_table.selectionModel().selectedRows()
        has_selection = len(selected_rows) > 0
        # 编辑按钮只在选择单行时可用
        self.edit_attr_btn.setEnabled(len(selected_rows) == 1)
        # 删除按钮在选择多行时可用
        self.delete_attr_btn.setEnabled(has_selection)
    
    def show_edit_attribute_dialog(self, existing_attribute):
        """显示编辑属性对话框"""
        from PyQt5.QtWidgets import QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton
        
        dialog = QDialog(self)
        dialog.setWindowTitle('编辑属性')
        dialog.resize(300, 150)
        
        # 居中显示
        if self.isVisible():
            dialog.move(
                self.x() + (self.width() - dialog.width()) // 2,
                self.y() + (self.height() - dialog.height()) // 2
            )
        
        layout = QVBoxLayout(dialog)
        
        layout.addWidget(QLabel('属性名称:'))
        attr_input = QLineEdit()
        if existing_attribute:
            attr_input.setText(existing_attribute)
        layout.addWidget(attr_input)
        
        btn_layout = QHBoxLayout()
        ok_btn = QPushButton('确定')
        cancel_btn = QPushButton('取消')
        btn_layout.addWidget(ok_btn)
        btn_layout.addWidget(cancel_btn)
        layout.addLayout(btn_layout)
        
        def on_ok():
            attr_name = attr_input.text().strip()
            if not attr_name:
                QMessageBox.warning(self, '提示', '属性名称不能为空')
                return

            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()

            # 编辑属性
            try:
                # 更新所有使用该属性的记录
                cursor.execute('UPDATE stock_attributes SET attribute = ? WHERE attribute = ?', (attr_name, existing_attribute))
                conn.commit()
                QMessageBox.information(self, '成功', f'属性 "{existing_attribute}" 已更新为 "{attr_name}"')
            except Exception as e:
                QMessageBox.warning(self, '错误', f'更新属性失败: {e}')

            conn.close()
            dialog.close()
            self.load_attributes()
        
        ok_btn.clicked.connect(on_ok)
        cancel_btn.clicked.connect(dialog.close)
        
        dialog.exec_()
    
    def show_edit_attribute_dialog(self):
        """显示编辑属性对话框"""
        selected_rows = self.attribute_table.selectionModel().selectedRows()
        if selected_rows and len(selected_rows) == 1:
            row = selected_rows[0].row()
            attribute = self.attribute_table.item(row, 1).text()
            self.show_edit_attribute_dialog(attribute)
    
    def on_attribute_cell_double_clicked(self, row, column):
        """处理单元格双击事件，存储原始值"""
        if column == 1:  # 只处理属性名称列
            item = self.attribute_table.item(row, column)
            if item:
                # 存储原始值，使用行号作为键
                self.original_attribute_values[row] = item.text()
    
    def on_attribute_item_changed(self, item):
        """处理属性名称编辑"""
        # 只处理属性名称列的编辑
        if item.column() != 1:
            return
        
        # 立即断开信号连接，避免重复触发
        try:
            self.attribute_table.itemChanged.disconnect()
        except TypeError:
            pass
        
        new_attr_name = item.text().strip()
        row = item.row()
        
        if not new_attr_name:
            QMessageBox.warning(self, '提示', '属性名称不能为空')
            if row in self.original_attribute_values:
                item.setText(self.original_attribute_values[row])
            self.attribute_table.itemChanged.connect(self.on_attribute_item_changed)
            return
        
        old_attr_name = self.original_attribute_values.get(row)
        if not old_attr_name:
            self.attribute_table.itemChanged.connect(self.on_attribute_item_changed)
            return
        
        if new_attr_name == old_attr_name:
            self.attribute_table.itemChanged.connect(self.on_attribute_item_changed)
            return
        
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute('SELECT COUNT(*) FROM stock_attributes WHERE attribute = ?', (new_attr_name,))
        if cursor.fetchone()[0] > 0:
            reply = QMessageBox.question(
                self,
                '属性已存在',
                f'属性 "{new_attr_name}" 已存在，是否需要合并？\n\n合并后 "{old_attr_name}" 的所有记录将合并到 "{new_attr_name}" 中。',
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No
            )
            if reply == QMessageBox.Yes:
                cursor.execute('UPDATE stock_attributes SET attribute = ? WHERE attribute = ?', (new_attr_name, old_attr_name))
                conn.commit()
                QMessageBox.information(self, '成功', f'属性 "{old_attr_name}" 已合并到 "{new_attr_name}"')
            else:
                item.setText(old_attr_name)
            conn.close()
            self.attribute_table.clearSelection()
            self.load_attributes(reset_page=False)
            self.attribute_table.itemChanged.connect(self.on_attribute_item_changed)
            return
        
        try:
            cursor.execute('UPDATE stock_attributes SET attribute = ? WHERE attribute = ?', (new_attr_name, old_attr_name))
            conn.commit()
            QMessageBox.information(self, '成功', f'属性 "{old_attr_name}" 已更新为 "{new_attr_name}"')
        except Exception as e:
            QMessageBox.warning(self, '错误', f'更新属性失败: {e}')
            item.setText(old_attr_name)
        
        conn.close()
        self.attribute_table.clearSelection()
        self.load_attributes(reset_page=False)
        self.attribute_table.itemChanged.connect(self.on_attribute_item_changed)
    
    def delete_attributes(self):
        """删除属性（支持批量删除）"""
        selected_rows = self.attribute_table.selectionModel().selectedRows()
        if selected_rows:
            # 收集所有选中的属性
            attributes = []
            for selected_row in selected_rows:
                row = selected_row.row()
                attribute = self.attribute_table.item(row, 1).text()
                attributes.append(attribute)
            
            # 构建确认消息
            if len(attributes) == 1:
                msg = f'确定要删除属性 "{attributes[0]}" 吗？\n\n此操作会删除所有股票中使用该属性的记录。'
            else:
                attr_list = '\n'.join([f'- {attr}' for attr in attributes])
                msg = f'确定要删除以下 {len(attributes)} 个属性吗？\n\n{attr_list}\n\n此操作会删除所有股票中使用这些属性的记录。'
            
            reply = QMessageBox.question(
                self, 
                '确认删除', 
                msg,
                QMessageBox.Yes | QMessageBox.No, 
                QMessageBox.No
            )
            
            if reply == QMessageBox.Yes:
                conn = sqlite3.connect(self.db_path)
                cursor = conn.cursor()
                
                try:
                    # 批量删除属性
                    placeholders = ','.join(['?'] * len(attributes))
                    cursor.execute(f'DELETE FROM stock_attributes WHERE attribute IN ({placeholders})', attributes)
                    conn.commit()
                    
                    if len(attributes) == 1:
                        QMessageBox.information(self, '成功', f'属性 "{attributes[0]}" 已删除')
                    else:
                        QMessageBox.information(self, '成功', f'{len(attributes)} 个属性已删除')
                except Exception as e:
                    QMessageBox.warning(self, '错误', f'删除属性失败: {e}')
                
                conn.close()
                # 重新加载数据，不重置页码
                self.load_attributes(reset_page=False)
                # 清除选中状态
                self.attribute_table.clearSelection()
                # 更新按钮状态
                self.on_attribute_selection_changed()
    
    def init_data_backtest_tab(self):
        """初始化数据回测页面"""
        layout = QVBoxLayout(self.data_backtest_tab)
        
        # 标题
        title = QLabel('数据回测')
        title.setStyleSheet('font-size: 16px; font-weight: bold; margin-bottom: 10px;')
        layout.addWidget(title)
        
        # 回测区域
        backtest_group = QGroupBox('回测功能')
        backtest_layout = QVBoxLayout(backtest_group)
        
        # 回测时间范围选择
        backtest_date_layout = QHBoxLayout()
        backtest_date_layout.addWidget(QLabel('开始日期:'))
        self.backtest_start_date = QDateEdit(QDate.currentDate().addDays(-60))
        self.backtest_start_date.setCalendarPopup(True)
        backtest_date_layout.addWidget(self.backtest_start_date)
        
        backtest_date_layout.addWidget(QLabel('结束日期:'))
        self.backtest_end_date = QDateEdit(QDate.currentDate().addDays(-1))
        self.backtest_end_date.setCalendarPopup(True)
        backtest_date_layout.addWidget(self.backtest_end_date)
        
        backtest_date_layout.addWidget(QLabel('跳过节假日:'))
        self.skip_holidays_checkbox = QCheckBox()
        self.skip_holidays_checkbox.setChecked(True)
        backtest_date_layout.addWidget(self.skip_holidays_checkbox)
        
        backtest_layout.addLayout(backtest_date_layout)
        
        # 回测按钮
        backtest_btn_layout = QHBoxLayout()
        self.backtest_btn = QPushButton('开始回测')
        self.backtest_btn.clicked.connect(self.start_backtest)
        self.backtest_btn.setStyleSheet('''
            QPushButton {
                background-color: #28a745;
                color: white;
                border: none;
                padding: 8px 16px;
                border-radius: 4px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #218838;
            }
        ''')
        backtest_btn_layout.addWidget(self.backtest_btn)
        
        self.view_backtest_history_btn = QPushButton('查看回测历史')
        self.view_backtest_history_btn.clicked.connect(self.view_backtest_history)
        backtest_btn_layout.addWidget(self.view_backtest_history_btn)
        self.view_bayesian_progress_btn = QPushButton('查看贝叶斯进度')
        self.view_bayesian_progress_btn.clicked.connect(self.view_bayesian_progress)
        backtest_btn_layout.addWidget(self.view_bayesian_progress_btn)

        self.param_corr_btn = QPushButton('参数属性判定')
        self.param_corr_btn.clicked.connect(self.run_param_correlation_analysis)
        self.param_corr_btn.setStyleSheet('''
            QPushButton {
                background-color: #6f42c1;
                color: white;
                border: none;
                padding: 8px 16px;
                border-radius: 4px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #5a32a3;
            }
        ''')
        backtest_btn_layout.addWidget(self.param_corr_btn)

        self.factor_govern_btn = QPushButton('因子治理')
        self.factor_govern_btn.clicked.connect(self.run_factor_governance)
        self.factor_govern_btn.setStyleSheet('''
            QPushButton {
                background-color: #e83e8c;
                color: white;
                border: none;
                padding: 8px 16px;
                border-radius: 4px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #c2185b;
            }
        ''')
        backtest_btn_layout.addWidget(self.factor_govern_btn)

        backtest_btn_layout.addStretch()
        backtest_layout.addLayout(backtest_btn_layout)
        
        # 回测结果标签
        self.backtest_result_label = QLabel('回测结果: 等待开始...')
        self.backtest_result_label.setStyleSheet('font-size: 12px; margin: 5px 0;')
        backtest_layout.addWidget(self.backtest_result_label)
        
        # 回测详情文本框
        self.backtest_detail_text = QTextEdit()
        self.backtest_detail_text.setReadOnly(True)
        self.backtest_detail_text.setMaximumHeight(150)
        self.backtest_detail_text.setStyleSheet('background-color: #f8f9fa; border: 1px solid #ced4da; border-radius: 3px;')
        backtest_layout.addWidget(self.backtest_detail_text)
        
        layout.addWidget(backtest_group)
        
        # 贝叶斯优化区域
        bayesian_group = QGroupBox('贝叶斯优化')
        bayesian_layout = QVBoxLayout(bayesian_group)
        
        self.bayesian_optimize_btn = QPushButton('贝叶斯优化系数')
        self.bayesian_optimize_btn.clicked.connect(self.run_bayesian_optimization)
        self.bayesian_optimize_btn.setStyleSheet('''
            QPushButton {
                background-color: #007bff;
                color: white;
                border: none;
                padding: 8px 16px;
                border-radius: 4px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #0056b3;
            }
        ''')
        bayesian_layout.addWidget(self.bayesian_optimize_btn)
        
        # 历史优化结果区域
        history_label = QLabel('历史贝叶斯优化结果')
        history_label.setStyleSheet('font-size: 14px; font-weight: bold; margin-top: 10px; margin-bottom: 5px;')
        bayesian_layout.addWidget(history_label)
        
        # 历史结果表格
        self.bayesian_history_table = QTableWidget()
        self.bayesian_history_table.setColumnCount(26)
        self.bayesian_history_table.setHorizontalHeaderLabels([
            '日期', '训练集得分', '验证集得分', '训练集范围', '验证集范围',
            '首股票权重', '末股票权重', '每板权重',
            '抢筹属性a', '涨幅系数b', '抢筹字母', '抢筹地区', '抢筹市值',
            '一字板', '字母属性', '地区属性',
            '属性数量', '负反馈数量', '负反馈其他', '负反馈字母', '负反馈地区',
            '节点指引', '同板压制', '股东持股', '市值权重', '市值指数'
        ])
        self.bayesian_history_table.setMaximumHeight(200)
        self.bayesian_history_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.bayesian_history_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.bayesian_history_table.setAlternatingRowColors(True)
        bayesian_layout.addWidget(self.bayesian_history_table)
        
        # 按钮布局
        history_btn_layout = QHBoxLayout()
        
        self.apply_bayesian_btn = QPushButton('应用选中结果系数')
        self.apply_bayesian_btn.clicked.connect(self.apply_selected_bayesian_params)
        self.apply_bayesian_btn.setStyleSheet('''
            QPushButton {
                background-color: #28a745;
                color: white;
                border: none;
                padding: 6px 12px;
                border-radius: 4px;
            }
            QPushButton:hover {
                background-color: #218838;
            }
        ''')
        history_btn_layout.addWidget(self.apply_bayesian_btn)
        
        refresh_history_btn = QPushButton('刷新历史记录')
        refresh_history_btn.clicked.connect(self.load_bayesian_history)
        history_btn_layout.addWidget(refresh_history_btn)
        
        history_btn_layout.addStretch()
        bayesian_layout.addLayout(history_btn_layout)
        
        layout.addWidget(bayesian_group)
        
        layout.addStretch()
        
        # 加载历史记录
        self.load_bayesian_history()
    
    def load_bayesian_history(self):
        """加载贝叶斯优化历史记录"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        try:
            cursor.execute('''
                SELECT date, train_score, valid_score, params_json, train_start, train_end, valid_start, valid_end
                FROM bayesian_results
                ORDER BY date DESC
                LIMIT 50
            ''')
            results = cursor.fetchall()
            
            self.bayesian_history_table.setRowCount(len(results))
            
            for row, (date, train_score, valid_score, params_json, train_start, train_end, valid_start, valid_end) in enumerate(results):
                import json
                try:
                    params = json.loads(params_json)
                except:
                    params = {}
                
                self.bayesian_history_table.setItem(row, 0, QTableWidgetItem(date))
                self.bayesian_history_table.setItem(row, 1, QTableWidgetItem(f'{train_score:.2f}'))
                self.bayesian_history_table.setItem(row, 2, QTableWidgetItem(f'{valid_score:.2f}'))
                train_range = f"{train_start}~{train_end}" if train_start and train_end else ''
                self.bayesian_history_table.setItem(row, 3, QTableWidgetItem(train_range))
                valid_range = f"{valid_start}~{valid_end}" if valid_start and valid_end else ''
                self.bayesian_history_table.setItem(row, 4, QTableWidgetItem(valid_range))
                self.bayesian_history_table.setItem(row, 5, QTableWidgetItem(f'{params.get("stock_weight_first", 0.0):.4f}'))
                self.bayesian_history_table.setItem(row, 6, QTableWidgetItem(f'{params.get("stock_weight_last", 0.0):.4f}'))
                self.bayesian_history_table.setItem(row, 7, QTableWidgetItem(f'{params.get("board_weight", 0.0):.4f}'))
                self.bayesian_history_table.setItem(row, 8, QTableWidgetItem(f'{params.get("rush_attr_coefficient", 0.0):.4f}'))
                self.bayesian_history_table.setItem(row, 9, QTableWidgetItem(f'{params.get("rush_pct_coefficient", 0.0):.4f}'))
                self.bayesian_history_table.setItem(row, 10, QTableWidgetItem(f'{params.get("rush_letter_attr_weight", 0.0):.4f}'))
                self.bayesian_history_table.setItem(row, 11, QTableWidgetItem(f'{params.get("rush_region_attr_weight", 0.0):.4f}'))
                self.bayesian_history_table.setItem(row, 12, QTableWidgetItem(f'{params.get("rush_market_cap_coefficient", 0.0):.4f}'))
                self.bayesian_history_table.setItem(row, 13, QTableWidgetItem(f'{params.get("yz_overall_weight", 0.0):.4f}'))
                self.bayesian_history_table.setItem(row, 14, QTableWidgetItem(f'{params.get("letter_attr_weight", 0.0):.4f}'))
                self.bayesian_history_table.setItem(row, 15, QTableWidgetItem(f'{params.get("region_attr_weight", 0.0):.4f}'))
                self.bayesian_history_table.setItem(row, 16, QTableWidgetItem(f'{params.get("attr_count_weight", 0.0):.4f}'))
                self.bayesian_history_table.setItem(row, 17, QTableWidgetItem(f'{params.get("negative_attr_count_weight", 0.0):.4f}'))
                self.bayesian_history_table.setItem(row, 18, QTableWidgetItem(f'{params.get("negative_attr_weight", 0.0):.4f}'))
                self.bayesian_history_table.setItem(row, 19, QTableWidgetItem(f'{params.get("negative_letter_attr_weight", 0.0):.4f}'))
                self.bayesian_history_table.setItem(row, 20, QTableWidgetItem(f'{params.get("negative_region_attr_weight", 0.0):.4f}'))
                self.bayesian_history_table.setItem(row, 21, QTableWidgetItem(f'{params.get("node_guide_weight", 0.0):.4f}'))
                self.bayesian_history_table.setItem(row, 22, QTableWidgetItem(f'{params.get("board_press_weight", 0.0):.4f}'))
                self.bayesian_history_table.setItem(row, 23, QTableWidgetItem(f'{params.get("holder_ratio_weight", 0.0):.4f}'))
                self.bayesian_history_table.setItem(row, 24, QTableWidgetItem(f'{params.get("market_cap_weight", 0.0):.4f}'))
                self.bayesian_history_table.setItem(row, 25, QTableWidgetItem(f'{params.get("market_cap_exponent", 0.0):.4f}'))
            
            self.bayesian_history_table.resizeColumnsToContents()
        finally:
            conn.close()
    
    def apply_selected_bayesian_params(self):
        """应用选中的贝叶斯优化历史结果的系数"""
        selected_rows = self.bayesian_history_table.selectionModel().selectedRows()
        if not selected_rows:
            QMessageBox.warning(self, '提示', '请先选择一行贝叶斯优化历史记录')
            return
        
        row = selected_rows[0].row()
        date_item = self.bayesian_history_table.item(row, 0)
        if not date_item:
            QMessageBox.warning(self, '提示', '无法获取选中的记录日期')
            return
        
        date_str = date_item.text()
        
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        try:
            cursor.execute('''
                SELECT params_json FROM bayesian_results WHERE date = ?
            ''', (date_str,))
            result = cursor.fetchone()
            
            if not result:
                QMessageBox.warning(self, '提示', '未找到对应的优化结果')
                return
            
            import json
            params = json.loads(result[0])
            
            # 应用参数到当前程序
            self.STOCK_WEIGHT_FIRST = params.get('stock_weight_first', self.STOCK_WEIGHT_FIRST)
            self.STOCK_WEIGHT_LAST = params.get('stock_weight_last', self.STOCK_WEIGHT_LAST)
            self.BOARD_WEIGHT = params.get('board_weight', self.BOARD_WEIGHT)

            self.RUSH_PCT_COEFFICIENT = params.get('rush_pct_coefficient', self.RUSH_PCT_COEFFICIENT)
            self.RUSH_LETTER_ATTR_WEIGHT = params.get('rush_letter_attr_weight', self.RUSH_LETTER_ATTR_WEIGHT)
            self.RUSH_REGION_ATTR_WEIGHT = params.get('rush_region_attr_weight', self.RUSH_REGION_ATTR_WEIGHT)
            self.RUSH_MARKET_CAP_COEFFICIENT = params.get('rush_market_cap_coefficient', self.RUSH_MARKET_CAP_COEFFICIENT)
            self.YZ_OVERALL_WEIGHT = params.get('yz_overall_weight', self.YZ_OVERALL_WEIGHT)
            self.LETTER_ATTR_WEIGHT = params.get('letter_attr_weight', self.LETTER_ATTR_WEIGHT)
            self.REGION_ATTR_WEIGHT = params.get('region_attr_weight', self.REGION_ATTR_WEIGHT)
            self.ATTR_COUNT_WEIGHT = params.get('attr_count_weight', self.ATTR_COUNT_WEIGHT)
            self.NEGATIVE_ATTR_COUNT_WEIGHT = params.get('negative_attr_count_weight', self.NEGATIVE_ATTR_COUNT_WEIGHT)
            self.NEGATIVE_ATTR_WEIGHT = params.get('negative_attr_weight', self.NEGATIVE_ATTR_WEIGHT)
            self.NEGATIVE_LETTER_ATTR_WEIGHT = params.get('negative_letter_attr_weight', self.NEGATIVE_LETTER_ATTR_WEIGHT)
            self.NEGATIVE_REGION_ATTR_WEIGHT = params.get('negative_region_attr_weight', self.NEGATIVE_REGION_ATTR_WEIGHT)
            self.BOARD_PRESS_WEIGHT = params.get('board_press_weight', self.BOARD_PRESS_WEIGHT)
            self.NODE_GUIDE_WEIGHT = params.get('node_guide_weight', self.NODE_GUIDE_WEIGHT)
            self.HOLDER_RATIO_WEIGHT = params.get('holder_ratio_weight', self.HOLDER_RATIO_WEIGHT)
            self.MARKET_CAP_WEIGHT = params.get('market_cap_weight', self.MARKET_CAP_WEIGHT)
            self.MARKET_CAP_EXPONENT = params.get('market_cap_exponent', self.MARKET_CAP_EXPONENT)
 
            # 先保存到配置文件（使用原始的 Coefficients 值进行查找替换）
            self.save_coefficients_to_config(params)
            
            # 再更新 Coefficients 类的属性
            Coefficients.STOCK_WEIGHT_FIRST = self.STOCK_WEIGHT_FIRST
            Coefficients.STOCK_WEIGHT_LAST = self.STOCK_WEIGHT_LAST
            Coefficients.BOARD_WEIGHT = self.BOARD_WEIGHT

            Coefficients.RUSH_PCT_COEFFICIENT = self.RUSH_PCT_COEFFICIENT
            Coefficients.RUSH_LETTER_ATTR_WEIGHT = self.RUSH_LETTER_ATTR_WEIGHT
            Coefficients.RUSH_REGION_ATTR_WEIGHT = self.RUSH_REGION_ATTR_WEIGHT
            Coefficients.RUSH_MARKET_CAP_COEFFICIENT = self.RUSH_MARKET_CAP_COEFFICIENT
            Coefficients.YZ_OVERALL_WEIGHT = self.YZ_OVERALL_WEIGHT
            Coefficients.LETTER_ATTR_WEIGHT = self.LETTER_ATTR_WEIGHT
            Coefficients.REGION_ATTR_WEIGHT = self.REGION_ATTR_WEIGHT
            Coefficients.ATTR_COUNT_WEIGHT = self.ATTR_COUNT_WEIGHT
            Coefficients.NEGATIVE_ATTR_COUNT_WEIGHT = self.NEGATIVE_ATTR_COUNT_WEIGHT
            Coefficients.NEGATIVE_ATTR_WEIGHT = self.NEGATIVE_ATTR_WEIGHT
            Coefficients.NEGATIVE_LETTER_ATTR_WEIGHT = self.NEGATIVE_LETTER_ATTR_WEIGHT
            Coefficients.NEGATIVE_REGION_ATTR_WEIGHT = self.NEGATIVE_REGION_ATTR_WEIGHT
            Coefficients.BOARD_PRESS_WEIGHT = self.BOARD_PRESS_WEIGHT
            Coefficients.NODE_GUIDE_WEIGHT = self.NODE_GUIDE_WEIGHT
            Coefficients.HOLDER_RATIO_WEIGHT = self.HOLDER_RATIO_WEIGHT
            Coefficients.MARKET_CAP_WEIGHT = self.MARKET_CAP_WEIGHT
            Coefficients.MARKET_CAP_EXPONENT = self.MARKET_CAP_EXPONENT
            
        except Exception as e:
            QMessageBox.warning(self, '错误', f'应用系数时发生错误: {str(e)}')
        finally:
            conn.close()
    
    def init_bidding_analysis_tab(self):
        layout = QVBoxLayout(self.bidding_analysis_tab)

        bidding_layout = QHBoxLayout()
        bidding_layout.addWidget(QLabel('日期:'))
        self.bidding_date = QDateEdit(QDate.currentDate())
        self.bidding_date.setCalendarPopup(True)
        self.bidding_date.dateChanged.connect(self.load_bidding_records)
        bidding_layout.addWidget(self.bidding_date)

        self.add_bidding_btn = QPushButton('添加竞价记录')
        self.add_bidding_btn.clicked.connect(self.show_add_bidding_dialog)
        bidding_layout.addWidget(self.add_bidding_btn)

        self.clear_bidding_btn = QPushButton('清除全部')
        self.clear_bidding_btn.clicked.connect(self.clear_bidding_records)
        bidding_layout.addWidget(self.clear_bidding_btn)

        layout.addLayout(bidding_layout)

        # 全量操作按钮
        onekey_btn = QPushButton('一键获取全部')
        onekey_btn.setStyleSheet('''
            QPushButton {
                background-color: #17a2b8; color: white; font-weight: bold;
                border: none; border-radius: 3px; padding: 6px 16px;
                font-size: 12px;
            }
            QPushButton:hover { background-color: #138496; }
        ''')
        onekey_btn.clicked.connect(self.onekey_fetch_all_bidding)
        bidding_layout.addWidget(onekey_btn)

        mega_batch_btn = QPushButton('全量批量获取')
        mega_batch_btn.setStyleSheet('''
            QPushButton {
                background-color: #e83e8c; color: white; font-weight: bold;
                border: none; border-radius: 3px; padding: 6px 20px;
                font-size: 13px;
            }
            QPushButton:hover { background-color: #c2185b; }
        ''')
        mega_batch_btn.clicked.connect(self.mega_batch_fetch)
        bidding_layout.addWidget(mega_batch_btn)

        bidding_layout.addStretch()

        # hexin-v 令牌配置
        hexin_layout = QHBoxLayout()
        hexin_layout.addWidget(QLabel('hexin-v:'))
        self.hexin_v_input = QLineEdit()
        self.hexin_v_input.setPlaceholderText('输入hexin-v令牌')
        self.hexin_v_input.setText(self.hexin_v)
        self.hexin_v_input.setMaximumWidth(400)
        self.hexin_v_input.setStyleSheet('font-size: 11px;')
        hexin_layout.addWidget(self.hexin_v_input)
        hexin_save_btn = QPushButton('更新')
        hexin_save_btn.setStyleSheet('''
            QPushButton {
                background-color: #6c757d; color: white; font-weight: bold;
                border: none; border-radius: 3px; padding: 2px 10px;
            }
            QPushButton:hover { background-color: #5a6268; }
        ''')
        hexin_save_btn.clicked.connect(self._save_hexin_v_from_ui)
        hexin_layout.addWidget(hexin_save_btn)
        hexin_layout.addStretch()
        layout.addLayout(hexin_layout)



        # 竞价记录分为加分项和减分项两部分
        records_layout = QHBoxLayout()
        
        # 左侧：竞价一字板
        additive_group = QVBoxLayout()
        additive_group.addWidget(QLabel('竞价一字板'))
        
        # 竞价一字板快速输入框
        additive_input_layout = QHBoxLayout()
        additive_input = QLineEdit()
        additive_input.setPlaceholderText('输入股票代码/简称回车添加到竞价一字板')
        additive_input.setMaximumWidth(250)
        additive_input.setStyleSheet('''
            background-color: #f8f9fa;
            border: 1px solid #ced4da;
            border-radius: 3px;
            padding: 2px 5px;
            font-size: 12px;
        ''')
        
        def handle_additive_input():
            stock_input = additive_input.text().strip()
            if not stock_input:
                return
            
            # 搜索股票
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            stock = None
            try:
                # 先按代码搜索
                cursor.execute('SELECT id, code, name FROM stocks WHERE code = ?', (stock_input,))
                result = cursor.fetchone()
                if result:
                    stock = result
                else:
                    # 再按名称搜索
                    cursor.execute('SELECT id, code, name FROM stocks WHERE name LIKE ?', (f'%{stock_input}%',))
                    result = cursor.fetchone()
                    if result:
                        stock = result
            finally:
                conn.close()
            
            if stock:
                stock_id, code, name = stock
                date = self.bidding_date.date().toString('yyyy-MM-dd')
                
                # 添加到加分项
                conn = sqlite3.connect(self.db_path)
                cursor = conn.cursor()
                try:
                    # 检查是否已存在
                    cursor.execute('''
                        SELECT id FROM bidding_records 
                        WHERE date = ? AND stock_id = ? AND is_additive = 1
                    ''', (date, stock_id))
                    if not cursor.fetchone():
                        cursor.execute('''
                            INSERT INTO bidding_records (date, stock_id, is_additive, type) 
                            VALUES (?, ?, 1, 1)
                        ''', (date, stock_id))
                        conn.commit()
                        # 重新加载数据
                        self.load_bidding_records()
                finally:
                    conn.close()
                
                additive_input.clear()
                additive_input.setFocus()
            else:
                QMessageBox.warning(self, '提示', f'未找到股票: {stock_input}')
        
        additive_input.returnPressed.connect(handle_additive_input)
        additive_input_layout.addWidget(additive_input)

        yz_fetch_btn = QPushButton('一键获取')
        yz_fetch_btn.setStyleSheet('''
            QPushButton {
                background-color: #fd7e14; color: white; font-weight: bold;
                border: none; border-radius: 3px; padding: 4px 12px;
            }
            QPushButton:hover { background-color: #e06b0d; }
        ''')
        yz_fetch_btn.clicked.connect(self.fetch_yiziban_stocks)
        additive_input_layout.addWidget(yz_fetch_btn)

        yz_batch_btn = QPushButton('批量获取')
        yz_batch_btn.setStyleSheet('''
            QPushButton {
                background-color: #6f42c1; color: white; font-weight: bold;
                border: none; border-radius: 3px; padding: 4px 12px;
            }
            QPushButton:hover { background-color: #5a32a3; }
        ''')
        yz_batch_btn.clicked.connect(self.batch_fetch_yiziban_stocks)
        additive_input_layout.addWidget(yz_batch_btn)

        additive_input_layout.addStretch()
        additive_group.addLayout(additive_input_layout)
        self.additive_records_table = QTableWidget()
        self.additive_records_table.setColumnCount(4)
        self.additive_records_table.setHorizontalHeaderLabels(['代码', '名称', '添加时间', '操作'])
        self.additive_records_table.setSelectionBehavior(QTableWidget.SelectRows)
        additive_group.addWidget(self.additive_records_table)
        records_layout.addLayout(additive_group)
        
        # 中间：竞价抢筹
        rushing_group = QVBoxLayout()
        rushing_group.addWidget(QLabel('竞价抢筹'))

        # 竞价抢筹快速输入框（两个输入栏：股票 + 涨幅）
        rushing_input_layout = QHBoxLayout()
        rushing_stock_input = QLineEdit()
        rushing_stock_input.setPlaceholderText('输入股票名称或代码')
        rushing_stock_input.setMaximumWidth(200)
        rushing_stock_input.setStyleSheet('''
            background-color: #f8f9fa;
            border: 1px solid #ced4da;
            border-radius: 3px;
            padding: 2px 5px;
            font-size: 12px;
        ''')

        rushing_pct_input = QDoubleSpinBox()
        rushing_pct_input.setRange(-50.0, 99.9)
        rushing_pct_input.setDecimals(2)
        rushing_pct_input.setValue(5.0)
        rushing_pct_input.setSuffix(' %')
        rushing_pct_input.setSingleStep(0.5)
        rushing_pct_input.setMaximumWidth(120)
        rushing_pct_input.setStyleSheet('font-size: 12px;')

        def handle_rushing_input():
            text = rushing_stock_input.text().strip()
            if not text:
                return
            stock_input = text
            pct_change = rushing_pct_input.value()

            # 搜索股票
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            stock = None
            try:
                cursor.execute('SELECT id, code, name FROM stocks WHERE code = ?', (stock_input,))
                stock = cursor.fetchone()
                if not stock:
                    cursor.execute('SELECT id, code, name FROM stocks WHERE name LIKE ?', (f'%{stock_input}%',))
                    stock = cursor.fetchone()
            finally:
                if stock is None:
                    conn.close()
                    QMessageBox.warning(self, '提示', f'未找到股票: {stock_input}')
                    return

            stock_id, code, name = stock
            date = self.bidding_date.date().toString('yyyy-MM-dd')
            try:
                cursor.execute('SELECT id FROM bidding_rush_stocks WHERE date = ? AND stock_id = ?', (date, stock_id))
                if cursor.fetchone():
                    cursor.execute('UPDATE bidding_rush_stocks SET pct_change = ? WHERE date = ? AND stock_id = ?',
                                  (pct_change, date, stock_id))
                else:
                    cursor.execute('INSERT INTO bidding_rush_stocks (date, stock_id, pct_change) VALUES (?, ?, ?)',
                                  (date, stock_id, pct_change))
                conn.commit()
                self.load_bidding_records()
            except Exception as e:
                print(f"Error adding rush stock: {e}")
            finally:
                conn.close()
            rushing_stock_input.clear()
            rushing_pct_input.setValue(5.0)
            rushing_stock_input.setFocus()

        rushing_stock_input.returnPressed.connect(handle_rushing_input)
        rushing_input_layout.addWidget(rushing_stock_input)
        rushing_input_layout.addWidget(rushing_pct_input)

        add_rush_btn = QPushButton('添加')
        add_rush_btn.setStyleSheet('''
            QPushButton {
                background-color: #28a745; color: white; font-weight: bold;
                border: none; border-radius: 3px; padding: 4px 12px;
            }
            QPushButton:hover { background-color: #218838; }
        ''')
        add_rush_btn.clicked.connect(handle_rushing_input)
        rushing_input_layout.addWidget(add_rush_btn)

        rush_import_btn = QPushButton('批量导入')
        rush_import_btn.clicked.connect(self.show_rush_attr_batch_import_dialog)
        rushing_input_layout.addWidget(rush_import_btn)

        rush_fetch_btn = QPushButton('获取早盘竞价')
        rush_fetch_btn.setStyleSheet('''
            QPushButton {
                background-color: #fd7e14; color: white; font-weight: bold;
                border: none; border-radius: 3px; padding: 4px 12px;
            }
            QPushButton:hover { background-color: #e06b0d; }
        ''')
        rush_fetch_btn.clicked.connect(self.fetch_morning_rush_stocks)
        rushing_input_layout.addWidget(rush_fetch_btn)

        batch_fetch_btn = QPushButton('批量获取')
        batch_fetch_btn.setStyleSheet('''
            QPushButton {
                background-color: #6f42c1; color: white; font-weight: bold;
                border: none; border-radius: 3px; padding: 4px 12px;
            }
            QPushButton:hover { background-color: #5a32a3; }
        ''')
        batch_fetch_btn.clicked.connect(self.batch_fetch_morning_rush_stocks)
        rushing_input_layout.addWidget(batch_fetch_btn)

        rush_clear_btn = QPushButton('一键清除')
        rush_clear_btn.setStyleSheet('''
            QPushButton {
                background-color: #dc3545; color: white; font-weight: bold;
                border: none; border-radius: 3px; padding: 4px 12px;
            }
            QPushButton:hover { background-color: #c82333; }
        ''')
        rush_clear_btn.clicked.connect(self.clear_rush_stocks)
        rushing_input_layout.addWidget(rush_clear_btn)

        rushing_input_layout.addStretch()
        rushing_group.addLayout(rushing_input_layout)

        self.rushing_attrs_table = QTableWidget()
        self.rushing_attrs_table.setColumnCount(5)
        self.rushing_attrs_table.setHorizontalHeaderLabels(['代码', '名称', '涨幅%', '添加时间', '操作'])
        self.rushing_attrs_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.rushing_attrs_table.cellChanged.connect(self.on_rush_attr_intensity_changed)
        rushing_group.addWidget(self.rushing_attrs_table)

        records_layout.addLayout(rushing_group)
        
        # 右侧：竞价负反馈
        subtractive_group = QVBoxLayout()
        subtractive_group.addWidget(QLabel('竞价负反馈'))
        
        # 竞价负反馈快速输入框
        subtractive_input_layout = QHBoxLayout()
        subtractive_input = QLineEdit()
        subtractive_input.setPlaceholderText('输入股票代码/简称回车添加到竞价负反馈')
        subtractive_input.setMaximumWidth(250)
        subtractive_input.setStyleSheet('''
            background-color: #f8f9fa;
            border: 1px solid #ced4da;
            border-radius: 3px;
            padding: 2px 5px;
            font-size: 12px;
        ''')
        
        def handle_subtractive_input():
            stock_input = subtractive_input.text().strip()
            if not stock_input:
                return
            
            # 搜索股票
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            stock = None
            try:
                # 先按代码搜索
                cursor.execute('SELECT id, code, name FROM stocks WHERE code = ?', (stock_input,))
                result = cursor.fetchone()
                if result:
                    stock = result
                else:
                    # 再按名称搜索
                    cursor.execute('SELECT id, code, name FROM stocks WHERE name LIKE ?', (f'%{stock_input}%',))
                    result = cursor.fetchone()
                    if result:
                        stock = result
            finally:
                conn.close()
            
            if stock:
                stock_id, code, name = stock
                date = self.bidding_date.date().toString('yyyy-MM-dd')
                
                # 添加到减分项
                conn = sqlite3.connect(self.db_path)
                cursor = conn.cursor()
                try:
                    # 检查是否已存在
                    cursor.execute('''
                        SELECT id FROM bidding_records 
                        WHERE date = ? AND stock_id = ? AND type = 3
                    ''', (date, stock_id))
                    if not cursor.fetchone():
                        cursor.execute('''
                            INSERT INTO bidding_records (date, stock_id, is_additive, type) 
                            VALUES (?, ?, 0, 3)
                        ''', (date, stock_id))
                        conn.commit()
                        # 重新加载数据
                        self.load_bidding_records()
                finally:
                    conn.close()
                
                subtractive_input.clear()
                subtractive_input.setFocus()
            else:
                QMessageBox.warning(self, '提示', f'未找到股票: {stock_input}')
        
        subtractive_input.returnPressed.connect(handle_subtractive_input)
        subtractive_input_layout.addWidget(subtractive_input)

        ff_fetch_btn = QPushButton('一键获取')
        ff_fetch_btn.setStyleSheet('''
            QPushButton {
                background-color: #fd7e14; color: white; font-weight: bold;
                border: none; border-radius: 3px; padding: 4px 12px;
            }
            QPushButton:hover { background-color: #e06b0d; }
        ''')
        ff_fetch_btn.clicked.connect(self.fetch_negative_stocks)
        subtractive_input_layout.addWidget(ff_fetch_btn)

        ff_batch_btn = QPushButton('批量获取')
        ff_batch_btn.setStyleSheet('''
            QPushButton {
                background-color: #6f42c1; color: white; font-weight: bold;
                border: none; border-radius: 3px; padding: 4px 12px;
            }
            QPushButton:hover { background-color: #5a32a3; }
        ''')
        ff_batch_btn.clicked.connect(self.batch_fetch_negative_stocks)
        subtractive_input_layout.addWidget(ff_batch_btn)

        subtractive_input_layout.addStretch()
        subtractive_group.addLayout(subtractive_input_layout)
        
        self.subtractive_records_table = QTableWidget()
        self.subtractive_records_table.setColumnCount(4)
        self.subtractive_records_table.setHorizontalHeaderLabels(['代码', '名称', '添加时间', '操作'])
        subtractive_group.addWidget(self.subtractive_records_table)
        records_layout.addLayout(subtractive_group)
        
        layout.addLayout(records_layout)

        # 分析结果分为竞价一字板属性、竞价抢筹属性和负反馈属性三部分
        analysis_layout = QHBoxLayout()
        
        # 左侧：竞价一字板属性
        additive_analysis_group = QVBoxLayout()
        additive_analysis_group.addWidget(QLabel('竞价一字板属性'))
        self.additive_analysis_result = QTableWidget()
        self.additive_analysis_result.setColumnCount(2)
        self.additive_analysis_result.setHorizontalHeaderLabels(['属性', '来源股票'])
        additive_analysis_group.addWidget(self.additive_analysis_result)
        analysis_layout.addLayout(additive_analysis_group)
        
        # 中间：竞价抢筹属性
        rushing_analysis_group = QVBoxLayout()
        rushing_analysis_group.addWidget(QLabel('竞价抢筹属性'))
        self.rushing_analysis_result = QTableWidget()
        self.rushing_analysis_result.setColumnCount(2)
        self.rushing_analysis_result.setHorizontalHeaderLabels(['属性', '出现次数'])
        rushing_analysis_group.addWidget(self.rushing_analysis_result)
        analysis_layout.addLayout(rushing_analysis_group)
        
        # 右侧：负反馈属性
        subtractive_analysis_group = QVBoxLayout()
        subtractive_analysis_group.addWidget(QLabel('负反馈属性'))
        self.subtractive_analysis_result = QTableWidget()
        self.subtractive_analysis_result.setColumnCount(2)
        self.subtractive_analysis_result.setHorizontalHeaderLabels(['属性', '数量'])
        subtractive_analysis_group.addWidget(self.subtractive_analysis_result)
        analysis_layout.addLayout(subtractive_analysis_group)
        
        layout.addLayout(analysis_layout)

        btn_layout = QHBoxLayout()
        
        # 添加选项复选框
        self.use_ladder_checkbox = QCheckBox('是否从昨天涨停梯队中选择')
        self.use_ladder_checkbox.setChecked(True)  # 默认选中
        btn_layout.addWidget(self.use_ladder_checkbox)
        
        # 弱转强分析选项（与上一个选项互斥）
        self.weak_to_strong_checkbox = QCheckBox('弱转强分析')
        self.weak_to_strong_checkbox.setChecked(False)
        btn_layout.addWidget(self.weak_to_strong_checkbox)
        
        # 互斥逻辑
        def on_use_ladder_changed(state):
            if state == 2:  # Qt.Checked
                self.weak_to_strong_checkbox.setChecked(False)
        
        def on_weak_to_strong_changed(state):
            if state == 2:  # Qt.Checked
                self.use_ladder_checkbox.setChecked(False)
        
        self.use_ladder_checkbox.stateChanged.connect(on_use_ladder_changed)
        self.weak_to_strong_checkbox.stateChanged.connect(on_weak_to_strong_changed)
        
        # 选择几板梯队选项
        ladder_level_layout = QHBoxLayout()
        ladder_level_label = QLabel('选择几板梯队:')
        self.ladder_level_combo = QComboBox()
        self.ladder_level_combo.addItem('所有')
        self.ladder_level_combo.addItem('自定义')
        
        self.ladder_level_input = QSpinBox()
        self.ladder_level_input.setMinimum(1)
        self.ladder_level_input.setMaximum(20)
        self.ladder_level_input.setValue(1)
        self.ladder_level_input.setEnabled(False)  # 默认禁用
        
        # 当选择自定义时启用输入框
        def on_ladder_level_change():
            if self.ladder_level_combo.currentText() == '自定义':
                self.ladder_level_input.setEnabled(True)
            else:
                self.ladder_level_input.setEnabled(False)
        
        self.ladder_level_combo.currentTextChanged.connect(on_ladder_level_change)
        
        ladder_level_layout.addWidget(ladder_level_label)
        ladder_level_layout.addWidget(self.ladder_level_combo)
        ladder_level_layout.addWidget(self.ladder_level_input)
        btn_layout.addLayout(ladder_level_layout)

        self.include_tech_checkbox = QCheckBox('包含科创股(688)')
        self.include_tech_checkbox.setChecked(False)
        btn_layout.addWidget(self.include_tech_checkbox)

        self.include_gem_checkbox = QCheckBox('包含创业板(300)')
        self.include_gem_checkbox.setChecked(False)
        btn_layout.addWidget(self.include_gem_checkbox)

        self.analyze_btn = QPushButton('分析竞价')
        self.analyze_btn.clicked.connect(self.analyze_bidding)
        btn_layout.addWidget(self.analyze_btn)

        self.correlation_btn = QPushButton('分析关联性')
        self.correlation_btn.clicked.connect(self.analyze_correlation)
        btn_layout.addWidget(self.correlation_btn)

        self.score_detail_btn = QPushButton('查看得分详情')
        self.score_detail_btn.clicked.connect(self.show_score_detail_dialog)
        self.score_detail_btn.setEnabled(False)  # 初始禁用，分析后启用
        btn_layout.addWidget(self.score_detail_btn)

        layout.addLayout(btn_layout)

        self.correlation_result = QTableWidget()
        self.correlation_result.setColumnCount(8)
        self.correlation_result.setHorizontalHeaderLabels(['股票代码', '股票名称', '得分', '竞价一字板', '竞价抢筹', '竞价负反馈', '昨日梯队数', '昨日梯队节点'])
        self.correlation_result.setSortingEnabled(True)
        layout.addWidget(self.correlation_result)

        # 评价按钮布局
        rating_layout = QHBoxLayout()
        rating_layout.addWidget(QLabel('评价:'))
        
        self.good_rating_btn = QPushButton('好评')
        self.good_rating_btn.setStyleSheet('background-color: green; color: white;')
        self.good_rating_btn.clicked.connect(lambda: self.rate_bidding_stock(1))
        rating_layout.addWidget(self.good_rating_btn)
        
        self.bad_rating_btn = QPushButton('差评')
        self.bad_rating_btn.setStyleSheet('background-color: red; color: white;')
        self.bad_rating_btn.clicked.connect(lambda: self.rate_bidding_stock(-1))
        rating_layout.addWidget(self.bad_rating_btn)
        
        self.remove_rating_btn = QPushButton('去除评价')
        self.remove_rating_btn.setStyleSheet('background-color: gray; color: white;')
        self.remove_rating_btn.clicked.connect(self.remove_bidding_stock_rating)
        rating_layout.addWidget(self.remove_rating_btn)
        
        layout.addLayout(rating_layout)
        
        # 贝叶斯优化按钮（已隐藏）
        self.bayesian_opt_btn = QPushButton('贝叶斯优化系数')
        self.bayesian_opt_btn.clicked.connect(self.run_bayesian_optimization)
        layout.addWidget(self.bayesian_opt_btn)
        self.bayesian_opt_btn.hide()
        
        self.load_bidding_records()

    def init_limit_up_tab(self):
        layout = QVBoxLayout(self.limit_up_tab)

        top_layout = QHBoxLayout()
        top_layout.addWidget(QLabel('日期:'))
        self.limit_up_date = QDateEdit(QDate.currentDate())
        self.limit_up_date.setCalendarPopup(True)
        top_layout.addWidget(self.limit_up_date)

        self.prev_day_btn = QPushButton('前一天')
        self.prev_day_btn.clicked.connect(self.on_prev_day_clicked)
        top_layout.addWidget(self.prev_day_btn)

        self.next_day_btn = QPushButton('后一天')
        self.next_day_btn.clicked.connect(self.on_next_day_clicked)
        top_layout.addWidget(self.next_day_btn)

        self.today_btn = QPushButton('今天')
        self.today_btn.clicked.connect(self.on_today_clicked)
        top_layout.addWidget(self.today_btn)

        top_layout.addWidget(QLabel('梯队数量:'))
        self.ladder_count_spin = QSpinBox()
        self.ladder_count_spin.setMinimum(0)
        self.ladder_count_spin.setMaximum(20)
        self.ladder_count_spin.setValue(0)
        # 移除 valueChanged 信号连接，改为手动修改
        top_layout.addWidget(self.ladder_count_spin)

        self.modify_ladder_btn = QPushButton('修改')
        self.modify_ladder_btn.clicked.connect(self.modify_ladder_count)
        top_layout.addWidget(self.modify_ladder_btn)

        self.export_ladder_btn = QPushButton('导出')
        self.export_ladder_btn.clicked.connect(self.export_ladder_to_markdown)
        top_layout.addWidget(self.export_ladder_btn)

        self.copy_yesterday_btn = QPushButton('复制昨天')
        self.copy_yesterday_btn.clicked.connect(self.copy_from_yesterday)
        top_layout.addWidget(self.copy_yesterday_btn)

        self.image_ocr_btn = QPushButton('图片识别导入')
        self.image_ocr_btn.clicked.connect(self.show_image_ocr_dialog)
        top_layout.addWidget(self.image_ocr_btn)

        self.clue_connection_btn = QPushButton('线索连线')
        self.clue_connection_btn.clicked.connect(self.show_clue_connection_dialog)
        top_layout.addWidget(self.clue_connection_btn)

        self.auto_get_limit_up_btn = QPushButton('自动获取涨停数据')
        self.auto_get_limit_up_btn.clicked.connect(self.auto_get_limit_up_data)
        top_layout.addWidget(self.auto_get_limit_up_btn)

        batch_limit_up_btn = QPushButton('批量获取')
        batch_limit_up_btn.setStyleSheet('''
            QPushButton {
                background-color: #6f42c1; color: white; font-weight: bold;
                border: none; border-radius: 3px; padding: 5px 15px;
            }
            QPushButton:hover { background-color: #5a32a3; }
        ''')
        batch_limit_up_btn.clicked.connect(self.batch_fetch_limit_up_data)
        top_layout.addWidget(batch_limit_up_btn)

        self.summary_attr_btn = QPushButton('总结')
        self.summary_attr_btn.setStyleSheet('''
            QPushButton {
                background-color: #9C27B0;
                color: white;
                border: none;
                padding: 5px 15px;
                border-radius: 3px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #7B1FA2;
            }
        ''')
        self.summary_attr_btn.clicked.connect(self.show_ladder_attr_summary)
        top_layout.addWidget(self.summary_attr_btn)

        layout.addLayout(top_layout)

        self.ladder_scroll = QScrollArea()
        self.ladder_scroll.setWidgetResizable(True)
        self.ladder_scroll.setMinimumHeight(400)  # 设置最小高度，确保有足够的滚动空间
        self.ladder_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)  # 垂直滚动条按需显示
        self.ladder_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)  # 水平滚动条按需显示
        self.ladder_widget = QWidget()
        self.ladder_layout = QVBoxLayout(self.ladder_widget)
        self.ladder_layout.setSpacing(10)  # 增加间距，让布局更美观
        self.ladder_scroll.setWidget(self.ladder_widget)

        layout.addWidget(self.ladder_scroll)

        # 初始化时加载数据
        self.load_limit_up_data()

    def load_limit_up_data(self):
        if self.is_loading_data:
            return

        self.is_loading_data = True

        while self.ladder_layout.count() > 0:
            item = self.ladder_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        date = self.limit_up_date.date().toString('yyyy-MM-dd')

        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        try:
            cursor.execute('SELECT ladder_count FROM ladder_settings WHERE date = ?', (date,))
            setting = cursor.fetchone()
            
            if setting:
                ladder_count = setting[0]
                # 更新梯队数量控件的值
                self.ladder_count_spin.setValue(ladder_count)
            else:
                # 如果没有设置，检查是否有节点数据
                cursor.execute('SELECT COUNT(*) FROM ladder_nodes WHERE date = ?', (date,))
                node_count = cursor.fetchone()[0]
                if node_count > 0:
                    # 使用节点数量作为梯队数量
                    ladder_count = node_count
                    self.ladder_count_spin.setValue(ladder_count)
                else:
                    ladder_count = self.ladder_count_spin.value()

            cursor.execute('SELECT id, node_level, node_name FROM ladder_nodes WHERE date = ? ORDER BY node_level', (date,))
            existing_nodes = {row[1]: row[0] for row in cursor.fetchall()}

            cursor.execute('''
                SELECT ls.id, ln.node_level, s.code, s.name, ls.order_index
                FROM ladder_stocks ls
                JOIN ladder_nodes ln ON ls.ladder_node_id = ln.id
                JOIN stocks s ON ls.stock_id = s.id
                WHERE ln.date = ?
                ORDER BY ln.node_level, ls.order_index
            ''', (date,))
            stock_records = cursor.fetchall()

            stock_by_level = {}
            for record in stock_records:
                ls_id, node_level, code, name, order_index = record
                if node_level not in stock_by_level:
                    stock_by_level[node_level] = []
                stock_by_level[node_level].append((ls_id, code, name))

            for i in range(ladder_count):
                level = i + 1
                board_count = ladder_count - i

                level_container = QWidget()
                level_container.setStyleSheet('background-color: #f9f9f9; border-radius: 5px; margin: 5px 0; padding: 5px;')
                level_layout = QHBoxLayout(level_container)
                level_layout.setContentsMargins(10, 10, 10, 10)
                level_layout.setSpacing(10)  # 增加间距，让布局更美观

                level_label = QLabel(f'{board_count}板:')
                level_label.setStyleSheet('font-weight: bold; font-size: 14px; min-width: 60px;')
                level_layout.addWidget(level_label)

                # 使用QTextEdit代替QLineEdit以支持自动换行
                node_name_input = QTextEdit()
                node_name_input.setPlaceholderText('节点名称')
                node_name_input.setObjectName(f'node_name_{level}')
                node_name_input.setMaximumWidth(150)  # 恢复原来的宽度限制
                node_name_input.setMaximumHeight(60)  # 设置最大高度，支持多行显示
                node_name_input.setStyleSheet('''
                    background-color: #fff3cd;
                    border: 1px solid #ffc107;
                    border-radius: 3px;
                    padding: 2px 5px;
                    font-size: 13px;
                ''')
                node_name_input.setLineWrapMode(QTextEdit.WidgetWidth)  # 按控件宽度自动换行
                if level in existing_nodes:
                    cursor.execute('SELECT node_name FROM ladder_nodes WHERE id = ?', (existing_nodes[level],))
                    node_name = cursor.fetchone()
                    if node_name and node_name[0]:
                        node_name_input.setText(node_name[0])
                # 添加自动保存功能
                node_id = existing_nodes.get(level)
                if node_id:
                    node_name_input.textChanged.connect(lambda nid=node_id, input_widget=node_name_input: self.save_node_name(nid, input_widget.toPlainText()))
                level_layout.addWidget(node_name_input)

                # 创建股票容器的滚动区域
                stocks_scroll = QScrollArea()
                stocks_scroll.setWidgetResizable(True)
                stocks_scroll.setMinimumHeight(80)
                stocks_scroll.setMaximumHeight(120)
                stocks_scroll.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
                
                # 股票内容容器
                stocks_container = QWidget()
                stocks_container.setStyleSheet('background-color: white; border: 1px solid #e0e0e0; border-radius: 3px; padding: 5px;')
                stocks_layout = FlowLayout(stocks_container)
                
                # 添加股票快速输入框
                add_stock_input = QLineEdit()
                add_stock_input.setPlaceholderText('输入股票代码/简称回车添加')
                add_stock_input.setMaximumWidth(200)
                add_stock_input.setStyleSheet('''
                    background-color: #f8f9fa;
                    border: 1px solid #ced4da;
                    border-radius: 3px;
                    padding: 2px 5px;
                    font-size: 12px;
                ''')
                
                # 存储滚动区域的引用，用于按钮控制
                stocks_scroll.stocks_container = stocks_container
                
                # 使用lambda函数捕获当前的输入框和level值
                add_stock_input.returnPressed.connect(lambda input_widget=add_stock_input, current_level=level, current_container=level_container:
                    self.handle_ladder_stock_input(input_widget, current_level, current_container)
                )
                stocks_layout.addWidget(add_stock_input)
                
                # 存储滚动区域的引用，用于按钮控制
                stocks_scroll.stocks_container = stocks_container
                
                stocks_scroll.setWidget(stocks_container)
                
                # 创建滚动按钮容器
                scroll_buttons_widget = QWidget()
                scroll_buttons_layout = QVBoxLayout(scroll_buttons_widget)
                scroll_buttons_layout.setContentsMargins(5, 0, 0, 0)
                
                # 上滚动按钮
                up_btn = QPushButton('↑')
                up_btn.setFixedSize(30, 30)
                up_btn.setStyleSheet('''
                    background-color: #f0f0f0;
                    border: 1px solid #e0e0e0;
                    border-radius: 3px;
                    font-size: 16px;
                    font-weight: bold;
                ''')
                up_btn.setToolTip('向上滚动')
                up_btn.clicked.connect(lambda: stocks_scroll.verticalScrollBar().setValue(stocks_scroll.verticalScrollBar().value() - 30))
                
                # 下滚动按钮
                down_btn = QPushButton('↓')
                down_btn.setFixedSize(30, 30)
                down_btn.setStyleSheet('''
                    background-color: #f0f0f0;
                    border: 1px solid #e0e0e0;
                    border-radius: 3px;
                    font-size: 16px;
                    font-weight: bold;
                ''')
                down_btn.setToolTip('向下滚动')
                down_btn.clicked.connect(lambda: stocks_scroll.verticalScrollBar().setValue(stocks_scroll.verticalScrollBar().value() + 30))
                
                scroll_buttons_layout.addWidget(up_btn)
                scroll_buttons_layout.addWidget(down_btn)
                
                # 添加到布局
                level_layout.addWidget(stocks_scroll)
                level_layout.addWidget(scroll_buttons_widget)

                if level in stock_by_level:
                    for ls_id, code, name in stock_by_level[level]:
                        stock_widget = QWidget()
                        stock_layout = QHBoxLayout(stock_widget)
                        stock_layout.setContentsMargins(0, 0, 0, 0)
                        stock_layout.setSpacing(2)
                        
                        stock_label = QLabel(f'{code}\n{name}')
                        
                        # 添加删除按钮
                        delete_btn = QPushButton('×')
                        delete_btn.setFixedSize(20, 20)
                        delete_btn.setStyleSheet('''
                            background-color: #F44336;
                            color: white;
                            border: none;
                            border-radius: 10px;
                            font-size: 12px;
                            font-weight: bold;
                        ''')
                        delete_btn.setToolTip('删除股票')
                        # 绑定删除事件
                        delete_btn.clicked.connect(lambda _, ls_id=ls_id: self.delete_stock_from_ladder(ls_id))
                        
                        # 获取股票评价并设置颜色
                        cursor.execute('SELECT id FROM stocks WHERE code = ?', (code,))
                        stock = cursor.fetchone()
                        if stock:
                            cursor.execute('SELECT rating FROM stock_ratings WHERE stock_id = ?', (stock[0],))
                            rating = cursor.fetchone()
                            if rating:
                                if rating[0] == 1:  # 好评
                                    stock_label.setStyleSheet('''
                                        background-color: #E8F5E9;
                                        border: 1px solid #4CAF50;
                                        border-radius: 3px;
                                        padding: 5px;
                                        margin: 0;
                                        text-align: center;
                                    ''')
                                elif rating[0] == -1:  # 差评
                                    stock_label.setStyleSheet('''
                                        background-color: #FFEBEE;
                                        border: 1px solid #F44336;
                                        border-radius: 3px;
                                        padding: 5px;
                                        margin: 0;
                                        text-align: center;
                                    ''')
                                else:
                                    stock_label.setStyleSheet('''
                                        background-color: #E8F5E9;
                                        border: 1px solid #4CAF50;
                                        border-radius: 3px;
                                        padding: 5px;
                                        margin: 0;
                                        text-align: center;
                                    ''')
                            else:
                                stock_label.setStyleSheet('''
                                    background-color: #E8F5E9;
                                    border: 1px solid #4CAF50;
                                    border-radius: 3px;
                                    padding: 5px;
                                    margin: 0;
                                    text-align: center;
                                ''')
                        else:
                            stock_label.setStyleSheet('''
                                background-color: #E8F5E9;
                                border: 1px solid #4CAF50;
                                border-radius: 3px;
                                padding: 5px;
                                margin: 0;
                                text-align: center;
                            ''')
                        
                        stock_label.setMinimumWidth(100)
                        stock_layout.addWidget(stock_label)
                        stock_layout.addWidget(delete_btn)
                        
                        # 只有不是最高梯队时才显示上移按钮
                        if level > 1:
                            move_up_btn = QPushButton('↑')
                            move_up_btn.setFixedSize(25, 25)
                            move_up_btn.setStyleSheet('''
                                background-color: #2196F3;
                                color: white;
                                border: none;
                                border-radius: 3px;
                                font-size: 14px;
                                font-weight: bold;
                            ''')
                            move_up_btn.clicked.connect(lambda checked, lv=level, lsid=ls_id, lbl=stock_label: self.move_stock_up(lv, lsid, lbl))
                            stock_layout.addWidget(move_up_btn)
                        
                        stock_widget.setMinimumWidth(130)
                        stocks_layout.addWidget(stock_widget)

                add_btn = QPushButton('+')
                add_btn.setFixedSize(30, 30)
                add_btn.setStyleSheet('''
                    QPushButton {
                        background-color: #4CAF50;
                        color: white;
                        border: none;
                        border-radius: 15px;
                        font-size: 16px;
                    }
                    QPushButton:hover {
                        background-color: #45a049;
                    }
                ''')
                add_btn.setObjectName(f'add_stock_{level}')
                add_btn.clicked.connect(lambda checked, lvl=level: self.show_add_stock_to_ladder_dialog(lvl, date))
                level_layout.addWidget(add_btn)

                clear_btn = QPushButton('清空')
                clear_btn.setFixedSize(50, 30)
                clear_btn.setStyleSheet('''
                    QPushButton {
                        background-color: #f44336;
                        color: white;
                        border: none;
                        border-radius: 15px;
                        font-size: 12px;
                    }
                    QPushButton:hover {
                        background-color: #da190b;
                    }
                ''')
                clear_btn.setObjectName(f'clear_ladder_{level}')
                clear_btn.setToolTip('清空该梯队的所有股票')
                clear_btn.clicked.connect(lambda checked, lvl=level, bc=board_count: self.clear_ladder_level(lvl, bc, date))
                level_layout.addWidget(clear_btn)

                # 显示当前梯队的股票数量
                stock_count = len(stock_by_level.get(level, []))
                count_label = QLabel(f'共{stock_count}只')
                count_label.setStyleSheet('color: #666; font-size: 12px; margin-left: 10px;')
                level_layout.addWidget(count_label)

                self.ladder_layout.addWidget(level_container)

                if i < ladder_count - 1:
                    line = QFrame()
                    line.setFrameShape(QFrame.HLine)
                    line.setFrameShadow(QFrame.Sunken)
                    line.setStyleSheet('margin: 5px 0;')
                    self.ladder_layout.addWidget(line)
        except Exception as e:
            print(f"Error loading limit up data: {e}")
        finally:
            conn.close()
            self.is_loading_data = False

    def show_image_ocr_dialog(self):
        from PyQt5.QtWidgets import QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QTextEdit, QFileDialog, QComboBox
        
        # 检查OCR库是否可用
        if not OCR_AVAILABLE:
            QMessageBox.warning(self, 'OCR库不可用', '请安装PIL和pytesseract库以使用图片识别功能')
            return
        
        dialog = QDialog(self)
        dialog.setWindowTitle('图片识别导入')
        dialog.resize(600, 500)
        
        # 居中显示
        if self.isVisible():
            dialog.move(
                self.x() + (self.width() - dialog.width()) // 2,
                self.y() + (self.height() - dialog.height()) // 2
            )
        
        layout = QVBoxLayout(dialog)
        
        # 图片选择
        image_layout = QHBoxLayout()
        image_layout.addWidget(QLabel('选择图片:'))
        self.image_path_label = QLabel('未选择图片')
        image_layout.addWidget(self.image_path_label)
        
        select_image_btn = QPushButton('浏览')
        select_image_btn.clicked.connect(self.select_image)
        image_layout.addWidget(select_image_btn)
        layout.addLayout(image_layout)
        
        # 梯队选择
        ladder_layout = QHBoxLayout()
        ladder_layout.addWidget(QLabel('目标梯队:'))
        self.ladder_combobox = QComboBox()
        date = self.limit_up_date.date().toString('yyyy-MM-dd')
        
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        # 获取当前日期的梯队数量
        cursor.execute('SELECT ladder_count FROM ladder_settings WHERE date = ?', (date,))
        setting = cursor.fetchone()
        
        if setting:
            ladder_count = setting[0]
        else:
            # 如果没有设置，检查是否有节点数据
            cursor.execute('SELECT COUNT(*) FROM ladder_nodes WHERE date = ?', (date,))
            node_count = cursor.fetchone()[0]
            if node_count > 0:
                ladder_count = node_count
            else:
                ladder_count = self.ladder_count_spin.value()
        
        conn.close()
        
        # 填充梯队选项
        for i in range(ladder_count):
            level = i + 1
            board_count = ladder_count - i
            self.ladder_combobox.addItem(f'{board_count}板 (梯队{level})', level)
        
        ladder_layout.addWidget(self.ladder_combobox)
        layout.addLayout(ladder_layout)
        
        # 识别结果
        result_layout = QVBoxLayout()
        result_layout.addWidget(QLabel('识别结果 (可编辑):'))
        self.ocr_result_text = QTextEdit()
        # 设置为可编辑，允许用户修改识别结果
        self.ocr_result_text.setReadOnly(False)
        result_layout.addWidget(self.ocr_result_text)
        layout.addLayout(result_layout)
        
        # 按钮
        btn_layout = QHBoxLayout()
        
        recognize_btn = QPushButton('识别图片')
        recognize_btn.clicked.connect(self.recognize_image)
        btn_layout.addWidget(recognize_btn)
        
        confirm_btn = QPushButton('确认导入')
        confirm_btn.clicked.connect(lambda: self.confirm_ocr_import(dialog))
        btn_layout.addWidget(confirm_btn)
        
        cancel_btn = QPushButton('取消')
        cancel_btn.clicked.connect(dialog.close)
        btn_layout.addWidget(cancel_btn)
        
        layout.addLayout(btn_layout)
        
        dialog.exec_()

    def show_clue_connection_dialog(self):
        from PyQt5.QtWidgets import QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QScrollArea, QWidget, QFrame, QGridLayout, QGroupBox, QTableWidget, QTableWidgetItem, QHeaderView
        from PyQt5.QtGui import QPainter, QColor, QPen, QBrush, QLinearGradient
        from PyQt5.QtCore import Qt, QRect

        date = self.limit_up_date.date().toString('yyyy-MM-dd')
        
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        try:
            # 获取梯队数量
            cursor.execute('SELECT ladder_count FROM ladder_settings WHERE date = ?', (date,))
            setting = cursor.fetchone()
            if not setting:
                QMessageBox.warning(self, '提示', '请先设置梯队数量')
                return
            
            ladder_count = setting[0]
            
            # 获取所有梯队节点
            cursor.execute('SELECT node_level, node_name FROM ladder_nodes WHERE date = ? ORDER BY node_level', (date,))
            nodes = cursor.fetchall()
            
            # 获取每个梯队的股票及属性
            ladder_stocks = {}  # {level: [(stock_name, [attr1, attr2, ...]), ...]}
            
            # 获取所有股票及其所属梯队
            cursor.execute('''
                SELECT ln.node_level, ls.stock_id, s.name, sa.attribute
                FROM ladder_stocks ls
                JOIN ladder_nodes ln ON ls.ladder_node_id = ln.id
                JOIN stocks s ON ls.stock_id = s.id
                LEFT JOIN stock_attributes sa ON ls.stock_id = sa.stock_id
                WHERE ln.date = ?
                ORDER BY ln.node_level, ls.order_index
            ''', (date,))
            all_results = cursor.fetchall()
            
            # 按梯队分组
            for level, stock_id, stock_name, attr in all_results:
                if level not in ladder_stocks:
                    ladder_stocks[level] = {}
                if stock_id not in ladder_stocks[level]:
                    ladder_stocks[level][stock_id] = {'name': stock_name, 'attrs': []}
                if attr:
                    ladder_stocks[level][stock_id]['attrs'].append(attr)
            
            # 转换为列表格式
            for level in ladder_stocks:
                ladder_stocks[level] = list(ladder_stocks[level].values())

        finally:
            conn.close()

        # 创建对话框
        dialog = QDialog(self)
        dialog.setWindowTitle('线索连线')
        dialog.resize(1400, 800)
        dialog.setMinimumSize(1200, 600)
        
        # 居中显示
        if self.isVisible():
            dialog.move(
                self.x() + (self.width() - dialog.width()) // 2,
                self.y() + (self.height() - dialog.height()) // 2
            )

        layout = QVBoxLayout(dialog)

        # 分析关联关系，以每只股票为起点找出所有关联股票
        levels = sorted(ladder_stocks.keys())
        groups = []
        
        # 过滤掉无效的梯队（level > ladder_count 的情况）
        valid_levels = [l for l in levels if l <= ladder_count]
        if not valid_levels:
            QMessageBox.warning(self, '提示', '没有有效的梯队数据')
            return
        
        # 为每个股票创建一个关联组
        for level in valid_levels:
            for stock in ladder_stocks[level]:
                # 找出所有与这只股票有共同属性的股票（按梯队分组）
                related_stocks = {}
                
                for target_level in valid_levels:
                    if target_level == level:
                        continue  # 跳过自己
                    
                    for target_stock in ladder_stocks[target_level]:
                        common_attrs = set(stock['attrs']) & set(target_stock['attrs'])
                        if common_attrs:
                            if target_level not in related_stocks:
                                related_stocks[target_level] = []
                            related_stocks[target_level].append(target_stock)
                
                # 如果没有关联股票则跳过
                if not related_stocks:
                    continue
                
                # 创建关联组
                group = {
                    'source_stock': stock,
                    'source_level': level,
                    'source_board_count': ladder_count - level + 1,
                    'related_stocks': related_stocks
                }
                groups.append(group)

        # 创建滚动区域
        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_content = QWidget()
        scroll_layout = QHBoxLayout(scroll_content)
        scroll_layout.setSpacing(20)
        scroll_layout.setContentsMargins(20, 20, 20, 20)

        # 创建关联组卡片
        for idx, group in enumerate(groups):
            source_stock = group['source_stock']
            source_board_count = group['source_board_count']
            related_stocks = group['related_stocks']
            
            group_box = QGroupBox(f'{source_stock["name"]} 的属性关联')
            group_layout = QVBoxLayout(group_box)
            group_layout.setSpacing(15)
            
            # 源股票卡片
            source_widget = QWidget()
            source_layout = QVBoxLayout(source_widget)
            
            level_label = QLabel(f'{source_board_count}板 (源股票)')
            level_label.setStyleSheet('font-size: 11px; color: #007bff; font-weight: bold;')
            source_layout.addWidget(level_label)
            
            name_label = QLabel(source_stock['name'])
            name_label.setStyleSheet('font-weight: bold; font-size: 16px; color: #007bff;')
            source_layout.addWidget(name_label)
            
            if source_stock['attrs']:
                attrs_label = QLabel(f"属性: {', '.join(source_stock['attrs'])}")
                attrs_label.setStyleSheet('font-size: 11px; color: #888;')
                source_layout.addWidget(attrs_label)
            
            source_widget.setStyleSheet('''
                QWidget {
                    background-color: #e7f3ff;
                    border: 2px solid #007bff;
                    border-radius: 8px;
                    padding: 12px;
                }
            ''')
            group_layout.addWidget(source_widget)
            
            # 分隔线
            line_label = QLabel('└─ 关联股票')
            line_label.setStyleSheet('font-size: 12px; color: #28a745;')
            group_layout.addWidget(line_label)
            
            # 关联股票（按梯队从上到下）
            for target_level in sorted(related_stocks.keys()):
                target_board_count = ladder_count - target_level + 1
                stocks = related_stocks[target_level]
                
                # 梯队标题
                level_title = QLabel(f'{target_board_count}板 ({len(stocks)}只)')
                level_title.setStyleSheet('font-size: 12px; color: #666; font-weight: bold; margin-top: 5px;')
                group_layout.addWidget(level_title)
                
                # 股票列表
                for stock in stocks:
                    stock_widget = QWidget()
                    stock_layout = QHBoxLayout(stock_widget)
                    stock_layout.setContentsMargins(10, 5, 10, 5)
                    
                    dot_label = QLabel('•')
                    dot_label.setStyleSheet('color: #28a745;')
                    stock_layout.addWidget(dot_label)
                    
                    name_label = QLabel(stock['name'])
                    name_label.setStyleSheet('font-size: 13px;')
                    stock_layout.addWidget(name_label)
                    
                    # 共同属性
                    common_attrs = set(source_stock['attrs']) & set(stock['attrs'])
                    if common_attrs:
                        attrs_label = QLabel(f"({', '.join(common_attrs)})")
                        attrs_label.setStyleSheet('font-size: 11px; color: #000000;')
                        stock_layout.addWidget(attrs_label)
                    
                    stock_widget.setStyleSheet('''
                        QWidget {
                            background-color: #f8f9fa;
                            border-radius: 4px;
                            margin: 2px;
                        }
                    ''')
                    group_layout.addWidget(stock_widget)
            
            scroll_layout.addWidget(group_box)

        scroll_area.setWidget(scroll_content)
        layout.addWidget(scroll_area)

        # 添加导出按钮
        export_btn = QPushButton('导出为图片')
        export_btn.clicked.connect(lambda: self.export_clue_to_image(scroll_content, dialog))
        layout.addWidget(export_btn)

        dialog.exec_()
    
    def export_clue_to_image(self, widget, parent):
        from PyQt5.QtWidgets import QFileDialog
        from PyQt5.QtGui import QPixmap
        from datetime import datetime
        
        # 生成默认文件名（当天日期）
        default_filename = datetime.now().strftime('%Y-%m-%d') + '.png'
        
        # 选择保存路径
        file_path, _ = QFileDialog.getSaveFileName(
            parent,
            '导出图片',
            default_filename,
            'PNG图片 (*.png);;JPG图片 (*.jpg)'
        )
        
        if not file_path:
            return
        
        # 获取完整内容的尺寸
        widget.adjustSize()
        full_size = widget.size()
        
        # 创建图片
        pixmap = QPixmap(full_size)
        widget.render(pixmap)
        
        # 保存图片
        pixmap.save(file_path)
        
        # 提示成功
        from PyQt5.QtWidgets import QMessageBox
        QMessageBox.information(parent, '导出成功', f'图片已保存到:\n{file_path}')
    
    def select_image(self):
        from PyQt5.QtWidgets import QFileDialog
        
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            '选择图片文件',
            '',
            'Image Files (*.png *.jpg *.jpeg *.bmp *.gif)'
        )
        
        if file_path:
            self.image_path_label.setText(file_path)
    
    def recognize_image(self):
        from PyQt5.QtWidgets import QMessageBox
        
        image_path = self.image_path_label.text()
        if image_path == '未选择图片':
            QMessageBox.warning(self, '提示', '请先选择图片文件')
            return
        
        try:
            # 设置tesseract路径
            pytesseract.pytesseract.tesseract_cmd = r'E:\tesseract\tesseract.exe'
            
            # 检查中文语言包是否存在
            tessdata_path = r'E:\tesseract\tessdata'
            chi_sim_path = os.path.join(tessdata_path, 'chi_sim.traineddata')
            
            if os.path.exists(chi_sim_path):
                lang = 'chi_sim+eng'
            else:
                lang = 'eng'
            
            # 打开图片
            image = Image.open(image_path)
            
            # 识别图片中的文字
            recognized_text = pytesseract.image_to_string(image, lang=lang)
            
            # 显示识别结果
            self.ocr_result_text.setText(recognized_text)
            
            QMessageBox.information(self, '识别完成', '图片文字识别已完成，请确认识别结果')
        except Exception as e:
            QMessageBox.warning(self, '识别失败', f'图片识别失败: {str(e)}')
    
    def confirm_ocr_import(self, dialog):
        from PyQt5.QtWidgets import QMessageBox
        
        # 获取识别结果
        ocr_text = self.ocr_result_text.toPlainText()
        if not ocr_text:
            QMessageBox.warning(self, '提示', '请先识别图片获取文字')
            return
        
        # 获取目标梯队
        level = self.ladder_combobox.currentData()
        if not level:
            QMessageBox.warning(self, '提示', '请选择目标梯队')
            return
        
        # 解析识别结果，提取股票代码和名称
        stock_candidates = self.parse_ocr_text(ocr_text)
        
        # 显示分词结果（调试用）
        if stock_candidates:
            candidates_str = '\n'.join(stock_candidates)
            QMessageBox.information(self, '分词结果', f'识别并分词后的候选股票名称：\n\n{candidates_str}')
        
        if not stock_candidates:
            QMessageBox.warning(self, '提示', '未从识别结果中提取到股票信息')
            return
        
        # 搜索并添加股票到梯队
        added_count = 0
        date = self.limit_up_date.date().toString('yyyy-MM-dd')
        
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        try:
            # 确保目标梯队节点存在
            cursor.execute('SELECT id FROM ladder_nodes WHERE date = ? AND node_level = ?', (date, level))
            node = cursor.fetchone()
            
            if not node:
                # 创建梯队节点
                cursor.execute('INSERT INTO ladder_nodes (date, node_level) VALUES (?, ?)', (date, level))
                conn.commit()
                cursor.execute('SELECT id FROM ladder_nodes WHERE date = ? AND node_level = ?', (date, level))
                node = cursor.fetchone()
            
            node_id = node[0]
            
            # 获取该梯队当前的股票数量，用于确定新股票的顺序
            cursor.execute('SELECT COUNT(*) FROM ladder_stocks WHERE ladder_node_id = ?', (node_id,))
            order_index = cursor.fetchone()[0]
            
            # 搜索并添加股票
            for candidate in stock_candidates:
                # 搜索股票
                cursor.execute('SELECT id, code, name FROM stocks WHERE code = ? OR name LIKE ?', (candidate, f'%{candidate}%'))
                stock = cursor.fetchone()
                
                if stock:
                    stock_id, code, name = stock
                    
                    # 检查股票是否已在该梯队中
                    cursor.execute('SELECT id FROM ladder_stocks WHERE ladder_node_id = ? AND stock_id = ?', (node_id, stock_id))
                    if not cursor.fetchone():
                        # 添加股票到梯队
                        cursor.execute('INSERT INTO ladder_stocks (ladder_node_id, stock_id, order_index) VALUES (?, ?, ?)', (node_id, stock_id, order_index))
                        order_index += 1
                        added_count += 1
            
            conn.commit()
            
            if added_count > 0:
                QMessageBox.information(self, '导入成功', f'成功导入 {added_count} 只股票到梯队')
                dialog.close()
                # 重新加载涨停梯队数据
                self.load_limit_up_data()
            else:
                QMessageBox.warning(self, '导入失败', '未找到匹配的股票或股票已在梯队中')
                
        except Exception as e:
            QMessageBox.warning(self, '导入失败', f'导入过程中发生错误: {str(e)}')
        finally:
            conn.close()
    
    def parse_ocr_text(self, text):
        """解析OCR识别结果，提取股票名称"""
        import re
        
        # 1. 首先清理所有非中文字符
        # 去掉所有空格
        text = re.sub(r'\s+', '', text)
        # 去掉所有英文字符
        text = re.sub(r'[A-Za-z]+', '', text)
        # 去掉所有数字
        text = re.sub(r'\d+', '', text)
        # 去掉所有标点符号
        text = re.sub(r'[\u0021-\u002F\u003A-\u0040\u005B-\u0060\u007B-\u007E\u3000-\u303F]', '', text)
        # 去掉时间格式
        text = re.sub(r'\d{1,2}:\d{2}', '', text)
        
        # 2. 提取所有中文字符
        chinese_text = ''.join(re.findall(r'[\u4e00-\u9fa5]+', text))
        
        candidates = []
        
        # 3. 对纯中文字符串进行分词 - 简化逻辑
        if len(chinese_text) >= 2:
            # 按行分割（假设每行是一个股票或多个股票）
            lines = chinese_text
            
            # 4. 尝试分割成2-4字的词组（减少重复）
            # 只从开头开始尝试分割，不滑动
            i = 0
            while i < len(chinese_text):
                # 优先尝试4字
                if i + 4 <= len(chinese_text):
                    candidates.append(chinese_text[i:i+4])
                    i += 4
                # 然后尝试3字
                elif i + 3 <= len(chinese_text):
                    candidates.append(chinese_text[i:i+3])
                    i += 3
                # 最后尝试2字
                elif i + 2 <= len(chinese_text):
                    candidates.append(chinese_text[i:i+2])
                    i += 2
                else:
                    i += 1
        
        # 5. 去重并过滤
        unique_candidates = list(set(candidates))
        
        # 6. 过滤掉明显不是股票名称的词
        filtered_candidates = []
        for candidate in unique_candidates:
            # 股票名称通常是2-4字
            if 2 <= len(candidate) <= 4:
                # 过滤掉常见的非股票名称词汇
                non_stock_words = ['通信', '燃气', '轮机', '并购', '重组', '创投', '服装', '家纺', '中新', '东望', '泰慕', '慕士', '杭电', '装置', '系统', '设备', '机械', '科技', '集团', '发展', '投资', '实业', '贸易']
                if candidate not in non_stock_words:
                    filtered_candidates.append(candidate)
        
        return filtered_candidates
    
    def show_add_stock_to_ladder_dialog(self, level, date):
        from PyQt5.QtWidgets import QDialog, QListWidget, QAbstractItemView, QHBoxLayout, QPushButton, QLineEdit

        dialog = QDialog(self)
        dialog.setWindowTitle(f'添加股票到梯队')
        dialog.resize(500, 400)
        
        # 居中显示
        if self.isVisible():
            dialog.move(
                self.x() + (self.width() - dialog.width()) // 2,
                self.y() + (self.height() - dialog.height()) // 2
            )

        layout = QVBoxLayout(dialog)

        # 搜索和排序功能
        search_layout = QHBoxLayout()
        search_input = QLineEdit()
        search_input.setPlaceholderText('搜索股票代码或名称')
        search_layout.addWidget(search_input)
        
        sort_btn = QPushButton('按名称排序')
        sort_btn.setCheckable(True)
        search_layout.addWidget(sort_btn)
        
        layout.addLayout(search_layout)

        layout.addWidget(QLabel('选择股票（可多选）:'))
        stock_list = QListWidget()
        stock_list.setSelectionMode(QAbstractItemView.MultiSelection)

        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        # 获取当前日期已在梯队中的股票
        cursor.execute('''
            SELECT s.code
            FROM ladder_stocks ls
            JOIN ladder_nodes ln ON ls.ladder_node_id = ln.id
            JOIN stocks s ON ls.stock_id = s.id
            WHERE ln.date = ?
        ''', (date,))
        existing_stocks = set([row[0] for row in cursor.fetchall()])
        
        # 获取所有股票，排除已在梯队中的
        cursor.execute('SELECT code, name FROM stocks ORDER BY code')
        stocks = cursor.fetchall()
        
        # 存储股票数据
        stock_data = [(code, name) for code, name in stocks if code not in existing_stocks]
        
        # 填充股票列表
        def populate_stock_list():
            stock_list.clear()
            filtered_stocks = stock_data
            
            # 搜索过滤
            search_text = search_input.text().strip().lower()
            if search_text:
                filtered_stocks = [(code, name) for code, name in filtered_stocks 
                                  if search_text in code.lower() or search_text in name.lower()]
            
            # 排序
            if sort_btn.isChecked():
                filtered_stocks.sort(key=lambda x: x[1])  # 按名称排序
            else:
                filtered_stocks.sort(key=lambda x: x[0])  # 按代码排序
            
            # 添加到列表
            for code, name in filtered_stocks:
                stock_list.addItem(f'{code} {name}')
        
        # 初始填充
        populate_stock_list()
        
        # 连接信号
        search_input.textChanged.connect(populate_stock_list)
        sort_btn.toggled.connect(populate_stock_list)
        
        # 添加双击事件，实现双击快速添加
        stock_list.itemDoubleClicked.connect(lambda item: self.add_single_stock_to_ladder(dialog, level, date, item))
        
        conn.close()

        layout.addWidget(stock_list)

        confirm_btn = QPushButton('确定')
        confirm_btn.clicked.connect(lambda: self.add_stocks_to_ladder(dialog, level, date, stock_list))
        layout.addWidget(confirm_btn)

        dialog.exec_()

    def add_stocks_to_ladder(self, dialog, level, date, stock_list):
        selected_items = stock_list.selectedItems()
        if not selected_items:
            dialog.close()
            return

        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        try:
            cursor.execute('SELECT id FROM ladder_nodes WHERE date = ? AND node_level = ?', (date, level))
            node = cursor.fetchone()

            if node:
                node_id = node[0]
            else:
                cursor.execute('INSERT INTO ladder_nodes (date, node_level) VALUES (?, ?)', (date, level))
                conn.commit()
                node_id = cursor.lastrowid

            cursor.execute('SELECT MAX(order_index) FROM ladder_stocks WHERE ladder_node_id = ?', (node_id,))
            max_order = cursor.fetchone()[0] or 0

            for i, item in enumerate(selected_items):
                stock_text = item.text()
                code = stock_text.split()[0] if stock_text else ''
                cursor.execute('SELECT id FROM stocks WHERE code = ?', (code,))
                stock = cursor.fetchone()
                if stock:
                    max_order += 1
                    cursor.execute('INSERT INTO ladder_stocks (ladder_node_id, stock_id, order_index) VALUES (?, ?, ?)',
                                  (node_id, stock[0], max_order))

            conn.commit()
        except Exception as e:
            print(f"Error adding stocks to ladder: {e}")
        finally:
            conn.close()

        dialog.close()
        self.load_limit_up_data()

    def add_single_stock_to_ladder(self, dialog, level, date, item):
        """双击添加单个股票到梯队"""
        stock_text = item.text()
        stock_code = stock_text.split()[0] if stock_text else ''
        
        if not stock_code:
            return

        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        try:
            # 查找或创建梯队节点
            cursor.execute('SELECT id FROM ladder_nodes WHERE date = ? AND node_level = ?', (date, level))
            node = cursor.fetchone()

            if node:
                node_id = node[0]
            else:
                cursor.execute('INSERT INTO ladder_nodes (date, node_level) VALUES (?, ?)', (date, level))
                conn.commit()
                node_id = cursor.lastrowid

            # 查找股票ID
            cursor.execute('SELECT id FROM stocks WHERE code = ?', (stock_code,))
            stock = cursor.fetchone()
            if not stock:
                conn.close()
                return

            stock_id = stock[0]

            # 检查是否已存在
            cursor.execute('SELECT id FROM ladder_stocks WHERE ladder_node_id = ? AND stock_id = ?', (node_id, stock_id))
            if cursor.fetchone():
                conn.close()
                return

            # 获取最大顺序索引
            cursor.execute('SELECT MAX(order_index) FROM ladder_stocks WHERE ladder_node_id = ?', (node_id,))
            max_order = cursor.fetchone()[0] or 0

            # 添加股票到梯队
            max_order += 1
            cursor.execute('INSERT INTO ladder_stocks (ladder_node_id, stock_id, order_index) VALUES (?, ?, ?)',
                          (node_id, stock_id, max_order))
            conn.commit()

        except Exception as e:
            print(f"Error adding single stock to ladder: {e}")
        finally:
            conn.close()

        # 重新加载梯队数据
        self.load_limit_up_data()
        dialog.close()

    def add_stock_to_ladder(self, dialog, level, date, stock_text):
        if not stock_text:
            dialog.close()
            return

        code = stock_text.split()[0] if stock_text else ''

        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        try:
            cursor.execute('SELECT id FROM stocks WHERE code = ?', (code,))
            stock = cursor.fetchone()
            if not stock:
                conn.close()
                dialog.close()
                return

            stock_id = stock[0]

            cursor.execute('SELECT id FROM ladder_nodes WHERE date = ? AND node_level = ?', (date, level))
            node = cursor.fetchone()

            if node:
                node_id = node[0]
            else:
                cursor.execute('INSERT INTO ladder_nodes (date, node_level) VALUES (?, ?)', (date, level))
                conn.commit()
                node_id = cursor.lastrowid

            cursor.execute('SELECT MAX(order_index) FROM ladder_stocks WHERE ladder_node_id = ?', (node_id,))
            max_order = cursor.fetchone()[0] or 0

            cursor.execute('INSERT INTO ladder_stocks (ladder_node_id, stock_id, order_index) VALUES (?, ?, ?)',
                          (node_id, stock_id, max_order + 1))
            conn.commit()
        except Exception as e:
            print(f"Error adding stock to ladder: {e}")
        finally:
            conn.close()

        if dialog:
            dialog.close()
        self.load_limit_up_data()

    def show_remove_stock_from_ladder_dialog(self, level, date):
        from PyQt5.QtWidgets import QDialog

        dialog = QDialog(self)
        dialog.setWindowTitle(f'从梯队删除股票')
        dialog.setGeometry(300, 300, 400, 300)

        layout = QVBoxLayout(dialog)

        layout.addWidget(QLabel('选择要删除的股票:'))
        stock_combo = QComboBox()
        stock_combo.setEditable(True)

        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        cursor.execute('''
            SELECT s.code, s.name, ls.id
            FROM ladder_stocks ls
            JOIN ladder_nodes ln ON ls.ladder_node_id = ln.id
            JOIN stocks s ON ls.stock_id = s.id
            WHERE ln.date = ? AND ln.node_level IN (1, 2)
            ORDER BY ls.order_index
        ''', (date, level))
        stocks = cursor.fetchall()
        stock_combo.addItems([f'{code} {name}' for code, name, _ in stocks])
        conn.close()

        layout.addWidget(stock_combo)

        confirm_btn = QPushButton('确定')
        confirm_btn.clicked.connect(lambda: self.remove_stock_from_ladder(dialog, level, date, stock_combo.currentText(), stocks))
        layout.addWidget(confirm_btn)

        dialog.exec_()

    def remove_stock_from_ladder(self, dialog, level, date, stock_text, stocks):
        if not stock_text:
            dialog.close()
            return

        code = stock_text.split()[0] if stock_text else ''

        for s_code, s_name, ls_id in stocks:
            if s_code == code:
                conn = sqlite3.connect(self.db_path)
                cursor = conn.cursor()
                try:
                    cursor.execute('DELETE FROM ladder_stocks WHERE id = ?', (ls_id,))
                    conn.commit()
                except Exception as e:
                    print(f"Error removing stock from ladder: {e}")
                finally:
                    conn.close()
                break

        dialog.close()
        self.load_limit_up_data()

    def refresh_ladder_stocks(self, container, lvl, d):
        """刷新梯队股票显示"""
        # 找到容器中的股票布局
        for child in container.children():
            if isinstance(child, QScrollArea):
                stocks_widget = child.widget()
                if stocks_widget:
                    # 找到FlowLayout
                    for w_child in stocks_widget.children():
                        if isinstance(w_child, FlowLayout):
                            # 清除所有现有widgets
                            while w_child.count() > 0:
                                item = w_child.takeAt(0)
                                if item.widget():
                                    item.widget().deleteLater()
                            
                            # 重新添加股票输入框
                            input_widget = QWidget()
                            input_layout = QHBoxLayout(input_widget)
                            input_layout.setContentsMargins(0, 0, 0, 0)
                            
                            temp_input = QLineEdit()
                            temp_input.setPlaceholderText('输入股票代码/简称回车添加')
                            temp_input.setMaximumWidth(200)
                            temp_input.setStyleSheet('''
                                background-color: #f8f9fa;
                                border: 1px solid #ced4da;
                                border-radius: 3px;
                                padding: 2px 5px;
                                font-size: 12px;
                            ''')
                            
                            # 绑定事件处理
                            temp_input.returnPressed.connect(lambda input_w=temp_input, current_lvl=lvl, current_container=container:
                                self.handle_ladder_stock_input(input_w, current_lvl, current_container)
                            )
                            
                            input_layout.addWidget(temp_input)
                            w_child.addWidget(input_widget)
                            
                            # 刷新后设置焦点到输入框
                            QTimer.singleShot(50, temp_input.setFocus)
                            
                            # 重新加载股票
                            conn2 = sqlite3.connect(self.db_path)
                            cursor2 = conn2.cursor()
                            cursor2.execute('''
                                SELECT ls.id, s.code, s.name
                                FROM ladder_stocks ls
                                JOIN ladder_nodes ln ON ls.ladder_node_id = ln.id
                                JOIN stocks s ON ls.stock_id = s.id
                                WHERE ln.date = ? AND ln.node_level IN (1, 2)
                                ORDER BY ls.order_index
                            ''', (d, lvl))
                            stocks = cursor2.fetchall()
                            conn2.close()
                            
                            for ls_id, code, name in stocks:
                                stock_widget = QWidget()
                                stock_widget.setStyleSheet('background-color: #e3f2fd; border-radius: 3px; padding: 3px; margin: 2px;')
                                stock_layout = QHBoxLayout(stock_widget)
                                stock_layout.setContentsMargins(5, 2, 5, 2)
                                
                                code_label = QLabel(code)
                                code_label.setStyleSheet('color: #1976d2; font-weight: bold;')
                                name_label = QLabel(name)
                                name_label.setStyleSheet('color: #333;')
                                
                                remove_btn = QPushButton('×')
                                remove_btn.setFixedSize(20, 20)
                                remove_btn.setStyleSheet('background-color: #ffcdd2; border: none; border-radius: 3px; color: #c62828; font-weight: bold;')
                                remove_btn.clicked.connect(lambda checked, lsid=ls_id, lid=lvl, did=d: self.remove_stock_from_ladder_by_id(lsid, lid, did))
                                
                                stock_layout.addWidget(code_label)
                                stock_layout.addWidget(name_label)
                                stock_layout.addWidget(remove_btn)
                                w_child.addWidget(stock_widget)
                            break
                    break

    def handle_ladder_stock_input(self, input_widget, level, container):
        """处理梯队股票输入"""
        stock_input = input_widget.text().strip()
        if not stock_input:
            return
        
        # 从数据库搜索股票
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        stock = None
        
        try:
            # 先按代码搜索
            cursor.execute('SELECT code, name FROM stocks WHERE code = ?', (stock_input,))
            result = cursor.fetchone()
            if result:
                stock = result
            else:
                # 再按名称模糊搜索
                cursor.execute('SELECT code, name FROM stocks WHERE name LIKE ?', (f'%{stock_input}%',))
                result = cursor.fetchone()
                if result:
                    stock = result
        finally:
            conn.close()
        
        # 添加股票
        if stock:
            code, name = stock
            # 直接操作数据库添加股票，不调用刷新界面的方法
            date = self.limit_up_date.date().toString('yyyy-MM-dd')
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            try:
                # 获取或创建梯队节点
                cursor.execute('SELECT id FROM ladder_nodes WHERE date = ? AND node_level = ?', (date, level))
                node = cursor.fetchone()
                
                if not node:
                    cursor.execute('INSERT INTO ladder_nodes (date, node_level) VALUES (?, ?)', (date, level))
                    conn.commit()
                    cursor.execute('SELECT id FROM ladder_nodes WHERE date = ? AND node_level = ?', (date, level))
                    node = cursor.fetchone()
                
                node_id = node[0]
                
                # 获取该梯队当前的股票数量
                cursor.execute('SELECT COUNT(*) FROM ladder_stocks WHERE ladder_node_id = ?', (node_id,))
                order_index = cursor.fetchone()[0]
                
                # 检查股票是否已在该梯队中
                cursor.execute('SELECT id FROM stocks WHERE code = ?', (code,))
                stock_id = cursor.fetchone()[0]
                
                cursor.execute('SELECT id FROM ladder_stocks WHERE ladder_node_id = ? AND stock_id = ?', (node_id, stock_id))
                if not cursor.fetchone():
                    cursor.execute('INSERT INTO ladder_stocks (ladder_node_id, stock_id, order_index) VALUES (?, ?, ?)', (node_id, stock_id, order_index))
                    conn.commit()
                    
                    # 只刷新当前梯队的股票显示
                    self.refresh_ladder_stocks(container, level, date)
            finally:
                conn.close()
        else:
            QMessageBox.warning(self, '提示', f'未找到股票: {stock_input}')

    def remove_stock_from_ladder_by_id(self, ls_id, level, date):
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        try:
            cursor.execute('DELETE FROM ladder_stocks WHERE id = ?', (ls_id,))
            conn.commit()
        except Exception as e:
            print(f"Error removing stock from ladder: {e}")
        finally:
            conn.close()

    def show_add_bidding_dialog(self):
        from PyQt5.QtWidgets import QDialog, QListWidget, QAbstractItemView, QHBoxLayout, QLineEdit

        dialog = QDialog(self)
        dialog.setWindowTitle('添加竞价记录')
        dialog.resize(500, 400)
        
        # 居中显示
        if self.isVisible():
            dialog.move(
                self.x() + (self.width() - dialog.width()) // 2,
                self.y() + (self.height() - dialog.height()) // 2
            )

        layout = QVBoxLayout(dialog)

        # 搜索功能
        search_layout = QHBoxLayout()
        search_input = QLineEdit()
        search_input.setPlaceholderText('搜索股票代码或名称')
        search_layout.addWidget(search_input)
        layout.addLayout(search_layout)

        layout.addWidget(QLabel('选择股票（可多选）:'))
        stock_list = QListWidget()
        stock_list.setSelectionMode(QAbstractItemView.MultiSelection)

        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('SELECT code, name FROM stocks ORDER BY code')
        stocks = cursor.fetchall()
        conn.close()

        # 存储股票数据
        stock_data = [(code, name) for code, name in stocks]

        # 填充股票列表
        def populate_stock_list():
            stock_list.clear()
            filtered_stocks = stock_data
            
            # 搜索过滤
            search_text = search_input.text().strip().lower()
            if search_text:
                filtered_stocks = [(code, name) for code, name in filtered_stocks 
                                  if search_text in code.lower() or search_text in name.lower()]
            
            # 按代码排序
            filtered_stocks.sort(key=lambda x: x[0])
            
            # 添加到列表
            for code, name in filtered_stocks:
                stock_list.addItem(f'{code} {name}')

        # 初始填充
        populate_stock_list()
        
        # 连接信号
        search_input.textChanged.connect(populate_stock_list)
        
        # 双击添加股票
        stock_list.itemDoubleClicked.connect(lambda item: self.add_single_bidding_record(dialog, item.text()))

        layout.addWidget(stock_list)

        # 添加加分项和减分项按钮
        button_layout = QHBoxLayout()
        
        add_btn = QPushButton('加分项')
        add_btn.setStyleSheet('background-color: green; color: white;')
        add_btn.clicked.connect(lambda: self.add_bidding_records(dialog, stock_list, True))
        button_layout.addWidget(add_btn)
        
        subtract_btn = QPushButton('减分项')
        subtract_btn.setStyleSheet('background-color: red; color: white;')
        subtract_btn.clicked.connect(lambda: self.add_bidding_records(dialog, stock_list, False))
        button_layout.addWidget(subtract_btn)
        
        layout.addLayout(button_layout)

        dialog.exec_()

    def add_bidding_records(self, dialog, stock_list, is_additive=True):
        try:
            selected_items = stock_list.selectedItems()
            if not selected_items:
                dialog.close()
                return

            date = self.bidding_date.date().toString('yyyy-MM-dd')

            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()

            try:
                for item in selected_items:
                    try:
                        stock_text = item.text()
                        if stock_text:
                            parts = stock_text.split()
                            if parts:
                                code = parts[0]
                                cursor.execute('SELECT id FROM stocks WHERE code = ?', (code,))
                                stock = cursor.fetchone()
                                if stock:
                                    cursor.execute('INSERT OR IGNORE INTO bidding_records (date, stock_id, is_additive) VALUES (?, ?, ?)',
                                                  (date, stock[0], 1 if is_additive else 0))
                    except Exception as e:
                        print(f"Error processing stock item: {e}")

                conn.commit()
            except Exception as e:
                print(f"Error adding bidding records: {e}")
            finally:
                conn.close()

            dialog.close()
            self.load_bidding_records()
        except Exception as e:
            print(f"Critical error in add_bidding_records: {e}")
            dialog.close()

    def add_single_bidding_record(self, dialog, stock_text):
        if not stock_text:
            return

        parts = stock_text.split()
        if not parts:
            return

        code = parts[0]
        date = self.bidding_date.date().toString('yyyy-MM-dd')

        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        try:
            cursor.execute('SELECT id FROM stocks WHERE code = ?', (code,))
            stock = cursor.fetchone()
            if stock:
                cursor.execute('INSERT OR IGNORE INTO bidding_records (date, stock_id, is_additive) VALUES (?, ?, ?)',
                              (date, stock[0], 1))  # 默认添加为加分项
                conn.commit()
                self.load_bidding_records()
        except Exception as e:
            print(f"Error adding single bidding record: {e}")
        finally:
            conn.close()

    def analyze_bidding(self):
        try:
            date = self.bidding_date.date().toString('yyyy-MM-dd')
            yesterday = QDate.fromString(date, 'yyyy-MM-dd').addDays(-1).toString('yyyy-MM-dd')

            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()

            try:
                # 获取昨天涨停梯队的最大node_level（1板梯队，板数最低）
                cursor.execute('SELECT MAX(node_level) FROM ladder_nodes WHERE date = ?', (yesterday,))
                max_node_level_result = cursor.fetchone()
                max_node_level = max_node_level_result[0] if max_node_level_result and max_node_level_result[0] else 0
                
                # 获取今天一字板中在昨天涨停梯队（非1板，即node_level < max）的股票及其所在梯队
                # 因为node_level=1是最高板，数字越大板数越低，所以1板梯队是node_level最大的
                if max_node_level > 1:
                    cursor.execute('''
                        SELECT s.code, ln.node_level
                        FROM bidding_records br
                        JOIN stocks s ON br.stock_id = s.id
                        JOIN ladder_stocks ls ON ls.stock_id = s.id
                        JOIN ladder_nodes ln ON ls.ladder_node_id = ln.id
                        WHERE br.date = ? AND br.type = 1
                        AND ln.date = ? AND ln.node_level < ?
                    ''', (date, yesterday, max_node_level))
                    yz_board_stocks = cursor.fetchall()
                else:
                    yz_board_stocks = []
                
                # 构建同板压制映射：{梯队级别: [同板股票代码]}
                board_press_map = {}
                for code, node_level in yz_board_stocks:
                    if node_level not in board_press_map:
                        # 获取该梯队的所有股票
                        cursor.execute('''
                            SELECT s.code
                            FROM ladder_stocks ls
                            JOIN ladder_nodes ln ON ls.ladder_node_id = ln.id
                            JOIN stocks s ON ls.stock_id = s.id
                            WHERE ln.date = ? AND ln.node_level IN (1, 2)
                        ''', (yesterday, node_level))
                        board_stocks = [row[0] for row in cursor.fetchall()]
                        board_press_map[node_level] = board_stocks
                
                # 获取昨天1板梯队的股票（node_level最大的梯队）
                yesterday_12board_stocks = []
                if max_node_level > 0:
                    cursor.execute('''
                        SELECT s.code
                        FROM ladder_stocks ls
                        JOIN ladder_nodes ln ON ls.ladder_node_id = ln.id
                        JOIN stocks s ON ls.stock_id = s.id
                        WHERE ln.date = ? AND ln.node_level IN (1, 2)
                    ''', (yesterday, max_node_level))
                    yesterday_12board_stocks = [row[0] for row in cursor.fetchall()]
                
# 检查竞价记录中是否有股票在昨天1板或2板梯队中
                bidding_codes = [code for _, _, code in records] if records else []
                has_1board_guidance = any(code in yesterday_12board_stocks for code in bidding_codes)

                # 获取竞价记录，按添加顺序（id）排序，越靠前的id越小
                cursor.execute('''
                    SELECT br.id, sa.attribute, s.code
                    FROM bidding_records br
                    JOIN stock_attributes sa ON br.stock_id = sa.stock_id
                    JOIN stocks s ON br.stock_id = s.id
                    WHERE br.date = ?
                    ORDER BY br.id
                ''', (date,))
                records = cursor.fetchall()

                # 计算加权得分
                # 越靠前的股票，权重越高
                # 假设第一个股票权重为1.0，每往后一个权重减少0.1，最低为0.1
                attr_weighted_count = {}
                
                # 按股票分组
                stock_attrs = {}
                stock_codes = {}
                for record_id, attr, code in records:
                    if record_id not in stock_attrs:
                        stock_attrs[record_id] = []
                        stock_codes[record_id] = code
                    stock_attrs[record_id].append(attr)
                
                # 计算加权得分
                total_stocks = len(stock_attrs)
                for idx, (stock_id, attrs) in enumerate(stock_attrs.items()):
                    # 权重计算：越靠前权重越高
                    # 第一个股票权重 = 1.0，最后一个 = 0.1
                    if total_stocks > 1:
                        weight = 1.0 - (idx / (total_stocks - 1)) * 0.9
                    else:
                        weight = 1.0
                    
                    # 应用同板压制系数
                    stock_code = stock_codes[stock_id]
                    # 查找该股票昨天所在的梯队
                    cursor.execute('''
                        SELECT ln.node_level
                        FROM ladder_stocks ls
                        JOIN ladder_nodes ln ON ls.ladder_node_id = ln.id
                        JOIN stocks s ON ls.stock_id = s.id
                        WHERE s.code = ? AND ln.date = ?
                    ''', (stock_code, yesterday))
                    node_result = cursor.fetchone()
                    if node_result:
                        node_level = node_result[0]
                        if node_level < max_node_level:
                            # 检查该股票是否在需要压制的同板股票中（非1板梯队）
                            for board_level, board_stocks in board_press_map.items():
                                if board_level == node_level and stock_code in board_stocks:
                                    weight *= self.BOARD_PRESS_WEIGHT
                                    break
                        
                        # 应用节点指引系数：如果竞价记录中有股票在昨天1板梯队中，
                        # 则昨天1板梯队的股票得分乘以节点指引系数
                        if has_1board_guidance and node_level == max_node_level:
                            weight *= self.NODE_GUIDE_WEIGHT
                    
                    for attr in attrs:
                        if attr not in attr_weighted_count:
                            attr_weighted_count[attr] = 0
                        attr_weighted_count[attr] += weight

                # 按加权得分排序
                sorted_attrs = sorted(attr_weighted_count.items(), key=lambda x: x[1], reverse=True)

                self.analysis_result.setRowCount(len(sorted_attrs))
                for i, (attr, count) in enumerate(sorted_attrs):
                    self.analysis_result.setItem(i, 0, QTableWidgetItem(attr))
                    # 显示加权得分，保留2位小数
                    self.analysis_result.setItem(i, 1, QTableWidgetItem(f'{count:.2f}'))

            except Exception as e:
                print(f"Error analyzing bidding: {e}")
                import traceback
                traceback.print_exc()
            finally:
                conn.close()
        except Exception as e:
            print(f"Critical error in analyze_bidding: {e}")
            import traceback
            traceback.print_exc()

    def show_score_detail_dialog(self):
        """显示得分详情对话框"""
        from PyQt5.QtWidgets import QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QTableWidget, QTableWidgetItem, QAbstractItemView

        dialog = QDialog(self)
        dialog.setWindowTitle('得分详情')
        dialog.resize(800, 600)

        # 居中显示
        if self.isVisible():
            dialog.move(
                self.x() + (self.width() - dialog.width()) // 2,
                self.y() + (self.height() - dialog.height()) // 2
            )

        layout = QVBoxLayout(dialog)

        # 计算公式展示区域
        formula_group = QGroupBox('得分计算公式')
        formula_layout = QVBoxLayout(formula_group)
        
        formula_text = QLabel()
        formula_text.setStyleSheet('font-family: monospace; font-size: 12px;')
        formula_text.setText('''
<b>得分计算公式（加法模型，因子归一化）：</b>
最终得分 = 属性得分总和 + 股东持股比例得分 + 市值得分 + 连板数得分 + 同板压制加分 + 节点指引加分
其中：
  属性得分总和 = 一字板属性得分 / NORM_YZ_SCORE（已含一字板系数）
               + 字母属性得分 / NORM_YZ_SCORE（已含字母属性系数）
               + 地区属性得分 / NORM_REGION_SCORE（已含地区属性系数）
               + 抢筹属性得分 = 抢筹字母属性得分 + 抢筹地区属性得分 + 抢筹其他属性得分
                抢筹字母属性得分 = sum(涨幅 × b) × 抢筹字母属性系数
                抢筹地区属性得分 = sum(涨幅 × b) × 抢筹地区属性系数
                抢筹其他属性得分 = sum(涨幅 × b) × 抢筹其他属性总系数
               - 负反馈属性得分 / NORM_FF_SCORE（已含负反馈整体系数）
  股东持股比例得分 = 持股比例 / NORM_HOLDER_RATIO × 股东持股权重
  市值得分 = (市值(亿) / NORM_MARKET_CAP) ^ MARKET_CAP_EXPONENT × 市值权重系数
  连板数得分 = 板数 / NORM_BOARD_COUNT × 每板权重 × 创业板系数
  同板压制加分 = 同板压制系数 / NORM_BOARD_PRESS（适用时）
  节点指引加分 = 节点指引系数 / NORM_NODE_GUIDE（适用时）

<b>归一化说明：</b>
所有输入因子除以各自的归一化最大值，映射到[-1,1]区间，
使各因子量级一致，权重仅代表重要性而非量级补偿。

<b>当前系数值：</b>
- 每板权重 (BOARD_WEIGHT) = {board_weight:.2f}
- 节点指引权重 (NODE_GUIDE_WEIGHT) = {node_guide_weight:.2f}
- 同板压制权重 (BOARD_PRESS_WEIGHT) = {board_press_weight:.2f}
- 股东持股权重 (HOLDER_RATIO_WEIGHT) = {holder_ratio_weight:.2f}
- 市值权重 (MARKET_CAP_WEIGHT) = {market_cap_weight:.4f}
- 市值指数 (MARKET_CAP_EXPONENT) = {market_cap_exponent:.4f}

	- 抢筹-字母属性系数 (RUSH_LETTER_ATTR_WEIGHT) = {rush_letter_attr_coeff:.4f}
	- 抢筹-地区属性系数 (RUSH_REGION_ATTR_WEIGHT) = {rush_region_attr_coeff:.4f}
	- 抢筹市值系数 (RUSH_MARKET_CAP_COEFFICIENT) = {rush_market_cap_coeff:.4f}
	- 涨幅系数 b (RUSH_PCT_COEFFICIENT) = {rush_pct_coeff:.4f}
- 一字板权重 (YZ_OVERALL_WEIGHT) = {yz_overall_weight:.2f}
- 字母属性权重 (LETTER_ATTR_WEIGHT) = {letter_attr_weight:.2f}
- 地区属性权重 (REGION_ATTR_WEIGHT) = {region_attr_weight:.2f}
- 属性数量权重 (ATTR_COUNT_WEIGHT) = {attr_count_weight:.2f}
- 负反馈数量权重 (NEGATIVE_ATTR_COUNT_WEIGHT) = {negative_attr_count_weight:.2f}
- 负反馈其他属性权重 (NEGATIVE_ATTR_WEIGHT) = {negative_attr_weight:.2f}
- 负反馈字母属性权重 (NEGATIVE_LETTER_ATTR_WEIGHT) = {negative_letter_attr_weight:.2f}
- 负反馈地区属性权重 (NEGATIVE_REGION_ATTR_WEIGHT) = {negative_region_attr_weight:.2f}

<b>归一化常量：</b>
- NORM_YZ_SCORE = {norm_yz:.1f}
- NORM_QC_SCORE = {norm_qc:.1f}
- NORM_FF_SCORE = {norm_ff:.0f}
- NORM_BOARD_COUNT = {norm_board:.0f}
- NORM_HOLDER_RATIO = {norm_holder:.0f}
- NORM_MARKET_CAP = {norm_market_cap:.0f}
- NORM_BOARD_PRESS = {norm_bp:.0f}
- NORM_NODE_GUIDE = {norm_ng:.0f}
- NORM_REGION_SCORE = {norm_region:.0f}
'''.format(
    board_weight=self.BOARD_WEIGHT,
    node_guide_weight=self.NODE_GUIDE_WEIGHT,
    board_press_weight=self.BOARD_PRESS_WEIGHT,
    holder_ratio_weight=self.HOLDER_RATIO_WEIGHT,
    market_cap_weight=self.MARKET_CAP_WEIGHT,
    market_cap_exponent=self.MARKET_CAP_EXPONENT,

    rush_pct_coeff=self.RUSH_PCT_COEFFICIENT,
    rush_letter_attr_coeff=self.RUSH_LETTER_ATTR_WEIGHT,
    rush_region_attr_coeff=self.RUSH_REGION_ATTR_WEIGHT,
    rush_market_cap_coeff=self.RUSH_MARKET_CAP_COEFFICIENT,
    yz_overall_weight=self.YZ_OVERALL_WEIGHT,
    letter_attr_weight=self.LETTER_ATTR_WEIGHT,
    region_attr_weight=self.REGION_ATTR_WEIGHT,
    attr_count_weight=self.ATTR_COUNT_WEIGHT,
    negative_attr_count_weight=self.NEGATIVE_ATTR_COUNT_WEIGHT,
    negative_attr_weight=self.NEGATIVE_ATTR_WEIGHT,
	negative_letter_attr_weight=self.NEGATIVE_LETTER_ATTR_WEIGHT,
	negative_region_attr_weight=self.NEGATIVE_REGION_ATTR_WEIGHT,
    norm_yz=NORM_YZ_SCORE,
    norm_qc=NORM_QC_SCORE,
    norm_ff=NORM_FF_SCORE,
    norm_board=NORM_BOARD_COUNT,
    norm_holder=NORM_HOLDER_RATIO,
    norm_market_cap=NORM_MARKET_CAP,
    norm_bp=NORM_BOARD_PRESS,
    norm_ng=NORM_NODE_GUIDE,
    norm_region=NORM_REGION_SCORE,
))
        formula_text.setWordWrap(True)
        formula_layout.addWidget(formula_text)

        # 说明标签
        info_label = QLabel('以下是个股得分计算详情，点击股票可查看详细计算过程')
        layout.addWidget(info_label)

        # 股票列表（左侧）
        stock_list_layout = QHBoxLayout()

        stock_list_widget = QTableWidget()
        stock_list_widget.setColumnCount(3)
        stock_list_widget.setHorizontalHeaderLabels(['股票代码', '股票名称', '总得分'])
        stock_list_widget.setSelectionBehavior(QAbstractItemView.SelectRows)
        stock_list_widget.setEditTriggers(QAbstractItemView.NoEditTriggers)

        # 填充股票列表
        stock_list_widget.setRowCount(len(self.score_details))
        sorted_stocks = sorted(self.score_details.items(), key=lambda x: x[1]['total_score'], reverse=True)
        for i, (code, details) in enumerate(sorted_stocks):
            stock_list_widget.setItem(i, 0, QTableWidgetItem(code))
            stock_list_widget.setItem(i, 1, QTableWidgetItem(details['name']))
            stock_list_widget.setItem(i, 2, QTableWidgetItem(f"{details['total_score']:.2f}"))

        stock_list_widget.resizeColumnToContents(0)
        stock_list_widget.resizeColumnToContents(1)
        stock_list_widget.resizeColumnToContents(2)

        # 详情显示（右侧）
        detail_widget = QTableWidget()
        detail_widget.setColumnCount(2)
        detail_widget.setHorizontalHeaderLabels(['项目', '详情'])
        detail_widget.setEditTriggers(QAbstractItemView.NoEditTriggers)

        def show_stock_detail(code):
            """显示选中股票的详细得分计算过程"""
            if code not in self.score_details:
                return

            details = self.score_details[code]
            detail_widget.setRowCount(0)

            # 基本信息
            rows = [
                ('股票代码', code),
                ('股票名称', details['name']),
                ('竞价抢筹匹配数', str(len(details.get('matching_attrs_qc', [])) + len(details.get('matching_attrs_qc_letter', [])) + len(details.get('matching_attrs_qc_region', [])))),
                ('', ''),
                ('=== 竞价一字板属性 ===', ''),
            ]

            # 竞价一字板属性得分
            attr_scores_yz = details.get('attr_scores_yz', {})
            matching_attrs_yz = details.get('matching_attrs_yz', [])

            for attr in matching_attrs_yz:
                attr_score = attr_scores_yz.get(attr, 0)
                rows.append((f'  {attr}', f'{attr_score:.4f}'))

            rows.append(('', ''))
            rows.append(('=== 字母属性（拼音首字母）===', ''))

            # 字母属性得分
            attr_scores_letter = details.get('attr_scores_letter', {})
            matching_attrs_letter = details.get('matching_attrs_letter', [])

            for attr in matching_attrs_letter:
                attr_score = attr_scores_letter.get(attr, 0)
                rows.append((f'  {attr}', f'{attr_score:.4f}'))

            rows.append(('', ''))
            rows.append(('=== 地区属性（如浙江、江苏等）===', ''))

            # 地区属性得分
            attr_scores_region = details.get('attr_scores_region', {})
            matching_attrs_region = details.get('matching_attrs_region', [])

            for attr in matching_attrs_region:
                attr_score = attr_scores_region.get(attr, 0)
                rows.append((f'  {attr}', f'{attr_score:.4f}'))

            rows.append(('', ''))
            rows.append(('=== 竞价抢筹-字母属性 ===', ''))

            # 竞价抢筹-字母属性得分
            attr_scores_qc_letter = details.get('attr_scores_qc_letter', {})
            matching_attrs_qc_letter = details.get('matching_attrs_qc_letter', [])

            for attr in matching_attrs_qc_letter:
                attr_score = attr_scores_qc_letter.get(attr, 0)
                rows.append((f'  {attr}', f'{attr_score:.4f}'))

            rows.append(('', ''))
            rows.append(('=== 竞价抢筹-地区属性 ===', ''))

            # 竞价抢筹-地区属性得分
            attr_scores_qc_region = details.get('attr_scores_qc_region', {})
            matching_attrs_qc_region = details.get('matching_attrs_qc_region', [])

            for attr in matching_attrs_qc_region:
                attr_score = attr_scores_qc_region.get(attr, 0)
                rows.append((f'  {attr}', f'{attr_score:.4f}'))

            rows.append(('', ''))
            rows.append(('=== 竞价抢筹-其他属性 ===', ''))

            # 竞价抢筹-其他属性得分
            attr_scores_qc = details.get('attr_scores_qc', {})
            matching_attrs_qc = details.get('matching_attrs_qc', [])

            for attr in matching_attrs_qc:
                attr_score = attr_scores_qc.get(attr, 0)
                rows.append((f'  {attr}', f'{attr_score:.4f}'))

            rows.append(('', ''))
            rows.append(('=== 竞价负反馈属性（字母系数）===', ''))
            attr_scores_ff_letter = details.get('attr_scores_ff_letter', {})
            for attr, attr_score in sorted(attr_scores_ff_letter.items()):
                rows.append((f'  {attr}', f'{attr_score:.4f}'))

            rows.append(('=== 竞价负反馈属性（地区系数）===', ''))
            attr_scores_ff_region = details.get('attr_scores_ff_region', {})
            for attr, attr_score in sorted(attr_scores_ff_region.items()):
                rows.append((f'  {attr}', f'{attr_score:.4f}'))

            rows.append(('=== 竞价负反馈属性（其他系数）===', ''))
            attr_scores_ff_other = details.get('attr_scores_ff_other', {})
            for attr, attr_score in sorted(attr_scores_ff_other.items()):
                rows.append((f'  {attr}', f'{attr_score:.4f}'))

            # 属性得分汇总
            attr_total_score = details.get('attr_total_score', 0)
            yz_total = sum(details.get('attr_scores_yz', {}).values())
            letter_total = sum(details.get('attr_scores_letter', {}).values())
            region_total = sum(details.get('attr_scores_region', {}).values())
            qc_letter_total = sum(details.get('attr_scores_qc_letter', {}).values())
            qc_region_total = sum(details.get('attr_scores_qc_region', {}).values())
            qc_other_total = sum(details.get('attr_scores_qc', {}).values())
            qc_total = qc_letter_total + qc_region_total + qc_other_total
            ff_letter_total = sum(details.get('attr_scores_ff_letter', {}).values())
            ff_region_total = sum(details.get('attr_scores_ff_region', {}).values())
            ff_other_total = sum(details.get('attr_scores_ff_other', {}).values())
            
            rows.append(('', ''))
            rows.append(('=== 属性得分汇总 ===', ''))
            rows.append(('  属性得分总和', f"{attr_total_score:.4f}"))
            rows.append(('    = 一字板({:.4f}) + 字母({:.4f}) + 地区({:.4f}) + 抢筹字母({:.4f}) + 抢筹地区({:.4f}) + 抢筹其他({:.4f}) + 负反馈字母({:.4f}) + 负反馈地区({:.4f}) + 负反馈其他({:.4f})'.format(yz_total, letter_total, region_total, qc_letter_total, qc_region_total, qc_other_total, ff_letter_total, ff_region_total, ff_other_total), ''))
            
            # 股东持股比例得分
            rows.append(('', ''))
            rows.append(('=== 股东持股比例得分 ===', ''))
            holder_ratio = details.get('holder_ratio', 0)
            holder_ratio_score = details.get('holder_ratio_score', 0)
            rows.append(('  持股比例', f"{holder_ratio:.4f}"))
            rows.append(('  持股比例得分', f"{holder_ratio_score:.4f}"))
            rows.append(('    = {:.4f} / NORM_HOLDER_RATIO × {:.2f}'.format(holder_ratio, self.HOLDER_RATIO_WEIGHT), ''))

            # 市值得分
            rows.append(('', ''))
            rows.append(('=== 市值得分 ===', ''))
            market_cap_billion = details.get('market_cap_billion', 0)
            market_cap_score = details.get('market_cap_score', 0)
            rows.append(('  市值（亿）', f"{market_cap_billion:.2f}"))
            rows.append(('  市值得分', f"{market_cap_score:.4f}"))
            rows.append(('  市值指数', f"{details.get('market_cap_exponent', self.MARKET_CAP_EXPONENT):.4f}"))
            rows.append(('    = ({:.2f} / NORM_MARKET_CAP) ^ {:.4f} × {:.4f}'.format(market_cap_billion, details.get('market_cap_exponent', self.MARKET_CAP_EXPONENT), self.MARKET_CAP_WEIGHT), ''))

            # 基础得分
            base_score = attr_total_score + holder_ratio_score + market_cap_score
            rows.append(('', ''))
            rows.append(('=== 基础得分 ===', ''))
            rows.append(('  属性得分 + 持股得分 + 市值得分', f"{base_score:.4f}"))
            rows.append(('    = 属性({:.4f}) + 持股({:.4f}) + 市值({:.4f})'.format(attr_total_score, holder_ratio_score, market_cap_score), ''))
            
            # 连板数得分
            rows.append(('', ''))
            rows.append(('=== 连板数得分 ===', ''))
            board_count = details.get('board_count', 0)
            board_term = details.get('board_term', 0)
            rows.append(('  板数', f"{board_count}板"))
            rows.append(('  连板数得分', f"{board_term:.4f}"))
            rows.append(('    = 板数({}) / NORM_BOARD_COUNT({}) × 每板权重({:.4f}) × 创业板系数'.format(board_count, NORM_BOARD_COUNT, self.BOARD_WEIGHT), ''))
            
            # 昨日梯队信息
            rows.append(('', ''))
            rows.append(('=== 昨日梯队信息 ===', ''))
            if board_count > 0:
                yesterday_ladder_count = details.get('yesterday_ladder_count', board_count)
                node_level = details.get('node_level', 0)
                
                conn = sqlite3.connect(self.db_path)
                cursor = conn.cursor()
                try:
                    yesterday = self.bidding_date.date().addDays(-1).toString('yyyy-MM-dd')
                    cursor.execute('SELECT node_name FROM ladder_nodes WHERE date = ? AND node_level = ?', (yesterday, node_level))
                    node_result = cursor.fetchone()
                    node_name = node_result[0] if node_result else ''
                except:
                    node_name = ''
                finally:
                    conn.close()
                
                stock_board = yesterday_ladder_count - node_level + 1
                rows.append(('  昨日所在梯队', f"{stock_board}板"))
                rows.append(('  昨日梯队节点', node_name))
            else:
                rows.append(('  昨日所在梯队', ''))
                rows.append(('  昨日梯队节点', ''))
            
            # 各项加分项
            rows.append(('', ''))
            rows.append(('=== 各项加分项 ===', ''))
            board_press_term = details.get('board_press_term', 0)
            if board_press_term != 0:
                rows.append(('  同板压制加分', f"+{board_press_term:.4f}"))
            node_guide_term = details.get('node_guide_term', 0)
            if node_guide_term != 0:
                rows.append(('  节点指引加分', f"+{node_guide_term:.4f}"))

            # 评价调整
            rows.append(('', ''))
            rows.append(('=== 评价调整 ===', ''))
            rating_score = details.get('rating_score', 1.0)
            rating_text = '好评' if rating_score == 1.0 else '差评'
            rows.append(('  股票评价', f'{rating_text} ({rating_score}x)'))
            
            # 负反馈分类系数调整（预留）
            negative_overall_factor = details.get('negative_overall_factor', 1.0)
            
            # 最终得分计算
            rows.append(('', ''))
            rows.append(('=== 最终得分计算 ===', ''))
            
            # 构建计算表达式
            terms = [f"{base_score:.4f}"]
            if board_term != 0:
                terms.append(f"{board_term:.4f}")
            if board_press_term != 0:
                terms.append(f"{board_press_term:.4f}")
            if node_guide_term != 0:
                terms.append(f"{node_guide_term:.4f}")
            if rating_score != 1.0:
                terms.append(f"×{rating_score}")
            
            calc_expression = ' + '.join(terms) if rating_score == 1.0 else ' + '.join(terms[:-1]) + f' × {rating_score}'
            total_score = details.get('total_score', 0)
            
            rows.append(('  计算过程', calc_expression))
            rows.append(('  最终得分', f"= {total_score:.4f}"))

            # 填充详情表格
            detail_widget.setRowCount(len(rows))
            for i, (item, value) in enumerate(rows):
                detail_widget.setItem(i, 0, QTableWidgetItem(item))
                detail_widget.setItem(i, 1, QTableWidgetItem(value))
                # 设置标题行样式
                if '===' in item:
                    detail_widget.item(i, 0).setBackground(Qt.gray)
                    detail_widget.item(i, 1).setBackground(Qt.gray)

            detail_widget.resizeColumnToContents(0)
            detail_widget.resizeColumnToContents(1)

        # 连接选择事件
        stock_list_widget.itemClicked.connect(lambda item: show_stock_detail(stock_list_widget.item(item.row(), 0).text()))

        # 默认显示第一个股票的详情
        if sorted_stocks:
            stock_list_widget.selectRow(0)
            show_stock_detail(sorted_stocks[0][0])

        # 左右布局
        left_frame = QVBoxLayout()
        left_frame.addWidget(QLabel('股票列表'))
        left_frame.addWidget(stock_list_widget)

        right_frame = QVBoxLayout()
        right_frame.addWidget(QLabel('得分计算详情'))
        right_frame.addWidget(detail_widget)

        split_layout = QHBoxLayout()
        split_layout.addLayout(left_frame, 1)
        split_layout.addLayout(right_frame, 2)

        layout.addLayout(split_layout)

        # 关闭按钮
        close_btn = QPushButton('关闭')
        close_btn.clicked.connect(dialog.close)
        layout.addWidget(close_btn)

        dialog.exec_()

    def analyze_correlation(self):
        try:
            today = self.bidding_date.date()
            yesterday = today.addDays(-1)
            yesterday_str = yesterday.toString('yyyy-MM-dd')
            today_str = today.toString('yyyy-MM-dd')

            print(f"Today: {today_str}, Yesterday: {yesterday_str}")

            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()

            try:
                # 应用因子治理：禁用的因子强制为0（加法权重）或1（乘法因子）
                _gov_backup = {}
                _gov_enabled = getattr(self, '_factor_enabled', {})
                _gov_mul = {'board_press_weight', 'node_guide_weight'}
                for _key, _attr in [('stock_weight_first', 'STOCK_WEIGHT_FIRST'),
                                    ('stock_weight_last', 'STOCK_WEIGHT_LAST'),
                                    ('board_weight', 'BOARD_WEIGHT'),

                            ('rush_pct_coefficient', 'RUSH_PCT_COEFFICIENT'),
                                ('rush_letter_attr_weight', 'RUSH_LETTER_ATTR_WEIGHT'),
                                ('rush_region_attr_weight', 'RUSH_REGION_ATTR_WEIGHT'),
                                ('rush_market_cap_coefficient', 'RUSH_MARKET_CAP_COEFFICIENT'),
                                    ('yz_overall_weight', 'YZ_OVERALL_WEIGHT'),
                                    ('letter_attr_weight', 'LETTER_ATTR_WEIGHT'),
                                    ('region_attr_weight', 'REGION_ATTR_WEIGHT'),
                                    ('attr_count_weight', 'ATTR_COUNT_WEIGHT'),
                                    ('negative_attr_count_weight', 'NEGATIVE_ATTR_COUNT_WEIGHT'),
                                    ('negative_attr_weight', 'NEGATIVE_ATTR_WEIGHT'),
							('negative_letter_attr_weight', 'NEGATIVE_LETTER_ATTR_WEIGHT'),
							('negative_region_attr_weight', 'NEGATIVE_REGION_ATTR_WEIGHT'),
                                    ('board_press_weight', 'BOARD_PRESS_WEIGHT'),
                                    ('node_guide_weight', 'NODE_GUIDE_WEIGHT'),
                                    ('holder_ratio_weight', 'HOLDER_RATIO_WEIGHT'),
                                    ('market_cap_weight', 'MARKET_CAP_WEIGHT'),
                                    ('market_cap_exponent', 'MARKET_CAP_EXPONENT')]:
                    if not _gov_enabled.get(_key, True):
                        _gov_backup[_attr] = getattr(self, _attr)
                        setattr(self, _attr, 1.0 if _key in _gov_mul else 0.0)

                # 获取竞价记录（仅一字板和负反馈），计算每个属性的数量（加权得分）
                cursor.execute('''
                    SELECT br.id, sa.attribute, br.is_additive, br.type
                    FROM bidding_records br
                    JOIN stock_attributes sa ON br.stock_id = sa.stock_id
                    WHERE br.date = ? AND br.type IN (1, 3)
                    ORDER BY br.id
                ''', (today_str,))
                records = cursor.fetchall()

                # 计算加权得分
                # 同时考虑股票顺序和属性出现次数
                attr_weighted_count = {}
                attr_weighted_count_yz = {}  # 一字板属性得分
                attr_weighted_count_letter = {}  # 字母属性得分（拼音首字母）
                attr_weighted_count_region = {}  # 地区属性得分（如浙江、江苏等）
                attr_weighted_count_qc = {}  # 抢筹其他属性得分
                attr_weighted_count_qc_letter = {}  # 抢筹字母属性得分
                attr_weighted_count_qc_region = {}  # 抢筹地区属性得分
                attr_weighted_count_ff = {}  # 负反馈属性得分
                
                # 按股票分组
                stock_attrs = {}
                stock_attrs_yz = {}
                stock_attrs_ff = {}
                stock_is_additive = {}
                for record_id, attr, is_additive, type in records:
                    if record_id not in stock_attrs:
                        stock_attrs[record_id] = []
                        stock_attrs_yz[record_id] = []
                        stock_attrs_ff[record_id] = []
                        stock_is_additive[record_id] = is_additive
                    stock_attrs[record_id].append(attr)
                    if type == 1:
                        stock_attrs_yz[record_id].append(attr)
                    elif type == 3:
                        stock_attrs_ff[record_id].append(attr)
                
                # 计算每个股票的权重（基于顺序）
                total_stocks = len(stock_attrs)
                stock_weights = {}
                for idx, stock_id in enumerate(stock_attrs.keys()):
                    # 权重计算：越靠前权重越高
                    if total_stocks > 1:
                        weight_range = self.STOCK_WEIGHT_FIRST - self.STOCK_WEIGHT_LAST
                        weight = self.STOCK_WEIGHT_FIRST - (idx / (total_stocks - 1)) * weight_range
                    else:
                        weight = self.STOCK_WEIGHT_FIRST
                    stock_weights[stock_id] = weight
                
                # 计算每个属性的总权重（股票权重之和）
                for stock_id, attrs in stock_attrs.items():
                    weight = stock_weights[stock_id]
                    is_additive = stock_is_additive[stock_id]
                    # 根据是加分项还是减分项调整权重
                    weight_factor = 1 if is_additive else -1
                    
                    # 一字板属性 - 去重处理，每个属性最多计1次；将字母属性（拼音首字母）与非字母属性分离
                    unique_yz_attrs = set(stock_attrs_yz[stock_id])  # 去重
                    for attr in unique_yz_attrs:
                        if attr not in attr_weighted_count:
                            attr_weighted_count[attr] = 0
                        attr_weighted_count[attr] += weight * weight_factor
                        if len(attr) == 1 and attr.isalpha():
                            # 字母属性（拼音首字母，如Z、F、J、K等）
                            if attr not in attr_weighted_count_letter:
                                attr_weighted_count_letter[attr] = 0
                            attr_weighted_count_letter[attr] += weight * weight_factor
                        elif attr in REGION_NAMES:
                            # 地区属性（如浙江、江苏、广东等）
                            if attr not in attr_weighted_count_region:
                                attr_weighted_count_region[attr] = 0
                            attr_weighted_count_region[attr] += weight * weight_factor
                        else:
                            # 非字母一字板属性
                            if attr not in attr_weighted_count_yz:
                                attr_weighted_count_yz[attr] = 0
                            attr_weighted_count_yz[attr] += weight * weight_factor
                    
                    # 负反馈属性 - 按分类使用各自的系数（字母、地区、其他）
                    for attr in stock_attrs_ff[stock_id]:
                        if attr not in attr_weighted_count_ff:
                            attr_weighted_count_ff[attr] = 0
                        if attr not in attr_weighted_count:
                            attr_weighted_count[attr] = 0
                        if len(attr) == 1 and attr.isalpha():
                            ff_score = weight * (-1) * self.NEGATIVE_LETTER_ATTR_WEIGHT
                        elif attr in REGION_NAMES:
                            ff_score = weight * (-1) * self.NEGATIVE_REGION_ATTR_WEIGHT
                        else:
                            ff_score = weight * (-1) * self.NEGATIVE_ATTR_WEIGHT
                        attr_weighted_count[attr] += ff_score
                        attr_weighted_count_ff[attr] += ff_score

                # 读取竞价抢筹股票，按其属性加权计算抢筹得分
                cursor.execute('''
                    SELECT brs.pct_change, s.id, s.total_market_cap
                    FROM bidding_rush_stocks brs
                    JOIN stocks s ON brs.stock_id = s.id
                    WHERE brs.date = ?
                    ORDER BY brs.pct_change DESC
                ''', (today_str,))
                rush_stock_data = cursor.fetchall()
                # skip rush scoring when all rush factors disabled by governance
                _all_rush_disabled = (
                    self.RUSH_PCT_COEFFICIENT == 0
                    and self.RUSH_LETTER_ATTR_WEIGHT == 0
                    and self.RUSH_REGION_ATTR_WEIGHT == 0
                    and self.RUSH_MARKET_CAP_COEFFICIENT == 0
                )
                if not _all_rush_disabled:
                    for idx, (pct_change, stock_id, total_market_cap) in enumerate(rush_stock_data):
                        # 获取该抢筹股票的属性
                        cursor.execute('SELECT attribute FROM stock_attributes WHERE stock_id = ?', (stock_id,))
                        for row in cursor.fetchall():
                            attr = row[0]
                            if attr in ATTR_REMOVE_SET:
                                continue
                            # 抢筹加分 = 涨幅 x 涨幅系数b x 市值因子（暂不乘属性系数，在第二遍乘以各自的分类系数）
                            market_cap_billion = (total_market_cap / 100000000) if total_market_cap else 50.0
                            rush_score_base = pct_change * self.RUSH_PCT_COEFFICIENT * (market_cap_billion * self.RUSH_MARKET_CAP_COEFFICIENT)
                            if attr not in attr_weighted_count:
                                attr_weighted_count[attr] = 0
                            attr_weighted_count[attr] += rush_score_base
                            if len(attr) == 1 and attr.isalpha():
                                # 字母属性
                                if attr not in attr_weighted_count_qc_letter:
                                    attr_weighted_count_qc_letter[attr] = 0
                                attr_weighted_count_qc_letter[attr] += rush_score_base
                            elif attr in REGION_NAMES:
                                # 地区属性
                                if attr not in attr_weighted_count_qc_region:
                                    attr_weighted_count_qc_region[attr] = 0
                                attr_weighted_count_qc_region[attr] += rush_score_base
                            else:
                                # 其他属性
                                if attr not in attr_weighted_count_qc:
                                    attr_weighted_count_qc[attr] = 0
                                attr_weighted_count_qc[attr] += rush_score_base

                # 打印属性得分，用于调试
                print("Attribute scores:")
                for attr, score in attr_weighted_count.items():
                    yz_score = attr_weighted_count_yz.get(attr, 0)
                    qc_score = attr_weighted_count_qc.get(attr, 0)
                    qc_letter_score = attr_weighted_count_qc_letter.get(attr, 0)
                    qc_region_score = attr_weighted_count_qc_region.get(attr, 0)
                    ff_score = attr_weighted_count_ff.get(attr, 0)
                    print(f"  {attr}: total={score:.2f} (yz={yz_score:.2f}, qc_letter={qc_letter_score:.4f}, qc_region={qc_region_score:.4f}, qc_other={qc_score:.4f}, ff={ff_score:.2f})")
                
                # 计算每个属性的出现次数（分别计算加分和减分）
                attr_additive_counts = {}
                attr_subtractive_counts = {}
                for record_id, attr, is_additive, type in records:
                    if is_additive:
                        attr_additive_counts[attr] = attr_additive_counts.get(attr, 0) + 1
                    else:
                        attr_subtractive_counts[attr] = attr_subtractive_counts.get(attr, 0) + 1
                
                # 最终加权得分 = 属性总权重 * (加分次数 - 减分次数) * 属性数量差距系数
                # 计算属性的总出现次数（绝对值）
                attr_total_counts = {}
                for attr in attr_additive_counts:
                    attr_total_counts[attr] = attr_additive_counts.get(attr, 0) + attr_subtractive_counts.get(attr, 0)
                for attr in attr_subtractive_counts:
                    if attr not in attr_total_counts:
                        attr_total_counts[attr] = attr_additive_counts.get(attr, 0) + attr_subtractive_counts.get(attr, 0)
                
                # 计算最终得分
                for attr in attr_weighted_count:
                    add_count = attr_additive_counts.get(attr, 0)
                    sub_count = attr_subtractive_counts.get(attr, 0)
                    net_count = add_count - sub_count
                    total_count = attr_total_counts.get(attr, 0)
                    
                    # 应用属性数量差距系数：次数越多，放大效果越明显（抢筹和负反馈使用）
                    count_factor = total_count ** self.ATTR_COUNT_WEIGHT if total_count > 0 else 1
                    # 负反馈属性使用单独的系数
                    negative_count_factor = total_count ** self.NEGATIVE_ATTR_COUNT_WEIGHT if total_count > 0 else 1
                    
                    # 应用到主属性得分字典（不应用数量差距系数）
                    attr_weighted_count[attr] *= net_count
                    
                    # 应用到一字板属性得分字典 - 只与股票顺序有关，不考虑属性数量，只应用一字板整体系数
                    if attr in attr_weighted_count_yz:
                        # 不乘以 net_count，只应用一字板整体系数
                        attr_weighted_count_yz[attr] *= self.YZ_OVERALL_WEIGHT
                    
                    # 应用到字母属性得分字典 - 使用字母属性系数
                    if attr in attr_weighted_count_letter:
                        attr_weighted_count_letter[attr] *= self.LETTER_ATTR_WEIGHT
                    
                    # 应用到地区属性得分字典 - 使用地区属性系数
                    if attr in attr_weighted_count_region:
                        attr_weighted_count_region[attr] *= self.REGION_ATTR_WEIGHT
                    
                    # 应用到抢筹属性得分字典 - 按分类应用各自的属性系数
                    if attr in attr_weighted_count_qc_letter:
                        attr_weighted_count_qc_letter[attr] *= self.RUSH_LETTER_ATTR_WEIGHT
                    if attr in attr_weighted_count_qc_region:
                        attr_weighted_count_qc_region[attr] *= self.RUSH_REGION_ATTR_WEIGHT

                    # 应用到负反馈属性得分字典 - 应用negative_count_factor，保持负号
                    if attr in attr_weighted_count_ff:
                        # 计算绝对值，应用negative_count_factor，再保持负号
                        abs_score = abs(attr_weighted_count_ff[attr])
                        attr_weighted_count_ff[attr] = -abs_score * negative_count_factor
                    
                    print(f"Attribute: {attr}, Add count: {add_count}, Sub count: {sub_count}, Total count: {total_count}, Count factor: {count_factor}, Negative count factor: {negative_count_factor}, Final score: {attr_weighted_count[attr]}")

                # 提取分析属性集合
                analysis_attrs = set(attr_weighted_count.keys())

                print(f"Analysis attrs count: {len(analysis_attrs)}")

                if not analysis_attrs:
                    print("No analysis attrs, returning early")
                    self.correlation_result.setRowCount(0)
                    conn.close()
                    return

                use_ladder = getattr(self, 'use_ladder_checkbox', None) and self.use_ladder_checkbox.isChecked()
                weak_to_strong = getattr(self, 'weak_to_strong_checkbox', None) and self.weak_to_strong_checkbox.isChecked()
                print(f"Use ladder: {use_ladder}, Weak to strong: {weak_to_strong}")
                
                # 打印梯队级别选择
                if hasattr(self, 'ladder_level_combo'):
                    ladder_level_option = self.ladder_level_combo.currentText()
                    ladder_level = self.ladder_level_input.value() if ladder_level_option == '自定义' else '所有'
                    print(f"Ladder level option: {ladder_level_option}, Selected level: {ladder_level}")
                
                yesterday_ladder_count = 0

                if weak_to_strong:
                    # 弱转强分析：从两天前在涨停梯队且昨天不在涨停梯队中的股票里选择
                    two_days_ago = (datetime.strptime(today_str, '%Y-%m-%d') - timedelta(days=2)).strftime('%Y-%m-%d')
                    print(f"Weak to strong analysis - two days ago: {two_days_ago}, yesterday: {yesterday_str}")
                    
                    # 获取两天前在涨停梯队中的股票
                    include_gem = getattr(self, 'include_gem_checkbox', None) and self.include_gem_checkbox.isChecked()
                    if include_gem:
                        cursor.execute('''
                            SELECT s.code, s.name, ln.node_level
                            FROM ladder_stocks ls
                            JOIN ladder_nodes ln ON ls.ladder_node_id = ln.id
                            JOIN stocks s ON ls.stock_id = s.id
                            WHERE ln.date = ?
                        ''', (two_days_ago,))
                    else:
                        cursor.execute('''
                            SELECT s.code, s.name, ln.node_level
                            FROM ladder_stocks ls
                            JOIN ladder_nodes ln ON ls.ladder_node_id = ln.id
                            JOIN stocks s ON ls.stock_id = s.id
                            WHERE ln.date = ? AND s.code NOT LIKE '300%'
                        ''', (two_days_ago,))
                    two_days_ago_stocks = set([(row[0], row[1], row[2]) for row in cursor.fetchall()])
                    print(f"Two days ago ({two_days_ago}) ladder stocks: {len(two_days_ago_stocks)}")
                    
                    # 获取昨天在涨停梯队中的股票
                    if include_gem:
                        cursor.execute('''
                            SELECT s.code, s.name, ln.node_level
                            FROM ladder_stocks ls
                            JOIN ladder_nodes ln ON ls.ladder_node_id = ln.id
                            JOIN stocks s ON ls.stock_id = s.id
                            WHERE ln.date = ?
                        ''', (yesterday_str,))
                    else:
                        cursor.execute('''
                            SELECT s.code, s.name, ln.node_level
                            FROM ladder_stocks ls
                            JOIN ladder_nodes ln ON ls.ladder_node_id = ln.id
                            JOIN stocks s ON ls.stock_id = s.id
                            WHERE ln.date = ? AND s.code NOT LIKE '300%'
                        ''', (yesterday_str,))
                    yesterday_stocks = set([row[0] for row in cursor.fetchall()])
                    print(f"Yesterday ({yesterday_str}) ladder stocks: {len(yesterday_stocks)}")
                    
                    # 筛选：两天前在梯队但昨天不在梯队的股票
                    target_stocks = [stock for stock in two_days_ago_stocks if stock[0] not in yesterday_stocks]
                    print(f"Weak to strong candidates: {len(target_stocks)}")
                    
                    # 如果没有符合条件的股票，显示提示
                    if not target_stocks:
                        print("No weak to strong stocks found")
                        message = f"没有找到符合条件的弱转强股票\n\n"\
                                f"分析日期：{today_str}\n"\
                                f"两天前：{two_days_ago}（{len(two_days_ago_stocks)}只股票在涨停梯队）\n"\
                                f"昨天：{yesterday_str}（{len(yesterday_stocks)}只股票在涨停梯队）\n\n"\
                                f"条件：两天前在涨停梯队且昨天不在涨停梯队\n\n"\
                                f"可能原因：\n"\
                                f"1. 两天前没有涨停梯队数据\n"\
                                f"2. 所有两天前涨停的股票昨天继续涨停\n"\
                                f"3. 日期数据不完整"
                        QMessageBox.information(self, "提示", message)
                        self.correlation_result.setRowCount(0)
                        conn.close()
                        return
                    
                    # 获取两天前的梯队数量
                    cursor.execute('SELECT ladder_count FROM ladder_settings WHERE date = ?', (two_days_ago,))
                    two_days_ago_setting = cursor.fetchone()
                    yesterday_ladder_count = two_days_ago_setting[0] if two_days_ago_setting else 0
                    
                    if yesterday_ladder_count == 0:
                        cursor.execute('SELECT COUNT(*) FROM ladder_nodes WHERE date = ?', (two_days_ago,))
                        node_count = cursor.fetchone()[0]
                        yesterday_ladder_count = node_count
                    
                    print(f"Two days ago ({two_days_ago}) ladder count: {yesterday_ladder_count}")
                    print(f"Weak to strong stocks count: {len(target_stocks)}")
                    
                elif use_ladder:
                    # 获取昨天的梯队数量
                    cursor.execute('SELECT ladder_count FROM ladder_settings WHERE date = ?', (yesterday_str,))
                    yesterday_ladder_setting = cursor.fetchone()
                    yesterday_ladder_count = yesterday_ladder_setting[0] if yesterday_ladder_setting else 0
                    
                    # 如果没有设置，检查节点数量
                    if yesterday_ladder_count == 0:
                        cursor.execute('SELECT COUNT(*) FROM ladder_nodes WHERE date = ?', (yesterday_str,))
                        node_count = cursor.fetchone()[0]
                        yesterday_ladder_count = node_count
                    
                    print(f"Yesterday ({yesterday_str}) ladder count: {yesterday_ladder_count}")
                    
                    # 从昨天涨停梯队中分析，排除已经在竞价记录中的股票，同时获取node_level
                    # 根据选择的梯队级别过滤
                    ladder_level_option = getattr(self, 'ladder_level_combo', None).currentText() if hasattr(self, 'ladder_level_combo') else '所有'
                    ladder_level = getattr(self, 'ladder_level_input', None).value() if hasattr(self, 'ladder_level_input') else 1
                    
                    if ladder_level_option == '所有':
                        # 选择所有梯队
                        cursor.execute('''
                            SELECT s.code, s.name, ln.node_level
                            FROM ladder_stocks ls
                            JOIN ladder_nodes ln ON ls.ladder_node_id = ln.id
                            JOIN stocks s ON ls.stock_id = s.id
                            WHERE ln.date = ?
                            AND s.id NOT IN (
                                SELECT br.stock_id 
                                FROM bidding_records br 
                                WHERE br.date = ?
                            )
                        ''', (yesterday_str, today_str))
                    else:
                        # 选择自定义梯队
                        # 计算对应的node_level：yesterday_ladder_count - (board_count - 1)
                        target_board_count = ladder_level
                        target_node_level = yesterday_ladder_count - (target_board_count - 1)
                        
                        cursor.execute('''
                            SELECT s.code, s.name, ln.node_level
                            FROM ladder_stocks ls
                            JOIN ladder_nodes ln ON ls.ladder_node_id = ln.id
                            JOIN stocks s ON ls.stock_id = s.id
                            WHERE ln.date = ?
                            AND ln.node_level = ?
                            AND s.id NOT IN (
                                SELECT br.stock_id 
                                FROM bidding_records br 
                                WHERE br.date = ?
                            )
                        ''', (yesterday_str, target_node_level, today_str))
                    target_stocks = cursor.fetchall()
                    print(f"Yesterday ({yesterday_str}) ladder stocks (excluding bidding): {len(target_stocks)}")
                else:
                    # 从所有股票中分析
                    # 排除已经在竞价记录中的股票
                    cursor.execute('''
                        SELECT s.code, s.name, 0 as node_level
                        FROM stocks s
                        WHERE s.id NOT IN (
                            SELECT br.stock_id 
                            FROM bidding_records br 
                            WHERE br.date = ?
                        )
                    ''', (today_str,))
                    target_stocks = cursor.fetchall()
                    print(f"All stocks (excluding bidding records): {len(target_stocks)}")

                # 过滤科创股
                include_tech = getattr(self, 'include_tech_checkbox', None) and self.include_tech_checkbox.isChecked()
                if not include_tech:
                    original_count = len(target_stocks)
                    target_stocks = [(code, name, level) for code, name, level in target_stocks if not code.startswith('688')]
                    print(f"Filtered out 688 stocks: {original_count} -> {len(target_stocks)}")

                # 过滤创业板
                include_gem = getattr(self, 'include_gem_checkbox', None) and self.include_gem_checkbox.isChecked()
                if not include_gem:
                    original_count = len(target_stocks)
                    target_stocks = [(code, name, level) for code, name, level in target_stocks if not code.startswith('300')]
                    print(f"Filtered out 300 stocks: {original_count} -> {len(target_stocks)}")

                # 获取最大node_level和同板压制数据（根据分析模式选择日期）
                if weak_to_strong:
                    # 弱转强分析：使用两天前的数据计算板数，但不应用同板压制
                    cursor.execute('SELECT MAX(node_level) FROM ladder_nodes WHERE date = ?', (two_days_ago,))
                    max_node_level_result = cursor.fetchone()
                    max_node_level = max_node_level_result[0] if max_node_level_result and max_node_level_result[0] else 0
                    board_press_map = {}  # 弱转强分析不使用同板压制
                else:
                    # 获取昨天涨停梯队的最大node_level（1板梯队，板数最低）
                    cursor.execute('SELECT MAX(node_level) FROM ladder_nodes WHERE date = ?', (yesterday_str,))
                    max_node_level_result = cursor.fetchone()
                    max_node_level = max_node_level_result[0] if max_node_level_result and max_node_level_result[0] else 0
                    
                    # 获取今天一字板中在昨天涨停梯队（非1板，即node_level < max）的股票及其所在梯队
                    # 因为node_level=1是最高板，数字越大板数越低，所以1板梯队是node_level最大的
                    if max_node_level > 1:
                        cursor.execute('''
                            SELECT s.code, ln.node_level
                            FROM bidding_records br
                            JOIN stocks s ON br.stock_id = s.id
                            JOIN ladder_stocks ls ON ls.stock_id = s.id
                            JOIN ladder_nodes ln ON ls.ladder_node_id = ln.id
                            WHERE br.date = ? AND br.type = 1
                            AND ln.date = ? AND ln.node_level < ?
                        ''', (today_str, yesterday_str, max_node_level))
                        yz_board_stocks = cursor.fetchall()
                    else:
                        yz_board_stocks = []
                    
                    # 构建同板压制映射：{梯队级别: [同板股票代码]}
                    board_press_map = {}
                    for code, node_level in yz_board_stocks:
                        if node_level not in board_press_map:
                            # 获取该梯队的所有股票
                            cursor.execute('''
                                SELECT s.code
                                FROM ladder_stocks ls
                                JOIN ladder_nodes ln ON ls.ladder_node_id = ln.id
                                JOIN stocks s ON ls.stock_id = s.id
                                WHERE ln.date = ? AND ln.node_level = ?
                            ''', (yesterday_str, node_level))
                            board_stocks = [row[0] for row in cursor.fetchall()]
                            board_press_map[node_level] = board_stocks
                    
                    # 获取昨天1板和2板梯队的股票（node_level最大的两个梯队）
                    yesterday_12board_stocks = []
                    if max_node_level > 0:
                        # node_level越大板数越低，max_node_level是1板，max_node_level-1是2板
                        cursor.execute('''
                            SELECT s.code
                            FROM ladder_stocks ls
                            JOIN ladder_nodes ln ON ls.ladder_node_id = ln.id
                            JOIN stocks s ON ls.stock_id = s.id
                            WHERE ln.date = ? AND ln.node_level >= ?
                        ''', (yesterday_str, max_node_level - 1))
                        yesterday_12board_stocks = [row[0] for row in cursor.fetchall()]
                    
                    # 检查竞价记录中是否有股票在昨天1板或2板梯队中
                    bidding_codes = []
                    cursor.execute('SELECT s.code FROM bidding_records br JOIN stocks s ON br.stock_id = s.id WHERE br.date = ?', (today_str,))
                    for row in cursor.fetchall():
                        bidding_codes.append(row[0])
                    has_1board_guidance = any(code in yesterday_12board_stocks for code in bidding_codes)

                for code, name, node_level in target_stocks[:5]:  # Show first 5
                    # 计算实际的板数：yesterday_ladder_count - (node_level - 1)
                    board_count = yesterday_ladder_count - (node_level - 1)
                    print(f"  - {code} {name} (Node level: {node_level}, Board count: {board_count})")

                scores = []
                # 清空之前的得分详情
                self.score_details = {}

                for code, name, node_level in target_stocks:
                    # 获取股票ID
                    cursor.execute('SELECT id FROM stocks WHERE code = ?', (code,))
                    stock_result = cursor.fetchone()
                    if not stock_result:
                        continue
                    stock_id = stock_result[0]
                    
                    is_rushing = False  # 已废弃：改为属性维度抢筹

                    # 获取股票属性
                    cursor.execute('''
                        SELECT attribute
                        FROM stock_attributes
                        WHERE stock_id = ?
                    ''', (stock_id,))
                    stock_attrs = set([row[0] for row in cursor.fetchall()])

                    # 计算匹配的属性的加权得分
                    matching_attrs_yz = []  # 一字板匹配属性
                    matching_attrs_letter = []  # 字母属性匹配
                    matching_attrs_region = []  # 地区属性匹配
                    matching_attrs_qc = []  # 抢筹其他属性匹配
                    matching_attrs_qc_letter = []  # 抢筹字母属性匹配
                    matching_attrs_qc_region = []  # 抢筹地区属性匹配
                    matching_attrs_ff = []  # 负反馈匹配属性
                    negative_attrs = []
                    attr_total = 0  # 属性得分总和
                    attr_scores_yz = {}  # 一字板属性得分
                    attr_scores_letter = {}  # 字母属性得分
                    attr_scores_region = {}  # 地区属性得分
                    attr_scores_qc = {}  # 抢筹其他属性得分
                    attr_scores_qc_letter = {}  # 抢筹字母属性得分
                    attr_scores_qc_region = {}  # 抢筹地区属性得分
                    attr_scores_ff = {}  # 负反馈属性得分
                    attr_scores_ff_letter = {}  # 负反馈字母属性
                    attr_scores_ff_region = {}  # 负反馈地区属性
                    attr_scores_ff_other = {}  # 负反馈其他属性
                    for attr in stock_attrs:
                        if attr in attr_weighted_count_yz and attr in attr_weighted_count:
                            attr_score = attr_weighted_count_yz[attr] / NORM_YZ_SCORE
                            attr_total += attr_score
                            matching_attrs_yz.append(attr)
                            attr_scores_yz[attr] = attr_score
                        if attr in attr_weighted_count_letter and attr in attr_weighted_count:
                            attr_score = attr_weighted_count_letter[attr] / NORM_YZ_SCORE
                            attr_total += attr_score
                            matching_attrs_letter.append(attr)
                            attr_scores_letter[attr] = attr_score
                        if attr in attr_weighted_count_region and attr in attr_weighted_count:
                            attr_score = attr_weighted_count_region[attr] / NORM_REGION_SCORE
                            attr_total += attr_score
                            matching_attrs_region.append(attr)
                            attr_scores_region[attr] = attr_score
                        if attr in attr_weighted_count_qc_letter and attr in attr_weighted_count:
                            attr_score = attr_weighted_count_qc_letter[attr]
                            attr_total += attr_score
                            matching_attrs_qc_letter.append(attr)
                            attr_scores_qc_letter[attr] = attr_score
                        if attr in attr_weighted_count_qc_region and attr in attr_weighted_count:
                            attr_score = attr_weighted_count_qc_region[attr]
                            attr_total += attr_score
                            matching_attrs_qc_region.append(attr)
                            attr_scores_qc_region[attr] = attr_score
                        if attr in attr_weighted_count_qc and attr in attr_weighted_count:
                            attr_score = attr_weighted_count_qc[attr]
                            attr_total += attr_score
                            matching_attrs_qc.append(attr)
                            attr_scores_qc[attr] = attr_score
                        if attr in attr_weighted_count_ff and attr in attr_weighted_count:
                            attr_score = attr_weighted_count_ff[attr] / NORM_FF_SCORE
                            attr_total += attr_score
                            matching_attrs_ff.append(attr)
                            attr_scores_ff[attr] = attr_score
                            if attr_score < 0:
                                negative_attrs.append(attr)
                            # 按分类保存用于显示区分
                            if len(attr) == 1 and attr.isalpha():
                                attr_scores_ff_letter[attr] = attr_score
                            elif attr in REGION_NAMES:
                                attr_scores_ff_region[attr] = attr_score
                            else:
                                attr_scores_ff_other[attr] = attr_score

                    # 负反馈整体系数已在属性计算时应用
                    negative_overall_factor = 1.0

                    # 获取股东持股比例得分
                    holder_ratio_score = 0
                    cursor.execute('SELECT holder_ratio FROM stocks WHERE id = ?', (stock_id,))
                    holder_result = cursor.fetchone()
                    if holder_result and holder_result[0] is not None:
                        holder_ratio_score = holder_result[0] * self.HOLDER_RATIO_WEIGHT / NORM_HOLDER_RATIO

                    # 获取市值得分（市值去掉亿，如100亿按100计算）
                    market_cap_score = 0
                    cursor.execute('SELECT total_market_cap FROM stocks WHERE id = ?', (stock_id,))
                    market_cap_result = cursor.fetchone()
                    if market_cap_result and market_cap_result[0] is not None:
                        market_cap_billion = market_cap_result[0] / 100000000
                        market_cap_score = (market_cap_billion / NORM_MARKET_CAP) ** self.MARKET_CAP_EXPONENT * self.MARKET_CAP_WEIGHT

                    # 基础得分 = 属性得分总和 + 股东持股比例得分 + 市值得分
                    base_score = attr_total + holder_ratio_score + market_cap_score

                    # 计算板数相关参数
                    board_count = 1  # 默认板数为1
                    gem_factor = 1   # 默认创业板系数为1

                    if (use_ladder or weak_to_strong) and node_level > 0:
                        # 计算实际的板数：yesterday_ladder_count - (node_level - 1)
                        board_count = yesterday_ladder_count - (node_level - 1)
                        # 创业板股票（代码以300开头）的板数得分乘以创业板系数
                        gem_factor = self.GEM_FACTOR if code.startswith('300') else 1

                    # 应用同板压制加分（弱转强分析不应用，非1板、2板梯队才使用）
                    board_press_term = 0
                    if use_ladder and not weak_to_strong and node_level < max_node_level and node_level > 2:
                        for board_level, board_stocks in board_press_map.items():
                            if board_level == node_level and code in board_stocks:
                                board_press_term = self.BOARD_PRESS_WEIGHT / NORM_BOARD_PRESS
                                break

                    # 应用节点指引加分（弱转强分析不应用）
                    node_guide_term = 0
                    if use_ladder and not weak_to_strong and max_node_level > 0:
                        if has_1board_guidance and node_level >= max_node_level - 1:
                            node_guide_term = self.NODE_GUIDE_WEIGHT / NORM_NODE_GUIDE

                    # 连板数得分（归一化）
                    board_term = (board_count / NORM_BOARD_COUNT) * gem_factor * self.BOARD_WEIGHT

                    # 最终得分 = 各分量加和
                    score = base_score + board_term + board_press_term + node_guide_term

                    # 检查股票是否被打了差评，如果是，权重降低
                    rating_score = 1.0
                    cursor.execute('SELECT rating FROM stock_ratings WHERE stock_id = ?', (stock_id,))
                    rating_result = cursor.fetchone()
                    if rating_result and rating_result[0] == -1:
                        rating_score = 0.1
                        score *= rating_score

                    # 保存得分详情
                    self.score_details[code] = {
                        'name': name,
                        'stock_id': stock_id,
                        'is_rushing': is_rushing,
                        'attr_scores_yz': attr_scores_yz,
                        'attr_scores_letter': attr_scores_letter,
                        'attr_scores_region': attr_scores_region,
                        'attr_scores_qc': attr_scores_qc,
                        'attr_scores_qc_letter': attr_scores_qc_letter,
                        'attr_scores_qc_region': attr_scores_qc_region,
                        'attr_scores_ff': attr_scores_ff,
                        'attr_scores_ff_letter': attr_scores_ff_letter,
                        'attr_scores_ff_region': attr_scores_ff_region,
                        'attr_scores_ff_other': attr_scores_ff_other,
                        'matching_attrs_yz': matching_attrs_yz,
                        'matching_attrs_letter': matching_attrs_letter,
                        'matching_attrs_region': matching_attrs_region,
                        'matching_attrs_qc': matching_attrs_qc,
                        'matching_attrs_qc_letter': matching_attrs_qc_letter,
                        'matching_attrs_qc_region': matching_attrs_qc_region,
                        'matching_attrs_ff': matching_attrs_ff,
                        'negative_attrs': negative_attrs,
                        'board_count': board_count,
                        'board_term': board_term,
                        'board_press_term': board_press_term,
                        'node_guide_term': node_guide_term,
                        'rating_score': rating_score,
                        'negative_overall_factor': negative_overall_factor,
                        'holder_ratio': holder_result[0] if holder_result and holder_result[0] is not None else 0,
                        'holder_ratio_score': holder_ratio_score,
                        'market_cap_billion': market_cap_billion if market_cap_result and market_cap_result[0] is not None else 0,
                        'market_cap_score': market_cap_score,
                        'market_cap_exponent': self.MARKET_CAP_EXPONENT,
                        'attr_total_score': attr_total,
                        'yesterday_ladder_count': yesterday_ladder_count,
                        'node_level': node_level,
                        'total_score': score
                    }

                    if score > 0:
                        scores.append((code, name, score, matching_attrs_yz, matching_attrs_letter, matching_attrs_region, matching_attrs_qc, matching_attrs_qc_letter, matching_attrs_qc_region, matching_attrs_ff, negative_attrs, is_rushing, node_level))

                print(f"Scores with matches: {len(scores)}")

                scores.sort(key=lambda x: x[2], reverse=True)

                self.correlation_result.setSortingEnabled(False)
                self.correlation_result.setRowCount(len(scores))
                for i, (code, name, score, matching_attrs_yz, matching_attrs_letter, matching_attrs_region, matching_attrs_qc, matching_attrs_qc_letter, matching_attrs_qc_region, matching_attrs_ff, negative_attrs, is_rushing, node_level) in enumerate(scores):
                    self.correlation_result.setItem(i, 0, QTableWidgetItem(code))
                    self.correlation_result.setItem(i, 1, QTableWidgetItem(name))
                    score_item = NumericTableItem(f"{score:.2f}")
                    score_item.setData(Qt.UserRole, score)
                    self.correlation_result.setItem(i, 2, score_item)
                    attrs_yz_str = ', '.join(matching_attrs_yz + matching_attrs_letter + matching_attrs_region)
                    self.correlation_result.setItem(i, 3, QTableWidgetItem(attrs_yz_str))
                    attrs_qc_str = ', '.join(matching_attrs_qc + matching_attrs_qc_letter + matching_attrs_qc_region)
                    self.correlation_result.setItem(i, 4, QTableWidgetItem(attrs_qc_str))
                    attrs_ff_str = ', '.join(matching_attrs_ff)
                    self.correlation_result.setItem(i, 5, QTableWidgetItem(attrs_ff_str))
                    if node_level > 0:
                        board_count = yesterday_ladder_count - (node_level - 1)
                        board_item = NumericTableItem(str(board_count))
                        board_item.setData(Qt.UserRole, board_count)
                        self.correlation_result.setItem(i, 6, board_item)
                        cursor.execute('SELECT node_name FROM ladder_nodes WHERE date = ? AND node_level = ?', (yesterday_str, node_level))
                        node_result = cursor.fetchone()
                        node_name = node_result[0] if node_result else ''
                        self.correlation_result.setItem(i, 7, QTableWidgetItem(node_name))
                    else:
                        self.correlation_result.setItem(i, 6, QTableWidgetItem(''))
                        self.correlation_result.setItem(i, 7, QTableWidgetItem(''))
                
                self.correlation_result.setSortingEnabled(True)
                
                # 调整列的宽度，使其默认放宽
                self.correlation_result.resizeColumnToContents(3)
                self.correlation_result.resizeColumnToContents(4)
                self.correlation_result.resizeColumnToContents(5)
                self.correlation_result.resizeColumnToContents(6)
                self.correlation_result.resizeColumnToContents(7)
                
                # 为每一行设置颜色
                for i, (code, name, score, matching_attrs_yz, matching_attrs_letter, matching_attrs_region, matching_attrs_qc, matching_attrs_qc_letter, matching_attrs_qc_region, matching_attrs_ff, negative_attrs, is_rushing, node_level) in enumerate(scores):
                    # 获取股票评价并设置颜色
                    cursor.execute('SELECT id FROM stocks WHERE code = ?', (code,))
                    stock = cursor.fetchone()
                    if stock:
                        cursor.execute('SELECT rating FROM stock_ratings WHERE stock_id = ?', (stock[0],))
                        rating = cursor.fetchone()
                        if rating:
                            if rating[0] == 1:  # 好评
                                for col in range(8):
                                    item = self.correlation_result.item(i, col)
                                    if item:
                                        item.setBackground(Qt.green)
                            elif rating[0] == -1:  # 差评
                                for col in range(8):
                                    item = self.correlation_result.item(i, col)
                                    if item:
                                        item.setBackground(Qt.red)

                # 启用查看得分详情按钮
                self.score_detail_btn.setEnabled(True)

            except Exception as e:
                print(f"Error analyzing correlation: {e}")
                import traceback
                traceback.print_exc()
            finally:
                for _attr, _val in _gov_backup.items():
                    setattr(self, _attr, _val)
                conn.close()
        except Exception as e:
            print(f"Critical error in analyze_correlation: {e}")
            import traceback
            traceback.print_exc()

    def save_limit_up_data(self):
        date = self.limit_up_date.date().toString('yyyy-MM-dd')
        ladder_count = self.ladder_count_spin.value()

        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        try:
            cursor.execute('DELETE FROM ladder_stocks WHERE ladder_node_id IN (SELECT id FROM ladder_nodes WHERE date = ?)', (date,))
            cursor.execute('DELETE FROM ladder_nodes WHERE date = ?', (date,))
            cursor.execute('DELETE FROM ladder_settings WHERE date = ?', (date,))
            conn.commit()

            if ladder_count > 0:
                cursor.execute('INSERT INTO ladder_settings (date, ladder_count) VALUES (?, ?)', (date, ladder_count))
                conn.commit()

                for i in range(ladder_count):
                    level = i + 1

                    level_container = self.ladder_layout.itemAt(i * 2).widget()
                    if not level_container:
                        continue

                    level_layout = level_container.layout()
                    if not level_layout:
                        continue

                    node_name_input = level_layout.itemAt(1).widget()
                    if isinstance(node_name_input, QLineEdit):
                        node_name = node_name_input.text().strip()
                    else:
                        node_name = ''

                    cursor.execute('INSERT INTO ladder_nodes (date, node_level, node_name) VALUES (?, ?, ?)',
                                  (date, level, node_name))
                    conn.commit()
                    node_id = cursor.lastrowid

                    stocks_container = level_layout.itemAt(2).widget()
                    if stocks_container:
                        flow_layout = stocks_container.layout()
                        if flow_layout:
                            for j in range(flow_layout.count()):
                                item = flow_layout.itemAt(j)
                                if item and item.widget():
                                    stock_label = item.widget()
                                    text = stock_label.text()
                                    lines = text.split('\n')
                                    if lines:
                                        code = lines[0].strip()
                                        cursor.execute('SELECT id FROM stocks WHERE code = ?', (code,))
                                        stock = cursor.fetchone()
                                        if stock:
                                            cursor.execute('INSERT INTO ladder_stocks (ladder_node_id, stock_id, order_index) VALUES (?, ?, ?)',
                                                          (node_id, stock[0], j))

            conn.commit()
        except Exception as e:
            print(f"Error saving limit up data: {e}")
        finally:
            conn.close()

    def export_ladder_to_markdown(self):
        from PyQt5.QtWidgets import QFileDialog, QMessageBox

        date = self.limit_up_date.date().toString('yyyy-MM-dd')

        file_path, _ = QFileDialog.getSaveFileName(
            self,
            '保存涨停梯队',
            f'涨停梯队_{date}.md',
            'Markdown Files (*.md)'
        )

        if not file_path:
            return

        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        try:
            cursor.execute('SELECT ladder_count FROM ladder_settings WHERE date = ?', (date,))
            setting = cursor.fetchone()
            ladder_count = setting[0] if setting else 0

            if ladder_count == 0:
                QMessageBox.information(self, '提示', '当前日期没有梯队数据！')
                conn.close()
                return

            markdown_content = []
            markdown_content.append(f'# 涨停梯队 - {date}\n')

            for i in range(ladder_count):
                level = i + 1  # 节点级别（从1开始）
                board_count = ladder_count - i  # 计算实际板数
                
                cursor.execute('''
                    SELECT id, node_name FROM ladder_nodes 
                    WHERE date = ? AND node_level = ?
                ''', (date, level))
                node = cursor.fetchone()

                if not node:
                    continue

                node_id, node_name = node
                header = f'## {board_count}板'
                if node_name:
                    header += f' - {node_name}'
                markdown_content.append(header + '\n')

                cursor.execute('''
                    SELECT s.code, s.name 
                    FROM ladder_stocks ls
                    JOIN stocks s ON ls.stock_id = s.id
                    WHERE ls.ladder_node_id = ?
                    ORDER BY ls.order_index
                ''', (node_id,))
                stocks = cursor.fetchall()

                if stocks:
                    markdown_content.append('| 代码 | 名称 | 属性 |')
                    markdown_content.append('|------|------|------|')

                    for code, name in stocks:
                        cursor.execute('SELECT attribute FROM stock_attributes WHERE stock_id = (SELECT id FROM stocks WHERE code = ?)', (code,))
                        attrs = cursor.fetchall()
                        attr_str = ', '.join([attr[0] for attr in attrs]) if attrs else '-'
                        markdown_content.append(f'| {code} | {name}　　| {attr_str} |')

                    markdown_content.append('')
                else:
                    markdown_content.append('（暂无股票）\n')

            with open(file_path, 'w', encoding='utf-8') as f:
                f.write('\n'.join(markdown_content))

            QMessageBox.information(self, '导出成功', f'涨停梯队已导出到：\n{file_path}')

        except Exception as e:
            print(f"Error exporting ladder: {e}")
            QMessageBox.warning(self, '导出失败', f'导出时发生错误：\n{str(e)}')
        finally:
            conn.close()

    def move_stock_up(self, current_level, ladder_stock_id, stock_label_widget):
        date = self.limit_up_date.date().toString('yyyy-MM-dd')
        
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        try:
            # 获取当前股票ID
            cursor.execute('SELECT stock_id FROM ladder_stocks WHERE id = ?', (ladder_stock_id,))
            stock_result = cursor.fetchone()
            if not stock_result:
                return
            
            stock_id = stock_result[0]
            
            # 获取上一个梯队的节点ID
            new_level = current_level - 1
            cursor.execute('SELECT id FROM ladder_nodes WHERE date = ? AND node_level = ?', (date, new_level))
            new_node = cursor.fetchone()
            if not new_node:
                return
            
            new_node_id = new_node[0]
            
            # 获取上一个梯队中的最大order_index
            cursor.execute('SELECT MAX(order_index) FROM ladder_stocks WHERE ladder_node_id = ?', (new_node_id,))
            max_order = cursor.fetchone()[0]
            new_order = (max_order + 1) if max_order is not None else 0
            
            # 从当前梯队删除
            cursor.execute('DELETE FROM ladder_stocks WHERE id = ?', (ladder_stock_id,))
            
            # 添加到上一个梯队
            cursor.execute('INSERT INTO ladder_stocks (ladder_node_id, stock_id, order_index) VALUES (?, ?, ?)',
                          (new_node_id, stock_id, new_order))
            
            conn.commit()
            
            # 重新加载数据
            self.load_limit_up_data()
            
        except Exception as e:
            print(f"Error moving stock up: {e}")
        finally:
            conn.close()

    def delete_stock_from_ladder(self, ladder_stock_id):
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        try:
            # 删除股票
            cursor.execute('DELETE FROM ladder_stocks WHERE id = ?', (ladder_stock_id,))
            conn.commit()
            
            # 重新加载数据
            self.load_limit_up_data()
            
        except Exception as e:
            print(f"Error deleting stock: {e}")
        finally:
            conn.close()

    def clear_ladder_level(self, level, board_count, date):
        from PyQt5.QtWidgets import QMessageBox
        
        # 显示确认弹窗
        reply = QMessageBox.question(
            self,
            '确认清空',
            f'确定要清空 {board_count} 板梯队的所有股票吗？',
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No
        )
        
        if reply == QMessageBox.No:
            return
        
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        try:
            # 获取该梯队的节点ID
            cursor.execute('SELECT id FROM ladder_nodes WHERE date = ? AND node_level = ?', (date, level))
            node = cursor.fetchone()
            
            if node:
                node_id = node[0]
                # 删除该节点下的所有股票
                cursor.execute('DELETE FROM ladder_stocks WHERE ladder_node_id = ?', (node_id,))
                conn.commit()
                
                # 重新加载数据
                self.load_limit_up_data()
                
        except Exception as e:
            print(f"Error clearing ladder level: {e}")
        finally:
            conn.close()

    def save_node_name(self, node_id, node_name):
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        try:
            # 更新节点名称
            cursor.execute('UPDATE ladder_nodes SET node_name = ? WHERE id = ?', (node_name, node_id))
            conn.commit()
        except Exception as e:
            print(f"Error saving node name: {e}")
        finally:
            conn.close()

    def save_node_name_and_update_label(self, node_id, node_name, label):
        self.save_node_name(node_id, node_name)
        label.setText(node_name)

    def modify_ladder_count(self):
        from PyQt5.QtWidgets import QMessageBox
        
        date = self.limit_up_date.date().toString('yyyy-MM-dd')
        new_count = self.ladder_count_spin.value()
        
        # 显示确认弹窗
        reply = QMessageBox.question(
            self,
            '确认修改',
            f'确定要修改 {date} 的梯队数量为 {new_count} 吗？',
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No
        )
        
        if reply == QMessageBox.No:
            return
        
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        try:
            # 检查是否已有数据
            cursor.execute('SELECT ladder_count FROM ladder_settings WHERE date = ?', (date,))
            existing = cursor.fetchone()
            
            if existing:
                # 更新现有设置
                cursor.execute('UPDATE ladder_settings SET ladder_count = ? WHERE date = ?', (new_count, date))
            else:
                # 创建新设置
                cursor.execute('INSERT INTO ladder_settings (date, ladder_count) VALUES (?, ?)', (date, new_count))
            
            conn.commit()
            
            # 重新加载数据
            self.load_limit_up_data()
            
            QMessageBox.information(self, '修改成功', f'梯队数量已修改为 {new_count}！')
            
        except Exception as e:
            print(f"Error modifying ladder count: {e}")
            QMessageBox.warning(self, '修改失败', f'修改时发生错误：\n{str(e)}')
        finally:
            conn.close()

    def copy_from_yesterday(self):
        from PyQt5.QtWidgets import QMessageBox
        
        today = self.limit_up_date.date()
        yesterday = today.addDays(-1)
        yesterday_str = yesterday.toString('yyyy-MM-dd')
        today_str = today.toString('yyyy-MM-dd')
        
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        try:
            # 检查昨天的梯队数据
            cursor.execute('SELECT ladder_count FROM ladder_settings WHERE date = ?', (yesterday_str,))
            yesterday_setting = cursor.fetchone()
            
            if not yesterday_setting:
                QMessageBox.information(self, '提示', '昨天没有梯队数据！')
                conn.close()
                return
            
            ladder_count = yesterday_setting[0]
            
            # 检查今天是否已有数据
            cursor.execute('SELECT ladder_count FROM ladder_settings WHERE date = ?', (today_str,))
            today_setting = cursor.fetchone()
            
            if today_setting:
                reply = QMessageBox.question(
                    self,
                    '确认覆盖',
                    '今天已有梯队数据，是否覆盖？',
                    QMessageBox.Yes | QMessageBox.No,
                    QMessageBox.No
                )
                if reply == QMessageBox.No:
                    conn.close()
                    return
                
                # 删除今天的数据
                cursor.execute('DELETE FROM ladder_stocks WHERE ladder_node_id IN (SELECT id FROM ladder_nodes WHERE date = ?)', (today_str,))
                cursor.execute('DELETE FROM ladder_nodes WHERE date = ?', (today_str,))
                cursor.execute('DELETE FROM ladder_settings WHERE date = ?', (today_str,))
            
            # 复制昨天的梯队数量设置
            cursor.execute('INSERT INTO ladder_settings (date, ladder_count) VALUES (?, ?)', (today_str, ladder_count))
            
            # 复制每个梯队
            for level in range(1, ladder_count + 1):
                # 获取昨天的节点
                cursor.execute('SELECT id, node_name FROM ladder_nodes WHERE date = ? AND node_level = ?',
                             (yesterday_str, level))
                yesterday_node = cursor.fetchone()
                
                if yesterday_node:
                    yesterday_node_id, node_name = yesterday_node
                    
                    # 创建今天的节点
                    cursor.execute('INSERT INTO ladder_nodes (date, node_level, node_name) VALUES (?, ?, ?)',
                                  (today_str, level, node_name))
                    new_node_id = cursor.lastrowid
                    
                    # 复制该节点下的所有股票
                    cursor.execute('''
                        SELECT stock_id, order_index FROM ladder_stocks 
                        WHERE ladder_node_id = ?
                        ORDER BY order_index
                    ''', (yesterday_node_id,))
                    stocks = cursor.fetchall()
                    
                    for stock_id, order_index in stocks:
                        cursor.execute('''
                            INSERT INTO ladder_stocks (ladder_node_id, stock_id, order_index) 
                            VALUES (?, ?, ?)
                        ''', (new_node_id, stock_id, order_index))
            
            conn.commit()
            conn.close()
            
            # 更新梯队数量控件并重新加载
            self.ladder_count_spin.setValue(ladder_count)
            self.load_limit_up_data()
            
            QMessageBox.information(self, '复制成功', f'已复制昨天的 {ladder_count} 个梯队数据到今天！')
            
        except Exception as e:
            print(f"Error copying from yesterday: {e}")
            QMessageBox.warning(self, '复制失败', f'复制时发生错误：\n{str(e)}')
            conn.close()

    def get_pinyin_initials(self, text, skip_keji=False):
        """获取文本的拼音首字母列表"""
        # 如果股票名称以"科技"结尾且需要跳过，返回空列表（后续会单独添加"科技"属性）
        if skip_keji and text.endswith('科技'):
            return []
        
        # 如果股票名称以"股份"结尾，去除"股份"二字
        if text.endswith('股份'):
            text = text[:-2]
        
        initials = []
        for char in text:
            pinyin_list = pinyin(char, style=Style.FIRST_LETTER)
            if pinyin_list and pinyin_list[0]:
                initial = pinyin_list[0][0].upper()
                if initial.isalpha():
                    initials.append(initial)
        return initials

    def add_pinyin_initial_attributes(self, stock_id, name, conn=None, cursor=None):
        """为股票添加拼音首字母属性"""
        close_conn = False
        if conn is None:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            close_conn = True
        
        try:
            # 获取拼音首字母（以科技结尾的股票跳过K、J属性）
            initials = self.get_pinyin_initials(name, skip_keji=True)
            
            # 去重
            unique_initials = list(set(initials))
            
            # 检查该股票是否已经有拼音首字母属性
            cursor.execute('SELECT attribute FROM stock_attributes WHERE stock_id = ?', (stock_id,))
            existing_attrs = set([row[0] for row in cursor.fetchall()])
            
            # 只添加不存在的属性
            for initial in unique_initials:
                if initial not in existing_attrs:
                    cursor.execute('INSERT INTO stock_attributes (stock_id, date, attribute) VALUES (?, ?, ?)',
                                  (stock_id, QDate.currentDate().toString('yyyy-MM-dd'), initial))
            
            # 如果股票名称以"科技"结尾，添加"科技"属性
            if name.endswith('科技') and '科技' not in existing_attrs:
                cursor.execute('INSERT INTO stock_attributes (stock_id, date, attribute) VALUES (?, ?, ?)',
                              (stock_id, QDate.currentDate().toString('yyyy-MM-dd'), '科技'))
            
            if close_conn:
                conn.commit()
        except Exception as e:
            print(f"Error adding pinyin attributes: {e}")
        finally:
            if close_conn:
                conn.close()

    def check_and_update_keji_attributes(self):
        """检查并更新科技属性（只执行一次）"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        try:
            # 创建设置表（如果不存在）
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS settings (
                    key TEXT PRIMARY KEY,
                    value TEXT
                )
            ''')
            
            # 检查是否已经执行过科技属性修正
            cursor.execute('SELECT value FROM settings WHERE key = ?', ('keji_updated',))
            result = cursor.fetchone()
            
            if result is None or result[0] != 'true':
                # 执行科技属性修正
                print("[科技属性修正] 首次运行，开始修正科技属性...")
                self.update_existing_pinyin_attributes()
                
                # 标记已执行
                cursor.execute('INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)',
                              ('keji_updated', 'true'))
                conn.commit()
                print("[科技属性修正] 已完成标记")
            else:
                print("[科技属性修正] 已执行过，跳过")
                
        except Exception as e:
            print(f"Error checking keji update: {e}")
        finally:
            conn.close()
    
    def update_existing_pinyin_attributes(self):
        """更新已有的股票拼音首字母属性，去除"股份"二字的影响，修正"科技"结尾股票的属性"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        try:
            # 获取所有股票及其名称
            cursor.execute('SELECT id, name FROM stocks')
            stocks = cursor.fetchall()
            
            updated_count = 0
            keji_updated_count = 0
            
            for stock_id, name in stocks:
                # 检查是否以"科技"结尾
                if name.endswith('科技'):
                    # 删除K、J属性
                    cursor.execute('''
                        DELETE FROM stock_attributes 
                        WHERE stock_id = ? AND attribute IN ('K', 'J')
                    ''', (stock_id,))
                    
                    # 添加"科技"属性（如果不存在）
                    cursor.execute('SELECT COUNT(*) FROM stock_attributes WHERE stock_id = ? AND attribute = ?',
                                  (stock_id, '科技'))
                    if cursor.fetchone()[0] == 0:
                        cursor.execute('INSERT INTO stock_attributes (stock_id, date, attribute) VALUES (?, ?, ?)',
                                      (stock_id, QDate.currentDate().toString('yyyy-MM-dd'), '科技'))
                    
                    conn.commit()
                    keji_updated_count += 1
                    print(f"Updated keji attributes for stock: {name} (ID: {stock_id})")
                # 检查是否以"股份"结尾
                elif name.endswith('股份'):
                    # 获取当前股票的所有拼音首字母属性
                    cursor.execute('''
                        SELECT id, attribute FROM stock_attributes 
                        WHERE stock_id = ? AND LENGTH(attribute) = 1 AND attribute GLOB '[A-Z]'
                    ''', (stock_id,))
                    existing_attributes = cursor.fetchall()
                    
                    # 计算正确的拼音首字母（以科技结尾的股票跳过K、J）
                    correct_initials = self.get_pinyin_initials(name, skip_keji=True)
                    correct_initial_set = set(correct_initials)
                    
                    # 获取现有的首字母集合
                    existing_initial_set = set([attr[1] for attr in existing_attributes])
                    
                    # 需要删除的首字母（存在于现有但不存在于正确列表中）
                    to_remove = existing_initial_set - correct_initial_set
                    
                    if to_remove:
                        # 删除多余的首字母属性
                        for attr_id, attr in existing_attributes:
                            if attr in to_remove:
                                cursor.execute('DELETE FROM stock_attributes WHERE id = ?', (attr_id,))
                        
                        conn.commit()
                        updated_count += 1
                        print(f"Updated pinyin attributes for stock: {name} (ID: {stock_id})")
            
            print(f"Updated pinyin attributes for {updated_count} stocks")
            print(f"Updated keji attributes for {keji_updated_count} stocks")
            return updated_count + keji_updated_count
        except Exception as e:
            print(f"Error updating pinyin attributes: {e}")
            return 0
        finally:
            conn.close()

    def delete_stocks(self):
        """删除股票及关联数据"""
        from PyQt5.QtWidgets import QInputDialog, QTextEdit, QVBoxLayout, QDialog, QDialogButtonBox

        dialog = QDialog(self)
        dialog.setWindowTitle('删除股票')
        dialog.setMinimumWidth(400)

        layout = QVBoxLayout(dialog)
        layout.addWidget(QLabel('输入要删除的股票代码（多个用逗号、空格或换行分隔）：'))

        text_edit = QTextEdit()
        text_edit.setPlaceholderText('例如：600519, 000858, 920199')
        text_edit.setMaximumHeight(120)
        layout.addWidget(text_edit)

        btn_box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btn_box.accepted.connect(dialog.accept)
        btn_box.rejected.connect(dialog.reject)
        layout.addWidget(btn_box)

        if dialog.exec_() != QDialog.Accepted:
            return

        raw = text_edit.toPlainText().strip()
        if not raw:
            return

        codes = re.findall(r'(\d{6})', raw)
        if not codes:
            QMessageBox.warning(self, '提示', '未识别到有效的股票代码')
            return

        # 去重
        codes = list(set(codes))

        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        found_ids = []
        not_found = []
        for code in codes:
            cursor.execute('SELECT id, name FROM stocks WHERE code = ?', (code,))
            row = cursor.fetchone()
            if row:
                found_ids.append((row[0], code, row[1]))
            else:
                not_found.append(code)

        if not found_ids:
            QMessageBox.warning(self, '提示', '这些股票代码在本地库中不存在')
            conn.close()
            return

        msg = f'确定要删除以下 {len(found_ids)} 只股票及其所有关联数据？\n\n（将同时删除属性、竞价记录、板数记录等）\n'
        for _, code, name in found_ids:
            msg += f'\n  {code} {name}'
        if not_found:
            msg += f'\n\n以下代码不存在：{", ".join(not_found)}'

        reply = QMessageBox.question(self, '确认删除', msg,
                                     QMessageBox.Yes | QMessageBox.No)
        if reply != QMessageBox.Yes:
            conn.close()
            return

        for stock_id, code, name in found_ids:
            cursor.execute('DELETE FROM stock_attributes WHERE stock_id = ?', (stock_id,))
            cursor.execute('DELETE FROM board_counts WHERE stock_id = ?', (stock_id,))
            cursor.execute('DELETE FROM bidding_records WHERE stock_id = ?', (stock_id,))
            cursor.execute('DELETE FROM bidding_rush_stocks WHERE stock_id = ?', (stock_id,))
            cursor.execute('DELETE FROM stocks WHERE id = ?', (stock_id,))
            print(f"已删除 {code} {name}")

        conn.commit()
        conn.close()

        QMessageBox.information(self, '完成', f'已删除 {len(found_ids)} 只股票')
        self.load_stocks()

    def add_stock(self):
        from PyQt5.QtWidgets import QMessageBox

        code = self.code_input.text().strip()
        name = self.name_input.text().strip()

        if not code or not name:
            return

        # 验证股票代码必须是6位数字
        if not code.isdigit() or len(code) != 6:
            QMessageBox.warning(
                self,
                '股票代码格式错误',
                '股票代码必须是6位数字！\n例如：600000'
            )
            return

        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        try:
            cursor.execute('SELECT id, name FROM stocks WHERE code = ?', (code,))
            existing_by_code = cursor.fetchone()

            if existing_by_code:
                QMessageBox.warning(
                    self,
                    '股票代码重复',
                    f'股票代码 "{code}" 已存在！\n股票名称：{existing_by_code[1]}'
                )
                conn.close()
                return

            cursor.execute('SELECT id, code FROM stocks WHERE name = ?', (name,))
            existing_by_name = cursor.fetchone()

            if existing_by_name:
                QMessageBox.warning(
                    self,
                    '股票名称重复',
                    f'股票名称 "{name}" 已存在！\n股票代码：{existing_by_name[1]}'
                )
                conn.close()
                return

            cursor.execute('INSERT INTO stocks (code, name) VALUES (?, ?)',
                          (code, name))
            conn.commit()
            cursor.execute('SELECT id FROM stocks WHERE code = ?', (code,))
            stock_id = cursor.fetchone()[0]

            # 为新股票添加拼音首字母属性
            self.add_pinyin_initial_attributes(stock_id, name)

            self.load_stocks(self.filter_input.text().strip())
            self.code_input.clear()
            self.name_input.clear()

            for i in range(self.stock_table.rowCount()):
                item = self.stock_table.item(i, 0)
                if item and int(item.text()) == stock_id:
                    self.stock_table.selectRow(i)
                    self.stock_table.item(i, 0).setSelected(True)
                    break

            self.show_add_attribute_dialog(code)

        except Exception as e:
            print(f"Error adding stock: {e}")
        finally:
            conn.close()

    def add_stock_auto(self):
        from PyQt5.QtWidgets import QMessageBox, QProgressDialog
        import requests

        code = self.code_input.text().strip()
        name = self.name_input.text().strip()

        if not code or not name:
            return

        if not code.isdigit() or len(code) != 6:
            QMessageBox.warning(self, '格式错误', '股票代码必须是6位数字！')
            return

        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        try:
            cursor.execute('SELECT id, name FROM stocks WHERE code = ?', (code,))
            existing = cursor.fetchone()
            if existing:
                QMessageBox.warning(self, '重复', f'股票代码 "{code}" 已存在！')
                return

            cursor.execute('INSERT INTO stocks (code, name) VALUES (?, ?)', (code, name))
            conn.commit()
            cursor.execute('SELECT id FROM stocks WHERE code = ?', (code,))
            stock_id = cursor.fetchone()[0]

            self.add_pinyin_initial_attributes(stock_id, name)

            self.load_stocks(self.filter_input.text().strip())
            self.code_input.clear()
            self.name_input.clear()

            progress = QProgressDialog('正在获取数据...', '取消', 0, 100, self)
            progress.setWindowTitle('添加股票auto')
            progress.setWindowModality(2)
            progress.setMinimumDuration(0)
            progress.setValue(10)
            progress.setLabelText(f'正在获取 {name}({code}) 的板块属性...')

            # 调用东方财富接口获取板块属性（核心题材+所属板块）
            if code.startswith('60') or code.startswith('688'):
                market = 'SH'
            else:
                market = 'SZ'
            secucode = f'{code}.{market}'

            headers = {
                'Accept': '*/*',
                'Accept-Language': 'zh-CN,zh;q=0.9',
                'Connection': 'keep-alive',
                'Origin': 'https://emweb.securities.eastmoney.com',
                'Referer': 'https://emweb.securities.eastmoney.com/',
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
            }

            all_board_names = []
            try:
                url = (
                    "https://datacenter.eastmoney.com/securities/api/data/v1/get?"
                    "reportName=RPT_F10_CORETHEME_BOARDTYPE&"
                    "columns=SECUCODE,SECURITY_CODE,SECURITY_NAME_ABBR,NEW_BOARD_CODE,BOARD_NAME,SELECTED_BOARD_REASON,IS_PRECISE,BOARD_RANK,BOARD_YIELD,DERIVE_BOARD_CODE&"
                    f"filter=(SECUCODE%3D%22{secucode}%22)(IS_PRECISE%3D%221%22)&"
                    "pageNumber=1&pageSize=200&sortTypes=1&sortColumns=BOARD_RANK&"
                    "source=HSF10&client=PC&v=05407725378079107"
                )
                resp = requests.get(url, headers=headers, timeout=15)
                resp.raise_for_status()
                data = resp.json()
                board_list = (data.get('result') or {}).get('data', [])
                for item in board_list:
                    bn = item.get('BOARD_NAME', '')
                    if bn and bn not in all_board_names:
                        all_board_names.append(bn)
            except Exception as e:
                print(f'获取板块属性失败: {e}')

            progress.setValue(30)

            # 再用第二个接口（更全的所属板块）
            try:
                url2 = (
                    "https://datacenter.eastmoney.com/securities/api/data/get?"
                    "type=RPT_F10_CORETHEME_BOARDTYPE&"
                    "sty=ALL&"
                    f"filter=(SECUCODE%3D%22{secucode}%22)&"
                    "p=1&ps=200&sr=1&st=BOARD_RANK&"
                    "source=HSF10&client=PC&v=04828710067581792"
                )
                resp2 = requests.get(url2, headers=headers, timeout=15)
                resp2.raise_for_status()
                data2 = resp2.json()
                board_list2 = data2.get('result', {}).get('data', [])
                for item in board_list2:
                    bn = item.get('BOARD_NAME', '')
                    if bn and bn not in all_board_names:
                        all_board_names.append(bn)
            except Exception as e:
                print(f'获取所属板块失败: {e}')

            progress.setValue(50)
            progress.setLabelText(f'正在整理 {len(all_board_names)} 个板块属性...')

            # 应用属性重命名和剔除
            cleaned_names = []
            for bn in all_board_names:
                if bn in ATTR_REMOVE_SET:
                    continue
                mapped = ATTR_RENAME_MAP.get(bn, bn)
                if mapped not in cleaned_names:
                    cleaned_names.append(mapped)
            all_board_names = cleaned_names

            progress.setLabelText(f'正在保存 {len(all_board_names)} 个板块属性...')

            # 保存属性到 stock_attributes
            date = QDate.currentDate().toString('yyyy-MM-dd')
            cursor.execute('SELECT attribute FROM stock_attributes WHERE stock_id = ? AND date = ?', (stock_id, date))
            existing_attrs = set(r[0] for r in cursor.fetchall())

            saved_attrs = 0
            for bn in all_board_names:
                if bn not in existing_attrs:
                    cleaned = clean_attr_name(bn)
                    if cleaned is None:
                        continue
                    cursor.execute('INSERT INTO stock_attributes (stock_id, date, attribute) VALUES (?, ?, ?)',
                                   (stock_id, date, cleaned))
                    saved_attrs += 1
            conn.commit()

            progress.setValue(70)
            progress.setLabelText(f'正在获取 {name}({code}) 的股东股本数据...')

            # 获取股东持股比例（股本）
            try:
                if not code.startswith('8'):  # 跳过北交所
                    filter_str = f'(SECUCODE%3D%22{secucode}%22)'
                    url3 = f'https://datacenter.eastmoney.com/securities/api/data/v1/get?reportName=RPT_DMSK_NEWINDICATOR&columns=SECURITY_CODE%2CSECUCODE%2CPE_DYNAMIC_EXPLAIN%2CPE_STATIC_EXPLAIN&quoteColumns=f20~01~SECURITY_CODE~TOTAL_MARKET_CAP&filter={filter_str}&sortTypes=&sortColumns=&pageNumber=1&pageSize=1&source=HSF10&client=PC&v=07776959445230575'
                    resp3 = requests.get(url3, headers=headers, timeout=10)
                    resp3.raise_for_status()
                    json3 = resp3.json()
                    if json3.get('result') and json3['result'].get('data'):
                        total_market_cap = json3['result']['data'][0].get('TOTAL_MARKET_CAP')
                        if total_market_cap is not None:
                            cursor.execute('UPDATE stocks SET total_market_cap = ? WHERE code = ?', (total_market_cap, code))
                            conn.commit()
                            print(f'[auto] {code} 总市值: {total_market_cap/100000000:.2f}亿')
            except Exception as e:
                print(f'[auto] 获取总市值失败: {e}')

            progress.setValue(85)

            # 获取股东持股比例（股本/行业数据）
            try:
                if not code.startswith('8'):
                    filter_str2 = f'(REPORT_DATE%3D%22{date}%22)(SECUCODE%3D%22{secucode}%22)'
                    url4 = f'https://datacenter.eastmoney.com/securities/api/data/v1/get?reportName=RPT_HOLDERNUMLATEST&columns=SECURITY_CODE%2CSECUCODE%2CSECURITY_NAME_ABBR%2CREPORT_DATE%2CHOLDER_NUM%2CHOLDER_NUM_MARK&quoteColumns=f23~01~SECURITY_CODE~TOTAL_HOLDER_RATIO&filter={filter_str2}&pageNumber=1&pageSize=1&sortTypes=-1&sortColumns=REPORT_DATE&source=HSF10&client=PC&v=08326872266920659'
                    resp4 = requests.get(url4, headers=headers, timeout=10)
                    resp4.raise_for_status()
                    json4 = resp4.json()
                    if json4.get('result') and json4['result'].get('data'):
                        holder_ratio = json4['result']['data'][0].get('TOTAL_HOLDER_RATIO')
                        if holder_ratio is not None:
                            cursor.execute('UPDATE stocks SET holder_ratio = ? WHERE code = ?', (holder_ratio, code))
                            conn.commit()
                            print(f'[auto] {code} 股东持股比例: {holder_ratio}')
            except Exception as e:
                print(f'[auto] 获取股东股本失败: {e}')

            progress.setValue(100)

            for i in range(self.stock_table.rowCount()):
                item = self.stock_table.item(i, 0)
                if item and int(item.text()) == stock_id:
                    self.stock_table.selectRow(i)
                    self.stock_table.item(i, 0).setSelected(True)
                    break

            QMessageBox.information(self, '添加成功',
                f'{name}({code}) 添加成功！\n\n'
                f'获取到 {len(all_board_names)} 个板块属性\n'
                f'新增 {saved_attrs} 个属性\n'
                f'已获取股本、市值数据')

        except Exception as e:
            QMessageBox.warning(self, '错误', f'添加失败: {str(e)}')
            print(f'add_stock_auto error: {e}')
            import traceback
            traceback.print_exc()
        finally:
            conn.close()

    def load_bidding_records(self):
        try:
            date = self.bidding_date.date().toString('yyyy-MM-dd')
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()

            # 加载竞价一字板
            cursor.execute('''
                SELECT b.id, s.id, s.code, s.name, b.create_time
                FROM bidding_records b
                JOIN stocks s ON b.stock_id = s.id
                WHERE b.date = ? AND b.type = 1
                ORDER BY b.id ASC
            ''', (date,))
            additive_records = cursor.fetchall()

            self.additive_records_table.setRowCount(len(additive_records))
            for i, (bidding_id, stock_id, code, name, create_time) in enumerate(additive_records):
                self.additive_records_table.setItem(i, 0, QTableWidgetItem(code))
                self.additive_records_table.setItem(i, 1, QTableWidgetItem(name))
                self.additive_records_table.setItem(i, 2, QTableWidgetItem(str(create_time)))
                
                # 添加操作按钮
                button_layout = QHBoxLayout()
                
                move_btn = QPushButton('移到减分项')
                move_btn.setStyleSheet('background-color: red; color: white;')
                move_btn.clicked.connect(lambda checked, bid=bidding_id: self.move_bidding_record(bid, False))
                move_btn.setFixedWidth(80)
                button_layout.addWidget(move_btn)
                
                delete_btn = QPushButton('删除')
                delete_btn.setStyleSheet('background-color: gray; color: white;')
                delete_btn.clicked.connect(lambda checked, bid=bidding_id: self.delete_bidding_record(bid))
                delete_btn.setFixedWidth(50)
                button_layout.addWidget(delete_btn)
                
                button_widget = QWidget()
                button_widget.setLayout(button_layout)
                self.additive_records_table.setCellWidget(i, 3, button_widget)
                
                # 获取股票评价并设置颜色
                cursor.execute('SELECT rating FROM stock_ratings WHERE stock_id = ?', (stock_id,))
                rating = cursor.fetchone()
                
                if rating:
                    if rating[0] == 1:  # 好评
                        for col in range(3):
                            item = self.additive_records_table.item(i, col)
                            if item:
                                item.setBackground(Qt.green)
                    elif rating[0] == -1:  # 差评
                        for col in range(3):
                            item = self.additive_records_table.item(i, col)
                            if item:
                                item.setBackground(Qt.red)

            # 加载竞价抢筹股票
            cursor.execute('''
                SELECT brs.id, s.code, s.name, brs.pct_change, brs.create_time
                FROM bidding_rush_stocks brs
                JOIN stocks s ON brs.stock_id = s.id
                WHERE brs.date = ?
                ORDER BY brs.pct_change DESC, brs.id ASC
            ''', (date,))
            rushing_stocks = cursor.fetchall()

            self.rushing_attrs_table.setRowCount(len(rushing_stocks))
            for i, (r_id, code, name, pct_change, create_time) in enumerate(rushing_stocks):
                self.rushing_attrs_table.setItem(i, 0, QTableWidgetItem(code))
                self.rushing_attrs_table.setItem(i, 1, QTableWidgetItem(name))
                pct_item = QTableWidgetItem(f'{pct_change:.2f}')
                pct_item.setTextAlignment(Qt.AlignCenter)
                pct_item.setData(Qt.UserRole, r_id)
                pct_item.setFlags(pct_item.flags() | Qt.ItemIsEditable)
                self.rushing_attrs_table.setItem(i, 2, pct_item)
                self.rushing_attrs_table.setItem(i, 3, QTableWidgetItem(str(create_time)))

                button_layout = QHBoxLayout()
                delete_btn = QPushButton('删除')
                delete_btn.setStyleSheet('background-color: gray; color: white;')
                delete_btn.clicked.connect(lambda checked, rid=r_id: self.delete_rush_attr(rid))
                delete_btn.setFixedWidth(50)
                button_layout.addWidget(delete_btn)
                button_widget = QWidget()
                button_widget.setLayout(button_layout)
                self.rushing_attrs_table.setCellWidget(i, 4, button_widget)

            # 加载竞价负反馈
            cursor.execute('''
                SELECT b.id, s.id, s.code, s.name, b.create_time
                FROM bidding_records b
                JOIN stocks s ON b.stock_id = s.id
                WHERE b.date = ? AND b.type = 3
                ORDER BY b.id ASC
            ''', (date,))
            subtractive_records = cursor.fetchall()

            self.subtractive_records_table.setRowCount(len(subtractive_records))
            for i, (bidding_id, stock_id, code, name, create_time) in enumerate(subtractive_records):
                self.subtractive_records_table.setItem(i, 0, QTableWidgetItem(code))
                self.subtractive_records_table.setItem(i, 1, QTableWidgetItem(name))
                self.subtractive_records_table.setItem(i, 2, QTableWidgetItem(str(create_time)))
                
                # 添加操作按钮
                button_layout = QHBoxLayout()
                
                move_btn = QPushButton('移到加分项')
                move_btn.setStyleSheet('background-color: green; color: white;')
                move_btn.clicked.connect(lambda checked, bid=bidding_id: self.move_bidding_record(bid, True))
                move_btn.setFixedWidth(80)
                button_layout.addWidget(move_btn)
                
                delete_btn = QPushButton('删除')
                delete_btn.setStyleSheet('background-color: gray; color: white;')
                delete_btn.clicked.connect(lambda checked, bid=bidding_id: self.delete_bidding_record(bid))
                delete_btn.setFixedWidth(50)
                button_layout.addWidget(delete_btn)
                
                button_widget = QWidget()
                button_widget.setLayout(button_layout)
                self.subtractive_records_table.setCellWidget(i, 3, button_widget)
                
                # 获取股票评价并设置颜色
                cursor.execute('SELECT rating FROM stock_ratings WHERE stock_id = ?', (stock_id,))
                rating = cursor.fetchone()
                
                if rating:
                    if rating[0] == 1:  # 好评
                        for col in range(3):
                            item = self.subtractive_records_table.item(i, col)
                            if item:
                                item.setBackground(Qt.green)
                    elif rating[0] == -1:  # 差评
                        for col in range(3):
                            item = self.subtractive_records_table.item(i, col)
                            if item:
                                item.setBackground(Qt.red)

            conn.close()
        except Exception as e:
            print(f"Error loading bidding records: {e}")

    def move_bidding_record(self, bidding_id, is_additive):
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()

            cursor.execute('UPDATE bidding_records SET is_additive = ? WHERE id = ?',
                          (1 if is_additive else 0, bidding_id))
            conn.commit()
            conn.close()

            self.load_bidding_records()
        except Exception as e:
            print(f"Error moving bidding record: {e}")

    def delete_bidding_record(self, bidding_id):
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()

            cursor.execute('DELETE FROM bidding_records WHERE id = ?', (bidding_id,))
            conn.commit()
            conn.close()

            self.load_bidding_records()
        except Exception as e:
            print(f"Error deleting bidding record: {e}")

    def delete_rush_attr(self, rush_id):
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()

            cursor.execute('DELETE FROM bidding_rush_stocks WHERE id = ?', (rush_id,))
            conn.commit()
            conn.close()

            self.load_bidding_records()
        except Exception as e:
            print(f"Error deleting rush stock: {e}")

    def on_rush_attr_intensity_changed(self, row, col):
        if col != 2:  # 涨幅%列
            return
        if getattr(self, '_updating_intensity', False):
            return

        item = self.rushing_attrs_table.item(row, col)
        if not item:
            return

        rush_id = item.data(Qt.UserRole)
        if not rush_id:
            return

        try:
            new_value = float(item.text())
        except ValueError:
            return

        if new_value < -50 or new_value > 99.9:
            return

        try:
            self._updating_intensity = True
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            cursor.execute('UPDATE bidding_rush_stocks SET pct_change = ? WHERE id = ?', (new_value, rush_id))
            conn.commit()
            conn.close()

            self.load_bidding_records()
        except Exception as e:
            print(f"Error updating rush attr intensity: {e}")
        finally:
            self._updating_intensity = False

    def show_rush_attr_batch_import_dialog(self):
        from PyQt5.QtWidgets import QDialog, QVBoxLayout, QTextEdit, QPushButton, QLabel, QHBoxLayout

        dialog = QDialog(self)
        dialog.setWindowTitle('竞价抢筹快速导入')
        dialog.setMinimumSize(450, 400)

        if self.isVisible():
            dialog.move(
                self.x() + (self.width() - dialog.width()) // 2,
                self.y() + (self.height() - dialog.height()) // 2
            )

        layout = QVBoxLayout(dialog)

        layout.addWidget(QLabel('每行一个股票，格式：股票名称/代码 涨幅%（空格分隔），例如：'))
        hint = QLabel('贵州茅台 5.2\n宁德时代 3.8\n东方财富 2.5')
        hint.setStyleSheet('color: #666; font-family: monospace; font-size: 12px;')
        layout.addWidget(hint)

        text_edit = QTextEdit()
        text_edit.setPlaceholderText('粘贴数据到这里...')
        layout.addWidget(text_edit)

        btn_layout = QHBoxLayout()
        import_btn = QPushButton('导入')
        import_btn.setStyleSheet('''
            QPushButton {
                background-color: #28a745; color: white; font-weight: bold;
                border: none; border-radius: 3px; padding: 6px 20px;
            }
            QPushButton:hover { background-color: #218838; }
        ''')
        cancel_btn = QPushButton('取消')
        cancel_btn.clicked.connect(dialog.reject)
        import_btn.clicked.connect(dialog.accept)
        btn_layout.addStretch()
        btn_layout.addWidget(import_btn)
        btn_layout.addWidget(cancel_btn)
        layout.addLayout(btn_layout)

        if dialog.exec_() != QDialog.Accepted:
            return

        date = self.bidding_date.date().toString('yyyy-MM-dd')
        lines = text_edit.toPlainText().strip().splitlines()

        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        errors = []
        success_count = 0

        try:
            for line in lines:
                line = line.strip()
                if not line:
                    continue

                parts = line.split()
                if len(parts) < 2:
                    errors.append(f'格式错误: "{line}"')
                    continue

                stock_input = ' '.join(parts[:-1])
                try:
                    pct_change = float(parts[-1])
                except ValueError:
                    errors.append(f'涨幅无效: "{line}"')
                    continue

                if pct_change < -50 or pct_change > 99.9:
                    errors.append(f'涨幅超出范围(-50~99.9): "{line}"')
                    continue

                # 搜索股票
                cursor.execute('SELECT id, code, name FROM stocks WHERE code = ?', (stock_input,))
                stock = cursor.fetchone()
                if not stock:
                    cursor.execute('SELECT id, code, name FROM stocks WHERE name LIKE ?', (f'%{stock_input}%',))
                    stock = cursor.fetchone()

                if not stock:
                    errors.append(f'未找到股票: "{stock_input}"')
                    continue

                stock_id, code, name = stock

                # 检查是否已存在
                cursor.execute('SELECT id FROM bidding_rush_stocks WHERE date = ? AND stock_id = ?', (date, stock_id))
                existing = cursor.fetchone()
                if existing:
                    cursor.execute('UPDATE bidding_rush_stocks SET pct_change = ? WHERE date = ? AND stock_id = ?',
                                   (pct_change, date, stock_id))
                else:
                    cursor.execute('INSERT INTO bidding_rush_stocks (date, stock_id, pct_change) VALUES (?, ?, ?)',
                                   (date, stock_id, pct_change))
                success_count += 1

            conn.commit()
        finally:
            conn.close()

        self.rushing_attrs_table.setUpdatesEnabled(False)
        self.load_bidding_records()
        self.rushing_attrs_table.setUpdatesEnabled(True)

        trimmed = self._trim_rush_attrs()
        if trimmed > 0:
            self.load_bidding_records()

        msg = '导入完成！'
        parts_msg = []
        if success_count:
            parts_msg.append(f'导入: {success_count} 只股票')
        if trimmed:
            parts_msg.append(f'超出{MAX_RUSH_ATTRS}条限制，已舍弃低涨幅{trimmed}条')
        if parts_msg:
            msg += '\n' + '，'.join(parts_msg)
        if errors:
            msg += f'\n\n错误: {len(errors)} 条\n' + '\n'.join(errors[:10])
            if len(errors) > 10:
                msg += f'\n...等共{len(errors)}条错误'

        from PyQt5.QtWidgets import QMessageBox
        QMessageBox.information(self, '导入结果', msg)

    def _trim_rush_attrs(self):
        date = self.bidding_date.date().toString('yyyy-MM-dd')
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        try:
            cursor.execute('SELECT COUNT(*) FROM bidding_rush_stocks WHERE date = ?', (date,))
            total = cursor.fetchone()[0]
            if total > MAX_RUSH_ATTRS:
                cursor.execute('''
                    DELETE FROM bidding_rush_stocks
                    WHERE date = ? AND id NOT IN (
                        SELECT id FROM bidding_rush_stocks
                        WHERE date = ?
                        ORDER BY pct_change DESC
                        LIMIT ?
                    )
                ''', (date, date, MAX_RUSH_ATTRS))
                conn.commit()
                return total - MAX_RUSH_ATTRS
        finally:
            conn.close()
        return 0

    def rate_bidding_stock(self, rating):
        from PyQt5.QtWidgets import QMessageBox

        # 检查是否从竞价记录表格中选择
        additive_selected_row = self.additive_records_table.currentRow()
        subtractive_selected_row = self.subtractive_records_table.currentRow()
        correlation_selected_row = self.correlation_result.currentRow()

        stock_code = None

        # 优先使用关联性结果表格中的选择，因为用户可能是想评价关联性结果中的股票
        if correlation_selected_row >= 0:
            # 从关联性分析结果表格中获取股票代码
            item = self.correlation_result.item(correlation_selected_row, 0)
            if item:
                stock_code = item.text().strip()
        elif additive_selected_row >= 0:
            # 从加分项表格中获取股票代码
            item = self.additive_records_table.item(additive_selected_row, 0)
            if item:
                stock_code = item.text().strip()
        elif subtractive_selected_row >= 0:
            # 从减分项表格中获取股票代码
            item = self.subtractive_records_table.item(subtractive_selected_row, 0)
            if item:
                stock_code = item.text().strip()

        if not stock_code:
            QMessageBox.warning(
                self,
                '提示',
                '请先选择一只股票！'
            )
            return

        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        try:
            # 根据股票代码查找股票ID
            cursor.execute('SELECT id FROM stocks WHERE code = ?', (stock_code,))
            stock_result = cursor.fetchone()
            if not stock_result:
                QMessageBox.warning(
                    self,
                    '提示',
                    f'未找到股票代码 {stock_code}！'
                )
                conn.close()
                return

            stock_id = stock_result[0]

            # 插入或更新评价
            cursor.execute('''
                INSERT OR REPLACE INTO stock_ratings (stock_id, rating) 
                VALUES (?, ?)
            ''', (stock_id, rating))
            conn.commit()
            
            # 重新加载竞价记录以更新颜色
            self.load_bidding_records()
            # 重新加载关联性分析结果以更新颜色
            self.analyze_correlation()
        except Exception as e:
            print(f"Error rating stock: {e}")
        finally:
            conn.close()

    def remove_bidding_stock_rating(self):
        from PyQt5.QtWidgets import QMessageBox

        # 检查是否从竞价记录表格中选择
        additive_selected_row = self.additive_records_table.currentRow()
        subtractive_selected_row = self.subtractive_records_table.currentRow()
        correlation_selected_row = self.correlation_result.currentRow()

        stock_code = None

        # 优先使用关联性结果表格中的选择，因为用户可能是想评价关联性结果中的股票
        if correlation_selected_row >= 0:
            # 从关联性分析结果表格中获取股票代码
            item = self.correlation_result.item(correlation_selected_row, 0)
            if item:
                stock_code = item.text().strip()
        elif additive_selected_row >= 0:
            # 从加分项表格中获取股票代码
            item = self.additive_records_table.item(additive_selected_row, 0)
            if item:
                stock_code = item.text().strip()
        elif subtractive_selected_row >= 0:
            # 从减分项表格中获取股票代码
            item = self.subtractive_records_table.item(subtractive_selected_row, 0)
            if item:
                stock_code = item.text().strip()

        if not stock_code:
            QMessageBox.warning(
                self,
                '提示',
                '请先选择一只股票！'
            )
            return

        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        try:
            # 根据股票代码查找股票ID
            cursor.execute('SELECT id FROM stocks WHERE code = ?', (stock_code,))
            stock_result = cursor.fetchone()
            if not stock_result:
                QMessageBox.warning(
                    self,
                    '提示',
                    f'未找到股票代码 {stock_code}！'
                )
                conn.close()
                return

            stock_id = stock_result[0]

            # 删除评价
            cursor.execute('DELETE FROM stock_ratings WHERE stock_id = ?', (stock_id,))
            conn.commit()
            
            # 重新加载竞价记录以更新颜色
            self.load_bidding_records()
            # 重新加载关联性分析结果以更新颜色
            self.analyze_correlation()
        except Exception as e:
            print(f"Error removing stock rating: {e}")
        finally:
            conn.close()

    def update_weight_parameters(self):
        """更新权重参数"""
        self.STOCK_WEIGHT_FIRST = self.first_stock_weight_input.value()
        self.STOCK_WEIGHT_LAST = self.last_stock_weight_input.value()
        self.BOARD_WEIGHT = self.board_weight_input.value()

        self.RUSH_PCT_COEFFICIENT = self.rush_pct_coefficient_input.value()
        self.RUSH_LETTER_ATTR_WEIGHT = self.rush_letter_attr_weight_input.value()
        self.RUSH_REGION_ATTR_WEIGHT = self.rush_region_attr_weight_input.value()
        self.RUSH_MARKET_CAP_COEFFICIENT = self.rush_market_cap_coefficient_input.value()
        self.YZ_OVERALL_WEIGHT = self.yz_overall_weight_input.value()
        self.ATTR_COUNT_WEIGHT = self.attr_count_weight_input.value()
        self.NEGATIVE_ATTR_COUNT_WEIGHT = self.negative_attr_count_weight_input.value()
        self.NEGATIVE_ATTR_WEIGHT = self.negative_attr_weight_input.value()
        self.NEGATIVE_LETTER_ATTR_WEIGHT = self.negative_letter_attr_weight_input.value()
        self.NEGATIVE_REGION_ATTR_WEIGHT = self.negative_region_attr_weight_input.value()
        self.BOARD_PRESS_WEIGHT = self.board_press_weight_input.value()
        self.NODE_GUIDE_WEIGHT = self.node_guide_weight_input.value()
        self.HOLDER_RATIO_WEIGHT = self.holder_ratio_weight_input.value()

    def save_correlation_results(self, results):
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        try:
            for bayes_key, name, r, abs_r, mi, slope, r_squared, judgment in results:
                cursor.execute('''
                    INSERT OR REPLACE INTO correlation_results
                    (param_key, judgment, r, abs_r, mi, slope, r_squared)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                ''', (bayes_key, judgment, r, abs_r, mi, slope, r_squared))
            conn.commit()
            print(f"[保存] 已保存{len(results)}个参数属性判定结果到数据库")
        finally:
            conn.close()

    def load_correlation_results(self):
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        try:
            cursor.execute('SELECT param_key, judgment, r, abs_r, mi, slope, r_squared FROM correlation_results')
            rows = cursor.fetchall()
            if not rows:
                return None

            param_keys = [
                'stock_weight_first', 'stock_weight_last', 'board_weight',
                'rush_attr_coefficient', 'rush_pct_coefficient',
                'rush_letter_attr_weight', 'rush_region_attr_weight',
                'rush_market_cap_coefficient',
                'yz_overall_weight', 'letter_attr_weight',
                'region_attr_weight', 'attr_count_weight',
                'negative_attr_count_weight',
                'negative_attr_weight', 'negative_letter_attr_weight', 'negative_region_attr_weight',
                'board_press_weight', 'node_guide_weight', 'holder_ratio_weight',
                'market_cap_weight', 'market_cap_exponent',
            ]
            display_names = {
                'stock_weight_first': '首股票权重',
                'stock_weight_last': '末股票权重',
                'board_weight': '每板权重',
                'rush_attr_coefficient': '抢筹属性总系数 a',
                'rush_pct_coefficient': '涨幅系数 b',
                'rush_letter_attr_weight': '抢筹字母属性',
                'rush_region_attr_weight': '抢筹地区属性',
                'rush_market_cap_coefficient': '抢筹市值系数',
                'yz_overall_weight': '一字板整体',
                'letter_attr_weight': '字母属性',
                'region_attr_weight': '地区属性',
                'attr_count_weight': '属性数量差距',
                'negative_attr_count_weight': '负反馈数量差距',
                'negative_attr_weight': '负反馈其他',
                'negative_letter_attr_weight': '负反馈字母',
                'negative_region_attr_weight': '负反馈地区',
                'board_press_weight': '同板压制',
                'node_guide_weight': '节点指引',
                'holder_ratio_weight': '股东持股',
                'market_cap_weight': '市值权重',
                'market_cap_exponent': '市值指数',
            }

            db_map = {row[0]: row for row in rows}
            results = []
            for k in param_keys:
                if k in db_map:
                    results.append((k, display_names[k], db_map[k][2], db_map[k][3], db_map[k][4], db_map[k][5], db_map[k][6], db_map[k][1]))
                else:
                    results.append((k, display_names[k], 0.0, 0.0, 0.0, 0.0, 0.0, '未知'))

            return {'results': results}
        finally:
            conn.close()

    def run_param_correlation_analysis(self):
        from PyQt5.QtWidgets import QDialog, QVBoxLayout, QHBoxLayout, QTableWidget, QTableWidgetItem, QHeaderView, QLabel, QProgressBar, QPushButton, QMessageBox

        # 检查是否已有保存的分析结果
        saved = self.load_correlation_results()
        new_only = False
        if saved is not None:
            msg = QMessageBox(self)
            msg.setWindowTitle('参数属性判定')
            msg.setText(f'已有{len(saved["results"])}个参数的分析结果，请选择操作：')
            btn_show = msg.addButton('显示已保存', QMessageBox.ActionRole)
            btn_new = msg.addButton('仅分析新增参数', QMessageBox.ActionRole)
            btn_all = msg.addButton('重新全部分析', QMessageBox.ActionRole)
            msg.addButton('取消', QMessageBox.RejectRole)
            msg.exec_()
            clicked = msg.clickedButton()
            if clicked == btn_show:
                self._show_correlation_results(saved)
                return
            elif clicked == btn_new:
                new_only = True
            elif clicked == btn_all:
                pass  # 继续全量分析
            else:
                return  # 取消

        start_date = self.backtest_start_date.date()
        end_date = self.backtest_end_date.date()
        start_str = start_date.toString('yyyy-MM-dd')
        end_str = end_date.toString('yyyy-MM-dd')

        dialog = QDialog(self)
        dialog.setWindowTitle('参数属性判定 - 皮尔逊相关系数分析')
        dialog.setMinimumSize(700, 500)
        dialog.setModal(False)
        if self.isVisible():
            dialog.move(
                self.x() + (self.width() - dialog.width()) // 2,
                self.y() + (self.height() - dialog.height()) // 2
            )

        layout = QVBoxLayout(dialog)

        self._corr_status_label = QLabel('正在执行200次蒙特卡洛采样，请稍候...')
        layout.addWidget(self._corr_status_label)
        layout.addWidget(QLabel(f'回测区间: {start_str} ~ {end_str}'))
        layout.addWidget(QLabel('皮尔逊|r|+互信息MI+一元线性回归R² 联合判定'))

        progress = QProgressBar()
        progress.setMaximum(100)
        layout.addWidget(progress)

        result_table = QTableWidget()
        result_table.setColumnCount(7)
        result_table.setHorizontalHeaderLabels(['参数名称', '皮尔逊 r', '|r|', '互信息 MI', '斜率 a', 'R²', '属性判定'])
        result_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        result_table.horizontalHeader().setSectionResizeMode(6, QHeaderView.ResizeToContents)
        layout.addWidget(result_table)

        btn_layout = QHBoxLayout()
        close_btn = QPushButton('关闭')
        close_btn.setEnabled(False)
        close_btn.clicked.connect(dialog.close)
        btn_layout.addStretch()
        btn_layout.addWidget(close_btn)
        layout.addLayout(btn_layout)

        # 如果仅分析新增，预加载已有判定结果的参数key
        analyzed_keys = set()
        if new_only:
            try:
                conn = sqlite3.connect(self.db_path)
                cursor = conn.cursor()
                cursor.execute('SELECT DISTINCT param_key FROM correlation_results')
                analyzed_keys = {row[0] for row in cursor.fetchall()}
                conn.close()
                if analyzed_keys:
                    self._corr_status_label.setText(f'已有 {len(analyzed_keys)} 个参数已判定，仅分析新增参数...')
            except Exception:
                pass

        def on_correlation_finished(data):
            # 仅分析新增模式：阻止 _on_correlation_finished 内部自动保存（由下面自行控制）
            if new_only:
                self._corr_suppress_save = True
            self._on_correlation_finished(dialog, result_table, progress, data, close_btn)
            self._corr_suppress_save = False
            # 仅保存新增参数的判定结果
            if new_only and analyzed_keys:
                new_results = [r for r in data['results'] if r[0] not in analyzed_keys]
                if new_results:
                    self.save_correlation_results(new_results)
                    suffix = f'，其中新增 {len(new_results)} 个'
                    self._corr_status_label.setText(f'分析完成 ✓{suffix}')

        self._correlation_thread = CorrelationAnalysisThread(self, self.db_path, start_str, end_str)
        self._correlation_thread.progress_signal.connect(progress.setValue)
        self._correlation_thread.finished_signal.connect(on_correlation_finished)
        self._correlation_thread.error_signal.connect(lambda msg: QMessageBox.critical(self, '错误', f'分析失败: {msg}'))
        self._correlation_thread.finished_signal.connect(self._correlation_thread.deleteLater)
        self._correlation_thread.error_signal.connect(self._correlation_thread.deleteLater)
        self._correlation_thread.error_signal.connect(dialog.close)

        self._correlation_thread.start()
        dialog.exec_()

    def _show_correlation_results(self, data):
        dialog = QDialog(self)
        dialog.setWindowTitle('参数属性判定 - 已保存结果')
        dialog.setMinimumSize(700, 500)
        dialog.setModal(False)
        if self.isVisible():
            dialog.move(
                self.x() + (self.width() - dialog.width()) // 2,
                self.y() + (self.height() - dialog.height()) // 2
            )

        layout = QVBoxLayout(dialog)
        layout.addWidget(QLabel('已保存的参数属性判定结果'))
        layout.addWidget(QLabel('如需重新分析，请再次点击"参数属性判定"按钮并选择"否"'))

        result_table = QTableWidget()
        result_table.setColumnCount(7)
        result_table.setHorizontalHeaderLabels(['参数名称', '皮尔逊 r', '|r|', '互信息 MI', '斜率 a', 'R²', '属性判定'])
        result_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        result_table.horizontalHeader().setSectionResizeMode(6, QHeaderView.ResizeToContents)
        layout.addWidget(result_table)

        self._on_correlation_finished(dialog, result_table, None, data, None)

        btn_layout = QHBoxLayout()
        close_btn = QPushButton('关闭')
        close_btn.clicked.connect(dialog.close)
        btn_layout.addStretch()
        btn_layout.addWidget(close_btn)
        layout.addLayout(btn_layout)

        dialog.exec_()

    def _on_correlation_finished(self, dialog, result_table, progress, data, close_btn):
        from PyQt5.QtGui import QColor
        from PyQt5.QtCore import Qt
        from PyQt5.QtWidgets import QTableWidgetItem

        results = data['results']

        result_table.setRowCount(len(results))
        for i, (bayes_key, name, r, abs_r, mi, slope, r_squared, judgment) in enumerate(results):
            result_table.setItem(i, 0, QTableWidgetItem(name))
            r_item = QTableWidgetItem(f'{r:.4f}')
            r_item.setTextAlignment(Qt.AlignCenter)
            result_table.setItem(i, 1, r_item)
            abs_item = QTableWidgetItem(f'{abs_r:.4f}')
            abs_item.setTextAlignment(Qt.AlignCenter)
            result_table.setItem(i, 2, abs_item)
            mi_item = QTableWidgetItem(f'{mi:.4f}')
            mi_item.setTextAlignment(Qt.AlignCenter)
            result_table.setItem(i, 3, mi_item)
            slope_item = QTableWidgetItem(f'{slope:.4f}')
            slope_item.setTextAlignment(Qt.AlignCenter)
            result_table.setItem(i, 4, slope_item)
            rsq_item = QTableWidgetItem(f'{r_squared:.4f}')
            rsq_item.setTextAlignment(Qt.AlignCenter)
            result_table.setItem(i, 5, rsq_item)
            result_table.setItem(i, 6, QTableWidgetItem(judgment))

            if judgment in ('线性相关',):
                color = QColor(144, 238, 144)
            elif judgment in ('非线性相关', '非线性(含线性趋势)'):
                color = QColor(135, 206, 250)
            elif judgment == '弱线性+非线性':
                color = QColor(255, 255, 150)
            elif judgment in ('弱线性相关', '线性相关(拟合存疑)'):
                color = QColor(255, 220, 150)
            elif judgment == '弱线性(拟合存疑)':
                color = QColor(255, 200, 200)
            else:
                color = QColor(255, 180, 180)
            for col in range(7):
                result_table.item(i, col).setBackground(color)

        if progress is not None:
            progress.setValue(100)
        if hasattr(self, '_corr_status_label'):
            self._corr_status_label.setText('分析完成 ✓')
        if close_btn is not None:
            close_btn.setEnabled(True)

        # 保存结果到数据库（仅当是实时分析产生的数据，非加载已保存）
        if progress is not None and len(results) > 0 and results[0][2] != 0.0 and not getattr(self, '_corr_suppress_save', False):
            self.save_correlation_results(results)

    def _save_factor_governance(self):
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        try:
            for key, enabled in self._factor_enabled.items():
                cursor.execute('''
                    INSERT OR REPLACE INTO factor_governance (param_key, enabled, weight_scale, updated_time)
                    VALUES (?, ?, 1.0, CURRENT_TIMESTAMP)
                ''', (key, 1 if enabled else 0))
            conn.commit()
        finally:
            conn.close()

    def _load_hexin_v(self):
        """从数据库加载 hexin-v 令牌"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            cursor.execute('SELECT value FROM app_settings WHERE key = ?', ('hexin_v',))
            row = cursor.fetchone()
            if row:
                self.hexin_v = row[0]
                print(f"[hexin-v] 已从数据库加载")
            conn.close()
        except Exception as e:
            print(f"[hexin-v] 加载失败: {e}")

    def _save_hexin_v_from_ui(self):
        """从 UI 输入框保存 hexin-v 令牌"""
        value = self.hexin_v_input.text().strip()
        if not value:
            QMessageBox.warning(self, '提示', 'hexin-v 不能为空')
            return
        self._save_hexin_v(value)
        QMessageBox.information(self, '成功', 'hexin-v 已更新并保存')

    def _save_hexin_v(self, value):
        """保存 hexin-v 令牌到数据库"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            cursor.execute('INSERT OR REPLACE INTO app_settings (key, value) VALUES (?, ?)',
                           ('hexin_v', value))
            conn.commit()
            conn.close()
            self.hexin_v = value
            print(f"[hexin-v] 已保存")
        except Exception as e:
            print(f"[hexin-v] 保存失败: {e}")

    def _load_factor_governance(self):
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            cursor.execute('SELECT param_key, enabled FROM factor_governance')
            rows = cursor.fetchall()
            conn.close()
            for key, enabled in rows:
                self._factor_enabled[key] = bool(enabled)
            if rows:
                print(f"[因子治理] 已加载{len(rows)}个因子启用状态")
        except Exception as e:
            print(f"[因子治理] 加载失败: {e}")

    def _update_factor_enabled(self, key, enabled):
        self._factor_enabled[key] = enabled
        self._save_factor_governance()

    # ============================================================
    # 因子治理系统 — 全量输入特征自动区分有效/无效因子
    # ============================================================

    FACTOR_DEFS = [
        ('stock_weight_first', '首股票权重', '权重系数', Coefficients.STOCK_WEIGHT_FIRST),
        ('stock_weight_last', '末股票权重', '权重系数', Coefficients.STOCK_WEIGHT_LAST),
        ('board_weight', '每板权重', '板数因子', Coefficients.BOARD_WEIGHT),

        ('rush_pct_coefficient', '涨幅系数 b', '抢筹系数', Coefficients.RUSH_PCT_COEFFICIENT),
        ('rush_letter_attr_weight', '抢筹字母属性系数', '抢筹系数', Coefficients.RUSH_LETTER_ATTR_WEIGHT),
        ('rush_region_attr_weight', '抢筹地区属性系数', '抢筹系数', Coefficients.RUSH_REGION_ATTR_WEIGHT),
        ('rush_market_cap_coefficient', '抢筹市值系数', '抢筹系数', Coefficients.RUSH_MARKET_CAP_COEFFICIENT),
        ('yz_overall_weight', '一字板整体', '一字板因子', Coefficients.YZ_OVERALL_WEIGHT),
        ('letter_attr_weight', '字母属性', '属性因子', Coefficients.LETTER_ATTR_WEIGHT),
        ('region_attr_weight', '地区属性', '属性因子', Coefficients.REGION_ATTR_WEIGHT),
        ('attr_count_weight', '属性数量差距', '属性因子', Coefficients.ATTR_COUNT_WEIGHT),
        ('negative_attr_count_weight', '负反馈数量差距', '负反馈因子', Coefficients.NEGATIVE_ATTR_COUNT_WEIGHT),
        ('negative_attr_weight', '负反馈其他属性', '负反馈因子', Coefficients.NEGATIVE_ATTR_WEIGHT),
('negative_letter_attr_weight', '负反馈字母属性', '负反馈因子', Coefficients.NEGATIVE_LETTER_ATTR_WEIGHT),
('negative_region_attr_weight', '负反馈地区属性', '负反馈因子', Coefficients.NEGATIVE_REGION_ATTR_WEIGHT),
        ('board_press_weight', '同板压制', '压制因子', Coefficients.BOARD_PRESS_WEIGHT),
        ('node_guide_weight', '节点指引', '指引因子', Coefficients.NODE_GUIDE_WEIGHT),
        ('holder_ratio_weight', '股东持股', '股东因子', Coefficients.HOLDER_RATIO_WEIGHT),
        ('market_cap_weight', '市值权重', '市值因子', Coefficients.MARKET_CAP_WEIGHT),
        ('market_cap_exponent', '市值指数', '市值因子', Coefficients.MARKET_CAP_EXPONENT),
    ]

    def run_factor_governance(self):
        from PyQt5.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QTableWidget,
            QTableWidgetItem, QHeaderView, QLabel, QProgressBar, QPushButton,
            QMessageBox, QTabWidget, QTextEdit, QCheckBox, QGroupBox, QGridLayout,
            QRadioButton, QButtonGroup, QSpinBox, QDoubleSpinBox, QFrame)
        from PyQt5.QtGui import QColor, QFont
        from PyQt5.QtCore import Qt, QThread, pyqtSignal

        start_date = self.backtest_start_date.date()
        end_date = self.backtest_end_date.date()
        start_str = start_date.toString('yyyy-MM-dd')
        end_str = end_date.toString('yyyy-MM-dd')

        dialog = QDialog(self)
        dialog.setWindowTitle('因子治理 — 全量输入特征有效/无效分析')
        dialog.setMinimumSize(1000, 700)
        dialog.setModal(False)
        if self.isVisible():
            dialog.move(
                self.x() + (self.width() - dialog.width()) // 2,
                self.y() + (self.height() - dialog.height()) // 2
            )
        layout = QVBoxLayout(dialog)

        tab_widget = QTabWidget()
        layout.addWidget(tab_widget)

        # === Tab 1: 分析结果 ===
        analysis_tab = QWidget()
        analysis_layout = QVBoxLayout(analysis_tab)
        tab_widget.addTab(analysis_tab, '分析结果')

        self._gov_status_label = QLabel('就绪，点击"开始分析"')
        analysis_layout.addWidget(self._gov_status_label)

        progress = QProgressBar()
        progress.setMaximum(100)
        analysis_layout.addWidget(progress)

        # 因子选择区域
        factor_sel_group = QGroupBox('选择要分析的因子（取消勾选则跳过分析，保留上次结果）')
        factor_sel_layout = QVBoxLayout(factor_sel_group)
        factor_sel_btn_layout = QHBoxLayout()
        select_all_btn = QPushButton('全选')
        deselect_all_btn = QPushButton('取消全选')
        factor_sel_btn_layout.addWidget(select_all_btn)
        factor_sel_btn_layout.addWidget(deselect_all_btn)
        factor_sel_btn_layout.addStretch()
        factor_sel_layout.addLayout(factor_sel_btn_layout)

        factor_scroll = QScrollArea()
        factor_scroll.setWidgetResizable(True)
        factor_scroll.setMaximumHeight(120)
        factor_scroll_content = QWidget()
        factor_scroll_grid = QGridLayout(factor_scroll_content)
        self._gov_factor_checkboxes = {}
        for idx, (key, name, group, val) in enumerate(self.FACTOR_DEFS):
            cb = QCheckBox(f'{name}')
            cb.setChecked(True)
            cb.setToolTip(f'参数: {key}')
            self._gov_factor_checkboxes[key] = cb
            factor_scroll_grid.addWidget(cb, idx // 4, idx % 4)
        factor_scroll.setWidget(factor_scroll_content)
        factor_sel_layout.addWidget(factor_scroll)

        select_all_btn.clicked.connect(
            lambda: [cb.setChecked(True) for cb in self._gov_factor_checkboxes.values()])
        deselect_all_btn.clicked.connect(
            lambda: [cb.setChecked(False) for cb in self._gov_factor_checkboxes.values()])
        analysis_layout.addWidget(factor_sel_group)

        self._gov_table = QTableWidget()
        self._gov_table.setColumnCount(9)
        self._gov_table.setHorizontalHeaderLabels([
            '因子名称', '分组', '当前值', '有效性分', '噪声水平', '冗余检测',
            '综合状态', '治理建议', '白名单'
        ])
        self._gov_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self._gov_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        analysis_layout.addWidget(self._gov_table)

        # 分析结果汇总区域
        summary_group = QGroupBox('治理方案摘要')
        summary_layout = QVBoxLayout(summary_group)
        self._gov_summary_text = QTextEdit()
        self._gov_summary_text.setReadOnly(True)
        self._gov_summary_text.setMaximumHeight(120)
        summary_layout.addWidget(self._gov_summary_text)
        analysis_layout.addWidget(summary_group)

        # === Tab 2: 全量分析报告 ===
        report_tab = QWidget()
        report_layout = QVBoxLayout(report_tab)
        tab_widget.addTab(report_tab, '全量分析报告')

        self._gov_report_text = QTextEdit()
        self._gov_report_text.setReadOnly(True)
        self._gov_report_text.setFont(QFont('Consolas', 9))
        report_layout.addWidget(self._gov_report_text)

        # === Tab 3: 治理控制 ===
        control_tab = QWidget()
        control_layout = QVBoxLayout(control_tab)
        tab_widget.addTab(control_tab, '治理控制')

        control_layout.addWidget(QLabel('选择要执行的治理操作（可多选）：'))

        self._gov_eliminate_cb = QCheckBox('剔除无效因子（有效性分<20 且 非白名单 → 权重归零）')
        self._gov_reduce_cb = QCheckBox('降权弱因子（有效性分20~40 → 权重×0.5）')
        self._gov_denoise_cb = QCheckBox('降噪高噪声因子（噪声水平>0.3 → 增强L2正则）')
        self._gov_whitelist_cb = QCheckBox('白名单保护（线性相关/关键因子 → 锁定不调整）')
        control_layout.addWidget(self._gov_eliminate_cb)
        control_layout.addWidget(self._gov_reduce_cb)
        control_layout.addWidget(self._gov_denoise_cb)
        control_layout.addWidget(self._gov_whitelist_cb)

        whitelist_group = QGroupBox('因子启用/禁用（勾选 = 启用，取消勾选 = 禁用，自动持久化保存）\n'
                                     '说明：禁用后同板压制/节点指引=1(无影响)，其他系数=0(不贡献)')
        whitelist_layout = QVBoxLayout(whitelist_group)
        whitelist_scroll = QScrollArea()
        whitelist_scroll.setWidgetResizable(True)
        whitelist_content = QWidget()
        whitelist_grid = QGridLayout(whitelist_content)
        self._gov_whitelist_cbs = {}
        for idx, (key, name, group, val) in enumerate(self.FACTOR_DEFS):
            cb = QCheckBox(f'{name} ({key})')
            cb.setChecked(self._factor_enabled.get(key, True))
            cb.stateChanged.connect(lambda state, k=key: self._update_factor_enabled(k, bool(state)))
            self._gov_whitelist_cbs[key] = cb
            whitelist_grid.addWidget(cb, idx // 3, idx % 3)
        whitelist_scroll.setWidget(whitelist_content)
        whitelist_layout.addWidget(whitelist_scroll)
        control_layout.addWidget(whitelist_group)

        # 动态管控配置
        dynamic_group = QGroupBox('训练中动态权重管控')
        dynamic_layout = QGridLayout(dynamic_group)
        self._gov_dynamic_cb = QCheckBox('启用训练中动态管控（每轮根据因子实时表现调整权重）')
        dynamic_layout.addWidget(self._gov_dynamic_cb, 0, 0, 1, 2)
        dynamic_layout.addWidget(QLabel('动态惩罚系数:'), 1, 0)
        self._gov_dynamic_penalty = QDoubleSpinBox()
        self._gov_dynamic_penalty.setRange(0.0, 1.0)
        self._gov_dynamic_penalty.setValue(0.3)
        self._gov_dynamic_penalty.setSingleStep(0.1)
        dynamic_layout.addWidget(self._gov_dynamic_penalty, 1, 1)
        dynamic_layout.addWidget(QLabel('动态更新频率(轮次):'), 2, 0)
        self._gov_dynamic_freq = QSpinBox()
        self._gov_dynamic_freq.setRange(1, 20)
        self._gov_dynamic_freq.setValue(5)
        dynamic_layout.addWidget(self._gov_dynamic_freq, 2, 1)
        control_layout.addWidget(dynamic_group)

        # === 底部按钮 ===
        btn_layout = QHBoxLayout()

        self._gov_analyze_btn = QPushButton('开始分析')
        self._gov_analyze_btn.setStyleSheet('''
            QPushButton { background-color: #28a745; color: white; font-weight: bold;
                border: none; border-radius: 3px; padding: 8px 20px; }
            QPushButton:hover { background-color: #218838; }
        ''')
        self._gov_analyze_btn.clicked.connect(
            lambda: self._run_factor_analysis(dialog, progress))
        btn_layout.addWidget(self._gov_analyze_btn)

        apply_btn = QPushButton('应用治理方案')
        apply_btn.setStyleSheet('''
            QPushButton { background-color: #e83e8c; color: white; font-weight: bold;
                border: none; border-radius: 3px; padding: 8px 20px; }
            QPushButton:hover { background-color: #c2185b; }
        ''')
        apply_btn.clicked.connect(lambda: self._apply_factor_governance(dialog))
        btn_layout.addWidget(apply_btn)

        close_btn = QPushButton('关闭')
        close_btn.clicked.connect(dialog.close)
        btn_layout.addStretch()
        btn_layout.addWidget(close_btn)
        layout.addLayout(btn_layout)

        dialog.exec_()

    def _run_factor_analysis(self, dialog, progress):
        from PyQt5.QtCore import QThread, pyqtSignal
        import math, random, copy

        start_date = self.backtest_start_date.date()
        end_date = self.backtest_end_date.date()
        start_str = start_date.toString('yyyy-MM-dd')
        end_str = end_date.toString('yyyy-MM-dd')

        self._gov_analyze_btn.setEnabled(False)
        self._gov_status_label.setText('全量因子分析中...')

        class FactorAnalysisThread(QThread):
            progress_signal = pyqtSignal(int)
            finished_signal = pyqtSignal(dict)
            error_signal = pyqtSignal(str)

            def __init__(self, main, db_path, start_str, end_str):
                super().__init__()
                self.main = main
                self.db_path = db_path
                self.start_str = start_str
                self.end_str = end_str

            def run(self):
                try:
                    FACTOR_DEFS = self.main.FACTOR_DEFS
                    n_factors = len(FACTOR_DEFS)

                    baseline_score = self.main._optimization_backtest(self.start_str, self.end_str)

                    single_scores = {}
                    self.progress_signal.emit(5)

                    for idx, (key, name, group, cur_val) in enumerate(FACTOR_DEFS):
                        # 跳过未选中的因子
                        factor_cbs = getattr(self.main, '_gov_factor_checkboxes', {})
                        if key in factor_cbs and not factor_cbs[key].isChecked():
                            single_scores[key] = {
                                'effectiveness': 0,
                                'marginal': 0,
                                'score_with': baseline_score,
                                'score_without': baseline_score,
                                'skipped': True,
                            }
                            pct = 5 + int((idx + 1) / n_factors * 40)
                            self.progress_signal.emit(pct)
                            continue

                        orig_val = getattr(self.main, BAYESIAN_TO_COEFFICIENT.get(key, key.upper()))

                        score_with = self.main._optimization_backtest(self.start_str, self.end_str)

                        # 禁用当前因子：乘法因子置1，其他置0
                        disabled_val = 1.0 if key in ('board_press_weight', 'node_guide_weight') else 0.0
                        setattr(self.main, BAYESIAN_TO_COEFFICIENT.get(key, key.upper()), disabled_val)
                        score_without = self.main._optimization_backtest(self.start_str, self.end_str)

                        setattr(self.main, BAYESIAN_TO_COEFFICIENT.get(key, key.upper()), orig_val)

                        marginal = score_with - score_without
                        effectiveness = max(0, marginal) / max(1, baseline_score) * 100
                        single_scores[key] = {
                            'effectiveness': min(effectiveness, 100),
                            'marginal': marginal,
                            'score_with': score_with,
                            'score_without': score_without,
                        }

                        pct = 5 + int((idx + 1) / n_factors * 40)
                        self.progress_signal.emit(pct)

                    self.progress_signal.emit(50)

                    noise_levels = {}
                    n_noise = max(3, n_factors // 3)
                    for idx, (key, name, group, cur_val) in enumerate(FACTOR_DEFS):
                        # 跳过未选中的因子
                        if key in factor_cbs and not factor_cbs[key].isChecked():
                            noise_levels[key] = 0.0
                            pct = 50 + int((idx + 1) / n_factors * 30)
                            self.progress_signal.emit(pct)
                            continue
                        vals = []
                        for _ in range(n_noise):
                            orig = getattr(self.main, BAYESIAN_TO_COEFFICIENT.get(key, key.upper()))
                            lo, hi = BAYESIAN_BOUNDS.get(key, (0.9 * orig, 1.1 * orig))
                            test_val = random.uniform(lo, hi)
                            setattr(self.main, BAYESIAN_TO_COEFFICIENT.get(key, key.upper()), test_val)
                            s = self.main._optimization_backtest(self.start_str, self.end_str)
                            vals.append(s)
                            setattr(self.main, BAYESIAN_TO_COEFFICIENT.get(key, key.upper()), orig)
                        mean_v = sum(vals) / len(vals)
                        std_v = math.sqrt(sum((v - mean_v) ** 2 for v in vals) / len(vals)) if len(vals) > 1 else 0
                        cv = std_v / max(abs(mean_v), 1e-8)
                        noise_levels[key] = min(cv * 5, 1.0)
                        pct = 50 + int((idx + 1) / n_factors * 30)
                        self.progress_signal.emit(pct)

                    self.progress_signal.emit(85)

                    redundancy = {}
                    keys = [k for k, _, _, _ in FACTOR_DEFS]
                    for k in keys:
                        redundancy[k] = 0.0
                    for i in range(len(keys)):
                        for j in range(i + 1, len(keys)):
                            ki, kj = keys[i], keys[j]
                            score_i = single_scores[ki]
                            score_j = single_scores[kj]
                            delta_i = abs(score_i['marginal'])
                            delta_j = abs(score_j['marginal'])
                            if delta_i > 0 and delta_j > 0:
                                ratio = min(delta_i, delta_j) / max(delta_i, delta_j)
                                if ratio > 0.7:
                                    redundancy[ki] = max(redundancy[ki], ratio)
                                    redundancy[kj] = max(redundancy[kj], ratio)

                    self.progress_signal.emit(95)

                    results = []
                    whitelist_selected = {k for k, cb in self.main._gov_whitelist_cbs.items() if cb.isChecked()}
                    for key, name, group, cur_val in FACTOR_DEFS:
                        # 跳过的因子
                        if single_scores[key].get('skipped'):
                            results.append((name, group, cur_val, 0.0, 0.0, 0.0,
                                            '已跳过', '保留(未分析)', key in whitelist_selected))
                            continue
                        eff = single_scores[key]['effectiveness']
                        noise = noise_levels[key]
                        red = redundancy[key]

                        if eff >= 60:
                            status = '有效因子'
                            suggestion = '保留'
                        elif eff >= 40:
                            status = '一般因子'
                            suggestion = '观察'
                        elif eff >= 20:
                            status = '弱因子'
                            suggestion = '降权' if key not in whitelist_selected else '白名单保护'
                        else:
                            status = '无效因子'
                            suggestion = '剔除' if key not in whitelist_selected else '白名单保护'

                        if noise > 0.5 and eff < 40:
                            if '降权' not in suggestion and '剔除' not in suggestion:
                                suggestion += '+降噪'
                            elif '剔除' not in suggestion:
                                suggestion = '降噪'

                        if red > 0.7:
                            if eff < 40:
                                suggestion = '降维(冗余)' if '剔除' not in suggestion else suggestion
                            else:
                                suggestion += '(冗余)'

                        if key in whitelist_selected:
                            if '剔除' in suggestion or '降权' in suggestion:
                                suggestion = '白名单保护'

                        results.append((name, group, cur_val, eff, noise, red, status, suggestion, key in whitelist_selected))

                    saved_judgments = self.main.load_correlation_results()
                    if saved_judgments:
                        jmap = {r[0]: r[7] for r in saved_judgments['results']}
                        for idx, (key, name, group, cur_val) in enumerate(FACTOR_DEFS):
                            j = jmap.get(key, '未知')
                            if j in ('非线性相关', '弱相关') and results[idx][6] == '有效因子':
                                results[idx] = (name, group, cur_val, results[idx][3], results[idx][4],
                                                results[idx][5], '非线性无效(属性印证)', '降权/降噪', results[idx][8])

                    self.progress_signal.emit(100)
                    self.finished_signal.emit({
                        'results': results,
                        'baseline': baseline_score,
                        'single_scores': single_scores,
                        'noise_levels': noise_levels,
                        'redundancy': redundancy,
                    })
                except Exception as e:
                    self.error_signal.emit(str(e))
                    import traceback
                    traceback.print_exc()

        self._gov_thread = FactorAnalysisThread(self, self.db_path, start_str, end_str)
        self._gov_thread.progress_signal.connect(progress.setValue)

        def on_gov_finished(data):
            self._gov_status_label.setText('分析完成 ✓')
            self._gov_analyze_btn.setEnabled(True)
            self._populate_gov_table(data)

        self._gov_thread.finished_signal.connect(on_gov_finished)
        self._gov_thread.finished_signal.connect(self._gov_thread.deleteLater)
        self._gov_thread.error_signal.connect(lambda msg: QMessageBox.critical(self, '错误', f'分析失败: {msg}'))
        self._gov_thread.error_signal.connect(self._gov_thread.deleteLater)
        self._gov_thread.start()

    def _populate_gov_table(self, data):
        from PyQt5.QtGui import QColor
        from PyQt5.QtCore import Qt
        from PyQt5.QtWidgets import QTableWidgetItem

        results = data['results']
        baseline = data['baseline']

        self._gov_table.setRowCount(len(results))
        cnt_eliminate = cnt_reduce = cnt_denoise = cnt_whitelist = 0
        report_lines = [f'{"="*70}', f'  因子治理全量分析报告', f'  {"="*70}', '']
        report_lines.append(f'基准回测得分: {baseline:.2f}')
        report_lines.append(f'因子总数: {len(results)}')
        report_lines.append('')

        for i, (name, group, cur_val, eff, noise, red, status, suggestion, whitelist) in enumerate(results):
            self._gov_table.setItem(i, 0, QTableWidgetItem(name))
            self._gov_table.setItem(i, 1, QTableWidgetItem(group))

            val_item = QTableWidgetItem(f'{cur_val:.4f}')
            val_item.setTextAlignment(Qt.AlignCenter)
            self._gov_table.setItem(i, 2, val_item)

            eff_item = QTableWidgetItem(f'{eff:.1f}')
            eff_item.setTextAlignment(Qt.AlignCenter)
            self._gov_table.setItem(i, 3, eff_item)

            noise_item = QTableWidgetItem(f'{noise:.2f}')
            noise_item.setTextAlignment(Qt.AlignCenter)
            self._gov_table.setItem(i, 4, noise_item)

            red_item = QTableWidgetItem(f'{red:.2f}')
            red_item.setTextAlignment(Qt.AlignCenter)
            self._gov_table.setItem(i, 5, red_item)

            self._gov_table.setItem(i, 6, QTableWidgetItem(status))
            self._gov_table.setItem(i, 7, QTableWidgetItem(suggestion))
            self._gov_table.setItem(i, 8, QTableWidgetItem('✓' if whitelist else ''))

            if status == '已跳过':
                color = QColor(230, 230, 230)
            elif '剔除' in suggestion:
                color = QColor(255, 200, 200)
                cnt_eliminate += 1
            elif '降权' in suggestion or '降噪' in suggestion:
                color = QColor(255, 240, 200)
                cnt_reduce += 1
            elif '白名单' in suggestion:
                color = QColor(200, 230, 255)
                cnt_whitelist += 1
            else:
                color = QColor(220, 255, 220)

            for col in range(9):
                self._gov_table.item(i, col).setBackground(color)

            if status != '已跳过':
                report_lines.append(f'  [{status:12s}] {name:10s} | 值={cur_val:.4f} | '
                                    f'有效={eff:.1f} | 噪声={noise:.2f} | 冗余={red:.2f} | 建议:{suggestion}')

        report_lines.append('')
        report_lines.append(f'{"="*70}')
        report_lines.append(f'  治理汇总: 剔除{cnt_eliminate}个 | 降权/降噪{cnt_reduce}个 | 白名单{cnt_whitelist}个 | 保留{len(results)-cnt_eliminate-cnt_reduce}个')
        report_lines.append(f'{"="*70}')

        self._gov_report_text.setPlainText('\n'.join(report_lines))

        summary = (
            f'因子分析完成: 有效因子{sum(1 for r in results if r[6]=="有效因子")}个, '
            f'一般因子{sum(1 for r in results if "一般" in r[6])}个, '
            f'弱因子{sum(1 for r in results if "弱因子" in r[6])}个, '
            f'无效因子{sum(1 for r in results if "无效" in r[6])}个\n'
            f'治理建议: 剔除{cnt_eliminate}个 | 降权/降噪{cnt_reduce}个 | 白名单{cnt_whitelist}个\n'
            f'（请切换到"治理控制"选项卡执行操作）'
        )
        self._gov_summary_text.setPlainText(summary)

    def _apply_factor_governance(self, dialog):
        from PyQt5.QtWidgets import QMessageBox

        actions = []
        if self._gov_eliminate_cb.isChecked():
            actions.append('剔除')
        if self._gov_reduce_cb.isChecked():
            actions.append('降权')
        if self._gov_denoise_cb.isChecked():
            actions.append('降噪')
        if self._gov_whitelist_cb.isChecked():
            actions.append('白名单')

        if not actions:
            QMessageBox.warning(self, '提示', '请至少选择一个治理操作')
            return

        whitelist = {k for k, cb in self._gov_whitelist_cbs.items() if cb.isChecked()}

        table = self._gov_table
        changed = []
        for i in range(table.rowCount()):
            name = table.item(i, 0).text()
            suggestion = table.item(i, 7).text()
            key = None
            for k, n, g, v in self.FACTOR_DEFS:
                if n == name:
                    key = k
                    break
            if key is None:
                continue

            if '剔除' in actions and '剔除' in suggestion and key not in whitelist:
                self._factor_enabled[key] = False
                changed.append(f'禁用 {name}（原系数值保留，评分中视为0）')
            elif '降权' in actions and ('降权' in suggestion or '降噪' in suggestion):
                if key not in whitelist:
                    param_attr = BAYESIAN_TO_COEFFICIENT.get(key, key.upper())
                    cur_val = getattr(self, param_attr)
                    new_val = cur_val * 0.5
                    setattr(self, param_attr, new_val)
                    coeff_name = BAYESIAN_TO_COEFFICIENT.get(key)
                    if coeff_name:
                        setattr(Coefficients, coeff_name, new_val)
                    changed.append(f'降权 {name}: {cur_val:.4f} → {new_val:.4f}')

        if '降噪' in actions:
            from config import L2_LAMBDA
            old_lam = L2_LAMBDA
            new_lam = old_lam * 5
            import config
            config.L2_LAMBDA = new_lam
            changed.append(f'增强L2正则: {old_lam} → {new_lam} (降噪)')

        if '白名单' in actions:
            for key in whitelist:
                changed.append(f'白名单保护: {key}')

        self._gov_status_label.setText(f'已执行治理: {", ".join(actions)}')
        QMessageBox.information(self, '治理完成',
            f'已执行治理操作: {", ".join(actions)}\n\n'
            f'变更详情:\n' + '\n'.join(changed) + '\n\n'
            f'注意：禁用因子在原系数值保留，评分中同板压制/节点指引视为1(无影响)，其他视为0(不贡献)。\n'
            f'可在"因子治理"对话框中随时重新启用。')

        self._save_factor_governance()

        if self._gov_dynamic_cb.isChecked():
            self._gov_dynamic_active = True
            self._gov_dynamic_penalty_val = self._gov_dynamic_penalty.value()
            self._gov_dynamic_freq_val = self._gov_dynamic_freq.value()
            print(f'[动态管控] 已启用, 惩罚系数={self._gov_dynamic_penalty_val}, 更新频率={self._gov_dynamic_freq_val}轮')

    def start_backtest(self):
        """开始回测"""
        try:
            self.backtest_result_label.setText('回测结果: 回测中...')
            self.backtest_detail_text.clear()
            
            start_date = self.backtest_start_date.date()
            end_date = self.backtest_end_date.date()
            top_n = 5  # 使用固定的top_n值（前5名）
            valid_score = 1  # 使用固定的验证得分系数（每只正确预测得1分）
            
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            total_score = 0.0
            valid_days = 0
            total_valid_stocks = 0
            total_count = 0
            detail_lines = []
            
            current_date = start_date
            skip_holidays = self.skip_holidays_checkbox.isChecked()
            
            while current_date <= end_date:
                date_str = current_date.toString('yyyy-MM-dd')
                
                # 跳过周末
                if skip_holidays and current_date.dayOfWeek() in [6, 7]:  # 6=周六, 7=周日
                    detail_lines.append(f"{date_str}: 跳过 - 周末")
                    current_date = current_date.addDays(1)
                    continue
                
                # 跳过法定节假日
                if skip_holidays and self.is_chinese_holiday(current_date):
                    detail_lines.append(f"{date_str}: 跳过 - 法定节假日")
                    current_date = current_date.addDays(1)
                    continue
                
                yesterday = current_date.addDays(-1)
                yesterday_str = yesterday.toString('yyyy-MM-dd')
                
                cursor.execute('''
                    SELECT COUNT(*) FROM ladder_nodes WHERE date = ?
                ''', (yesterday_str,))
                yesterday_has_ladder = cursor.fetchone()[0] > 0
                
                cursor.execute('''
                    SELECT COUNT(*) FROM ladder_nodes WHERE date = ?
                ''', (date_str,))
                today_has_ladder = cursor.fetchone()[0] > 0
                
                if yesterday_has_ladder and today_has_ladder:
                    cursor.execute('''
                        SELECT s.code, s.name, ln.node_level
                        FROM ladder_stocks ls
                        JOIN ladder_nodes ln ON ls.ladder_node_id = ln.id
                        JOIN stocks s ON ls.stock_id = s.id
                        WHERE ln.date = ?
                    ''', (yesterday_str,))
                    yesterday_ladder_stocks = cursor.fetchall()
                    yesterday_ladder_codes = set([stock[0] for stock in yesterday_ladder_stocks])
                    
                    cursor.execute('''
                        SELECT s.code, s.name, ln.node_level
                        FROM ladder_stocks ls
                        JOIN ladder_nodes ln ON ls.ladder_node_id = ln.id
                        JOIN stocks s ON ls.stock_id = s.id
                        WHERE ln.date = ?
                    ''', (date_str,))
                    today_ladder_stocks = cursor.fetchall()
                    today_ladder_codes = set([stock[0] for stock in today_ladder_stocks])
                    
                    if yesterday_ladder_codes:
                        result = self.run_correlation_for_date(date_str, yesterday_str)
                        
                        if result and len(result) >= top_n:
                            top_n_stocks = result[:top_n]
                            
                            # 打印前N名股票及其排名（用于调试）
                            top_n_info = ", ".join([f"{i+1}.{stock[0]}({stock[2]:.1f})" for i, stock in enumerate(top_n_stocks)])
                            
                            # 根据排名计算得分：第1名命中得5分，第2名得4分，依此类推
                            day_score = 0
                            hit_stocks = []
                            for rank, stock in enumerate(top_n_stocks, 1):
                                code = stock[0]
                                if code in today_ladder_codes:
                                    # 根据排名获取得分
                                    score_for_rank = self.BACKTEST_RANK_SCORES.get(rank, 0)
                                    day_score += score_for_rank
                                    hit_stocks.append(f"{rank}.{code}(+{score_for_rank})")
                            
                            if day_score > 0:
                                total_score += day_score
                                total_valid_stocks += len(hit_stocks)
                                valid_days += 1
                                detail_lines.append(f"{date_str}: 有效 (+{day_score}) - 前{top_n}名: [{top_n_info}] - 命中: {', '.join(hit_stocks)}")
                            else:
                                detail_lines.append(f"{date_str}: 无效 (0) - 前{top_n}名: [{top_n_info}] - 未命中")
                        else:
                            detail_lines.append(f"{date_str}: 无效 (0) - 关联性分析结果不足{top_n}只")
                    else:
                        detail_lines.append(f"{date_str}: 跳过 - 昨天无梯队股票")
                else:
                    detail_lines.append(f"{date_str}: 跳过 - 昨天或今天无梯队")
                
                total_count += 1
                current_date = current_date.addDays(1)
            
            conn.close()
            
            l2_penalty = self._l2_regularization_penalty()
            final_score = total_score - l2_penalty
            
            detail_lines.append(f'')
            detail_lines.append(f'L2正则惩罚: -{l2_penalty:.4f}')
            detail_lines.append(f'原始回测得分: {total_score:.2f}')
            detail_lines.append(f'最终得分(含正则): {final_score:.2f}')
            
            # 保存回测结果到数据库
            try:
                conn2 = sqlite3.connect(self.db_path)
                cursor2 = conn2.cursor()
                cursor2.execute('''
                    INSERT INTO backtest_results 
                    (test_date, start_date, end_date, total_score, valid_days, total_valid_stocks, 
                     top_n, valid_score, stock_weight_first, stock_weight_last, board_weight,
                     rush_pct_coefficient, rush_letter_attr_weight, rush_region_attr_weight, rush_market_cap_coefficient, yz_overall_weight, attr_count_weight, negative_attr_count_weight, board_press_weight, negative_attr_weight, negative_letter_attr_weight, negative_region_attr_weight, node_guide_weight, holder_ratio_weight, detail_text)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''', (
                    QDate.currentDate().toString('yyyy-MM-dd'),
                    start_date.toString('yyyy-MM-dd'),
                    end_date.toString('yyyy-MM-dd'),
                    final_score,
                    valid_days,
                    total_valid_stocks,
                    top_n,
                    valid_score,
                    self.STOCK_WEIGHT_FIRST,
                    self.STOCK_WEIGHT_LAST,
                    self.BOARD_WEIGHT,

                    self.RUSH_PCT_COEFFICIENT,
                    self.RUSH_LETTER_ATTR_WEIGHT,
                    self.RUSH_REGION_ATTR_WEIGHT,
                    self.RUSH_MARKET_CAP_COEFFICIENT,
                    self.YZ_OVERALL_WEIGHT,
                    self.ATTR_COUNT_WEIGHT,
                    self.NEGATIVE_ATTR_COUNT_WEIGHT,
                    self.BOARD_PRESS_WEIGHT,
                    self.NEGATIVE_ATTR_WEIGHT,
                    self.NEGATIVE_LETTER_ATTR_WEIGHT,
                    self.NEGATIVE_REGION_ATTR_WEIGHT,
                    self.NODE_GUIDE_WEIGHT,
                    self.HOLDER_RATIO_WEIGHT,
                    '\n'.join(detail_lines)
                ))
                conn2.commit()
                conn2.close()
            except Exception as e:
                print(f"Error saving backtest result: {e}")
            
            self.backtest_result_label.setText(f'回测结果: 总得分 {final_score} (回测范围 {total_count} 个交易日, 有效 {valid_days} 天, 命中 {total_valid_stocks} 只)')
            self.backtest_detail_text.setPlainText('\n'.join(detail_lines))
            
        except Exception as e:
            self.backtest_result_label.setText(f'回测结果: 错误 - {str(e)}')
            print(f"Error in backtest: {e}")
            import traceback
            traceback.print_exc()

    def view_backtest_history(self):
        """查看历史回测结果"""
        from PyQt5.QtWidgets import QDialog, QTableWidget, QTableWidgetItem, QVBoxLayout, QPushButton, QHBoxLayout, QAbstractItemView
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            cursor.execute('''
                SELECT id, test_date, start_date, end_date, total_score, valid_days,
                       total_valid_stocks, top_n, valid_score,
                       stock_weight_first, stock_weight_last, board_weight,
                       yz_overall_weight, attr_count_weight, negative_attr_count_weight, board_press_weight, negative_attr_weight, negative_letter_attr_weight, negative_region_attr_weight, node_guide_weight, holder_ratio_weight
                FROM backtest_results
                ORDER BY create_time DESC
                LIMIT 50
            ''')
            results = cursor.fetchall()
            conn.close()
            
            if not results:
                QMessageBox.information(self, '历史回测', '暂无回测记录')
                return
            
            # 创建对话框显示历史记录
            dialog = QDialog(self)
            dialog.setWindowTitle('历史回测结果')
            dialog.setGeometry(200, 200, 1000, 600)
            layout = QVBoxLayout()
            dialog.setLayout(layout)
            
            # 创建表格
            table = QTableWidget()
            table.setColumnCount(21)
            table.setHorizontalHeaderLabels([
                'ID', '测试日期', '开始日期', '结束日期', '总得分', '有效天数',
                '命中股票数', '前N名', '有效得分',
                '首股权重', '末股权重', '板数权重',
	                '一字板系数', '属性系数', '负反馈数量系数', '同板压制系数', '负反馈其他属性', '负反馈字母属性', '负反馈地区属性', '节点指引系数', '持股比例权重'
            ])
            table.setRowCount(len(results))
            
            for i, row in enumerate(results):
                for j, value in enumerate(row):
                    if isinstance(value, float):
                        item = QTableWidgetItem(f'{value:.2f}')
                    elif value is None:
                        item = QTableWidgetItem('')
                    else:
                        item = QTableWidgetItem(str(value))
                    table.setItem(i, j, item)
            
            table.resizeColumnsToContents()
            table.setColumnWidth(14, 200)  # detail_text列宽一些
            table.setSelectionBehavior(QTableWidget.SelectRows)  # 选择整行
            table.setSelectionMode(QTableWidget.MultiSelection)  # 多行选择（支持Ctrl+点击多选）
            
            layout.addWidget(table)
            
            # 按钮区域
            btn_layout = QHBoxLayout()
            
            # 删除按钮
            delete_btn = QPushButton('删除选中')
            delete_btn.clicked.connect(lambda: self.delete_backtest_record(table, results))
            btn_layout.addWidget(delete_btn)
            
            btn_layout.addStretch()
            
            # 关闭按钮
            close_btn = QPushButton('关闭')
            close_btn.clicked.connect(dialog.close)
            btn_layout.addWidget(close_btn)
            
            layout.addLayout(btn_layout)
            
            dialog.exec_()
            
        except Exception as e:
            QMessageBox.warning(self, '错误', f'查看历史回测失败: {str(e)}')
            print(f"Error viewing backtest history: {e}")
            import traceback
            traceback.print_exc()

    def is_chinese_holiday(self, date):
        """判断是否为中国的法定节假日"""
        try:
            from chinese_calendar import is_holiday
            # 转换QDate为Python date对象
            python_date = date.toPyDate()
            return is_holiday(python_date)
        except ImportError:
            month = date.month()
            day = date.day()
            
            fixed_holidays = [
                (1, 1),
                (5, 1),
                (10, 1), (10, 2), (10, 3),
            ]
            
            if (month, day) in fixed_holidays:
                return True
            
            if month == 1 and 21 <= day <= 27:
                return True
            if month == 2 and 1 <= day <= 10:
                return True
            if month == 4 and day in [4, 5]:
                return True
            if month == 5 and day == 5:
                return True
            if month == 6 and day in [6, 7]:
                return True
            if month == 9 and day in [14, 15, 16]:
                return True
            
            return False

    def delete_backtest_record(self, table, results):
        """删除选中的回测记录（支持多选）"""
        selected_items = table.selectedItems()
        if not selected_items:
            QMessageBox.information(self, '提示', '请先选择要删除的记录')
            return
        
        # 获取所有选中的行（去重）
        selected_row_indices = set()
        for item in selected_items:
            selected_row_indices.add(item.row())
        
        # 获取要删除的记录ID列表
        record_ids = []
        for row_idx in selected_row_indices:
            record_ids.append(results[row_idx][0])
        
        # 根据选中数量显示不同的确认消息
        if len(record_ids) == 1:
            reply = QMessageBox.question(self, '确认删除', '确定要删除这条回测记录吗？',
                                         QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        else:
            reply = QMessageBox.question(self, '确认删除', f'确定要删除选中的 {len(record_ids)} 条回测记录吗？',
                                         QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        
        if reply == QMessageBox.Yes:
            try:
                conn = sqlite3.connect(self.db_path)
                cursor = conn.cursor()
                
                # 批量删除
                placeholders = ','.join(['?' for _ in record_ids])
                cursor.execute(f'DELETE FROM backtest_results WHERE id IN ({placeholders})', record_ids)
                conn.commit()
                conn.close()
                
                QMessageBox.information(self, '成功', f'成功删除 {len(record_ids)} 条记录')
                self.view_backtest_history()  # 刷新列表
            except Exception as e:
                QMessageBox.warning(self, '错误', f'删除失败: {str(e)}')
                print(f"Error deleting backtest record: {e}")
                import traceback
                traceback.print_exc()

    def run_correlation_for_date(self, today_str, yesterday_str):
        """对指定日期运行关联性分析，返回分析结果"""
        try:
            # 应用因子治理：禁用的因子强制为0（加法权重）或1（乘法因子）
            _gov_backup = {}
            _gov_enabled = getattr(self, '_factor_enabled', {})
            _gov_mul = {'board_press_weight', 'node_guide_weight'}
            for _key, _attr in [('stock_weight_first', 'STOCK_WEIGHT_FIRST'),
                                ('stock_weight_last', 'STOCK_WEIGHT_LAST'),
                                ('board_weight', 'BOARD_WEIGHT'),

                            ('rush_pct_coefficient', 'RUSH_PCT_COEFFICIENT'),
                                ('rush_letter_attr_weight', 'RUSH_LETTER_ATTR_WEIGHT'),
                                ('rush_region_attr_weight', 'RUSH_REGION_ATTR_WEIGHT'),
                                ('rush_market_cap_coefficient', 'RUSH_MARKET_CAP_COEFFICIENT'),
                                ('yz_overall_weight', 'YZ_OVERALL_WEIGHT'),
                                ('letter_attr_weight', 'LETTER_ATTR_WEIGHT'),
                                ('region_attr_weight', 'REGION_ATTR_WEIGHT'),
                                ('attr_count_weight', 'ATTR_COUNT_WEIGHT'),
                                ('negative_attr_count_weight', 'NEGATIVE_ATTR_COUNT_WEIGHT'),
                                ('negative_attr_weight', 'NEGATIVE_ATTR_WEIGHT'),
							('negative_letter_attr_weight', 'NEGATIVE_LETTER_ATTR_WEIGHT'),
							('negative_region_attr_weight', 'NEGATIVE_REGION_ATTR_WEIGHT'),
                                ('board_press_weight', 'BOARD_PRESS_WEIGHT'),
                                ('node_guide_weight', 'NODE_GUIDE_WEIGHT'),
                                ('holder_ratio_weight', 'HOLDER_RATIO_WEIGHT'),
                                ('market_cap_weight', 'MARKET_CAP_WEIGHT'),
                                ('market_cap_exponent', 'MARKET_CAP_EXPONENT')]:
                if not _gov_enabled.get(_key, True):
                    _gov_backup[_attr] = getattr(self, _attr)
                    setattr(self, _attr, 1.0 if _key in _gov_mul else 0.0)

            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            # 获取竞价记录（仅一字板和负反馈），计算每个属性的数量（加权得分）
            cursor.execute('''
                SELECT br.id, sa.attribute, br.is_additive, br.type
                FROM bidding_records br
                JOIN stock_attributes sa ON br.stock_id = sa.stock_id
                WHERE br.date = ? AND br.type IN (1, 3)
                ORDER BY br.id
            ''', (today_str,))
            records = cursor.fetchall()
            
            if not records:
                conn.close()
                return []
            
            # 计算加权得分
            attr_weighted_count = {}
            attr_weighted_count_yz = {}
            attr_weighted_count_letter = {}
            attr_weighted_count_region = {}
            attr_weighted_count_qc = {}  # 抢筹其他属性得分
            attr_weighted_count_qc_letter = {}  # 抢筹字母属性得分
            attr_weighted_count_qc_region = {}  # 抢筹地区属性得分
            attr_weighted_count_ff = {}
            
            # 统计每个属性的加减次数
            attr_additive_counts = {}
            attr_subtractive_counts = {}
            
            stock_attrs = {}
            stock_attrs_yz = {}
            stock_attrs_ff = {}
            stock_is_additive = {}
            for record_id, attr, is_additive, type in records:
                if record_id not in stock_attrs:
                    stock_attrs[record_id] = []
                    stock_attrs_yz[record_id] = []
                    stock_attrs_ff[record_id] = []
                    stock_is_additive[record_id] = is_additive
                stock_attrs[record_id].append(attr)
                if type == 1:
                    stock_attrs_yz[record_id].append(attr)
                elif type == 3:
                    stock_attrs_ff[record_id].append(attr)
                
                # 统计加减次数
                if is_additive:
                    attr_additive_counts[attr] = attr_additive_counts.get(attr, 0) + 1
                else:
                    attr_subtractive_counts[attr] = attr_subtractive_counts.get(attr, 0) + 1
            
            total_stocks = len(stock_attrs)
            stock_weights = {}
            for idx, stock_id in enumerate(stock_attrs.keys()):
                if total_stocks > 1:
                    weight_range = self.STOCK_WEIGHT_FIRST - self.STOCK_WEIGHT_LAST
                    weight = self.STOCK_WEIGHT_FIRST - (idx / (total_stocks - 1)) * weight_range
                else:
                    weight = self.STOCK_WEIGHT_FIRST
                stock_weights[stock_id] = weight
            
            for stock_id, attrs in stock_attrs.items():
                weight = stock_weights[stock_id]
                is_additive = stock_is_additive[stock_id]
                weight_factor = 1 if is_additive else -1
                
                # 一字板属性 - 去重处理，每个属性最多计1次；将字母属性（拼音首字母）与非字母属性分离
                unique_yz_attrs = set(stock_attrs_yz[stock_id])
                for attr in unique_yz_attrs:
                    if attr not in attr_weighted_count:
                        attr_weighted_count[attr] = 0
                    attr_weighted_count[attr] += weight * weight_factor
                    if len(attr) == 1 and attr.isalpha():
                        if attr not in attr_weighted_count_letter:
                            attr_weighted_count_letter[attr] = 0
                        attr_weighted_count_letter[attr] += weight * weight_factor
                    elif attr in REGION_NAMES:
                        if attr not in attr_weighted_count_region:
                            attr_weighted_count_region[attr] = 0
                        attr_weighted_count_region[attr] += weight * weight_factor
                    else:
                        if attr not in attr_weighted_count_yz:
                            attr_weighted_count_yz[attr] = 0
                        attr_weighted_count_yz[attr] += weight * weight_factor
                
                # 负反馈属性 - 按分类使用各自的系数（字母、地区、其他）
                for attr in stock_attrs_ff[stock_id]:
                    if attr not in attr_weighted_count_ff:
                        attr_weighted_count_ff[attr] = 0
                    if attr not in attr_weighted_count:
                        attr_weighted_count[attr] = 0
                    if len(attr) == 1 and attr.isalpha():
                        ff_score = weight * (-1) * self.NEGATIVE_LETTER_ATTR_WEIGHT
                    elif attr in REGION_NAMES:
                        ff_score = weight * (-1) * self.NEGATIVE_REGION_ATTR_WEIGHT
                    else:
                        ff_score = weight * (-1) * self.NEGATIVE_ATTR_WEIGHT
                    attr_weighted_count[attr] += ff_score
                    attr_weighted_count_ff[attr] += ff_score

            # 读取竞价抢筹股票，按其属性加权计算抢筹得分
            cursor.execute('''
                SELECT brs.pct_change, s.id, s.total_market_cap
                FROM bidding_rush_stocks brs
                JOIN stocks s ON brs.stock_id = s.id
                WHERE brs.date = ?
                ORDER BY brs.pct_change DESC
            ''', (today_str,))
            rush_stock_data = cursor.fetchall()
            # skip rush scoring when all rush factors disabled by governance
            _all_rush_disabled = (
                self.RUSH_PCT_COEFFICIENT == 0
                and self.RUSH_LETTER_ATTR_WEIGHT == 0
                and self.RUSH_REGION_ATTR_WEIGHT == 0
                and self.RUSH_MARKET_CAP_COEFFICIENT == 0
            )
            if not _all_rush_disabled:
                for idx, (pct_change, stock_id, total_market_cap) in enumerate(rush_stock_data):
                    # 获取该抢筹股票的属性
                    cursor.execute('SELECT attribute FROM stock_attributes WHERE stock_id = ?', (stock_id,))
                    for row in cursor.fetchall():
                        attr = row[0]
                        if attr in ATTR_REMOVE_SET:
                            continue
                        # 抢筹加分 = 涨幅 x 涨幅系数b x 市值因子（暂不乘属性系数，在第二遍乘以各自的分类系数）
                        market_cap_billion = (total_market_cap / 100000000) if total_market_cap else 50.0
                        rush_score_base = pct_change * self.RUSH_PCT_COEFFICIENT * (market_cap_billion * self.RUSH_MARKET_CAP_COEFFICIENT)
                        if attr not in attr_weighted_count:
                            attr_weighted_count[attr] = 0
                        attr_weighted_count[attr] += rush_score_base
                        if len(attr) == 1 and attr.isalpha():
                            # 字母属性
                            if attr not in attr_weighted_count_qc_letter:
                                attr_weighted_count_qc_letter[attr] = 0
                            attr_weighted_count_qc_letter[attr] += rush_score_base
                        elif attr in REGION_NAMES:
                            # 地区属性
                            if attr not in attr_weighted_count_qc_region:
                                attr_weighted_count_qc_region[attr] = 0
                            attr_weighted_count_qc_region[attr] += rush_score_base
                        else:
                            # 其他属性
                            if attr not in attr_weighted_count_qc:
                                attr_weighted_count_qc[attr] = 0
                            attr_weighted_count_qc[attr] += rush_score_base

            # 计算属性的总出现次数（绝对值）
            attr_total_counts = {}
            for attr in attr_additive_counts:
                attr_total_counts[attr] = attr_additive_counts.get(attr, 0) + attr_subtractive_counts.get(attr, 0)
            for attr in attr_subtractive_counts:
                if attr not in attr_total_counts:
                    attr_total_counts[attr] = attr_additive_counts.get(attr, 0) + attr_subtractive_counts.get(attr, 0)
            
            # 计算最终得分
            for attr in attr_weighted_count:
                add_count = attr_additive_counts.get(attr, 0)
                sub_count = attr_subtractive_counts.get(attr, 0)
                net_count = add_count - sub_count
                total_count = attr_total_counts.get(attr, 0)
                
                # 应用属性数量差距系数：次数越多，放大效果越明显（抢筹和负反馈使用）
                count_factor = total_count ** self.ATTR_COUNT_WEIGHT if total_count > 0 else 1
                # 负反馈属性使用单独的系数
                negative_count_factor = total_count ** self.NEGATIVE_ATTR_COUNT_WEIGHT if total_count > 0 else 1
                
                # 应用到主属性得分字典（不应用数量差距系数）
                attr_weighted_count[attr] *= net_count
                
                # 应用到一字板属性得分字典 - 只与股票顺序有关，不考虑属性数量，只应用一字板整体系数
                if attr in attr_weighted_count_yz:
                    attr_weighted_count_yz[attr] *= self.YZ_OVERALL_WEIGHT
                
                # 应用到字母属性得分字典 - 使用字母属性系数
                if attr in attr_weighted_count_letter:
                    attr_weighted_count_letter[attr] *= self.LETTER_ATTR_WEIGHT
                
                # 应用到地区属性得分字典 - 使用地区属性系数
                if attr in attr_weighted_count_region:
                    attr_weighted_count_region[attr] *= self.REGION_ATTR_WEIGHT
                
                # 应用到抢筹属性得分字典 - 按分类应用各自的属性系数
                if attr in attr_weighted_count_qc_letter:
                    attr_weighted_count_qc_letter[attr] *= self.RUSH_LETTER_ATTR_WEIGHT
                if attr in attr_weighted_count_qc_region:
                    attr_weighted_count_qc_region[attr] *= self.RUSH_REGION_ATTR_WEIGHT

                # 应用到负反馈属性得分字典 - 应用negative_count_factor，保持负号
                if attr in attr_weighted_count_ff:
                    abs_score = abs(attr_weighted_count_ff[attr])
                    attr_weighted_count_ff[attr] = -abs_score * negative_count_factor
            
            cursor.execute('SELECT ladder_count FROM ladder_settings WHERE date = ?', (yesterday_str,))
            yesterday_ladder_setting = cursor.fetchone()
            yesterday_ladder_count = yesterday_ladder_setting[0] if yesterday_ladder_setting else 0
            
            if yesterday_ladder_count == 0:
                cursor.execute('SELECT COUNT(*) FROM ladder_nodes WHERE date = ?', (yesterday_str,))
                node_count = cursor.fetchone()[0]
                yesterday_ladder_count = node_count
            
            cursor.execute('''
                SELECT s.code, s.name, ln.node_level
                FROM ladder_stocks ls
                JOIN ladder_nodes ln ON ls.ladder_node_id = ln.id
                JOIN stocks s ON ls.stock_id = s.id
                WHERE ln.date = ?
                AND s.id NOT IN (
                    SELECT br.stock_id 
                    FROM bidding_records br 
                    WHERE br.date = ?
                )
            ''', (yesterday_str, today_str))
            target_stocks = cursor.fetchall()
            
            # 获取昨天涨停梯队的最大node_level（1板梯队，板数最低）
            cursor.execute('SELECT MAX(node_level) FROM ladder_nodes WHERE date = ?', (yesterday_str,))
            max_node_level_result = cursor.fetchone()
            max_node_level = max_node_level_result[0] if max_node_level_result and max_node_level_result[0] else 0
            
            # 获取今天一字板中在昨天涨停梯队（非1板，即node_level < max）的股票及其所在梯队
            if max_node_level > 1:
                cursor.execute('''
                    SELECT s.code, ln.node_level
                    FROM bidding_records br
                    JOIN stocks s ON br.stock_id = s.id
                    JOIN ladder_stocks ls ON ls.stock_id = s.id
                    JOIN ladder_nodes ln ON ls.ladder_node_id = ln.id
                    WHERE br.date = ? AND br.type = 1
                    AND ln.date = ? AND ln.node_level < ?
                ''', (today_str, yesterday_str, max_node_level))
                yz_board_stocks = cursor.fetchall()
            else:
                yz_board_stocks = []
            
            # 构建同板压制映射：{梯队级别: [同板股票代码]}
            board_press_map = {}
            for code, node_level in yz_board_stocks:
                if node_level not in board_press_map:
                    # 获取该梯队的所有股票
                    cursor.execute('''
                        SELECT s.code
                        FROM ladder_stocks ls
                        JOIN ladder_nodes ln ON ls.ladder_node_id = ln.id
                        JOIN stocks s ON ls.stock_id = s.id
                        WHERE ln.date = ? AND ln.node_level = ?
                    ''', (yesterday_str, node_level))
                    board_stocks = [row[0] for row in cursor.fetchall()]
                    board_press_map[node_level] = board_stocks
            
            # 获取昨天1板和2板梯队的股票（node_level最大的两个梯队）
            yesterday_12board_stocks = []
            if max_node_level > 0:
                # node_level越大板数越低，max_node_level是1板，max_node_level-1是2板
                cursor.execute('''
                    SELECT s.code
                    FROM ladder_stocks ls
                    JOIN ladder_nodes ln ON ls.ladder_node_id = ln.id
                    JOIN stocks s ON ls.stock_id = s.id
                    WHERE ln.date = ? AND ln.node_level >= ?
                ''', (yesterday_str, max_node_level - 1))
                yesterday_12board_stocks = [row[0] for row in cursor.fetchall()]
            
            # 检查竞价记录中是否有股票在昨天1板或2板梯队中
            bidding_codes = []
            cursor.execute('SELECT s.code FROM bidding_records br JOIN stocks s ON br.stock_id = s.id WHERE br.date = ?', (today_str,))
            for row in cursor.fetchall():
                bidding_codes.append(row[0])
            has_1board_guidance = any(code in yesterday_12board_stocks for code in bidding_codes)
            
            scores = []
            for code, name, node_level in target_stocks:
                cursor.execute('SELECT id FROM stocks WHERE code = ?', (code,))
                stock_result = cursor.fetchone()
                if not stock_result:
                    continue
                stock_id = stock_result[0]
                
                is_rushing = False  # 已废弃：改为属性维度抢筹
                
                cursor.execute('SELECT attribute FROM stock_attributes WHERE stock_id = ?', (stock_id,))
                stock_attrs_set = set([row[0] for row in cursor.fetchall()])
                
                attr_total = 0
                for attr in stock_attrs_set:
                    if attr in attr_weighted_count_yz and attr in attr_weighted_count:
                        attr_total += attr_weighted_count_yz[attr] / NORM_YZ_SCORE
                    if attr in attr_weighted_count_letter and attr in attr_weighted_count:
                        attr_total += attr_weighted_count_letter[attr] / NORM_YZ_SCORE
                    if attr in attr_weighted_count_region and attr in attr_weighted_count:
                        attr_total += attr_weighted_count_region[attr] / NORM_REGION_SCORE
                    if attr in attr_weighted_count_qc and attr in attr_weighted_count:
                        attr_total += attr_weighted_count_qc[attr]
                    if attr in attr_weighted_count_ff and attr in attr_weighted_count:
                        attr_total += attr_weighted_count_ff[attr] / NORM_FF_SCORE
                
                # 负反馈整体系数已在属性计算时应用
                
                # 获取股东持股比例得分
                holder_ratio_score = 0
                cursor.execute('SELECT holder_ratio FROM stocks WHERE id = ?', (stock_id,))
                holder_result = cursor.fetchone()
                if holder_result and holder_result[0] is not None:
                    holder_ratio_score = holder_result[0] * self.HOLDER_RATIO_WEIGHT / NORM_HOLDER_RATIO

                # 获取市值得分（市值去掉亿，如100亿按100计算）
                market_cap_score = 0
                cursor.execute('SELECT total_market_cap FROM stocks WHERE id = ?', (stock_id,))
                market_cap_result = cursor.fetchone()
                if market_cap_result and market_cap_result[0] is not None:
                    market_cap_billion = market_cap_result[0] / 100000000
                    market_cap_score = (market_cap_billion / NORM_MARKET_CAP) ** self.MARKET_CAP_EXPONENT * self.MARKET_CAP_WEIGHT

                # 基础得分 = 属性得分总和 + 股东持股比例得分 + 市值得分
                base_score = attr_total + holder_ratio_score + market_cap_score
                
                # 计算板数相关参数
                board_count = 1  # 默认板数为1
                gem_factor = 1   # 默认创业板系数为1
                
                if node_level > 0:
                    board_count = yesterday_ladder_count - (node_level - 1)
                    gem_factor = self.GEM_FACTOR if code.startswith('300') else 1
                
                # 应用同板压制加分（非1板、2板梯队才使用）
                board_press_term = 0
                if node_level < max_node_level - 1:
                    for board_level, board_stocks in board_press_map.items():
                        if board_level == node_level and code in board_stocks:
                            board_press_term = self.BOARD_PRESS_WEIGHT / NORM_BOARD_PRESS
                            break
                
                # 应用节点指引加分
                node_guide_term = 0
                if max_node_level > 0 and has_1board_guidance and node_level >= max_node_level - 1:
                    node_guide_term = self.NODE_GUIDE_WEIGHT / NORM_NODE_GUIDE
                
                # 连板数得分（归一化）
                board_term = (board_count / NORM_BOARD_COUNT) * gem_factor * self.BOARD_WEIGHT
                
                # 最终得分 = 各分量加和
                score = base_score + board_term + board_press_term + node_guide_term
                
                cursor.execute('SELECT rating FROM stock_ratings WHERE stock_id = ?', (stock_id,))
                rating_result = cursor.fetchone()
                if rating_result and rating_result[0] == -1:
                    score *= 0.1

                if score > 0:
                    scores.append((code, name, score))
            
            scores.sort(key=lambda x: x[2], reverse=True)

            for _attr, _val in _gov_backup.items():
                setattr(self, _attr, _val)
            conn.close()
            return scores

        except Exception as e:
            for _attr, _val in _gov_backup.items():
                setattr(self, _attr, _val)
            print(f"Error in run_correlation_for_date: {e}")
            import traceback
            traceback.print_exc()
            return []

    def clear_bidding_records(self):
        try:
            date = self.bidding_date.date().toString('yyyy-MM-dd')
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()

            cursor.execute('DELETE FROM bidding_records WHERE date = ?', (date,))
            cursor.execute('DELETE FROM bidding_rush_stocks WHERE date = ?', (date,))
            conn.commit()
            conn.close()

            self.load_bidding_records()
        except Exception as e:
            print(f"Error clearing bidding records: {e}")

    def clear_rush_stocks(self):
        """清空当前日期竞价抢筹列表"""
        try:
            date = self.bidding_date.date().toString('yyyy-MM-dd')
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            cursor.execute('DELETE FROM bidding_rush_stocks WHERE date = ?', (date,))
            conn.commit()
            conn.close()
            self.load_bidding_records()
        except Exception as e:
            print(f"Error clearing rush stocks: {e}")

    def _ensure_rush_stock(self, cursor, date, stock_id, pct_change):
        """确保股票已写入竞价抢筹表（insert 或 update）"""
        cursor.execute('SELECT id FROM bidding_rush_stocks WHERE date = ? AND stock_id = ?',
                       (date, stock_id))
        if cursor.fetchone():
            cursor.execute('UPDATE bidding_rush_stocks SET pct_change = ? WHERE date = ? AND stock_id = ?',
                           (pct_change, date, stock_id))
        else:
            cursor.execute('INSERT INTO bidding_rush_stocks (date, stock_id, pct_change) VALUES (?, ?, ?)',
                           (date, stock_id, pct_change))

    def _deduplicate_bidding_data(self, conn, dates):
        """后处理：对指定日期列表，去除抢筹表中与一字板重复的股票（一字板股票不应同时出现在抢筹里）"""
        cursor = conn.cursor()
        total_removed = 0
        for qdate in dates:
            date_str = qdate.toString('yyyy-MM-dd')
            # 找出一字板(type=1)和抢筹(type=2)在同一日期共存的股票
            cursor.execute('''
                SELECT br2.stock_id
                FROM bidding_records br1
                JOIN bidding_records br2 ON br1.date = br2.date AND br1.stock_id = br2.stock_id
                WHERE br1.date = ? AND br1.type = 1 AND br2.type = 2
            ''', (date_str,))
            dup_stock_ids = [row[0] for row in cursor.fetchall()]
            if not dup_stock_ids:
                continue
            # 删除 bidding_rush_stocks 中对应记录
            placeholders = ','.join(['?'] * len(dup_stock_ids))
            cursor.execute(f'''
                DELETE FROM bidding_rush_stocks
                WHERE date = ? AND stock_id IN ({placeholders})
            ''', (date_str, *dup_stock_ids))
            removed_rush = cursor.rowcount
            # 删除 bidding_records 中对应的抢筹(type=2)记录
            cursor.execute(f'''
                DELETE FROM bidding_records
                WHERE date = ? AND type = 2 AND stock_id IN ({placeholders})
            ''', (date_str, *dup_stock_ids))
            removed_rec = cursor.rowcount
            total_removed += removed_rush
            if removed_rush > 0:
                print(f'[去重] {date_str}: 移除 {removed_rush} 条抢筹记录（与一字板重复）')
        conn.commit()
        return total_removed

    def _batch_create_rush_missing_stocks(self, stock_list, date):
        """批量创建不在本地库的股票并获取板块属性"""
        import requests
        from PyQt5.QtWidgets import QProgressDialog

        progress = QProgressDialog('正在创建股票并获取属性...', '取消', 0, len(stock_list), self)
        progress.setWindowTitle('批量创建股票')
        progress.setWindowModality(2)
        progress.setMinimumDuration(0)

        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        for i, item in enumerate(stock_list):
            if progress.wasCanceled():
                break

            code, name = item[0], item[1]
            pct_change = item[2] if len(item) >= 3 else 5.0

            progress.setValue(i)
            progress.setLabelText(f'正在处理 {name}({code})  ({i+1}/{len(stock_list)})')

            try:
                # 检查是否已被其他操作插入
                cursor.execute('SELECT id FROM stocks WHERE code = ?', (code,))
                existing = cursor.fetchone()
                if existing:
                    # 已有但未加入抢筹表，加入
                    self._ensure_rush_stock(cursor, date, existing[0], pct_change)
                    continue

                cursor.execute('INSERT INTO stocks (code, name) VALUES (?, ?)', (code, name))
                conn.commit()
                cursor.execute('SELECT id FROM stocks WHERE code = ?', (code,))
                stock_id = cursor.fetchone()[0]

                # 加入竞价抢筹列表
                self._ensure_rush_stock(cursor, date, stock_id, pct_change)

                # 添加拼音首字母属性
                self.add_pinyin_initial_attributes(stock_id, name, conn, cursor)

                # 从东方财富获取板块属性
                if code.startswith('920'):
                    market = 'BJ'
                elif code.startswith('60') or code.startswith('688'):
                    market = 'SH'
                else:
                    market = 'SZ'
                secucode = f'{code}.{market}'

                headers = {
                    'Accept': '*/*',
                    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
                }

                board_names = []
                try:
                    url = (
                        "https://datacenter.eastmoney.com/securities/api/data/v1/get?"
                        "reportName=RPT_F10_CORETHEME_BOARDTYPE&"
                        "columns=SECUCODE,SECURITY_CODE,SECURITY_NAME_ABBR,NEW_BOARD_CODE,BOARD_NAME,SELECTED_BOARD_REASON,IS_PRECISE,BOARD_RANK,BOARD_YIELD,DERIVE_BOARD_CODE&"
                        f"filter=(SECUCODE%3D%22{secucode}%22)(IS_PRECISE%3D%221%22)&"
                        "pageNumber=1&pageSize=200&sortTypes=1&sortColumns=BOARD_RANK&"
                        "source=HSF10&client=PC&v=05407725378079107"
                    )
                    resp = requests.get(url, headers=headers, timeout=10)
                    data = resp.json()
                    for item in (data.get('result') or {}).get('data', []):
                        bn = item.get('BOARD_NAME', '')
                        if bn and bn not in board_names:
                            board_names.append(bn)
                except Exception as e:
                    print(f'获取 {code} 板块属性失败: {e}')

                # 用第二个接口补充
                try:
                    url2 = (
                        "https://datacenter.eastmoney.com/securities/api/data/get?"
                        "type=RPT_F10_CORETHEME_BOARDTYPE&sty=ALL&"
                        f"filter=(SECUCODE%3D%22{secucode}%22)&"
                        "p=1&ps=200&sr=1&st=BOARD_RANK&"
                        "source=HSF10&client=PC&v=04828710067581792"
                    )
                    resp2 = requests.get(url2, headers=headers, timeout=10)
                    data2 = resp2.json()
                    for item in data2.get('result', {}).get('data', []):
                        bn = item.get('BOARD_NAME', '')
                        if bn and bn not in board_names:
                            board_names.append(bn)
                except Exception as e:
                    print(f'获取 {code} 板块属性失败(接口2): {e}')

                # 保存属性到数据库
                for board_name in board_names:
                    cursor.execute(
                        'INSERT INTO stock_attributes (stock_id, date, attribute) VALUES (?, ?, ?)',
                        (stock_id, date, board_name)
                    )
                conn.commit()
            except Exception as e:
                print(f"创建股票 {code} {name} 失败: {e}")

        conn.close()
        progress.setValue(len(stock_list))

    def _call_rush_api(self, qdate):
        """调用问财API获取指定日期的竞价数据，返回 (datas, gain_key, components)"""
        date_str = qdate.toString('yyyy-MM-dd')
        qc_date = qdate.toString('yyyy.M.d')
        gain_key = f'竞价涨幅[{qdate.toString("yyyyMMdd")}]'
        question = f'{qc_date}竞价涨幅大于5%;按照竞价涨幅排序'

        url = 'https://www.iwencai.com/unifiedwap/unified-wap/v2/result/get-robot-data'
        headers = {
            'Accept': 'application/json, text/plain, */*',
            'Content-Type': 'application/x-www-form-urlencoded',
            'Origin': 'https://www.iwencai.com',
            'hexin-v': self.hexin_v,
            'Cookie': 'chat_bot_session_id=1cb8476c0ca664fc1761430187cd06b1; other_uid=Ths_iwencai_Xuangu_946086f7d300ab583cb90ff02ea2d227',
            'User-Agent': (
                'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                'AppleWebKit/537.36 (KHTML, like Gecko) '
                'Chrome/148.0.0.0 Safari/537.36 Edg/148.0.0.0'
            ),
        }
        post_data = {
            'source': 'Ths_iwencai_Xuangu',
            'version': '2.0',
            'query_area': '',
            'block_list': '',
            'add_info': '{"urp":{"scene":1,"company":1,"business":1},"contentType":"json","searchInfo":true}',
            'question': question,
            'perpage': '50',
            'page': '1',
            'secondary_intent': 'stock',
            'log_info': '{"input_type":"typewrite"}',
            'rsh': 'Ths_iwencai_Xuangu_pdqany9yolmgyksxprq8mpymlluypssu',
        }

        response = requests.post(url, headers=headers, data=post_data, timeout=15)
        result = response.json()

        answer = result.get('data', {}).get('answer', [])
        if not answer:
            raise Exception('问财接口未返回数据')

        txt_list = answer[0].get('txt', [])
        if not txt_list:
            raise Exception('接口返回格式异常（txt为空）')

        components = txt_list[0].get('content', {}).get('components', [])
        if not components:
            raise Exception('接口返回格式异常（components为空）')

        datas = components[0].get('data', {}).get('datas', [])
        return datas, gain_key, components

    def fetch_morning_rush_stocks(self):
        """通过问财接口获取指定日期竞价涨幅大于5%的股票，按竞价涨幅排序"""
        qdate = self.bidding_date.date()
        date = qdate.toString('yyyy-MM-dd')

        if qdate.dayOfWeek() in [6, 7]:
            QMessageBox.warning(self, '提示', f'{date} 是周末，当天不开盘')
            return
        if self.is_chinese_holiday(qdate):
            QMessageBox.warning(self, '提示', f'{date} 是法定节假日，当天不开盘')
            return

        try:
            datas, gain_key, components = self._call_rush_api(qdate)
            if not datas:
                QMessageBox.warning(self, '提示', '未获取到股票数据')
                return

            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            added = 0
            missing_stocks = []

            for item in datas:
                if added >= MAX_RUSH_ATTRS:
                    break
                code = str(item.get('code', '')).zfill(6)
                pct_change = float(item.get(gain_key, 0))

                if pct_change < 5.0:
                    continue
                if pct_change > 35.0:
                    continue
                if not code:
                    continue

                # 解析股票名称，用于 ST 过滤
                stock_name = item.get('股票名称', '') or item.get('name', '')
                if not stock_name and components:
                    for col in components[0].get('data', {}).get('columns', []):
                        if col.get('label') in ('name', '股票名称'):
                            stock_name = item.get(col.get('key', ''), '')
                            break
                if '*ST' in stock_name or 'ST' in stock_name:
                    continue

                cursor.execute('SELECT id FROM stocks WHERE code = ?', (code,))
                stock = cursor.fetchone()
                if stock:
                    stock_id = stock[0]
                    cursor.execute(
                        'SELECT id FROM bidding_records WHERE date = ? AND stock_id = ? AND type = 1',
                        (date, stock_id))
                    if cursor.fetchone():
                        continue
                    self._ensure_rush_stock(cursor, date, stock_id, pct_change)
                    added += 1
                else:
                    if stock_name:
                        missing_stocks.append((code, stock_name, pct_change))
                        added += 1  # 也计入限制

            conn.commit()
            conn.close()

            msg = f'从问财获取到 {added} 只竞价涨幅>5%的股票，已填入竞价抢筹列表'
            if missing_stocks:
                msg += f'\n\n发现 {len(missing_stocks)} 只股票不在本地库中：'
                for mc, mn, _ in missing_stocks[:10]:
                    msg += f'\n  {mc} {mn}'
                if len(missing_stocks) > 10:
                    msg += f'\n  ...等共{len(missing_stocks)}只'
                msg += '\n\n是否一键创建并获取它们的板块属性？'

                reply = QMessageBox.question(self, '发现新股票', msg,
                                             QMessageBox.Yes | QMessageBox.No)
                if reply == QMessageBox.Yes:
                    self._batch_create_rush_missing_stocks(missing_stocks, date)
                    self.load_bidding_records()
                    QMessageBox.information(self, '完成', f'已创建 {len(missing_stocks)} 只新股票并获取其板块属性')
            else:
                QMessageBox.information(self, '成功', msg)

            self.load_bidding_records()

        except requests.exceptions.Timeout:
            QMessageBox.warning(self, '错误', '请求超时，请检查网络')
        except Exception as e:
            import traceback
            traceback.print_exc()
            err_msg = str(e)
            try:
                err_msg += f'\n响应状态: {getattr(response, "status_code", "?")}'
                print(f'Response text: {getattr(response, "text", "?")[:500]}')
            except Exception:
                pass

            is_auth_error = (
                'JSONDecodeError' in err_msg or
                'Expecting value' in err_msg or
                '403' in err_msg
            )
            if is_auth_error:
                reply = QMessageBox.warning(self, '令牌过期',
                    '问财接口返回异常，可能是 hexin-v 令牌已过期。\n'
                    '是否打开输入框更新 hexin-v？\n\n'
                    '（从浏览器 F12 → Network 抓取 get-robot-data 请求的 hexin-v 请求头）',
                    QMessageBox.Yes | QMessageBox.No)
                if reply == QMessageBox.Yes:
                    self.hexin_v_input.setFocus()
                    self.hexin_v_input.selectAll()
            else:
                QMessageBox.warning(self, '错误', f'获取数据失败: {err_msg[:200]}')

    def _call_yiziban_api(self, qdate):
        """调用问财API获取指定日期的竞价一字板（竞价涨停）数据"""
        qc_date = qdate.toString('yyyy.M.d')
        question = f'{qc_date}竞价涨停，按封单量从大到小排序'

        url = 'https://www.iwencai.com/unifiedwap/unified-wap/v2/result/get-robot-data'
        headers = {
            'Accept': 'application/json, text/plain, */*',
            'Content-Type': 'application/x-www-form-urlencoded',
            'Origin': 'https://www.iwencai.com',
            'hexin-v': self.hexin_v,
            'Cookie': 'chat_bot_session_id=1cb8476c0ca664fc1761430187cd06b1; other_uid=Ths_iwencai_Xuangu_946086f7d300ab583cb90ff02ea2d227',
            'User-Agent': (
                'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                'AppleWebKit/537.36 (KHTML, like Gecko) '
                'Chrome/148.0.0.0 Safari/537.36 Edg/148.0.0.0'
            ),
        }
        post_data = {
            'source': 'Ths_iwencai_Xuangu',
            'version': '2.0',
            'query_area': '',
            'block_list': '',
            'add_info': '{"urp":{"scene":1,"company":1,"business":1},"contentType":"json","searchInfo":true}',
            'question': question,
            'perpage': '50',
            'page': '1',
            'secondary_intent': 'stock',
            'log_info': '{"input_type":"typewrite"}',
            'rsh': 'Ths_iwencai_Xuangu_pdqany9yolmgyksxprq8mpymlluypssu',
        }

        response = requests.post(url, headers=headers, data=post_data, timeout=15)
        result = response.json()

        answer = result.get('data', {}).get('answer', [])
        if not answer:
            raise Exception('问财接口未返回数据')

        txt_list = answer[0].get('txt', [])
        if not txt_list:
            raise Exception('接口返回格式异常（txt为空）')

        components = txt_list[0].get('content', {}).get('components', [])
        if not components:
            raise Exception('接口返回格式异常（components为空）')

        datas = components[0].get('data', {}).get('datas', [])
        return datas, components

    def fetch_yiziban_stocks(self):
        """通过问财接口获取指定日期竞价涨停（一字板）股票"""
        qdate = self.bidding_date.date()
        date = qdate.toString('yyyy-MM-dd')

        if qdate.dayOfWeek() in [6, 7]:
            QMessageBox.warning(self, '提示', f'{date} 是周末，当天不开盘')
            return
        if self.is_chinese_holiday(qdate):
            QMessageBox.warning(self, '提示', f'{date} 是法定节假日，当天不开盘')
            return

        try:
            datas, components = self._call_yiziban_api(qdate)
            if not datas:
                QMessageBox.warning(self, '提示', '未获取到股票数据')
                return

            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            added = 0
            missing_stocks = []

            for item in datas:
                code = str(item.get('code', '')).zfill(6)
                if not code:
                    continue

                # 解析股票名称用于 ST 过滤
                stock_name = item.get('股票名称', '') or item.get('name', '')
                if not stock_name and components:
                    for col in components[0].get('data', {}).get('columns', []):
                        if col.get('label') in ('name', '股票名称'):
                            stock_name = item.get(col.get('key', ''), '')
                            break
                if '*ST' in stock_name or 'ST' in stock_name:
                    continue

                cursor.execute('SELECT id FROM stocks WHERE code = ?', (code,))
                stock = cursor.fetchone()
                if stock:
                    stock_id = stock[0]
                    cursor.execute(
                        'SELECT id FROM bidding_records WHERE date = ? AND stock_id = ? AND type = 1',
                        (date, stock_id))
                    if cursor.fetchone():
                        continue
                    cursor.execute(
                        'INSERT INTO bidding_records (date, stock_id, is_additive, type) VALUES (?, ?, 1, 1)',
                        (date, stock_id))
                    added += 1
                else:
                    name = stock_name
                    if not name and components:
                        for col in components[0].get('data', {}).get('columns', []):
                            if col.get('label') in ('name', '股票名称'):
                                name = item.get(col.get('key', ''), '')
                                break
                    if name:
                        missing_stocks.append((code, name))

            conn.commit()
            conn.close()

            msg = f'从问财获取到 {added} 只竞价涨停股票，已填入竞价一字板列表'
            if missing_stocks:
                msg += f'\n\n发现 {len(missing_stocks)} 只股票不在本地库中：'
                for mc, mn in missing_stocks[:10]:
                    msg += f'\n  {mc} {mn}'
                if len(missing_stocks) > 10:
                    msg += f'\n  ...等共{len(missing_stocks)}只'
                msg += '\n\n是否一键创建并获取它们的板块属性？'
                reply = QMessageBox.question(self, '发现新股票', msg,
                                             QMessageBox.Yes | QMessageBox.No)
                if reply == QMessageBox.Yes:
                    missing_with_pct = [(c, n, 10.0) for c, n in missing_stocks]
                    self._batch_create_rush_missing_stocks(missing_with_pct, date)
                    self.load_bidding_records()
                    QMessageBox.information(self, '完成', f'已创建 {len(missing_stocks)} 只新股票并获取其板块属性')
            else:
                QMessageBox.information(self, '成功', msg)

            self.load_bidding_records()

        except requests.exceptions.Timeout:
            QMessageBox.warning(self, '错误', '请求超时，请检查网络')
        except Exception as e:
            import traceback
            traceback.print_exc()
            err_msg = str(e)
            try:
                err_msg += f'\n响应状态: {getattr(response, "status_code", "?")}'
            except Exception:
                pass
            is_auth_error = (
                'JSONDecodeError' in err_msg or
                'Expecting value' in err_msg or
                '403' in err_msg
            )
            if is_auth_error:
                reply = QMessageBox.warning(self, '令牌过期',
                    '问财接口返回异常，可能是 hexin-v 令牌已过期。\n'
                    '是否打开输入框更新 hexin-v？',
                    QMessageBox.Yes | QMessageBox.No)
                if reply == QMessageBox.Yes:
                    self.hexin_v_input.setFocus()
                    self.hexin_v_input.selectAll()
            else:
                QMessageBox.warning(self, '错误', f'获取数据失败: {err_msg[:200]}')

    def batch_fetch_morning_rush_stocks(self):
        """批量获取多日早盘竞价数据"""
        from PyQt5.QtWidgets import QDialog, QVBoxLayout, QHBoxLayout, QDateEdit, QDialogButtonBox

        dialog = QDialog(self)
        dialog.setWindowTitle('批量获取早盘竞价')
        dialog.setMinimumWidth(350)

        layout = QVBoxLayout(dialog)
        range_layout = QHBoxLayout()
        range_layout.addWidget(QLabel('起始日期:'))
        start_date = QDateEdit()
        start_date.setCalendarPopup(True)
        start_date.setDate(QDate.currentDate().addDays(-5))
        range_layout.addWidget(start_date)
        range_layout.addWidget(QLabel('结束日期:'))
        end_date = QDateEdit()
        end_date.setCalendarPopup(True)
        end_date.setDate(QDate.currentDate())
        range_layout.addWidget(end_date)
        layout.addLayout(range_layout)

        btn_box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btn_box.accepted.connect(dialog.accept)
        btn_box.rejected.connect(dialog.reject)
        layout.addWidget(btn_box)

        if dialog.exec_() != QDialog.Accepted:
            return

        sd = start_date.date()
        ed = end_date.date()
        if sd > ed:
            QMessageBox.warning(self, '提示', '起始日期不能晚于结束日期')
            return

        # 计算交易日数量
        trading_dates = []
        d = sd
        while d <= ed:
            if d.dayOfWeek() not in [6, 7] and not self.is_chinese_holiday(d):
                trading_dates.append(d)
            d = d.addDays(1)

        if not trading_dates:
            QMessageBox.warning(self, '提示', '所选区间内无交易日')
            return

        total = len(trading_dates)
        reply = QMessageBox.question(self, '确认',
            f'所选区间共 {total} 个交易日，将逐个获取早盘竞价数据。\n'
            f'请确保 hexin-v 令牌有效。\n\n是否继续？',
            QMessageBox.Yes | QMessageBox.No)
        if reply != QMessageBox.Yes:
            return

        from PyQt5.QtWidgets import QProgressDialog
        progress = QProgressDialog('正在批量获取...', '取消', 0, total, self)
        progress.setWindowTitle('批量获取早盘竞价')
        progress.setWindowModality(2)
        progress.setMinimumDuration(0)

        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        total_added = 0
        total_skipped = 0
        all_missing_stocks = {}  # (code, name) -> pct_change for latest date
        errors = []

        for i, qdate in enumerate(trading_dates):
            if progress.wasCanceled():
                break

            date_str = qdate.toString('yyyy-MM-dd')
            progress.setValue(i)
            progress.setLabelText(f'[{i+1}/{total}] {date_str}')

            try:
                datas, gain_key, components = self._call_rush_api(qdate)
                if not datas:
                    continue

                added = 0
                for item in datas:
                    if added >= MAX_RUSH_ATTRS:
                        break
                    code = str(item.get('code', '')).zfill(6)
                    pct_change = float(item.get(gain_key, 0))

                    if pct_change < 5.0 or pct_change > 35.0 or not code:
                        continue

                    # 过滤 ST 股票
                    stock_name = item.get('股票名称', '') or item.get('name', '')
                    if not stock_name and components:
                        for col in components[0].get('data', {}).get('columns', []):
                            if col.get('label') in ('name', '股票名称'):
                                stock_name = item.get(col.get('key', ''), '')
                                break
                    if '*ST' in stock_name or 'ST' in stock_name:
                        continue

                    cursor.execute('SELECT id FROM stocks WHERE code = ?', (code,))
                    stock = cursor.fetchone()
                    if stock:
                        stock_id = stock[0]
                        cursor.execute(
                            'SELECT id FROM bidding_records WHERE date = ? AND stock_id = ? AND type = 1',
                            (date_str, stock_id))
                        if cursor.fetchone():
                            total_skipped += 1
                            continue
                        self._ensure_rush_stock(cursor, date_str, stock_id, pct_change)
                        added += 1
                    else:
                        if stock_name and (code, stock_name) not in all_missing_stocks:
                            all_missing_stocks[(code, stock_name)] = pct_change
                            added += 1  # 也计入限制

                total_added += added
                conn.commit()

                import time
                time.sleep(1.5)  # 避免请求过快

            except Exception as e:
                errors.append(f'{date_str}: {str(e)[:80]}')
                continue

        conn.close()
        progress.setValue(total)

        # 汇总信息
        summary = f'批量获取完成！\n\n共处理 {total} 个交易日\n成功写入 {total_added} 条记录'
        if total_skipped > 0:
            summary += f'\n跳过（竞价一字板）{total_skipped} 条'
        if all_missing_stocks:
            summary += f'\n发现 {len(all_missing_stocks)} 只不在本地库的股票'
        if errors:
            summary += f'\n\n{len(errors)} 个日期获取失败（已跳过）：'
            for e in errors[:5]:
                summary += f'\n  {e}'

        if all_missing_stocks:
            summary += '\n\n是否一键创建并获取它们的板块属性？'
            reply = QMessageBox.question(self, '批量获取完成', summary,
                                         QMessageBox.Yes | QMessageBox.No)
            if reply == QMessageBox.Yes:
                # 用最后一个交易日创建缺失股票
                missing_list = [(c, n, p) for (c, n), p in all_missing_stocks.items()]
                last_date = trading_dates[-1].toString('yyyy-MM-dd')
                self._batch_create_rush_missing_stocks(missing_list, last_date)
                QMessageBox.information(self, '完成',
                    f'已创建 {len(all_missing_stocks)} 只新股票并获取其板块属性')
        else:
            QMessageBox.information(self, '批量获取完成', summary)

        self.load_bidding_records()

    def batch_fetch_yiziban_stocks(self):
        """批量获取多日竞价一字板数据"""
        from PyQt5.QtWidgets import QDialog, QVBoxLayout, QHBoxLayout, QDateEdit, QDialogButtonBox

        dialog = QDialog(self)
        dialog.setWindowTitle('批量获取竞价一字板')
        dialog.setMinimumWidth(350)

        layout = QVBoxLayout(dialog)
        range_layout = QHBoxLayout()
        range_layout.addWidget(QLabel('起始日期:'))
        start_date = QDateEdit()
        start_date.setCalendarPopup(True)
        start_date.setDate(QDate.currentDate().addDays(-5))
        range_layout.addWidget(start_date)
        range_layout.addWidget(QLabel('结束日期:'))
        end_date = QDateEdit()
        end_date.setCalendarPopup(True)
        end_date.setDate(QDate.currentDate())
        range_layout.addWidget(end_date)
        layout.addLayout(range_layout)

        btn_box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btn_box.accepted.connect(dialog.accept)
        btn_box.rejected.connect(dialog.reject)
        layout.addWidget(btn_box)

        if dialog.exec_() != QDialog.Accepted:
            return

        sd = start_date.date()
        ed = end_date.date()
        if sd > ed:
            QMessageBox.warning(self, '提示', '起始日期不能晚于结束日期')
            return

        trading_dates = []
        d = sd
        while d <= ed:
            if d.dayOfWeek() not in [6, 7] and not self.is_chinese_holiday(d):
                trading_dates.append(d)
            d = d.addDays(1)

        if not trading_dates:
            QMessageBox.warning(self, '提示', '所选区间内无交易日')
            return

        total = len(trading_dates)
        reply = QMessageBox.question(self, '确认',
            f'所选区间共 {total} 个交易日，将逐个获取竞价一字板数据。\n'
            f'请确保 hexin-v 令牌有效。\n\n是否继续？',
            QMessageBox.Yes | QMessageBox.No)
        if reply != QMessageBox.Yes:
            return

        from PyQt5.QtWidgets import QProgressDialog
        progress = QProgressDialog('正在批量获取...', '取消', 0, total, self)
        progress.setWindowTitle('批量获取竞价一字板')
        progress.setWindowModality(2)
        progress.setMinimumDuration(0)

        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        total_added = 0
        all_missing_stocks = {}
        errors = []

        for i, qdate in enumerate(trading_dates):
            if progress.wasCanceled():
                break

            date_str = qdate.toString('yyyy-MM-dd')
            progress.setValue(i)
            progress.setLabelText(f'[{i+1}/{total}] {date_str}')

            try:
                datas, components = self._call_yiziban_api(qdate)
                if not datas:
                    continue

                added = 0
                for item in datas:
                    code = str(item.get('code', '')).zfill(6)
                    if not code:
                        continue

                    stock_name = item.get('股票名称', '') or item.get('name', '')
                    if not stock_name and components:
                        for col in components[0].get('data', {}).get('columns', []):
                            if col.get('label') in ('name', '股票名称'):
                                stock_name = item.get(col.get('key', ''), '')
                                break
                    if '*ST' in stock_name or 'ST' in stock_name:
                        continue

                    cursor.execute('SELECT id FROM stocks WHERE code = ?', (code,))
                    stock = cursor.fetchone()
                    if stock:
                        stock_id = stock[0]
                        cursor.execute(
                            'SELECT id FROM bidding_records WHERE date = ? AND stock_id = ? AND type = 1',
                            (date_str, stock_id))
                        if cursor.fetchone():
                            continue
                        cursor.execute(
                            'INSERT INTO bidding_records (date, stock_id, is_additive, type) VALUES (?, ?, 1, 1)',
                            (date_str, stock_id))
                        added += 1
                    else:
                        name = stock_name
                        if not name and components:
                            for col in components[0].get('data', {}).get('columns', []):
                                if col.get('label') in ('name', '股票名称'):
                                    name = item.get(col.get('key', ''), '')
                                    break
                        if name and (code, name) not in all_missing_stocks:
                            all_missing_stocks[(code, name)] = True

                total_added += added
                conn.commit()

                import time
                time.sleep(0.5)
            except Exception as e:
                errors.append(f'{date_str}: {str(e)[:80]}')
                continue

        conn.close()
        progress.setValue(total)

        summary = f'批量获取完成！\n\n共处理 {total} 个交易日\n成功写入 {total_added} 条一字板记录'
        if all_missing_stocks:
            summary += f'\n发现 {len(all_missing_stocks)} 只不在本地库的股票'
        if errors:
            summary += f'\n\n{len(errors)} 个日期获取失败（已跳过）：'
            for e in errors[:5]:
                summary += f'\n  {e}'

        if all_missing_stocks:
            summary += '\n\n是否一键创建并获取它们的板块属性？'
            reply = QMessageBox.question(self, '批量获取完成', summary,
                                         QMessageBox.Yes | QMessageBox.No)
            if reply == QMessageBox.Yes:
                missing_list = [(c, n, 10.0) for (c, n) in all_missing_stocks]
                last_date = trading_dates[-1].toString('yyyy-MM-dd')
                self._batch_create_rush_missing_stocks(missing_list, last_date)
                QMessageBox.information(self, '完成',
                    f'已创建 {len(all_missing_stocks)} 只新股票并获取其板块属性')
        else:
            QMessageBox.information(self, '批量获取完成', summary)

        self.load_bidding_records()

    def _call_negative_api(self, qdate):
        """调用问财API获取指定日期竞价跌幅小于-5%的股票"""
        qc_date = qdate.toString('yyyy.M.d')
        question = f'{qc_date}当天竞价跌幅小于-5%，按{qc_date}当天竞价跌幅排序'
        gain_key = f'竞价涨幅[{qdate.toString("yyyyMMdd")}]'

        url = 'https://www.iwencai.com/unifiedwap/unified-wap/v2/result/get-robot-data'
        headers = {
            'Accept': 'application/json, text/plain, */*',
            'Content-Type': 'application/x-www-form-urlencoded',
            'Origin': 'https://www.iwencai.com',
            'hexin-v': self.hexin_v,
            'Cookie': 'chat_bot_session_id=1cb8476c0ca664fc1761430187cd06b1; other_uid=Ths_iwencai_Xuangu_946086f7d300ab583cb90ff02ea2d227',
            'User-Agent': (
                'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                'AppleWebKit/537.36 (KHTML, like Gecko) '
                'Chrome/148.0.0.0 Safari/537.36 Edg/148.0.0.0'
            ),
        }
        post_data = {
            'source': 'Ths_iwencai_Xuangu',
            'version': '2.0',
            'query_area': '',
            'block_list': '',
            'add_info': '{"urp":{"scene":1,"company":1,"business":1},"contentType":"json","searchInfo":true}',
            'question': question,
            'perpage': '50',
            'page': '1',
            'secondary_intent': 'stock',
            'log_info': '{"input_type":"typewrite"}',
            'rsh': 'Ths_iwencai_Xuangu_pdqany9yolmgyksxprq8mpymlluypssu',
        }

        response = requests.post(url, headers=headers, data=post_data, timeout=15)
        result = response.json()

        answer = result.get('data', {}).get('answer', [])
        if not answer:
            raise Exception('问财接口未返回数据')

        txt_list = answer[0].get('txt', [])
        if not txt_list:
            raise Exception('接口返回格式异常（txt为空）')

        components = txt_list[0].get('content', {}).get('components', [])
        if not components:
            raise Exception('接口返回格式异常（components为空）')

        datas = components[0].get('data', {}).get('datas', [])
        return datas, gain_key, components

    def fetch_negative_stocks(self):
        """通过问财接口获取指定日期竞价跌幅小于-5%的股票"""
        qdate = self.bidding_date.date()
        date = qdate.toString('yyyy-MM-dd')

        if qdate.dayOfWeek() in [6, 7]:
            QMessageBox.warning(self, '提示', f'{date} 是周末，当天不开盘')
            return
        if self.is_chinese_holiday(qdate):
            QMessageBox.warning(self, '提示', f'{date} 是法定节假日，当天不开盘')
            return

        try:
            datas, gain_key, components = self._call_negative_api(qdate)
            if not datas:
                QMessageBox.warning(self, '提示', '未获取到股票数据')
                return

            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            added = 0
            missing_stocks = []

            for item in datas[:MAX_RUSH_ATTRS]:
                code = str(item.get('code', '')).zfill(6)
                if not code:
                    continue

                stock_name = item.get('股票名称', '') or item.get('name', '')
                if not stock_name and components:
                    for col in components[0].get('data', {}).get('columns', []):
                        if col.get('label') in ('name', '股票名称'):
                            stock_name = item.get(col.get('key', ''), '')
                            break
                if '*ST' in stock_name or 'ST' in stock_name:
                    continue

                cursor.execute('SELECT id FROM stocks WHERE code = ?', (code,))
                stock = cursor.fetchone()
                if stock:
                    stock_id = stock[0]
                    cursor.execute(
                        'SELECT id FROM bidding_records WHERE date = ? AND stock_id = ? AND type = 3',
                        (date, stock_id))
                    if cursor.fetchone():
                        continue
                    cursor.execute(
                        'INSERT INTO bidding_records (date, stock_id, is_additive, type) VALUES (?, ?, 0, 3)',
                        (date, stock_id))
                    added += 1
                else:
                    name = stock_name
                    if not name and components:
                        for col in components[0].get('data', {}).get('columns', []):
                            if col.get('label') in ('name', '股票名称'):
                                name = item.get(col.get('key', ''), '')
                                break
                    if name:
                        missing_stocks.append((code, name))

            conn.commit()
            conn.close()

            msg = f'从问财获取到 {added} 只竞价跌幅<-5%的股票，已填入竞价负反馈列表'
            if missing_stocks:
                msg += f'\n\n发现 {len(missing_stocks)} 只股票不在本地库中：'
                for mc, mn in missing_stocks[:10]:
                    msg += f'\n  {mc} {mn}'
                if len(missing_stocks) > 10:
                    msg += f'\n  ...等共{len(missing_stocks)}只'
                msg += '\n\n是否一键创建并获取它们的板块属性？'
                reply = QMessageBox.question(self, '发现新股票', msg,
                                             QMessageBox.Yes | QMessageBox.No)
                if reply == QMessageBox.Yes:
                    missing_with_pct = [(c, n, 5.0) for c, n in missing_stocks]
                    self._batch_create_rush_missing_stocks(missing_with_pct, date)
                    self.load_bidding_records()
                    QMessageBox.information(self, '完成', f'已创建 {len(missing_stocks)} 只新股票并获取其板块属性')
            else:
                QMessageBox.information(self, '成功', msg)

            self.load_bidding_records()

        except requests.exceptions.Timeout:
            QMessageBox.warning(self, '错误', '请求超时，请检查网络')
        except Exception as e:
            import traceback
            traceback.print_exc()
            err_msg = str(e)
            try:
                err_msg += f'\n响应状态: {getattr(response, "status_code", "?")}'
            except Exception:
                pass
            is_auth_error = (
                'JSONDecodeError' in err_msg or
                'Expecting value' in err_msg or
                '403' in err_msg
            )
            if is_auth_error:
                reply = QMessageBox.warning(self, '令牌过期',
                    '问财接口返回异常，可能是 hexin-v 令牌已过期。\n'
                    '是否打开输入框更新 hexin-v？',
                    QMessageBox.Yes | QMessageBox.No)
                if reply == QMessageBox.Yes:
                    self.hexin_v_input.setFocus()
                    self.hexin_v_input.selectAll()
            else:
                QMessageBox.warning(self, '错误', f'获取数据失败: {err_msg[:200]}')

    def batch_fetch_negative_stocks(self):
        """批量获取多日竞价负反馈数据"""
        from PyQt5.QtWidgets import QDialog, QVBoxLayout, QHBoxLayout, QDateEdit, QDialogButtonBox

        dialog = QDialog(self)
        dialog.setWindowTitle('批量获取竞价负反馈')
        dialog.setMinimumWidth(350)

        layout = QVBoxLayout(dialog)
        range_layout = QHBoxLayout()
        range_layout.addWidget(QLabel('起始日期:'))
        start_date = QDateEdit()
        start_date.setCalendarPopup(True)
        start_date.setDate(QDate.currentDate().addDays(-5))
        range_layout.addWidget(start_date)
        range_layout.addWidget(QLabel('结束日期:'))
        end_date = QDateEdit()
        end_date.setCalendarPopup(True)
        end_date.setDate(QDate.currentDate())
        range_layout.addWidget(end_date)
        layout.addLayout(range_layout)

        btn_box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btn_box.accepted.connect(dialog.accept)
        btn_box.rejected.connect(dialog.reject)
        layout.addWidget(btn_box)

        if dialog.exec_() != QDialog.Accepted:
            return

        sd = start_date.date()
        ed = end_date.date()
        if sd > ed:
            QMessageBox.warning(self, '提示', '起始日期不能晚于结束日期')
            return

        trading_dates = []
        d = sd
        while d <= ed:
            if d.dayOfWeek() not in [6, 7] and not self.is_chinese_holiday(d):
                trading_dates.append(d)
            d = d.addDays(1)

        if not trading_dates:
            QMessageBox.warning(self, '提示', '所选区间内无交易日')
            return

        total = len(trading_dates)
        reply = QMessageBox.question(self, '确认',
            f'所选区间共 {total} 个交易日，将逐个获取竞价负反馈数据。\n'
            f'请确保 hexin-v 令牌有效。\n\n是否继续？',
            QMessageBox.Yes | QMessageBox.No)
        if reply != QMessageBox.Yes:
            return

        from PyQt5.QtWidgets import QProgressDialog
        progress = QProgressDialog('正在批量获取...', '取消', 0, total, self)
        progress.setWindowTitle('批量获取竞价负反馈')
        progress.setWindowModality(2)
        progress.setMinimumDuration(0)

        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        total_added = 0
        all_missing_stocks = {}
        errors = []

        for i, qdate in enumerate(trading_dates):
            if progress.wasCanceled():
                break

            date_str = qdate.toString('yyyy-MM-dd')
            progress.setValue(i)
            progress.setLabelText(f'[{i+1}/{total}] {date_str}')

            try:
                datas, gain_key, components = self._call_negative_api(qdate)
                if not datas:
                    continue

                added = 0
                for item in datas[:MAX_RUSH_ATTRS]:
                    code = str(item.get('code', '')).zfill(6)
                    if not code:
                        continue

                    stock_name = item.get('股票名称', '') or item.get('name', '')
                    if not stock_name and components:
                        for col in components[0].get('data', {}).get('columns', []):
                            if col.get('label') in ('name', '股票名称'):
                                stock_name = item.get(col.get('key', ''), '')
                                break
                    if '*ST' in stock_name or 'ST' in stock_name:
                        continue

                    cursor.execute('SELECT id FROM stocks WHERE code = ?', (code,))
                    stock = cursor.fetchone()
                    if stock:
                        stock_id = stock[0]
                        cursor.execute(
                            'SELECT id FROM bidding_records WHERE date = ? AND stock_id = ? AND type = 3',
                            (date_str, stock_id))
                        if cursor.fetchone():
                            continue
                        cursor.execute(
                            'INSERT INTO bidding_records (date, stock_id, is_additive, type) VALUES (?, ?, 0, 3)',
                            (date_str, stock_id))
                        added += 1
                    else:
                        name = stock_name
                        if not name and components:
                            for col in components[0].get('data', {}).get('columns', []):
                                if col.get('label') in ('name', '股票名称'):
                                    name = item.get(col.get('key', ''), '')
                                    break
                        if name and (code, name) not in all_missing_stocks:
                            all_missing_stocks[(code, name)] = True

                total_added += added
                conn.commit()

                import time
                time.sleep(0.5)
            except Exception as e:
                errors.append(f'{date_str}: {str(e)[:80]}')
                continue

        conn.close()
        progress.setValue(total)

        summary = f'批量获取完成！\n\n共处理 {total} 个交易日\n成功写入 {total_added} 条负反馈记录'
        if all_missing_stocks:
            summary += f'\n发现 {len(all_missing_stocks)} 只不在本地库的股票'
        if errors:
            summary += f'\n\n{len(errors)} 个日期获取失败（已跳过）：'
            for e in errors[:5]:
                summary += f'\n  {e}'

        if all_missing_stocks:
            summary += '\n\n是否一键创建并获取它们的板块属性？'
            reply = QMessageBox.question(self, '批量获取完成', summary,
                                         QMessageBox.Yes | QMessageBox.No)
            if reply == QMessageBox.Yes:
                missing_list = [(c, n, 5.0) for (c, n) in all_missing_stocks]
                last_date = trading_dates[-1].toString('yyyy-MM-dd')
                self._batch_create_rush_missing_stocks(missing_list, last_date)
                QMessageBox.information(self, '完成',
                    f'已创建 {len(all_missing_stocks)} 只新股票并获取其板块属性')
        else:
            QMessageBox.information(self, '批量获取完成', summary)

        self.load_bidding_records()

    def mega_batch_fetch(self):
        """全量批量获取：选择日期区间和数据类型（一字板/抢筹/负反馈），一次性执行，失败可重试"""
        from PyQt5.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QDateEdit,
            QDialogButtonBox, QCheckBox, QGroupBox)

        dialog = QDialog(self)
        dialog.setWindowTitle('全量批量获取')
        dialog.setMinimumWidth(400)

        layout = QVBoxLayout(dialog)

        # 日期区间
        date_layout = QHBoxLayout()
        date_layout.addWidget(QLabel('起始日期:'))
        sd = QDateEdit()
        sd.setCalendarPopup(True)
        sd.setDate(QDate.currentDate().addDays(-5))
        date_layout.addWidget(sd)
        date_layout.addWidget(QLabel('结束日期:'))
        ed = QDateEdit()
        ed.setCalendarPopup(True)
        ed.setDate(QDate.currentDate())
        date_layout.addWidget(ed)
        layout.addLayout(date_layout)

        # 数据类型选择
        type_group = QGroupBox('选择要获取的数据类型')
        type_layout = QVBoxLayout(type_group)
        cb_yz = QCheckBox('竞价一字板（竞价涨停）')
        cb_yz.setChecked(True)
        type_layout.addWidget(cb_yz)
        cb_qc = QCheckBox('竞价抢筹（竞价涨幅>5%）')
        cb_qc.setChecked(True)
        type_layout.addWidget(cb_qc)
        cb_ff = QCheckBox('竞价负反馈（竞价跌幅<-5%）')
        cb_ff.setChecked(True)
        type_layout.addWidget(cb_ff)
        layout.addWidget(type_group)

        btn_box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btn_box.accepted.connect(dialog.accept)
        btn_box.rejected.connect(dialog.reject)
        layout.addWidget(btn_box)

        if dialog.exec_() != QDialog.Accepted:
            return

        start_date = sd.date()
        end_date = ed.date()
        if start_date > end_date:
            QMessageBox.warning(self, '提示', '起始日期不能晚于结束日期')
            return

        selected = []
        if cb_yz.isChecked():
            selected.append('yz')
        if cb_qc.isChecked():
            selected.append('qc')
        if cb_ff.isChecked():
            selected.append('ff')
        if not selected:
            QMessageBox.warning(self, '提示', '请至少选择一种数据类型')
            return

        # 计算交易日
        trading_dates = []
        d = start_date
        while d <= end_date:
            if d.dayOfWeek() not in [6, 7] and not self.is_chinese_holiday(d):
                trading_dates.append(d)
            d = d.addDays(1)

        if not trading_dates:
            QMessageBox.warning(self, '提示', '所选区间内无交易日')
            return

        type_names = {'yz': '一字板', 'qc': '抢筹', 'ff': '负反馈'}
        selected_names = [type_names[t] for t in selected]

        reply = QMessageBox.question(self, '确认',
            f'日期区间: {start_date.toString("yyyy-MM-dd")} ~ {end_date.toString("yyyy-MM-dd")}\n'
            f'交易日: {len(trading_dates)} 天\n'
            f'数据类型: {", ".join(selected_names)}\n'
            f'总执行步数: {len(trading_dates) * len(selected)}\n\n'
            f'请确保 hexin-v 令牌有效。\n是否继续？',
            QMessageBox.Yes | QMessageBox.No)
        if reply != QMessageBox.Yes:
            return

        # 构造所有步骤
        all_steps = [(qd, t) for qd in trading_dates for t in selected]

        def run_steps(steps_to_run, existing_added, existing_missing):
            """执行指定步骤列表，返回 (失败的步骤列表, 新增added, 新增missing)"""
            from PyQt5.QtWidgets import QProgressDialog
            total = len(steps_to_run)
            progress = QProgressDialog('正在全量获取...', '取消', 0, total, self)
            progress.setWindowTitle('全量批量获取')
            progress.setWindowModality(2)
            progress.setMinimumDuration(0)

            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            added = existing_added.copy()
            missing = dict(existing_missing)
            failed = []
            step = 0

            for qdate, t in steps_to_run:
                if progress.wasCanceled():
                    break

                date_str = qdate.toString('yyyy-MM-dd')
                progress.setValue(step)
                progress.setLabelText(f'[{date_str}] {type_names[t]}  ({step+1}/{total})')

                try:
                    if t == 'yz':
                        datas, components = self._call_yiziban_api(qdate)
                        type_code = 1
                        is_add = 1
                    elif t == 'qc':
                        datas, gain_key, components = self._call_rush_api(qdate)
                        type_code = 2
                        is_add = 1
                    else:
                        datas, gain_key, components = self._call_negative_api(qdate)
                        type_code = 3
                        is_add = 0

                    if not datas:
                        step += 1
                        continue

                    limit = None if t == 'yz' else MAX_RUSH_ATTRS
                    added_count = 0
                    for item in (datas if limit is None else datas[:limit]):
                        code = str(item.get('code', '')).zfill(6)
                        if not code:
                            continue

                        stock_name = item.get('股票名称', '') or item.get('name', '')
                        if not stock_name and components:
                            for col in components[0].get('data', {}).get('columns', []):
                                if col.get('label') in ('name', '股票名称'):
                                    stock_name = item.get(col.get('key', ''), '')
                                    break
                        if '*ST' in stock_name or 'ST' in stock_name:
                            continue

                        cursor.execute('SELECT id FROM stocks WHERE code = ?', (code,))
                        stock = cursor.fetchone()
                        if stock:
                            stock_id = stock[0]
                            if t == 'yz':
                                cursor.execute(
                                    'SELECT id FROM bidding_records WHERE date = ? AND stock_id = ? AND type = 1',
                                    (date_str, stock_id))
                                if cursor.fetchone():
                                    continue
                                cursor.execute(
                                    'INSERT INTO bidding_records (date, stock_id, is_additive, type) VALUES (?, ?, ?, ?)',
                                    (date_str, stock_id, is_add, type_code))
                            elif t == 'qc':
                                cursor.execute(
                                    'SELECT id FROM bidding_records WHERE date = ? AND stock_id = ? AND type IN (1,2)',
                                    (date_str, stock_id))
                                if cursor.fetchone():
                                    continue
                                pct = float(item.get(gain_key, 0))
                                self._ensure_rush_stock(cursor, date_str, stock_id, pct)
                            else:
                                cursor.execute(
                                    'SELECT id FROM bidding_records WHERE date = ? AND stock_id = ? AND type = 3',
                                    (date_str, stock_id))
                                if cursor.fetchone():
                                    continue
                                cursor.execute(
                                    'INSERT INTO bidding_records (date, stock_id, is_additive, type) VALUES (?, ?, ?, ?)',
                                    (date_str, stock_id, is_add, type_code))
                            added_count += 1
                        else:
                            name = stock_name
                            if not name and components:
                                for col in components[0].get('data', {}).get('columns', []):
                                    if col.get('label') in ('name', '股票名称'):
                                        name = item.get(col.get('key', ''), '')
                                        break
                            if name and (code, name) not in missing:
                                missing[(code, name)] = True

                    added[t] = added.get(t, 0) + added_count
                    conn.commit()

                    import time
                    time.sleep(3)
                except Exception as e:
                    failed.append((qdate, t))
                    print(f'[全量批量] 失败: {date_str} {type_names[t]}: {e}')

                step += 1

            conn.close()
            progress.setValue(total)
            return failed, added, missing

        # 第一轮执行所有步骤
        failed_steps, total_added, all_missing = run_steps(all_steps, {}, {})

        # 如果有失败，弹窗显示结果并提供重试
        while failed_steps:
            parts = [f'{type_names[t]}: {total_added.get(t, 0)}条' for t in selected]
            summary = f'全量获取完成（部分失败）\n\n共处理 {len(all_steps)} 步\n'
            summary += ' | '.join(parts)
            summary += f'\n\n失败 {len(failed_steps)} 步：'
            for qd, ft in failed_steps[:10]:
                summary += f'\n  {qd.toString("yyyy-MM-dd")} {type_names[ft]}'
            if len(failed_steps) > 10:
                summary += f'\n  ...等共 {len(failed_steps)} 步'
            if all_missing:
                summary += f'\n\n发现 {len(all_missing)} 只不在本地库的股票'

            retry_dlg = QMessageBox(self)
            retry_dlg.setWindowTitle('全量批量获取')
            retry_dlg.setText(summary)
            retry_btn = retry_dlg.addButton('重试失败步骤', QMessageBox.ActionRole)
            retry_dlg.addButton('完成（不重试）', QMessageBox.RejectRole)
            retry_dlg.exec_()

            if retry_dlg.clickedButton() == retry_btn:
                failed_steps, total_added, all_missing = run_steps(failed_steps, total_added, all_missing)
            else:
                break

        # 全量获取完成后，对抢筹与一字板进行去重（同一日期同只股票不应同时出现在两者中）
        if 'qc' in selected and 'yz' in selected:
            conn = sqlite3.connect(self.db_path)
            removed = self._deduplicate_bidding_data(conn, trading_dates)
            conn.close()
            if removed > 0:
                # 调整抢筹计数（减掉被去重的数量）
                total_added['qc'] = max(0, total_added.get('qc', 0) - removed)
                print(f'[全量批量] 去重完成: 共移除 {removed} 条重复抢筹记录')

        # 处理缺失股票
        if all_missing:
            parts = [f'{type_names[t]}: {total_added.get(t, 0)}条' for t in selected]
            summary = '全量获取完成！\n'
            summary += ' | '.join(parts)
            summary += f'\n发现 {len(all_missing)} 只不在本地库的股票\n\n是否一键创建并获取它们的板块属性？'
            reply = QMessageBox.question(self, '全量获取完成', summary,
                                         QMessageBox.Yes | QMessageBox.No)
            if reply == QMessageBox.Yes:
                missing_list = [(c, n, 5.0) for (c, n) in all_missing]
                last_date = trading_dates[-1].toString('yyyy-MM-dd')
                self._batch_create_rush_missing_stocks(missing_list, last_date)
                QMessageBox.information(self, '完成',
                    f'已创建 {len(all_missing)} 只新股票并获取其板块属性')
        else:
            parts = [f'{type_names[t]}: {total_added.get(t, 0)}条' for t in selected]
            summary = '全量获取完成！\n'
            summary += ' | '.join(parts)
            QMessageBox.information(self, '全量获取完成', summary)

        self.load_bidding_records()

    def onekey_fetch_all_bidding(self):
        """一键获取当天所有竞价信息：一字板 + 抢筹 + 负反馈"""
        qdate = self.bidding_date.date()
        date = qdate.toString('yyyy-MM-dd')

        if qdate.dayOfWeek() in [6, 7]:
            QMessageBox.warning(self, '提示', f'{date} 是周末，当天不开盘')
            return
        if self.is_chinese_holiday(qdate):
            QMessageBox.warning(self, '提示', f'{date} 是法定节假日，当天不开盘')
            return

        from PyQt5.QtWidgets import QProgressDialog
        progress = QProgressDialog('正在获取竞价数据...', '取消', 0, 3, self)
        progress.setWindowTitle('一键获取全部')
        progress.setWindowModality(2)
        progress.setMinimumDuration(0)

        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        results = {'yz': 0, 'qc': 0, 'ff': 0}
        all_missing = {}
        errors = []

        # 1. 一字板
        try:
            progress.setValue(0)
            progress.setLabelText(f'[{date}] 获取竞价一字板...')
            datas, components = self._call_yiziban_api(qdate)
            if datas:
                for item in datas:
                    code = str(item.get('code', '')).zfill(6)
                    if not code:
                        continue
                    stock_name = item.get('股票名称', '') or item.get('name', '')
                    if not stock_name and components:
                        for col in components[0].get('data', {}).get('columns', []):
                            if col.get('label') in ('name', '股票名称'):
                                stock_name = item.get(col.get('key', ''), '')
                                break
                    if '*ST' in stock_name or 'ST' in stock_name:
                        continue
                    cursor.execute('SELECT id FROM stocks WHERE code = ?', (code,))
                    stock = cursor.fetchone()
                    if stock:
                        sid = stock[0]
                        cursor.execute('SELECT id FROM bidding_records WHERE date = ? AND stock_id = ? AND type = 1', (date, sid))
                        if not cursor.fetchone():
                            cursor.execute('INSERT INTO bidding_records (date, stock_id, is_additive, type) VALUES (?, ?, 1, 1)', (date, sid))
                            results['yz'] += 1
                    else:
                        name = stock_name
                        if not name and components:
                            for col in components[0].get('data', {}).get('columns', []):
                                if col.get('label') in ('name', '股票名称'):
                                    name = item.get(col.get('key', ''), '')
                                    break
                        if name and (code, name) not in all_missing:
                            all_missing[(code, name)] = True
            conn.commit()
        except Exception as e:
            errors.append(f'一字板: {str(e)[:60]}')

        # 2. 抢筹
        try:
            progress.setValue(1)
            progress.setLabelText(f'[{date}] 获取竞价抢筹...')
            datas, gain_key, components = self._call_rush_api(qdate)
            if datas:
                n = 0
                for item in datas:
                    if n >= MAX_RUSH_ATTRS:
                        break
                    code = str(item.get('code', '')).zfill(6)
                    pct = float(item.get(gain_key, 0))
                    if pct < 5.0 or pct > 35.0 or not code:
                        continue
                    stock_name = item.get('股票名称', '') or item.get('name', '')
                    if not stock_name and components:
                        for col in components[0].get('data', {}).get('columns', []):
                            if col.get('label') in ('name', '股票名称'):
                                stock_name = item.get(col.get('key', ''), '')
                                break
                    if '*ST' in stock_name or 'ST' in stock_name:
                        continue
                    cursor.execute('SELECT id FROM stocks WHERE code = ?', (code,))
                    stock = cursor.fetchone()
                    if stock:
                        sid = stock[0]
                        cursor.execute('SELECT id FROM bidding_records WHERE date = ? AND stock_id = ? AND type = 1', (date, sid))
                        if cursor.fetchone():
                            continue
                        self._ensure_rush_stock(cursor, date, sid, pct)
                        results['qc'] += 1
                        n += 1
                    else:
                        name = stock_name
                        if not name and components:
                            for col in components[0].get('data', {}).get('columns', []):
                                if col.get('label') in ('name', '股票名称'):
                                    name = item.get(col.get('key', ''), '')
                                    break
                        if name and (code, name) not in all_missing:
                            all_missing[(code, name)] = True
                            n += 1
            conn.commit()
        except Exception as e:
            errors.append(f'抢筹: {str(e)[:60]}')

        # 3. 负反馈
        try:
            progress.setValue(2)
            progress.setLabelText(f'[{date}] 获取竞价负反馈...')
            datas, gain_key, components = self._call_negative_api(qdate)
            if datas:
                n = 0
                for item in datas:
                    if n >= MAX_RUSH_ATTRS:
                        break
                    code = str(item.get('code', '')).zfill(6)
                    if not code:
                        continue
                    stock_name = item.get('股票名称', '') or item.get('name', '')
                    if not stock_name and components:
                        for col in components[0].get('data', {}).get('columns', []):
                            if col.get('label') in ('name', '股票名称'):
                                stock_name = item.get(col.get('key', ''), '')
                                break
                    if '*ST' in stock_name or 'ST' in stock_name:
                        continue
                    cursor.execute('SELECT id FROM stocks WHERE code = ?', (code,))
                    stock = cursor.fetchone()
                    if stock:
                        sid = stock[0]
                        cursor.execute('SELECT id FROM bidding_records WHERE date = ? AND stock_id = ? AND type = 3', (date, sid))
                        if not cursor.fetchone():
                            cursor.execute('INSERT INTO bidding_records (date, stock_id, is_additive, type) VALUES (?, ?, 0, 3)', (date, sid))
                            results['ff'] += 1
                            n += 1
                    else:
                        name = stock_name
                        if not name and components:
                            for col in components[0].get('data', {}).get('columns', []):
                                if col.get('label') in ('name', '股票名称'):
                                    name = item.get(col.get('key', ''), '')
                                    break
                        if name and (code, name) not in all_missing:
                            all_missing[(code, name)] = True
                            n += 1
            conn.commit()
        except Exception as e:
            errors.append(f'负反馈: {str(e)[:60]}')

        conn.close()
        progress.setValue(3)

        msg = (f'获取完成！\n\n'
               f'一字板: {results["yz"]} 只\n'
               f'抢筹: {results["qc"]} 只\n'
               f'负反馈: {results["ff"]} 只')
        if all_missing:
            msg += f'\n发现 {len(all_missing)} 只股票不在本地库中'
        if errors:
            msg += f'\n\n{len(errors)} 个接口异常：\n' + '\n'.join(errors)

        if all_missing:
            msg += '\n\n是否一键创建并获取它们的板块属性？'
            reply = QMessageBox.question(self, '获取完成', msg,
                                         QMessageBox.Yes | QMessageBox.No)
            if reply == QMessageBox.Yes:
                missing_list = [(c, n, 5.0) for (c, n) in all_missing]
                self._batch_create_rush_missing_stocks(missing_list, date)
                QMessageBox.information(self, '完成',
                    f'已创建 {len(all_missing)} 只新股票并获取其板块属性')
        else:
            QMessageBox.information(self, '获取完成', msg)

        self.load_bidding_records()

    def analyze_bidding(self):
        try:
            date = self.bidding_date.date().toString('yyyy-MM-dd')

            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()

            cursor.execute('''
                SELECT br.id, s.name, sa.attribute, br.is_additive, br.type
                FROM bidding_records br
                JOIN stock_attributes sa ON br.stock_id = sa.stock_id
                JOIN stocks s ON br.stock_id = s.id
                WHERE br.date = ? AND br.type IN (1, 3)
            ''', (date,))
            records = cursor.fetchall()

            # 先按股票分组，对每个股票的属性去重
            stock_attrs = {}
            # 记录每个属性来自哪些股票
            attr_stocks = {}
            for record_id, stock_name, attr, is_additive, type in records:
                if record_id not in stock_attrs:
                    stock_attrs[record_id] = {1: set(), 3: set()}
                stock_attrs[record_id][type].add(attr)
                # 记录属性来源股票
                if type not in attr_stocks:
                    attr_stocks[type] = {}
                if attr not in attr_stocks[type]:
                    attr_stocks[type][attr] = set()
                attr_stocks[type][attr].add(stock_name)

            # 统计一字板和负反馈属性（去重后）
            yz_attrs = {}
            ff_attrs = {}
            
            for record_id, attrs_by_type in stock_attrs.items():
                for attr in attrs_by_type.get(1, set()):
                    yz_attrs[attr] = yz_attrs.get(attr, 0) + 1
                for attr in attrs_by_type.get(3, set()):
                    ff_attrs[attr] = ff_attrs.get(attr, 0) - 1

            # 读取竞价抢筹股票，分析其属性
            cursor.execute('''
                SELECT s.id
                FROM bidding_rush_stocks brs
                JOIN stocks s ON brs.stock_id = s.id
                WHERE brs.date = ?
                ORDER BY brs.pct_change DESC
            ''', (date,))
            rush_stock_ids = [row[0] for row in cursor.fetchall()]

            rush_attrs = {}
            for stock_id in rush_stock_ids:
                cursor.execute('SELECT attribute FROM stock_attributes WHERE stock_id = ?', (stock_id,))
                for row in cursor.fetchall():
                    attr = row[0]
                    if attr in ATTR_REMOVE_SET:
                        continue
                    rush_attrs[attr] = rush_attrs.get(attr, 0) + 1

            rush_attrs_sorted = sorted(rush_attrs.items(), key=lambda x: x[1], reverse=True)

            yz_attrs_sorted = sorted(yz_attrs.items(), key=lambda x: x[1], reverse=True)
            ff_attrs_filtered = {attr: count for attr, count in ff_attrs.items() if len(attr) > 1}
            ff_attrs_sorted = sorted(ff_attrs_filtered.items(), key=lambda x: x[1])

            # 显示竞价一字板属性（显示属性和来源股票）
            self.additive_analysis_result.setRowCount(len(yz_attrs_sorted))
            for i, (attr, count) in enumerate(yz_attrs_sorted):
                self.additive_analysis_result.setItem(i, 0, QTableWidgetItem(attr))
                # 获取来源股票列表
                stock_list = attr_stocks.get(1, {}).get(attr, set())
                stock_str = ', '.join(sorted(stock_list))
                self.additive_analysis_result.setItem(i, 1, QTableWidgetItem(stock_str))
            # 设置列宽，来源股票列放宽
            self.additive_analysis_result.setColumnWidth(0, 100)  # 属性列
            self.additive_analysis_result.setColumnWidth(1, 300)  # 来源股票列

            # 显示竞价抢筹属性（从抢筹股票的属性统计得出）
            self.rushing_analysis_result.setRowCount(len(rush_attrs_sorted))
            for i, (attr, count) in enumerate(rush_attrs_sorted):
                self.rushing_analysis_result.setItem(i, 0, QTableWidgetItem(attr))
                count_item = QTableWidgetItem(str(count))
                count_item.setTextAlignment(Qt.AlignCenter)
                self.rushing_analysis_result.setItem(i, 1, count_item)

            # 显示负反馈属性
            self.subtractive_analysis_result.setRowCount(len(ff_attrs_sorted))
            for i, (attr, count) in enumerate(ff_attrs_sorted):
                self.subtractive_analysis_result.setItem(i, 0, QTableWidgetItem(attr))
                self.subtractive_analysis_result.setItem(i, 1, QTableWidgetItem(str(count)))

            conn.close()
        except Exception as e:
            print(f"Error analyzing bidding: {e}")
    
    def view_bayesian_progress(self):
        from PyQt5.QtWidgets import QDialog, QVBoxLayout, QLabel, QPushButton, QTextEdit, QProgressBar, QHBoxLayout
        
        dialog = QDialog(self)
        dialog.setWindowTitle('贝叶斯优化进度')
        dialog.setMinimumSize(600, 450)
        
        layout = QVBoxLayout(dialog)
        
        status_layout = QHBoxLayout()
        status_layout.addWidget(QLabel('状态:'))
        self.bayesian_stage_label = QLabel(self.bayesian_progress.get('stage', '等待中'))
        status_layout.addWidget(self.bayesian_stage_label)
        status_layout.addStretch()
        layout.addLayout(status_layout)
        
        self.bayesian_progress_bar = QProgressBar()
        self.bayesian_progress_bar.setRange(0, 100)
        self.bayesian_progress_bar.setValue(self.bayesian_progress.get('pct', 0))
        layout.addWidget(self.bayesian_progress_bar)
        
        self.bayesian_log_text = QTextEdit()
        self.bayesian_log_text.setReadOnly(True)
        logs = '\n'.join(self.bayesian_progress.get('logs', []))
        self.bayesian_log_text.setPlainText(logs if logs else '等待优化启动...')
        layout.addWidget(self.bayesian_log_text)
        
        btn_layout = QHBoxLayout()
        close_btn = QPushButton('关闭')
        close_btn.clicked.connect(dialog.accept)
        btn_layout.addStretch()
        btn_layout.addWidget(close_btn)
        layout.addLayout(btn_layout)
        
        timer = QTimer(dialog)
        timer.timeout.connect(lambda: self._refresh_bayesian_progress(dialog))
        timer.start(2000)
        dialog.finished.connect(timer.stop)
        
        dialog.exec_()
    
    def _refresh_bayesian_progress(self, dialog):
        if not self.bayesian_running and dialog.isVisible():
            self.bayesian_stage_label.setText('已完成' if self.bayesian_progress.get('stage') != '错误' else '出错')
            self.bayesian_progress_bar.setValue(100)
        else:
            self.bayesian_stage_label.setText(self.bayesian_progress.get('stage', '等待中'))
            self.bayesian_progress_bar.setValue(self.bayesian_progress.get('pct', 0))
        logs = '\n'.join(self.bayesian_progress.get('logs', []))
        self.bayesian_log_text.setPlainText(logs if logs else '等待优化启动...')
        scrollbar = self.bayesian_log_text.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())
    
    def run_bayesian_optimization(self):
        """使用贝叶斯优化寻找最优系数组合（异步线程）"""
        from PyQt5.QtWidgets import QMessageBox, QDialog, QLabel, QDateEdit, QPushButton, QVBoxLayout, QHBoxLayout, QSpinBox
        
        if self.bayesian_running:
            QMessageBox.information(self, '提示', '贝叶斯优化正在进行中，请稍候...')
            return
        
        try:
            dialog = QDialog(self)
            dialog.setWindowTitle('贝叶斯优化参数设置')
            dialog.setModal(True)
            
            layout = QVBoxLayout(dialog)
            
            train_group = QVBoxLayout()
            train_group.addWidget(QLabel('=== 训练集时间范围（贝叶斯优化使用） ==='))
            train_layout = QHBoxLayout()
            train_layout.addWidget(QLabel('开始日期:'))
            train_start_date = QDateEdit(QDate.currentDate().addDays(-60))
            train_start_date.setCalendarPopup(True)
            train_layout.addWidget(train_start_date)
            train_layout.addWidget(QLabel('结束日期:'))
            train_end_date = QDateEdit(QDate.currentDate().addDays(-30))
            train_end_date.setCalendarPopup(True)
            train_layout.addWidget(train_end_date)
            train_group.addLayout(train_layout)
            layout.addLayout(train_group)
            
            valid_group = QVBoxLayout()
            valid_group.addWidget(QLabel('=== 验证集时间范围（参数验证使用） ==='))
            valid_layout = QHBoxLayout()
            valid_layout.addWidget(QLabel('开始日期:'))
            valid_start_date = QDateEdit(QDate.currentDate().addDays(-29))
            valid_start_date.setCalendarPopup(True)
            valid_layout.addWidget(valid_start_date)
            valid_layout.addWidget(QLabel('结束日期:'))
            valid_end_date = QDateEdit(QDate.currentDate())
            valid_end_date.setCalendarPopup(True)
            valid_layout.addWidget(valid_end_date)
            valid_group.addLayout(valid_layout)
            layout.addLayout(valid_group)
            
            rounds_group = QVBoxLayout()
            rounds_group.addWidget(QLabel('=== 优化参数设置 ==='))
            rounds_layout = QHBoxLayout()
            rounds_layout.addWidget(QLabel('优化次数:'))
            runs_spin = QSpinBox()
            runs_spin.setRange(1, 10)
            runs_spin.setValue(3)
            runs_spin.setSuffix(' 次')
            rounds_layout.addWidget(runs_spin)
            rounds_layout.addWidget(QLabel('初始采样点:'))
            init_spin = QSpinBox()
            init_spin.setRange(5, 100)
            init_spin.setValue(15)
            rounds_layout.addWidget(init_spin)
            rounds_layout.addWidget(QLabel('迭代轮次:'))
            iter_spin = QSpinBox()
            iter_spin.setRange(10, 500)
            iter_spin.setValue(30)
            rounds_layout.addWidget(iter_spin)
            rounds_group.addLayout(rounds_layout)
            shrink_layout = QHBoxLayout()
            shrink_layout.addWidget(QLabel('区间收缩轮次:'))
            shrink_spin = QSpinBox()
            shrink_spin.setRange(0, 10)
            shrink_spin.setValue(3)
            shrink_spin.setSuffix(' 轮')
            shrink_spin.setToolTip('0=不收缩，每次收缩将搜索范围缩小一半并重新搜索')
            shrink_layout.addWidget(shrink_spin)
            shrink_layout.addStretch()
            rounds_group.addLayout(shrink_layout)

            optimal_layout = QHBoxLayout()
            dim = len(BAYESIAN_BOUNDS)
            optimal_layout.addWidget(QLabel(f'当前系数个数: {dim}'))
            auto_btn = QPushButton('获取最优贝叶斯参数')
            auto_btn.setStyleSheet('''
                QPushButton {
                    background-color: #4CAF50;
                    color: white;
                    border: none;
                    padding: 5px 15px;
                    border-radius: 3px;
                    font-weight: bold;
                }
                QPushButton:hover {
                    background-color: #45a049;
                }
            ''')
            auto_btn.setToolTip('根据当前系数个数自动计算最优优化参数')
            auto_btn.clicked.connect(lambda: self._auto_fill_bayesian_params(runs_spin, init_spin, iter_spin, shrink_spin))
            optimal_layout.addWidget(auto_btn)
            optimal_layout.addStretch()
            rounds_group.addLayout(optimal_layout)

            layout.addLayout(rounds_group)

            btn_layout = QHBoxLayout()
            ok_btn = QPushButton('确定')
            ok_btn.clicked.connect(dialog.accept)
            btn_layout.addWidget(ok_btn)
            cancel_btn = QPushButton('取消')
            cancel_btn.clicked.connect(dialog.reject)
            btn_layout.addWidget(cancel_btn)
            layout.addLayout(btn_layout)
            
            result = dialog.exec_()
            if result != QDialog.Accepted:
                return
            
            user_runs = runs_spin.value()
            user_init_points = init_spin.value()
            user_n_iter = iter_spin.value()
            user_shrink_rounds = shrink_spin.value()
            
            train_start = train_start_date.date().toString('yyyy-MM-dd')
            train_end = train_end_date.date().toString('yyyy-MM-dd')
            valid_start = valid_start_date.date().toString('yyyy-MM-dd')
            valid_end = valid_end_date.date().toString('yyyy-MM-dd')
            
            print(f"[贝叶斯优化] 训练集: {train_start} ~ {train_end}")
            print(f"[贝叶斯优化] 验证集: {valid_start} ~ {valid_end}")

            # 自适应参数范围：先运行相关性分析，线性参数窄域收缩，非线性参数全域探索
            custom_pbounds = None
            if QMessageBox.question(self, '自适应参数范围',
                f'是否先运行参数属性判定，自动为线性参数缩小搜索范围、非线性参数保留全域探索？\n\n'
                f'线性参数 → 窄区间 (当前值±{SHRINK_FACTOR*50:.0f}%)\n'
                f'非线性参数 → 宽区间 (BAYESIAN_BOUNDS 全域)',
                QMessageBox.Yes | QMessageBox.No) == QMessageBox.Yes:
                try:
                    saved = self.load_correlation_results()
                    if saved is not None and all(row[7] != '未知' for row in saved['results']):
                        print(f"[自适应] 使用已保存的参数属性判定结果 ({len(saved['results'])}个)")
                        corr_results = saved
                    else:
                        from PyQt5.QtWidgets import QDialog, QVBoxLayout, QLabel, QProgressBar
                        from PyQt5.QtCore import Qt

                        corr_dialog = QDialog(self)
                        corr_dialog.setWindowTitle('参数属性判定中...')
                        corr_dialog.setModal(True)
                        corr_dialog.setMinimumWidth(400)
                        if self.isVisible():
                            corr_dialog.move(
                                self.x() + (self.width() - corr_dialog.width()) // 2,
                                self.y() + (self.height() - corr_dialog.height()) // 2
                            )

                        clayout = QVBoxLayout(corr_dialog)
                        clayout.addWidget(QLabel('正在分析参数属性，请稍候...'))
                        clayout.addWidget(QLabel('将对15个参数各采样200次，评估线性和非线性特征'))
                        cprogress = QProgressBar()
                        cprogress.setMaximum(100)
                        clayout.addWidget(cprogress)

                        corr_thread = CorrelationAnalysisThread(self, self.db_path, train_start, valid_end)
                        corr_thread.progress_signal.connect(cprogress.setValue)
                        corr_results = {}

                        def on_corr_finished(data):
                            nonlocal corr_results
                            corr_results.update(data)
                            self.save_correlation_results(data['results'])

                        corr_thread.finished_signal.connect(on_corr_finished)
                        corr_thread.finished_signal.connect(corr_dialog.accept)
                        corr_thread.start()
                        corr_dialog.exec_()

                    if corr_results:
                        custom_pbounds = {}
                        for bk_from_data, name, r, abs_r, mi, slope, r_squared, judgment in corr_results['results']:
                            bayes_key = bk_from_data
                            if bayes_key is None:
                                continue

                            lo, hi = BAYESIAN_BOUNDS[bayes_key]
                            cur_val = getattr(self, BAYESIAN_TO_COEFFICIENT[bayes_key])

                            is_linear = judgment in ('线性相关', '线性相关(拟合存疑)', '弱线性相关')
                            if is_linear and (abs_r >= 0.4 or abs_r == 0.0):
                                margin = (hi - lo) * SHRINK_FACTOR * 0.5
                                new_lo = max(lo, cur_val - margin)
                                new_hi = min(hi, cur_val + margin)
                                if new_hi - new_lo < (hi - lo) * 0.02:
                                    new_lo = lo
                                    new_hi = hi
                                custom_pbounds[bayes_key] = (new_lo, new_hi)
                                print(f"[自适应] 线性参数 {bayes_key}: [{lo},{hi}] → [{new_lo:.4f},{new_hi:.4f}]")
                            else:
                                custom_pbounds[bayes_key] = (lo, hi)
                                print(f"[自适应] 非线性参数 {bayes_key}: 保留全域 [{lo},{hi}]")

                        QMessageBox.information(self, '自适应范围已生成',
                            f'自适应参数范围生成完成！\n'
                            f'线性参数(窄域): {sum(1 for k in custom_pbounds if custom_pbounds[k] != BAYESIAN_BOUNDS[k])} 个\n'
                            f'非线性参数(全域): {sum(1 for k in custom_pbounds if custom_pbounds[k] == BAYESIAN_BOUNDS[k])} 个')
                except Exception as e:
                    print(f"[自适应] 相关性分析失败，回退默认范围: {e}")
                    custom_pbounds = None
            
            # 重置进度状态
            self.bayesian_progress = {'stage': '启动中', 'pct': 0, 'logs': []}
            self.bayesian_running = True
            
            # 创建并启动后台线程
            linear_param_set = set()
            if custom_pbounds is not None:
                for k in BAYESIAN_BOUNDS:
                    if k in custom_pbounds and custom_pbounds[k] != BAYESIAN_BOUNDS[k]:
                        linear_param_set.add(k)
                print(f"[自适应] 线性参数集({len(linear_param_set)}个): {sorted(linear_param_set)}")

            self.bayesian_thread = BayesianOptimizationThread(
                parent=self,
                db_path=self.db_path,
                train_start=train_start,
                train_end=train_end,
                valid_start=valid_start,
                valid_end=valid_end,
                user_runs=user_runs,
                user_init_points=user_init_points,
                user_n_iter=user_n_iter,
                user_shrink_rounds=user_shrink_rounds,
                custom_pbounds=custom_pbounds,
                linear_param_set=linear_param_set,
            )
            self.bayesian_thread.progress_signal.connect(self._on_bayesian_progress)
            self.bayesian_thread.finished_signal.connect(self._on_bayesian_finished)
            self.bayesian_thread.error_signal.connect(self._on_bayesian_error)
            self.bayesian_thread.start()
            
            QMessageBox.information(self, '优化已启动', 
                f'贝叶斯优化已后台启动，可在数据回测标签页点击"查看贝叶斯进度"查看实时状态。\n\n'
                f'训练集: {train_start} ~ {train_end}\n'
                f'验证集: {valid_start} ~ {valid_end}\n'
                f'优化参数: {user_runs}次 × ({user_init_points}+{user_n_iter}) 轮，收缩{user_shrink_rounds}轮')
            
        except Exception as e:
            self.bayesian_running = False
            QMessageBox.warning(self, '启动失败', f'启动贝叶斯优化失败: {str(e)}')
            print(f"启动贝叶斯优化错误: {e}")
            import traceback
            traceback.print_exc()

    def _auto_fill_bayesian_params(self, runs_spin, init_spin, iter_spin, shrink_spin):
        dim = len(BAYESIAN_BOUNDS)
        base_runs = 3
        if dim <= 5:
            base_init = 20
            base_iter = 40
            base_shrink = 3
        elif dim <= 8:
            base_init = 25
            base_iter = 50
            base_shrink = 3
        elif dim <= 12:
            base_init = 30
            base_iter = 60
            base_shrink = 4
        else:
            base_init = 40
            base_iter = 80
            base_shrink = 4
        total_evals = dim * base_init
        final_init = max(10, min(80, total_evals // 2))
        final_iter = max(20, min(200, total_evals))
        runs_spin.setValue(base_runs)
        init_spin.setValue(final_init)
        iter_spin.setValue(final_iter)
        shrink_spin.setValue(base_shrink)
        from PyQt5.QtWidgets import QMessageBox
        QMessageBox.information(None, '参数已优化',
            f'根据当前 {dim} 个系数自动计算最优参数：\n\n'
            f'优化次数: {base_runs} 次\n'
            f'初始采样点: {final_init} 个\n'
            f'迭代轮次: {final_iter} 轮\n'
            f'区间收缩轮次: {base_shrink} 轮')

    def _on_bayesian_progress(self, data):
        self.bayesian_progress['stage'] = data.get('stage', '')
        self.bayesian_progress['pct'] = data.get('pct', 0)
        if 'log' in data:
            self.bayesian_progress['logs'].append(data['log'])
            print(f"[贝叶斯进度] {data['log']}")

    def _on_bayesian_finished(self, data):
        self.bayesian_running = False
        self.bayesian_thread = None
        valid_score = data.get('valid_score', 0)
        train_score = data.get('train_score', 0)
        params = data.get('params', {})
        logs = data.get('logs', '')
        self.bayesian_progress['logs'].append('优化完成!')
        print(f"[贝叶斯优化] 完成! 验证得分: {valid_score:.2f}")
        self.show_bayesian_result_dialog(valid_score, train_score, params)

    def _on_bayesian_error(self, error_msg):
        self.bayesian_running = False
        self.bayesian_thread = None
        self.bayesian_progress['logs'].append(f'错误: {error_msg}')
        from PyQt5.QtWidgets import QMessageBox
        QMessageBox.warning(self, '优化失败', f'贝叶斯优化过程中发生错误:\n{error_msg}')
        print(f"Bayesian optimization error: {error_msg}")
    
    def show_bayesian_result_dialog(self, valid_score, train_score, best_params):
        """显示贝叶斯优化结果弹窗，支持一键修改配置文件"""
        from PyQt5.QtWidgets import QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QTextEdit
        
        dialog = QDialog(self)
        dialog.setWindowTitle('贝叶斯优化结果')
        dialog.setMinimumWidth(500)
        
        layout = QVBoxLayout(dialog)
        
        # 得分信息
        score_label = QLabel(f"<b>验证集得分:</b> {valid_score:.2f}（训练集得分: {train_score:.2f}）")
        score_label.setStyleSheet('font-size: 14px;')
        layout.addWidget(score_label)
        
        # 参数列表
        params_text = QTextEdit()
        params_text.setReadOnly(True)
        params_text.setMaximumHeight(200)
        
        result_text = "最优系数组合:\n"
        for key, value in best_params.items():
            # 获取中文名称
            coeff_name = COEFFICIENT_NAMES.get(BAYESIAN_TO_COEFFICIENT.get(key, ''), key)
            result_text += f"  {coeff_name}: {value:.4f}\n"
        
        params_text.setText(result_text)
        layout.addWidget(params_text)
        
        # 按钮
        btn_layout = QHBoxLayout()
        
        apply_btn = QPushButton('应用到当前程序')
        apply_btn.clicked.connect(dialog.accept)
        btn_layout.addWidget(apply_btn)
        
        save_btn = QPushButton('保存到配置文件')
        save_btn.clicked.connect(lambda: self.save_coefficients_to_config(best_params))
        btn_layout.addWidget(save_btn)
        
        close_btn = QPushButton('关闭')
        close_btn.clicked.connect(dialog.reject)
        btn_layout.addWidget(close_btn)
        
        layout.addLayout(btn_layout)
        
        dialog.exec_()
    
    def save_coefficients_to_config(self, best_params):
        """将最优参数保存到配置文件"""
        config_path = os.path.join(os.path.dirname(__file__), 'config.py')
        
        # 读取配置文件
        with open(config_path, 'r', encoding='utf-8') as f:
            content = f.read()
        
        # 更新系数值
        for bayes_key, value in best_params.items():
            if bayes_key == 'stock_weight_first':
                content = content.replace(
                    f"STOCK_WEIGHT_FIRST = {Coefficients.STOCK_WEIGHT_FIRST}",
                    f"STOCK_WEIGHT_FIRST = {value:.4f}"
                )
            elif bayes_key == 'stock_weight_last':
                content = content.replace(
                    f"STOCK_WEIGHT_LAST = {Coefficients.STOCK_WEIGHT_LAST}",
                    f"STOCK_WEIGHT_LAST = {value:.4f}"
                )
            elif bayes_key in BAYESIAN_TO_COEFFICIENT:
                coeff_name = BAYESIAN_TO_COEFFICIENT[bayes_key]
                old_value = getattr(Coefficients, coeff_name)
                content = content.replace(
                    f"{coeff_name} = {old_value}",
                    f"{coeff_name} = {value:.4f}"
                )
        
        # 写入配置文件
        with open(config_path, 'w', encoding='utf-8') as f:
            f.write(content)
        
        QMessageBox.information(self, '保存成功', '系数已保存到配置文件！下次启动程序时会自动加载。')
    
    def _l2_regularization_penalty(self):
        weights = [
            self.STOCK_WEIGHT_FIRST,
            self.STOCK_WEIGHT_LAST,
            self.BOARD_WEIGHT,

            self.RUSH_PCT_COEFFICIENT,
            self.RUSH_LETTER_ATTR_WEIGHT,
            self.RUSH_REGION_ATTR_WEIGHT,
            self.RUSH_MARKET_CAP_COEFFICIENT,
            self.YZ_OVERALL_WEIGHT,
            self.LETTER_ATTR_WEIGHT,
            self.REGION_ATTR_WEIGHT,
            self.ATTR_COUNT_WEIGHT,
            self.NEGATIVE_ATTR_COUNT_WEIGHT,
            self.NEGATIVE_ATTR_WEIGHT,
            self.NEGATIVE_LETTER_ATTR_WEIGHT,
            self.NEGATIVE_REGION_ATTR_WEIGHT,
            self.BOARD_PRESS_WEIGHT,
            self.NODE_GUIDE_WEIGHT,
            self.HOLDER_RATIO_WEIGHT,
            self.MARKET_CAP_WEIGHT,
            self.MARKET_CAP_EXPONENT,
        ]
        l2_sum = sum(w * w for w in weights)
        return L2_LAMBDA * l2_sum / L2_SCALE

    def _optimization_backtest(self, start_date_str, end_date_str):
        """简化版回测，用于贝叶斯优化评估"""
        from PyQt5.QtCore import QDate

        # 备份并应用因子治理：禁用的因子强制为0
        _gov_backup = {}
        _gov_enabled = getattr(self, '_factor_enabled', {})
        _gov_mul = {'board_press_weight', 'node_guide_weight'}
        for _key, _attr in [('stock_weight_first', 'STOCK_WEIGHT_FIRST'),
                            ('stock_weight_last', 'STOCK_WEIGHT_LAST'),
                            ('board_weight', 'BOARD_WEIGHT'),

                            ('rush_pct_coefficient', 'RUSH_PCT_COEFFICIENT'),
                            ('rush_letter_attr_weight', 'RUSH_LETTER_ATTR_WEIGHT'),
                            ('rush_region_attr_weight', 'RUSH_REGION_ATTR_WEIGHT'),
                            ('rush_market_cap_coefficient', 'RUSH_MARKET_CAP_COEFFICIENT'),
                            ('yz_overall_weight', 'YZ_OVERALL_WEIGHT'),
                            ('letter_attr_weight', 'LETTER_ATTR_WEIGHT'),
                            ('region_attr_weight', 'REGION_ATTR_WEIGHT'),
                            ('attr_count_weight', 'ATTR_COUNT_WEIGHT'),
                            ('negative_attr_count_weight', 'NEGATIVE_ATTR_COUNT_WEIGHT'),
                            ('negative_attr_weight', 'NEGATIVE_ATTR_WEIGHT'),
							('negative_letter_attr_weight', 'NEGATIVE_LETTER_ATTR_WEIGHT'),
							('negative_region_attr_weight', 'NEGATIVE_REGION_ATTR_WEIGHT'),
                            ('board_press_weight', 'BOARD_PRESS_WEIGHT'),
                            ('node_guide_weight', 'NODE_GUIDE_WEIGHT'),
                            ('holder_ratio_weight', 'HOLDER_RATIO_WEIGHT'),
                            ('market_cap_weight', 'MARKET_CAP_WEIGHT'),
                            ('market_cap_exponent', 'MARKET_CAP_EXPONENT')]:
            if not _gov_enabled.get(_key, True):
                _gov_backup[_attr] = getattr(self, _attr)
                setattr(self, _attr, 1.0 if _key in _gov_mul else 0.0)

        start_date = QDate.fromString(start_date_str, 'yyyy-MM-dd')
        end_date = QDate.fromString(end_date_str, 'yyyy-MM-dd')
        
        total_score = 0.0
        valid_days = 0
        
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        try:
            current_date = start_date
            while current_date <= end_date:
                date_str = current_date.toString('yyyy-MM-dd')
                
                # 跳过周末
                if current_date.dayOfWeek() in [6, 7]:
                    current_date = current_date.addDays(1)
                    continue
                
                # 跳过法定节假日
                if self.is_chinese_holiday(current_date):
                    current_date = current_date.addDays(1)
                    continue
                
                yesterday = current_date.addDays(-1)
                yesterday_str = yesterday.toString('yyyy-MM-dd')
                
                cursor.execute('SELECT COUNT(*) FROM ladder_nodes WHERE date = ?', (yesterday_str,))
                yesterday_has_ladder = cursor.fetchone()[0] > 0
                
                cursor.execute('SELECT COUNT(*) FROM ladder_nodes WHERE date = ?', (date_str,))
                today_has_ladder = cursor.fetchone()[0] > 0
                
                if yesterday_has_ladder and today_has_ladder:
                    result = self.run_correlation_for_date(date_str, yesterday_str)
                    
                    # 使用固定的top_n值（前5名）
                    top_n = 5
                    if result and len(result) >= top_n:
                        top_n_stocks = result[:top_n]
                        top_n_codes = set([stock[0] for stock in top_n_stocks])
                        
                        cursor.execute('SELECT s.code FROM ladder_stocks ls JOIN ladder_nodes ln ON ls.ladder_node_id = ln.id JOIN stocks s ON ls.stock_id = s.id WHERE ln.date = ?', (yesterday_str,))
                        yesterday_ladder_codes = set([row[0] for row in cursor.fetchall()])
                        
                        cursor.execute('SELECT s.code FROM ladder_stocks ls JOIN ladder_nodes ln ON ls.ladder_node_id = ln.id JOIN stocks s ON ls.stock_id = s.id WHERE ln.date = ?', (date_str,))
                        today_ladder_codes = set([row[0] for row in cursor.fetchall()])
                        
                        # 根据排名计算得分：第1名命中得5分，第2名得4分，依此类推
                        day_score = 0
                        for rank, stock in enumerate(top_n_stocks, 1):
                            code = stock[0]
                            if code in today_ladder_codes:
                                # 根据排名获取得分
                                score_for_rank = self.BACKTEST_RANK_SCORES.get(rank, 0)
                                day_score += score_for_rank
                        
                        if day_score > 0:
                            total_score += day_score
                            valid_days += 1
                
                current_date = current_date.addDays(1)

            return total_score - self._l2_regularization_penalty()

        finally:
            for _attr, _val in _gov_backup.items():
                setattr(self, _attr, _val)
            conn.close()

    def _fetch_limit_up_for_date(self, target_date):
        """核心：获取指定日期的涨停数据并写入DB，返回 (success, result_dict)"""
        import requests, json, re

        date_str = target_date.toString('yyyyMMdd')
        date_yyyy_mm_dd = target_date.toString('yyyy-MM-dd')

        today = QDate.currentDate()
        days_to_check = 0
        check_date = today
        while days_to_check < 7:
            check_date = check_date.addDays(-1)
            if check_date.dayOfWeek() not in [6, 7]:
                days_to_check += 1
        is_recent = target_date >= check_date

        if is_recent:
            stocks_by_lbc, source = self._fetch_from_eastmoney(date_str)
        else:
            stocks_by_lbc, source = self._fetch_from_backup(date_str)

        if not stocks_by_lbc:
            return False, {'reason': '未获取到涨停数据'}

        max_lbc = max(stocks_by_lbc.keys())

        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        try:
            cursor.execute('SELECT id FROM ladder_nodes WHERE date = ?', (date_yyyy_mm_dd,))
            node_ids = [row[0] for row in cursor.fetchall()]
            for node_id in node_ids:
                cursor.execute('DELETE FROM ladder_stocks WHERE ladder_node_id = ?', (node_id,))
            cursor.execute('DELETE FROM ladder_nodes WHERE date = ?', (date_yyyy_mm_dd,))

            cursor.execute('REPLACE INTO ladder_settings (date, ladder_count) VALUES (?, ?)',
                           (date_yyyy_mm_dd, max_lbc))

            added_count = 0
            skipped_count = 0
            skipped_codes = []

            for lbc in sorted(stocks_by_lbc.keys(), reverse=True):
                node_level = max_lbc - lbc + 1
                stocks = stocks_by_lbc[lbc]

                cursor.execute('INSERT INTO ladder_nodes (date, node_level) VALUES (?, ?)',
                               (date_yyyy_mm_dd, node_level))
                node_id = cursor.lastrowid

                order_index = 0
                for code in stocks:
                    cursor.execute('SELECT id FROM stocks WHERE code = ?', (code,))
                    stock_result = cursor.fetchone()
                    if stock_result:
                        stock_id = stock_result[0]
                        cursor.execute('INSERT INTO ladder_stocks (ladder_node_id, stock_id, order_index) VALUES (?, ?, ?)',
                                       (node_id, stock_id, order_index))
                        order_index += 1
                        added_count += 1
                    else:
                        skipped_count += 1
                        skipped_codes.append(code)

            yesterday = target_date.addDays(-1)
            yesterday_str = yesterday.toString('yyyy-MM-dd')
            cursor.execute('SELECT ladder_count FROM ladder_settings WHERE date = ?', (yesterday_str,))
            yesterday_setting = cursor.fetchone()
            yesterday_max_lbc = yesterday_setting[0] if yesterday_setting else 0
            if yesterday_max_lbc > 0:
                cursor.execute('SELECT node_level, node_name FROM ladder_nodes WHERE date = ? AND node_name IS NOT NULL AND node_name != ""', (yesterday_str,))
                yesterday_nodes = cursor.fetchall()
                if yesterday_nodes:
                    yesterday_board_to_name = {}
                    for nl, name in yesterday_nodes:
                        board_count = yesterday_max_lbc - nl + 1
                        yesterday_board_to_name[board_count] = name
                    for today_nl in range(1, max_lbc + 1):
                        today_board = max_lbc - today_nl + 1
                        target_yesterday_board = today_board - 1
                        if target_yesterday_board in yesterday_board_to_name:
                            node_name = yesterday_board_to_name[target_yesterday_board]
                            cursor.execute('UPDATE ladder_nodes SET node_name = ? WHERE date = ? AND node_level = ? AND (node_name IS NULL OR node_name = "")',
                                           (node_name, date_yyyy_mm_dd, today_nl))

            conn.commit()

            total_stocks = sum(len(stocks) for stocks in stocks_by_lbc.values())
            return True, {
                'source': source,
                'total_stocks': total_stocks,
                'added_count': added_count,
                'max_lbc': max_lbc,
                'skipped_count': skipped_count,
                'skipped_codes': skipped_codes,
            }
        except Exception as e:
            conn.rollback()
            return False, {'reason': str(e)}
        finally:
            conn.close()

    def auto_get_limit_up_data(self):
        """自动获取涨停数据（优先使用东方财富接口，更早数据使用备用接口）"""
        try:
            target_date = self.limit_up_date.date()

            self.auto_get_limit_up_btn.setEnabled(False)
            self.auto_get_limit_up_btn.setText('获取中...')

            success, result = self._fetch_limit_up_for_date(target_date)

            if not success:
                QMessageBox.information(self, '提示', result.get('reason', '获取失败'))
                return

            msg = (f'成功获取涨停数据！\n\n数据源: {result["source"]}\n'
                   f'共 {result["total_stocks"]} 只涨停股票\n'
                   f'成功导入 {result["added_count"]} 只\n'
                   f'最高连板: {result["max_lbc"]} 板')
            if result['skipped_count'] > 0:
                msg += f'\n\n跳过 {result["skipped_count"]} 只（未在股票列表中）:\n{", ".join(result["skipped_codes"])}'

            QMessageBox.information(self, '获取成功', msg)
            self.load_limit_up_data()
        except Exception as e:
            QMessageBox.warning(self, '错误', f'获取失败: {str(e)}')
        finally:
            self.auto_get_limit_up_btn.setEnabled(True)
            self.auto_get_limit_up_btn.setText('自动获取涨停数据')

    def batch_fetch_limit_up_data(self):
        """批量获取多日涨停梯队数据"""
        from PyQt5.QtWidgets import QDialog, QVBoxLayout, QHBoxLayout, QDateEdit, QDialogButtonBox

        dialog = QDialog(self)
        dialog.setWindowTitle('批量获取涨停梯队')
        dialog.setMinimumWidth(350)

        layout = QVBoxLayout(dialog)
        range_layout = QHBoxLayout()
        range_layout.addWidget(QLabel('起始日期:'))
        start_date = QDateEdit()
        start_date.setCalendarPopup(True)
        start_date.setDate(QDate.currentDate().addDays(-5))
        range_layout.addWidget(start_date)
        range_layout.addWidget(QLabel('结束日期:'))
        end_date = QDateEdit()
        end_date.setCalendarPopup(True)
        end_date.setDate(QDate.currentDate())
        range_layout.addWidget(end_date)
        layout.addLayout(range_layout)

        btn_box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btn_box.accepted.connect(dialog.accept)
        btn_box.rejected.connect(dialog.reject)
        layout.addWidget(btn_box)

        if dialog.exec_() != QDialog.Accepted:
            return

        sd = start_date.date()
        ed = end_date.date()
        if sd > ed:
            QMessageBox.warning(self, '提示', '起始日期不能晚于结束日期')
            return

        trading_dates = []
        d = sd
        while d <= ed:
            if d.dayOfWeek() not in [6, 7] and not self.is_chinese_holiday(d):
                trading_dates.append(d)
            d = d.addDays(1)

        if not trading_dates:
            QMessageBox.warning(self, '提示', '所选区间内无交易日')
            return

        total = len(trading_dates)
        reply = QMessageBox.question(self, '确认',
            f'所选区间共 {total} 个交易日，将逐个获取涨停数据。\n\n是否继续？',
            QMessageBox.Yes | QMessageBox.No)
        if reply != QMessageBox.Yes:
            return

        from PyQt5.QtWidgets import QProgressDialog
        progress = QProgressDialog('正在批量获取涨停数据...', '取消', 0, total, self)
        progress.setWindowTitle('批量获取涨停梯队')
        progress.setWindowModality(2)
        progress.setMinimumDuration(0)

        success_count = 0
        total_added = 0
        total_skipped = 0
        errors = []

        for i, qdate in enumerate(trading_dates):
            if progress.wasCanceled():
                break

            date_str = qdate.toString('yyyy-MM-dd')
            progress.setValue(i)
            progress.setLabelText(f'[{i+1}/{total}] {date_str}')

            try:
                success, result = self._fetch_limit_up_for_date(qdate)
                if success:
                    success_count += 1
                    total_added += result['added_count']
                    total_skipped += result['skipped_count']
                else:
                    errors.append(f'{date_str}: {result.get("reason", "未知错误")[:60]}')
            except Exception as e:
                errors.append(f'{date_str}: {str(e)[:60]}')

            import time
            time.sleep(1.5)

        progress.setValue(total)

        summary = f'批量获取完成！\n\n成功获取 {success_count}/{total} 个交易日\n共导入 {total_added} 只股票'
        if total_skipped > 0:
            summary += f'\n跳过（未在股票列表）{total_skipped} 只'
        if errors:
            summary += f'\n\n{len(errors)} 个日期失败：'
            for e in errors[:8]:
                summary += f'\n  {e}'

        QMessageBox.information(self, '批量获取完成', summary)
        self.load_limit_up_data()

    def show_ladder_attr_summary(self):
        from PyQt5.QtWidgets import QDialog, QTextEdit, QVBoxLayout, QPushButton, QHBoxLayout, QLabel, QFrame

        date = self.limit_up_date.date().toString('yyyy-MM-dd')

        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        try:
            cursor.execute('''
                SELECT s.id, s.code, s.name, ln.node_level, ls.order_index
                FROM ladder_stocks ls
                JOIN ladder_nodes ln ON ls.ladder_node_id = ln.id
                JOIN stocks s ON ls.stock_id = s.id
                WHERE ln.date = ?
                ORDER BY ln.node_level, ls.order_index
            ''', (date,))
            ladder_stocks = cursor.fetchall()

            if not ladder_stocks:
                QMessageBox.information(self, '提示', f'{date} 没有涨停梯队数据')
                return

            stock_order = {}
            for row in ladder_stocks:
                stock_order[row[0]] = (row[3], row[4])

            stock_ids = [row[0] for row in ladder_stocks]
            placeholders = ','.join('?' * len(stock_ids))

            cursor.execute(f'''
                SELECT s.id, s.code, s.name, a.attribute
                FROM stock_attributes a
                JOIN stocks s ON a.stock_id = s.id
                WHERE a.stock_id IN ({placeholders})
            ''', stock_ids)
            attr_records = cursor.fetchall()

            attr_count = {}
            attr_stocks_map = {}
            for stock_id, code, name, attr in attr_records:
                attr_count[attr] = attr_count.get(attr, 0) + 1
                if attr not in attr_stocks_map:
                    attr_stocks_map[attr] = []
                attr_stocks_map[attr].append((name, stock_id))

            def sort_key_by_ladder(item):
                _, sid = item
                order = stock_order.get(sid, (999, 999))
                return order

            for attr in attr_stocks_map:
                attr_stocks_map[attr].sort(key=sort_key_by_ladder)

            sorted_attrs = sorted(attr_count.items(), key=lambda x: (-x[1], x[0]))

            total_stock_count = len(ladder_stocks)
            total_attr_count = len(attr_count)

            dialog = QDialog(self)
            dialog.setWindowTitle(f'涨停梯队属性汇总 - {date}')
            dialog.setWindowFlags(dialog.windowFlags() & ~Qt.WindowContextHelpButtonHint)
            dialog.resize(760, 640)
            if self.isVisible():
                dialog.move(
                    self.x() + (self.width() - dialog.width()) // 2,
                    self.y() + (self.height() - dialog.height()) // 2
                )

            dialog.setStyleSheet('''
                QDialog {
                    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                        stop:0 #f0f4f8, stop:1 #e8edf3);
                    border-radius: 12px;
                }
            ''')

            layout = QVBoxLayout(dialog)
            layout.setContentsMargins(0, 0, 0, 0)
            layout.setSpacing(0)

            header_frame = QFrame()
            header_frame.setStyleSheet('''
                QFrame {
                    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                        stop:0 #1a237e, stop:0.5 #283593, stop:1 #3949ab);
                    border-top-left-radius: 12px;
                    border-top-right-radius: 12px;
                    padding: 16px 20px;
                }
            ''')
            header_layout = QVBoxLayout(header_frame)
            header_layout.setContentsMargins(20, 14, 20, 14)

            title_label = QLabel(f'📊 涨停梯队属性汇总')
            title_label.setStyleSheet('font-size: 18px; font-weight: bold; color: white;')
            header_layout.addWidget(title_label)

            subtitle_label = QLabel(f'{date}  ·  共 {total_stock_count} 只股票  ·  {total_attr_count} 个属性')
            subtitle_label.setStyleSheet('font-size: 12px; color: rgba(255,255,255,0.75); margin-top: 2px;')
            header_layout.addWidget(subtitle_label)

            layout.addWidget(header_frame)

            content_container = QFrame()
            content_container.setStyleSheet('''
                QFrame {
                    background: white;
                    margin: 0px;
                    padding: 0px;
                }
            ''')
            content_layout = QVBoxLayout(content_container)
            content_layout.setContentsMargins(16, 12, 16, 8)
            content_layout.setSpacing(0)

            summary_bar = QFrame()
            summary_bar.setStyleSheet('''
                QFrame {
                    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                        stop:0 #e8eaf6, stop:1 #f5f5ff);
                    border: 1px solid #c5cae9;
                    border-radius: 8px;
                    padding: 10px 14px;
                }
            ''')
            summary_bar_layout = QHBoxLayout(summary_bar)
            summary_bar_layout.setContentsMargins(14, 8, 14, 8)

            top_attr_label = QLabel(f'🏆 最多属性: <b>{sorted_attrs[0][0]}</b> ({sorted_attrs[0][1]}次)')
            top_attr_label.setStyleSheet('font-size: 13px; color: #37474f;')
            summary_bar_layout.addWidget(top_attr_label)
            summary_bar_layout.addStretch()

            avg_label = QLabel(f'📈 平均属性/股: <b>{total_attr_count / total_stock_count:.1f}</b>')
            avg_label.setStyleSheet('font-size: 13px; color: #37474f;')
            summary_bar_layout.addWidget(avg_label)

            content_layout.addWidget(summary_bar)

            content_layout.addSpacing(8)

            section_label = QLabel('📋 属性排名')
            section_label.setStyleSheet('font-size: 14px; font-weight: bold; color: #1a237e; padding: 6px 2px;')
            content_layout.addWidget(section_label)

            text_edit = QTextEdit()
            text_edit.setReadOnly(True)
            text_edit.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
            text_edit.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
            text_edit.setStyleSheet('''
                QTextEdit {
                    background: #fafbfc;
                    border: 1px solid #e0e0e0;
                    border-radius: 8px;
                    padding: 12px 16px;
                    font-size: 13px;
                    color: #263238;
                    selection-background-color: #bbdefb;
                }
                QTextEdit:focus {
                    border: 1px solid #7986cb;
                }
            ''')

            html_parts = []
            html_parts.append('<style>')
            html_parts.append('''
                body { font-family: "Microsoft YaHei", "PingFang SC", "Helvetica Neue", Arial, sans-serif; margin: 0; padding: 0; }
                table.attr-table { width: 100%; border-collapse: collapse; margin: 6px 0; }
                table.attr-table th {
                    background: #e8eaf6; color: #1a237e; font-weight: bold; font-size: 12px;
                    padding: 8px 10px; text-align: center; border-bottom: 2px solid #7986cb;
                    position: sticky; top: 0; z-index: 1;
                }
                table.attr-table td { padding: 7px 10px; text-align: center; font-size: 13px; border-bottom: 1px solid #eeeeee; }
                table.attr-table tr:nth-child(even) td { background: #f5f6fa; }
                table.attr-table tr:hover td { background: #e3f2fd; }
                .rank-num { display: inline-block; width: 22px; height: 22px; line-height: 22px; border-radius: 11px;
                    font-size: 11px; font-weight: bold; text-align: center; color: white; }
                .rank-1 { background: linear-gradient(135deg, #ff6f00, #ffb300); }
                .rank-2 { background: linear-gradient(135deg, #546e7a, #90a4ae); }
                .rank-3 { background: linear-gradient(135deg, #6d4c41, #a1887f); }
                .rank-other { background: #bdbdbd; }
                .count-badge { display: inline-block; background: #e8eaf6; color: #283593; font-weight: bold;
                    padding: 1px 10px; border-radius: 10px; font-size: 12px; }
                .pct-badge { display: inline-block; color: #78909c; font-size: 12px; }
            ''')
            html_parts.append('</style>')

            html_parts.append('<table class="attr-table">')
            html_parts.append('<tr><th style="width:40px">#</th><th style="text-align:left">属性名称</th><th style="width:80px">次数</th><th style="width:60px">占比</th><th>分布</th></tr>')

            for i, (attr, count) in enumerate(sorted_attrs):
                rank = i + 1
                if rank == 1:
                    rank_class = 'rank-1'
                elif rank == 2:
                    rank_class = 'rank-2'
                elif rank == 3:
                    rank_class = 'rank-3'
                else:
                    rank_class = 'rank-other'

                pct = count / total_stock_count * 100
                stock_names = attr_stocks_map.get(attr, [])
                names_str = '、'.join(s[0] for s in stock_names)

                html_parts.append(f'<tr><td><span class="rank-num {rank_class}">{rank}</span></td>')
                html_parts.append(f'<td style="text-align:left;font-weight:{600 if rank <= 3 else 400};">{attr}</td>')
                html_parts.append(f'<td><span class="count-badge">{count}</span></td>')
                html_parts.append(f'<td><span class="pct-badge">{pct:.1f}%</span></td>')
                html_parts.append(f'<td style="text-align:left;font-size:12px;line-height:1.6;max-width:340px;word-break:break-all">{names_str}</td></tr>')

            html_parts.append('</table>')

            text_edit.setHtml(''.join(html_parts))
            content_layout.addWidget(text_edit)

            content_layout.addSpacing(6)

            btn_container = QFrame()
            btn_container.setStyleSheet('QFrame { background: transparent; }')
            btn_layout = QHBoxLayout(btn_container)
            btn_layout.setContentsMargins(4, 4, 4, 4)

            close_btn = QPushButton('✕  关闭')
            close_btn.setCursor(Qt.PointingHandCursor)
            close_btn.setStyleSheet('''
                QPushButton {
                    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                        stop:0 #f5f5f5, stop:1 #e0e0e0);
                    border: 1px solid #ccc;
                    border-radius: 6px;
                    padding: 8px 28px;
                    font-size: 13px;
                    font-weight: bold;
                    color: #424242;
                }
                QPushButton:hover {
                    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                        stop:0 #e8eaf6, stop:1 #c5cae9);
                    border: 1px solid #9fa8da;
                    color: #1a237e;
                }
                QPushButton:pressed {
                    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                        stop:0 #c5cae9, stop:1 #9fa8da);
                }
            ''')
            close_btn.clicked.connect(dialog.accept)
            btn_layout.addStretch()
            btn_layout.addWidget(close_btn)

            content_layout.addWidget(btn_container)

            layout.addWidget(content_container)

            dialog.exec_()

        except Exception as e:
            QMessageBox.warning(self, '错误', f'获取属性汇总失败: {str(e)}')
            print(f'show_ladder_attr_summary error: {e}')
            import traceback
            traceback.print_exc()
        finally:
            conn.close()

    def _fetch_from_eastmoney(self, date_str):
        """从东方财富接口获取涨停数据"""
        import requests
        import json
        import re
        
        url = f'https://push2ex.eastmoney.com/getTopicZTPool?cb=callbackdata&ut=7eea3edcaed734bea9cbfc24409ed989&dpt=wz.ztzt&Pageindex=0&pagesize=170&sort=fbt%3Aasc&date={date_str}&_=1777472326957'
        
        headers = {
            'Accept': '*/*',
            'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
            'Connection': 'keep-alive',
            'Referer': 'https://quote.eastmoney.com/ztb/detail',
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36'
        }
        
        print(f"[东方财富接口] 正在请求: {url}")
        response = requests.get(url, headers=headers, timeout=30)
        print(f"[东方财富接口] 请求完成，状态码: {response.status_code}")
        response.raise_for_status()
        
        # 处理JSONP格式，提取JSON部分
        content = response.text
        print(f"[东方财富接口] 响应长度: {len(content)} 字符")
        
        match = re.match(r'callbackdata\((.*)\)', content)
        if not match:
            print(f"[东方财富接口] 错误: 无法解析JSONP格式")
            raise ValueError('无法解析东方财富返回数据')
        
        json_data = json.loads(match.group(1))
        print(f"[东方财富接口] JSON解析成功")
        
        if 'data' not in json_data or 'pool' not in json_data['data']:
            print(f"[东方财富接口] 错误: 返回数据格式错误，缺少data或pool字段")
            raise ValueError('东方财富返回数据格式错误')
        
        pool = json_data['data']['pool']
        print(f"[东方财富接口] 获取到 {len(pool)} 只涨停股票")
        
        # 按连板数分组
        stocks_by_lbc = {}
        for item in pool:
            code = item.get('c')
            lbc = item.get('lbc', 1)
            if code:
                if lbc not in stocks_by_lbc:
                    stocks_by_lbc[lbc] = []
                stocks_by_lbc[lbc].append(code)
        
        print(f"[东方财富接口] 分组完成，共 {len(stocks_by_lbc)} 个梯队")
        for lbc, stocks in sorted(stocks_by_lbc.items(), reverse=True):
            print(f"  - {lbc}板: {len(stocks)} 只")
        
        return stocks_by_lbc, '东方财富'
    
    def _fetch_from_backup(self, date_str):
        """从备用接口获取涨停数据"""
        import requests
        import json
        
        url = f'https://stock.quicktiny.cn/api/ladder/day/{date_str}'
        
        headers = {
            'accept': 'application/json, text/plain, */*',
            'accept-language': 'zh-CN,zh;q=0.9,en;q=0.8',
            'authorization': 'Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpZCI6IjY5NjcwNmJiZGUxMzJkODFkM2ZhMDgzYyIsInZlcnNpb24iOjAsImlhdCI6MTc3NzQ0Nzg2OSwiZXhwIjoxNzgwMDM5ODY5fQ.WmOGaekC9ZiLw6G3CHG_abkFK5OrZr65AlgfSJEop2k',
            'referer': 'https://stock.quicktiny.cn/stock-ladder',
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36'
        }
        
        print(f"[备用接口] 正在请求: {url}")
        response = requests.get(url, headers=headers, timeout=30)
        print(f"[备用接口] 请求完成，状态码: {response.status_code}")
        response.raise_for_status()
        
        json_data = response.json()
        print(f"[备用接口] JSON解析成功")
        
        if 'dates' not in json_data or len(json_data['dates']) == 0:
            print(f"[备用接口] 错误: 返回数据格式错误，缺少dates字段或dates为空")
            raise ValueError('备用接口返回数据格式错误')
        
        date_data = json_data['dates'][0]
        print(f"[备用接口] 日期数据获取成功")
        
        if 'boards' not in date_data:
            print(f"[备用接口] 错误: 返回数据格式错误，缺少boards字段")
            raise ValueError('备用接口返回数据格式错误')
        
        boards = date_data['boards']
        print(f"[备用接口] 获取到 {len(boards)} 个梯队")
        
        # 按板数分组
        stocks_by_lbc = {}
        total_stocks = 0
        for board in boards:
            level = board.get('level', 1)
            stocks = board.get('stocks', [])
            total_stocks += len(stocks)
            for stock in stocks:
                code = stock.get('code')
                if code:
                    if level not in stocks_by_lbc:
                        stocks_by_lbc[level] = []
                    stocks_by_lbc[level].append(code)
        
        print(f"[备用接口] 共 {total_stocks} 只涨停股票")
        print(f"[备用接口] 分组完成，共 {len(stocks_by_lbc)} 个梯队")
        for lbc, stocks in sorted(stocks_by_lbc.items(), reverse=True):
            print(f"  - {lbc}板: {len(stocks)} 只")
        
        return stocks_by_lbc, '备用接口'

    def get_holder_ratio_data(self):
        """从东方财富获取股东股本数据"""
        import requests
        import json
        from urllib.parse import quote
        
        try:
            # 获取当前日期
            today = QDate.currentDate()
            month = today.month()
            
            # 判断当前季度
            if month <= 3:
                end_date = f"{today.year()}-03-31"
            elif month <= 6:
                end_date = f"{today.year()}-03-31"
            elif month <= 9:
                end_date = f"{today.year()}-06-30"
            else:
                end_date = f"{today.year()}-09-30"
            
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            cursor.execute('SELECT code, name FROM stocks ORDER BY code')
            stocks = cursor.fetchall()
            
            self.get_holder_ratio_btn.setEnabled(False)
            self.get_holder_ratio_btn.setText('获取中...')
            
            success_count = 0
            fail_count = 0
            fail_codes = []
            
            print(f"[股东股本接口] 开始获取数据，目标日期: {end_date}")
            
            for code, name in stocks:
                try:
                    # 跳过8开头的股票（北交所股票）
                    if code.startswith('8'):
                        print(f"[股东股本接口] {code} {name}: 跳过8开头股票")
                        continue
                        
                    # 判断市场代码
                    if code.startswith('60') or code.startswith('688'):
                        market = 'SH'
                    else:
                        market = 'SZ'
                    
                    secucode = f"{code}.{market}"
                    
                    # URL编码filter参数
                    filter_str = f'(SECUCODE%3D%22{secucode}%22)(END_DATE%3D%27{end_date}%27)'
                    
                    url = f'https://datacenter.eastmoney.com/securities/api/data/v1/get?reportName=RPT_F10_EH_HOLDERS&columns=ALL&quoteColumns=&filter={filter_str}&pageNumber=1&pageSize=&sortTypes=1&sortColumns=HOLDER_RANK&source=HSF10&client=PC&v=09275759773361191'
                    
                    headers = {
                        'Accept': '*/*',
                        'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
                        'Connection': 'keep-alive',
                        'Origin': 'https://emweb.securities.eastmoney.com',
                        'Referer': 'https://emweb.securities.eastmoney.com/',
                        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36'
                    }
                    
                    response = requests.get(url, headers=headers, timeout=10)
                    response.raise_for_status()
                    
                    json_data = response.json()
                    
                    if json_data.get('result') and json_data['result'].get('data'):
                        holder_ratio = json_data['result']['data'][0].get('HOLD_NUM_RATIO')
                        if holder_ratio is not None:
                            cursor.execute('UPDATE stocks SET holder_ratio = ? WHERE code = ?', (holder_ratio, code))
                            success_count += 1
                            print(f"[股东股本接口] {code} {name}: {holder_ratio}%")
                        else:
                            fail_count += 1
                            fail_codes.append(code)
                            print(f"[股东股本接口] {code} {name}: 无持股比例数据")
                    else:
                        fail_count += 1
                        fail_codes.append(code)
                        print(f"[股东股本接口] {code} {name}: 无数据")
                    
                except Exception as e:
                    fail_count += 1
                    fail_codes.append(code)
                    print(f"[股东股本接口] {code} 获取失败: {str(e)}")
            
            conn.commit()
            conn.close()
            
            print(f"[股东股本接口] 获取完成: 成功 {success_count}, 失败 {fail_count}")
            
            msg = f'获取完成！\n\n成功: {success_count} 只\n失败: {fail_count} 只'
            if fail_codes:
                msg += f'\n\n失败股票代码:\n{", ".join(fail_codes)}'
            
            QMessageBox.information(self, '获取结果', msg)
            
        except Exception as e:
            QMessageBox.warning(self, '错误', f'获取失败: {str(e)}')
            print(f"[股东股本接口] 错误: {str(e)}")
        finally:
            self.get_holder_ratio_btn.setEnabled(True)
            self.get_holder_ratio_btn.setText('获取股票股本')

    def refresh_stock_attributes(self):
        """弹窗输入多个股票代码，刷新其板块属性"""
        from PyQt5.QtWidgets import QInputDialog, QTextEdit, QVBoxLayout, QDialog, QDialogButtonBox

        dialog = QDialog(self)
        dialog.setWindowTitle('刷新股票属性')
        dialog.setMinimumWidth(400)

        layout = QVBoxLayout(dialog)
        layout.addWidget(QLabel('输入股票代码（多个用逗号、空格或换行分隔）：'))

        text_edit = QTextEdit()
        text_edit.setPlaceholderText('例如：600519, 000858, 920199')
        text_edit.setMaximumHeight(120)
        layout.addWidget(text_edit)

        btn_box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btn_box.accepted.connect(dialog.accept)
        btn_box.rejected.connect(dialog.reject)
        layout.addWidget(btn_box)

        if dialog.exec_() != QDialog.Accepted:
            return

        raw = text_edit.toPlainText().strip()
        if not raw:
            return

        # 解析代码：去掉空格、逗号、换行，取6位数字
        codes = re.findall(r'(\d{6})', raw)
        if not codes:
            QMessageBox.warning(self, '提示', '未识别到有效的股票代码')
            return

        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        missing_codes = []
        found_stocks = []
        for code in codes:
            cursor.execute('SELECT id, name, code FROM stocks WHERE code = ?', (code,))
            row = cursor.fetchone()
            if row:
                found_stocks.append((row[0], row[1], code))
            else:
                missing_codes.append(code)

        if not found_stocks:
            QMessageBox.warning(self, '提示', '这些股票代码在本地库中不存在，请先添加股票')
            conn.close()
            return

        msg = f'将从东方财富获取 {len(found_stocks)} 只股票的板块属性并覆盖保存'
        if missing_codes:
            msg += f'\n\n以下 {len(missing_codes)} 个代码不在本地库中，已跳过：\n' + ', '.join(missing_codes)
        reply = QMessageBox.question(self, '确认', msg,
                                     QMessageBox.Yes | QMessageBox.No)
        if reply != QMessageBox.Yes:
            conn.close()
            return

        from PyQt5.QtWidgets import QProgressDialog
        progress = QProgressDialog('正在获取板块属性...', '取消', 0, len(found_stocks), self)
        progress.setWindowTitle('刷新属性')
        progress.setWindowModality(2)
        progress.setMinimumDuration(0)

        headers = {
            'Accept': '*/*',
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        }

        for i, (stock_id, name, code) in enumerate(found_stocks):
            if progress.wasCanceled():
                break

            progress.setValue(i)
            progress.setLabelText(f'正在处理 {name}({code})  ({i+1}/{len(found_stocks)})')

            try:
                if code.startswith('920'):
                    market = 'BJ'
                elif code.startswith('60') or code.startswith('688'):
                    market = 'SH'
                else:
                    market = 'SZ'
                secucode = f'{code}.{market}'

                board_names = []
                url = (
                    "https://datacenter.eastmoney.com/securities/api/data/v1/get?"
                    "reportName=RPT_F10_CORETHEME_BOARDTYPE&"
                    "columns=SECUCODE,SECURITY_CODE,SECURITY_NAME_ABBR,NEW_BOARD_CODE,BOARD_NAME,SELECTED_BOARD_REASON,IS_PRECISE,BOARD_RANK,BOARD_YIELD,DERIVE_BOARD_CODE&"
                    f"filter=(SECUCODE%3D%22{secucode}%22)(IS_PRECISE%3D%221%22)&"
                    "pageNumber=1&pageSize=200&sortTypes=1&sortColumns=BOARD_RANK&"
                    "source=HSF10&client=PC&v=05407725378079107"
                )
                resp = requests.get(url, headers=headers, timeout=10)
                data = resp.json()
                for item in (data.get('result') or {}).get('data', []):
                    bn = item.get('BOARD_NAME', '')
                    if bn and bn not in board_names:
                        board_names.append(bn)

                url2 = (
                    "https://datacenter.eastmoney.com/securities/api/data/get?"
                    "type=RPT_F10_CORETHEME_BOARDTYPE&sty=ALL&"
                    f"filter=(SECUCODE%3D%22{secucode}%22)&"
                    "p=1&ps=200&sr=1&st=BOARD_RANK&"
                    "source=HSF10&client=PC&v=04828710067581792"
                )
                resp2 = requests.get(url2, headers=headers, timeout=10)
                data2 = resp2.json()
                for item in data2.get('result', {}).get('data', []):
                    bn = item.get('BOARD_NAME', '')
                    if bn and bn not in board_names:
                        board_names.append(bn)

                # 合并保存：保留旧属性，新增不存在的属性
                today = datetime.now().strftime('%Y-%m-%d')
                for board_name in board_names:
                    cursor.execute(
                        'SELECT id FROM stock_attributes WHERE stock_id = ? AND attribute = ?',
                        (stock_id, board_name))
                    if not cursor.fetchone():
                        cursor.execute(
                            'INSERT INTO stock_attributes (stock_id, date, attribute) VALUES (?, ?, ?)',
                            (stock_id, today, board_name)
                        )
                conn.commit()
            except Exception as e:
                print(f'刷新 {code} 属性失败: {e}')

        conn.close()
        progress.setValue(len(found_stocks))
        QMessageBox.information(self, '完成', f'已刷新 {len(found_stocks)} 只股票的板块属性')

    def get_total_market_cap_data(self):
        """获取股票总市值数据"""
        import requests
        
        try:
            # 弹出确认对话框，询问是否更新已有数据
            reply = QMessageBox.question(
                self,
                '确认获取',
                '是否更新已有市值数据的股票？\n\n是：更新所有股票\n否：只更新没有市值数据的股票（默认）',
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No  # 默认选择否
            )
            
            update_existing = (reply == QMessageBox.Yes)
            
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            # 根据用户选择决定查询条件
            if update_existing:
                cursor.execute('SELECT code, name FROM stocks ORDER BY code')
            else:
                cursor.execute('SELECT code, name FROM stocks WHERE total_market_cap IS NULL ORDER BY code')
            
            stocks = cursor.fetchall()
            
            if not stocks:
                QMessageBox.information(self, '提示', '没有需要获取市值数据的股票')
                return
            
            print(f"[总市值接口] 待获取股票数量: {len(stocks)} 只")
            if len(stocks) > 0 and not update_existing:
                print(f"[总市值接口] 以下股票缺少市值数据，将获取:")
                for code, name in stocks[:5]:  # 只打印前5个
                    print(f"  - {code} {name}")
                if len(stocks) > 5:
                    print(f"  ... 还有 {len(stocks) - 5} 只股票")
            
            self.get_market_cap_btn.setEnabled(False)
            self.get_market_cap_btn.setText('获取中...')
            
            success_count = 0
            fail_count = 0
            fail_codes = []
            
            print(f"[总市值接口] 开始获取数据 (更新已有: {update_existing})")
            
            try:
                for code, name in stocks:
                    try:
                        # 跳过8开头的股票（北交所股票）
                        if code.startswith('8'):
                            print(f"[总市值接口] {code} {name}: 跳过8开头股票")
                            continue
                            
                        # 判断市场代码
                        if code.startswith('60') or code.startswith('688'):
                            market = 'SH'
                        else:
                            market = 'SZ'
                        
                        secucode = f"{code}.{market}"
                        
                        # URL编码filter参数
                        filter_str = f'(SECUCODE%3D%22{secucode}%22)'
                        
                        url = f'https://datacenter.eastmoney.com/securities/api/data/v1/get?reportName=RPT_DMSK_NEWINDICATOR&columns=SECURITY_CODE%2CSECUCODE%2CPE_DYNAMIC_EXPLAIN%2CPE_STATIC_EXPLAIN&quoteColumns=f20~01~SECURITY_CODE~TOTAL_MARKET_CAP&filter={filter_str}&sortTypes=&sortColumns=&pageNumber=1&pageSize=1&source=HSF10&client=PC&v=07776959445230575'
                        
                        headers = {
                            'Accept': '*/*',
                            'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
                            'Connection': 'keep-alive',
                            'Origin': 'https://emweb.securities.eastmoney.com',
                            'Referer': 'https://emweb.securities.eastmoney.com/',
                            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36'
                        }
                        
                        response = requests.get(url, headers=headers, timeout=10)
                        response.raise_for_status()
                        
                        json_data = response.json()
                        
                        if json_data.get('result') and json_data['result'].get('data'):
                            total_market_cap = json_data['result']['data'][0].get('TOTAL_MARKET_CAP')
                            if total_market_cap is not None:
                                cursor.execute('UPDATE stocks SET total_market_cap = ? WHERE code = ?', (total_market_cap, code))
                                success_count += 1
                                market_cap_billion = total_market_cap / 100000000
                                print(f"[总市值接口] {code} {name}: {market_cap_billion:.2f}亿")
                            else:
                                fail_count += 1
                                fail_codes.append(code)
                                print(f"[总市值接口] {code} {name}: 无总市值数据")
                        else:
                            fail_count += 1
                            fail_codes.append(code)
                            print(f"[总市值接口] {code} {name}: 无数据")
                        
                    except Exception as e:
                        fail_count += 1
                        fail_codes.append(code)
                        print(f"[总市值接口] {code} 获取失败: {str(e)}")
            
            except KeyboardInterrupt:
                print(f"[总市值接口] 用户中断，正在保存已获取的数据...")
                conn.commit()
                conn.close()
                QMessageBox.information(self, '提示', f'已中断获取！\n\n已保存成功获取的 {success_count} 只股票市值数据')
                print(f"[总市值接口] 已保存 {success_count} 只股票市值数据")
                return
            
            conn.commit()
            conn.close()
            
            print(f"[总市值接口] 获取完成: 成功 {success_count}, 失败 {fail_count}")
            
            msg = f'获取完成！\n\n成功: {success_count} 只\n失败: {fail_count} 只'
            if fail_codes:
                msg += f'\n\n失败股票代码:\n{", ".join(fail_codes)}'
            
            QMessageBox.information(self, '获取结果', msg)
            
            # 重新加载股票列表
            self.load_stocks(self.filter_input.text().strip())
            
        except Exception as e:
            QMessageBox.warning(self, '错误', f'获取失败: {str(e)}')
            print(f"[总市值接口] 错误: {str(e)}")
        finally:
            self.get_market_cap_btn.setEnabled(True)
            self.get_market_cap_btn.setText('获取股票总市值')

if __name__ == '__main__':
    app = QApplication(sys.argv)
    window = StockMasterApp()
    window.show()
    sys.exit(app.exec_())