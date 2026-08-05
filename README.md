# LiveTranslate

繁體中文｜[English](README_en.md)｜[简体中文](README_zh-CN.md)

> 本專案 fork 自 [TheDeathDragon/LiveTranslate](https://github.com/TheDeathDragon/LiveTranslate)（MIT）。本 fork 差異：模型下載一律走 HuggingFace（移除 ModelScope）、首啟精靈移除自動倒數（下載一律手動確認）、繁體中文（台灣）為第一級語言與主文件。

Windows 即時語音翻譯工具。擷取系統音訊（WASAPI loopback）與可選的麥克風輸入，語音辨識後呼叫 LLM API 翻譯，結果顯示在透明的字幕浮窗上。

適用於看外語影片、直播、語音對話等情境——不需修改播放器，全域音訊擷取即開即用。

![Python 3.10+](https://img.shields.io/badge/Python-3.10%2B-blue)
![Windows](https://img.shields.io/badge/Platform-Windows-0078d4)
![License](https://img.shields.io/badge/License-MIT-green)

## 螢幕截圖

![LiveTranslate](screenshot/zh.png)

## 安裝影片

[![安裝示範](https://img.shields.io/badge/Bilibili-安裝示範-00A1D6?logo=bilibili)](https://www.bilibili.com/video/BV1K2Awz6Euw) 適用於看外語影片、直播、ASMR 等情境，也可以用語音輸入即時並行翻譯多種語言（上游作者錄製）

## 功能特性

- **即時翻譯管線**：系統音訊 → VAD → ASR → LLM 翻譯 → 字幕顯示
- **多 ASR 引擎**：faster-whisper、SenseVoice、FunASR Nano、Anime-Whisper
- **遠端 ASR**：透過 HTTP 把語音辨識放到 GPU 機器上跑 —— 見 [REMOTE_ASR.md](REMOTE_ASR.md)
- **相容任意 OpenAI 格式 API**：DeepSeek、Grok、Qwen、GPT、Ollama、vLLM 等
- **串流翻譯顯示**：翻譯結果逐字即時顯示
- **模型獨立設定**：串流傳輸、結構化輸出（JSON）、上下文歷史、停用思考
- **麥克風混音**：可選擇把麥克風輸入混合到系統音訊一起辨識
- **低延遲 VAD**：32ms 音訊區塊 + Silero VAD，自適應靜音偵測
- **透明字幕浮窗**：永遠置頂、滑鼠穿透、可拖曳，14 種配色主題
- **CUDA 加速**：ASR 模型 GPU 推論
- **模型自動管理**：首次啟動精靈，模型一律從 HuggingFace 下載（可設定下載 Proxy）
- **內建效能測試**：比較翻譯模型的速度與品質

## 更新日誌

查看 [繁體中文更新日誌](i18n/CHANGELOG_zh-TW.md) | [简体中文更新日志](i18n/CHANGELOG_zh-CN.md) | [English Changelog](i18n/CHANGELOG_en.md)

## 系統需求

- **作業系統**：Windows 10/11
- **Python**：3.10–3.12（免安裝版不需自行安裝）
- **GPU**（建議）：NVIDIA GPU + CUDA 12.6（RTX 50 系列等 Blackwell 架構需要 CUDA 12.8）
- **網路**：需要能連上翻譯 API 與 HuggingFace

## 快速開始

### 免安裝版（免裝 Python，推薦新手）

從 [Releases](https://github.com/moonstarsky37/LiveTranslate/releases) 下載 `LiveTranslate-portable-*.zip`，解壓縮後點兩下 **`start.bat`** 即可。首次執行會自動下載可攜版 Python 3.12 並依 GPU 安裝相依套件，不需預先安裝任何 Python。

### 從原始碼安裝

```bash
git clone https://github.com/moonstarsky37/LiveTranslate.git
cd LiveTranslate
```

點兩下 **`install.bat`** 一鍵安裝——腳本會自動：
1. 偵測 Python 3.10–3.12（未安裝則透過 winget 自動安裝）
2. 建立虛擬環境
3. 偵測 NVIDIA GPU，選擇 CUDA / CPU 版 PyTorch
4. 安裝全部相依套件

安裝完成後點兩下 **`start.bat`** 啟動。

更新時點兩下 **`update.bat`**——會自動拉取最新程式碼並更新相依套件（未安裝 Git 會透過 winget 自動安裝）。

<details>
<summary>手動安裝</summary>

```bash
python -m venv .venv
.venv\Scripts\activate

# PyTorch（三選一）
pip install torch torchaudio --index-url https://download.pytorch.org/whl/cu126  # CUDA
pip install torch torchaudio --index-url https://download.pytorch.org/whl/cu128  # CUDA（RTX 50 系列）
pip install torch torchaudio --index-url https://download.pytorch.org/whl/cpu    # 僅 CPU

# 相依套件
pip install -r requirements.txt

# 啟動
.venv\Scripts\python.exe main.py
```

</details>

## 首次使用

1. 跳出首次啟動精靈——設定模型快取路徑；模型一律從 HuggingFace 下載，不需選擇下載來源
2. 按「開始下載」——本 fork 已移除自動倒數，下載一律等你手動確認才開始
3. 自動下載 Silero VAD + SenseVoice 模型（約 1GB）
4. 下載完成後進入主介面

> 若你的網路連 HuggingFace 不穩，可在精靈的「下載 Proxy」欄位填入 Proxy 位址，再按「開始下載」。

## 設定翻譯 API

設定 → 翻譯標籤頁：

| 參數 | 範例 |
|------|------|
| API Base | `https://api.deepseek.com/v1` |
| API Key | 你的金鑰 |
| Model | `deepseek-chat` |
| Proxy | `none` / `system` / 自訂位址 |

## 架構

```
Audio (WASAPI 32ms) → VAD (Silero) → ASR → LLM Translation → Overlay
         ↑ 可選麥克風混音
```

```
main.py                 主入口，管線編排
├── audio_capture.py    WASAPI loopback + 麥克風混音
├── vad_processor.py    Silero VAD
├── asr_engine.py       faster-whisper 後端
├── asr_funasr.py       統一 FunASR 模型選擇後端
├── asr_sensevoice.py   SenseVoice 後端
├── asr_funasr_nano.py  FunASR Nano 後端
├── asr_anime_whisper.py Anime-Whisper 後端 (日語動畫/Galgame)
├── asr_remote.py        遠端 Whisper 用戶端 (→ asr_server.py, 見 REMOTE_ASR.md)
├── translator.py       OpenAI 相容翻譯用戶端 (串流/JSON/上下文)
├── model_manager.py    模型下載與快取管理
├── subtitle_overlay.py PyQt6 透明字幕浮窗
├── control_panel.py    設定面板 UI (7 個標籤頁)
├── dialogs.py          設定精靈、下載、模型設定對話框
└── benchmark.py        翻譯效能測試
```

## 致謝

- [TheDeathDragon/LiveTranslate](https://github.com/TheDeathDragon/LiveTranslate) — 本專案的原始出處，核心實作皆源自於此
- [faster-whisper](https://github.com/SYSTRAN/faster-whisper) — 基於 CTranslate2 的 Whisper 推論
- [FunASR](https://github.com/modelscope/FunASR) — SenseVoice / Fun-ASR-Nano
- [Anime-Whisper](https://huggingface.co/litagin/anime-whisper) — 日語動畫/Galgame 專用 ASR
- [Silero VAD](https://github.com/snakers4/silero-vad) — 語音活動偵測

## Star History

<a href="https://www.star-history.com/?repos=TheDeathDragon%2FLiveTranslate%2Cmoonstarsky37%2FLiveTranslate&type=date&legend=top-left">
 <picture>
   <source media="(prefers-color-scheme: dark)" srcset="https://api.star-history.com/image?repos=TheDeathDragon/LiveTranslate,moonstarsky37/LiveTranslate&type=date&theme=dark&legend=top-left" />
   <source media="(prefers-color-scheme: light)" srcset="https://api.star-history.com/image?repos=TheDeathDragon/LiveTranslate,moonstarsky37/LiveTranslate&type=date&legend=top-left" />
   <img alt="Star History Chart" src="https://api.star-history.com/image?repos=TheDeathDragon/LiveTranslate,moonstarsky37/LiveTranslate&type=date&legend=top-left" />
 </picture>
</a>

## 授權

[MIT License](LICENSE)
