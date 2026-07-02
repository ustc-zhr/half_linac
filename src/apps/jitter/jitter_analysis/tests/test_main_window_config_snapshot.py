from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from jitter_analysis.config.models import PvListConfig
from jitter_analysis.gui.config_snapshot import config_snapshot_text
from jitter_analysis.gui.main_window import MainWindow


def test_read_loaded_config_snapshot_text_prefers_in_memory_source_text():
    config = PvListConfig(
        schema_version="2.0",
        machine=None,
        defaults=None,
        groups=[],
        knobs=[],
        objects=[],
        presets=[],
        source_path="/path/that/does/not/exist.json",
        source_text='{"schema_version": "2.0"}',
    )
    window = MainWindow.__new__(MainWindow)
    window.loaded_config = config

    assert window._read_loaded_config_snapshot_text() == '{"schema_version": "2.0"}'


def test_config_snapshot_text_falls_back_to_source_path(tmp_path: Path):
    config_path = tmp_path / "pvlist.json"
    config_path.write_text('{"schema_version": "2.0"}', encoding="utf-8")
    config = PvListConfig(
        schema_version="2.0",
        machine=None,
        defaults=None,
        groups=[],
        knobs=[],
        objects=[],
        presets=[],
        source_path=str(config_path),
        source_text="",
    )

    assert config_snapshot_text(config) == '{"schema_version": "2.0"}'
