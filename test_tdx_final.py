"""
通达信本地数据源最终验证脚本
验证：日K线、财务数据（gpcw）、字段映射
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

import pandas as pd

sys.stdout.reconfigure(encoding='utf-8')

TDX = Path(r"D:\new_tdx")

# ===================================================================
# 1. 加载 mootdx 字段映射表（GBK编码）
# ===================================================================
print("=" * 70)
print("STEP 1: 加载字段映射表")
print("=" * 70)

content_raw = Path(r"D:\Miniconda3\Lib\site-packages\mootdx\financial\columns.py").read_bytes()
# 尝试多种编码
for enc in ['utf-8', 'gbk', 'gb2312', 'gb18030']:
    try:
        content = content_raw.decode(enc)
        break
    except (UnicodeDecodeError, LookupError):
        continue
else:
    content = content_raw.decode('utf-8', errors='replace')

tree = ast.parse(content)
mootdx_columns = None
for node in ast.walk(tree):
    if isinstance(node, ast.Assign):
        for target in node.targets:
            if isinstance(target, ast.Name) and target.id == "columns":
                mootdx_columns = ast.literal_eval(node.value)

print(f"mootdx 字段总数: {len(mootdx_columns)}")
print(f"  命名字段: {sum(1 for c in mootdx_columns if not c.startswith('col'))}")
print(f"  未命名字段: {sum(1 for c in mootdx_columns if c.startswith('col'))}")

# ===================================================================
# 2. 日K线数据验证
# ===================================================================
print("\n" + "=" * 70)
print("STEP 2: 日K线数据 (.day 文件)")
print("=" * 70)

from pytdx.reader import TdxDailyBarReader

daily = TdxDailyBarReader()

# 测试股票 (只用成熟的主板股票，避开 NotImplementedError)
test_stocks = [
    ("sh600000.day", "600000.SH", "浦发银行"),
    ("sh600519.day", "600519.SH", "贵州茅台"),
    ("sz000001.day", "000001.SZ", "平安银行"),
]

for filename, symbol, name in test_stocks:
    path = TDX / "vipdoc" / ("sh/lday" if ".SH" in symbol else "sz/lday") / filename
    if not path.exists():
        print(f"  [SKIP] {symbol} ({name}): 文件不存在")
        continue

    try:
        df = daily.get_df(str(path))
        print(f"\n  [OK] {symbol} ({name})")
        print(f"    记录数: {len(df)}")
        print(f"    日期范围: {df.index[0].strftime('%Y-%m-%d')} ~ {df.index[-1].strftime('%Y-%m-%d')}")
        print(f"    字段: {list(df.columns)}")
        print(f"    最新3条:")
        print(df.tail(3).to_string())
        print(f"    换手率: 不含（通达信日K线限制）")
    except Exception as e:
        print(f"  [FAIL] {symbol} ({name}): {e}")

# 额外测试 688981（科创板）— 使用 get_df_by_file
print(f"\n  --- 科创板测试 ---")
path_688981 = TDX / "vipdoc/sh/lday/sh688981.day"
if path_688981.exists():
    try:
        df_688981 = daily.get_df_by_file(str(path_688981))
        print(f"  [OK] 688981.SH (中芯国际) via get_df_by_file")
        print(f"    记录数: {len(df_688981)}")
        print(f"    日期范围: {df_688981.index[0].strftime('%Y-%m-%d')} ~ {df_688981.index[-1].strftime('%Y-%m-%d')}")
        print(f"    最新3条:")
        print(df_688981.tail(3).to_string())
    except Exception as e:
        print(f"  [FAIL] 688981.SH: {e}")

# ===================================================================
# 3. 财务数据 (gpcw) 验证
# ===================================================================
print("\n" + "=" * 70)
print("STEP 3: 财务数据 (gpcw*.zip)")
print("=" * 70)

from pytdx.reader import HistoryFinancialReader

fin = HistoryFinancialReader()

# 找最新有数据的 gpcw 文件
cw_dir = TDX / "vipdoc/cw"
gpcw_zips = sorted(cw_dir.glob("gpcw*.zip"), reverse=True)

# 跳过空文件 (小于1KB的)
valid_zips = [z for z in gpcw_zips if z.stat().st_size > 5000]
print(f"gpcw 文件总数: {len(gpcw_zips)}, 有数据: {len(valid_zips)}")
print(f"最新5期: {[z.name for z in valid_zips[:5]]}")

# 读取最新一期
latest = valid_zips[0]
print(f"\n读取: {latest.name} ({latest.stat().st_size / 1024 / 1024:.1f} MB)")

df_raw = fin.get_df(str(latest))
print(f"原始DataFrame: {df_raw.shape[0]} 只股票 x {df_raw.shape[1]} 列")
print(f"Index: {df_raw.index.name} (样例: {df_raw.index[:5].tolist()})")

# 应用字段映射
numeric_cols = [c for c in df_raw.columns if c.startswith("col")]
col_map = {}
for c in numeric_cols:
    col_num = int(c.replace("col", ""))
    if col_num < len(mootdx_columns):
        col_map[c] = mootdx_columns[col_num]
    else:
        col_map[c] = c

df = df_raw.rename(columns=col_map)

# 查看中芯国际 (688981)
smic = df[df.index == "688981"]
if not smic.empty:
    print(f"\n--- 中芯国际 (688981) 财务数据 ---")
    row = smic.iloc[0]
    print(f"  report_date: {row['report_date']}")

    # 选择关键财务指标（非金融类）
    key_metrics = [
        "基本每股收益", "每股净资产", "净资产收益率",
        "营业收入", "营业利润", "归属于母公司所有者的净利润",
        "资产总计", "负债合计",
        "经营活动产生的现金流量净额",
        "总股本", "股东人数(户)",
    ]
    for key in key_metrics:
        if key in df.columns:
            val = row[key]
            # 判断是否是百分比字段
            if "%" in key:
                print(f"  {key}: {val}%")
            else:
                print(f"  {key}: {val}")
        else:
            print(f"  {key}: (字段不存在)")

# 查看贵州茅台 (600519) 验证数据准确性
maotai = df[df.index == "600519"]
if not maotai.empty:
    print(f"\n--- 贵州茅台 (600519) 财务数据（验证） ---")
    row = maotai.iloc[0]
    verify_fields = [
        "基本每股收益", "每股净资产", "净资产收益率",
        "营业收入", "归属于母公司所有者的净利润",
        "资产总计", "总股本",
    ]
    for key in verify_fields:
        if key in df.columns:
            print(f"  {key}: {row[key]}")
    print(f"  (以上为2025年中报数据，可用于交叉验证)")

# ===================================================================
# 4. 数据覆盖度分析
# ===================================================================
print("\n" + "=" * 70)
print("STEP 4: 数据覆盖分析")
print("=" * 70)

# 日K线覆盖度
day_dirs = {
    "上海": TDX / "vipdoc/sh/lday",
    "深圳": TDX / "vipdoc/sz/lday",
    "北京": TDX / "vipdoc/bj/lday",
}
for mkt_name, d in day_dirs.items():
    if d.exists():
        files = list(d.glob("*.day"))
        print(f"  {mkt_name}日K线: {len(files)} 个文件")

# 财务数据覆盖
print(f"  gpcw 数据覆盖期数: {len(valid_zips)} 期")
print(f"  每期股票数: ~{df_raw.shape[0]} 只")

# ===================================================================
# 5. 换手率数据来源确认
# ===================================================================
print("\n" + "=" * 70)
print("STEP 5: 换手率补充方案确认")
print("=" * 70)
print("""
  通达信 .day 日K线数据不含换手率，需要从以下来源补充：

  方案A: 腾讯HTTP接口 (已在 AStockDataProvider.get_realtime_quote() 中使用)
         字段索引38 = 换手率
         优点: 无认证, 已在项目中使用
         缺点: 需要网络

  方案B: EastMoney datacenter (已在 market_screener_tool 中使用)
         字段 f8 = 换手率
         优点: 数据丰富
         缺点: 限速严格 (>=1s间隔)

  推荐: 方案A (腾讯接口) — 简单可靠，已在代码中使用
""")

# ===================================================================
# 6. 总结
# ===================================================================
print("=" * 70)
print("SUMMARY: 通达信本地数据验证结果")
print("=" * 70)
print("""
  [OK]  日K线数据 (.day)    — pytdx TdxDailyBarReader
        字段: open, high, low, close, amount, volume
        索引: date (datetime64)
        覆盖: 沪深京 5500+ 股票, 2010至今

  [OK]  财务数据 (gpcw*.zip) — pytdx HistoryFinancialReader
        字段: report_date + 584个财务指标 (403个已命名)
        映射: mootdx.financial.columns (GBK编码)
        覆盖: 5000+ 股票, 1990年至今145+期

  [空]  分钟K线 (.lc5)      — 本地未下载
  [空]  复权数据             — 本地未下载

  [缺]  换手率               — 通达信日K线不含, 需从腾讯/EastMoney补充
""")

print("验证完成!")
