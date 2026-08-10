# 架構細節(維護文件)

> 這份是給貢獻者/維護者的:README 的「架構」節只放導覽段落與圖,
> 所有維護程序與實作說明集中在這裡。圖的規格 JSON 與本檔同目錄。

## 圖的三份來源與同步規則

| 檔案 | 角色 |
|---|---|
| `docs/architecture.zh.json` / `docs/architecture.en.json` | archviz 規格(產 SVG 的唯一來源) |
| `screenshot/architecture-zh.svg` / `-en.svg` | README 嵌入的預渲染圖 |
| 本檔下方的 mermaid 區塊 | 文字版(review 與 diff 友善) |

**改架構時三者一起改**。SVG 重產(archviz 為維護者本機工具,JSON 驅動):

```
uv run archviz docs/architecture.zh.json --name architecture-zh -o screenshot
uv run archviz docs/architecture.en.json --name architecture-en -o screenshot
```

(README 原本直接嵌 mermaid,GitHub render 失敗「Unable to render rich display」,
2026-08-10 起改嵌 SVG。)

## 實作要點

- 跨執行緒的 UI 更新一律走 Qt signal。
- 設定檔讀寫只經過 `SettingsStore`(原子寫入+載入時遷移):寫入先落 `.tmp` 再
  `os.replace`,程式中途當掉設定檔也不會損毀;舊版設定鍵在載入時自動升級並寫回。
- ASR worker 是 multiprocessing **spawn** 子行程:任何入口/探針腳本必須有
  `if __name__ == "__main__"` 守衛,否則 worker re-import 時會執行模組層代碼。
- 切換辨識引擎 = 汰換整個 worker 子行程,模型與 VRAM 隨行程釋放,GUI 不阻塞;
  目標載入失敗時以保存的前一組 worker 設定還原。

## mermaid 文字版(zh)

```mermaid
flowchart TB
    audio(["系統音訊（可混入麥克風）"])

    subgraph main["GUI 主行程（Qt 事件圈）"]
        direction TB
        subgraph capthread["擷取執行緒"]
            cap["音訊擷取 core/audio_capture.py<br/>WASAPI loopback·32ms"]
            vad["語句切分 core/vad_processor.py<br/>Silero VAD／能量式"]
        end
        asrq["ASR 佇列執行緒 core/pipeline.py<br/>增量辨識與斷句"]
        cli["ASRClient asr/client.py<br/>worker 生命週期與逾時"]
        tr["翻譯 translation/translator.py<br/>非同步·串流·JSON·上下文"]
        ui1["字幕浮窗 ui/overlay/"]
        ui2["OBS 字幕視窗<br/>ui/overlay/subtitle_window.py"]
        tw["逐字稿 core/transcript_writer.py"]
        cp["設定面板 ui/control_panel/<br/>對話框 ui/dialogs/"]
    end

    subgraph wk["ASR worker 子行程 asr/worker.py"]
        eng["單一辨識引擎，擇一載入<br/>SenseVoice ONNX（預設，CPU 秒開）<br/>faster-whisper／FunASR／Anime-Whisper／遠端 ASR"]
    end

    llm[("OpenAI 相容 API<br/>雲端或本機 llama.cpp／Ollama／vLLM")]
    hub[("HuggingFace Hub<br/>（僅下載模型時）")]

    st["設定 config/store.py<br/>user_settings.json ＋ config.yaml"]
    mm["模型管理 model_manager/<br/>registry·cache·download"]

    audio --> cap --> vad -->|"完整語句段"| asrq --> cli
    cli <-->|"multiprocessing.Pipe"| eng
    cli -->|"辨識文字"| tr
    tr <-->|"HTTPS"| llm
    tr --> ui1 & ui2 & tw
    cp -.->|"讀寫設定"| st
    cp -.->|"缺模型時觸發下載"| mm
    mm <-->|"下載"| hub
```

## mermaid 文字版(en)

```mermaid
flowchart TB
    audio(["System audio (mic mix-in optional)"])

    subgraph main["GUI process (Qt event loop)"]
        direction TB
        subgraph capthread["Capture thread"]
            cap["Audio capture core/audio_capture.py<br/>WASAPI loopback · 32ms chunks"]
            vad["Sentence segmentation core/vad_processor.py<br/>Silero VAD / energy-based"]
        end
        asrq["ASR queue thread core/pipeline.py<br/>incremental ASR + sentence splitting"]
        cli["ASRClient asr/client.py<br/>worker lifecycle + timeouts"]
        tr["Translation translation/translator.py<br/>async · streaming · JSON · context"]
        ui1["Subtitle overlay ui/overlay/"]
        ui2["OBS subtitle window<br/>ui/overlay/subtitle_window.py"]
        tw["Transcripts core/transcript_writer.py"]
        cp["Settings panel ui/control_panel/<br/>dialogs ui/dialogs/"]
    end

    subgraph wk["ASR worker subprocess asr/worker.py"]
        eng["One recognition engine, loaded exclusively<br/>SenseVoice ONNX (default, starts in seconds on CPU)<br/>faster-whisper / FunASR / Anime-Whisper / remote ASR"]
    end

    llm[("OpenAI-compatible API<br/>cloud or local llama.cpp / Ollama / vLLM")]
    hub[("HuggingFace Hub<br/>(model downloads only)")]

    st["Settings config/store.py<br/>user_settings.json + config.yaml"]
    mm["Model management model_manager/<br/>registry · cache · download"]

    audio --> cap --> vad -->|"complete utterance"| asrq --> cli
    cli <-->|"multiprocessing.Pipe"| eng
    cli -->|"recognized text"| tr
    tr <-->|"HTTPS"| llm
    tr --> ui1 & ui2 & tw
    cp -.->|"read/write settings"| st
    cp -.->|"triggers downloads when models are missing"| mm
    mm <-->|"download"| hub
```
