# vidgrab

> A tiny command-line tool for AI agents: give it a TikTok / YouTube / Douyin / X link (or a whole share text),
> it resolves the video through [Apify](https://apify.com), downloads it, and prints one line of JSON with the
> local file and the playable URL. Python 3.8+, standard library only. Downloads older than 7 days are cleaned up
> automatically. Agents: read [AGENTS.md](AGENTS.md).

給 AI agent 用的影片解析下載小工具。丟給它一條 TikTok、YouTube、抖音或 X 的連結（整段分享文案也可以），它會用 Apify 解析出影片的直連地址，下載到本地，最後輸出一行 JSON，裡面有**本地檔案**和**播放地址**。agent 拿到後，把兩樣東西一起發給主人就行。

## 特點

- **單檔、零依賴**：只有 `vidgrab.py` 一個檔案，只用 Python 標準庫，不用 `pip install`。
- **貼整段分享文案也行**：例如抖音的「4.82 复制打开抖音… https://v.douyin.com/xxx/ 复制此链接…」，會自動抽出第一個連結。
- **選最合適的片源**：無水印優先；預設最高 1080p；優先 H.264 mp4，手機都能播。
- **YouTube 高畫質**：YouTube 的高畫質版本，畫面和聲音是分開的兩個檔案。如果有 `ffmpeg`，就分別下載再合併（不重新編碼，很快）；沒有 `ffmpeg` 時，改下載本身帶聲音的版本（畫質較低），並在輸出裡註明。
- **不被限速**：用 10MB 一段的分段請求下載，避開 YouTube 對整檔下載的限速。
- **7 天自動清理**：每次執行都會順手刪掉下載資料夾裡超過 7 天的檔案。只刪自己產生的 `vidgrab-*` 檔案，資料夾裡的其他東西不碰。
- **省一半費用**：Apify 用 256MB 記憶體跑，每條影片約 **$0.093**；actor 預設的 2GB 要約 $0.183。Apify 免費方案每月 $5，大約夠 50 條影片。

## 安裝

```bash
git clone https://github.com/jiebaosystem/vidgrab.git
cd vidgrab
cp .env.example .env        # 填入 APIFY_TOKEN
python3 vidgrab.py --version
```

- **Apify token**：在 [Apify Console → Settings → API & Integrations](https://console.apify.com/settings/integrations) 取得。可以寫在 `vidgrab.py` 旁邊的 `.env` 裡，也可以設成環境變數 `APIFY_TOKEN`；環境變數優先。
- **ffmpeg**（可選，建議裝）：macOS 用 `brew install ffmpeg`，Debian/Ubuntu 用 `apt install ffmpeg`。

## 用法

```bash
python3 vidgrab.py "https://v.douyin.com/qTaYgDk_lwY/"
python3 vidgrab.py "4.82 复制打开抖音，看看【…】 https://v.douyin.com/qTaYgDk_lwY/ 复制此链接…"
python3 vidgrab.py https://www.youtube.com/watch?v=aqz-KE-bpKQ --max-height 720
python3 vidgrab.py resolve https://x.com/…/status/…    # 只解析，不下載
python3 vidgrab.py clean                                # 手動清理過期檔案（平常不需要）
```

成功時輸出一行 JSON，退出碼為 0：

```json
{"ok": true, "platform": "douyin", "title": "示例视频标题", "author": "示例作者",
 "source_url": "https://v.douyin.com/qTaYgDk_lwY/",
 "play_url": "https://v11-o.douyinvod.com/…",
 "quality": "720p",
 "file": "/Users/me/vidgrab/vidgrab-20261015-053225-douyin-7695006267269467418.mp4",
 "size": 210260311, "expires": "2026-10-22"}
```

| 欄位 | 說明 |
|---|---|
| `platform` | `douyin` / `tiktok` / `youtube` / `x` / `other` |
| `source_url` | 從輸入中抽出的原始連結 |
| `play_url` | 解析出的直連地址，可以直接在瀏覽器或播放器打開。**通常幾小時後失效**（YouTube 約 6 小時） |
| `audio_url` | 只在 YouTube 畫面和聲音分開時出現。此時 `play_url` 只有畫面 |
| `file`、`size` | 下載的檔案和大小（bytes） |
| `expires` | 這個檔案會在這天之後被自動清理 |
| `note` | 有需要注意的情況時才出現，例如沒有 ffmpeg 所以退到低畫質 |

失敗時輸出 `{"ok": false, "stage": "…", "error": "…"}`，退出碼為 1。`stage` 標示失敗在哪一步：

| `stage` | 意思 |
|---|---|
| `input` | 輸入裡沒有連結 |
| `config` | 沒有設定 `APIFY_TOKEN` |
| `resolve` | Apify 解析失敗，例如影片已刪除、私人影片、平台暫時擋住 |
| `download` | 解析成功但下載失敗，例如地址過期或被拒 |

## 設定（環境變數）

| 變數 | 預設 | 說明 |
|---|---|---|
| `APIFY_TOKEN` | （必填） | Apify API token |
| `VIDGRAB_DIR` | `~/vidgrab` | 下載資料夾 |
| `VIDGRAB_KEEP_DAYS` | `7` | 下載的檔案保留幾天 |

## 解析用的 Apify actor

| 平台 | actor |
|---|---|
| 抖音 | [`easyapi/douyin-video-downloader`](https://apify.com/easyapi/douyin-video-downloader) |
| TikTok、YouTube、X、其他 | [`easyapi/all-in-one-media-downloader`](https://apify.com/easyapi/all-in-one-media-downloader) |

兩個 actor 都按事件計費：啟動費每 GB 記憶體 $0.09（不足 1GB 也按 1GB 算），每條結果另收約 $0.003。vidgrab 固定用 256MB 呼叫，2 秒左右就能跑完，每條約 $0.093。

## 測試

```bash
python3 -m unittest discover -s tests     # 離線測試，不連網、不花錢
```

## 授權

MIT
