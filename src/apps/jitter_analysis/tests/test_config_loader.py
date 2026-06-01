from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from jitter_analysis.config.loader import load_config


def test_load_example_config():
    config = load_config(ROOT / "configs" / "irfel_pvlist_v2.example.json")
    assert config.schema_version == "2.0"
    assert len(config.knobs) >= 1
    assert len(config.objects) >= 1
