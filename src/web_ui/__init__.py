"""Static local research interface."""

from pathlib import Path


WEB_UI_DIRECTORY = Path(__file__).resolve().parent


def load_index_html() -> str:
    return (WEB_UI_DIRECTORY / "index.html").read_text(
        encoding="utf-8"
    )


__all__ = ["WEB_UI_DIRECTORY", "load_index_html"]
