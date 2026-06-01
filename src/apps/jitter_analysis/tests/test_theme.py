from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from jitter_analysis.gui.theme import (
    DEFAULT_THEME_ID,
    available_themes,
    build_app_stylesheet,
    theme_label,
    theme_spec,
)


def test_default_theme_exists():
    spec = theme_spec(DEFAULT_THEME_ID)
    assert spec.theme_id == DEFAULT_THEME_ID
    assert theme_label(DEFAULT_THEME_ID)


def test_multiple_themes_are_available():
    themes = available_themes()
    assert len(themes) >= 5
    assert len({theme.theme_id for theme in themes}) == len(themes)


def test_stylesheet_contains_theme_specific_menu_and_tab_rules():
    stylesheet = build_app_stylesheet("mist_blue")
    assert "QMenuBar" in stylesheet
    assert "workspaceTabBar" in stylesheet
    assert "analysisTabBar" in stylesheet
    assert "#d9ebff" in stylesheet


def test_dark_theme_stylesheet_contains_dark_palette_tokens():
    stylesheet = build_app_stylesheet("night_shift")
    assert "#0f141b" in stylesheet
    assert "#1b2430" in stylesheet
    assert "#f3f8ff" in stylesheet
