"""Small rolling sample buffer for the Orbit Display jitter view."""

from collections import deque
from math import isfinite, sqrt
from operator import index


DEFAULT_WINDOW_SIZE = 30
MIN_WINDOW_SIZE = 10
MAX_WINDOW_SIZE = 3600


def finite_mm(value, scale):
    try:
        number = float(value) * scale
    except (TypeError, ValueError, OverflowError):
        return None
    return number if isfinite(number) else None


class JitterSamples:
    def __init__(self, bpm_count, window_size=DEFAULT_WINDOW_SIZE):
        self.bpm_count = bpm_count
        self.window_size = DEFAULT_WINDOW_SIZE
        self.set_window_size(window_size)
        self.samples = deque(maxlen=MAX_WINDOW_SIZE)

    def clear(self):
        self.samples.clear()

    def set_window_size(self, size):
        try:
            size = index(size)
        except TypeError as exc:
            raise ValueError("Window size must be a whole number of samples") from exc
        if not MIN_WINDOW_SIZE <= size <= MAX_WINDOW_SIZE:
            raise ValueError(
                f"Window size must be between {MIN_WINDOW_SIZE} and {MAX_WINDOW_SIZE} samples"
            )
        self.window_size = size

    def add(self, timestamp, x_values, y_values, scale):
        def normalize(values):
            values = [] if values is None else list(values)
            return tuple(
                finite_mm(values[index], scale) if index < len(values) else None
                for index in range(self.bpm_count)
            )

        self.samples.append((timestamp, normalize(x_values), normalize(y_values)))

    def visible(self):
        return list(self.samples)[-self.window_size:]

    def series(self, bpm_index, plane):
        position = 1 if plane == "x" else 2
        return [(row[0], row[position][bpm_index]) for row in self.visible()]

    def rms(self, bpm_index, plane, minimum=10):
        values = [value for _, value in self.series(bpm_index, plane) if value is not None]
        if len(values) < minimum:
            return None, len(values)
        mean = sum(values) / len(values)
        return sqrt(sum((value - mean) ** 2 for value in values) / len(values)), len(values)
