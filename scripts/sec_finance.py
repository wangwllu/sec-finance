#!/usr/bin/env python3
"""
sec_finance.py - SEC XBRL Financial Data Fetcher for US-Listed Chinese Companies

Fetches structured financial data (revenue, net income, EPS) from the SEC XBRL API.
Designed for Chinese companies listed on US exchanges (JD, Alibaba, Baidu, PDD, etc.)

Usage:
    python3 scripts/sec_finance.py --search "JD.com"
    python3 scripts/sec_finance.py --cik 0001549802
    python3 scripts/sec_finance.py --cik 0001549802 --period annual
    python3 scripts/sec_finance.py --cik 0001549802 --period quarterly --output json
"""

import argparse
import json
import re
import ssl
import sys
import textwrap
import time
import urllib.parse
import urllib.request
import urllib.error
from datetime import datetime
from typing import Any, Optional

# ─────────────────────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────────────────────

# Use a standard browser User-Agent — SEC API is sensitive to unusual UAs
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

XBRL_BASE = "https://data.sec.gov/api/xbrl"
EDGAR_BASE = "https://www.sec.gov/cgi-bin/browse-edgar"
EFTS_SEARCH = "https://efts.sec.gov/LATEST/search-index"

# Revenue concepts — priority order (IFRS-style first, then GAAP)
REVENUE_CONCEPTS = [
    "RevenueFromContractWithCustomerExcludingAssessedTax",  # IFRS / Chinese companies
    "SalesRevenueNet",                                      # US GAAP
    "SalesRevenueGoodsNet",                                 # US GAAP
    "SalesRevenueServicesNet",                              # US GAAP
    "Revenues",                                             # Generic fallback
]

NET_INCOME_CONCEPTS = [
    "NetIncomeLossAvailableToCommonStockholdersBasic",        # US GAAP — net income to common shareholders
    "NetIncomeLoss",                                          # US GAAP fallback
    "ProfitLoss",                                             # IFRS — pre-tax income
]

EPS_CONCEPTS = [
    "EarningsPerShareBasicAndDiluted",
    "BasicAndDilutedEarningsPerShare",
    "BasicEarningsPerShare",
    "DilutedEarningsPerShare",
]

# Common Chinese stocks — built-in CIK table
CHINESE_STOCKS = {
    "JD.com":           {"cik": "0001549802", "ticker": "JD",    "exchange": "NASDAQ"},
    "Alibaba":          {"cik": "0001577552", "ticker": "BABA",  "exchange": "NYSE"},
    "Baidu":            {"cik": "0001329099", "ticker": "BIDU", "exchange": "NASDAQ"},
    "PDD Holdings":     {"cik": "0001738036", "ticker": "PDD",  "exchange": "NASDAQ"},
    "NetEase":          {"cik": "0001068008", "ticker": "NTES",  "exchange": "NASDAQ"},
    "Tencent (TCEHY)":  {"cik": "0001794714", "ticker": "TCEHY","exchange": "OTC"},
    "NIO":              {"cik": "0001737649", "ticker": "NIO",   "exchange": "NYSE"},
    "Li Auto":          {"cik": "0001811527", "ticker": "LI",    "exchange": "NASDAQ"},
    "XPeng":            {"cik": "0001819779", "ticker": "XPEV",  "exchange": "NYSE"},
    "Bilibili":         {"cik": "0001691536", "ticker": "BILI",  "exchange": "NASDAQ"},
    "Trip.com":         {"cik": "0001262517", "ticker": "TCOM",  "exchange": "NASDAQ"},
    "Weibo":            {"cik": "0001522556", "ticker": "WB",    "exchange": "NASDAQ"},
    "Full Truck Alliance": {"cik": "0001836308", "ticker": "YMM", "exchange": "NYSE"},
    "KE Holdings":      {"cik": "0001823415", "ticker": "BEKE",  "exchange": "NYSE"},
}


# ─────────────────────────────────────────────────────────────────────────────
# HTTP helpers
# ─────────────────────────────────────────────────────────────────────────────

