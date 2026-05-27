"""
日次自動通知スクリプト(ローカル&GitHub Actions 両対応版)

【動作モード自動切り替え】
  CSV モード   : 当日の楽天証券CSVが同フォルダにあれば自動で使用
                 -> 楽天スーパースクリーナーの財務データをそのまま使うため高精度
  yfinanceモード: CSVがない場合(GitHub Actions など)
                 -> CORE_UNIVERSE の銘柄を yfinance で取得してフィルタリング

【処理フロー(CSVモード)】
  1. 当日CSVを自動検出
  2. 財務フィルター(売上高+利益成長率>=20%, 自己資本比率>=50%, 時価総額100-1500億)
  3. 当日騰落フィルター(+-5%超は除外)
  4. Q1 日経地合いチェック
  5. Q4-Q11 チャート審査
  6. Q8 ニュース / Q9 決算日チェック
  7. スコア計算 -> Discord に通知

実行: python notify_daily.py
環境変数 DISCORD_WEBHOOK_URL が必須。
"""
import os
import sys
import warnings
import datetime
import glob

warnings.filterwarnings('ignore')

# 自身のディレクトリを import path に追加
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)


def main():
    mode = os.environ.get('NOTIFY_MODE', 'morning').strip()
    print(f"=== 日本株スイング 日次通知 [{mode}] {datetime.datetime.utcnow().isoformat()} UTC ===")

    # Webhook URL 確認
    webhook = os.environ.get('DISCORD_WEBHOOK_URL', '').strip()
    if not webhook:
        print("ERROR: 環境変数 DISCORD_WEBHOOK_URL が設定されていません")
        sys.exit(1)
    print(f"Webhook: {webhook[:60]}...")

    # -- モード分岐 --
    if mode == 'noon':
        _noon_report(webhook)
        return
    if mode == 'evening':
        _evening_report(webhook)
        return
    # morning(デフォルト)はそのまま下へ

    today_str = datetime.date.today().strftime("%Y-%m-%d")

    # -- CSV モード自動検出 --
    # 当日の楽天証券CSVが存在すれば高精度CSVモードで実行
    csv_path = _find_today_csv(SCRIPT_DIR)
    if csv_path:
        print(f"\nCSV検出: {os.path.basename(csv_path)}")
        print("  CSVモードで実行します（楽天財務データ使用 -> 高精度）\n")
        try:
            _csv_mode_analysis(csv_path, webhook, today_str)
        except Exception as e:
            print(f"\n  [WARN] CSV解析に失敗しました: {e}")
            print("  yfinanceモードにフォールバックします...\n")
        else:
            print("\n=== 完了 (CSVモード) ===")
            return

    # -- yfinanceモード(クラウド自動化 / CSVなし時のフォールバック) --
    print("yfinanceモードで実行します (CORE_UNIVERSE 使用)\n")

    # 必要モジュール
    try:
        from core_universe import get_universe
        from financial_filter import filter_universe
        from swing_analyzer import (
            check_nikkei_q1, analyze_chart,
            check_news_q8, check_earnings_q9, compute_score,
        )
        from notifier import (
            send_analysis_result, send_text,
        )
    except ImportError as e:
        print(f"ERROR: モジュール読み込み失敗: {e}")
        sys.exit(1)

    # -- Step 1: ユニバース取得 --
    print("\n[1/5] 銘柄ユニバース取得中...")
    universe = get_universe(include_watchlist=True, script_dir=SCRIPT_DIR)
    print(f"  対象: {len(universe)} 銘柄")

    # -- Step 2: 財務フィルター --
    print("\n[2/5] 財務フィルター実行中...(時間がかかります)")
    fin_result = filter_universe(universe, verbose=True)
    fin_passed = fin_result['passed']
    print(f"\n  財務通過: {len(fin_passed)}/{len(universe)}")

    if not fin_passed:
        print("\n  本日は財務通過銘柄なし。通知して終了。")
        send_text(
            f"📅 **{today_str}** 日本株スイング\n"
            f"財務フィルター通過銘柄なし。\n"
            f"対象 {len(universe)}銘柄スキャン -> 通過0\n"
            f"何もしないことは、立派な利益確定です。",
            webhook_url=webhook,
        )
        return

    # -- Step 4: Q1 日経地合い --
    print("\n[3/5] Q1 日経地合いチェック中...")
    q1_status, q1_msg = check_nikkei_q1()
    print(f"  Q1: {q1_msg}")

    if q1_status == "stop":
        print("\n  弱気地合い -> 全面見送り通知して終了")
        ok, msg = send_analysis_result(
            passed=[],
            q1_status=q1_status,
            q1_msg=q1_msg,
            today_str=today_str,
            webhook_url=webhook,
        )
        print(f"  Discord: {msg}")
        return

    # -- Step 5: Q4-Q11 チャート審査 --
    print(f"\n[4/5] チャート審査中({len(fin_passed)}銘柄)...")
    passed = []
    failed = []

    for i, (ticker, fin) in enumerate(fin_passed, 1):
        code = ticker.replace('.T', '')
        name = _get_ticker_name(code, ticker)

        print(f"  [{i}/{len(fin_passed)}] {code} {name}", end='', flush=True)
        result, err = analyze_chart(code)
        if err:
            print(f"  [SKIP] ({err})")
            continue

        # 当日騰落チェック(+-5%超は除外)
        try:
            import yfinance as yf
            hist = yf.Ticker(ticker).history(period='5d')
            if len(hist) >= 2:
                today_close = hist['Close'].iloc[-1]
                prev_close = hist['Close'].iloc[-2]
                day_change = (today_close - prev_close) / prev_close * 100
                if abs(day_change) > 5.0:
                    print(f"  [SKIP] 当日+-5%超({day_change:+.1f}%)")
                    continue
        except Exception:
            pass

        # Q9 決算日
        q9_status, q9_msg = check_earnings_q9(code)
        result['q9_status'] = q9_status
        result['q9_msg'] = q9_msg
        if result['all_pass'] and q9_status == 'danger':
            result['all_pass'] = False

        # Q8 ニュース
        q8 = check_news_q8(code, name)
        result['q8'] = q8
        if q8['verdict'] == 'danger':
            result['all_pass'] = False

        if result['all_pass']:
            total, grade, scores = compute_score(result)
            result['score'] = total
            result['grade'] = grade
            result['score_detail'] = scores
            result['_code'] = code
            result['_name'] = name
            result.update(fin)
            passed.append(result)
            print(f"  [PASS] {total}点[{grade}]")
        else:
            failed.append({'_code': code, '_name': name, **result})
            print(f"  [NG]")

    passed.sort(key=lambda x: x.get('score', 0), reverse=True)

    # -- Step 6: Discord 送信 --
    print(f"\n[5/5] Discord 送信中...")
    print(f"  通過: {len(passed)} 銘柄  /  見送り: {len(failed)} 銘柄")
    ok, msg = send_analysis_result(
        passed=passed,
        q1_status=q1_status,
        q1_msg=q1_msg,
        today_str=today_str,
        webhook_url=webhook,
    )
    if ok:
        print(f"  送信成功")
    else:
        print(f"  送信失敗: {msg}")
        sys.exit(1)

    print("\n=== 完了 (yfinanceモード) ===")


