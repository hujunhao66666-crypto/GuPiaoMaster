# 工作线程模块 - QThread子类

import math
import os
import random
import sqlite3
from datetime import datetime

from PyQt5.QtCore import QThread, pyqtSignal

from config import (
    BAYESIAN_BOUNDS, BAYESIAN_TO_COEFFICIENT,
    SHRINK_FACTOR, SHRINK_MIN_RATIO, L2_LAMBDA, L2_SCALE
)


class BayesianOptimizationThread(QThread):
    """贝叶斯优化工作线程"""
    progress_signal = pyqtSignal(dict)
    finished_signal = pyqtSignal(dict)
    error_signal = pyqtSignal(str)

    def __init__(self, parent, db_path, train_start, train_end, valid_start, valid_end,
                 user_runs, user_init_points, user_n_iter, user_shrink_rounds, custom_pbounds=None,
                 linear_param_set=None, ema_alpha=0.95):
        super().__init__(parent)
        self.main = parent
        self.db_path = db_path
        self.train_start = train_start
        self.train_end = train_end
        self.valid_start = valid_start
        self.valid_end = valid_end
        self.user_runs = user_runs
        self.user_init_points = user_init_points
        self.user_n_iter = user_n_iter
        self.user_shrink_rounds = user_shrink_rounds
        self.custom_pbounds = custom_pbounds
        self.linear_param_set = linear_param_set or set()
        self.ema_alpha = ema_alpha
        self.original_coeffs = {}
        self.logs = []

    def log(self, msg):
        self.logs.append(msg)
        print(msg)

    def run(self):
        from bayes_opt import BayesianOptimization
        try:
            if self.custom_pbounds is not None:
                pbounds = self.custom_pbounds.copy()
                self.log("[贝叶斯] 使用自适应参数范围（线性窄域/非线性全域）")
            else:
                pbounds = BAYESIAN_BOUNDS.copy()
            # 通用范围扩大：所有参数范围乘以3倍（以上一轮最优值为中心向外扩）
            EXPANSION_FACTOR = 3.0
            for k in list(pbounds.keys()):
                lo, hi = pbounds[k]
                center = (lo + hi) / 2
                half_range = (hi - lo) / 2 * EXPANSION_FACTOR
                min_range = max(abs(center) * 0.05, 0.001)
                if half_range < min_range / 2:
                    half_range = min_range / 2
                pbounds[k] = (center - half_range, center + half_range)
            self.log(f"[贝叶斯] 参数范围已扩大 {EXPANSION_FACTOR} 倍（防局部最优）")

            # 过滤掉因子治理中禁用的参数
            gov = getattr(self.main, '_factor_enabled', {})
            disabled_params = {k for k, v in gov.items() if not v}
            if disabled_params:
                for k in list(pbounds.keys()):
                    if k in disabled_params:
                        del pbounds[k]
                self.log(f"[贝叶斯] 因子治理禁用了 {len(disabled_params)} 个参数: {sorted(disabled_params)}")
                self.log(f"[贝叶斯] 优化参数数: {len(pbounds)} 个")
            else:
                self.log(f"[贝叶斯] 全部 {len(pbounds)} 个参数参与优化")

            self.original_coeffs = {
                'STOCK_WEIGHT_FIRST': self.main.STOCK_WEIGHT_FIRST,
                'STOCK_WEIGHT_LAST': self.main.STOCK_WEIGHT_LAST,
                'BOARD_WEIGHT': self.main.BOARD_WEIGHT,
                'YZ_OVERALL_WEIGHT': self.main.YZ_OVERALL_WEIGHT,
                'LETTER_ATTR_WEIGHT': self.main.LETTER_ATTR_WEIGHT,
                'REGION_ATTR_WEIGHT': self.main.REGION_ATTR_WEIGHT,
                'ATTR_COUNT_WEIGHT': self.main.ATTR_COUNT_WEIGHT,
                'NEGATIVE_ATTR_COUNT_WEIGHT': self.main.NEGATIVE_ATTR_COUNT_WEIGHT,
                'NEGATIVE_ATTR_WEIGHT': self.main.NEGATIVE_ATTR_WEIGHT,
                'NEGATIVE_LETTER_ATTR_WEIGHT': self.main.NEGATIVE_LETTER_ATTR_WEIGHT,
                'NEGATIVE_REGION_ATTR_WEIGHT': self.main.NEGATIVE_REGION_ATTR_WEIGHT,
                'BOARD_PRESS_WEIGHT': self.main.BOARD_PRESS_WEIGHT,
                'NODE_GUIDE_WEIGHT': self.main.NODE_GUIDE_WEIGHT,
                'HOLDER_RATIO_WEIGHT': self.main.HOLDER_RATIO_WEIGHT,
                'MARKET_CAP_WEIGHT': self.main.MARKET_CAP_WEIGHT,
                'MARKET_CAP_EXPONENT': self.main.MARKET_CAP_EXPONENT,
            }

            def set_params(params):
                for key, val in params.items():
                    attr_name = BAYESIAN_TO_COEFFICIENT.get(key)
                    if attr_name:
                        setattr(self.main, attr_name, val)

            def evaluate_train(**params):
                set_params(params)
                return self.main._optimization_backtest(self.train_start, self.train_end)

            def evaluate_valid(params):
                set_params(params)
                return self.main._optimization_backtest(self.valid_start, self.valid_end)

            total_stages = self.user_runs + (self.user_shrink_rounds if self.user_shrink_rounds > 0 else 0) + 1
            stage = 0

            optimization_results = []

            for run_num in range(self.user_runs):
                stage_text = f'宽搜索 ({run_num + 1}/{self.user_runs})'
                pct = int((stage + 1) * 90 / total_stages)
                self.progress_signal.emit({'stage': stage_text, 'pct': pct, 'log': f'第 {run_num + 1}/{self.user_runs} 次贝叶斯优化...'})

                optimizer = BayesianOptimization(
                    f=evaluate_train, pbounds=pbounds, verbose=2,
                    random_state=42 + run_num,
                )
                optimizer.maximize(init_points=self.user_init_points, n_iter=self.user_n_iter)

                train_score = optimizer.max['target']
                current_params = optimizer.max['params']
                optimization_results.append((train_score, current_params))
                self.log(f'第 {run_num + 1} 次宽搜索完成，训练得分: {train_score:.2f}')
                stage += 1

            optimization_results.sort(key=lambda x: x[0], reverse=True)
            best_train_score, best_params = optimization_results[0]
            original_bounds = pbounds.copy()
            self.log(f'宽搜索完成，最优训练得分: {best_train_score:.2f}')

            final_shrink_factor = None
            if self.user_shrink_rounds > 0:
                for shrink_round in range(self.user_shrink_rounds):
                    stage_text = f'区间收缩 ({shrink_round + 1}/{self.user_shrink_rounds})'
                    pct = int((stage + 1) * 90 / total_stages)
                    self.progress_signal.emit({'stage': stage_text, 'pct': pct, 'log': f'区间收缩第 {shrink_round + 1} 轮...'})

                    factor = SHRINK_FACTOR ** (shrink_round + 1)
                    final_shrink_factor = factor
                    shrank_bounds = {}
                    for param_name, (orig_low, orig_high) in original_bounds.items():
                        if orig_low > orig_high:
                            orig_low, orig_high = orig_high, orig_low
                        best_val = best_params[param_name]
                        orig_range = orig_high - orig_low
                        half_range = orig_range * factor / 2
                        min_range = orig_range * SHRINK_MIN_RATIO / 2
                        half_range = max(half_range, min_range)
                        new_low = max(orig_low, best_val - half_range)
                        new_high = min(orig_high, best_val + half_range)
                        shrank_bounds[param_name] = (new_low, new_high)

                    shrink_opt = BayesianOptimization(
                        f=evaluate_train, pbounds=shrank_bounds, verbose=2,
                        random_state=100 + shrink_round,
                    )
                    shrink_opt.maximize(init_points=max(5, self.user_init_points // 2), n_iter=max(10, self.user_n_iter // 2))

                    shrink_score = shrink_opt.max['target']
                    shrink_params = shrink_opt.max['params']
                    self.log(f'区间收缩第 {shrink_round + 1} 轮 训练得分: {shrink_score:.2f}')

                    if shrink_score > best_train_score:
                        best_train_score = shrink_score
                        best_params = shrink_params
                        optimization_results.append((shrink_score, shrink_params))
                        self.log('发现更优参数!')
                    stage += 1

            self.progress_signal.emit({'stage': '验证中', 'pct': 92, 'log': '正在验证最优参数...'})

            valid_results = []
            for i, (train_score, params) in enumerate(optimization_results):
                valid_score = evaluate_valid(params)
                valid_results.append((valid_score, params, train_score))
                self.log(f'第 {i + 1} 组 验证得分: {valid_score:.2f}')

            valid_results.sort(key=lambda x: (x[0], x[2]), reverse=True)
            best_valid_score, best_params, best_train_score = valid_results[0]
            best_params = {k: round(v, 4) for k, v in best_params.items()}

            # === 后处理：EMA平滑 → 正则约束 → 稳定性校验 → 重优化 ===
            chrono_params = [params for _, params in optimization_results]
            ema_log_header = False

            if len(chrono_params) >= 2:
                self.log('')
                self.log('=' * 70)
                self.log(f'[后处理] EMA系数平滑处理 (α={self.ema_alpha}) — 消除随机波动/采样噪声/局部极值跳变')
                self.log('=' * 70)
                ema_smoothed = chrono_params[0].copy()
                for i in range(1, len(chrono_params)):
                    for k in ema_smoothed:
                        ema_smoothed[k] = self.ema_alpha * chrono_params[i][k] + (1 - self.ema_alpha) * ema_smoothed[k]
                for k in sorted(best_params.keys()):
                    raw = best_params[k]
                    smooth = round(ema_smoothed[k], 4)
                    self.log(f'  EMA({k}): {raw:.4f} → {smooth:.4f}    delta={smooth - raw:+.4f}')
                self.log('  → 以EMA平滑值替代原始最优参数')
                for k in best_params:
                    best_params[k] = round(ema_smoothed[k], 4)

            self.log('')
            self.log('=' * 70)
            linear_count = sum(1 for k in best_params if k in self.linear_param_set)
            nonlinear_count = len(best_params) - linear_count
            self.log(f'[正则化] 线性参数{linear_count}个 → L2约束 / 非线性参数{nonlinear_count}个 → 弹性网约束')
            self.log('=' * 70)
            lambda_ridge = L2_LAMBDA
            lambda_lasso = L2_LAMBDA * 0.5
            for k in sorted(best_params.keys()):
                orig = best_params[k]
                if k in self.linear_param_set:
                    penalty = 1 - lambda_ridge
                    best_params[k] = round(orig * penalty, 4)
                    self.log(f'  [L2] {k}: {orig:.4f} × {penalty:.4f} = {best_params[k]:.4f}')
                else:
                    penalty = 1 - (lambda_ridge + lambda_lasso)
                    best_params[k] = round(orig * penalty, 4)
                    self.log(f'  [EN] {k}: {orig:.4f} × {penalty:.4f} = {best_params[k]:.4f}')

            self.log('')
            self.log('=' * 70)
            self.log('[稳定性] 连续3轮参数波动检测 (CV < 5% 为合格)')
            self.log('=' * 70)
            stable_flag = True
            unstable_params = []
            if len(chrono_params) >= 3:
                last3 = chrono_params[-3:]
                for k in sorted(best_params.keys()):
                    vals = [p[k] for p in last3]
                    mean_v = sum(vals) / len(vals)
                    if mean_v == 0:
                        cv = 0.0
                    else:
                        std_v = math.sqrt(sum((v - mean_v) ** 2 for v in vals) / len(vals))
                        cv = std_v / abs(mean_v)
                    ok = cv < 0.05
                    if not ok:
                        stable_flag = False
                        unstable_params.append(k)
                    self.log(f'  {"✓" if ok else "✗"} {k}: mean={mean_v:.4f}, std={std_v:.4f}, CV={cv:.4f}')
            else:
                self.log('  迭代次数不足3轮，跳过稳定性检测')

            if not stable_flag and len(chrono_params) >= 3:
                self.log('')
                self.log('=' * 70)
                self.log('[重优化] 参数波动过大，执行小幅贝叶斯寻优+管控流程')
                self.log('=' * 70)
                self.log(f'  不稳定参数: {", ".join(unstable_params)}')
                try:
                    reopt_lo = {}
                    reopt_hi = {}
                    for k in best_params.keys():
                        lo, hi = BAYESIAN_BOUNDS[k]
                        center = best_params[k]
                        half_range = (hi - lo) * 0.08
                        reopt_lo[k] = max(lo, center - half_range)
                        reopt_hi[k] = min(hi, center + half_range)
                    reopt_bounds = {k: (reopt_lo[k], reopt_hi[k]) for k in best_params}

                    self.log('  重优化搜索范围:')
                    for k in sorted(reopt_bounds.keys()):
                        self.log(f'    {k}: [{reopt_lo[k]:.4f}, {reopt_hi[k]:.4f}]')

                    reopt = BayesianOptimization(
                        f=evaluate_train, pbounds=reopt_bounds, verbose=0,
                        random_state=999,
                    )
                    reopt.maximize(init_points=8, n_iter=20)
                    reopt_score = reopt.max['target']
                    reopt_params = reopt.max['params']
                    self.log(f'  重优化训练得分: {reopt_score:.2f}')

                    reopt_valid = evaluate_valid(reopt_params)
                    self.log(f'  重优化验证得分: {reopt_valid:.2f}')

                    combined_ratio = 0.6
                    if reopt_valid * combined_ratio + reopt_score * (1 - combined_ratio) > \
                       best_valid_score * combined_ratio + best_train_score * (1 - combined_ratio):
                        self.log('  → 重优化参数更优，采纳重优化结果')
                        for k in best_params:
                            best_params[k] = round(reopt_params[k], 4)
                        best_valid_score = reopt_valid
                        best_train_score = reopt_score
                    else:
                        self.log('  → 重优化参数未超越当前最优，保留原始参数')
                except Exception as reopt_e:
                    self.log(f'  重优化失败: {reopt_e}')

            # 重新验证最终参数（平滑+正则已改变参数值）
            self.log('')
            self.log('─' * 70)
            self.log('[验证] 对后处理最终参数重新验证')
            self.log('─' * 70)
            set_params(best_params)
            final_valid = self.main._optimization_backtest(self.valid_start, self.valid_end)
            final_train = self.main._optimization_backtest(self.train_start, self.train_end)
            self.log(f'  最终训练得分: {final_train:.2f}  (原: {best_train_score:.2f})')
            self.log(f'  最终验证得分: {final_valid:.2f}  (原: {best_valid_score:.2f})')
            best_train_score = round(final_train, 2)
            best_valid_score = round(final_valid, 2)

            self.log('')
            self.log('─' * 70)
            self.log('[最终] 后处理完成，最终参数如下:')
            for k, v in best_params.items():
                self.log(f'  {k} = {v}')

            self.progress_signal.emit({'stage': '更新配置', 'pct': 97, 'log': '正在更新配置文件...'})

            for key, val in best_params.items():
                attr_name = BAYESIAN_TO_COEFFICIENT.get(key)
                if attr_name:
                    setattr(self.main, attr_name, val)

            try:
                update_factor = final_shrink_factor if final_shrink_factor is not None else 0.5
                new_bounds = {}
                for param_name, (orig_low, orig_high) in original_bounds.items():
                    best_val = best_params[param_name]
                    orig_range = orig_high - orig_low
                    half_range = orig_range * update_factor / 2
                    min_range = orig_range * SHRINK_MIN_RATIO / 2
                    half_range = max(half_range, min_range)
                    new_low = max(orig_low, best_val - half_range)
                    new_high = min(orig_high, best_val + half_range)
                    new_bounds[param_name] = (new_low, new_high)

                config_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'config.py')
                with open(config_path, 'r', encoding='utf-8') as f:
                    content = f.read()
                bounds_lines = []
                for param_name in BAYESIAN_BOUNDS:
                    lo, hi = new_bounds[param_name]
                    if lo > hi:
                        lo, hi = hi, lo  # 确保下界 <= 上界
                    comment = {
                        'stock_weight_first': '第一个股票的权重',
                        'stock_weight_last': '最后一个股票的权重',
                        'board_weight': '每板的权重',
                        'rushing_weight': '竞价抢筹权重(废弃)',
                        'rush_pct_coefficient': '涨幅系数 b',
                        'rush_letter_attr_weight': '抢筹字母属性系数',
                        'rush_region_attr_weight': '抢筹地区属性系数',
                        'rush_market_cap_coefficient': '抢筹市值系数',
                        'yz_overall_weight': '一字板整体系数',
                        'letter_attr_weight': '字母属性系数',
                        'region_attr_weight': '地区属性系数',
                        'attr_count_weight': '属性数量差距系数',
                        'negative_attr_count_weight': '负反馈属性数量差距系数',
                        'negative_attr_weight': '负反馈其他属性系数',
                        'negative_letter_attr_weight': '负反馈字母属性系数',
                        'negative_region_attr_weight': '负反馈地区属性系数',
                        'board_press_weight': '同板压制系数',
                        'node_guide_weight': '节点指引系数',
                        'holder_ratio_weight': '股东持股比例权重系数',
                    }.get(param_name, '')
                    bounds_lines.append(f"    '{param_name}': ({lo:.4f}, {hi:.4f}),  # {comment}")
                new_bounds_section = "# 贝叶斯优化参数搜索范围\nBAYESIAN_BOUNDS = {\n" + "\n".join(bounds_lines) + "\n}\n"
                import re
                content = re.sub(
                    r'# 贝叶斯优化参数搜索范围\nBAYESIAN_BOUNDS = \{.*?\n\}',
                    new_bounds_section.rstrip(),
                    content, flags=re.DOTALL
                )
                with open(config_path, 'w', encoding='utf-8') as f:
                    f.write(content)
                self.log('参数搜索范围已更新到配置文件')
            except Exception as e:
                self.log(f'更新配置文件失败: {e}')

            import json
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            try:
                from datetime import datetime
                cursor.execute('''
                    INSERT INTO bayesian_results (date, train_score, valid_score, params_json, train_start, train_end, valid_start, valid_end)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ''', (datetime.now().strftime('%Y-%m-%d'), best_train_score, best_valid_score, json.dumps(best_params),
                      self.train_start, self.train_end, self.valid_start, self.valid_end))
                conn.commit()
            finally:
                conn.close()

            self.finished_signal.emit({
                'valid_score': best_valid_score,
                'train_score': best_train_score,
                'params': best_params,
                'logs': '\n'.join(self.logs),
            })

        except Exception as e:
            for key, value in self.original_coeffs.items():
                setattr(self.main, key, value)
            self.error_signal.emit(str(e))
            import traceback
            traceback.print_exc()


class CorrelationAnalysisThread(QThread):
    """相关性分析工作线程"""
    progress_signal = pyqtSignal(int)
    finished_signal = pyqtSignal(dict)
    error_signal = pyqtSignal(str)

    def __init__(self, parent, db_path, start_str, end_str):
        super().__init__(parent)
        self.main = parent
        self.db_path = db_path
        self.start_str = start_str
        self.end_str = end_str

    @staticmethod
    def _calc_pearson(x, y):
        n = len(x)
        sum_x = sum(x)
        sum_y = sum(y)
        sum_xy = sum(xi * yi for xi, yi in zip(x, y))
        sum_x2 = sum(xi * xi for xi in x)
        sum_y2 = sum(yi * yi for yi in y)
        numerator = n * sum_xy - sum_x * sum_y
        var_x = max(0.0, n * sum_x2 - sum_x * sum_x)
        var_y = max(0.0, n * sum_y2 - sum_y * sum_y)
        denominator = math.sqrt(var_x * var_y)
        return numerator / denominator if denominator != 0 else 0.0

    @staticmethod
    def _calc_mutual_info(x, y):
        n = len(x)
        bins = max(5, int(math.sqrt(n) * 0.8))
        min_x, max_x = min(x), max(x)
        min_y, max_y = min(y), max(y)
        if max_x == min_x or max_y == min_y:
            return 0.0

        bin_w_x = (max_x - min_x) / bins
        bin_w_y = (max_y - min_y) / bins

        def discretize(vals, min_v, bin_w):
            res = []
            for v in vals:
                idx = int((v - min_v) / bin_w)
                if idx >= bins:
                    idx = bins - 1
                res.append(idx)
            return res

        x_idx = discretize(x, min_x, bin_w_x)
        y_idx = discretize(y, min_y, bin_w_y)

        p_x = [0.0] * bins
        p_y = [0.0] * bins
        p_xy = [[0.0] * bins for _ in range(bins)]

        for i in range(n):
            p_x[x_idx[i]] += 1.0
            p_y[y_idx[i]] += 1.0
            p_xy[x_idx[i]][y_idx[i]] += 1.0

        p_x = [v / n for v in p_x]
        p_y = [v / n for v in p_y]
        for i in range(bins):
            for j in range(bins):
                p_xy[i][j] /= n

        def entropy(p):
            return -sum(pi * math.log2(pi) for pi in p if pi > 0)

        h_x = entropy(p_x)
        h_y = entropy(p_y)
        h_xy = entropy([p_xy[i][j] for i in range(bins) for j in range(bins)])

        return max(0.0, h_x + h_y - h_xy)

    @staticmethod
    def _calc_linear_regression(x, y):
        n = len(x)
        sum_x = sum(x)
        sum_y = sum(y)
        sum_xy = sum(xi * yi for xi, yi in zip(x, y))
        sum_x2 = sum(xi * xi for xi in x)
        slope = (n * sum_xy - sum_x * sum_y) / (n * sum_x2 - sum_x * sum_x) if (n * sum_x2 - sum_x * sum_x) != 0 else 0.0
        intercept = (sum_y - slope * sum_x) / n
        ss_res = sum((yi - (slope * xi + intercept)) ** 2 for xi, yi in zip(x, y))
        ss_tot = sum((yi - sum_y / n) ** 2 for yi in y)
        r_squared = 1.0 - ss_res / ss_tot if ss_tot != 0 else 0.0
        return slope, intercept, r_squared

    def run(self):
        try:
            param_keys = [
                'stock_weight_first', 'stock_weight_last', 'board_weight',
                'rush_pct_coefficient', 'rush_letter_attr_weight', 'rush_region_attr_weight', 'rush_market_cap_coefficient', 'yz_overall_weight', 'letter_attr_weight',
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

            orig_params = {}
            for key in param_keys:
                orig_params[key] = getattr(self.main, BAYESIAN_TO_COEFFICIENT[key])

            n_samples = 200
            all_params = []
            all_scores = []

            for i in range(n_samples):
                sample = {}
                for key in param_keys:
                    lo, hi = BAYESIAN_BOUNDS[key]
                    sample[key] = random.uniform(lo, hi)
                    setattr(self.main, BAYESIAN_TO_COEFFICIENT[key], sample[key])
                all_params.append(sample)
                score = self.main._optimization_backtest(self.start_str, self.end_str)
                all_scores.append(score)
                self.progress_signal.emit(int((i + 1) / n_samples * 100))

            for key, val in orig_params.items():
                setattr(self.main, BAYESIAN_TO_COEFFICIENT[key], val)

            results = []
            for key in param_keys:
                x = [s[key] for s in all_params]
                y = all_scores

                r = self._calc_pearson(x, y)
                mi = self._calc_mutual_info(x, y)
                slope, intercept, r_squared = self._calc_linear_regression(x, y)
                abs_r = abs(r)

                if abs_r >= 0.5 and mi >= 0.3:
                    if r_squared >= 0.25:
                        judgment = '线性相关'
                    else:
                        judgment = '线性相关(拟合存疑)'
                elif abs_r >= 0.3 and mi >= 0.3:
                    judgment = '弱线性+非线性'
                elif abs_r < 0.3 and mi >= 0.3:
                    if r_squared >= 0.2:
                        judgment = '非线性(含线性趋势)'
                    else:
                        judgment = '非线性相关'
                elif abs_r >= 0.3 and mi < 0.3:
                    if r_squared >= 0.1:
                        judgment = '弱线性相关'
                    else:
                        judgment = '弱线性(拟合存疑)'
                else:
                    judgment = '弱相关'

                results.append((key, display_names[key], r, abs_r, mi, slope, r_squared, judgment))

            self.finished_signal.emit({'results': results})
        except Exception as e:
            self.error_signal.emit(str(e))
            import traceback
            traceback.print_exc()
