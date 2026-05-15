# 日本株スイング 自動分析システム

楽天証券口座での日本株スイングトレード用の半自動シグナル生成ツール。

## 機能

- 📊 楽天証券スーパースクリーナーCSVを使った完全分析(ローカル)
- 💸 yfinance ベースの完全自動化(クラウド)
- 📉 Q1〜Q11 の13関門スコアリング
- 📰 ニュースキーワード自動スキャン(Q8)
- 📅 決算日チェック(Q9)
- 📨 Discord 通知(iPhone プッシュ対応)
- ⏰ GitHub Actions で毎朝自動シグナル送信

## ファイル構成

| ファイル | 役割 |
|---------|------|
| `swing_analyzer.py` | メイン分析(楽天CSV使用、PCで実行) |
| `trade_review.py` | 紙トレ集計、連敗判定(Q13) |
| `notifier.py` | Discord 通知モジュール |
| `notify_daily.py` | GitHub Actions 用 完全自動通知 |
| `core_universe.py` | 監視銘柄ユニバース(~80銘柄) |
| `financial_filter.py` | yfinance ベース財務フィルター |
| `watchlist.json` | 連続通過銘柄の追跡記録 |

## ローカル実行

### 朝の分析(楽天CSV使用)
```
run_analysis.bat をダブルクリック
```

### 紙トレ集計
```
review.bat をダブルクリック
```

### Discord 通知設定
```
通知設定.bat をダブルクリック
```

## クラウド運用

- GitHub Actions で毎朝 06:30 JST に自動実行
- yfinance で財務データ取得 → STEP1 財務フィルター
- Q1〜Q11 のチャート審査
- Discord にプッシュ通知

## 必要環境

- Python 3.11 以上
- Windows / macOS / Linux
- 楽天証券 国内株式口座

## 注意

- 本ツールは情報提供のみ目的、投資判断は自己責任で
- 個人利用を目的としています

## License

個人利用のみ。
