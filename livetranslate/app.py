"""Application startup: logging, QApplication assembly, wizard dispatch,
window creation and tray hookup. Extracted from livetranslate/main.py (PR 3.4).
"""

import sys
import signal
import logging
import threading
import os
from datetime import datetime

# livetranslate.main applies the model-cache env vars and enforces the
# torch-before-PyQt6 import order at module level; import it first.
from livetranslate.main import LiveTranslateApp

from livetranslate.model_manager import (
    DEFAULT_FUNASR_MODEL,
    apply_hf_token,
    get_missing_models,
)

from PyQt6.QtWidgets import QApplication, QDialog, QMessageBox
from PyQt6.QtGui import QIcon, QPixmap, QPainter, QColor, QFont, QFontDatabase
from PyQt6.QtCore import QTimer, Qt

from livetranslate.ui.overlay.subtitle_overlay import SubtitleOverlay
from livetranslate.ui.overlay.subtitle_window import SubtitleWindow
from livetranslate.ui.log_window import LogWindow
from livetranslate.ui.control_panel import (
    ControlPanel,
    SETTINGS_FILE,
    _load_saved_settings,
    _save_settings,
)
from livetranslate.ui.dialogs import (
    SetupWizardDialog,
    ModelDownloadDialog,
)
from livetranslate.ui.tray import build_tray
from livetranslate.i18n import t, set_lang


def setup_logging():
    from livetranslate.paths import ROOT

    log_dir = ROOT / "logs"
    log_dir.mkdir(exist_ok=True)
    log_file = log_dir / f"livetrans_{datetime.now():%Y%m%d_%H%M%S}.log"

    file_handler = logging.FileHandler(log_file, encoding="utf-8")
    file_handler.setLevel(logging.DEBUG)
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.INFO)

    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    file_handler.setFormatter(fmt)
    console_handler.setFormatter(fmt)

    logging.basicConfig(level=logging.DEBUG, handlers=[file_handler, console_handler])

    for noisy in (
        "httpcore",
        "httpx",
        "openai",
        "filelock",
        "huggingface_hub",
        "funasr",
        "modelscope",
        "onnxruntime",
    ):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    # ERROR, not WARNING: huggingface_hub warns "unauthenticated requests" on
    # every anonymous download, which lands in the user-visible dialog logs.
    logging.getLogger("huggingface_hub").setLevel(logging.ERROR)

    logging.info(f"Log file: {log_file}")

    # FunASR/ModelScope spam the root logger; suppress after our own init log
    logging.getLogger().setLevel(logging.WARNING)
    logging.getLogger("LiveTranslate").setLevel(logging.DEBUG)

    _logger = logging.getLogger("LiveTranslate")

    def _excepthook(exc_type, exc_value, exc_tb):
        _logger.critical("Uncaught exception", exc_info=(exc_type, exc_value, exc_tb))
        sys.__excepthook__(exc_type, exc_value, exc_tb)

    sys.excepthook = _excepthook

    def _thread_excepthook(args):
        _logger.critical(
            f"Uncaught exception in thread {args.thread}",
            exc_info=(args.exc_type, args.exc_value, args.exc_traceback),
        )

    threading.excepthook = _thread_excepthook

    return _logger


log = logging.getLogger("LiveTranslate")


def create_app_icon() -> QIcon:
    pix = QPixmap(64, 64)
    pix.fill(QColor(0, 0, 0, 0))
    p = QPainter(pix)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setBrush(QColor(60, 130, 240))
    p.setPen(Qt.PenStyle.NoPen)
    p.drawRoundedRect(4, 4, 56, 56, 12, 12)
    p.setPen(QColor(255, 255, 255))
    p.setFont(QFont("Consolas", 28, QFont.Weight.Bold))
    p.drawText(pix.rect(), Qt.AlignmentFlag.AlignCenter, "LT")
    p.end()
    return QIcon(pix)


def load_config():
    # config.yaml is read-only factory defaults, served by the settings store
    from livetranslate.config.store import SettingsStore

    return SettingsStore().factory_defaults()


