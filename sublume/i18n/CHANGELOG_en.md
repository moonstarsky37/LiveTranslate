# Changelog

## 2026-08-10 (v2026.08.10-tw.1)
- **Project renamed: LiveTranslate → Sublume** (sub for subtitles + lume, Latin for light). Development has split from the original upstream project as of this release. What actually changes for you: the GitHub URL is now moonstarsky37/Sublume (the old URL redirects, update.bat keeps working) and log files are prefixed sublume_*; settings, downloaded models and transcripts are reused in place - no manual migration
- **torch is now optional**: the default Lightweight profile has no torch - VAD runs on a bundled ONNX model alongside the SenseVoice ONNX engine, shrinking the install from ~5GB to ~1GB with no GPU required. Install the Full profile only for the FunASR / Anime-Whisper engines (it can be added later, see next item)
- Picking a torch engine on a Lightweight install now offers a one-click fix: "Launch Installer" reruns the installer with the Full profile preselected (settings and downloaded models untouched); the app closes during the install and you restart it after. The portable build keeps the manual commands (now including the previously missing requirements-torch.txt line)
- Install guard: a completion marker (.sublume-ready) - an interrupted install is caught at the next launch and sent back to the installer instead of booting half an environment. Existing installs get caught once after this update; rerun install.bat (or update.bat) once to heal
- README overhauled: new positioning, an interface overview (three surfaces + three header layouts) with five new screenshots, and two-profile install docs; the architecture diagram is now a pre-rendered SVG (the mermaid block failed to render on GitHub)
- (backfill for v2026.08.09-tw.1/tw.2) Internal refactor: model_manager and the dialogs became packages; the README gained an architecture diagram

## 2026-08-08 (v2026.08.08-tw.5)
- New SenseVoice ONNX engine (Settings -> ASR -> Engine): the same model as an int8 ONNX export, running on CPU with no GPU needed. ASR startup drops from 56s to about 2s, the model from 936MB to 239MB, and transcription speed matches the current GPU path - which frees the GPU for translation. The existing FunASR engine is untouched; you can switch between them at any time
- The first-launch setup screen now asks which recognition engine to use: SenseVoice ONNX (239MB, no graphics card) is preselected, with the FunASR build (936MB, GPU inference) as the alternative. The choice decides what gets downloaded and can still be changed later in Settings

## 2026-08-08 (v2026.08.08-tw.4)
- The overlay's run/pause button now says what clicking it does: "Pause" while running, "Start" while paused. It used to show the current state, which read as the exact opposite under the usual "button label = action" convention
- Settings -> Style gained an overlay layout picker: Classic (unchanged), Compact (bigger buttons, the four window toggles move into a "..." menu), Minimal (only run/pause and settings on the bar, header 62px -> 32px)
- Fixed the control panel opening *behind* the overlay when you press Settings: the panel is now top-most as well and takes focus, and pressing Settings while it is open but unfocused brings it forward instead of hiding it (previously nothing appeared to happen)
- Settings -> Cache now lists every cached directory instead of only the ones it has a name for: unknown models, aborted half-downloads and leftover metadata stubs all show up, and "delete all" really empties the cache (those entries used to be invisible, so a "cleared" cache could still be holding gigabytes)

