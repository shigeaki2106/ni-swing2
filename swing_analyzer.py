#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  シゲアキ専用 スイングトレード 自動分析ツール
  13の関門 チャート系 自動判定 (Q1, Q4-Q7, Q10, Q11)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  使い方:
    1. 楽天証券スーパースクリーナーからCSVをダウンロード
    2. このスクリプトと同じフォルダにCSVを置く
    3. run_analysis.bat をダブルクリック

  必要なもの:
    - Python 3.8以上
    - yfinance ライブラリ (setup.bat で自動インストール)
"""

import sys
import os
import re
import glob
import json
import io
import base64
import pandas as pd
import numpy as np
import yfinance as yf
from datetime import datetime, date, timedelta

try:
    import matplotlib
    matplotlib.use('Agg')           # ウィンドウを出さないバックエンド
    import matplotlib.pyplot as plt
    import matplotlib.dates as mdates
    # Windows で日本語フォントを試みる (失敗してもクラッシュしない)
    for _jfont in ['MS Gothic', 'Yu Gothic', 'Meiryo', 'IPAexGothic']:
        try:
            matplotlib.rcParams['font.family'] = _jfont
            break
        except Exception:
            pass
    matplotlib.rcParams['axes.unicode_minus'] = False
    _MATPLOTLIB_OK = True
except ImportError:
    _MATPLOTLIB_OK = False

# ============================================================
#  設定値（変更しないこと）
# ============================================================
MIN_REVENUE_GROWTH = 20.0   # 売上高成長率 最低ライン (%)
MIN_PROFIT_GROWTH  = 20.0   # 経常利益成長率 最低ライン (%)
MIN_EQUITY_RATIO   = 50.0   # 自己資本比率 最低ライン (%)
MIN_MARKET_CAP     = 10000  # 時価総額 下限 (百万円) = 100億円
MAX_MARKET_CAP     = 150000 # 時価総額 上限 (百万円) = 1500億円
MAX_DAY_CHANGE_PCT = 5.0    # 当日騰落 除外ライン (%)
MA25_CAUTION_PCT   = 5.0    # 25日線乖離 注意ライン (%)
MA25_REJECT_PCT    = 15.0   # 25日線乖離 却下ライン (%)
GAP_REJECT_PCT     = 5.0    # ギャップ却下ライン (%)
WATCHLIST_FILE     = "watchlist.json"

# ============================================================
#  Q8 ニュース警告キーワード
# ============================================================

# 1つでも引っかかると【見送り】候補（目視確認を促す）
Q8_DANGER_KEYWORDS = [
    '不祥事', '粉飾', '行政処分', '公募増資', '第三者割当', '増資',
    '下方修正', '業績修正', '不正', '訴訟', '大量退任', '経営交代',
    '業績悪化', '赤字転落', '特別損失', '業績予想を修正', '経営陣',
]

# 確認できたら好材料（スコア加点の参考）
Q8_POSITIVE_KEYWORDS = [
    '上方修正', '自社株買い', '増配', '株式分割', '大型受注',
    '資本提携', 'M&A', '黒字転換', '好業績',
]


# ============================================================
#  スコア計算（通過銘柄の優先順位付け）
# ============================================================

def compute_score(result):
    """
    各項目の「強さ」を数値化して合計スコア（100点満点）を返す。

    Q4  移動平均配列の余裕 : 25点
    Q5  200日線の上昇率    : 15点
    Q6  出来高比率         : 20点
    Q7  25日線乖離の適切さ : 25点
    Q10 ギャップの小ささ   : 15点
    """
    scores = {}

    # Q4: MAの間隔が広いほど高スコア（パーフェクトオーダー前提）
    margin = result.get('_ma_margin', 0) * 100  # %に変換
    if   margin >= 15: scores['Q4'] = 25
    elif margin >= 10: scores['Q4'] = 20
    elif margin >=  5: scores['Q4'] = 15
    else:              scores['Q4'] = 10

    # Q5: 200日線が30日間でどれだけ上昇したか
    ma200g = result.get('_ma200_growth_pct', 0)
    if   ma200g >= 5: scores['Q5'] = 15
    elif ma200g >= 3: scores['Q5'] = 12
    elif ma200g >= 1: scores['Q5'] = 8
    else:             scores['Q5'] = 5

    # Q6: 上昇日/下落日の出来高比率
    vr = result.get('_vol_ratio', 1)
    if   vr >= 2.5: scores['Q6'] = 20
    elif vr >= 2.0: scores['Q6'] = 17
    elif vr >= 1.5: scores['Q6'] = 13
    elif vr >= 1.3: scores['Q6'] = 10
    else:           scores['Q6'] = 5   # 0.8〜1.3のケース（条件付き通過）

    # Q7: 25日線乖離（0〜2%が理想、離れるほど減点）
    dist = result.get('_dist_pct', 0)
    if   dist < 0:    scores['Q7'] = 10  # 25日線以下（慎重に）
    elif dist <= 2:   scores['Q7'] = 25  # 理想圏
    elif dist <= 5:   scores['Q7'] = 20
    elif dist <= 8:   scores['Q7'] = 13
    elif dist <= 15:  scores['Q7'] = 7
    else:             scores['Q7'] = 0   # 見送り（ここには来ないはず）

    # Q10: ギャップが小さいほど高スコア
    gap = abs(result.get('_gap_pct', 0))
    if   gap <= 0.5: scores['Q10'] = 15
    elif gap <= 1.0: scores['Q10'] = 12
    elif gap <= 2.0: scores['Q10'] = 9
    elif gap <= 3.0: scores['Q10'] = 6
    else:            scores['Q10'] = 3   # 3〜5%（ギリギリ通過）

    total = sum(scores.values())
    if   total >= 80: grade = "S"
    elif total >= 65: grade = "A"
    elif total >= 50: grade = "B"
    else:             grade = "C"

    return total, grade, scores


# ============================================================
#  Q9：次回決算日チェック
# ============================================================

def business_days_until(target_date):
    """今日から target_date までの営業日数（土日を除く）を返す"""
    today = date.today()
    if target_date <= today:
        return 0
    count = 0
    d = today
    while d < target_date:
        d += timedelta(days=1)
        if d.weekday() < 5:  # 月〜金
            count += 1
    return count


def check_earnings_q9(code):
    """
    yfinance から次回決算日を取得し、3営業日以内かどうかを判定する。

    戻り値: (status, message)
      status: "safe"   → 安全（決算まで余裕あり）
              "danger" → 見送り推奨（3営業日以内）
              "unknown"→ データなし（手動確認が必要）
    """
    try:
        ticker = yf.Ticker(f"{code}.T")
        earnings_date = None

        # yfinance のバージョンによって形式が異なるため複数パターン対応
        try:
            cal = ticker.calendar
            if cal is not None:
                if isinstance(cal, dict):
                    raw = cal.get("Earnings Date")
                    if raw:
                        earnings_date = raw[0] if isinstance(raw, list) else raw
                elif hasattr(cal, "loc") and "Earnings Date" in cal.index:
                    earnings_date = cal.loc["Earnings Date"].iloc[0]
                elif hasattr(cal, "columns") and "Earnings Date" in cal.columns:
                    earnings_date = cal["Earnings Date"].iloc[0]
        except Exception:
            pass

        # earnings_dates プロパティも試す（新しいyfinance）
        if earnings_date is None:
            try:
                ed = ticker.earnings_dates
                if ed is not None and not ed.empty:
                    future = ed[ed.index.tz_localize(None) > datetime.now()] if ed.index.tzinfo else ed[ed.index > datetime.now()]
                    if not future.empty:
                        earnings_date = future.index[0]
            except Exception:
                pass

        if earnings_date is None:
            return "unknown", "決算日データなし → 楽天証券で手動確認"

        # datetime/Timestamp → date 型に変換
        if hasattr(earnings_date, "date"):
            earnings_date = earnings_date.date()
        elif isinstance(earnings_date, str):
            earnings_date = datetime.strptime(earnings_date[:10], "%Y-%m-%d").date()

        bdays = business_days_until(earnings_date)

        if bdays <= 3:
            return "danger", f"決算 {earnings_date} まで {bdays}営業日 → 見送り推奨"
        else:
            return "safe", f"決算 {earnings_date} まで {bdays}営業日 → 安全"

    except Exception as e:
        return "unknown", f"取得エラー ({e}) → 手動確認"


# ============================================================
#  D: Q8 ニュース半自動チェック
# ============================================================

def check_news_q8(code, name):
    """
    yfinance の ticker.news を使い、Q8（ニュース・材料）を半自動チェックする。

    戻り値 dict:
      news_list     : 最新15件のニュースリスト (各要素: title/date/flag/found_keywords)
      danger_count  : 危険キーワードが含まれたニュース数
      positive_count: ポジティブキーワードが含まれたニュース数
      verdict       : 'danger' / 'positive' / 'ok' / 'unknown'
      found_danger  : 検出された危険キーワードのリスト
      found_positive: 検出されたポジティブキーワードのリスト
      error         : エラーメッセージ (正常時はNone)

    ⚠ yfinance のニュースは英語ヘッドラインが多い。
      日本語記事はヒット率が下がるため、0件でも「安全」ではなく
      「取得できなかった可能性がある」と扱う。
      最終確認は楽天証券Webで目視することを推奨。
    """
    result = {
        'news_list': [],
        'danger_count': 0,
        'positive_count': 0,
        'verdict': 'ok',
        'found_danger': [],
        'found_positive': [],
        'error': None,
    }

    try:
        ticker = yf.Ticker(f"{code}.T")
        news = ticker.news

        if not news:
            result['error'] = 'ニュース0件（yfinanceで取得できず）'
            result['verdict'] = 'unknown'
            return result

        for item in news[:15]:
            if not isinstance(item, dict):
                continue

            # yfinanceバージョンによるキー差異を吸収
            title = (item.get('title')
                     or item.get('Title')
                     or (item.get('content') or {}).get('title', ''))
            pub_raw = (item.get('providerPublishTime')
                       or item.get('published')
                       or (item.get('content') or {}).get('pubDate'))

            if not title:
                continue

            # 日付文字列を生成
            date_str = ''
            if pub_raw:
                try:
                    if isinstance(pub_raw, (int, float)):
                        date_str = datetime.fromtimestamp(pub_raw).strftime('%Y/%m/%d')
                    else:
                        date_str = str(pub_raw)[:10]
                except Exception:
                    date_str = ''

            # キーワードスキャン
            found_d = [kw for kw in Q8_DANGER_KEYWORDS  if kw in title]
            found_p = [kw for kw in Q8_POSITIVE_KEYWORDS if kw in title]

            if found_d:
                flag = 'danger'
                result['danger_count'] += 1
                result['found_danger'].extend(found_d)
            elif found_p:
                flag = 'positive'
                result['positive_count'] += 1
                result['found_positive'].extend(found_p)
            else:
                flag = 'neutral'

            result['news_list'].append({
                'title': title,
                'date': date_str,
                'flag': flag,
                'found_keywords': found_d or found_p,
            })

        # verdict
        if result['danger_count'] > 0:
            result['verdict'] = 'danger'
        elif result['positive_count'] > 0:
            result['verdict'] = 'positive'
        else:
            result['verdict'] = 'ok'

    except Exception as e:
        result['error'] = str(e)
        result['verdict'] = 'unknown'

    return result


# ============================================================
#  紙トレ記録テンプレート自動生成
# ============================================================

def generate_trade_template(script_dir, r, today_str):
    """
    通過銘柄 1件につき紙トレ記録テンプレートを1ファイル生成する。
    ファイル名: trades/trade_YYYYMMDD_コード_銘柄名.txt
    """
    trades_dir = os.path.join(script_dir, "trades")
    os.makedirs(trades_dir, exist_ok=True)

    code  = r['_code']
    name  = r['_name']
    score = r.get('score', 0)
    grade = r.get('grade', '?')
    sd    = r.get('score_detail', {})

    safe_name = re.sub(r'[\\/:*?"<>|]', '', name)  # ファイル名に使えない文字を除去
    filename  = os.path.join(trades_dir, f"trade_{today_str.replace('-','')}_{code}_{safe_name}.txt")

    # 既に同じファイルがあれば上書きしない（当日2回実行した場合）
    if os.path.exists(filename):
        return filename

    content = f"""================================================================
  紙トレ記録  {today_str}
  {code} {name}
