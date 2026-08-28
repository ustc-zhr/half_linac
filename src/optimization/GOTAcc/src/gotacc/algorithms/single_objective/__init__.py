"""Single-objective optimization algorithms."""

_EXPORTS = {
    "BOOptimizer": (".bo", "BOOptimizer"),
    "ConsBOOptimizer": (".consbo", "ConsBOOptimizer"),
    "ConsMGGPOSOOptimizer": (".consmggpo_so", "ConsMGGPOSOOptimizer"),
    "MGGPOSOOptimizer": (".mggpo_so", "MGGPOSOOptimizer"),
    "TuRBOOptimizer": (".turbo", "TuRBOOptimizer"),
    "RCDSOptimizer": (".rcds", "RCDSOptimizer"),
}


def __getattr__(name):
    if name not in _EXPORTS:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    from importlib import import_module

    module_name, attr_name = _EXPORTS[name]
    value = getattr(import_module(module_name, __name__), attr_name)
    globals()[name] = value
    return value

__all__ = [
    "BOOptimizer",
    "ConsBOOptimizer",
    "ConsMGGPOSOOptimizer",
    "MGGPOSOOptimizer",
    "TuRBOOptimizer",
    "RCDSOptimizer",
]
