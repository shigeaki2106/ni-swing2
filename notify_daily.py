"""
日次自動通知スクリプト(GitHub Actions専用、完全自動化版)

毎朝定時に GitHub Actions 上で実行され、以下を行います:

  1. core_universe.py の銘柄ユニバースを取得
  2. yfinance で財務データ取得 → 楽天SPF相当のフィルター適用
  3. STEP2 当日騰落フィルター(±5%超は除外)
  4. Q1 日経地合いチェック(下落地合いなら通知して終了)
  5. Q4-Q11 のチャート審査(swing_analyzer.py の関数を再利用)
  6. Q8 ニュースキーワードスキャン
  7. Q9 決算日チェック
  8. スコア計算 → Discord に通知送信

実行: python notify_daily.py
環境変数 DISCORD_WEBHOOK_URL が必須。
"""
import os
import sys
import warnings
import datetime

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

    # ── モード分岐 ──
    if mode == 'noon':
        _noon_report(webhook)
        return
    if mode == 'evening':
        _evening_report(webhook)
        return
    # morning(デフォルト)はそのまま下へ

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

    today_str = datetime.date.today().strftime("%Y-%m-%d")

    # ── Step 1: ユニバース取得 ──
    print("\n[1/5] 銘柄ユニバース取得中...")
    universe = get_universe(include_watchlist=True, script_dir=SCRIPT_DIR)
    print(f"  対象: {len(universe)} 銘柄")

    # ── Step 2: 財務フィルター ──
    print("\n[2/5] 財務フィルター実行中...(時間がかかります)")
    fin_result = filter_universe(universe, verbose=True)
    fin_passed = fin_result['passed']
    print(f"\n  財務通過: {len(fin_passed)}/{len(universe)}")

    if not fin_passed:
        print("\n  本日は財務通過銘柄なし。通知して終了。")
        send_text(
            f"📅 **{today_str}** 日本株スイング\n"
            f"財務フィルター通過銘柄なし。\n"
            f"対象 {len(universe)}銘柄スキャン → 通過0\n"
            f"何もしないことは、立派な利益確定です。",
            webhook_url=webhook,
        )
        return

    # ── Step 3: 当日騰落チェック ──
    # チャート審査で扱うので個別取得する
    # ここでは単純化のため一括スキップ(analyze_chart に含まれる)

    # ── Step 4: Q1 日経地合い ──
    print("\n[3/5] Q1 日経地合いチェック中...")
    q1_status, q1_msg = check_nikkei_q1()
    print(f"  Q1: {q1_msg}")

    if q1_status == "stop":
        print("\n  弱気地合い → 全面見送り通知して終了")
        # 弱気の場合は短い通知で終わる
        from notifier import send_analysis_result
        ok, msg = send_analysis_result(
            passed=[],
            q1_status=q1_status,
            q1_msg=q1_msg,
            today_str=today_str,
            webhook_url=webhook,
        )
        print(f"  Discord: {msg}")
        return

    # ── Step 5: Q4-Q11 チャート審査 ──
    print(f"\n[4/5] チャート審査中({len(fin_passed)}銘柄)...")
    passed = []
    failed = []

    for i, (ticker, fin) in enumerate(fin_passed, 1):
        # ticker は '6920.T' 形式、code は '6920'
        code = ticker.replace('.T', '')
        name = _get_ticker_name(code, ticker)

        print(f"  [{i}/{len(fin_passed)}] {code} {name}", end='', flush=True)
        result, err = analyze_chart(code)
        if err:
            print(f"  [SKIP] ({err})")
            continue

        # 当日騰落チェック(±5%超は除外)
        try:
            import yfinance as yf
            import pandas as pd
            hist = yf.Ticker(ticker).history(period='5d')
            if len(hist) >= 2:
                today_close = hist['Close'].iloc[-1]
                prev_close = hist['Close'].iloc[-2]
                day_change = (today_close - prev_close) / prev_close * 100
                if abs(day_change) > 5.0:
                    print(f"  [SKIP] 当日±5%超({day_change:+.1f}%)")
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

        # 通過なら情報まとめ
        if result['all_pass']:
            total, grade, scores = compute_score(result)
            result['score'] = total
            result['grade'] = grade
            result['score_detail'] = scores
            result['_code'] = code
            result['_name'] = name
            result.update(fin)  # 財務データもマージ
            passed.append(result)
            print(f"  [PASS] {total}点[{grade}]")
        else:
            failed.append({'_code': code, '_name': name, **result})
            print(f"  [NG]")

    # スコア順にソート
    passed.sort(key=lambda x: x.get('score', 0), reverse=True)

    # ── Step 6: Discord 送信 ──
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
        print(f"  ✅ 送信成功")
    else:
        print(f"  ❌ 送信失敗: {msg}")
        sys.exit(1)

    print("\n=== 完了 ===")