def _make_ctx() -> ssl.SSLContext:
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


def _get(url: str, timeout: int = 20, retries: int = 2) -> dict:
    """Fetch JSON from SEC API with retry on transient errors."""
    parsed = urllib.parse.urlparse(url)
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(
                url,
                headers={
                    "User-Agent": USER_AGENT,
                    "Accept": "application/json",
                    "Host": parsed.netloc,
                },
            )
            with urllib.request.urlopen(req, timeout=timeout, context=_make_ctx()) as resp:
                return json.loads(resp.read())
        except ssl.SSLEOFError as e:
            if attempt < retries:
                time.sleep(1.5 ** attempt)
                continue
            raise ConnectionError(f"SSL error (EOF) after {retries + 1} attempts: {url}") from e
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt < retries:
                # Rate limited — back off
                time.sleep(3 * (attempt + 1))
                continue
            if e.code == 404:
                raise ValueError(f"CIK or resource not found: {url}") from e
            raise ValueError(f"HTTP {e.code} fetching {url}: {e.reason}") from e
        except urllib.error.URLError as e:
            if attempt < retries:
                time.sleep(1.5 ** attempt)
                continue
            raise ConnectionError(f"Network error fetching {url}: {e.reason}") from e


# ─────────────────────────────────────────────────────────────────────────────
# CIK lookup
# ─────────────────────────────────────────────────────────────────────────────

def cik_from_name(company_name: str) -> Optional[dict]:
    """Search for a company by name. Checks built-in table first, then SEC EDGAR."""
    name_lower = company_name.strip().lower()

    # Fast path: check built-in table
    for stock_name, info in CHINESE_STOCKS.items():
        if name_lower == stock_name.lower() or name_lower in stock_name.lower():
            return {"name": stock_name, **info}

    # EDGAR HTML search
    encoded = urllib.parse.quote(company_name)
    url = f"{EDGAR_BASE}?action=getcompany&company={encoded}&type=13F&dateb=&owner=include&count=10"
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=15, context=_make_ctx()) as resp:
            html = resp.read().decode("utf-8", errors="replace")
    except Exception as e:
        raise ConnectionError(f"Failed to search SEC EDGAR: {e}")

    ciks = re.findall(r"CIK=(\d+)", html)
    if not ciks:
        raise ValueError(f"No SEC results found for company: {company_name}")

    # Deduplicate and pick first
    ciks = list(dict.fromkeys(ciks))
    cik = ciks[0].zfill(10)

    # Extract company name from HTML
    name_match = re.search(r"companyName>([^<]+)<", html)
    name = name_match.group(1).strip() if name_match else company_name

    return {"cik": cik, "name": name}


# ─────────────────────────────────────────────────────────────────────────────
# XBRL data fetching
# ─────────────────────────────────────────────────────────────────────────────

def fetch_company_facts(cik: str) -> dict:
    """Fetch all company facts from SEC XBRL API."""
    padded = cik.zfill(10)
    return _get(f"{XBRL_BASE}/companyfacts/CIK{padded}.json")


