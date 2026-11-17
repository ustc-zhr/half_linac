import os
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]


def test_existing_proxy_on_pythonpath_stays_ahead_of_sibling_checkouts():
    env = {**os.environ, "PYTHONPATH": str(ROOT / "scripts/python_imports"), "PYTHONDONTWRITEBYTECODE": "1"}
    result = subprocess.run([sys.executable, "-B", "scripts/run_digital_shadow.py", "--help"],
                            cwd=ROOT, env=env, capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    assert "run-vm" in result.stdout


def test_already_imported_different_checkout_is_still_rejected():
    code = """
import sys, types
from pathlib import Path
root = Path(sys.argv[1])
sys.path.insert(0, str(root))
from repo_bootstrap import ensure_repo_import_path
package = types.ModuleType('half_linac')
package.__path__ = [str(root / 'different_checkout')]
sys.modules['half_linac'] = package
try:
    ensure_repo_import_path(str(root / 'scripts/run_digital_shadow.py'))
except RuntimeError as error:
    assert 'different HALF checkout' in str(error)
else:
    raise AssertionError('A pre-imported different checkout must be rejected')
"""
    result = subprocess.run([sys.executable, "-B", "-c", code, str(ROOT)],
                            capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
