"""
验证通达信本地数据文件读取 v2
测试 pytdx.reader 对各种本地文件格式的解析能力
"""
from __future__ import annotations

import struct
import sys
from pathlib import Path

import pandas as pd

sys.stdout.reconfigure(encoding='utf-8')

TDX = Path(r"D:\new_tdx")

# =========================================================================
# 1. 日K线数据 (.day 文件)
# =========================================================================
print("=" * 60)
print("1. 日K线数据 (.day 文件)")
print("=" * 60)

from pytdx.reader import TdxDailyBarReader

daily = TdxDailyBarReader()

tests = [
    (TDX / "vipdoc/sh/lday/sh600000.day", "600000.SH", "浦发银行"),
    (TDX / "vipdoc/sh/lday/sh600519.day", "600519.SH", "贵州茅台"),
    (TDX / "vipdoc/sz/lday/sz000001.day", "000001.SZ", "平安银行"),
    (TDX / "vipdoc/sz/lday/sz000651.day", "000651.SZ", "格力电器"),
    (TDX / "vipdoc/sh/lday/sh000001.day", "000001.SH", "上证指数"),
    (TDX / "vipdoc/sz/lday/sz399006.day", "399006.SZ", "创业板指"),
]

for path, sym, name in tests:
    if not path.exists():
        print(f"  [SKIP] {sym} ({name}): 文件不存在")
        continue
    try:
        df = daily.get_df(str(path))
        print(f"\n  [OK] {sym} ({name}): {len(df)} 条记录")
        print(f"  columns: {list(df.columns)}")
        print(f"  index name: {df.index.name}, index dtype: {df.index.dtype}")
        print(f"  日期范围: {df.index[0]} ~ {df.index[-1]}")
        print(f"  最近3行:")
        print(df.tail(3).to_string())
        # 确认无换手率
        has_turnover = any('turn' in str(c).lower() for c in df.columns)
        print(f"  包含换手率? {has_turnover}")
    except Exception as e:
        print(f"  [FAIL] {sym} ({name}): {e}")

# =========================================================================
# 2. 财务数据 (vipdoc/cw/*.dat)
# =========================================================================
print("\n" + "=" * 60)
print("2. 财务数据 (vipdoc/cw/*.dat) - HistoryFinancialReader")
print("=" * 60)

from pytdx.reader import HistoryFinancialReader

fin = HistoryFinancialReader()

cw_dir = TDX / "vipdoc/cw"
cw_files = sorted(cw_dir.glob("*.dat")) if cw_dir.exists() else []
print(f"  文件总数: {len(cw_files)}")

# 按市场分类
for prefix, mkt_name in [("gpsh", "上海"), ("gpsz", "深圳"), ("gpbj", "北京")]:
    cnt = sum(1 for f in cw_files if f.name.startswith(prefix))
    print(f"  {mkt_name}: {cnt} 个")

# 找一个上海/深圳的文件测试 (北京可能数据少)
test_files = [f for f in cw_files if f.name.startswith(("gpsh", "gpsz"))]
if test_files:
    test_cw = test_files[0]
    print(f"\n  测试文件: {test_cw.name} ({test_cw.stat().st_size} bytes)")

    try:
        df_fin = fin.get_df(str(test_cw))
        print(f"  [OK] 解析成功: {df_fin.shape[0]} 行, {df_fin.shape[1]} 列")
        print(f"  列名: {list(df_fin.columns)}")
        print(f"\n  前2行 (前10列):")
        print(df_fin.iloc[:2, :10].to_string())
    except Exception as e:
        print(f"  [FAIL] HistoryFinancialReader: {e}")
        import traceback
        traceback.print_exc()
else:
    print("  [WARN] 未找到上海/深圳的财务数据文件")

# =========================================================================
# 3. 手动解析 vipdoc/cw/*.dat 格式
# =========================================================================
print("\n" + "=" * 60)
print("3. 手动分析 cw/*.dat 文件格式")
print("=" * 60)

