from pathlib import Path
import math
import sys


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import jitter_analysis.epics.client as client_module
from jitter_analysis.acquisition.sampler import AcquisitionSampler
from jitter_analysis.config.models import AnalysisFlags, ObjectSpec
from jitter_analysis.epics.client import PyEpicsClient, ReadResult


class _FakePV:
    def __init__(self, module, pv_name: str) -> None:
        self._module = module
        self._pv_name = pv_name

    @property
    def connected(self):
        return self._module.connections.get(self._pv_name, True)

    def wait_for_connection(self, timeout=None):
        self._module.wait_calls.append((self._pv_name, timeout))
        return self._module.connections.get(self._pv_name, True)

    def connect(self, timeout=None):
        self._module.connect_calls.append((self._pv_name, timeout))
        return self.connected

    def get(self, timeout=None):
        self._module.get_calls.append((self._pv_name, timeout))
        return self._module.values.get(self._pv_name)


class _FakeEpicsModuleBase:
    def __init__(self, values: dict[str, object], connections: dict[str, bool] | None = None) -> None:
        self.values = dict(values)
        self.connections = dict(connections or {})
        self.get_calls: list[tuple[str, float | None]] = []
        self.wait_calls: list[tuple[str, float | None]] = []
        self.connect_calls: list[tuple[str, float | None]] = []
        self.poll_calls: list[tuple[float | None, float | None]] = []
        self.caput_calls: list[tuple[str, float, bool, float | None]] = []
        self._pvs: dict[str, _FakePV] = {}

    def PV(self, pv_name: str):
        pv = self._pvs.get(pv_name)
        if pv is None:
            pv = _FakePV(self, pv_name)
            self._pvs[pv_name] = pv
        return pv

    def caput(self, pv_name: str, value: float, wait=True, timeout=None):
        self.caput_calls.append((pv_name, value, bool(wait), timeout))
        return True

    def poll(self, evt=1.0e-5, iot=1.0):
        self.poll_calls.append((evt, iot))


class _FakeEpicsModuleBulk(_FakeEpicsModuleBase):
    def __init__(self, values: dict[str, object], connections: dict[str, bool] | None = None) -> None:
        super().__init__(values, connections)
        self.bulk_calls: list[tuple[list[str], float | None]] = []
        self.bulk_put_calls: list[tuple[list[str], list[float], bool | None, float | None]] = []
        self.bulk_put_result = None

    def caget_many(self, pv_names, timeout=None):
        names = [str(pv_name) for pv_name in pv_names]
        self.bulk_calls.append((names, timeout))
        return [self.values.get(name) for name in names]

    def caput_many(self, pv_names, values, wait=None, timeout=None):
        names = [str(pv_name) for pv_name in pv_names]
        rows = [float(value) for value in values]
        self.bulk_put_calls.append((names, rows, wait, timeout))
        return self.bulk_put_result


def _object_spec(object_id: str, read_pv: str) -> ObjectSpec:
    return ObjectSpec(
        id=object_id,
        name=object_id.upper(),
        group="diag",
        read_pv=read_pv,
        unit="arb",
        precision=3,
        kind="monitor",
        access="ro",
        analysis=AnalysisFlags(),
    )


def test_read_many_uses_bulk_api_and_preserves_order():
    fake_epics = _FakeEpicsModuleBulk(
        values={"pv:a": 1.25, "pv:b": None, "pv:c": 3.75},
        connections={"pv:a": True, "pv:b": False, "pv:c": True},
    )
    original_epics = client_module.epics
    client_module.epics = fake_epics
    try:
        client = PyEpicsClient(timeout_sec=0.25)
        results = client.read_many(["pv:c", "pv:b", "pv:a"])
    finally:
        client_module.epics = original_epics

    assert fake_epics.bulk_calls == [(["pv:c", "pv:b", "pv:a"], 0.25)]
    assert fake_epics.wait_calls == []
    assert fake_epics.get_calls == []
    assert [result.connected for result in results] == [True, False, True]
    assert [result.value for result in results] == [3.75, None, 1.25]


