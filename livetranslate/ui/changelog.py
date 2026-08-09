"""Changelog rendering for the settings tab: pick the latest entry from the
locale's CHANGELOG_*.md and convert its markdown subset to HTML.

Moved out of ui/dialogs.py in M3-2 — these are not dialogs; the underscore
prefixes were dropped because they are this module's public API."""

import re

from livetranslate.i18n import I18N_DIR


def changelog_to_html(text: str) -> str:
    """Convert CHANGELOG.md subset to HTML (headings, bold, lists)."""
    lines = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("### "):
            lines.append(f"<h3>{stripped[4:]}</h3>")
        elif stripped.startswith("## "):
            lines.append(f"<h2>{stripped[3:]}</h2>")
        elif stripped.startswith("# "):
            continue  # skip file title
        elif stripped.startswith("- "):
            item = stripped[2:]
            item = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", item)
            item = re.sub(r"`(.+?)`", r"<code>\1</code>", item)
            lines.append(f"<li>{item}</li>")
        elif stripped:
            lines.append(f"<p>{stripped}</p>")
    return "\n".join(lines)


def load_latest_changelog() -> tuple[str, str]:
    """Return (first_h2_title, html) for the latest changelog. Uses i18n lang."""
    from livetranslate.i18n import get_lang
    lang = get_lang()
    path = I18N_DIR / f"CHANGELOG_{lang}.md"
    if not path.exists():
        path = I18N_DIR / "CHANGELOG_en.md"
    if not path.exists():
        return "", ""
    text = path.read_text("utf-8")
    # First H2 (## date) is the latest entry and serves as the tracking key
    m = re.search(r"^## (.+)$", text, re.MULTILINE)
    if not m:
        return "", ""
    title = m.group(1).strip()
    # Drop the top-level file heading (# Title) — keep everything from first H2 onwards
    body = text[m.start():]
    return title, changelog_to_html(body)