# ============================================================
#  CSV 自動検出
# ============================================================

def _find_today_csv(script_dir):
    """当日(JST)に更新された楽天証券CSVをスクリプトフォルダから探す。

    戻り値: CSVのパス、または None(当日CSVが見つからない場合)
    """
    pattern = os.path.join(script_dir, "*.csv")
    all_csv = [
        f for f in glob.glob(pattern)
        if "results" not in f.replace("\\", "/")
    ]
    if not all_csv:
        return None

    # JST = UTC+9
    jst_offset = datetime.timedelta(hours=9)
    today_jst = (datetime.datetime.utcnow() + jst_offset).date()

    today_csv = [
        f for f in all_csv
        if (datetime.datetime.utcfromtimestamp(os.path.getmtime(f)) + jst_offset).date() == today_jst
    ]
    if not today_csv:
        return None  # 当日CSVなし -> yfinanceモードへ

    return max(today_csv, key=os.path.getmtime)


# ============================================================
#  CSV モード分析
# ============================================================

def _csv_mode_analysis(csv_path, webhook, today_str):
    """楽天証券CSVを使った高精度分析 -> Discord通知。

    swing_analyzer.run_analysis と同じロジックを
    Discord通知専用にまとめたもの。
    """
    from swing_analyzer import (
        load_csv, clean_number, parse_day_change,
        check_nikkei_q1, analyze_chart,
        check_news_q8, check_earnings_q9, compute_score,
    )
    from notifier import send_analysis_result, send_text

    # -- CSVパース --
    df = load_csv(csv_path)
    df.columns = df.columns.str.strip()

    col = {}
    for c in df.columns:
        if 'コード' in c:           col['code']    = c
        elif '銘柄名' in c:         col['name']    = c
        elif '売上高変化率' in c:   col['revenue'] = c
        elif '経常利益変化率' in c: col['profit']  = c
        elif '自己資本比率' in c:   col['equity']  = c
        elif '時価総額' in c:       col['mktcap']  = c
        elif '前日比' in c:         col['change']  = c

    required = ['code', 'name', 'revenue', 'profit', 'equity', 'mktcap', 'change']
    missing = [k for k in required if k not in col]
    if missing:
        raise ValueError(f"CSVに必要な列が見つかりません: {missing}")

    df['_code']    = df[col['code']].astype(str).str.strip()
    df['_name']    = df[col['name']].astype(str).str.strip()
    df['_revenue'] = df[col['revenue']].apply(clean_number)
    df['_profit']  = df[col['profit']].apply(clean_number)
    df['_equity']  = df[col['equity']].apply(clean_number)
    df['_mktcap']  = df[col['mktcap']].apply(clean_number)
    df['_change']  = df[col['change']].apply(parse_day_change)

    print(f"  CSV: {len(df)}銘柄を読み込みました")

    # -- STEP1: 財務フィルター --
    MIN_REVENUE = 20.0
    MIN_PROFIT  = 20.0
    MIN_EQUITY  = 50.0
    MIN_MKTCAP  = 10_000   # 百万円 = 100億円
    MAX_MKTCAP  = 150_000  # 百万円 = 1500億円

    f1 = df[
        (df['_revenue'] >= MIN_REVENUE) &
        (df['_profit']  >= MIN_PROFIT)  &
        (df['_equity']  >= MIN_EQUITY)  &
        (df['_mktcap']  >= MIN_MKTCAP)  &
        (df['_mktcap']  <= MAX_MKTCAP)
    ].copy()

    for _, r in df[~df.index.isin(f1.index)].iterrows():
        reasons = []
        if r['_revenue'] < MIN_REVENUE: reasons.append(f"売上高{r['_revenue']:.1f}%")
        if r['_profit']  < MIN_PROFIT:  reasons.append(f"利益{r['_profit']:.1f}%")
        if r['_equity']  < MIN_EQUITY:  reasons.append(f"自己資本{r['_equity']:.1f}%")
        if not (MIN_MKTCAP <= r['_mktcap'] <= MAX_MKTCAP):
            reasons.append(f"時価総額{r['_mktcap']:.0f}M")
        print(f"  [財務NG] {r['_code']} {r['_name']}: {' / '.join(reasons)}")

    print(f"  財務フィルター通過: {len(f1)}/{len(df)}銘柄")

    # -- STEP2: 当日騰落フィルター(+-5%超は除外) --
    f2       = f1[f1['_change'].abs() <= 5.0].copy()
    excluded = f1[f1['_change'].abs() >  5.0]
    for _, r in excluded.iterrows():
        print(f"  [騰落除外] {r['_code']} {r['_name']} ({r['_change']:+.1f}%)")

    print(f"  チャート審査対象: {len(f2)}銘柄")

    if len(f2) == 0:
        send_text(
            f"📅 **{today_str}** 日本株スイング 分析結果\n"
            f"チャート審査対象銘柄なし。\n"
            f"何もしないことは、立派な利益確定です。",
            webhook_url=webhook,
        )
        return

    # -- Q1: 日経地合い --
    print("\nQ1 日経地合いチェック中...")
    q1_status, q1_msg = check_nikkei_q1()
    print(f"  {q1_msg}")

    if q1_status == "stop":
        print("  弱気地合い -> 全面見送り")
        send_analysis_result(
            passed=[], q1_status=q1_status, q1_msg=q1_msg,
            today_str=today_str, webhook_url=webhook,
        )
        return

    # -- Q4-Q11: チャート審査 --
    print(f"\nチャート審査中({len(f2)}銘柄)...")
    passed = []

    for _, row in f2.iterrows():
        code = row['_code']
        name = row['_name']
        print(f"  {code} {name}", end='', flush=True)

        result, err = analyze_chart(code)
        if err:
            print(f"  [SKIP] {err}")
            continue

        # Q9 決算日
        q9_status, q9_msg = check_earnings_q9(code)
        result['q9_status'] = q9_status
        result['q9_msg']    = q9_msg
        if result['all_pass'] and q9_status == 'danger':
            result['all_pass'] = False

        # Q8 ニュース
        q8 = check_news_q8(code, name)
        result['q8'] = q8
        if q8['verdict'] == 'danger':
            result['all_pass'] = False

        if result['all_pass']:
            total, grade, scores = compute_score(result)
            result['score']        = total
            result['grade']        = grade
            result['score_detail'] = scores
            result['_code']        = code
            result['_name']        = name
            passed.append(result)
            print(f"  [PASS] {total}点[{grade}]")
        else:
            print("  [NG]")

    passed.sort(key=lambda x: x.get('score', 0), reverse=True)
    print(f"\n  通過: {len(passed)}銘柄")

    # -- Discord 送信 --
    print("Discord 送信中...")
    ok, msg = send_analysis_result(
        passed=passed,
        q1_status=q1_status,
        q1_msg=q1_msg,
        today_str=today_str,
        webhook_url=webhook,
    )
    if not ok:
        raise RuntimeError(f"Discord送信失敗: {msg}")
    print(f"  送信成功")