def fetch_financials(cik: str, period: str = "all", limit: int = 8) -> dict:
    """
    Fetch key financials for a CIK.

    Args:
        cik:        10-digit CIK
        period:     "quarterly", "annual", or "all"
        limit:      Max periods to return

    Returns:
        dict with metadata + list of period financials
    """
    facts = fetch_company_facts(cik)
    us_gaap = facts.get("facts", {}).get("us-gaap", {})
    ifrs    = facts.get("facts", {}).get("ifrs-full", {})

    # ── Extract concepts (try IFRS first for revenue, then GAAP) ────────────
    revenue_concepts = (
        [c for c in REVENUE_CONCEPTS if c in ifrs] +
        [c for c in REVENUE_CONCEPTS if c in us_gaap]
    )
    ni_concepts = (
        [c for c in NET_INCOME_CONCEPTS if c in ifrs] +
        [c for c in NET_INCOME_CONCEPTS if c in us_gaap]
    )
    eps_concepts = [c for c in EPS_CONCEPTS if c in us_gaap]

    revenue_entries = _extract_entries(ifrs, revenue_concepts) or \
                      _extract_entries(us_gaap, revenue_concepts)
    ni_entries      = _extract_entries(ifrs, ni_concepts) or \
                      _extract_entries(us_gaap, ni_concepts)
    eps_entries     = _extract_entries(us_gaap, eps_concepts)

    # ── Merge by period (end date + form) ────────────────────────────────────
    merged = _merge_by_period(revenue_entries, ni_entries, eps_entries)

    # ── Filter by period type ────────────────────────────────────────────────
    if period != "all":
        merged = [m for m in merged if m.get("period_type") == period]

    # ── Deduplicate: prefer 20-F/10-K annual, 10-Q/6-K quarterly ───────────
    merged = _deduplicate_periods(merged)

    return {
        "cik":              cik.zfill(10),
        "company_name":     facts.get("entityName", ""),
        "data_updated":     facts.get("dataLastUpdated", ""),
        "period_type":      period,
        "concepts_used": {
            "revenue":    revenue_concepts[0] if revenue_concepts else "N/A",
            "net_income": ni_concepts[0] if ni_concepts else "N/A",
            "eps":        eps_concepts[0] if eps_concepts else "N/A",
        },
        "financials": merged[:limit],
    }


def _extract_entries(facts: dict, concept_list: list) -> list:
    """
    Extract entries from fact dict.
    For each (end, form) pair, keeps the entry from the highest-priority concept
    (concept_list order), and within same concept, the latest 'filed' date.

    Returns list of {start, end, val, form, filed, period_type, currency, concept} dicts.
    The SEC XBRL API returns data as:
        facts[concept]['units'][currency] = [{start, end, val, form, filed, ...}, ...]
    """
    best: dict = {}  # (end, form) -> (concept_priority, entry)

    for prio, concept in enumerate(concept_list):
        if concept not in facts:
            continue
        concept_data = facts[concept]
        units = concept_data.get("units", {})
        if not units:
            continue

        # Prefer CNY for Chinese companies, then USD, then first available
        currency_order = ["CNY", "USD", "HKD", next(iter(units.keys()), None)]
        currency = next((c for c in currency_order if c in units), None)
        if not currency:
            continue

        for e in units[currency]:
            k = (e.get("end", ""), e.get("form", ""))
            existing = best.get(k)
            # Keep if: no existing, or lower concept priority, or same concept but later filed
            if existing is None:
                should_keep = True
            elif prio < existing[0]:
                should_keep = True
            elif prio == existing[0] and e.get("filed", "") > existing[1].get("filed", ""):
                should_keep = True
            else:
                should_keep = False

            if should_keep:
                best[k] = (prio, {
                    "start":       e.get("start", ""),
                    "end":          e.get("end", ""),
                    "val":          e.get("val"),
                    "form":         e.get("form", ""),
                    "filed":        e.get("filed", ""),
                    "currency":     currency,
                    "concept":      concept,
                    "period_type":  _classify_period(e.get("form", ""), e.get("start", ""), e.get("end", "")),
                })

    return [v[1] for v in best.values()]


def _classify_period(form: str, start: str, end: str) -> str:
    """Classify a filing as annual or quarterly."""
    form_upper = (form or "").upper()
    if "10-K" in form_upper or "20-F" in form_upper or "40-F" in form_upper:
        return "annual"
    if "10-Q" in form_upper or "6-K" in form_upper:
        return "quarterly"
    if start and end:
        try:
            s = datetime.strptime(start, "%Y-%m-%d")
            e = datetime.strptime(end, "%Y-%m-%d")
            days = (e - s).days
            return "annual" if 300 < days < 400 else "quarterly"
        except ValueError:
            pass
    return "unknown"


