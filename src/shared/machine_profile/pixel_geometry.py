from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from .models import AppContext, MachineProfile, MachineProfileError


@dataclass(frozen=True)
class FlagPixelGeometry:
    shape: tuple[int, int]
    pixel_width_mm: float
    flip_y: bool = False
    default_roi: Mapping[str, int] | None = None


def resolve_element_image_geometry(
    target: MachineProfile | AppContext,
    flag_id: str,
    backend_name: str,
) -> FlagPixelGeometry:
    """Resolve one FLAG's explicit image geometry for a control backend."""
    profile = target.profile if isinstance(target, AppContext) else target
    element = profile.get_element(flag_id)
    if element.kind != "flag":
        raise MachineProfileError(f"Element {flag_id!r} is not a flag.")
    geometry = _expect_mapping(
        element.image_geometry,
        f"elements.{flag_id}.image_geometry",
    )
    backend_geometry = _expect_mapping(
        geometry.get(backend_name),
        f"elements.{flag_id}.image_geometry.{backend_name}",
    )
    return _parse_geometry(
        backend_geometry,
        f"elements.{flag_id}.image_geometry.{backend_name}",
    )


def resolve_flag_pixel_geometry(
    workflow: Mapping[str, object],
    workflow_path: str,
    backend_name: str,
    flag_id: str | None = None,
) -> FlagPixelGeometry:
    geometry = workflow.get("flag_pixel_geometry")
    if isinstance(geometry, Mapping):
        return _resolve_structured_geometry(geometry, workflow_path, backend_name, flag_id)
    if geometry is not None:
        raise MachineProfileError(f"{workflow_path}.flag_pixel_geometry must be a mapping.")
    return _resolve_legacy_geometry(workflow, workflow_path, backend_name)


def _resolve_structured_geometry(
    geometry: Mapping[str, object],
    workflow_path: str,
    backend_name: str,
    flag_id: str | None,
) -> FlagPixelGeometry:
    default_by_backend = _expect_mapping(
        geometry.get("default"),
        f"{workflow_path}.flag_pixel_geometry.default",
    )
    default_geometry = _expect_mapping(
        default_by_backend.get(backend_name),
        f"{workflow_path}.flag_pixel_geometry.default.{backend_name}",
    )

    selected_geometry = dict(default_geometry)
    by_flag = geometry.get("by_flag", {})
    if by_flag is not None:
        by_flag = _expect_mapping(by_flag, f"{workflow_path}.flag_pixel_geometry.by_flag")
        if flag_id:
            flag_geometry_by_backend = by_flag.get(flag_id)
            if flag_geometry_by_backend is not None:
                flag_geometry_by_backend = _expect_mapping(
                    flag_geometry_by_backend,
                    f"{workflow_path}.flag_pixel_geometry.by_flag.{flag_id}",
                )
                flag_geometry = flag_geometry_by_backend.get(backend_name)
                if flag_geometry is not None:
                    selected_geometry.update(
                        _expect_mapping(
                            flag_geometry,
                            f"{workflow_path}.flag_pixel_geometry.by_flag.{flag_id}.{backend_name}",
                        )
                    )

    return _parse_geometry(
        selected_geometry,
        f"{workflow_path}.flag_pixel_geometry"
        + (f".by_flag.{flag_id}.{backend_name}" if flag_id else f".default.{backend_name}"),
    )


def _resolve_legacy_geometry(
    workflow: Mapping[str, object],
    workflow_path: str,
    backend_name: str,
) -> FlagPixelGeometry:
    shape_by_backend = _expect_mapping(
        workflow.get("flag_pixel_shape"),
        f"{workflow_path}.flag_pixel_shape",
    )
    width_by_backend = _expect_mapping(
        workflow.get("flag_pixel_width_mm"),
        f"{workflow_path}.flag_pixel_width_mm",
    )
    shape = shape_by_backend.get(backend_name)
    if not isinstance(shape, list) or len(shape) != 2:
        raise MachineProfileError(
            f"{workflow_path}.flag_pixel_shape.{backend_name} must be [nx, ny]."
        )
    if backend_name not in width_by_backend:
        raise MachineProfileError(
            f"{workflow_path}.flag_pixel_width_mm is missing backend {backend_name!r}."
        )
    return _parse_geometry(
        {"shape": shape, "pixel_width_mm": width_by_backend[backend_name]},
        f"{workflow_path}.{backend_name}",
    )


def _parse_geometry(raw: Mapping[str, object], location: str) -> FlagPixelGeometry:
    shape = raw.get("shape")
    if not isinstance(shape, list) or len(shape) != 2:
        raise MachineProfileError(f"{location}.shape must be [nx, ny].")
    try:
        pixel_shape = (int(shape[0]), int(shape[1]))
    except (TypeError, ValueError) as exc:
        raise MachineProfileError(f"{location}.shape must contain integer values.") from exc
    if pixel_shape[0] <= 0 or pixel_shape[1] <= 0:
        raise MachineProfileError(f"{location}.shape values must be positive.")

    try:
        pixel_width_mm = float(raw["pixel_width_mm"])
    except KeyError as exc:
        raise MachineProfileError(f"{location}.pixel_width_mm is required.") from exc
    except (TypeError, ValueError) as exc:
        raise MachineProfileError(f"{location}.pixel_width_mm must be numeric.") from exc
    if pixel_width_mm <= 0:
        raise MachineProfileError(f"{location}.pixel_width_mm must be positive.")

    flip_y = raw.get("flip_y", False)
    if not isinstance(flip_y, bool):
        raise MachineProfileError(f"{location}.flip_y must be boolean.")

    default_roi = raw.get("default_roi")
    if default_roi is not None:
        default_roi = _expect_mapping(default_roi, f"{location}.default_roi")
        default_roi = {
            key: int(default_roi[key])
            for key in ("x", "y", "width", "height")
        }

    return FlagPixelGeometry(
        shape=pixel_shape,
        pixel_width_mm=pixel_width_mm,
        flip_y=flip_y,
        default_roi=default_roi,
    )


def _expect_mapping(value: object, location: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise MachineProfileError(f"{location} must be a mapping.")
    return value


__all__ = [
    "FlagPixelGeometry",
    "resolve_element_image_geometry",
    "resolve_flag_pixel_geometry",
]