# ============================================================
#  ユーティリティ
# ============================================================

def _get_ticker_name(code, ticker):
    """銘柄コードから日本語名を取得。
    優先順: (1) watchlist.json -> (2) yfinance ticker.info -> (3) コードそのまま
    """
    try:
        import json
        wl_path = os.path.join(SCRIPT_DIR, 'watchlist.json')
        if os.path.exists(wl_path):
            with open(wl_path, 'r', encoding='utf-8') as f:
                wl = json.load(f)
            if code in wl and 'name' in wl[code]:
                return wl[code]['name']
    except Exception:
        pass

    try:
        import yfinance as yf
        info = yf.Ticker(ticker).info
        name = info.get('shortName') or info.get('longName')
        if name:
            return name
    except Exception:
        pass

    return code


# ============================================================
#  昼レポート (noon)
# ============================================================

def _noon_report(webhook):
    """昼 12:30 JST -- 前場終了チェック"""
    import yfinance as yf

    today_str = datetime.date.today().strftime("%Y-%m-%d")
    jst_now = datetime.datetime.utcnow() + datetime.timedelta(hours=9)
    print(f"[昼レポート] {today_str} {jst_now.strftime('%H:%M')} JST")

    try:
        from swing_analyzer import check_nikkei_q1
        from notifier import _post
    except ImportError as e:
        print(f"ERROR: {e}")
        sys.exit(1)

    nk = yf.Ticker("^N225")
    hist = nk.history(period='5d')
    n225_now = hist['Close'].iloc[-1]
    n225_prev = hist['Close'].iloc[-2]
    n225_chg = (n225_now - n225_prev) / n225_prev * 100

    usdjpy_val = "---"
    try:
        fx = yf.Ticker("JPY=X")
        fx_hist = fx.history(period='2d')
        usdjpy_val = f"{fx_hist['Close'].iloc[-1]:.2f}円"
    except Exception:
        pass

    q1_status, q1_msg = check_nikkei_q1()
    q1_label = {'go': '強気', 'caution': '中立', 'stop': '弱気'}.get(q1_status, '?')
    q1_color = {'go': 0x1D9E75, 'caution': 0xEF9F27, 'stop': 0xE24B4A}.get(q1_status, 0x888780)

    chg_icon = "📈" if n225_chg >= 0 else "📉"
    chg_str = f"{n225_chg:+.2f}%"

    payload = {
        'content': f"🕐 **{today_str} 昼チェック（前場終了）**",
        'embeds': [
            {
                'title': f'{chg_icon} 日経225  {n225_now:,.0f}円  ({chg_str})',
                'description': (
                    f"**USD/JPY**: {usdjpy_val}\n"
                    f"**地合い**: {q1_label}\n"
                    f"```{q1_msg}```\n"
                    f"後場は 12:30-15:30 JST です。地合いを確認して慎重に。"
                ),
                'color': q1_color,
            }
        ],
    }
    ok, msg = _post(payload, webhook)
    print(f"  {'OK' if ok else 'NG'} {msg}")
    if not ok:
        sys.exit(1)


