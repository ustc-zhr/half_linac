"""Opt-in real-shaped capture through an injected reader and actual Elegant."""
import json
import os
from pathlib import Path
import subprocess
import sys
import time

import pytest


@pytest.mark.skipif(os.environ.get("HALF_SHADOW_REAL_DEMO_INTEGRATION") != "1",
                    reason="Set HALF_SHADOW_REAL_DEMO_INTEGRATION=1 for offline Elegant GUI verification")
@pytest.mark.parametrize("save_images", [False, True])
def test_real_demo_gui_capture_and_archived_replay_without_any_pv_access(tmp_path, monkeypatch, save_images):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    # Fresh workers inherit a deny-all EPICS shim as well as parent-side guards.
    blocker = tmp_path / "blocker"
    blocker.mkdir()
    (blocker / "sitecustomize.py").write_text('''import epics
def forbidden(*args, **kwargs):
    raise RuntimeError("PV access forbidden during offline real demo")
for name in ("PV", "get_pv", "caget", "caput", "caget_many", "caput_many"):
    if hasattr(epics, name): setattr(epics, name, forbidden)
epics.ca.create_channel = forbidden
epics.ca.poll = forbidden
''')
    repo = Path(__file__).resolve().parents[1]
    monkeypatch.setenv("PYTHONPATH", str(blocker) + os.pathsep + str(repo / "scripts/python_imports"))
    import epics
    def forbidden(*args, **kwargs):
        raise AssertionError("PV access forbidden in GUI")
    for name in ("PV", "get_pv", "caget", "caput", "caget_many", "caput_many"):
        monkeypatch.setattr(epics, name, forbidden)
    from PyQt5.QtWidgets import QApplication
    from half_linac.src.apps.digital_shadow.main import DigitalShadowWindow
    app = QApplication.instance() or QApplication([])
    window = DigitalShadowWindow(output_root=tmp_path / "runs")

    def wait():
        deadline = time.monotonic() + 30
        while window.controller.busy:
            assert time.monotonic() < deadline, window.log.toPlainText()
            app.processEvents()
            time.sleep(0.01)
        app.processEvents()
        assert window.controller.record["status"] == "partial", window.log.toPlainText()
        assert window.current_archive, window.status_label.text()
        return window.current_archive

    try:
        window.show()
        window.mode.setCurrentIndex(window.mode.findData("demo-real"))
        window.save_images.setChecked(save_images)
        window.example_button.click()
        window.run_button.click()
        first = wait()
        assert first["backend"] == "real" and first["result"]["synthetic"]
        assert first["result"]["prediction_status"] == "complete"
        assert len(first["result"]["comparisons"]) == window.results.rowCount() == 8
        assert all(row["status"] == "incompatible" and row["residual"] is None for row in first["result"]["comparisons"])
        assert len(first["result"]["missing_normalized_quadrupoles"]) == 2
        units = {entry["unit"] for entry in first["snapshot"]["entries"]}
        assert units == {"m^-2", "mm"}
        assert len(first["manifest"]["unit_normalizations"]) == 4
        assert "SYNTHETIC" in window.result_banner.text()
        assert {axis.get_ylabel() for axis in window.figure.axes} == {"Width [mm]"}
        if save_images:
            from half_linac.src.shared.digital_shadow.image_store import ImageStore
            images = first["image_index"]
            assert images["summary"]["saved"] == images["summary"]["requested"] == 4
            assert images["summary"]["status"] == "complete"
            pool = ImageStore(images["store_root"], images["summary"]["limit_bytes"])
            for frame in images["frames"]:
                assert pool.get(frame["object"]).dtype.name == "uint16"
                assert frame["derived_rms"]["status"] == "valid"
            assert len(list(pool.root.glob("*.npz"))) == 1
            assert window.image_results.rowCount() == 4
            window.images_toggle.setChecked(True)
            app.processEvents()
        window.grab().save(str(tmp_path / "real_demo_complete.png"))
        window._use_archive_inputs()
        assert window.mode.currentData() == "replay"
        window.run_button.click()
        second = wait()
        assert second["result"]["input_hash"] == first["result"]["input_hash"]
        assert second["result"]["comparisons"] == first["result"]["comparisons"]
        if save_images:
            assert first["image_index"] == second["image_index"]
            assert not list(Path(second["run_dir"]).parent.rglob("*.npz"))

        # Even outside the GUI, an unflagged command cannot open real PVs.
        request = Path(first["run_dir"]) / "request.json"
        out = tmp_path / "unflagged_live"
        live = subprocess.run([sys.executable, "-B", str(repo / "scripts/run_digital_shadow.py"),
            "run-real", "--request", str(request), "--output-root", str(out)],
            capture_output=True, text=True, timeout=10)
        assert live.returncode == 1 and "explicit allow_live" in live.stderr
        assert not out.exists()
        (tmp_path / "acceptance.json").write_text(json.dumps({
            "status": "passed", "first_run": first["run_dir"], "replay_run": second["run_dir"],
            "comparison_count": 8, "all_residuals_empty": True, "input_hash_equal": True,
            "raw_units_preserved": True, "live_command_without_flag_refused": True,
            "pv_api_denied_in_parent_and_worker": True,
            "images_enabled": save_images,
            "image_summary": first["image_index"]["summary"] if save_images else None,
        }, indent=2))
    finally:
        window.controller.shutdown_now()
        window.close()
        app.processEvents()
