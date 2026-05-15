"""
日本株スイング Discord 通知モジュール

swing_analyzer.py の分析結果や、watchlist の状況を
Discord Webhook 経由で iPhone にプッシュ通知します。

設定方法:
  1. Discord でチャンネルの「インテグレーション」→「ウェブフック」を作成
  2. Webhook URL をコピー
  3. このフォルダに `discord_config.txt` を作って URL を1行で保存
     または環境変数 DISCORD_WEBHOOK_URL に設定

CRITICAL:
  discord_config.txt は .gitignore に登録(GitHubに公開されません)
"""
import os
import sys
import json
import urllib.request
import urllib.error
import datetime


CONFIG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'discord_config.txt')


# ──────────────────────────────────────────────────
# Webhook URL の取得・保存
# ──────────────────────────────────────────────────

def get_webhook_url():
    """URL を以下の優先順位で取得:
    1. Streamlit secrets (クラウド版)
    2. 環境変数 DISCORD_WEBHOOK_URL (GitHub Actions / ローカル上書き)
    3. ローカル設定ファイル (PC運用)
    """
    # 1. Streamlit secrets
    try:
        import streamlit as st
        if hasattr(st, 'secrets'):
            try:
                url = st.secrets.get('DISCORD_WEBHOOK_URL', '')
                if url:
                    return str(url).strip()
            except Exception:
                pass
    except ImportError:
        pass

    # 2. 環境変数
    url = os.environ.get('DISCORD_WEBHOOK_URL', '').strip()
    if url:
        return url

    # 3. ローカルファイル
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                url = f.read().strip()
                if url and 'discord' in url and '/webhooks/' in url:
                    return url
        except Exception:
            pass
    return None


def save_webhook_url(url):
    """ローカル設定ファイルに URL を保存"""
    url = url.strip()
    valid_prefixes = (
        'https://discord.com/api/webhooks/',
        'https://discordapp.com/api/webhooks/',
        'https://canary.discord.com/api/webhooks/',
        'https://ptb.discord.com/api/webhooks/',
    )
    if not any(url.startswith(p) for p in valid_prefixes):
        return False, (
            "URLの形式が違います。\n"
            "  Discord Webhook URL は以下のいずれかの形です:\n"
            "    https://discord.com/api/webhooks/...\n"
            "    https://discordapp.com/api/webhooks/..."
        )
    try:
        with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
            f.write(url + '\n')
        return True, f"保存しました: {CONFIG_FILE}"
    except Exception as e:
        return False, f"保存失敗: {e}"


# ──────────────────────────────────────────────────
# 低レベル送信
# ──────────────────────────────────────────────────

