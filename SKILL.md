---
name: sec-finance
description: Fetch US-listed Chinese company financials from SEC EDGAR — XBRL data, 6-K filings, 20-F annual reports. Use for quarterly/annual financial analysis of Chinese stocks (JD, Alibaba, Baidu, Tencent, etc.) when IR websites are blocked by Cloudflare.
---

# SEC Finance Fetcher

通过 SEC EDGAR 获取中概股财务数据，适用于 IR 网站被 Cloudflare 拦截的情况。

## 核心工具

| 来源 | 适用场景 | 难度 |
|------|---------|------|
| **SEC XBRL API** | 结构性财务数据（收入、净利润、EPS）| 简单 |
| **SEC EDGAR API** | 查 filing 列表、下载 6-K/20-F 原文 | 中等 |
| **urllib 直接下载** | 静态 PDF 文件（IR 网站年报 PDF）| 简单 |

## 1. SEC XBRL API（最可靠）

无需认证，直接返回结构化财务数据。

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
    # 找收入数据
    revenues = data['facts']['us-gaap']['Revenues']['recent']
    print(revenues)
```

CIK 查询：
```python
# 通过公司名搜索 CIK
req = urllib.request.Request(
    'https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&company=JD.com&type=13F&dateb=&owner=include&count=10',
    headers={'User-Agent': 'Mozilla/5.0'}
)
```

## 2. SEC EDGAR API

查询 filing 列表（6-K 季报、20-F 年报）：

```python
import urllib.request, ssl, json

ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE

# 搜索某公司的 6-K
query = 'JD.com quarterly results'
req = urllib.request.Request(
    f'https://efts.sec.gov/LATEST/search-index?q={query}&forms=6-K',
    headers={'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)'}
)
with urllib.request.urlopen(req, timeout=15, context=ctx) as resp:
    data = json.loads(resp.read())
    for h in data['hits']['hits']:
        s = h['_source']
        print(f"{s['file_date']} | {s['period_ending']} | {s.get('file_description','')}")
```

## 3. 下载 IR 网站静态 PDF

Cloudflare 保护的 IR 网站，年报 PDF（静态文件）可以直接下：

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

**关键：** 必须加 `Referer` 和正确的 `User-Agent`，否则被拦截。

## 判断规则

| 情况 | 方法 |
|------|------|
| 已知静态 PDF URL（IR 网站年报） | urllib + SSL 跳过验证 |
| 找公司 CIK + 查 filing 列表 | SEC EDGAR API (`efts.sec.gov`) |
| 拿结构化财务数据（营收/净利） | SEC XBRL API (`data.sec.gov`) |
| JS 动态渲染 + Cloudflare 保护 | SEC EDGAR 6-K 原件 |
| 直接搜索财务数据 | XBRL API 最快 |

## 常见中概股 CIK

| 公司 | 代码 | CIK |
|------|------|-----|
| JD.com | 9618.HK | 0001549802 |
| Alibaba | 9988.HK | 0001577552 |
| Baidu | 9888.HK | 0001329099 |
| Tencent | — | 0001794714 |
| PDD | PDD | 0001738036 |
| NetEase | NTES | 0001068008 |

## 已知限制

- SEC EDGAR API 在大量请求时可能 SSL 错误（重试即可）
- `efts.sec.gov` 查询返回有限结果（用 CIK 直接查更全）
- `data.sec.gov` XBRL API 最稳定，推荐优先使用
- PDF 下载需要正确 Referer，否则 403