================================================================

【スクリーニング結果】
  Score : {score}点 / 100点  [{grade}ランク]
          Q4(MA配列)+{sd.get('Q4',0)}  Q5(200日線)+{sd.get('Q5',0)}  Q6(出来高)+{sd.get('Q6',0)}  Q7(乖離)+{sd.get('Q7',0)}  Q10(ギャップ)+{sd.get('Q10',0)}

  Q4  移動平均配列 : {r.get('q4','---')}
  Q5  200日線方向  : {r.get('q5','---')}
  Q6  出来高の質   : {r.get('q6','---')}
  Q7  25日線乖離   : {r.get('q7','---')}
  Q9  決算日       : {r.get('q9_msg','手動確認')}
  Q10 ギャップ     : {r.get('q10','---')}
  Q11 3ヶ月高値    : {r.get('q11','---')}

  移動平均線:
    MA25  = {r.get('ma25',0):,.0f}円
    MA50  = {r.get('ma50',0):,.0f}円
    MA150 = {r.get('ma150',0):,.0f}円
    MA200 = {r.get('ma200',0):,.0f}円

  スクリーニング時の株価 : {r.get('close',0):,.0f}円

----------------------------------------------------------------
【エントリー記録】（ここを自分で入力）

  エントリー日時  :
  エントリー価格  :          円
  株数            :          株
  損切りライン    :          円  （ -     円 /  -   % ）
  目標価格        :          円  （ +     円 /  +   % ）

  エントリー根拠（Q8確認内容・チャートの形など）:


  エントリー時の気持ち（正直に）:


----------------------------------------------------------------
【決済記録】（決済後に入力）

  決済日時        :
  決済価格        :          円
  損益            :          円  （      % ）
  保有日数        :          日

  決済理由        :


  反省・学び      :