def _post(payload, webhook_url=None):
    url = webhook_url or get_webhook_url()
    if not url:
        return False, "Webhook URLが未設定です"

    data = json.dumps(payload, ensure_ascii=False).encode('utf-8')
    req = urllib.request.Request(
        url, data=data,
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


def send_text(message, webhook_url=None):
    """単純なテキストメッセージを送信"""
    return _post({'content': message}, webhook_url)


# ──────────────────────────────────────────────────
# 色設定
# ──────────────────────────────────────────────────

def _color_for_q1(status):
    return {
        'go':      0x1D9E75,  # green
        'caution': 0xEF9F27,  # amber
        'stop':    0xE24B4A,  # red
    }.get(status, 0x888780)


def _color_for_grade(grade):
    return {
        'S': 0xFFD700,  # gold
        'A': 0xC0C0C0,  # silver
        'B': 0xCD7F32,  # bronze
        'C': 0x888780,  # gray
    }.get(grade, 0x888780)


# ──────────────────────────────────────────────────
# 分析結果通知(swing_analyzer.py 用)
# ──────────────────────────────────────────────────

def send_analysis_result(passed, q1_status, q1_msg, today_str, webhook_url=None):
    """swing_analyzer.py の分析結果を Discord に送信。

    Args:
        passed: 通過銘柄のリスト(各銘柄は dict、swing_analyzer の出力形式)
        q1_status: 'go' / 'caution' / 'stop'
        q1_msg: Q1判定のメッセージ
        today_str: '2026-05-15' 形式の日付
        webhook_url: 任意で上書き
    """
    embeds = []

    # Q1 マーケット環境
    q1_label = {
        'go':      '🟢 強気 — 通常運用OK',
        'caution': '🟡 中立 — 慎重に',
        'stop':    '🔴 弱気 — 全面見送り',
    }.get(q1_status, '? 不明')
    embeds.append({
        'title': '🌐 Q1 日経地合い判定',
        'description': f'**{q1_label}**\n```{q1_msg}```',
        'color': _color_for_q1(q1_status),
    })

    # 通過銘柄
    if not passed:
        embeds.append({
            'title': '🎯 本日の通過銘柄',
            'description': '今日は条件に合う銘柄が見つかりませんでした。\n何もしないことは、立派な利益確定です。',
            'color': 0x888780,
        })
    else:
        # スコア順にソート(既にソート済みの想定だが念のため)
        ranked = sorted(passed, key=lambda x: x.get('score', 0), reverse=True)
        for rank, r in enumerate(ranked[:10], 1):
            code = r.get('_code', '?')
            name = r.get('_name', '?')
            score = r.get('score', 0)
            grade = r.get('grade', '?')
            close = r.get('close', 0)
            q8 = r.get('q8') or {}

            # Q8 アイコン
            q8_v = q8.get('verdict', 'unknown')
            q8_icon = {
                'danger':   '⚠ 危険KW',
                'positive': '★ ポジ',
                'unknown':  '? 要確認',
                'ok':       '✓ OK',
            }.get(q8_v, '?')

            # 判定詳細
            lines = [
                f"**株価**: {close:,.0f}円",
                f"**Q4** 移動平均配列: {r.get('q4', '?').split(' ', 1)[-1] if r.get('q4') else '?'}",
                f"**Q5** 200日線: {r.get('q5', '?').split(' ', 1)[-1] if r.get('q5') else '?'}",
                f"**Q6** 出来高: {r.get('q6', '?').split(' ', 1)[-1] if r.get('q6') else '?'}",
                f"**Q7** 25日線乖離: {r.get('q7', '?').split(' ', 1)[-1] if r.get('q7') else '?'}",
                f"**Q8** ニュース: {q8_icon}",
                f"**Q9** 決算: {r.get('q9_msg', '?')[:40]}",
                f"**Q10** ギャップ: {r.get('q10', '?').split(' ', 1)[-1] if r.get('q10') else '?'}",
                f"**Q11** 3M高値: {r.get('q11', '?')}",
            ]

            embed = {
                'title': f"#{rank}  {code} {name}  {score}点 [{grade}]",
                'description': '\n'.join(lines),
                'color': _color_for_grade(grade),
            }
            embeds.append(embed)

        if len(ranked) > 10:
            embeds.append({
                'description': f"他 {len(ranked) - 10} 銘柄あり(HTMLレポートで確認)",
                'color': 0x888780,
            })

    content = f"📅 **{today_str}** 日本株スイング 分析結果"
    payload = {'content': content, 'embeds': embeds[:10]}
    return _post(payload, webhook_url)


# ──────────────────────────────────────────────────
# watchlist 監視結果通知(notify_daily.py 用)
# ──────────────────────────────────────────────────

def send_watchlist_status(q1_status, q1_msg, watch_results, today_str, webhook_url=None):
    """watchlist の銘柄について Q1+Q4-Q11 を再チェックした結果を送信。

    Args:
        watch_results: [{'code', 'name', 'all_pass', 'q4'-'q11' などの結果}, ...]
        その他は send_analysis_result と同じ
    """
    embeds = []

    # Q1
    q1_label = {
        'go':      '🟢 強気',
        'caution': '🟡 中立',
        'stop':    '🔴 弱気',
    }.get(q1_status, '?')
    embeds.append({
        'title': f'🌐 Q1 日経地合い: {q1_label}',
        'description': q1_msg,
        'color': _color_for_q1(q1_status),
    })

    # watchlist 集計
    if not watch_results:
        embeds.append({
            'title': '👀 ウォッチリスト',
            'description': 'ウォッチリストが空です。\nまずは run_analysis.bat で財務スクリーニングを実行し、通過銘柄を登録してください。',
            'color': 0x888780,
        })
    else:
        # 通過(再ヒット)とそれ以外を分ける
        passing = [r for r in watch_results if r.get('all_pass')]
        not_passing = [r for r in watch_results if not r.get('all_pass') and not r.get('_error')]
        errors = [r for r in watch_results if r.get('_error')]

        if passing:
            field_lines = []
            for r in passing[:15]:
                q8 = r.get('q8') or {}
                q8_v = q8.get('verdict', 'unknown')
                q8_icon = {
                    'danger': '⚠', 'positive': '★', 'unknown': '?', 'ok': '✓'
                }.get(q8_v, '?')
                close = r.get('close', 0)
                field_lines.append(
                    f"**{r['code']} {r['name']}**  {close:,.0f}円  Q8:{q8_icon}"
                )
            embeds.append({
                'title': f'✅ 条件キープ中({len(passing)}銘柄)',
                'description': '\n'.join(field_lines),
                'color': 0x1D9E75,
            })

        if not_passing:
            lines = []
            for r in not_passing[:15]:
                fails = []
                if not r.get('q4_pass', True): fails.append('Q4')
                if not r.get('q5_pass', True): fails.append('Q5')
                if not r.get('q6_pass', True): fails.append('Q6')
                if not r.get('q7_pass', True): fails.append('Q7')
                if not r.get('q10_pass', True): fails.append('Q10')
                lines.append(f"{r['code']} {r['name']}  ✗ NG: {', '.join(fails) or '?'}")
            embeds.append({
                'title': f'⚠ 条件離脱({len(not_passing)}銘柄)',
                'description': '\n'.join(lines),
                'color': 0xEF9F27,
            })

        if errors:
            err_lines = [f"{r['code']} {r['name']}: {r.get('_error', '?')[:30]}"
                         for r in errors[:10]]
            embeds.append({
                'title': f'? データ取得失敗({len(errors)}銘柄)',
                'description': '\n'.join(err_lines),
                'color': 0x888780,
            })

    content = f"📅 **{today_str}** ウォッチリスト日次チェック"
    payload = {'content': content, 'embeds': embeds[:10]}
    return _post(payload, webhook_url)


# ──────────────────────────────────────────────────
# テスト送信
# ──────────────────────────────────────────────────

def test_notification(webhook_url=None):
    """テストメッセージを送信して接続確認"""
    today = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    payload = {
        'content': f"✅ **日本株スイング Discord 通知テスト** ({today})",
        'embeds': [{
            'title': '接続テスト成功',
            'description': 'この通知が見えていれば設定OKです。\nこれから毎日、分析結果がこのチャンネルに届きます。',
            'color': 0x1D9E75,
            'footer': {'text': '日本株スイングプロジェクト'},
        }],
    }
    return _post(payload, webhook_url)


# ──────────────────────────────────────────────────
# CLI(対話メニュー)
# ──────────────────────────────────────────────────

def cli_menu():
    os.system('cls' if os.name == 'nt' else 'clear')
    print("=" * 65)
    print("   📨  Discord 通知設定")
    print("=" * 65)
    print()
    url = get_webhook_url()
    if url:
        masked = url[:40] + "..." if len(url) > 40 else url
        print(f"  ✅  Webhook URL 設定済み: {masked}")
    else:
        print("  ⚠️  Webhook URL 未設定")
    print()
    print("""  1. Webhook URL を設定する
  2. テスト送信(接続確認)
  0. 戻る
""")
    choice = input("  選択: ").strip()

    if choice == '1':
        print()
        print("  Discord Webhook URL を貼り付けてください")
        print("  (https://discord.com/api/webhooks/... の形式)")
        new_url = input("  URL: ").strip()
        if not new_url:
            print("  キャンセルしました。")
        else:
            ok, msg = save_webhook_url(new_url)
            print(f"  {'✅' if ok else '⚠️'}  {msg}")
        input("\n  Enterキーで戻る...")
        return cli_menu()

    elif choice == '2':
        if not get_webhook_url():
            print("\n  ⚠️  先に Webhook URL を設定してください。")
        else:
            print("\n  📨  テスト送信中...")
            ok, msg = test_notification()
            print(f"  {'✅' if ok else '⚠️'}  {msg}")
            if ok:
                print("  Discord のチャンネルを確認してください!")
        input("\n  Enterキーで戻る...")
        return cli_menu()


if __name__ == '__main__':
    cli_menu()