## 2026-08-08 (v2026.08.08-tw.3)
- Installation now runs on uv: no preinstalled Python required — the installer fetches uv's own Python 3.12 instead of hunting for (or misusing) a system one
- Fixed install.bat creating the virtual environment inside `scripts\`, where start.bat could not find it (affects source installs since v2026.08.07-tw.1)
- The overlay monitor bar now shows ASR state: loading / unavailable is visible at a glance, so startup and engine switches no longer look like a freeze
- The ASR worker runs one warm-up inference after loading a model: the first sentence after load no longer waits an extra 5-6s
- Pausing now hands the half-spoken sentence to ASR and then clears the VAD buffer, so resuming never replays speech captured before the pause and that half sentence is not silently lost (matching what closing the app already did)
- Settings -> ASR gained a "min speech density" slider (0 disables it): heavily-paused sources (clipped video, slow speakers) are no longer discarded wholesale as noise
- Settings -> Cache gained an optional HuggingFace token field: lifts the anonymous download rate limit, and is never written to the log
- Fixed the duplicated "unauthenticated requests" warning during downloads (huggingface_hub 1.27 overrides the logger level when it loads, so the suppression now goes through an environment variable)

## 2026-08-08 (v2026.08.08-tw.1)
- Factory-default cleanup: removed the upstream-inherited API key (config.yaml now suggests a local llama-server + translategemma, with the conventional "sk-local" key); the first-launch API prefill derives from those factory defaults
- The first-launch API hint is now vendor-neutral: any OpenAI-compatible API works, no specific service is recommended
- Hardening: an empty API key no longer crashes startup (the client substitutes an inert placeholder; cloud APIs still return a normal auth error)

## 2026-08-07 (v2026.08.07-tw.1, first release of this fork)
- This is the moonstarsky37/LiveTranslate fork: model downloads are HuggingFace-only (existing ModelScope caches remain readable)
- Setup wizard redesign: the 15s auto-start countdown is gone, downloads start only on an explicit click; settings are persisted at click time, so an interrupted download resumes via the missing-model dialog instead of re-running the wizard
- Traditional Chinese (zh-TW) is now a first-class language: new zh-TW UI and primary docs, zh-TW/zh-HK systems default to it; legacy "zh" settings migrate to zh-TW automatically
- VAD fix: a held sub-min-speech tail is now force-flushed after prolonged silence, so the last short sentence before a pause no longer disappears
- Fixed requirements.txt missing pysbd, which crashed fresh installs at the first incremental-ASR call
- Internal refactor (Phase 1-3): packaged layout (livetranslate/), single settings entry point (SettingsStore), and the main / control_panel / overlay / supervisor monoliths fully split, with behavior pinned by AST comparison and characterization tests
- CI quality gate (ruff + mypy + pytest) with branch protection: nothing merges to main without green CI
- (tw.2) Download experience: disable the HuggingFace Xet CDN to dodge intermittent 500/CAS aborts; failed downloads now show a short error plus a retry hint (full traceback goes to the file log only); silenced the symlink / anonymous-rate-limit warning walls
- (tw.2) Setup wizard and model download dialogs got their close button (X) back; closing mid-download is safe - the next launch resumes via the missing-model flow

## 2026-07-11
- Fixed Fun-ASR-Nano first load: the Qwen3-0.6B weight download could be killed by the 180s worker startup timeout (#32); weights are now fetched up-front in the model download phase, so worker startup no longer waits on large downloads
- Fixed model detection misses caused by the ModelScope 1.38+ cache location change (#32, #33): all cache layouts across SDK versions are recognized, so upgrading the SDK no longer re-downloads existing models
- Qwen3-0.6B weights can now be downloaded from ModelScope; ModelScope-hub users are no longer forced through HuggingFace (#33)

## 2026-06-20
- New "Remote Whisper" ASR engine: offload speech recognition to a separate GPU machine (ships `asr_server.py` server), so a box without a GPU can still transcribe in real time
- New "WebID / ID Verify" translation prompt preset, tuned for video identity-verification calls
- ASR now runs in an isolated subprocess: the worker auto-restarts on crash/timeout and recycles when memory grows past a threshold, so a recognition failure no longer drags down the UI
- Subtitle window mouse click-through (#28): a toggle in the subtitle settings plus a "Subtitle Click-through" tray shortcut; when on, clicks pass to the window behind (middle-click drag is disabled while on — turn it off to reposition)

## 2026-05-10
- New "Export to file" menu: original / translation / combined formats, accessible from overlay right-click menu and tray menu
- New "Transcript persistence" (enabled by default): each session creates 3 files under `transcripts/` (original / translation / combined), appended in real time per segment — no longer bounded by the 50-message overlay cap
- Settings panel "Cache" tab: added "Transcript persistence" group with toggle and open-folder button
- Memory ceiling protection: tray notification shown once when RSS exceeds 4096MB, advising restart (ASR backends keep native-side workspaces/caches that Python GC and `torch.cuda.empty_cache` may not reclaim)
- New `MEM[asr#/tick]` log lines: per-ASR-call RSS / GPU (alloc/reserved) / audio duration / overlay message count / VAD buffer length for memory diagnostics

## 2026-04-20
- Removed Qwen3-ASR engine (ONNX + GGUF hybrid had compatibility issues; model files and llama.cpp runtime dependencies cleaned up)
- Model config: new "Advanced Parameters" group — `temperature`, `top_p`, `max_tokens`, `frequency_penalty`, `presence_penalty`, `seed`, each gated by an independent "Override" checkbox (unchecked = use server default)
- Model config: new `extra_body` (JSON) field for provider-specific parameters (e.g. `thinking_budget`, `reasoning_effort`), validated on save
- Fix: Anime-Whisper download dialog was a silent no-op when the model was not cached
- Fix: settings panel "Changelog" tab showed blank (regex expected H3 but files used H2 headings — broken since the tab was added in March)

## 2026-04-18
- New ASR engine: Anime-Whisper (litagin/anime-whisper), Japanese-only, specialized for anime / galgame speech (breaths, sighs, non-verbal sounds)
- Fix HF cache detection: aborted downloads leaving empty dirs no longer trigger false "cached" state

## 2026-03-31
- Pipeline thread split: capture+VAD+ASR was a single thread; now capture and ASR run on separate threads, so long-segment ASR no longer blocks live RMS/VAD bar updates
- ASR scheduling uses a bounded queue (16 segments); oldest interim segments are dropped when full to prevent backlog-induced latency

## 2026-03-26
- Default translation prompt improvements: added ASR error-correction rules (fix typos/homophones from context) and fluency rules (avoid word-for-word literal translation)

## 2026-03-25
- Style tab: new "Reset window positions" button — subtitle window returns to (100,100), overlay returns to bottom-right of screen
- Subtitle window default position changed from bottom-center to (100,100); minimum height adjusted to 200px
- Window position restore now validates against the visible screen area (`availableGeometry` excludes the taskbar); height changes clamp to screen bounds to prevent windows from being pushed off-screen

## 2026-03-24
- Subtitle window: auto word-wrap for long text (no more split segments), smooth height animation, pixmap render cache
- Overlay & subtitle window: position/size persistence across restarts
- Overlay: compact mode toggle animation
- Settings: removed valid-key whitelist restriction

## 2026-03-23
- Rebranded LiveTrans → LiveTranslate
- Model config: streaming toggle, structured output, context count, disable thinking (default on)
- Streaming translation display in overlay
- Prompt improvements: no alternatives, instant apply
- Repetition loop detection and user warning
- ASR engine labels: Accurate / Fast
- Changelog tab in settings panel
