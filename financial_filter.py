"""
yfinance ベースの財務フィルター

楽天証券 CSV の代わりに yfinance API で財務データを取得し、
swing_analyzer.py の STEP1(財務フィルター)相当を実施する。

代替対応:
  楽天 CSV 項目               → yfinance データ
  ─────────────────────────────────────────────────
  売上高変化率(前年度比)     → info.revenueGrowth × 100
  経常利益変化率(前年度比)   → info.earningsGrowth × 100
  自己資本比率                → balance_sheet から計算
  時価総額                    → info.marketCap / 1_000_000

注意:
  yfinance の Japanese financial data は時々欠損する。
  欠損項目は通過扱いとして、人間が後で確認する方針。
  (False positives より False negatives を避ける戦略)
"""
import warnings
warnings.filterwarnings('ignore')

import yfinance as yf
import pandas as pd


# 楽天 SPF と同じ閾値
MIN_REVENUE_GROWTH = 20.0
MIN_PROFIT_GROWTH  = 20.0
MIN_EQUITY_RATIO   = 50.0
MIN_MARKET_CAP     = 10000   # 百万円 = 100億円
MAX_MARKET_CAP     = 200000  # 百万円 = 2000億円(楽天SPFは1500億だが、緩めに)


def fetch_financials(ticker_code):
    """yfinance から1銘柄の財務データを取得して dict を返す。

    Args:
        ticker_code: '6920.T' などの yfinance 形式のティッカー

    Returns:
        {
            'market_cap': 時価総額(百万円), or None
            'revenue_growth': 売上高成長率(%), or None
            'earnings_growth': 利益成長率(%), or None
            'equity_ratio': 自己資本比率(%), or None
            'error': エラーメッセージ, or None
        }
    """
    result = {
        'market_cap': None,
        'revenue_growth': None,
        'earnings_growth': None,
        'equity_ratio': None,
        'error': None,
    }

    try:
        ticker = yf.Ticker(ticker_code)
        info = ticker.info or {}

        # 時価総額(円 → 百万円)
        mc = info.get('marketCap')
        if mc:
            result['market_cap'] = mc / 1_000_000

        # 売上高成長率(yfinance: 小数 → %)
        rg = info.get('revenueGrowth')
        if rg is not None:
            result['revenue_growth'] = rg * 100

        # 利益成長率
        eg = info.get('earningsGrowth')
        if eg is not None:
            result['earnings_growth'] = eg * 100

        # 自己資本比率: balance_sheet から計算
        try:
            bs = ticker.balance_sheet
            if bs is not None and not bs.empty:
                # 最新四半期(列が時系列、降順)
                latest_col = bs.columns[0]
                # 行名はバージョンによって異なる
                equity = None
                assets = None
                for eq_key in ('Stockholders Equity', 'Total Stockholder Equity',
                               'Common Stock Equity'):
                    if eq_key in bs.index:
                        equity = bs.loc[eq_key, latest_col]
                        break
                for as_key in ('Total Assets',):
                    if as_key in bs.index:
                        assets = bs.loc[as_key, latest_col]
                        break
                if equity and assets and assets > 0:
                    result['equity_ratio'] = (equity / assets) * 100
        except Exception:
            pass  # balance_sheet 取得失敗は致命的でない

    except Exception as e:
        result['error'] = str(e)

    return result


def passes_financial_filter(fin):
    """財務フィルター判定。

    判定方針:
        - 時価総額: 必須(欠損なら除外)
        - 売上高/利益成長率/自己資本比率: データがあれば閾値判定、無ければ通過扱い

    Returns:
        (passed: bool, reasons: list[str])
    """
    reasons = []

    # 時価総額(必須)
    mc = fin.get('market_cap')
    if mc is None:
        reasons.append('時価総額データなし')
        return False, reasons
    if mc < MIN_MARKET_CAP:
        reasons.append(f'時価総額{mc:.0f}M (下限{MIN_MARKET_CAP}M未満)')
    if mc > MAX_MARKET_CAP:
        reasons.append(f'時価総額{mc:.0f}M (上限{MAX_MARKET_CAP}M超過)')

    # 売上高成長率(データがあれば判定)
    rg = fin.get('revenue_growth')
    if rg is not None and rg < MIN_REVENUE_GROWTH:
        reasons.append(f'売上高{rg:.1f}% (下限{MIN_REVENUE_GROWTH}%未満)')

    # 利益成長率
    eg = fin.get('earnings_growth')
    if eg is not None and eg < MIN_PROFIT_GROWTH:
        reasons.append(f'利益{eg:.1f}% (下限{MIN_PROFIT_GROWTH}%未満)')

    # 自己資本比率
    er = fin.get('equity_ratio')
    if er is not None and er < MIN_EQUITY_RATIO:
        reasons.append(f'自己資本{er:.1f}% (下限{MIN_EQUITY_RATIO}%未満)')

    return len(reasons) == 0, reasons


def filter_universe(tickers, verbose=True):
    """ユニバースから財務フィルター通過銘柄を返す。

    Args:
        tickers: ティッカー文字列のリスト
        verbose: 進捗を print 出力するか

    Returns:
        {
            'passed':   [(ticker, financials_dict), ...],
            'rejected': [(ticker, financials_dict, reasons), ...],
            'errors':   [(ticker, error_msg), ...],
        }
    """
    passed, rejected, errors = [], [], []
    total = len(tickers)

    for i, t in enumerate(tickers, 1):
        if verbose:
            print(f"  [{i}/{total}] {t} 財務取得中...", end='', flush=True)
        fin = fetch_financials(t)
        if fin.get('error'):
            errors.append((t, fin['error']))
            if verbose:
                print(f"  [SKIP] {fin['error'][:30]}")
            continue
        ok, reasons = passes_financial_filter(fin)
        if ok:
            passed.append((t, fin))
            if verbose:
                mc = fin.get('market_cap', 0) or 0
                rg = fin.get('revenue_growth')
                rg_str = f"{rg:+.1f}%" if rg is not None else "?"
                print(f"  [OK] 時価{mc:.0f}M 売上{rg_str}")
        else:
            rejected.append((t, fin, reasons))
            if verbose:
                print(f"  [NG] {' / '.join(reasons[:2])}")

    return {'passed': passed, 'rejected': rejected, 'errors': errors}


if __name__ == '__main__':
    # 単体テスト: ユニバースの一部だけ確認
    from core_universe import CORE_UNIVERSE
    test_tickers = CORE_UNIVERSE[:5]
    print(f"  財務フィルターテスト: {test_tickers}")
    print()
    result = filter_universe(test_tickers, verbose=True)
    print()
    print(f"  通過: {len(result['passed'])}")
    print(f"  除外: {len(result['rejected'])}")
    print(f"  エラー: {len(result['errors'])}")