# ============================================================
#  夜レポート (evening)
# ============================================================

def _evening_report(webhook):
    """夜 20:00 JST -- 後場終了レビュー"""
    import yfinance as yf

    today_str = datetime.date.today().strftime("%Y-%m-%d")
    jst_now = datetime.datetime.utcnow() + datetime.timedelta(hours=9)
    print(f"[夜レポート] {today_str} {jst_now.strftime('%H:%M')} JST")

    try:
        from notifier import _post
    except ImportError as e:
        print(f"ERROR: {e}")
        sys.exit(1)

    nk = yf.Ticker("^N225")
    hist = nk.history(period='5d')
    n225_close = hist['Close'].iloc[-1]
    n225_open  = hist['Open'].iloc[-1]
    n225_high  = hist['High'].iloc[-1]
    n225_low   = hist['Low'].iloc[-1]
    n225_prev  = hist['Close'].iloc[-2]
    n225_chg   = (n225_close - n225_prev) / n225_prev * 100

    topix_chg_str = "---"
    try:
        tp_ticker = yf.Ticker("^TOPX")
        tp_hist = tp_ticker.history(period='5d')
        if len(tp_hist) >= 2:
            tc = tp_hist['Close'].iloc[-1]
            tp = tp_hist['Close'].iloc[-2]
            topix_chg_str = f"{(tc - tp) / tp * 100:+.2f}%"
    except Exception:
        pass

    usdjpy_val = "---"
    try:
        fx = yf.Ticker("JPY=X")
        fx_hist = fx.history(period='2d')
        usdjpy_val = f"{fx_hist['Close'].iloc[-1]:.2f}円"
    except Exception:
        pass

    chg_icon = "📈" if n225_chg >= 0 else "📉"
    chg_str = f"{n225_chg:+.2f}%"
    color = 0x1D9E75 if n225_chg >= 0 else 0xE24B4A

    if n225_chg >= 1.5:
        advice = "強い上昇日でした。明日は利食い売りに注意。高値追いは慎重に。"
    elif n225_chg >= 0:
        advice = "小幅高で安定。明日も地合いを確認してからエントリーしましょう。"
    elif n225_chg >= -1.5:
        advice = "小幅安。大きな崩れではありません。明日の寄り付き確認を。"
    else:
        advice = "大幅安の一日でした。明日は様子見が無難。焦りは禁物です。"

    payload = {
        'content': f"🌙 **{today_str} 夜レビュー（後場終了）**",
        'embeds': [
            {
                'title': f'{chg_icon} 日経225 終値  {n225_close:,.0f}円  ({chg_str})',
                'description': (
                    f"**始値**: {n225_open:,.0f}円　"
                    f"**高値**: {n225_high:,.0f}円　"
                    f"**安値**: {n225_low:,.0f}円\n"
                    f"**TOPIX**: {topix_chg_str}　**USD/JPY**: {usdjpy_val}\n\n"
                    f"💡 {advice}"
                ),
                'color': color,
            }
        ],
    }
    ok, msg = _post(payload, webhook)
    print(f"  {'OK' if ok else 'NG'} {msg}")
    if not ok:
        sys.exit(1)


if __name__ == '__main__':
    main()