================================================================
"""

    with open(filename, "w", encoding="utf-8") as f:
        f.write(content)

    return filename


# ============================================================
#  ウォッチリスト管理
# ============================================================

def load_watchlist(script_dir):
    """watchlist.json を読み込む。なければ空で返す。"""
    path = os.path.join(script_dir, WATCHLIST_FILE)
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_watchlist(script_dir, wl):
    """watchlist.json に書き込む。"""
    path = os.path.join(script_dir, WATCHLIST_FILE)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(wl, f, ensure_ascii=False, indent=2)


def update_watchlist(script_dir, passed_today, today_str):
    """
    本日の通過銘柄でウォッチリストを更新する。

    ロジック：
    - 前回通過 AND 今回通過 → streak +1
    - 前回未通過 AND 今回通過 → streak = 1（新規/復活）
    - 前回通過 AND 今回未通過 → streak = 0（途切れ）
    """
    wl = load_watchlist(script_dir)
    meta = wl.get("_meta", {"last_run_date": None, "passed_last_run": []})
    passed_last = set(meta.get("passed_last_run", []))
    today_codes = {r['_code'] for r in passed_today}

    # 今回通過した銘柄を更新
    for r in passed_today:
        code = r['_code']
        entry = wl.get(code, {
            "name": r['_name'],
            "streak": 0,
            "best_streak": 0,
            "total_passes": 0,
            "first_seen": today_str,
            "last_seen": today_str,
        })
        # 前回も通過していれば連続、そうでなければリセット
        entry["streak"] = entry.get("streak", 0) + 1 if code in passed_last else 1
        entry["best_streak"] = max(entry.get("best_streak", 0), entry["streak"])
        entry["total_passes"] = entry.get("total_passes", 0) + 1
        entry["last_seen"] = today_str
        entry["name"] = r['_name']
        wl[code] = entry

    # 前回通過したが今回通過しなかった銘柄はストリークをリセット
    for code in passed_last:
        if code not in today_codes and code in wl:
            wl[code]["streak"] = 0

    # メタ情報を更新
    wl["_meta"] = {
        "last_run_date": today_str,
        "passed_last_run": list(today_codes),
    }
    save_watchlist(script_dir, wl)
    return wl


def _q8_summary(q8):
    """Q8 結果を1行テキストに変換するユーティリティ"""
    if q8 is None:
        return '未確認'
    v = q8.get('verdict', 'unknown')
    if v == 'danger':
        kws = ', '.join(set(q8.get('found_danger', [])))
        return f'[WARN] 危険KW検出: {kws}  ← 楽天証券で要目視確認'
    elif v == 'positive':
        kws = ', '.join(set(q8.get('found_positive', [])))
        return f'[OK] ポジティブ: {kws}'
    elif v == 'unknown':
        err = q8.get('error', '取得不可')
        return f'[?] {err}  ← 楽天証券で手動確認'
    else:
        n = len(q8.get('news_list', []))
        return f'[OK] 危険KWなし ({n}件スキャン)'


def display_watchlist(wl, today_str):
    """ウォッチリストをコンソールに表示する。"""
    entries = {k: v for k, v in wl.items() if k != "_meta"}
    if not entries:
        return

    meta         = wl.get("_meta", {})
    passed_last  = set(meta.get("passed_last_run", []))

    # 連続通過中（streak > 0）
    active = sorted(
        [(k, v) for k, v in entries.items() if v.get("streak", 0) > 0],
        key=lambda x: x[1]["streak"],
        reverse=True,
    )
    # 前回は通過していたが今回途切れた銘柄
    broken = [(k, v) for k, v in entries.items()
              if v.get("streak", 0) == 0 and k in passed_last]

    print("\n" + "=" * 65)
    print("  ウォッチリスト")
    print("=" * 65)

    if active:
        print("\n  連続通過中の銘柄:")
        for code, e in active:
            streak    = e["streak"]
            stars     = "*" * min(streak, 5)  # 最大5個
            is_new    = e.get("first_seen") == today_str
            new_label = "  <- 本日初登場" if is_new else f"  (初回: {e.get('first_seen', '?')})"
            best      = e.get("best_streak", streak)
            print(f"  [{stars:<5}] {code} {e['name']}  {streak}日連続"
                  f"  (最長記録: {best}日){new_label}")
    else:
        print("\n  現在連続通過中の銘柄はありません。")

    if broken:
        print("\n  本日ストリーク途切れ:")
        for code, e in broken:
            print(f"  [-----] {code} {e['name']}  "
                  f"最長{e.get('best_streak', '?')}日  累計{e.get('total_passes', '?')}回通過")

    total_watched = len([k for k in entries if entries[k].get("total_passes", 0) > 0])
    print(f"\n  累計ウォッチ銘柄数: {total_watched}銘柄")


# ============================================================
#  コンソールとファイルに同時に出力するクラス
# ============================================================

class Tee:
    def __init__(self, *streams):
        self.streams = streams
    def write(self, data):
        for s in self.streams:
            try:
                s.write(data)
            except Exception:
                pass
    def flush(self):
        for s in self.streams:
            try:
                s.flush()
            except Exception:
                pass


# ============================================================
#  ユーティリティ関数
# ============================================================

def clean_number(val):
    """数値文字列をfloatに変換（カンマ・符号・%記号を除去）"""
    if pd.isna(val):
        return None
    s = str(val).replace(',', '').replace(' ', '').replace('+', '').replace('%', '')
    try:
        return float(s)
    except Exception:
        return None


def parse_day_change(val):
    """前日比の文字列からパーセントを抽出 例: "+13.0 (+0.84%) " → 0.84"""
    s = str(val)
    m = re.search(r'\(([+-]?\d+\.?\d*)%\)', s)
    if m:
        return float(m.group(1))
    try:
        return float(s.replace(',', '').replace('+', '').strip())
    except Exception:
        return 0.0


def load_csv(csv_path):
    """楽天証券CSVを読み込む（文字コードを自動判別）"""
    for encoding in ['cp932', 'utf-8-sig', 'utf-8']:
        try:
            df = pd.read_csv(csv_path, encoding=encoding)
            return df
        except Exception:
            continue
    raise ValueError(f"CSVを読み込めませんでした: {csv_path}")


def find_latest_csv(folder):
    """フォルダ内の最新CSVを返す（resultsフォルダは除外）"""
    pattern = os.path.join(folder, "*.csv")
    files = [f for f in glob.glob(pattern)
             if "results" not in f.replace("\\", "/")]
    if not files:
        return None
    return max(files, key=os.path.getmtime)


# ============================================================
#  Q1: 日経平均 地合い確認
# ============================================================

def check_nikkei_q1():
    try:
        nikkei = yf.Ticker("^N225")
        hist   = nikkei.history(period="3mo")
        if len(hist) < 30:
            return None, "データ不足"

        hist['MA25'] = hist['Close'].rolling(25).mean()
        latest   = hist.iloc[-1]
        ma25_now = hist['MA25'].dropna().iloc[-1]
        ma25_old = hist['MA25'].dropna().iloc[-10]

        above_ma25  = latest['Close'] > ma25_now
        ma25_rising = ma25_now > ma25_old

        if above_ma25 and ma25_rising:
            status = "go"
            label  = "① 強気 [GREEN] 25日線の上・上向き"
        elif above_ma25:
            status = "caution"
            label  = "② 中立 [YELLOW] 25日線の上・横ばい"
        else:
            status = "stop"
            label  = "⑤ 弱気 [RED] 25日線の下"

        msg = (f"{label}\n"
               f"     日経平均: {latest['Close']:,.0f}円 / "
               f"25日線: {ma25_now:,.0f}円")
        return status, msg

    except Exception as e:
        return None, f"取得エラー: {e}"


# ============================================================
#  Q4-Q7, Q10, Q11: 個別銘柄 チャート分析
# ============================================================

def analyze_chart(code):
    try:
        ticker = yf.Ticker(f"{code}.T")
        hist   = ticker.history(period="14mo")
        if len(hist) < 210:
            return None, f"データ不足({len(hist)}日)"

        hist['MA25']  = hist['Close'].rolling(25).mean()
        hist['MA50']  = hist['Close'].rolling(50).mean()
        hist['MA150'] = hist['Close'].rolling(150).mean()
        hist['MA200'] = hist['Close'].rolling(200).mean()

        last = hist.iloc[-1]
        prev = hist.iloc[-2]
        close = last['Close']
        ma25  = last['MA25']
        ma50  = last['MA50']
        ma150 = last['MA150']
        ma200 = last['MA200']

        # Q4: パーフェクトオーダー
        q4_pass = bool(close > ma50 > ma150 > ma200)
        if q4_pass:
            q4 = "① [OK] パーフェクトオーダー"
        elif close > ma50:
            q4 = "② [NG] 一部逆転あり"
        else:
            q4 = "③ [NG] バラバラ"

        # Q5: 200日線の方向
        ma200_30ago = hist['MA200'].dropna().iloc[-31] if len(hist['MA200'].dropna()) >= 31 else hist['MA200'].dropna().iloc[0]
        q5_pass = bool(ma200 > ma200_30ago)
        q5 = "① [OK] 上向き" if q5_pass else "③ [NG] 下向き/横ばい"

        # Q6: 出来高の質
        recent   = hist.tail(20).copy()
        recent['is_up'] = recent['Close'] >= recent['Open']
        up_vol   = recent.loc[recent['is_up'],  'Volume'].mean()
        down_vol = recent.loc[~recent['is_up'], 'Volume'].mean()
        ratio    = (up_vol / down_vol) if (not pd.isna(down_vol) and down_vol > 0) else 2.0

        if ratio >= 1.3:
            q6_pass = True
            q6 = f"① [OK] 上昇時に出来高増加 (比率{ratio:.1f}倍)"
        elif ratio >= 0.8:
            q6_pass = True
            q6 = f"③ [OK] 出来高横ばい (比率{ratio:.1f}倍)"
        else:
            q6_pass = False
            q6 = f"② [NG] 下落時に出来高増加 (比率{ratio:.1f}倍)"

        # Q7: 25日線乖離率
        dist = ((close - ma25) / ma25) * 100
        if dist > MA25_REJECT_PCT:
            q7_pass = False
            q7 = f"③ [NG] 過熱 {dist:+.1f}% ({MA25_REJECT_PCT}%超)"
        elif dist > MA25_CAUTION_PCT:
            q7_pass = True
            q7 = f"② [OK] やや過熱 {dist:+.1f}%"
        else:
            q7_pass = True
            q7 = f"① [OK] 健全 {dist:+.1f}%"

        # Q10: 本日のギャップ
        gap = ((last['Open'] - prev['Close']) / prev['Close']) * 100
        if gap >= GAP_REJECT_PCT:
            q10_pass = False
            q10 = f"② [NG] ギャップアップ {gap:+.1f}%"
        elif gap <= -GAP_REJECT_PCT:
            q10_pass = False
            q10 = f"③ [NG] ギャップダウン {gap:+.1f}%"
        else:
            q10_pass = True
            q10 = f"① [OK] 通常範囲 {gap:+.1f}%"

        # Q11: 直近3ヶ月高値
        high_3m   = hist.tail(63)['High'].max()
        near_high = close >= high_3m * 0.97
        q11 = f"{high_3m:,.0f}円{'  <- 高値近辺' if near_high else ''}"

        all_pass = q4_pass and q5_pass and q6_pass and q7_pass and q10_pass

        return {
            'q4': q4,   'q4_pass': q4_pass,
            'q5': q5,   'q5_pass': q5_pass,
            'q6': q6,   'q6_pass': q6_pass,
            'q7': q7,   'q7_pass': q7_pass,
            'q10': q10, 'q10_pass': q10_pass,
            'q11': q11,
            'all_pass': all_pass,
            'close': close,
            'ma25': ma25, 'ma50': ma50, 'ma150': ma150, 'ma200': ma200,
            # スコア計算用の生データ
            '_ma200_growth_pct': (ma200 / ma200_30ago - 1) * 100,
            '_vol_ratio': ratio,
            '_dist_pct': dist,
            '_gap_pct': gap,
            '_ma_margin': (close/ma50 - 1) + (ma50/ma150 - 1) + (ma150/ma200 - 1),
        }, None

    except Exception as e:
        return None, str(e)


# ============================================================
#  分析本体
# ============================================================

# ============================================================
#  C: HTML レポート生成
# ============================================================

def _make_chart_b64(code, name, score, grade, q8_verdict):
    """
    個別銘柄の株価チャートを matplotlib で描き、base64 PNG 文字列を返す。
    失敗した場合は None を返す（呼び出し側でスキップ）。

    レイアウト: 上段=株価+MA線、下段=出来高バー
    """
    if not _MATPLOTLIB_OK:
        return None
    try:
        ticker = yf.Ticker(f"{code}.T")
        hist   = ticker.history(period="14mo")
        if len(hist) < 60:
            return None

        # 移動平均を計算してから表示範囲を直近6ヶ月に絞る
        hist['MA25']  = hist['Close'].rolling(25).mean()
        hist['MA50']  = hist['Close'].rolling(50).mean()
        hist['MA150'] = hist['Close'].rolling(150).mean()
        hist['MA200'] = hist['Close'].rolling(200).mean()
        hist = hist.tail(125)   # 約6ヶ月分

        # 3ヶ月高値
        high_3m = hist.tail(65)['High'].max()

        # 出来高の色（上昇日=緑、下落日=赤）
        colors = ['#26a69a' if c >= o else '#ef5350'
                  for c, o in zip(hist['Close'], hist['Open'])]

        fig, (ax1, ax2) = plt.subplots(
            2, 1,
            figsize=(12, 6),
            gridspec_kw={'height_ratios': [3, 1]},
            facecolor='#1e1e1e',
        )
        for ax in (ax1, ax2):
            ax.set_facecolor('#1e1e1e')
            ax.tick_params(colors='#cccccc', labelsize=8)
            for spine in ax.spines.values():
                spine.set_edgecolor('#444444')

        idx = hist.index
        # 株価・MA線
        ax1.plot(idx, hist['Close'], color='#e0e0e0', linewidth=1.4, label='株価')
        ax1.plot(idx, hist['MA25'],  color='#ff9800', linewidth=1.2, label='MA25')
        ax1.plot(idx, hist['MA50'],  color='#f44336', linewidth=1.0, label='MA50')
        ax1.plot(idx, hist['MA150'], color='#4caf50', linewidth=1.0, label='MA150')
        ax1.plot(idx, hist['MA200'], color='#9c27b0', linewidth=1.0,
                 linestyle='--', label='MA200')
        # 3ヶ月高値ライン
        ax1.axhline(y=high_3m, color='#78909c', linewidth=0.8,
                    linestyle=':', label=f'3M高値 {high_3m:,.0f}')

        ax1.legend(loc='upper left', fontsize=7,
                   facecolor='#2a2a2a', edgecolor='#555', labelcolor='#cccccc',
                   framealpha=0.8)
        ax1.xaxis.set_major_formatter(mdates.DateFormatter('%m/%d'))
        ax1.xaxis.set_major_locator(mdates.MonthLocator())
        ax1.tick_params(axis='x', labelbottom=False)
        ax1.yaxis.set_major_formatter(
            matplotlib.ticker.FuncFormatter(lambda x, _: f'{x:,.0f}'))

        # Q8 バッジ色をタイトルに反映
        q8_icon = {'danger': '⚠', 'positive': '★', 'unknown': '?', 'ok': '✓'}.get(
            q8_verdict, '?')
        title = f'{code} {name}  Score:{score}点[{grade}]  Q8:{q8_icon}'
        ax1.set_title(title, color='#ffffff', fontsize=10, pad=6)

        # 出来高バー
        ax2.bar(idx, hist['Volume'], color=colors, width=0.8)
        ax2.xaxis.set_major_formatter(mdates.DateFormatter('%m/%d'))
        ax2.xaxis.set_major_locator(mdates.MonthLocator())
        ax2.yaxis.set_major_formatter(
            matplotlib.ticker.FuncFormatter(lambda x, _: f'{x/1e4:.0f}万'))
        ax2.set_ylabel('出来高', color='#aaaaaa', fontsize=7)

        plt.tight_layout(h_pad=0.3)

        buf = io.BytesIO()
        fig.savefig(buf, format='png', dpi=100, bbox_inches='tight',
                    facecolor='#1e1e1e')
        plt.close(fig)
        buf.seek(0)
        return base64.b64encode(buf.read()).decode('utf-8')

    except Exception:
        return None


def _make_nikkei_chart_b64():
    """日経平均+MA25 のチャートを base64 PNG で返す"""
    if not _MATPLOTLIB_OK:
        return None
    try:
        hist = yf.Ticker("^N225").history(period="6mo")
        if len(hist) < 30:
            return None
        hist['MA25'] = hist['Close'].rolling(25).mean()

        fig, ax = plt.subplots(figsize=(12, 3), facecolor='#1e1e1e')
        ax.set_facecolor('#1e1e1e')
        ax.tick_params(colors='#cccccc', labelsize=8)
        for spine in ax.spines.values():
            spine.set_edgecolor('#444444')

        idx = hist.index
        ax.plot(idx, hist['Close'], color='#e0e0e0', linewidth=1.4, label='日経平均')
        ax.plot(idx, hist['MA25'],  color='#ff9800', linewidth=1.2, label='MA25')
        ax.legend(loc='upper left', fontsize=8,
                  facecolor='#2a2a2a', edgecolor='#555', labelcolor='#cccccc')
        ax.xaxis.set_major_formatter(mdates.DateFormatter('%m/%d'))
        ax.xaxis.set_major_locator(mdates.MonthLocator())
        ax.yaxis.set_major_formatter(
            matplotlib.ticker.FuncFormatter(lambda x, _: f'{x:,.0f}'))
        ax.set_title('日経平均 (6ヶ月)', color='#ffffff', fontsize=10, pad=6)

        plt.tight_layout()
        buf = io.BytesIO()
        fig.savefig(buf, format='png', dpi=100, bbox_inches='tight',
                    facecolor='#1e1e1e')
        plt.close(fig)
        buf.seek(0)
        return base64.b64encode(buf.read()).decode('utf-8')
    except Exception:
        return None


def generate_html_report(script_dir, passed, failed, q1_status, q1_msg, today_str):
    """
    分析結果を HTML レポートとして results/report_YYYYMMDD_HHMM.html に保存する。

    - 日経Q1判定バナー
    - 日経チャート（matplotlib）
    - 通過銘柄サマリー表
    - 通過銘柄ごとの株価チャート（MA25/50/150/200 + 出来高 + Q8結果）
    - 見送り銘柄一覧

    matplotlib が未インストールの場合はチャート画像なしでテキストのみ出力。
    """
    results_dir = os.path.join(script_dir, "results")
    os.makedirs(results_dir, exist_ok=True)
    now_str = datetime.now().strftime("%Y%m%d_%H%M")
    html_path = os.path.join(results_dir, f"report_{now_str}.html")

    # ---- Q1バナー設定 ----
    q1_cls   = {'go': 'q1-go', 'caution': 'q1-caution'}.get(q1_status, 'q1-stop')
    q1_icon  = {'go': '🟢', 'caution': '🟡'}.get(q1_status, '🔴')
    q1_label = {'go': '強気 [GREEN]', 'caution': '中立 [YELLOW]'}.get(q1_status, '弱気 [RED]')

    # ---- 日経チャート ----
    print("  HTMLレポート: 日経チャート生成中...")
    nikkei_b64 = _make_nikkei_chart_b64()
    nikkei_img = (f'<img class="chart-img" src="data:image/png;base64,{nikkei_b64}" alt="日経チャート">'
                  if nikkei_b64 else '<p class="no-chart">チャート生成スキップ (matplotlib未導入または取得失敗)</p>')

    # ---- サマリー表 ----
    def grade_cls(g):
        return {'S': 'badge-S', 'A': 'badge-A', 'B': 'badge-B', 'C': 'badge-C'}.get(g, 'badge-C')

    def grade_badge(g):
        return f'<span class="badge {grade_cls(g)}">{g}ランク</span>'

    def q8_badge(q8):
        if q8 is None: return '<span class="badge badge-unknown">Q8:?</span>'
        v = q8.get('verdict', 'unknown')
        if v == 'danger':
            kws = '/'.join(set(q8.get('found_danger', ['?'])))
            return f'<span class="badge badge-danger">⚠ {kws}</span>'
        elif v == 'positive':
            kws = '/'.join(set(q8.get('found_positive', [''])))
            return f'<span class="badge badge-positive">★ {kws}</span>'
        elif v == 'unknown':
            return '<span class="badge badge-unknown">Q8:要確認</span>'
        else:
            return '<span class="badge badge-ok">Q8:OK</span>'

    summary_rows = ''
    for rank, r in enumerate(passed, 1):
        g = r.get('grade', '?')
        summary_rows += f"""
        <tr>
          <td style="text-align:center">#{rank}</td>
          <td><b>{r['_code']}</b></td>
          <td>{r['_name']}</td>
          <td style="text-align:right">{r['close']:,.0f}円</td>
          <td style="text-align:center">{r.get('score',0)}点</td>
          <td>{grade_badge(g)} {q8_badge(r.get('q8'))}</td>
          <td style="font-size:12px">{r.get('q9_msg','?')}</td>
        </tr>"""

    failed_rows = ''
    for r in failed:
        reasons = []
        if not r.get('q4_pass', True): reasons.append('Q4:MA配列')
        if not r.get('q5_pass', True): reasons.append('Q5:200日線')
        if not r.get('q6_pass', True): reasons.append('Q6:出来高')
        if not r.get('q7_pass', True): reasons.append('Q7:過熱')
        if r.get('q8', {}).get('verdict') == 'danger':
            reasons.append('Q8:危険KW')
        if r.get('q9_status') == 'danger': reasons.append('Q9:決算直前')
        if not r.get('q10_pass', True): reasons.append('Q10:ギャップ')
        failed_rows += f"""
        <tr>
          <td><b>{r['_code']}</b> {r['_name']}</td>
          <td>{' / '.join(reasons) or '-'}</td>
        </tr>"""

    # ---- 個別銘柄セクション ----
    stock_sections = ''
    for rank, r in enumerate(passed, 1):
        code, name = r['_code'], r['_name']
        score = r.get('score', 0)
        grade = r.get('grade', '?')
        q8    = r.get('q8')
        q8_v  = q8.get('verdict', 'unknown') if q8 else 'unknown'

        print(f"  HTMLレポート: {code} {name} チャート生成中...")
        chart_b64 = _make_chart_b64(code, name, score, grade, q8_v)
        chart_html = (f'<img class="chart-img" src="data:image/png;base64,{chart_b64}" alt="{code} chart">'
                      if chart_b64 else
                      '<p class="no-chart">チャート生成スキップ (matplotlib未導入または取得失敗)</p>')

        # Q8 ニュースリスト
        news_html = ''
        if q8 and q8.get('news_list'):
            news_html = '<div class="q8-news"><b>Q8 スキャン結果（直近15件）:</b>'
            for n in q8['news_list']:
                cls = {'danger': 'news-danger', 'positive': 'news-positive'}.get(n['flag'], 'news-neutral')
                kw_str = ''
                if n['found_keywords']:
                    kw_str = f'  <b>[{", ".join(n["found_keywords"])}]</b>'
                news_html += f'<div class="news-item {cls}">{n["date"]}  {n["title"]}{kw_str}</div>'
            news_html += '</div>'
        elif q8 and q8.get('error'):
            news_html = f'<p style="color:#888;font-size:12px">Q8: {q8["error"]}</p>'

        sd = r.get('score_detail', {})
        stock_sections += f"""
        <div class="stock-card">
          <div class="stock-title">
            #{rank}  {code} {name}
            <span class="badge {grade_cls(grade)}">{score}点 [{grade}]</span>
            {q8_badge(q8)}
          </div>
          {chart_html}
          <table style="margin-top:12px">
            <tr><th colspan="2">判定詳細</th></tr>
            <tr><td>Q4 移動平均配列</td><td>{r.get('q4','?')}</td></tr>
            <tr><td>Q5 200日線方向</td><td>{r.get('q5','?')}</td></tr>
            <tr><td>Q6 出来高の質</td><td>{r.get('q6','?')}</td></tr>
            <tr><td>Q7 25日線乖離</td><td>{r.get('q7','?')}</td></tr>
            <tr><td>Q8 ニュース</td><td>{_q8_summary(q8)}</td></tr>
            <tr><td>Q9 決算日</td><td>{r.get('q9_msg','?')}</td></tr>
            <tr><td>Q10 ギャップ</td><td>{r.get('q10','?')}</td></tr>
            <tr><td>Q11 3ヶ月高値</td><td>{r.get('q11','?')}</td></tr>
            <tr><td>MA25/50/150/200</td>
                <td>{r['ma25']:,.0f} / {r['ma50']:,.0f} / {r['ma150']:,.0f} / {r['ma200']:,.0f}</td></tr>
            <tr><td>スコア内訳</td>
                <td>Q4+{sd.get('Q4',0)} Q5+{sd.get('Q5',0)} Q6+{sd.get('Q6',0)} Q7+{sd.get('Q7',0)} Q10+{sd.get('Q10',0)}</td></tr>
          </table>
          {news_html}
        </div>"""

    # ---- 候補なしの場合 ----
    if not passed:
        stock_sections = '<div class="no-candidates">本日の通過銘柄はありません。<br>何もしないことは、立派な利益確定です。</div>'

    matplotlib_warn = '' if _MATPLOTLIB_OK else \
        '<div style="background:#fff3cd;padding:10px;border-radius:6px;margin-bottom:16px;">⚠ matplotlib が未インストールのためチャートは表示されません。<code>setup.bat</code> を実行してください。</div>'

    # ---- HTML 組み立て ----
    html = f"""<!DOCTYPE html>