def _merge_by_period(rev_entries: list, ni_entries: list,
                     eps_entries: list) -> list:
    """Merge revenue, net income, EPS entries by (end, form) key.

    For NI, prefer more-specific concepts (NetIncomeLossAvailableToCommonStockholdersBasic)
    over less-specific ones (ProfitLoss) when multiple have data for same period.
    """
    def key(e): return (e.get("end", ""), e.get("form", ""))

    rev_map = {key(r): r for r in rev_entries}
    eps_map = {key(e): e for e in eps_entries}

    # NI: build map but resolve conflicts by concept specificity
    NI_PRIORITY = [
        "NetIncomeLossAvailableToCommonStockholdersBasic",
        "NetIncomeLossAvailableToCommonStockholdersDiluted",
        "NetIncomeLoss",
        "ProfitLoss",
    ]
    ni_map: dict = {}
    for n in ni_entries:
        k = key(n)
        concept = n.get("concept", "")
        existing = ni_map.get(k)
        if existing is None:
            ni_map[k] = n
        else:
            # Replace if current concept is higher priority
            existing_concept = existing.get("concept", "")
            if NI_PRIORITY.index(concept) < NI_PRIORITY.index(existing_concept):
                ni_map[k] = n

    all_keys = set(rev_map) | set(ni_map) | set(eps_map)
    result = []
    for k in sorted(all_keys, reverse=True):
        end, form = k
        result.append({
            "period_end":  end,
            "form":        form,
            "period_type": _classify_period(form, "", end),
            "revenue":     rev_map.get(k, {}).get("val"),
            "net_income":  ni_map.get(k, {}).get("val"),
            "eps":         eps_map.get(k, {}).get("val"),
            "currency":    rev_map.get(k, {}).get("currency")
                           or ni_map.get(k, {}).get("currency", "CNY"),
        })
    return result


def _deduplicate_periods(periods: list) -> list:
    """
    Deduplicate periods by (period_end, form) key, keeping highest-scoring form.
    
    Bug fix: previous key was (year, period_type) which caused 2017 data from a
    2018 filing to collide with 2018 period data from the same filing year.
    Using (period_end, form) ensures exact period dates don't collide across
    different filings/forms.
    
    Priority: 20-F/10-K > 6-K/10-Q > others.
    Within same form, prefer latest filed.
    """
    seen = {}
    for p in periods:
        period_end = p.get("period_end", "")
        form = p.get("form", "")
        k = (period_end, form)  # exact period + exact form = unique key
        # Score: higher is better
        score = (
            4 if "20-F" in form else
            3 if "10-K" in form else
            2 if "6-K" in form else
            1 if "10-Q" in form else 0
        )
        if k not in seen or score > seen[k]["_score"]:
            p["_score"] = score
            seen[k] = p
    return list(seen.values())


# ─────────────────────────────────────────────────────────────────────────────
# Formatting
# ─────────────────────────────────────────────────────────────────────────────

def format_table(data: dict) -> str:
    """Format financials as a readable ASCII table."""
    rows = data.get("financials", [])
    if not rows:
        return (f"\nNo financial data found for "
                f"{data.get('company_name') or data.get('cik')}\n")

    currency = rows[0].get("currency", "CNY") if rows else "CNY"
    currency_symbol = {"CNY": "¥", "USD": "$", "HKD": "HK$"}.get(currency, currency + " ")

    lines = []
    pad = 82
    lines.append(f"\n{'═' * pad}")
    lines.append(f"  {data.get('company_name', 'N/A')}  |  CIK: {data.get('cik')}  |  "
                  f"Updated: {data.get('data_updated', 'N/A')}")
    lines.append(f"{'═' * pad}")
    lines.append(f"  {'Period End':<12} {'Form':<7} {'Type':<10} "
                 f"{'Revenue':>18} {'Net Income':>18} {'EPS':>12}")
    lines.append(f"  {'─' * 70}")

    for row in rows:
        rev = _fmt_money(row.get("revenue"), currency_symbol)
        ni  = _fmt_money(row.get("net_income"), currency_symbol)
        eps = _fmt_eps(row.get("eps"), currency_symbol)
        lines.append(
            f"  {row.get('period_end', 'N/A'):<12} "
            f"{row.get('form', ''):<7} "
            f"{row.get('period_type', ''):<10} "
            f"{rev:>18} {ni:>18} {eps:>12}"
        )

    lines.append(f"{'═' * pad}")
    cu = data["concepts_used"]
    lines.append(f"  Concepts: revenue={cu['revenue']}, net_income={cu['net_income']}, eps={cu['eps']}")
    lines.append(f"  Currency: {currency}  |  Period filter: {data.get('period_type', 'all')}")
    return "\n".join(lines)


