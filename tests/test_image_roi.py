import numpy as np
import pytest

from half_linac.src.shared.beam_diagnostics.roi import ImageROI, ROIError, crop_image, resolve_roi, save_roi


def test_roi_crop_preserves_full_frame_coordinates():
    image = np.arange(20).reshape(4, 5)
    cropped, selected, warnings = crop_image(image, ImageROI(1, 1, 3, 2))
    assert selected == ImageROI(1, 1, 3, 2)
    assert warnings == ()
    np.testing.assert_array_equal(cropped, [[6, 7, 8], [11, 12, 13]])


def test_roi_validation_and_clamping():
    with pytest.raises(ROIError):
        ImageROI(-1, 0, 2, 2)
    clipped, _selected, warnings = crop_image(np.zeros((4, 5)), ImageROI(4, 3, 4, 4))
    assert clipped.shape == (1, 1)
    assert warnings


def test_runtime_roi_overrides_configured(tmp_path):
    path = tmp_path / "roi.json"
    save_roi(path, ImageROI(1, 1, 2, 2))
    roi, source, warnings = resolve_roi(runtime_path=path, configured={"x": 0, "y": 0, "width": 4, "height": 4}, shape=(4, 5))
    assert roi == ImageROI(1, 1, 2, 2)
    assert source == "runtime"
    assert warnings == ()


def test_configured_roi_is_used_when_runtime_roi_is_missing(tmp_path):
    roi, source, warnings = resolve_roi(
        runtime_path=tmp_path / "missing.json",
        configured={"x": 2, "y": 1, "width": 3, "height": 2},
        shape=(5, 8),
    )
    assert roi == ImageROI(2, 1, 3, 2)
    assert source == "configured"
    assert warnings == ()


def test_full_frame_is_the_disabled_roi_fallback(tmp_path):
    roi, source, warnings = resolve_roi(
        runtime_path=tmp_path / "missing.json",
        configured=None,
        shape=(5, 8),
    )
    assert roi == ImageROI(0, 0, 8, 5)
    assert source == "full_frame"
    assert warnings == ()
