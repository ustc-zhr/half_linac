"""Bind research unit tests to checked-in inputs without changing CLI defaults."""
from pathlib import Path
from unittest.mock import patch

FROZEN_INPUTS = Path(__file__).parent / "fixtures" / "infobax_research"


def use_frozen_inputs(test_case, *modules):
    for module in modules:
        binding = patch.object(module, "FROZEN", FROZEN_INPUTS)
        binding.start()
        test_case.addCleanup(binding.stop)