def _fmt_money(val, symbol: str = "¥") -> str:
    if val is None:
        return f"{symbol}N/A"
    if not isinstance(val, (int, float)):
        return f"{symbol}{val}"
    abs_val = abs(val)
    if abs_val >= 1_000_000_000:
        return f"{symbol}{val / 1_000_000_000:.2f}B"
    if abs_val >= 1_000_000:
        return f"{symbol}{val / 1_000_000:.2f}M"
    return f"{symbol}{val:,.0f}"


def _fmt_eps(val, symbol: str = "¥") -> str:
    if val is None:
        return f"{symbol}N/A"
    if not isinstance(val, (int, float)):
        return str(val)
    return f"${val:.2f}"


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Fetch SEC XBRL financial data for US-listed Chinese companies.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent(f"""
            Examples:
              python3 sec_finance.py --search "JD.com"
              python3 sec_finance.py --cik 0001549802 --period quarterly
              python3 sec_finance.py --cik 0001549802 --period annual --output json
              python3 sec_finance.py --cik 0001549802 --limit 4

            Built-in CIK Reference:
              JD.com:       0001549802  |  Alibaba:    0001577552
              Baidu:        0001329099  |  PDD:        0001738036
              NetEase:      0001068008  |  Tencent:    0001794714
        """),
    )
    parser.add_argument("--search",  type=str, help="Company name to search (auto-resolve CIK)")
    parser.add_argument("--cik",     type=str, help="10-digit CIK (with leading zeros)")
    parser.add_argument("--period",  type=str, choices=["quarterly", "annual", "all"],
                        default="all", help="Filter by period type (default: all)")
    parser.add_argument("--output",  type=str, choices=["json", "table"],
                        default="table", help="Output format (default: table)")
    parser.add_argument("--limit",   type=int, default=8,
                        help="Max periods to show (default: 8)")

    args = parser.parse_args()

    try:
        # ── Search mode ───────────────────────────────────────────────────────
        if args.search:
            result = cik_from_name(args.search)
            print(f"\n✅ Found: {result['name']}")
            print(f"   CIK:      {result['cik']}")
            print(f"   Ticker:   {result.get('ticker', 'N/A')}")
            print(f"   Exchange: {result.get('exchange', 'N/A')}")
            print(f"\n   Fetching financials...\n")
            time.sleep(0.5)  # gentle rate limit
            data = fetch_financials(result["cik"], period=args.period, limit=args.limit)
            if args.output == "json":
                print(json.dumps(data, indent=2, ensure_ascii=False))
            else:
                print(format_table(data))
            return

        # ── CIK mode ──────────────────────────────────────────────────────────
        if args.cik:
            cik = args.cik.strip().zfill(10)
            data = fetch_financials(cik, period=args.period, limit=args.limit)
            if args.output == "json":
                print(json.dumps(data, indent=2, ensure_ascii=False))
            else:
                print(format_table(data))
            return

        # ── Default: show help + CIK reference ────────────────────────────────
        parser.print_help()
        print(f"\n\n📋  Built-in Chinese Stock CIK Reference ({len(CHINESE_STOCKS)} companies):")
        print(f"  {'Company':<22} {'CIK':<12} {'Ticker':<8} {'Exchange'}")
        print(f"  {'─' * 55}")
        for name, info in CHINESE_STOCKS.items():
            print(f"  {name:<22} {info['cik']:<12} {info['ticker']:<8} {info['exchange']}")

    except ValueError as e:
        print(f"❌  {e}", file=sys.stderr)
        sys.exit(1)
    except ConnectionError as e:
        print(f"🌐  {e}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"❌  Unexpected error: {e}", file=sys.stderr)
        raise


if __name__ == "__main__":
    main()
