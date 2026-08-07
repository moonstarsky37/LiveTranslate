"""Benchmark tab: translation speed test across all configured models."""

from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from livetranslate.benchmark import run_benchmark
from livetranslate.i18n import t


class BenchmarkTabMixin:
    """Benchmark tab methods, mixed into ControlPanel."""

    def _create_benchmark_tab(self):
        widget = QWidget()
        layout = QVBoxLayout(widget)

        ctrl_row = QHBoxLayout()
        ctrl_row.addWidget(QLabel(t("label_source")))
        self._bench_lang = QComboBox()
        self._bench_lang.addItems(["ja", "en", "zh", "ko", "fr", "de"])
        self._bench_lang.setCurrentIndex(0)
        ctrl_row.addWidget(self._bench_lang)
        ctrl_row.addWidget(QLabel(t("target_label")))
        self._bench_target = QComboBox()
        self._bench_target.addItems(["zh", "en", "ja", "ko", "fr", "de", "es", "ru"])
        ctrl_row.addWidget(self._bench_target)
        ctrl_row.addStretch()
        self._bench_btn = QPushButton(t("btn_test_all"))
        self._bench_btn.clicked.connect(self._run_benchmark)
        ctrl_row.addWidget(self._bench_btn)
        layout.addLayout(ctrl_row)

        self._bench_output = QTextEdit()
        self._bench_output.setReadOnly(True)
        self._bench_output.setFont(QFont("Consolas", 9))
        self._bench_output.setStyleSheet(
            "background: #1e1e2e; color: #cdd6f4; border: 1px solid #444;"
        )
        layout.addWidget(self._bench_output)

        return widget

    def _run_benchmark(self):
        models = self._current_settings.get("models", [])
        if not models:
            return

        source_lang = self._bench_lang.currentText()
        target_lang = self._bench_target.currentText()
        timeout_s = self._current_settings.get("timeout", 5)

        self._bench_btn.setEnabled(False)
        self._bench_btn.setText(t("testing"))
        self._bench_output.clear()

        from livetranslate.translation.translator import DEFAULT_PROMPT, LANGUAGE_DISPLAY

        src = LANGUAGE_DISPLAY.get(source_lang, source_lang)
        tgt = LANGUAGE_DISPLAY.get(target_lang, target_lang)
        prompt = self._current_settings.get("system_prompt", DEFAULT_PROMPT)
        try:
            prompt = prompt.format(source_lang=src, target_lang=tgt)
        except (KeyError, IndexError):
            pass

        run_benchmark(
            models, source_lang, target_lang, timeout_s, prompt, self._bench_result.emit
        )

    def _on_bench_result(self, text: str):
        if text == "__DONE__":
            self._bench_btn.setEnabled(True)
            self._bench_btn.setText(t("btn_test_all"))
        else:
            self._bench_output.append(text)
