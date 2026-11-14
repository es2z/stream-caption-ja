# stream-caption-ja

ゲーム配信向けのリアルタイム字幕システムです。話した日本語をローカルAIで認識し（必要なら英語に翻訳して）、YouTube / Twitch のプレイヤーの **字幕（CC）** として表示します。映像に焼き付けるのではなく、視聴者がCCボタンをオンにしたときだけ表示されます。

```
マイク ─▶ ブラウザ(localhost:7877) ─▶ [WSL2 / Docker]
                                      ├ 文の区切り(無音検出)
                                      ├ 日本語認識  Haas-ja-v1.0
                                      ├ 英語翻訳    PLaMo 3 Translate 0.8B (llama.cpp)
                                      └ 字幕送信 ─┬▶ OBS (obs-websocket) ─▶ CEA-608 ─▶ YouTube / Twitch の CC
                                                  └▶ YouTube 字幕取り込み URL (HTTP POST) ─▶ YouTube の CC
```

| モード | 字幕 | 送り先 |
|---|---|---|
| `en`（英語に翻訳） | 英語 | OBS → YouTube・Twitch |
| `ja`（日本語のまま） | 日本語 | YouTube（HTTP POST）のみ |
| `both`（英語＋日本語） | 日本語と英語の2行（並び順を選べる） | YouTube（HTTP POST）は2行、OBS には英語だけ |

> **日本語字幕について**: OBS が使う CEA-608 という字幕規格は英語・欧州の言語の文字しか送れません。
> そのため日本語字幕は YouTube の HTTP POST 方式でだけ出せます。Twitch には日本語の CC を出す手段がありません。

## ⚠ 設定を変えたら（反映方法）

**ファイルを書き換えただけでは反映されません。** 変えたものに応じて、WSL で `stream-caption-ja` フォルダに入って次のコマンドを実行してください。

