"""
羽田空港 P1〜P5 駐車場の混雑状況を公式サイトから取得して docs/data.json に書き出す。

公式ページ（https://tokyo-haneda.com/access/parking/index.html）は
混雑状況を JavaScript で描画しているため、Playwright でページを実際に描画してから読み取る。

P1〜P5 の対応づけは 2 通りで行い、食い違えば警告を出す:
  1) 画面上の位置（地図上: 上段左=P3 上段右=P4 中段左=P2 中段右=P1 最下段=P5）
  2) DOM 上の並び順（2026-11 時点では P1, P2, P3, P4, P5 の順）
位置による判定を優先する（ページの HTML 構造が変わっても地図のレイアウトは変わりにくいため）。
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from playwright.sync_api import sync_playwright

URL = "https://tokyo-haneda.com/access/parking/index.html"
JST = timezone(timedelta(hours=9))
ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "docs" / "data.json"
HISTORY = ROOT / "docs" / "history.json"
HISTORY_MAX = 300  # 10分間隔で約2日分

STATUSES = ["空車", "混雑", "満車", "エラー"]

LOTS = {
    "P1": {"terminal": "T1", "label": "第1ターミナル", "kind": "国内線", "capacity": 2351,
           "map": "https://goo.gl/maps/k2QZcz9SF7FT7joK8", "reserve": None},
    "P2": {"terminal": "T1", "label": "第1ターミナル", "kind": "国内線", "capacity": 2315,
           "map": "https://goo.gl/maps/xY1oV6vBzgLGnSs78",
           "reserve": "https://hnd-rsv.aeif.or.jp/airport2/app/toppage"},
    "P3": {"terminal": "T2", "label": "第2ターミナル", "kind": "国内線", "capacity": 2449,
           "map": "https://goo.gl/maps/H79SwVtMrVHywj2k9",
           "reserve": "https://hnd-rsv.aeif.or.jp/airport2/app/toppage"},
    "P4": {"terminal": "T2", "label": "第2ターミナル", "kind": "国内線", "capacity": 3087,
           "map": "https://goo.gl/maps/a9qE5ywJTgk3XmRe6",
           "reserve": "https://haneda-p4.jp/airport/entrance/0000.jsf"},
    "P5": {"terminal": "T3", "label": "第3ターミナル", "kind": "国際線", "capacity": 2910,
           "map": "https://goo.gl/maps/aw7RLP4Rtc4KQcSc9",
           "reserve": "https://pk-reserve.haneda-airport.jp/airport/entrance/0000.jsf"},
}

# ページ内で実行する抽出ロジック。
# 「P3」リンク（aeif.or.jp へのリンク）を起点に、凡例(li)を除いた
# 状態表示要素(空車/混雑/満車/エラー)を5つ含む最小の祖先要素を探す。
EXTRACT_JS = r"""
(statuses) => {
  const isStatusLeaf = (el) =>
    el.children.length === 0 &&
    statuses.includes((el.textContent || '').trim()) &&
    !el.closest('li');
  const collect = (root) => Array.from(root.querySelectorAll('*')).filter(isStatusLeaf);

  let anchor = Array.from(document.querySelectorAll('a'))
    .find(a => (a.textContent || '').trim() === 'P3' && (a.href || '').includes('aeif.or.jp'));
  let container = null;
  if (anchor) {
    let node = anchor.parentElement;
    for (let i = 0; i < 6 && node; i++, node = node.parentElement) {
      if (collect(node).length >= 5) { container = node; break; }
    }
  }
  if (!container) {
    // フォールバック: ページ全体から凡例以外の状態表示を拾う
    container = document.body;
  }
  const cRect = container.getBoundingClientRect();
  return {
    found_anchor: !!anchor,
    items: collect(container).map((el, i) => {
      const r = el.getBoundingClientRect();
      return {
        dom_index: i,
        text: el.textContent.trim(),
        cls: el.className || '',
        x: r.left + r.width / 2 - cRect.left,
        y: r.top + r.height / 2 - cRect.top,
        w: r.width, h: r.height,
      };
    }),
  };
}
"""


def map_by_position(items: list[dict]) -> dict[str, dict]:
    """地図上の位置から P1〜P5 を割り当てる。"""
    if len(items) != 5:
        raise ValueError(f"状態表示が {len(items)} 個見つかった（5個を想定）")
    by_y = sorted(items, key=lambda it: it["y"])
    p5 = by_y[-1]
    top, mid = by_y[0:2], by_y[2:4]
    # 上段と中段の y が明確に分かれていること、最下段が十分離れていることを確認
    if not (max(t["y"] for t in top) < min(m["y"] for m in mid) - 20 and
            max(m["y"] for m in mid) < p5["y"] - 40):
        raise ValueError("地図レイアウトが想定と異なる（行の判別ができない）")
    top_l, top_r = sorted(top, key=lambda it: it["x"])
    mid_l, mid_r = sorted(mid, key=lambda it: it["x"])
    return {"P3": top_l, "P4": top_r, "P2": mid_l, "P1": mid_r, "P5": p5}


def map_by_dom(items: list[dict]) -> dict[str, dict]:
    return {f"P{i + 1}": it for i, it in enumerate(items[:5])}


def scrape() -> dict:
    json_endpoints: list[str] = []
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(locale="ja-JP", viewport={"width": 1280, "height": 1000},
                                user_agent=("Mozilla/5.0 (personal parking checker; "
                                            "runs every 10 min) Chrome/130 Safari/537.36"))

        # 将来 API を直接叩けるよう、読み込まれた JSON の URL を記録しておく
        def on_response(resp):
            ct = resp.headers.get("content-type", "")
            if "json" in ct and "tokyo-haneda" in resp.url:
                json_endpoints.append(resp.url)
        page.on("response", on_response)

        page.goto(URL, wait_until="networkidle", timeout=60_000)
        # 状態表示が描画されるまで待つ
        page.wait_for_function(
            """(s) => Array.from(document.querySelectorAll('*')).filter(el =>
                 el.children.length===0 && s.includes((el.textContent||'').trim()) && !el.closest('li')
               ).length >= 5""",
            arg=STATUSES, timeout=30_000)
        result = page.evaluate(EXTRACT_JS, STATUSES)
        browser.close()

    items = result["items"]
    warnings: list[str] = []
    if not result["found_anchor"]:
        warnings.append("起点のP3リンクが見つからず、ページ全体から抽出した")

    try:
        mapping = map_by_position(items)
        method = "position"
        dom = map_by_dom(items)
        diff = [k for k in mapping if mapping[k]["text"] != dom[k]["text"]]
        if diff:
            warnings.append(f"位置判定とDOM順判定が不一致: {','.join(diff)}（位置判定を採用）")
    except ValueError as e:
        warnings.append(f"位置判定に失敗: {e}。DOM順で判定")
        if len(items) < 5:
            raise
        mapping = map_by_dom(items)
        method = "dom_order"

    lots = {}
    for key, meta in LOTS.items():
        lots[key] = {**meta, "status": mapping[key]["text"]}

    return {
        "updated_at": datetime.now(JST).isoformat(timespec="seconds"),
        "source": URL,
        "method": method,
        "warnings": warnings,
        "json_endpoints_seen": sorted(set(json_endpoints)),
        "lots": lots,
        "ok": True,
    }


def main() -> int:
    DATA.parent.mkdir(parents=True, exist_ok=True)
    try:
        data = scrape()
    except Exception as e:  # 失敗時は前回値を残しつつ「取得失敗」を記録
        prev = json.loads(DATA.read_text("utf-8")) if DATA.exists() else {}
        data = {**prev, "ok": False,
                "error": f"{type(e).__name__}: {e}",
                "failed_at": datetime.now(JST).isoformat(timespec="seconds")}
        DATA.write_text(json.dumps(data, ensure_ascii=False, indent=2), "utf-8")
        print("取得失敗:", data["error"], file=sys.stderr)
        return 1

    DATA.write_text(json.dumps(data, ensure_ascii=False, indent=2), "utf-8")

    hist = json.loads(HISTORY.read_text("utf-8")) if HISTORY.exists() else []
    hist.append({"t": data["updated_at"],
                 **{k: v["status"] for k, v in data["lots"].items()}})
    HISTORY.write_text(json.dumps(hist[-HISTORY_MAX:], ensure_ascii=False), "utf-8")

    print(json.dumps({k: v["status"] for k, v in data["lots"].items()}, ensure_ascii=False))
    for w in data["warnings"]:
        print("警告:", w, file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
