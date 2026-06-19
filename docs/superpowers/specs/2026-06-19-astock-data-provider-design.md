# AStockDataProvider — a-stock-data 统一数据层接入设计

**日期**: 2026-06-19
**版本**: 1.0
**状态**: 待实施

---

## 1. 背景

Vibe-Trading 现有数据层只有 `DataLoaderProtocol` 定义的 OHLCV 行情接口（10 个 loader），
缺乏研报、新闻、公告、基础数据（F10/财报）、信号/资金面等 A 股特有数据的系统化接入能力。
开源项目 [a-stock-data](https://github.com/simonlin1212/a-stock-data) (Apache 2.0) 提供了
27 个实测可用端点，覆盖 7 层数据，零 API Key（除 iwencai），HTTP 直连。

## 2. 目标

在**不修改任何现有 Loader** 的前提下，新增平行 `DataProvider` 注册机制，将 a-stock-data
的 27 个端点封装为一个 `AStockDataProvider`，补全 Agent/MCP/Backtest 的非行情数据能力。

## 3. 架构设计

### 3.1 新目录结构

```
agent/backtest/data_providers/       ← 新增
├── __init__.py
├── base.py                          ← DataProviderProtocol 协议
├── registry.py                      ← @register_provider 装饰器 + 注册表
└── astock.py                        ← AStockDataProvider 主类

agent/backtest/loaders/              ← 现有（不改动）
├── ... (tushare/yfinance/akshare/... — 全部保留)
```

### 3.2 DataProviderProtocol

```python
@runtime_checkable
class DataProviderProtocol(Protocol):
    name: str
    version: str

    def is_available(self) -> bool: ...
    def check_prerequisites(self) -> list[str]: ...  # 返回缺失依赖列表
```

Provider 的方法分7组，按需调用，不做统一 fetch() 签名（数据形态各异）。

### 3.3 AStockDataProvider 端点清单（27个）

**行情层 (3):**
| 方法 | 来源 | 返回 |
|------|------|------|
| `get_kline(code, start, end, interval)` | mootdx TCP | DataFrame (open/high/low/close/volume) |
| `get_realtime_quote(code)` | tencent HTTP | dict (price/PE/PB/market_cap/turnover/limit_up/down) |
| `get_index_quote(codes)` | tencent HTTP | dict (指数/ETF行情) |

**研报层 (4):**
| 方法 | 来源 | 返回 |
|------|------|------|
| `get_research_reports(code, keyword, page, page_size)` | eastmoney reportapi | list[dict] (title/org/rating/date/PDF URL) |
| `download_report_pdf(url, save_path)` | eastmoney | bytes |
| `get_consensus_eps(code)` | 同花顺 THS | dict (年份→EPS) |
| `search_reports_nl(query)` | iwencai (需 Key) | list[dict] |

**信号层 (9):**
| 方法 | 来源 | 返回 |
|------|------|------|
| `get_strong_stocks()` | 同花顺 | DataFrame (强势股+题材归因) |
| `get_north_flow(market, date)` | 同花顺 | DataFrame (北向分钟资金) |
| `get_concept_blocks(code)` | eastmoney slist | list[dict] (板块归属) |
| `get_fund_flow_minute(code)` | eastmoney push2 | DataFrame (分钟资金流) |
| `get_dragon_tiger_stock(code)` | eastmoney datacenter | list[dict] (个股龙虎榜) |
| `get_dragon_tiger_market(date)` | eastmoney datacenter | DataFrame (全市场龙虎榜) |
| `get_unlock_calendar(code, days)` | eastmoney datacenter | list[dict] (解禁日历) |
| `get_sector_ranking()` | eastmoney | DataFrame (行业排名) |
| `get_theme_attribution()` | 同花顺 | list[dict] (题材归因) |

**资金面 (5):**
| 方法 | 来源 | 返回 |
|------|------|------|
| `get_margin_trading(code, start, end)` | eastmoney datacenter | list[dict] (融资融券) |
| `get_block_trades(code, start, end)` | eastmoney datacenter | list[dict] (大宗交易) |
| `get_shareholder_changes(code)` | eastmoney datacenter | list[dict] (股东户数) |
| `get_dividend_history(code)` | eastmoney datacenter | list[dict] (分红送转) |
| `get_fund_flow_120d(code)` | eastmoney push2his | DataFrame (120日资金流) |

**新闻层 (2):**
| 方法 | 来源 | 返回 |
|------|------|------|
| `get_stock_news(code, limit)` | eastmoney search | list[dict] (个股新闻) |
| `get_global_news(limit)` | eastmoney np-weblist | list[dict] (7×24快讯) |

**基础数据 (4):**
| 方法 | 来源 | 返回 |
|------|------|------|
| `get_financial_snapshot(code)` | mootdx finance | dict (37字段快照) |
| `get_company_f10(code, category)` | mootdx F10 | str/dict (9大类) |
| `get_stock_info(code)` | eastmoney push2 | dict (行业/股本/市值/上市日期) |
| `get_financial_statements(code, report_type)` | sina finance | DataFrame (资产负债表/利润表/现金流) |

**公告层 (1):**
| 方法 | 来源 | 返回 |
|------|------|------|
| `get_announcements(code, keyword, start, end, page)` | 巨潮 cninfo | list[dict] (公告全文/PDF) |

### 3.4 Provider 注册机制

```python
# agent/backtest/data_providers/registry.py
PROVIDER_REGISTRY: dict[str, Type[DataProviderProtocol]] = {}

def register_provider(cls):    # 装饰器，类似 @register
    PROVIDER_REGISTRY[cls.name] = cls
    return cls

def get_provider(name: str) -> DataProviderProtocol:
    """按名称获取 provider 实例"""

def list_providers() -> list[str]:
    """列出所有已注册 provider"""

def list_available_providers() -> list[str]:
    """列出所有可用（is_available()=True）的 provider"""
```

### 3.5 与 Loader 的关系（平行不交叉）

```
                    请求入口
                       │
          ┌────────────┼────────────┐
          ▼            ▼            ▼
      Agent Tool    MCP Tool    Backtest Engine
          │            │            │
     ┌────┴────┐  ┌───┴───┐   ┌───┴───┐
     │Loader   │  │Loader │   │Loader │  ← OHLCV 行情（现有，不改）
     │Registry │  │Registry│   │Registry│
     └─────────┘  └───────┘   └───────┘
          │
     ┌────┴────┐  ┌─────────┐ ┌──────────┐
     │Provider │  │Provider │ │Provider  │  ← 非行情数据（新增）
     │Registry │  │  (MCP)  │ │(Backtest)│
     └─────────┘  └─────────┘ └──────────┘
```

Loader → 行情 OHLCV → 已有 → 不动
Provider → 研报/新闻/公告/基础/信号/资金 → 新增 → 平行

## 4. 新增 Agent/MCP Tools

在 `agent/src/tools/` 下新增，按功能分组：

| Tool 名称 | 调用 Provider 方法 | 用途 |
|-----------|-------------------|------|
| `get_research_reports` | get_research_reports, search_reports_nl | 查研报 |
| `get_stock_news` | get_stock_news, get_global_news | 查新闻 |
| `get_announcements` | get_announcements | 查公告 |
| `get_stock_profile` | get_financial_snapshot, get_company_f10, get_stock_info | 查基本面 |
| `get_stock_financials` | get_financial_statements | 财报三表 |
| `get_market_signals` | get_strong_stocks, get_north_flow, get_dragon_tiger_*, get_sector_ranking | 市场信号 |
| `get_capital_flow` | get_margin_trading, get_block_trades, get_fund_flow_*, get_dividend_history | 资金面 |

MCP server (`mcp_server.py`) 同步注册这 7 个新工具。

## 5. 依赖变更

`agent/requirements.txt` 新增：

```
mootdx>=0.10
stockstats
```

`pyproject.toml` 的 `dependencies` 同步新增这两个包。

## 6. 配置方式

- **无需新 env 变量** — a-stock-data 所有端点（除 iwencai）零鉴权
- iwencai 语义搜索：可选 `IWENCAI_API_KEY`（已存在 `.env.example` 中无，新增为可选）
- 东财限流内置在 `em_get()` 中，无需配置

## 7. 不影响范围（明确保证）

- ❌ 不修改 `agent/backtest/loaders/` 下任何文件
- ❌ 不修改 Loader 注册表 / fallback chain
- ❌ 不修改 Backtest engine 核心逻辑
- ❌ 不修改现有 MCP 工具签名
- ✅ 所有现有测试应继续通过
- ✅ Provider 不可用时优雅降级（`is_available()` 返回 False）

## 8. 实施顺序

| 阶段 | 内容 | 文件 |
|------|------|------|
| 1 | DataProvider 基类 + 注册表 | `data_providers/base.py`, `registry.py` |
| 2 | AStockDataProvider 全 27 端点 | `data_providers/astock.py` |
| 3 | 依赖更新 | `requirements.txt`, `pyproject.toml` |
| 4 | Agent Tools（7个） | `src/tools/astock_*.py` |
| 5 | MCP 工具注册 | `mcp_server.py` |
| 6 | 集成测试 + 冒烟验证 | 验证脚本 |