> **先に Docker Desktop を起動しておくこと。** WSL の `docker` コマンドは Docker Desktop が動いている間だけ使えます。
> 止まっていると `docker: No such file or directory` などのエラーになります（→「[困ったとき](#困ったとき)」）。

| 変えたもの | 実行するコマンド |
|---|---|
| `config/config.yaml`（辞書・用語集・無音判定など） | `docker compose restart app` |
| `.env`（OBS のパスワード、YouTube の URL、翻訳モデルなど） | `docker compose up -d` |
| `docker-compose.yml` | `docker compose up -d` |
| プログラムの更新（`git pull` したあと） | `docker compose up -d --build` |

- **`.env` は `restart` では読み直されません。** 必ず `up -d` を使ってください（変わったコンテナだけが作り直されます）
- `config.yaml` の場合は、Docker Desktop の Containers 画面で `app` の再起動ボタン（↻）を押しても同じです
- 反映できたかは `docker compose logs -f app` で「準備ができました」が出るのを見て確認できます（Ctrl+C でログ表示だけ終わります）
- 画面で切り替えたモードや OBS / YouTube のオン・オフは、再起動すると `config.yaml` / `.env` の値に戻ります
- ディスクの使用量が大きく増える可能性があるのは `--build` だけです（ふだんは数MB 程度。詳しくは「[ディスクの容量](#ディスクの容量)」）

## 必要なもの

- Windows 11 + WSL2
- NVIDIA GPU（Haas は CUDA 必須）と最新の NVIDIA ドライバー（Windows 側に入れるだけで WSL2 から使えます）
- Docker（次のどちらか）
  - **Docker Desktop（WSL2 バックエンド）** … おすすめ。GPU とネットワークの設定がほぼ不要
  - WSL 内に直接入れた Docker Engine + NVIDIA Container Toolkit
- OBS Studio 30 以降（obs-websocket 5 が組み込み済み）
- ディスク 40GB 程度の空き（Docker 全体で約 35GB 使います）

## セットアップ

```bash
# WSL のシェルで
git clone https://github.com/es2z/stream-caption-ja.git
cd stream-caption-ja
cp .env.example .env
cp config/config.example.yaml config/config.yaml
# .env の OBS_PASSWORD を設定する
docker compose up -d --build
docker compose logs -f app     # 「準備ができました」と出るまで待つ(初回はモデルのダウンロードで数分)
```

初回のビルドは PyTorch / NeMo を入れるため時間がかかります（イメージは十数GB）。

### OBS の設定

1. OBS の「ツール → WebSocket サーバー設定」で「WebSocket サーバーを有効にする」をオン
2. 「認証を有効にする」をオンにしてパスワードを決め、`.env` の `OBS_PASSWORD` に書く
3. 配信の映像エンコーダは **H.264**（x264 / NVENC H.264）にする。HEVC / AV1 では CEA-608 字幕が通らない場合があります

OBS 側に入れるプラグインやスクリプトはありません。字幕は OBS が配信中のときだけ送られます。

### 配信サイトの設定

- **YouTube（英語・CEA-608）**: YouTube Studio のライブ配信設定 → 字幕をオン → 「埋め込み 608/708」を選ぶ
- **YouTube（日本語・HTTP POST）**: 字幕をオン → 「POST 字幕を URL に送信」を選び、言語を日本語にする。表示される字幕取り込み URL を `.env` の `YOUTUBE_CAPTION_URL` に書くか、画面の入力欄に貼る。配信の遅延は「通常の遅延」にする（HTTP POST 字幕は低遅延モードでは使えません）
- **Twitch**: 設定は不要です。字幕が届くと、プレイヤーに CC ボタンが出ます

YouTube は1つの配信につき字幕を1系統しか受け付けません。英語（608）と日本語（POST）は同時に出せないので、どちらかを選んでください。

### 使い方

1. Windows のブラウザ（Chrome / Edge）で <http://localhost:7877> を開く
2. マイクを選んで「開始」を押す（初回はマイクの許可を求められます）
3. 話すと、ログに認識結果と字幕が流れます。「テスト用の字幕」から、しゃべらずに送信経路を確認することもできます

モードと並び順の切り替え、OBS / YouTube のオン・オフは画面からできます（再起動すると config.yaml の値に戻ります）。

### 起動・停止

`docker-compose.yml` に `restart: unless-stopped` を指定しているので、一度 `docker compose up -d` したあとは、**Docker Desktop が起動すると自動で立ち上がります**（Docker Desktop の設定「Start Docker Desktop when you sign in」をオンにしておけば、Windows へのサインインだけで準備完了になります）。手動で止めた場合は、次に手動で起動するまで止まったままです。

- **Docker Desktop の画面から**: 左の Containers に `stream-caption-ja` があるので、▶（起動）/ ■（停止）を押す
- **WSL のコマンドで**（`stream-caption-ja` フォルダ内）:
  - 起動: `docker compose start`（初回や設定変更後は `docker compose up -d`）
  - 停止: `docker compose stop`（VRAM も解放されます）
  - 削除: `docker compose down`（コンテナを消す。ダウンロード済みのモデルは残ります）
  - ログを見る: `docker compose logs -f app`（Ctrl+C はログ表示をやめるだけで、コンテナは動き続けます）

## 更新

```bash
cd ~/stream-caption-ja
git pull
docker compose pull llama      # llama.cpp も新しくする場合
docker compose up -d --build
docker image prune -f          # 古いイメージを消す
```

`.env` と `config/config.yaml` は git の管理外なので、更新しても上書きされません。新しい設定項目は `config/config.example.yaml` を見て、必要なものだけ自分の `config.yaml` に足してください（書かなければ既定値で動きます）。

更新のあとに Windows 側のディスクの空きを取り戻したいときは、「[ディスクの容量](#ディスクの容量)」の掃除を実行してください。

> ブラウザのタブは開いたままにしてください。Chrome は裏に回したタブの処理を抑えることがありますが、マイクを使っているタブは通常抑えられません。

## ディスクの容量

### どの操作で増えるか

| 操作 | ディスクの増え方 |
|---|---|
| `docker compose restart app` / `docker compose up -d` | ほとんど増えない |
| `docker compose up -d --build`（プログラムだけ変わった場合） | 数MB 程度 |
| `docker compose up -d --build`（下の「大きく増える場合」） | **約 11GB 増える** |
| `docker compose pull llama`（新しい llama.cpp があった場合） | 約 7GB 増える |

**大きく増える場合**: PyTorch などが入った約 11GB の部分は、`app/Dockerfile` の次の行が変わったときに作り直されます（自分で書き換えたときや、そういう更新を `git pull` したとき）。

- `FROM python:...`（ベースイメージ）
- `COPY --from=ghcr.io/astral-sh/uv:...`（uv の版）
- `ARG HAAS_COMMIT=...`（Haas-ASR の版）

ベースイメージと uv は版を固定してあるので、配布元が更新されただけで作り直しになることはありません。

作り直したあとも古いものは Docker の中に残ります。しかも Docker の仮想ディスク（`docker_data.vhdx`）は、**中身を消しても Windows 側のファイルが自動では小さくなりません**。空きを取り戻すには、次の掃除を実行します。

### 掃除する（まとめて一度に）

Windows の PowerShell で次を実行します（`<ユーザー名>` は WSL のユーザー名に置き換え）。**WSL のシェルからは実行しないでください。** 途中で WSL を止めるためです。

```powershell
powershell -ExecutionPolicy Bypass -File \\wsl.localhost\Ubuntu-24.04\home\<ユーザー名>\stream-caption-ja\scripts\cleanup-disk.ps1
```

やること:

1. 使われなくなった古いイメージを消す
2. 今のイメージで使っていない古いビルドキャッシュを消す
3. Docker Desktop と WSL を止めて仮想ディスクを圧縮し、Docker Desktop を起動し直す（数分。字幕アプリは自動で立ち上がります）

- 消さないもの: 今のイメージ、モデル、`.env` / `config.yaml`、uv のダウンロードキャッシュ（約 7GB。再ビルドでダウンロードし直さないためのもの）
- 圧縮には管理者権限が必要です。管理者でない PowerShell から実行した場合は、圧縮の手前で確認画面が出ます
- 開いている WSL のターミナルやエディタは閉じられます。作業中のものは保存してから実行してください
- 掃除のあとも、次の `--build` で作り直しにはなりません

| オプション | 内容 |
|---|---|
| `-IncludeDownloadCache` | uv のダウンロードキャッシュ（約 7GB）も消す。次の `--build` で PyTorch などをダウンロードし直します |
| `-SkipCompact` | Docker を止めずに、中の掃除だけする（Windows 側の空きは増えません。管理者権限も要りません） |
| `-Yes` | 開始前の確認を省く |

## マイクの取り込み方（Motu → Cakewalk → RVC の場合）

字幕用の音声は、**ボイスチェンジャーに通す前の声（地声）** を使うのがおすすめです。変換後の声より認識精度が上がります。ブラウザが選べる入力デバイスに地声が出ていれば使えるので、次のどれかで分岐させます。

1. **RVC が受け取っている入力デバイスをそのまま選ぶ（いちばん簡単）**
   Cakewalk の出力が仮想ケーブルや Motu のループバック経由で RVC に入っているなら、ブラウザでも同じ入力デバイスを選べば、同時に取り込めます（WASAPI 共有モード）。RVC がそのデバイスを排他モードや ASIO で開いている場合はこの方法は使えません。
2. **Motu のミキサーで分岐する**
   CueMix などで、マイク入力を別のループバック チャンネルにも送ります。Cakewalk / RVC の経路はそのままです。対応はモデルによります。
3. **Cakewalk のセンドで分岐する**
   ボーカル トラックからセンド（プリフェーダー）で別の出力に送り、その出力を仮想オーディオデバイス（VB-CABLE など）に割り当てます。Cakewalk が ASIO で Motu だけを使っている場合は、ASIO から出せる仮想デバイス（VoiceMeeter の ASIO 挿入など）が必要です。

DAW で EQ・コンプをかけた音を使う場合は、画面の「ノイズ抑制・エコー除去・自動音量調整」はすべてオフにしてください（既定でオフ）。メーターの黄色い線（`silence_dbfs`）より声が十分大きく、無音時はそれより小さくなるように調整します。

## 困ったとき

### `docker` コマンドが使えない

```
-bash: /usr/bin/docker: No such file or directory
failed to connect to the docker API at npipe:////./pipe/docker_engine ...
The command 'docker' could not be found in this WSL 2 distro. We recommend to activate the WSL integration ...
```

どちらも **Docker Desktop が起動していない**（または WSL 連携が切れている）ときに出ます。WSL の `/usr/bin/docker` は、Docker Desktop が動いている間だけ使えるようになるものです。PC を再起動したあとによく起きます。

1. Windows で Docker Desktop を起動し、左下が「Engine running」になるまで待つ
2. WSL で `hash -r` を実行する（直らなければターミナルを開き直す）
3. もう一度コマンドを実行する

3つ目のメッセージ（`could not be found in this WSL 2 distro`）が出たときや、それでも直らないときは、**WSL 連携がオフ**になっています。

1. Docker Desktop の設定を開き、検索欄に「WSL」と入力する（または「Go to classic Settings」→ Resources → WSL integration）
2. 使っている Ubuntu のスイッチをオンにして「Apply」（従来の画面では「Apply & restart」）
3. WSL のターミナルを開き直す

Ubuntu が一覧に出ないときは、PowerShell で `wsl -l -v` を実行し、VERSION が `2` になっているか確認してください（`1` なら `wsl --set-version Ubuntu 2`）。アップデートで連携がオフに戻ることもあります。

**毎回起動するのが面倒なら**: Docker Desktop の設定で「Start Docker Desktop when you sign in」をオンにすると、Windows へのサインインだけで Docker Desktop が起動し、このアプリのコンテナも自動で立ち上がります。

### 画面（localhost:7877）が開けない

- Docker Desktop が起動しているか確認する
- `docker compose ps` で `app` が `running` になっているか見る。なっていなければ `docker compose logs app` でエラーを確認する
- 初回や再ビルド後はモデルの読み込みに1分ほどかかります。ログに「準備ができました」と出るまで待つ
- `running` なのに開けないときは、ログの「準備ができました: http://localhost:....」の番号を見る。7877 でなければコンテナが古いままなので、`docker compose up -d --build` で作り直す

### Docker がおかしい・起動しない（ディスクがいっぱい）

C ドライブの空きがなくなると、Docker の仮想ディスクに書き込めなくなり、次のようなエラーが出ます。

```
input/output error
OCI runtime exec failed: exec: "runc": executable file not found in $PATH
preparing block device /dev/sdX: ... is not usable: no such device or address
```

1. C ドライブの空きを作る（数GB 以上）
2. Docker Desktop を終了し、Windows の PowerShell で `wsl --shutdown` を実行する
3. Docker Desktop を起動し直す（コンテナは自動で立ち上がります）

直ったら、「[ディスクの容量](#ディスクの容量)」の掃除で Docker が使っている分を減らしておくと再発しにくくなります。

## ネットワーク（OBS に繋がらないとき）

コンテナから Windows 上の OBS へは `host.docker.internal:4455` で接続します。

- **Docker Desktop**: そのまま繋がります。Windows ファイアウォールが OBS の受信を止めていないか確認してください
- **WSL 内の Docker Engine**: `host.docker.internal` は WSL の VM を指すので Windows に届きません。次のどちらかにします
  - WSL をミラー モードにする（`%UserProfile%\.wslconfig` に `[wsl2]` `networkingMode=mirrored`）。そのうえで `.env` を `OBS_HOST=127.0.0.1` にし、`docker-compose.yml` の app に `network_mode: host` を足す（その場合 `ports` と `extra_hosts` は外す）
  - NAT モードのまま、`.env` の `OBS_HOST` に Windows の IP（WSL で `ip route show default` の via の値）を書く

## 設定

`config/config.yaml`（変更したら `docker compose restart app`。反映方法の一覧は「[⚠ 設定を変えたら](#-設定を変えたら反映方法)」）

- `asr.vocabulary`: ゲーム名・キャラ名などを「表記: [読み]」で登録すると、認識されやすくなります
- `translator.glossary`: 「日本語: 英語」で訳語を固定します（その語が出た文にだけ使われます）
- `translator.context`: 配信の説明。訳の方向性が安定します
- `segmenter.pause_seconds`: 文を確定する無音の長さ。短くすると字幕は早く出ますが、文が細切れになります
- `segmenter.silence_dbfs`: 無音と判定する音量
- `filters.drop_texts`: 「えー」「あー」など、それだけなら字幕にしない言葉

翻訳モデルは `.env` の `PLAMO_MODEL` で変えられます（標準は `pfnet/plamo-3-translate-0.8b-GGUF:Q4_K_M`。2B にする場合は `pfnet/plamo-3-translate-2b-GGUF:Q4_K_M`）。

## 負荷と遅延の目安

ここに書いた値は実測値ではなく推定です。

| 項目 | 目安 |
|---|---|
| VRAM | 合計 3〜4GB 程度（Haas 約 2GB + PLaMo 0.8B 約 1GB） |
| GPU 使用率 | 文が確定した瞬間に短く使うだけで、平均は数%以下 |
| ディスク | Docker 全体で約 35GB（イメージ約 19GB + ビルド用キャッシュ約 7GB + モデル約 2GB など） |
| 遅延 | 話し終わりから字幕の送信まで約 1〜1.5 秒（無音判定 0.7 秒 + 認識・翻訳） |

字幕はエンコード時に映像のデータへ乗るので、配信サイト側の遅延が大きくても映像とのズレは広がりません。実際の処理時間は画面のログの「遅延」欄に出ます。

GPU なしで配線だけ確認したいときは、`.env` に `ASR_BACKEND=dummy` を書いて起動すると、認識のかわりに固定の文が出ます。

## 開発

```bash
cd app
pip install -e ".[dev]"
pytest
```

テストは GPU・OBS・翻訳サーバーを偽物に置き換えて動きます。

## ライセンスについて

- [Haas-ASR / Haas-ja-v1.0](https://github.com/third-intelligence/Haas-ASR): Apache-2.0。`segmenter.py` と `capture-worklet.js` は Haas-ASR のデモを元にしています
- [PLaMo 3 Translate](https://huggingface.co/pfnet/plamo-3-translate-0.8b-GGUF): PLaMo コミュニティライセンス。個人の私的利用は無料・登録不要です。収益化した配信など商用利用に当たる場合は、売上10億円以下なら PFN のフォームで事前登録すれば無償で使えます
