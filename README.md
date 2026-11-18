# 羽田空港 駐車場チェッカー

羽田空港公式サイトの「駐車場（混雑情報）」から P1〜P5 の状態（空車／混雑／満車／エラー）を自動で取得し、
スマホで見やすく表示する Web アプリです。自分のターミナルが混雑・満車のときは、
**① 空港内で空いている別の駐車場** と **② 周辺駐車場（国内線専用のものは国際線選択時に除外）** を提示します。

## しくみ

```
GitHub Actions（10分ごと, 日本時間 5:00〜23:50）
  └ Playwright で公式ページを描画 → P1〜P5 の状態を読み取り
      └ docs/data.json, docs/history.json をコミット
GitHub Pages（docs/）
  └ index.html が data.json を読み込んで表示（1分ごと・画面復帰時に再読込）
```

公式ページの混雑表示は JavaScript で描画されていて公開 API もないため、ブラウザ（Playwright）で実際に描画してから読み取っています。
P1〜P5 の対応づけは **地図上の位置**（上段左 P3／上段右 P4／中段左 P2／中段右 P1／最下段 P5）で判定し、
DOM 上の並び順とも照合して、食い違えば画面に警告を出します。

## セットアップ（15分程度）

1. GitHub で **新しいリポジトリ** を作る（無料プランで Pages を使うには Public）。
2. このフォルダの中身をすべてアップロード（`.github` フォルダも含めること）。
3. **Settings → Pages** → Source: *Deploy from a branch* → Branch: `main` / フォルダ: `/docs` → Save。
4. **Settings → Actions → General** → *Workflow permissions* を **Read and write permissions** にする。
5. **Actions** タブ → 「羽田駐車場の混雑状況を取得」→ **Run workflow** で初回実行。
   緑のチェックになり、`docs/data.json` が更新されれば成功。
6. `https://<ユーザー名>.github.io/<リポジトリ名>/` をスマホで開き、ホーム画面に追加。

## 注意点・限界

- **反映の遅れ**: 公式は3分ごと更新、本アプリは10分ごと取得。GitHub Actions の定時実行は混雑時に
  数分〜数十分遅れることがあります。25分以上古いデータは画面に警告が出ます。出発直前は公式ページも確認してください。
- **周辺駐車場の満空は自動取得していません**。調べた範囲では、羽田エアポートガーデンの満空欄はページ上で
  時刻がプレースホルダのままで、TCAT の満空（iPosNet）は自動取得を禁止（robots）しているため、
  リンクで開く形にしています。一覧は `docs/nearby.json` を編集すれば変えられます。
- **公式ページの改修で壊れる可能性**があります。取得に失敗すると Actions が赤になり、画面に「取得失敗」と表示されます。
  `data.json` の `json_endpoints_seen` に公式ページが読み込んだ JSON の URL が記録されるので、
  API が見つかれば Playwright なしの軽い取得方式に切り替えられます。
- **リポジトリ**: 10分ごとのコミットで履歴が増えます（容量はごくわずか）。60日間まったく活動がない
  リポジトリでは定期実行が止まる仕様がありますが、本ワークフロー自身のコミットで通常は維持されます。
- 取得頻度は1時間あたり最大6回と控えめにしています。個人利用の範囲でお使いください。

## ファイル

| パス | 役割 |
|---|---|
| `scraper/scrape.py` | 公式ページから状態を取得して `docs/data.json` / `docs/history.json` を書く |
| `.github/workflows/scrape.yml` | 10分ごとの定期実行 |
| `docs/index.html` | Web アプリ本体 |
| `docs/nearby.json` | 周辺駐車場の一覧（公式「近隣駐車場のご紹介」をもとに作成） |

ローカルで試す場合:

```bash
pip install -r requirements.txt && python -m playwright install chromium
python scraper/scrape.py      # docs/data.json が更新される
cd docs && python -m http.server 8000   # http://localhost:8000 で表示
```