<html lang="ja">
<head>
  <meta charset="UTF-8">
  <title>スイング分析レポート {today_str}</title>
  <style>
    :root {{ color-scheme: light; }}
    * {{ box-sizing: border-box; }}
    body {{ font-family: 'Meiryo','MS Gothic',sans-serif; background:#f0f2f5; margin:0; padding:20px; color:#212529; }}
    .header {{ background:#1a1a2e; color:#e0e0e0; padding:18px 24px; border-radius:10px; margin-bottom:18px; }}
    .header h1 {{ margin:0 0 4px; font-size:20px; }}
    .header p  {{ margin:0; font-size:13px; color:#aaa; }}
    .q1-banner {{ padding:14px 20px; border-radius:8px; margin-bottom:18px; font-size:16px; font-weight:bold; }}
    .q1-go     {{ background:#d4edda; border-left:6px solid #28a745; color:#155724; }}
    .q1-caution{{ background:#fff3cd; border-left:6px solid #ffc107; color:#856404; }}
    .q1-stop   {{ background:#f8d7da; border-left:6px solid #dc3545; color:#721c24; }}
    .section-title {{ font-size:16px; font-weight:bold; color:#343a40; margin:24px 0 8px; border-bottom:2px solid #dee2e6; padding-bottom:4px; }}
    table {{ width:100%; border-collapse:collapse; background:white; border-radius:8px; overflow:hidden; box-shadow:0 1px 3px rgba(0,0,0,.1); }}
    th {{ background:#343a40; color:white; padding:9px 12px; text-align:left; font-size:13px; }}
    td {{ padding:8px 12px; border-bottom:1px solid #e9ecef; font-size:13px; }}
    tr:last-child td {{ border-bottom:none; }}
    tr:hover td {{ background:#f8f9fa; }}
    .badge {{ display:inline-block; padding:3px 9px; border-radius:12px; font-size:11px; font-weight:bold; margin:2px; }}
    .badge-S {{ background:#ffd700; color:#333; }}
    .badge-A {{ background:#b0b0b0; color:#333; }}
    .badge-B {{ background:#cd7f32; color:#fff; }}
    .badge-C {{ background:#6c757d; color:#fff; }}
    .badge-danger  {{ background:#dc3545; color:#fff; }}
    .badge-positive{{ background:#28a745; color:#fff; }}
    .badge-ok      {{ background:#6c757d; color:#fff; }}
    .badge-unknown {{ background:#ffc107; color:#333; }}
    .stock-card {{ background:white; padding:20px; margin-bottom:28px; border-radius:10px; box-shadow:0 2px 6px rgba(0,0,0,.1); }}
    .stock-title {{ font-size:20px; font-weight:bold; margin-bottom:12px; }}
    .chart-img {{ max-width:100%; height:auto; border-radius:6px; display:block; margin-bottom:10px; }}
    .no-chart {{ color:#888; font-size:13px; padding:16px; background:#f8f9fa; border-radius:6px; text-align:center; }}
    .q8-news {{ margin-top:14px; }}
    .q8-news b {{ font-size:13px; color:#495057; }}
    .news-item {{ padding:6px 12px; margin:3px 0; border-radius:4px; font-size:12px; line-height:1.5; }}
    .news-danger   {{ background:#f8d7da; border-left:4px solid #dc3545; }}
    .news-positive {{ background:#d4edda; border-left:4px solid #28a745; }}
    .news-neutral  {{ background:#f8f9fa; border-left:4px solid #dee2e6; color:#555; }}
    .no-candidates {{ text-align:center; padding:50px; color:#666; font-size:18px; background:white; border-radius:10px; }}
  </style>
</head>
<body>
  <div class="header">
    <h1>📊 スイング分析レポート</h1>
    <p>{today_str}  ／  通過銘柄: {len(passed)}  見送り: {len(failed)}</p>
  </div>

  {matplotlib_warn}

  <div class="q1-banner {q1_cls}">
    Q1 地合い判定 {q1_icon} {q1_label}<br>
    <span style="font-size:14px;font-weight:normal">{q1_msg}</span>
  </div>

  <div class="section-title">📈 日経平均チャート (6ヶ月)</div>
  {nikkei_img}

  <div class="section-title">✅ 通過銘柄サマリー ({len(passed)}銘柄)</div>
  <table>
    <tr><th>#</th><th>コード</th><th>銘柄名</th><th>現在値</th><th>スコア</th><th>判定</th><th>決算</th></tr>
    {summary_rows if passed else '<tr><td colspan="7" style="text-align:center;color:#888">なし</td></tr>'}
  </table>

  <div class="section-title">🔍 銘柄別チャート・詳細</div>
  {stock_sections}

  <div class="section-title">❌ 見送り銘柄 ({len(failed)}銘柄)</div>
  <table>
    <tr><th>銘柄</th><th>見送り理由</th></tr>
    {failed_rows if failed else '<tr><td colspan="2" style="text-align:center;color:#888">なし</td></tr>'}
  </table>

  <p style="text-align:center;color:#aaa;font-size:12px;margin-top:30px">
    生成: {datetime.now().strftime('%Y/%m/%d %H:%M:%S')}  ／  シゲアキ専用スイングトレード自動分析
  </p>
</body>
</html>"""

    with open(html_path, 'w', encoding='utf-8') as f:
        f.write(html)

    return html_path


def run_analysis(script_dir):
    """分析処理の本体。早期終了は return で抜ける（input/sys.exit は呼ばない）"""

    # CSVを探す
    if len(sys.argv) > 1:
        csv_path = sys.argv[1]
    else:
        csv_path = find_latest_csv(script_dir)

    if not csv_path or not os.path.exists(csv_path):
        print("\n[ERROR] CSVファイルが見つかりません。")
        print(f"   フォルダ: {script_dir}")
        print("   楽天証券スーパースクリーナーのCSVをこのフォルダに置いてください。")
        return

    print(f"\nCSV: {os.path.basename(csv_path)}")

    # CSVパース
    df = load_csv(csv_path)
    df.columns = df.columns.str.strip()

    col = {}
    for c in df.columns:
        if 'コード'     in c: col['code']    = c
        elif '銘柄名'   in c: col['name']    = c
        elif '売上高変化率'   in c: col['revenue'] = c
        elif '経常利益変化率' in c: col['profit']  = c
        elif '自己資本比率'   in c: col['equity']  = c
        elif '時価総額'       in c: col['mktcap']  = c
        elif '前日比'         in c: col['change']  = c

    df['_code']    = df[col['code']].astype(str).str.strip()
    df['_name']    = df[col['name']].astype(str).str.strip()
    df['_revenue'] = df[col['revenue']].apply(clean_number)
    df['_profit']  = df[col['profit']].apply(clean_number)
    df['_equity']  = df[col['equity']].apply(clean_number)
    df['_mktcap']  = df[col['mktcap']].apply(clean_number)
    df['_change']  = df[col['change']].apply(parse_day_change)

    print(f"{len(df)}銘柄を読み込みました。\n")

    # STEP1: 財務フィルター
    print("=" * 65)
    print("STEP1: 財務フィルター")
    print("=" * 65)

    f1 = df[
        (df['_revenue'] >= MIN_REVENUE_GROWTH) &
        (df['_profit']  >= MIN_PROFIT_GROWTH)  &
        (df['_equity']  >= MIN_EQUITY_RATIO)   &
        (df['_mktcap']  >= MIN_MARKET_CAP)     &
        (df['_mktcap']  <= MAX_MARKET_CAP)
    ].copy()

    for _, r in df[~df.index.isin(f1.index)].iterrows():
        reasons = []
        if r['_revenue'] < MIN_REVENUE_GROWTH: reasons.append(f"売上高{r['_revenue']:.1f}%")
        if r['_profit']  < MIN_PROFIT_GROWTH:  reasons.append(f"利益{r['_profit']:.1f}%")
        if r['_equity']  < MIN_EQUITY_RATIO:   reasons.append(f"自己資本{r['_equity']:.1f}%")
        if not (MIN_MARKET_CAP <= r['_mktcap'] <= MAX_MARKET_CAP):
            reasons.append(f"時価総額{r['_mktcap']:.0f}M")
        print(f"  [NG] {r['_code']} {r['_name']}: {' / '.join(reasons)}")

    print(f"\n  財務通過: {len(f1)}/{len(df)}銘柄")

    # STEP2: 当日騰落フィルター
    print("\n" + "=" * 65)
    print(f"STEP2: 当日騰落フィルター (+-{MAX_DAY_CHANGE_PCT}%超は除外)")
    print("=" * 65)

    f2        = f1[f1['_change'].abs() <= MAX_DAY_CHANGE_PCT].copy()
    excluded  = f1[f1['_change'].abs() >  MAX_DAY_CHANGE_PCT]

    for _, r in excluded.iterrows():
        print(f"  [除外] {r['_code']} {r['_name']}: 本日{r['_change']:+.2f}% → 飛び乗りリスク")

    print(f"\n  チャート審査対象: {len(f2)}銘柄")

    if len(f2) == 0:
        print("\n  本日は候補銘柄なし。")
        print("  何もしないことは、立派な利益確定です。")
        return

    # Q1: 地合い確認
    print("\n" + "=" * 65)
    print("Q1: 日経平均 地合い確認中...")
    print("=" * 65)
    q1_status, q1_msg = check_nikkei_q1()
    print(f"  {q1_msg}\n")

    if q1_status == "stop":
        print("  [STOP] 弱気地合いのため、本日は全面【見送り】です。")
        return

    # Q4-Q11: チャート審査
    print("=" * 65)
    print("Q4-Q11: チャート審査中...")
    print("=" * 65)

    passed, failed, errors = [], [], []

    for _, row in f2.iterrows():
        code, name = row['_code'], row['_name']
        print(f"  ... {code} {name}", end="", flush=True)
        result, err = analyze_chart(code)
        if err:
            print(f"  [SKIP] ({err})")
            errors.append((code, name, err))
            continue

        # Q9: 決算日チェック（チャートOKの銘柄のみ実施）
        q9_status, q9_msg = check_earnings_q9(code)
        result['q9_status'] = q9_status
        result['q9_msg']    = q9_msg

        if result['all_pass'] and q9_status == "danger":
            result['all_pass'] = False  # 決算直前なら見送りに変更

        # Q8: ニュース半自動チェック（チャートOKの銘柄のみ実施）
        print(f"\n     Q8 ニュースチェック中...", end="", flush=True)
        q8 = check_news_q8(code, name)
        result['q8'] = q8

        if q8['verdict'] == 'danger':
            # 危険キーワード検出 → 見送り（ただし目視確認を促す）
            kws = ', '.join(set(q8['found_danger']))
            print(f" [Q8-WARN] 危険キーワード検出: {kws} → 楽天証券で要確認")
            result['all_pass'] = False
        elif q8['verdict'] == 'positive':
            kws = ', '.join(set(q8['found_positive']))
            print(f" [Q8-OK] ポジティブ: {kws}")
        elif q8['verdict'] == 'unknown':
            print(f" [Q8-?] {q8.get('error','取得不可')} → 楽天証券で手動確認")
        else:
            print(f" [Q8-OK] 危険キーワードなし")

        if result['all_pass']:
            total, grade, scores = compute_score(result)
            result['score']       = total
            result['grade']       = grade
            result['score_detail'] = scores
            print(f"  --> [PASS]  Score: {total}点 [{grade}]")
            passed.append({**row.to_dict(), **result})
        else:
            print("  --> [NG]")
            failed.append({**row.to_dict(), **result})

    # 最終結果
    print("\n" + "=" * 65)
    print("  最終結果")
    print("=" * 65)
    print(f"  通過: {len(passed)}銘柄  /  見送り: {len(failed)}銘柄")

    if passed:
        # スコア順に並び替え
        passed.sort(key=lambda x: x.get('score', 0), reverse=True)

        print("\n" + "-" * 65)
        print("  【名探偵モード 最終審査 対象銘柄】（スコア順）")
        print("-" * 65)
        for rank, r in enumerate(passed, 1):
            score = r.get('score', 0)
            grade = r.get('grade', '?')
            sd    = r.get('score_detail', {})
            print(f"""
  #{rank}  {r['_code']} {r['_name']}  {r['close']:,.0f}円
      ★ Score: {score}点 / 100点  [{grade}ランク]
         Q4(MA配列)+{sd.get('Q4',0)}  Q5(200日線)+{sd.get('Q5',0)}  Q6(出来高)+{sd.get('Q6',0)}  Q7(乖離)+{sd.get('Q7',0)}  Q10(ギャップ)+{sd.get('Q10',0)}
    Q4  移動平均配列: {r['q4']}
    Q5  200日線方向:  {r['q5']}
    Q6  出来高の質:   {r['q6']}
    Q7  25日線乖離:   {r['q7']}
    Q8  ニュース:     {_q8_summary(r.get('q8'))}
    Q9  決算日:       {r.get('q9_msg', '未確認')}
    Q10 ギャップ:     {r['q10']}
    Q11 3ヶ月高値:    {r['q11']}
    MA25={r['ma25']:,.0f} / MA50={r['ma50']:,.0f} / MA150={r['ma150']:,.0f} / MA200={r['ma200']:,.0f}""")

        print("\n" + "-" * 65)
        print("  残り手動確認（楽天証券Webで）")
        print("-" * 65)
        print("  Q8:  ニュース → 自動スキャン済み。[WARN]の銘柄は楽天証券Webで目視確認推奨")
        print("  Q9:  決算日 ← 自動取得済み（データなし銘柄は手動確認）")
        print("  Q12: 保有銘柄数（上限2銘柄）")
        print("  Q13: 直近トレード成績（3連敗なら1週間停止）")
    else:
        print("\n  本日の候補銘柄はありません。")
        print("  何もしないことは、立派な利益確定です。")

    if failed:
        print("\n" + "-" * 65)
        print("  見送り理由")
        print("-" * 65)
        for r in failed:
            reasons = []
            if not r.get('q4_pass', True):  reasons.append("Q4:移動平均NG")
            if not r.get('q5_pass', True):  reasons.append("Q5:200日線下向き")
            if not r.get('q6_pass', True):  reasons.append("Q6:出来高NG")
            if not r.get('q7_pass', True):  reasons.append("Q7:過熱")
            q8 = r.get('q8')
            if q8 and q8.get('verdict') == 'danger':
                kws = '/'.join(set(q8.get('found_danger', [])))
                reasons.append(f"Q8:危険KW({kws})")
            if r.get('q9_status') == "danger": reasons.append(f"Q9:{r.get('q9_msg','決算直前')}")
            if not r.get('q10_pass', True): reasons.append("Q10:ギャップNG")
            print(f"  {r['_code']} {r['_name']}: {' / '.join(reasons)}")

    # 紙トレ記録テンプレートを自動生成
    today_str = datetime.now().strftime("%Y-%m-%d")
    if passed:
        print("\n" + "-" * 65)
        print("  紙トレ記録テンプレート生成")
        print("-" * 65)
        for r in passed:
            fpath = generate_trade_template(script_dir, r, today_str)
            fname = os.path.basename(fpath)
            print(f"  {fname}")
        print("  -> trades フォルダに保存しました")

    # ウォッチリストを更新して表示
    wl = update_watchlist(script_dir, passed, today_str)
    display_watchlist(wl, today_str)

    # HTML レポート生成
    print("\n" + "-" * 65)
    print("  HTML レポート生成中...")
    print("-" * 65)
    try:
        html_path = generate_html_report(
            script_dir, passed, failed, q1_status, q1_msg, today_str)
        print(f"  [OK] レポート保存先:")
        print(f"  {html_path}")
    except Exception as e:
        print(f"  [SKIP] HTML レポート生成に失敗しました: {e}")

    # Discord 通知(任意・設定済みなら自動送信)
    try:
        import notifier  # 同フォルダの notifier.py
        if notifier.get_webhook_url():
            print("\n" + "-" * 65)
            print("  Discord 通知送信中...")
            print("-" * 65)
            ok, msg = notifier.send_analysis_result(
                passed, q1_status, q1_msg, today_str)
            print(f"  {'[OK]' if ok else '[NG]'}  {msg}")
        else:
            # Webhook未設定なら静かにスキップ(無理に通知しない)
            pass
    except ImportError:
        pass  # notifier.py がない場合はスキップ
    except Exception as e:
        print(f"  [SKIP] Discord 通知に失敗しました: {e}")


# ============================================================
#  エントリーポイント
# ============================================================

def main():
    now        = datetime.now()
    script_dir = os.path.dirname(os.path.abspath(__file__))

    # resultsフォルダに結果ファイルを準備
    results_dir     = os.path.join(script_dir, "results")
    os.makedirs(results_dir, exist_ok=True)
    result_filename = os.path.join(results_dir, f"result_{now.strftime('%Y%m%d_%H%M')}.txt")
    result_file     = open(result_filename, "w", encoding="utf-8")

    # コンソールとファイルに同時出力
    sys.stdout = Tee(sys.__stdout__, result_file)

    try:
        print("=" * 65)
        print("  シゲアキ専用 スイングトレード 自動分析")
        print(f"  {now.strftime('%Y/%m/%d  %H:%M')}")
        print("=" * 65)

        run_analysis(script_dir)

        print("\n" + "=" * 65)
        print(f"  分析完了  {datetime.now().strftime('%H:%M:%S')}")
        print("=" * 65)

    finally:
        sys.stdout = sys.__stdout__
        result_file.close()
        print(f"\n  記録を保存しました:")
        print(f"  {result_filename}")

    input("\n  Enterキーで終了...")


if __name__ == "__main__":
    main()
