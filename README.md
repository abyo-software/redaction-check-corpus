# redaction-check-corpus

黒塗り（墨消し）の消し残りを検査するための、判定基準とテスト用 PDF の一式です。
The criteria and test PDFs for checking whether redactions in a PDF really removed what they cover.

- 判定基準：`redaction-check-spec.json`（`specVersion` 付き）
- 期待する判定：`manifest.json`
- テスト用 PDF：`files/`（1 ファイルに 1 つの失敗パターン）
- 生成スクリプト：`generate.py`、`16_same_name.py`（Python 3 の標準ライブラリだけで動き、毎回同じバイトを出力します）

この基準は、次の 2 つの実装が共通に使っています。

- 無料の墨消しチェッカー（ブラウザ版）: https://abyo.net/pdf/redaction-check/ （英語: https://abyo.net/pdf/en/redaction-check/ ）
- abyo PDF Editor の保存前検査（開発中）

どちらの実装も、このリポジトリの全ファイルについて `manifest.json` と同じ判定（verdict）を出すことをテストで確かめています。
`expect.checks` に挙げた検査項目は必ず出す必要があり、`mustNotFlag` に挙げた項目は出してはいけません。それ以外の項目を追加で出すことは許されます。

`manifest.json` が定めるのは、基準に沿うどの実装も出すべき結果です。圧縮された古い版を読めるか、壊れたファイルを修復して開けるかといった能力の差で結果が変わるケース（#111、#112 など）は、弱い側に合わせた期待値にし、`acceptVerdicts` と注記で示しています。それより強い結果は、各実装が自分のテストで確かめます。
特定のソフトに甘い基準にしないため、ほかのソフトで正しく墨消ししたファイルが「問題なし」になることも確かめる方針です
（Acrobat で処理したファイル #12 は準備中です）。

## 判定の段階

| verdict | 表示 |
|---|---|
| `fail` | 問題あり（読める情報が残っている） |
| `review` | 要確認（人の判断が必要、または検査できなかった） |
| `pass` | 検査した項目で問題は見つかりませんでした |

検査できなかった項目は `checksSkipped` に必ず残し、「問題なし」とは扱いません。
この基準は、塗った箇所が本当に消えているかを調べるもので、塗るべき箇所をすべて塗ったかどうかは判断しません。
画像の中に写っている文字の内容も読み取りません。

## ファイルを作り直す

    python -I generate.py files
    python -I 16_same_name.py files

`13-*` は abyo PDF Editor で実際に墨消しした出力なので、作り直せません（作り方は `manifest.json` の `madeBy` にあります）。

## 新しいケースの提案

誤検出や見逃しの例があれば Issue で教えてください。個人情報を含む実際の文書は送らないでください。

## License

CC0 1.0 Universal. 判定基準・期待値・テスト用 PDF・生成スクリプトはパブリックドメインとして提供します。ほかの検査ツールの検証にも自由に使えます。
