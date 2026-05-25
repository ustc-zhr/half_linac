from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping, Sequence

from half_linac.src.shared.elegant_backend import (
    VmPublishPlan,
    VmPublisher,
    build_vm_publish_plan,
)
from half_linac.src.shared.machine_profile import MachineProfile, resolve_machine_runtime


class HalfVmPublisher:
    def __init__(
        self,
        profile: MachineProfile | None = None,
        *,
        plan: VmPublishPlan | None = None,
        publisher: VmPublisher | None = None,
    ):
        self.profile = profile or resolve_machine_runtime().profile
        self.plan = plan or build_vm_publish_plan(self.profile)
        self._publisher = publisher or VmPublisher()

    def publish_bpm(self, bpmcen_path: str | Path) -> bool:
        return self._publisher.publish_bpms(self.plan, bpmcen_path)

    def publish_flags(
        self,
        *,
        lattice: Mapping[str, Mapping[str, Any]],
        usedline: Sequence[str],
        elegant_dir: str | Path,
    ) -> bool:
        return self._publisher.publish_watch_images(
            self.plan,
            lattice=lattice,
            usedline=usedline,
            elegant_dir=elegant_dir,
        )