def _get_ticker_name(code, ticker):
    """銘柄コードから日本語名を取得。
    優先順: ① watchlist.json → ② yfinance ticker.info → ③ コードそのまま
    """
    # watchlist から
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

    # yfinance info から
    try:
        import yfinance as yf
        info = yf.Ticker(ticker).info
        name = info.get('shortName') or info.get('longName')
        if name:
            return name
    except Exception:
        pass

    return code


def _noon_report(webhook):
    """昼 12:30 JST — 前場終了チェック"""
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

    # 日経225 現在値
    nk = yf.Ticker("^N225")
    hist = nk.history(period='5d')
    n225_now = hist['Close'].iloc[-1]
    n225_prev = hist['Close'].iloc[-2]
    n225_chg = (n225_now - n225_prev) / n225_prev * 100

    # USD/JPY
    usdjpy_val = "---"
    try:
        fx = yf.Ticker("JPY=X")
        fx_hist = fx.history(period='2d')
        usdjpy_val = f"{fx_hist['Close'].iloc[-1]:.2f}円"
    except Exception:
        pass

    # Q1 地合い
    q1_status, q1_msg = check_nikkei_q1()
    q1_label = {'go': '🟢 強気', 'caution': '🟡 中立', 'stop': '🔴 弱気'}.get(q1_status, '?')
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
                    f"後場は 12:30〜15:30 JST です。地合いを確認して慎重に。"
                ),
                'color': q1_color,
            }
        ],
    }
    ok, msg = _post(payload, webhook)
    print(f"  {'✅' if ok else '❌'} {msg}")
    if not ok:
        sys.exit(1)


def _evening_report(webhook):
    """夜 20:00 JST — 後場終了レビュー"""
    import yfinance as yf

    today_str = datetime.date.today().strftime("%Y-%m-%d")
    jst_now = datetime.datetime.utcnow() + datetime.timedelta(hours=9)
    print(f"[夜レポート] {today_str} {jst_now.strftime('%H:%M')} JST")

    try:
        from notifier import _post
    except ImportError as e:
        print(f"ERROR: {e}")
        sys.exit(1)

    # 日経225 本日終値
    nk = yf.Ticker("^N225")
    hist = nk.history(period='5d')
    n225_close = hist['Close'].iloc[-1]
    n225_open  = hist['Open'].iloc[-1]
    n225_high  = hist['High'].iloc[-1]
    n225_low   = hist['Low'].iloc[-1]
    n225_prev  = hist['Close'].iloc[-2]
    n225_chg   = (n225_close - n225_prev) / n225_prev * 100

    # TOPIX
    topix_chg_str = "---"
    try:
        tp = yf.Ticker("^TOPX")
        tp_hist = tp.history(period='5d')
        if len(tp_hist) >= 2:
            tc = tp_hist['Close'].iloc[-1]
            tp = tp_hist['Close'].iloc[-2]
            topix_chg_str = f"{(tc - tp) / tp * 100:+.2f}%"
    except Exception:
        pass

    # USD/JPY
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

    # 明日の一言アドバイス
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
    print(f"  {'✅' if ok else '❌'} {msg}")
    if not ok:
        sys.exit(1)


if __name__ == '__main__':
    main()
