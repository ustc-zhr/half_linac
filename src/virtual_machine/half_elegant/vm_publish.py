from __future__ import annotations

from pathlib import Path

import epics.ca
from epics import caput, caput_many

import half_linac.runtime_config as st
from half_linac.src.shared.elegant_backend.parser import (
    _load_bpm_centroids_from_sdds,
    _load_watch_image_from_sdds,
)


EPICS_CONNECTION_TIMEOUT_S = 0.5
EPICS_PUT_TIMEOUT_S = 5.0


class HalfVmPublisher:
    def _publish_many_best_effort(self, label, pv_names, values):
        if not pv_names:
            return True

        try:
            results = caput_many(
                pv_names,
                values,
                wait=False,
                connection_timeout=EPICS_CONNECTION_TIMEOUT_S,
                put_timeout=EPICS_PUT_TIMEOUT_S,
            )
        except epics.ca.ChannelAccessException as exc:
            print(f"{label} publish skipped: {exc}")
            return False
        except Exception as exc:
            print(f"{label} publish skipped: {exc}")
            return False

        failures = 0
        if results is not None:
            failures = sum(1 for result in results if result in (None, False))

        if failures:
            print(f"{label} publish incomplete: {failures}/{len(pv_names)} PV writes were not confirmed.")
            return False

        return True

    def _publish_flags_best_effort(self, flag_updates):
        if not flag_updates:
            return True

        try:
            failures = 0
            for channel, value in flag_updates:
                result = caput(
                    channel,
                    value,
                    wait=False,
                    connection_timeout=EPICS_CONNECTION_TIMEOUT_S,
                    timeout=EPICS_PUT_TIMEOUT_S,
                )
                if result in (None, False):
                    failures += 1
        except epics.ca.ChannelAccessException as exc:
            print(f"flag publish skipped: {exc}")
            return False
        except Exception as exc:
            print(f"flag publish skipped: {exc}")
            return False

        if failures:
            print(f"flag publish incomplete: {failures}/{len(flag_updates)} PV writes were not confirmed.")
            return False

        return True

    def publish_bpm(self, bpmcen_path) -> bool:
        bpm = _load_bpm_centroids_from_sdds(Path(bpmcen_path))

        x_channels = []
        y_channels = []
        x_values = []
        y_values = []
        for element_id, data in bpm.items():
            x_channels.append(f"HALF:IN:BPM:{element_id}:X:ao")
            y_channels.append(f"HALF:IN:BPM:{element_id}:Y:ao")
            x_values.append(data["Cx"])
            y_values.append(data["Cy"])

        x_ok = self._publish_many_best_effort("BPM X", x_channels, x_values)
        y_ok = self._publish_many_best_effort("BPM Y", y_channels, y_values)
        return x_ok and y_ok

    def publish_flags(self, *, lattice, usedline, elegant_dir) -> bool:
        elegant_dir = Path(elegant_dir)
        flag_updates = []

        for element_id, element in lattice.items():
            if (
                element["TYPE"] == "WATCH"
                and element["MODE"].lower() == "coord"
                and element["DISABLE"] == "0"
                and "PRF" in element["NAME"]
                and element_id in usedline
            ):
                channel = f"HALF:IN:FLAG:{element_id}:image1:ArrayData:vm"
                if "ESA" in element_id:
                    image = _load_watch_image_from_sdds(
                        elegant_dir / f"{element_id}.out",
                        pixel_shape=tuple(st.ESAflag_pixel_vm),
                        pixel_width_mm=float(st.ESAflag_pixel_width),
                    )
                else:
                    image = _load_watch_image_from_sdds(
                        elegant_dir / f"{element_id}.out",
                        pixel_shape=tuple(st.flag_pixel_vm),
                        pixel_width_mm=float(st.flag_pixel_width),
                    )
                flag_updates.append((channel, image))

        return self._publish_flags_best_effort(flag_updates)
