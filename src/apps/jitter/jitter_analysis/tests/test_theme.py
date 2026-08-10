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
from jitter_analysis.gui.plots.theme import plot_theme_spec


def test_default_theme_exists():
    spec = theme_spec(DEFAULT_THEME_ID)
    assert spec.theme_id == DEFAULT_THEME_ID
    assert theme_label(DEFAULT_THEME_ID)


def test_multiple_themes_are_available():
    themes = available_themes()
    assert len(themes) == 2
    assert [theme.theme_id for theme in themes] == ["control_room", "night_shift"]
    assert len({theme.theme_id for theme in themes}) == len(themes)


def test_stylesheet_contains_theme_specific_menu_and_tab_rules():
    stylesheet = build_app_stylesheet("control_room")
    assert "QMenuBar" in stylesheet
    assert "workspaceTabBar" in stylesheet
    assert "analysisTabBar" in stylesheet
    assert "#e0ecff" in stylesheet


def test_dark_theme_stylesheet_contains_dark_palette_tokens():
    stylesheet = build_app_stylesheet("night_shift")
    assert "#0f1519" in stylesheet
    assert "#172027" in stylesheet
    assert "#45d0bc" in stylesheet
    assert '"IBM Plex Sans", "Source Han Sans SC", "Segoe UI", sans-serif' in stylesheet


def test_stylesheet_sets_file_dialog_tool_button_icon_size():
    stylesheet = build_app_stylesheet("night_shift")
    assert "QFileDialog QToolButton" in stylesheet
    assert "qproperty-iconSize: 18px 18px" in stylesheet


def test_plot_theme_background_tracks_gui_theme():
    assert plot_theme_spec("night_shift").background == "#11181e"
    assert plot_theme_spec("control_room").background == "#fffdf8"
