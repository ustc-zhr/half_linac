from __future__ import annotations

import io
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch


REPO_ROOT = Path(__file__).resolve().parents[1]
PARENT = REPO_ROOT.parent
if str(PARENT) not in sys.path:
    sys.path.insert(0, str(PARENT))

from half_linac.src.virtual_machine.common import start_VM


class VmRuntimeLoggingTests(unittest.TestCase):
    def test_update_logs_published_data_with_device_counts(self):
        parser = SimpleNamespace(json_to_lte_ele=Mock(), lattice={})
        publisher = SimpleNamespace(
            publish_bpms=Mock(return_value=True),
            publish_watch_images=Mock(return_value=False),
            publish_watch_scalars=Mock(return_value=True),
        )
        plan = SimpleNamespace(
            bpm_specs=(object(), object()),
            watch_image_specs=(object(),),
            watch_scalar_specs=(object(), object(), object(), object()),
        )

        output = io.StringIO()
        with (
            patch.object(start_VM, "_run_elegant"),
            patch.object(start_VM, "read_runtime_state", return_value={"usedline": []}),
            redirect_stdout(output),
        ):
            start_VM._update_vm_outputs(
                parser,
                publisher,
                plan,
                Path("/tmp/elegant"),
                Path("/tmp/runtime.json"),
            )

        self.assertEqual(
            output.getvalue().splitlines(),
            [
                "Running Elegant simulation...",
                "Elegant simulation completed.",
                "Published BPM positions (2 devices).",
                "Screen image publishing skipped or incomplete (1 device).",
                "Published ICT bunch charge (4 devices).",
            ],
        )

    def test_update_omits_unconfigured_publish_categories(self):
        parser = SimpleNamespace(json_to_lte_ele=Mock(), lattice={})
        publisher = SimpleNamespace(
            publish_bpms=Mock(),
            publish_watch_images=Mock(),
            publish_watch_scalars=Mock(),
        )
        plan = SimpleNamespace(bpm_specs=(), watch_image_specs=(), watch_scalar_specs=())

        with (
            patch.object(start_VM, "_run_elegant"),
            patch.object(start_VM, "read_runtime_state", return_value={"usedline": []}),
            redirect_stdout(io.StringIO()),
        ):
            start_VM._update_vm_outputs(
                parser,
                publisher,
                plan,
                Path("/tmp/elegant"),
                Path("/tmp/runtime.json"),
            )

        publisher.publish_bpms.assert_not_called()
        publisher.publish_watch_images.assert_not_called()
        publisher.publish_watch_scalars.assert_not_called()


if __name__ == "__main__":
    unittest.main()
