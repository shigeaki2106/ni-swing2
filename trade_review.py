#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  シゲアキ専用 トレード成績集計ツール
  trades/ フォルダの紙トレ記録を集計し
  勝率・損益・連敗・Q13(3連敗で1週間停止)を判定
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  使い方:
    1. swing_analyzer で生成された trades/ フォルダの記録に
       エントリー価格・決済価格などを手書きで埋める
    2. review.bat をダブルクリック

  必要なもの:
    - Python 3.8以上 (yfinanceや外部ライブラリは不要)
"""

import sys
import os
import re
import glob
from datetime import datetime, date, timedelta


# ============================================================
#  設定
# ============================================================
LOSS_STREAK_LIMIT = 3   # Q13: この連敗数に達したら1週間停止推奨
PAUSE_DAYS        = 7   # 停止推奨期間 (営業日カウントではなく暦日)


# ============================================================
#  数値抽出ユーティリティ
# ============================================================

def extract_number(text):
    """文字列から最初に出てくる数値(±カンマ・小数点対応)を返す。なければNone。

    "1500 円" → 1500.0
    "1,500 円" → 1500.0
    "+15,000円 (+10%)" → 15000.0
    "-5.0 %" → -5.0

    regex の alternation backtracking が環境依存で挙動が変わる事象に
    遭遇したため、手書きスキャンで実装している。
    """
    if text is None:
        return None
    s = str(text)
    n = len(s)
    i = 0
    while i < n:
        ch = s[i]
        # 数値の開始: 符号 or 数字 or 数字始まりの小数点
        if ch.isdigit() or (ch in '+-' and i + 1 < n and s[i + 1].isdigit()):
            start = i
            if ch in '+-':
                i += 1
            # 数字とカンマを取り込む
            while i < n and (s[i].isdigit() or s[i] == ','):
                i += 1
            # 小数点とそれに続く数字
            if i < n and s[i] == '.' and i + 1 < n and s[i + 1].isdigit():
                i += 1
                while i < n and s[i].isdigit():
                    i += 1
            token = s[start:i].replace(',', '')
            # カンマだけ末尾に残るケースを除去
            token = token.rstrip(',')
            if token in ('', '+', '-'):
                continue
            try:
                return float(token)
            except Exception:
                continue
        i += 1
    return None


def extract_percent(text):
    """カッコ内のパーセント値を抽出 例: '損益 : +5000円 ( +3.5 % )' → 3.5"""
    if text is None:
        return None
    m = re.search(r'\(\s*([+-]?\d+(?:\.\d+)?)\s*%\s*\)', str(text))
    if m:
        try:
            return float(m.group(1))
        except Exception:
            return None
    return None


def extract_date(text):
    """日付らしきものを抽出 (YYYY/MM/DD, YYYY-MM-DD, MM/DD など)"""
    if text is None:
        return None
    s = str(text)
    # YYYY/MM/DD or YYYY-MM-DD
    m = re.search(r'(20\d{2})[/\-年](\d{1,2})[/\-月](\d{1,2})', s)
    if m:
        try:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except Exception:
            return None
    # MM/DD (今年とみなす)
    m = re.search(r'(\d{1,2})[/\-月](\d{1,2})', s)
    if m:
        try:
            return date(date.today().year, int(m.group(1)), int(m.group(2)))
        except Exception:
            return None
    return None


def get_value_after_colon(line):
    """'  エントリー価格  :   1500 円' のようなコロン区切り行から、コロン後ろの値部分を返す"""
    if ':' not in line and '：' not in line:
        return ''
    sep = ':' if ':' in line else '：'
    return line.split(sep, 1)[1]


# ============================================================
#  紙トレ記録ファイルのパース
# ============================================================

FILENAME_RE = re.compile(r'trade_(\d{8})_(\d+)_(.+)\.txt$')

def parse_filename(filepath):
    """trade_YYYYMMDD_コード_銘柄名.txt からスクリーニング日・コード・銘柄名を取得"""
    name = os.path.basename(filepath)
    m = FILENAME_RE.search(name)
    if not m:
        return None, None, None
    try:
        d = datetime.strptime(m.group(1), '%Y%m%d').date()
    except Exception:
        d = None
    return d, m.group(2), m.group(3)


def parse_trade_file(filepath):
    """
    紙トレ記録 1ファイルをパースして dict にする。

    戻り値の主要キー:
      code, name, screening_date, status,
      entry_date, entry_price, shares,
      stop_price, target_price,
      exit_date, exit_price, pl_yen, pl_pct, hold_days,
      exit_reason, score, grade
    """
    screening_date, code, name = parse_filename(filepath)

    with open(filepath, 'r', encoding='utf-8') as f:
        text = f.read()

    rec = {
        'filepath': filepath,
        'filename': os.path.basename(filepath),
        'code': code,
        'name': name,
        'screening_date': screening_date,
        'status': '予定',  # 予定 / 保有中 / 完了
        'entry_date': None,  'entry_price': None, 'shares': None,
        'stop_price': None,  'target_price': None,
        'exit_date': None,   'exit_price': None,
        'pl_yen': None,      'pl_pct': None,      'hold_days': None,
        'exit_reason': '',
        'score': None,       'grade': None,
        'is_win': None,
    }

    for raw in text.splitlines():
        line = raw.strip()
        # スコア行 例: "Score : 87点 / 100点  [Sランク]"
        if line.startswith('Score'):
            m = re.search(r'(\d+)\s*点', line)
            if m: rec['score'] = int(m.group(1))
            m = re.search(r'\[([SABC])', line)
            if m: rec['grade'] = m.group(1)
            continue

        # 各種コロン区切り行
        if 'エントリー日時' in line:
            rec['entry_date'] = extract_date(get_value_after_colon(line))
        elif 'エントリー価格' in line:
            rec['entry_price'] = extract_number(get_value_after_colon(line))
        elif line.startswith('株数') or '株数' in line[:6]:
            rec['shares'] = extract_number(get_value_after_colon(line))
        elif '損切りライン' in line:
            rec['stop_price'] = extract_number(get_value_after_colon(line).split('（')[0].split('(')[0])
        elif '目標価格' in line:
            rec['target_price'] = extract_number(get_value_after_colon(line).split('（')[0].split('(')[0])
        elif '決済日時' in line:
            rec['exit_date'] = extract_date(get_value_after_colon(line))
        elif '決済価格' in line:
            rec['exit_price'] = extract_number(get_value_after_colon(line))
        elif '損益' in line and '円' in line:
            val_part = get_value_after_colon(line)
            # コロン後ろから「円」までの数値
            yen_part = val_part.split('円')[0] if '円' in val_part else val_part
            rec['pl_yen'] = extract_number(yen_part)
            rec['pl_pct'] = extract_percent(val_part)
        elif '保有日数' in line:
            rec['hold_days'] = extract_number(get_value_after_colon(line))
        elif '決済理由' in line:
            rec['exit_reason'] = get_value_after_colon(line).strip()

    # ステータス判定
    has_entry = rec['entry_price'] is not None and rec['entry_price'] > 0
    has_exit  = rec['exit_price']  is not None and rec['exit_price']  > 0
    if has_entry and has_exit:
        rec['status'] = '完了'
    elif has_entry:
        rec['status'] = '保有中'
    else:
        rec['status'] = '予定'

    # 損益が記入されていなければ計算で補完
    if rec['status'] == '完了':
        if rec['pl_yen'] is None and rec['shares']:
            rec['pl_yen'] = (rec['exit_price'] - rec['entry_price']) * rec['shares']
        if rec['pl_pct'] is None and rec['entry_price']:
            rec['pl_pct'] = (rec['exit_price'] / rec['entry_price'] - 1) * 100
        # 保有日数の補完
        if rec['hold_days'] is None and rec['entry_date'] and rec['exit_date']:
            rec['hold_days'] = (rec['exit_date'] - rec['entry_date']).days
        # 勝敗判定 (損益％を優先、なければ価格比較)
        if rec['pl_pct'] is not None:
            rec['is_win'] = rec['pl_pct'] > 0
        else:
            rec['is_win'] = rec['exit_price'] > rec['entry_price']

    return rec


# ============================================================
#  集計
# ============================================================

def aggregate(records):
    """完了トレードの統計を計算する"""
    completed = [r for r in records if r['status'] == '完了']
    holding   = [r for r in records if r['status'] == '保有中']
    plan_only = [r for r in records if r['status'] == '予定']

    stats = {
        'total_records': len(records),
        'completed': len(completed),
        'holding': len(holding),
        'plan_only': len(plan_only),
        'wins': 0, 'losses': 0, 'win_rate': 0.0,
        'total_pl_yen': 0.0, 'total_pl_pct': 0.0,
        'avg_pl_yen': 0.0,   'avg_pl_pct': 0.0,
        'avg_hold_days': 0.0,
        'best_trade': None,  'worst_trade': None,
        'current_streak': 0, 'streak_type': None,  # 'win' or 'loss'
        'best_win_streak': 0, 'worst_loss_streak': 0,
        'q13_pause': False,  'q13_resume_date': None,
        'recent_5': [],
    }

    if not completed:
        return stats, completed, holding, plan_only

    # 完了トレードを「決済日」優先、なければ「エントリー日」、なければ「スクリーニング日」で時系列ソート
    def sort_key(r):
        return r['exit_date'] or r['entry_date'] or r['screening_date'] or date.min
    completed.sort(key=sort_key)

    pls_yen = [r['pl_yen'] for r in completed if r['pl_yen'] is not None]
    pls_pct = [r['pl_pct'] for r in completed if r['pl_pct'] is not None]
    holds   = [r['hold_days'] for r in completed if r['hold_days'] is not None]

    stats['wins']         = sum(1 for r in completed if r['is_win'])
    stats['losses']       = sum(1 for r in completed if r['is_win'] is False)
    stats['win_rate']     = stats['wins'] / len(completed) * 100 if completed else 0
    stats['total_pl_yen'] = sum(pls_yen) if pls_yen else 0
    stats['total_pl_pct'] = sum(pls_pct) if pls_pct else 0
    stats['avg_pl_yen']   = sum(pls_yen)/len(pls_yen) if pls_yen else 0
    stats['avg_pl_pct']   = sum(pls_pct)/len(pls_pct) if pls_pct else 0
    stats['avg_hold_days']= sum(holds)/len(holds) if holds else 0

    if pls_pct:
        best  = max(completed, key=lambda r: r['pl_pct'] if r['pl_pct'] is not None else -9999)
        worst = min(completed, key=lambda r: r['pl_pct'] if r['pl_pct'] is not None else  9999)
        stats['best_trade']  = best
        stats['worst_trade'] = worst

    # 連勝/連敗のカウント
    cur, cur_type = 0, None
    best_w, worst_l = 0, 0
    for r in completed:
        if r['is_win'] is True:
            if cur_type == 'win':
                cur += 1
            else:
                cur, cur_type = 1, 'win'
            best_w = max(best_w, cur)
        elif r['is_win'] is False:
            if cur_type == 'loss':
                cur += 1
            else:
                cur, cur_type = 1, 'loss'
            worst_l = max(worst_l, cur)

    stats['current_streak']    = cur
    stats['streak_type']       = cur_type
    stats['best_win_streak']   = best_w
    stats['worst_loss_streak'] = worst_l

    # Q13: 直近のトレードがLOSS_STREAK_LIMIT連敗ちょうど（or それ以上）なら停止推奨
    if cur_type == 'loss' and cur >= LOSS_STREAK_LIMIT:
        stats['q13_pause'] = True
        last_exit = completed[-1].get('exit_date')
        if last_exit:
            stats['q13_resume_date'] = last_exit + timedelta(days=PAUSE_DAYS)

    # 直近5件
    stats['recent_5'] = completed[-5:][::-1]

    return stats, completed, holding, plan_only


# ============================================================
#  レポート出力
# ============================================================

def fmt_yen(v):
    if v is None: return '?'
    sign = '+' if v > 0 else ''
    return f'{sign}{v:,.0f}円'

def fmt_pct(v):
    if v is None: return '?'
    sign = '+' if v > 0 else ''
    return f'{sign}{v:.2f}%'


def print_report(stats, completed, holding, plan_only, today_str):
    print('=' * 65)
    print('  シゲアキ専用 トレード成績レポート')
    print(f'  {today_str}')
    print('=' * 65)

    print(f'\n  記録ファイル合計: {stats["total_records"]}件')
    print(f'    完了:   {stats["completed"]}件')
    print(f'    保有中: {stats["holding"]}件')
    print(f'    予定:   {stats["plan_only"]}件 (まだエントリーしてない記録)')

    if stats['completed'] == 0:
        print('\n  まだ完了したトレードがありません。')
        print('  trades/ フォルダの記録ファイルにエントリー価格・決済価格を')
        print('  記入してから再度実行してください。')
        return

    # ========== 統計 ==========
    print('\n' + '=' * 65)
    print('  全体成績 (完了トレードのみ)')
    print('=' * 65)
    print(f'  勝率           : {stats["win_rate"]:.1f}%  ({stats["wins"]}勝 {stats["losses"]}敗)')
    print(f'  累計損益       : {fmt_yen(stats["total_pl_yen"])}  ({fmt_pct(stats["total_pl_pct"])})')
    print(f'  平均損益/トレード: {fmt_yen(stats["avg_pl_yen"])}  ({fmt_pct(stats["avg_pl_pct"])})')
    print(f'  平均保有日数    : {stats["avg_hold_days"]:.1f}日')
    print(f'  最長連勝記録    : {stats["best_win_streak"]}連勝')
    print(f'  最長連敗記録    : {stats["worst_loss_streak"]}連敗')

    # ========== ベスト/ワースト ==========
    if stats['best_trade']:
        b = stats['best_trade']
        print(f'\n  ベストトレード  : {b["code"]} {b["name"]}  '
              f'{fmt_pct(b["pl_pct"])}  {fmt_yen(b["pl_yen"])}')
    if stats['worst_trade']:
        w = stats['worst_trade']
        print(f'  ワーストトレード: {w["code"]} {w["name"]}  '
              f'{fmt_pct(w["pl_pct"])}  {fmt_yen(w["pl_yen"])}')

    # ========== Q13 ==========
    print('\n' + '=' * 65)
    print('  Q13: 連敗チェック (3連敗で1週間停止ルール)')
    print('=' * 65)
    if stats['q13_pause']:
        print(f'  [STOP] {stats["current_streak"]}連敗中! 本日は新規エントリー見送り推奨。')
        if stats['q13_resume_date']:
            print(f'         再開可能日の目安: {stats["q13_resume_date"]} 以降')
        print('         冷静になる時間を確保し、紙トレ記録を読み返しましょう。')
    elif stats['streak_type'] == 'loss' and stats['current_streak'] > 0:
        remain = LOSS_STREAK_LIMIT - stats['current_streak']
        print(f'  [WARN] 現在 {stats["current_streak"]}連敗中 '
              f'(あと{remain}敗で停止ルール発動)')
    elif stats['streak_type'] == 'win':
        print(f'  [GOOD] 現在 {stats["current_streak"]}連勝中! 慢心せず淡々と。')
    else:
        print('  [OK] 現在連敗していません。')

    # ========== 直近5件 ==========
    print('\n' + '=' * 65)
    print('  直近のトレード (新しい順)')
    print('=' * 65)
    for r in stats['recent_5']:
        mark = '◎' if r['is_win'] else '×'
        d = r['exit_date'] or r['entry_date'] or r['screening_date'] or '?'
        print(f'  [{mark}] {d}  {r["code"]} {r["name"]}  '
              f'{fmt_pct(r["pl_pct"])} ({fmt_yen(r["pl_yen"])})  '
              f'保有{r["hold_days"] if r["hold_days"] is not None else "?"}日')

    # ========== 保有中 ==========
    if holding:
        print('\n' + '=' * 65)
        print('  保有中 (まだ決済していない)')
        print('=' * 65)
        for r in holding:
            ed = r['entry_date'] or r['screening_date'] or '?'
            shares = f'{int(r["shares"])}株' if r['shares'] else '?株'
            entry  = f'{r["entry_price"]:,.0f}円' if r['entry_price'] else '?'
            stop   = f'{r["stop_price"]:,.0f}円' if r['stop_price'] else '未設定'
            target = f'{r["target_price"]:,.0f}円' if r['target_price'] else '未設定'
            print(f'  {r["code"]} {r["name"]}  エントリー: {ed} {entry} {shares}')
            print(f'    損切: {stop}  /  目標: {target}')

    # ========== 予定 (テンプレートのみ) ==========
    if plan_only:
        print('\n' + '=' * 65)
        print('  予定 (まだエントリーしていないテンプレート)')
        print('=' * 65)
        for r in plan_only[:10]:
            d = r['screening_date'] or '?'
            print(f'  {d}  {r["code"]} {r["name"]}')
        if len(plan_only) > 10:
            print(f'  ...他 {len(plan_only)-10}件')


# ============================================================
#  エントリーポイント
# ============================================================

def main():
    script_dir = os.path.dirname(os.path.abspath(__file__))
    trades_dir = os.path.join(script_dir, 'trades')

    if not os.path.isdir(trades_dir):
        print('\n[ERROR] trades フォルダが見つかりません。')
        print(f'   想定パス: {trades_dir}')
        print('   先に run_analysis を実行して紙トレ記録テンプレートを生成してください。')
        input('\n  Enterキーで終了...')
        return

    files = sorted(glob.glob(os.path.join(trades_dir, 'trade_*.txt')))
    if not files:
        print('\n  trades フォルダに記録ファイルがありません。')
        print('  先に run_analysis を実行して紙トレ記録テンプレートを生成してください。')
        input('\n  Enterキーで終了...')
        return

    records = []
    for f in files:
        try:
            records.append(parse_trade_file(f))
        except Exception as e:
            print(f'  [SKIP] {os.path.basename(f)}: {e}')

    stats, completed, holding, plan_only = aggregate(records)

    today_str = datetime.now().strftime('%Y/%m/%d  %H:%M')

    # 結果を results/review_YYYYMMDD_HHMM.txt にも保存
    results_dir = os.path.join(script_dir, 'results')
    os.makedirs(results_dir, exist_ok=True)
    out_path = os.path.join(results_dir,
        f'review_{datetime.now().strftime("%Y%m%d_%H%M")}.txt')

    # コンソール+ファイル両方に出力
    class Tee:
        def __init__(self, *streams): self.streams = streams
        def write(self, data):
            for s in self.streams:
                try: s.write(data)
                except Exception: pass
        def flush(self):
            for s in self.streams:
                try: s.flush()
                except Exception: pass

    with open(out_path, 'w', encoding='utf-8') as f:
        sys.stdout = Tee(sys.__stdout__, f)
        try:
            print_report(stats, completed, holding, plan_only, today_str)
            print('\n' + '=' * 65)
            print(f'  集計完了  {datetime.now().strftime("%H:%M:%S")}')
            print('=' * 65)
        finally:
            sys.stdout = sys.__stdout__

    print(f'\n  記録を保存しました:\n  {out_path}')
    input('\n  Enterキーで終了...')


if __name__ == '__main__':
    main()
