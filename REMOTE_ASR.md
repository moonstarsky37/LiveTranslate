# Remote Whisper ASR

Run speech recognition on a separate GPU machine and let LiveTranslate talk to it over HTTP. Useful when the PC running LiveTranslate has no NVIDIA GPU (CPU-only faster-whisper is too slow for real-time) but another machine on the LAN does.

```
LiveTranslate (this PC) ──HTTP──> server.py (GPU machine) ──> faster-whisper / CUDA
      RemoteASREngine              /transcribe, /health
```

## 1. On the GPU machine — run the server

Requires Python 3.10+ and an NVIDIA GPU.

The server lives at `livetranslate/asr/server.py`. It is self-contained (only faster-whisper, FastAPI, uvicorn, and numpy), so copying that single file to the GPU machine is enough.

```bash
pip install faster-whisper fastapi uvicorn numpy

python server.py --host 0.0.0.0 --port 8765 \
    --model large-v3 --device cuda --compute-type float16
```

`--model` accepts any faster-whisper size: `tiny`, `base`, `small`, `medium`, `large-v3`. On an 8 GB card, `large-v3` (float16) uses about 4 GB and is the most accurate; `medium` is a lighter option. The model downloads from Hugging Face on first run.

When the log shows `Uvicorn running on http://0.0.0.0:8765`, it's ready:

```bash
curl http://localhost:8765/health      # {"status":"ok","model":"large-v3"}
```

### CUDA libraries

CTranslate2 (faster-whisper's backend) needs the CUDA 12 cuBLAS and cuDNN 9 libraries on the library path. If you hit `Library libcublas.so.12 is not found`:

```bash
pip install nvidia-cublas-cu12 nvidia-cudnn-cu12
export LD_LIBRARY_PATH=`python3 -c 'import os, nvidia.cublas.lib, nvidia.cudnn.lib; print(os.path.dirname(nvidia.cublas.lib.__file__) + ":" + os.path.dirname(nvidia.cudnn.lib.__file__))'`
```

Alternatively, install a system CUDA toolkit.

### Slow model download?

Set a Hugging Face mirror before launching:

```bash
export HF_ENDPOINT=https://hf-mirror.com
```

### Run as a service (optional, Linux/systemd)

To start the server on boot and restart it on failure:

```ini
# /etc/systemd/system/asr.service
[Unit]
Description=LiveTranslate Remote ASR Server
After=network-online.target

[Service]
User=youruser
WorkingDirectory=/home/youruser
Environment=HF_ENDPOINT=https://hf-mirror.com
ExecStart=/usr/bin/python3 -u /home/youruser/server.py --host 0.0.0.0 --port 8765 --model large-v3 --device cuda --compute-type float16
Restart=on-failure

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl enable --now asr.service
journalctl -u asr.service -f          # follow logs
```

## 2. In LiveTranslate — point at the server

1. Open Settings → VAD / ASR.
2. Set the ASR engine to Remote Whisper (remote GPU server).
3. Enter the address in Remote ASR Server URL, e.g. `http://192.168.1.10:8765`.

Recognition now runs on the GPU machine, so no local ASR model download is needed. Translation still uses whatever model is configured in the Translation tab.

## HTTP API

| Method | Path | Body | Response |
|--------|------|------|----------|
| `POST` | `/transcribe` | `[uint32 lang_len][lang bytes][float32 PCM 16 kHz mono]` | `{"text", "language", "elapsed"}` |
| `GET`  | `/health` | — | `{"status": "ok", "model": "..."}` |

`lang_len` + `lang` is an optional language hint (e.g. `de`); send length `0` (or `auto`) to let Whisper auto-detect.