if cw_files:
    # 找中芯国际的财务数据
    smic_files = [f for f in cw_files if '688981' in f.name or '00981' in f.name]
    if not smic_files:
        # 找一个上海主板的
        smic_files = [f for f in cw_files if f.name.startswith('gpsh')]

    if smic_files:
        fpath = smic_files[0]
        print(f"  分析文件: {fpath.name} ({fpath.stat().st_size} bytes)")
        with open(fpath, 'rb') as f:
            data = f.read()

        # TDX 财务数据格式:
        # 每条记录以 2 字节长度字段开始，然后是数据负载
        # 记录类型由负载的第一个字节标识
        offset = 0
        record_count = 0
        while offset < min(len(data), 500):
            if offset + 2 > len(data):
                break
            rec_len = struct.unpack_from('<H', data, offset)[0]
            print(f"    offset={offset}: rec_len={rec_len}", end="")
            if rec_len > 0 and offset + 2 + rec_len <= len(data):
                payload = data[offset+2:offset+2+min(rec_len, 60)]
                # Show first few bytes of payload
                print(f"  head={payload[:16].hex()}")
                record_count += 1
            else:
                print(f"  (too large or eof)")
                break
            offset += 2 + rec_len
        print(f"  前 {record_count} 条记录分析完毕")

# =========================================================================
# 4. 股本变迁数据 (gbbq)
# =========================================================================
print("\n" + "=" * 60)
print("4. 股本变迁数据 - GbbqReader")
print("=" * 60)

from pytdx.reader import GbbqReader

gbbq = GbbqReader()
# gbbq 文件通常在 vipdoc 下
gbbq_dirs = [
    TDX / "vipdoc",
    TDX / "T0002",
]
for d in gbbq_dirs:
    for pattern in ["gbbq*.dat", "*.gbbq"]:
        for f in d.glob(pattern):
            print(f"  发现: {f}")

# =========================================================================
# 5. 板块数据
# =========================================================================
print("\n" + "=" * 60)
print("5. 板块数据 - BlockReader")
print("=" * 60)

from pytdx.reader import BlockReader, CustomerBlockReader

# 检查 blocknew 目录
block_dir = TDX / "T0002" / "blocknew"
if block_dir.exists():
    blk_files = list(block_dir.glob("*.blk")) + list(block_dir.glob("*.dat"))
    print(f"  blocknew 文件数: {len(blk_files)}")
    for f in blk_files[:5]:
        print(f"    {f.name}")
    if blk_files:
        try:
            # BlockReader expects a .dat file with specific format
            reader = BlockReader()
            # Test if we can read any block file
            print(f"  BlockReader available")
        except Exception as e:
            print(f"  BlockReader init failed: {e}")

# =========================================================================
# 6. 分钟K线目录检查
# =========================================================================
print("\n" + "=" * 60)
print("6. 分钟K线和其他目录检查")
print("=" * 60)

for sub in ["sh/minline", "sz/minline", "bj/minline", "ds/minline",
            "sh/fzline", "sz/fzline", "bj/fzline",
            "sh/eday", "sz/eday", "bj/eday"]:
    p = TDX / "vipdoc" / sub
    if p.exists():
        files = list(p.iterdir())
        if files:
            print(f"  vipdoc/{sub}: {len(files)} files, e.g. {files[0].name}")
        else:
            print(f"  vipdoc/{sub}: exists but empty")
    else:
        print(f"  vipdoc/{sub}: NOT EXISTS")

# =========================================================================
# 7. 总结
# =========================================================================
print("\n" + "=" * 60)
print("7. 数据可用性总结")
print("=" * 60)
print("""
  本地通达信可读取的数据:
  - 日K线 (.day):      pytdx TdxDailyBarReader -> 已验证可用
                        字段: open, high, low, close, amount, volume
                        date 作为 index
                        缺: 换手率(turnover_rate)

  - 财务数据 (cw/*.dat): pytdx HistoryFinancialReader -> 待确认

  - 分钟K线 (.lc5):     本地无数据 (minline 目录为空)

  - 复权数据:           本地无数据 (fzline 目录为空)
""")

# =========================================================================
# 8. 测试 HistoryFinancialReader 正确用法
# =========================================================================
print("=" * 60)
print("8. HistoryFinancialReader 深入测试")
print("=" * 60)

# 找一个上海主板的 .dat 文件
sh_cw_files = [f for f in cw_files if f.name.startswith('gpsh')]
if sh_cw_files:
    test_f = sh_cw_files[0]
    print(f"  文件: {test_f.name}")

    # 先看看原始字节
    with open(test_f, 'rb') as f:
        raw = f.read()
    print(f"  大小: {len(raw)} bytes")

    # 尝试用 HistoryFinancialReader 的不同方式解析
    # 查看 HistoryFinancialReader 的源码
    import inspect
    print(f"\n  HistoryFinancialReader 源码:")
    print(inspect.getsource(HistoryFinancialReader.get_df))

print("\n验证完成!")
