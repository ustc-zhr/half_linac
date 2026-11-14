"""Compatibility imports for the archived strict-frame prototype.

Production InfoBAX uses MeasurementSnapshot/PointAcquirer and the main window.
The explicit verified-frame policy remains available to isolated IOC checks.
"""
from .measurement_service import CameraSettings, VerifiedFrameAcquirer as CameraPointAcquirer, fingerprint

__all__ = ["CameraSettings", "CameraPointAcquirer", "fingerprint"]
