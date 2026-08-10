from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass
from typing import Deque, Dict, List, Optional

from .utils import circular_mean_deg, median


@dataclass
class Sample:
    timestamp: float
    values: Dict[str, Optional[float]]
    ok: bool
    errors: Dict[str, str]


class DataBuffer:
    def __init__(self, max_age_s: float | None):
        self.max_age_s = None if max_age_s is None else float(max_age_s)
        self._samples: Deque[Sample] = deque()

    def append(self, sample: Sample) -> None:
        self._samples.append(sample)
        self.prune()

    def prune(self) -> None:
        if self.max_age_s is None:
            return
        cutoff = time.time() - self.max_age_s
        while self._samples and self._samples[0].timestamp < cutoff:
            self._samples.popleft()

    def samples_since(self, window_s: float) -> List[Sample]:
        cutoff = time.time() - float(window_s)
        return [s for s in self._samples if s.timestamp >= cutoff]

    def count_since(self, window_s: float) -> int:
        return len(self.samples_since(window_s))

    def aggregate(self, window_s: float) -> Optional[Dict[str, float]]:
        return self._aggregate_samples(self.samples_since(window_s))

    def aggregate_all(self) -> Optional[Dict[str, float]]:
        return self._aggregate_samples(list(self._samples))

    @staticmethod
    def _aggregate_samples(samples: List[Sample]) -> Optional[Dict[str, float]]:
        if not samples:
            return None

        keys = set()
        for s in samples:
            keys.update(s.values.keys())

        agg: Dict[str, float] = {}
        for key in sorted(keys):
            vals = [s.values.get(key) for s in samples]
            clean = [float(v) for v in vals if v is not None]
            if not clean:
                continue
            if key.endswith(".phase") or key == "phase":
                cm = circular_mean_deg(clean)
                if cm is not None:
                    agg[key] = cm
            else:
                med = median(clean)
                if med is not None:
                    agg[key] = med
        return agg
