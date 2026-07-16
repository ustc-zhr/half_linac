from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

import numpy as np


class BackgroundStoreError(ValueError):
    pass


def validate_background_image(image, expected_shape=None) -> np.ndarray:
    array = np.asarray(image, dtype=float)
    if array.ndim != 2:
        raise BackgroundStoreError("Background image must be two-dimensional.")
    if expected_shape is not None and tuple(array.shape) != tuple(expected_shape):
        raise BackgroundStoreError(
            f"Background shape {tuple(array.shape)} does not match expected "
            f"shape {tuple(expected_shape)}."
        )
    if not np.all(np.isfinite(array)):
        raise BackgroundStoreError("Background image contains non-finite values.")
    return array


def save_background(
    image,
    image_path: Path,
    metadata_path: Path,
    metadata: Mapping[str, Any],
) -> tuple[Path, Path]:
    array = validate_background_image(image)
    image_path = Path(image_path)
    metadata_path = Path(metadata_path)
    image_path.parent.mkdir(parents=True, exist_ok=True)
    metadata_path.parent.mkdir(parents=True, exist_ok=True)

    image_temp = image_path.with_suffix(image_path.suffix + ".tmp")
    metadata_temp = metadata_path.with_suffix(metadata_path.suffix + ".tmp")
    try:
        with image_temp.open("wb") as stream:
            np.save(stream, array)
        metadata_temp.write_text(
            json.dumps(dict(metadata), indent=2, sort_keys=True),
            encoding="utf-8",
        )
        image_temp.replace(image_path)
        metadata_temp.replace(metadata_path)
    finally:
        for path in (image_temp, metadata_temp):
            try:
                path.unlink()
            except OSError:
                pass
    return image_path, metadata_path


def load_background(
    image_path: Path,
    metadata_path: Path | None = None,
    *,
    expected_shape=None,
) -> tuple[np.ndarray, dict[str, Any]]:
    image_path = Path(image_path)
    try:
        image = np.load(image_path, allow_pickle=False)
    except (OSError, ValueError) as exc:
        raise BackgroundStoreError(f"Could not load background image: {exc}") from exc
    array = validate_background_image(image, expected_shape)

    metadata = {}
    if metadata_path is not None and Path(metadata_path).is_file():
        try:
            raw = json.loads(Path(metadata_path).read_text(encoding="utf-8"))
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            raise BackgroundStoreError(f"Could not load background metadata: {exc}") from exc
        if not isinstance(raw, dict):
            raise BackgroundStoreError("Background metadata must be a JSON object.")
        metadata = raw
    return array, metadata