def test_read_many_falls_back_to_individual_gets_without_bulk_support():
    fake_epics = _FakeEpicsModuleBase(
        values={"pv:x": 10.0, "pv:y": 20.0},
        connections={"pv:x": True, "pv:y": True},
    )
    original_epics = client_module.epics
    client_module.epics = fake_epics
    try:
        client = PyEpicsClient(timeout_sec=0.5)
        results = client.read_many(["pv:x", "pv:y"])
    finally:
        client_module.epics = original_epics

    assert fake_epics.wait_calls == []
    assert fake_epics.get_calls == [("pv:x", 0.0), ("pv:y", 0.0)]
    assert [result.connected for result in results] == [True, True]
    assert [result.value for result in results] == [10.0, 20.0]


def test_snapshot_connections_uses_cached_state_without_waiting():
    fake_epics = _FakeEpicsModuleBase(
        values={"pv:x": 10.0, "pv:y": 20.0},
        connections={"pv:x": True, "pv:y": False},
    )
    original_epics = client_module.epics
    client_module.epics = fake_epics
    try:
        client = PyEpicsClient(timeout_sec=0.5)
        states = client.snapshot_connections(["pv:y", "pv:x"])
    finally:
        client_module.epics = original_epics

    assert states == [False, True]
    assert fake_epics.wait_calls == []
    assert fake_epics.poll_calls == [(1.0e-05, 0.0)]


def test_write_many_uses_bulk_api_and_preserves_order():
    fake_epics = _FakeEpicsModuleBulk(values={})
    fake_epics.bulk_put_result = [True, False, True]
    original_epics = client_module.epics
    client_module.epics = fake_epics
    try:
        client = PyEpicsClient(timeout_sec=0.75)
        results = client.write_many([("pv:c", 3.0), ("pv:a", 1.0), ("pv:b", 2.0)])
    finally:
        client_module.epics = original_epics

    assert fake_epics.bulk_put_calls == [
        (["pv:c", "pv:a", "pv:b"], [3.0, 1.0, 2.0], True, 0.75)
    ]
    assert fake_epics.caput_calls == []
    assert results == [True, False, True]


def test_write_many_falls_back_to_individual_caputs_without_bulk_support():
    fake_epics = _FakeEpicsModuleBase(values={})
    original_epics = client_module.epics
    client_module.epics = fake_epics
    try:
        client = PyEpicsClient(timeout_sec=0.4)
        results = client.write_many([("pv:x", 10.0), ("pv:y", 20.0)])
    finally:
        client_module.epics = original_epics

    assert fake_epics.caput_calls == [
        ("pv:x", 10.0, True, 0.4),
        ("pv:y", 20.0, True, 0.4),
    ]
    assert results == [True, True]


def test_write_many_raises_when_bulk_status_is_ambiguous():
    fake_epics = _FakeEpicsModuleBulk(values={})
    fake_epics.bulk_put_result = None
    original_epics = client_module.epics
    client_module.epics = fake_epics
    try:
        client = PyEpicsClient(timeout_sec=0.6)
        try:
            client.write_many([("pv:x", 10.0)])
            raised = False
        except RuntimeError as exc:
            raised = True
            assert "caput_many returned no per-PV completion status" in str(exc)
    finally:
        client_module.epics = original_epics

    assert raised is True
    assert fake_epics.caput_calls == []


def test_sample_objects_share_one_batch_timestamp():
    class _StubClient:
        def __init__(self) -> None:
            self.calls: list[list[str]] = []

        def read_many(self, pv_names: list[str]) -> list[ReadResult]:
            self.calls.append(list(pv_names))
            return [
                ReadResult(value=1.5, connected=True),
                ReadResult(value=None, connected=False),
            ]

    client = _StubClient()
    sampler = AcquisitionSampler(client)
    objects = [
        _object_spec("bpm01_x", "PV:BPM01:X"),
        _object_spec("bpm01_y", "PV:BPM01:Y"),
    ]

    samples = sampler.sample_objects(objects, step_index=4)

    assert client.calls == [["PV:BPM01:X", "PV:BPM01:Y"]]
    assert [sample.pv_id for sample in samples] == ["bpm01_x", "bpm01_y"]
    assert samples[0].timestamp == samples[1].timestamp
    assert samples[0].step_index == 4
    assert samples[1].step_index == 4
    assert samples[0].batch_index == 4
    assert samples[1].batch_index == 4
    assert samples[0].value == 1.5
    assert math.isnan(samples[1].value)
    assert samples[1].connected is False
