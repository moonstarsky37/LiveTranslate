"""Changelog tab: renders i18n/CHANGELOG_{lang}.md as HTML."""

from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import QVBoxLayout, QWidget


class ChangelogTabMixin:
    """Changelog tab methods, mixed into ControlPanel."""

    def _create_changelog_tab(self):
        from sublume.ui.changelog import load_latest_changelog
        widget = QWidget()
        layout = QVBoxLayout(widget)
        _, html = load_latest_changelog()
        from PyQt6.QtWidgets import QTextBrowser
        browser = QTextBrowser()
        browser.setOpenExternalLinks(True)
        browser.setHtml(html)
        browser.setFont(QFont("Microsoft YaHei UI", 10))
        layout.addWidget(browser)
        return widget
