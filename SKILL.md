---
name: sec-finance
description: Fetch US-listed Chinese company financials from SEC EDGAR — XBRL data, 6-K filings, 20-F annual reports. Use for quarterly/annual financial analysis of Chinese stocks (JD, Alibaba, Baidu, Tencent, etc.) when IR websites are blocked by Cloudflare.
---

# SEC Finance Fetcher

通过 SEC XBRL API 获取中概股财务数据，适用于 IR 网站被 Cloudflare 拦截的情况。

**核心脚本:** `scripts/sec_finance.py` — 开箱即用的命令行工具，支持公司名搜索、CIK 查询、季度/年度筛选、JSON/表格输出。

---

## 快速开始

```bash
# 搜索公司（自动查 CIK）
python3 scripts/sec_finance.py --search "JD.com"

# 直接用 CIK 查询（季度数据）
python3 scripts/sec_finance.py --cik 0001549802 --period quarterly

# 年度数据，JSON 输出
python3 scripts/sec_finance.py --cik 0001549802 --period annual --output json
```

---

## 常见中概股 CIK（内置）

| 公司 | CIK | Ticker | Exchange |
|------|-----|--------|----------|
| JD.com | 0001549802 | JD | NASDAQ |
| Alibaba | 0001577552 | BABA | NYSE |
| Baidu | 0001329099 | BIDU | NASDAQ |
| PDD Holdings | 0001738036 | PDD | NASDAQ |
| NetEase | 0001068008 | NTES | NASDAQ |
| Tencent | 0001794714 | TCEHY | OTC |
| NIO | 0001737649 | NIO | NYSE |
| Li Auto | 0001811527 | LI | NASDAQ |
| XPeng | 0001819779 | XPEV | NYSE |
| Bilibili | 0001691536 | BILI | NASDAQ |
| Trip.com | 0001262517 | TCOM | NASDAQ |
| Weibo | 0001522556 | WB | NASDAQ |
| Full Truck Alliance | 0001836308 | YMM | NYSE |
| KE Holdings | 0001823415 | BEKE | NYSE |

---

## 工具对比

| 来源 | 适用场景 | 难度 | 稳定性 |
|------|---------|------|--------|
| **SEC XBRL API** | 结构性财务数据（收入、净利润、EPS）| 简单 | ⭐⭐⭐⭐⭐ |
| **SEC EDGAR API** | 查 filing 列表、下载 6-K/20-F 原文 | 中等 | ⭐⭐⭐⭐ |
| **urllib 直接下载** | 静态 PDF 文件（IR 网站年报 PDF）| 简单 | ⭐⭐⭐ |

---

## XBRL 概念映射（中概股重点字段）

| 财务指标 | GAAP 概念 | IFRS 概念 |
|----------|-----------|-----------|
| 收入 | `Revenues` | `RevenueFromContractWithCustomerExcludingAssessedTax` |
| 净利润 | `NetIncomeLoss` | `ProfitLoss` |
| EPS | `EarningsPerShareBasicAndDiluted` | — |

中概股多用 IFRS（香港/开曼注册），优先查 `ifrs-full` 再 fallback 到 `us-gaap`。

---

## 核心 API

### SEC XBRL API（推荐，最稳定）

```python
import urllib.request, ssl, json

ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE

cik = "0001549802"  # JD.com
req = urllib.request.Request(
    f'https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json',
    headers={
        'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)',
        'Accept': 'application/json'
    }
)
with urllib.request.urlopen(req, timeout=15, context=ctx) as resp:
    data = json.loads(resp.read())
    revenues = data['facts']['us-gaap']['Revenues']['recent']
    print(revenues)
```

### SEC EDGAR CIK 搜索

```python
import urllib.request, ssl, re

ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE

req = urllib.request.Request(
    'https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&company=JD.com&type=13F&dateb=&owner=include&count=10',
    headers={'User-Agent': 'Mozilla/5.0'}
)
with urllib.request.urlopen(req, timeout=15, context=ctx) as resp:
    html = resp.read().decode('utf-8', errors='replace')
    ciks = re.findall(r'CIK=(\d+)', html)
    print(ciks)
```

### 下载 IR 网站静态 PDF（Cloudflare 绕过）

```python
import urllib.request, ssl

ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE

req = urllib.request.Request(
    'https://ir.jd.com/static-files/a8463094-68bf-40ad-9185-ed9f16ce564e',
    headers={
        'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36',
        'Referer': 'https://ir.jd.com/',
        'Accept': '*/*',
    }
)
with urllib.request.urlopen(req, timeout=20, context=ctx) as resp:
    data = resp.read()
    with open('annual_report.pdf', 'wb') as f:
        f.write(data)
    print(f"Downloaded {len(data)} bytes")
```

**关键：** 必须加 `Referer` 和正确的 `User-Agent`，否则 403。

---

## 判断规则

| 情况 | 方法 |
|------|------|
| 已知静态 PDF URL（IR 网站年报） | urllib + SSL 跳过验证 |
| 找公司 CIK + 查 filing 列表 | SEC EDGAR API (`efts.sec.gov`) |
| 拿结构化财务数据（营收/净利） | SEC XBRL API (`data.sec.gov`) |
| JS 动态渲染 + Cloudflare 保护 | SEC EDGAR 6-K 原件 |
| 直接搜索财务数据 | XBRL API 最快 |

---

## 已知限制

- SEC EDGAR API 在大量请求时可能 SSL 错误（重试即可）
- `efts.sec.gov` 查询返回有限结果（用 CIK 直接查更全）
- `data.sec.gov` XBRL API 最稳定，推荐优先使用
- PDF 下载需要正确 Referer，否则 403
- XBRL 数据可能有 1-2 天延迟，最新季度可能未收录
