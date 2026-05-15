"""
週末自動通知スクリプト(GitHub Actions専用)

NOTIFY_MODE に応じて以下を送信:
  weekend_sat … 土曜朝 = 今週の振り返り
  weekend_sun … 日曜朝 = 来週の注目ポイント

実行: python notify_weekend.py
環境変数 DISCORD_WEBHOOK_URL / NOTIFY_MODE が必要。
"""
import os
import sys
import json
import warnings
import datetime
import urllib.request
import urllib.error

warnings.filterwarnings('ignore')

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)


# ──────────────────────────────────────────────────────────
# 共通: Discord 送信
# ──────────────────────────────────────────────────────────

def _post(payload, webhook_url):
    data = json.dumps(payload, ensure_ascii=False).encode('utf-8')
    req = urllib.request.Request(
        webhook_url, data=data,
        headers={'Content-Type': 'application/json', 'User-Agent': 'NiSwing/1.0'},
        method='POST',
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            if resp.status in (200, 204):
                return True, "送信成功"
            return False, f"HTTP {resp.status}"
    except urllib.error.HTTPError as e:
        return False, f"HTTPエラー {e.code}: {e.reason}"
    except Exception as e:
        return False, f"送信エラー: {e}"


# ──────────────────────────────────────────────────────────
# 共通: 市場データ取得
# ──────────────────────────────────────────────────────────

def _get_market_data():
    """yfinance から主要指数の週次データを取得"""
    import yfinance as yf

    result = {}

    # 日経225
    try:
        nk = yf.Ticker("^N225")
        hist = nk.history(period='10d')
        # 今週(直近5営業日)の最初と最後
        if len(hist) >= 2:
            week_start = hist['Close'].iloc[-5] if len(hist) >= 5 else hist['Close'].iloc[0]
            week_end   = hist['Close'].iloc[-1]
            result['n225_close'] = week_end
            result['n225_week_chg'] = (week_end - week_start) / week_start * 100
            result['n225_high'] = hist['High'].iloc[-5:].max() if len(hist) >= 5 else hist['High'].max()
            result['n225_low']  = hist['Low'].iloc[-5:].min()  if len(hist) >= 5 else hist['Low'].min()
    except Exception as e:
        print(f"  [WARN] 日経225取得失敗: {e}")

    # S&P500
    try:
        sp = yf.Ticker("^GSPC")
        sp_hist = sp.history(period='10d')
        if len(sp_hist) >= 2:
            w_start = sp_hist['Close'].iloc[-5] if len(sp_hist) >= 5 else sp_hist['Close'].iloc[0]
            w_end   = sp_hist['Close'].iloc[-1]
            result['sp500_close'] = w_end
            result['sp500_week_chg'] = (w_end - w_start) / w_start * 100
    except Exception as e:
        print(f"  [WARN] S&P500取得失敗: {e}")

    # NASDAQ
    try:
        nd = yf.Ticker("^IXIC")
        nd_hist = nd.history(period='10d')
        if len(nd_hist) >= 2:
            w_start = nd_hist['Close'].iloc[-5] if len(nd_hist) >= 5 else nd_hist['Close'].iloc[0]
            w_end   = nd_hist['Close'].iloc[-1]
            result['nasdaq_week_chg'] = (w_end - w_start) / w_start * 100
    except Exception as e:
        print(f"  [WARN] NASDAQ取得失敗: {e}")

    # USD/JPY
    try:
        fx = yf.Ticker("JPY=X")
        fx_hist = fx.history(period='10d')
        if len(fx_hist) >= 2:
            result['usdjpy'] = fx_hist['Close'].iloc[-1]
            result['usdjpy_week_chg'] = fx_hist['Close'].iloc[-1] - (
                fx_hist['Close'].iloc[-5] if len(fx_hist) >= 5 else fx_hist['Close'].iloc[0]
            )
    except Exception as e:
        print(f"  [WARN] USD/JPY取得失敗: {e}")

    # VIX(恐怖指数)
    try:
        vx = yf.Ticker("^VIX")
        vx_hist = vx.history(period='5d')
        if len(vx_hist) >= 1:
            result['vix'] = vx_hist['Close'].iloc[-1]
    except Exception:
        pass

    return result


def _chg_str(chg):
    return f"{chg:+.2f}%" if chg is not None else "---"

def _chg_icon(chg):
    if chg is None:
        return "➖"
    return "📈" if chg >= 0 else "📉"

def _color(chg):
    if chg is None:
        return 0x888780
    return 0x1D9E75 if chg >= 0 else 0xE24B4A


# ──────────────────────────────────────────────────────────
# 土曜: 今週の振り返り
# ──────────────────────────────────────────────────────────

def saturday_report(webhook):
    """今週の市場まとめ + スイング視点コメント"""
    today_str = datetime.date.today().strftime("%Y-%m-%d")
    # 今週の月曜〜金曜の範囲を計算
    today = datetime.date.today()
    mon = today - datetime.timedelta(days=today.weekday() + 1)  # 前週月曜(土→-6)
    # JST では土曜=前営業週終了
    fri = today - datetime.timedelta(days=1)
    week_label = f"{mon.strftime('%m/%d')}〜{fri.strftime('%m/%d')}"

    print(f"[土曜レポート] 今週の振り返り ({week_label})")

    md = _get_market_data()

    n225_close     = md.get('n225_close')
    n225_week_chg  = md.get('n225_week_chg')
    n225_high      = md.get('n225_high')
    n225_low       = md.get('n225_low')
    sp500_chg      = md.get('sp500_week_chg')
    nasdaq_chg     = md.get('nasdaq_week_chg')
    usdjpy         = md.get('usdjpy')
    usdjpy_chg     = md.get('usdjpy_week_chg')
    vix            = md.get('vix')

    # スイング視点コメント
    if n225_week_chg is not None:
        if n225_week_chg >= 3:
            swing_comment = (
                "📈 強い上昇週でした。来週は高値更新チャレンジか調整か、\n"
                "週明け寄り付きの動きを確認してから判断しましょう。"
            )
        elif n225_week_chg >= 0:
            swing_comment = (
                "横ばい〜小幅高の一週間。トレンドは上向き維持。\n"
                "来週は出来高・値動きを見ながら好機を探りましょう。"
            )
        elif n225_week_chg >= -3:
            swing_comment = (
                "小幅安の週でした。大きな崩れではありませんが、\n"
                "来週は地合いを慎重に確認してからエントリーを。"
            )
        else:
            swing_comment = (
                "⚠ 大幅下落の週でした。来週は反発狙いより\n"
                "下値確認を優先。焦ってポジションを持つのは危険です。"
            )
    else:
        swing_comment = "データ取得に失敗しました。来週の地合いは朝の通知で確認してください。"

    n225_line = (
        f"**日経225**: {n225_close:,.0f}円  (週次 {_chg_str(n225_week_chg)})\n"
        f"　高値 {n225_high:,.0f}円 / 安値 {n225_low:,.0f}円"
        if n225_close else "日経225: データ取得失敗"
    )
    usdjpy_line = (
        f"**USD/JPY**: {usdjpy:.2f}円  (週次 {usdjpy_chg:+.2f}円)"
        if usdjpy else "USD/JPY: データ取得失敗"
    )
    sp_line = (
        f"**S&P500**: {_chg_icon(sp500_chg)} 週次 {_chg_str(sp500_chg)}"
    )
    nd_line = (
        f"**NASDAQ**: {_chg_icon(nasdaq_chg)} 週次 {_chg_str(nasdaq_chg)}"
    )
    vix_line = (
        f"**VIX(恐怖指数)**: {vix:.1f}"
        + (" 🔴 高め — 市場は警戒感あり" if vix and vix > 25 else
           " 🟡 やや高め" if vix and vix > 18 else
           " 🟢 落ち着いている" if vix else "")
        if vix else ""
    )

    desc = "\n".join(filter(None, [
        n225_line, usdjpy_line, sp_line, nd_line, vix_line,
        "", swing_comment,
    ]))

    payload = {
        'content': f"📊 **{week_label} 今週の振り返り**",
        'embeds': [{
            'title': f'{_chg_icon(n225_week_chg)} 週間サマリー',
            'description': desc,
            'color': _color(n225_week_chg),
            'footer': {'text': '来週も無理せず、チャンスが来たら乗る。それだけでいい。'},
        }],
    }
    ok, msg = _post(payload, webhook)
    print(f"  {'✅' if ok else '❌'} {msg}")
    if not ok:
        sys.exit(1)


# ──────────────────────────────────────────────────────────
# 日曜: 来週の注目ポイント
# ──────────────────────────────────────────────────────────

# 主な月次経済指標スケジュール(固定 or 目安)
# 実際の発表日は月により変わるため、曜日ベースで目安を提示
_WEEKLY_EVENTS = [
    # (weekday, 説明)  weekday: 0=月, 1=火, 2=水, 3=木, 4=金
    (0, "🇯🇵 東証寄り付き ─ 週初の地合いを必ずチェック"),
    (2, "🇺🇸 米FOMC議事録・FRB発言が多い曜日(注意)"),
    (4, "🇯🇵 SQ(第2金曜日)の可能性あり ─ 変動に注意"),
]

def sunday_report(webhook):
    """来週の注目ポイント + 準備チェックリスト"""
    today_str = datetime.date.today().strftime("%Y-%m-%d")
    today = datetime.date.today()
    next_mon = today + datetime.timedelta(days=1)  # 日曜の翌日=月曜
    next_fri = next_mon + datetime.timedelta(days=4)
    week_label = f"{next_mon.strftime('%m/%d')}〜{next_fri.strftime('%m/%d')}"

    print(f"[日曜レポート] 来週の注目ポイント ({week_label})")

    md = _get_market_data()
    vix = md.get('vix')
    n225_close = md.get('n225_close')
    n225_week_chg = md.get('n225_week_chg')

    # 来週の心構えコメント
    if n225_week_chg is not None and n225_week_chg >= 2:
        outlook = (
            "今週は上昇しました。来週は利食い売りが出やすいタイミング。\n"
            "高値圏でのエントリーは慎重に。押し目を待つ作戦も有効です。"
        )
    elif n225_week_chg is not None and n225_week_chg <= -2:
        outlook = (
            "今週は下落しました。来週は反発狙いが考えられますが、\n"
            "まず月曜の地合いを確認してから。焦りは禁物です。"
        )
    else:
        outlook = (
            "横ばい〜小幅の動きでした。来週はレンジ抜けを狙いたい。\n"
            "朝の地合いチェックを必ず実施してからエントリーを判断しましょう。"
        )

    # VIXコメント
    vix_comment = ""
    if vix:
        if vix > 30:
            vix_comment = f"⚠ VIX {vix:.1f} — 恐怖指数が高い。来週は大きな動きに備えて。"
        elif vix > 20:
            vix_comment = f"🟡 VIX {vix:.1f} — やや不安定。ストップロスを必ず設定。"
        else:
            vix_comment = f"🟢 VIX {vix:.1f} — 落ち着いた環境。チャンスを冷静に探ろう。"

    checklist = (
        "**来週の準備チェックリスト**\n"
        "☑ 月曜朝の通知で地合い確認\n"
        "☑ 通過銘柄は昼・夜の通知でフォロー\n"
        "☑ エントリー前に Q1〜Q11 再確認\n"
        "☑ ストップロス(損切りライン)を先に決める"
    )

    n225_line = (
        f"**日経225 前週終値**: {n225_close:,.0f}円  (週次 {_chg_str(n225_week_chg)})"
        if n225_close else ""
    )

    desc = "\n".join(filter(None, [
        n225_line, vix_comment, "",
        outlook, "", checklist,
    ]))

    payload = {
        'content': f"🗓 **来週({week_label})の注目ポイント**",
        'embeds': [{
            'title': '📋 週初めの準備',
            'description': desc,
            'color': 0x5865F2,  # Discord blurple
            'footer': {'text': 'ゆっくり休んで、月曜に備えましょう。'},
        }],
    }
    ok, msg = _post(payload, webhook)
    print(f"  {'✅' if ok else '❌'} {msg}")
    if not ok:
        sys.exit(1)


# ──────────────────────────────────────────────────────────
# エントリーポイント
# ──────────────────────────────────────────────────────────

def main():
    mode = os.environ.get('NOTIFY_MODE', 'weekend_sat').strip()
    print(f"=== 週末通知 [{mode}] {datetime.datetime.utcnow().isoformat()} UTC ===")

    webhook = os.environ.get('DISCORD_WEBHOOK_URL', '').strip()
    if not webhook:
        print("ERROR: 環境変数 DISCORD_WEBHOOK_URL が設定されていません")
        sys.exit(1)

    if mode == 'weekend_sun':
        sunday_report(webhook)
    else:
        saturday_report(webhook)

    print("=== 完了 ===")


if __name__ == '__main__':
    main()