def main():
    setup_logging()
    log.info("LiveTranslate starting...")
    config = load_config()
    config.setdefault("asr", {})
    config["asr"].setdefault("asr_engine", "funasr")
    config["asr"].setdefault("funasr_model", DEFAULT_FUNASR_MODEL)
    # Migrations (funasr normalization + fork hub/zh rules) now live inside
    # SettingsStore.load(), which _load_saved_settings() delegates to.
    saved = _load_saved_settings()
    # Before any download path runs (wizard, missing-model dialog, ASR worker).
    apply_hf_token((saved or {}).get("hf_token"))

    # Log actual effective config
    _asr_eng = (saved or {}).get("asr_engine", config["asr"].get("asr_engine", "funasr"))
    _funasr_model = (saved or {}).get(
        "funasr_model", config["asr"].get("funasr_model", DEFAULT_FUNASR_MODEL)
    )
    _active_idx = (saved or {}).get("active_model", 0)
    _models = (saved or {}).get("models", [])
    if 0 <= _active_idx < len(_models):
        _m = _models[_active_idx]
        _model_info = f"{_m.get('name', '?')} ({_m.get('model', '?')})"
    else:
        _model_info = f"{config['translation']['model']} (default)"
    if _asr_eng == "funasr":
        log.info(
            f"Config loaded: ASR={_asr_eng}/{_funasr_model}, "
            f"Translator={_model_info}"
        )
    else:
        log.info(f"Config loaded: ASR={_asr_eng}, Translator={_model_info}")

    # Apply UI language before creating any widgets
    if saved and saved.get("ui_lang"):
        set_lang(saved["ui_lang"])

    os.environ["QT_LOGGING_RULES"] = (
        "qt.text.font.db=false;qt.qpa.fonts.warning=false"
    )
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    # Pin a guaranteed TrueType UI font to avoid DirectWrite failures on the
    # legacy "MS Sans Serif" bitmap font Windows may resolve as the default
    if "Segoe UI" in QFontDatabase.families():
        app.setFont(QFont("Segoe UI", 9))
    _app_icon = create_app_icon()
    app.setWindowIcon(_app_icon)

    # First launch → setup wizard (hub + download) → configure translation API
    if not SETTINGS_FILE.exists():
        wizard = SetupWizardDialog()
        if wizard.exec() != QDialog.DialogCode.Accepted:
            sys.exit(0)
        saved = _load_saved_settings()
        log.info("Setup wizard completed")

        # Prompt user to configure translation API
        from livetranslate.ui.dialogs import ModelEditDialog

        info = QMessageBox(
            QMessageBox.Icon.Information,
            t("window_setup"),
            t("setup_api_hint"),
        )
        info.exec()

        # Prefill from the factory defaults; the user replaces them with
        # their own endpoint, key and model (any OpenAI-compatible API).
        _tc = config["translation"]
        dlg = ModelEditDialog(None, {
            "name": _tc["model"],
            "api_base": _tc["api_base"],
            "api_key": _tc["api_key"],
            "model": _tc["model"],
        })
        dlg.setWindowTitle(t("setup_api_title"))
        if dlg.exec() == QDialog.DialogCode.Accepted:
            data = dlg.get_data()
            if data.get("api_key"):
                saved["models"] = [data]
                saved["active_model"] = 0
                _save_settings(saved)
                log.info(f"Translation API configured: {data['name']}")
        # If user skips, ControlPanel will create default placeholder from config.yaml

    # Non-first launch but models missing → download dialog
    else:
        saved = saved or {}
        current_engine = saved.get("asr_engine", config["asr"].get("asr_engine", "funasr"))
        missing = get_missing_models(
            current_engine,
            (
                saved.get("funasr_model", config["asr"].get("funasr_model"))
                if current_engine == "funasr"
                else saved.get("whisper_model_size", config["asr"]["model_size"])
            ),
            "hf",
        )
        if missing:
            log.info(f"Missing models: {[m['name'] for m in missing]}")
            dlg = ModelDownloadDialog(
                missing,
                hub="hf",
                proxy=saved.get("download_proxy", "system"),
            )
            if dlg.exec() != QDialog.DialogCode.Accepted:
                sys.exit(0)

    log_window = LogWindow()
    log_handler = log_window.get_handler()
    logging.getLogger().addHandler(log_handler)

    panel = ControlPanel(config, saved_settings=saved)

    overlay = SubtitleOverlay(config["subtitle"])
    if saved:
        ox = saved.get("overlay_x")
        oy = saved.get("overlay_y")
        ow = saved.get("overlay_w")
        oh = saved.get("overlay_h")
        if ox is not None and oy is not None:
            if SubtitleWindow._is_pos_visible(ox, oy):
                overlay.move(ox, oy)
            else:
                screen = QApplication.primaryScreen()
                geo = screen.availableGeometry()
                overlay.move(geo.right() - overlay.width() - 20, geo.bottom() - overlay.height() - 60)
        if ow and oh:
            overlay.resize(ow, oh)
    overlay.show()

    # Subtitle window
    subwin_cfg = (saved or {}).get("subtitle_mode")
    subwin = SubtitleWindow(subwin_cfg)
    subwin_was_enabled = (subwin_cfg or {}).get("enabled", False)

    live_trans = LiveTranslateApp(config)
    live_trans.set_overlay(overlay)
    live_trans.set_subtitle_window(subwin)
    live_trans.set_panel(panel)

    def _deferred_init():
        panel._apply_settings()
        models = panel.get_settings().get("models", [])
        active_idx = panel.get_settings().get("active_model", 0)
        overlay.set_models(models, active_idx)
        target = panel.get_settings().get("target_language", "zh")
        overlay.set_target_language(target)
        asr_lang = panel.get_settings().get("asr_language", "auto")
        overlay.set_source_language(asr_lang)
        style = panel.get_settings().get("style")
        if style:
            overlay.apply_style(style)
        active_model = panel.get_active_model()
        if active_model:
            live_trans._on_model_changed(active_model)

    QTimer.singleShot(100, _deferred_init)

    # tray_ns holds every tray menu/action/closure — it must stay referenced
    # for the whole app lifetime (Qt ownership, see ui/tray.py docstring).
    tray_ns = build_tray(
        app, live_trans, overlay, subwin, panel, log_window, _app_icon,
        subwin_was_enabled,
    )

    QTimer.singleShot(500, tray_ns.on_start)

    signal.signal(signal.SIGINT, lambda *_: tray_ns.on_quit())
    timer = QTimer()
    timer.timeout.connect(lambda: None)
    timer.start(200)

    sys.exit(app.exec())


if __name__ == "__main__":
    import multiprocessing as _multiprocessing

    _multiprocessing.freeze_support()
    main()
