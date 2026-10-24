# 草加駅前 ドット絵マップ (Soka Station pixel-art walkable map)

草加駅 (埼玉県草加市) の駅前、約 240 m x 240 m を、ポケモン風の 2D ドット絵で歩けるようにした、
フロントエンドだけの静的サイトです。サーバー処理・外部通信はありません。

## 遊び方

    python3 -m http.server 8765
    # ブラウザで http://127.0.0.1:8765/ を開く

- PC: 矢印キー / WASD
- スマホ縦持ち: 画面下の四角い十字ボタン
- 建物・線路・水面には入れません。駅の下をくぐる通路 (OSM の building_passage / corridor) は通れます。
- `?debug` を付けると `window.__soka.teleport(x, y, dir)` が使えます (テスト用)。

## 縮尺

- 1 タイル = 3 m、タイル画像 16x16 px、マップ 80x80 タイル (240 m 四方)
- 表示は整数倍 (スマホ 3 倍 x 画面密度、PC 4 倍)、`image-rendering: pixelated`

## データ -> マップの流れ

1. `tools/fetch_osm.py` : OpenStreetMap から駅ノード (id 7481965874) 中心 ±165 m を取得 (Overpass 優先、ダメなら OSM API 0.6 /map)。
   個人情報を避けるため、住所・番地・電話・店名・ユーザー名などは保存せず、必要なタグだけ残し、名前は駅と公共性の高い施設だけにします。
   結果: `data/osm_raw.json`
2. `tools/make_art.py` : Pillow で全アートを生成 (`assets/tiles.png`, `tiles.json`, `player.png`, `sign_soka.png`)。ポケモンの素材は使っていません。
3. `tools/build_map.py` : OSM を 3 m グリッドに変換し、道路・歩道・芝・樹木・建物 (屋根行+壁行)・駅 (高架)・横断歩道・小物を配置して `assets/map.json` を作成。
   手直しは `data/overrides.json` (任意) に `{"set":[{"x":..,"y":..,"base":"タイル名","solid":true}]}` と書きます。
4. `tools/render_overview.py out.png [倍率] [--solid]` : 全体図を PNG にします (確認用)。
5. `tools/e2e/e2e.js` : puppeteer-core による動作確認 (キー移動、衝突、D-pad タッチ、横スクロールなし、コンソールエラーなし、スクリーンショット)。

    python3 tools/fetch_osm.py && python3 tools/make_art.py && python3 tools/build_map.py

## 仮定と注意

- 駅の線路はすべて高架 (bridge=viaduct) として描画し、補助線 (側線・留置線) は線路でなく床として単純化しています。
- 駅ビル (VARIE) と線路・ホームを 1 つの「高架駅」領域として扱い、通路タイルだけが歩けます。
- 街灯は OSM のデータではなく、道路沿いに自動配置した装飾です。
- 3 m グリッドのため、細い道や歩道は実際の幅と一致しません。

## ライセンス・出典

- 地図データ © OpenStreetMap contributors (ODbL) — https://www.openstreetmap.org/copyright
  `data/osm_raw.json` と、そこから作った `assets/map.json` は ODbL の対象です。再配布時は出典表示と同じライセンスでの共有が必要です。
- 画面の「草加駅」看板は Noto Sans CJK JP Bold (SIL Open Font License 1.1) で作った 1bit ビットマップのみ同梱しています (フォント本体は同梱しません)。
- その他のコード・ドット絵 (スクリプト生成) の公開ライセンスは未決定です。公開前に決めてください。
