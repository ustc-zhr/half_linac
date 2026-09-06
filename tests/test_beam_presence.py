import numpy as np

from half_linac.src.shared.beam_diagnostics import (
    ImageROI,
    detect_beam_presence,
)


def test_beam_presence_rejects_empty_image():
    result = detect_beam_presence(np.zeros((100, 120)))

    assert not result.has_beam


def test_beam_presence_selects_brightest_region_and_keeps_global_roi_center():
    image = np.zeros((200, 200))
    image[80:88, 20:28] = 500.0
    image[80:92, 144:156] = 150.0

    result = detect_beam_presence(
        image,
        roi=ImageROI(x=100, y=0, width=100, height=200),
    )

    assert result.has_beam
    assert result.center_x_pixel == 149.5


def test_beam_presence_reports_but_does_not_reject_large_aspect_ratio():
    image = np.zeros((200, 200))
    image[98:103, 50:130] = 500.0

    result = detect_beam_presence(image)

    assert result.has_beam
    assert result.aspect_ratio > 6.0
    assert result.area_px == 400
