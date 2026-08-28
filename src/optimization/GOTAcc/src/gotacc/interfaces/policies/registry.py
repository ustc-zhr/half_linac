from __future__ import annotations

import copy
from dataclasses import dataclass, field, replace
from typing import Any, Callable, Literal, Mapping


PolicyKind = Literal["write", "objective", "constraint"]
PolicyFactory = Callable[[Mapping[str, Any]], Any]
PresetAdapter = Callable[[Mapping[str, Any], dict[str, Any]], Mapping[str, Any]]


@dataclass(frozen=True)
class PolicyDefinition:
    """Metadata and construction entry for one policy implementation."""

    name: str
    kind: PolicyKind
    factory: PolicyFactory
    default_kwargs: Mapping[str, Any] = field(default_factory=dict)
    aliases: tuple[str, ...] = ()
    description: str = ""
    gui_visible: bool = True
    is_default: bool = False

    def defaults(self) -> dict[str, Any]:
        return copy.deepcopy(dict(self.default_kwargs))

    def build(self, kwargs: Mapping[str, Any] | None = None) -> Any:
        params = self.defaults()
        if kwargs is not None:
            if not isinstance(kwargs, Mapping):
                raise TypeError(f"{self.kind} policy kwargs must be a mapping")
            # Preserve the current builders' behavior: unknown kwargs are ignored.
            params.update({key: value for key, value in kwargs.items() if key in params})
        return self.factory(params)


@dataclass(frozen=True)
class PolicyPreset:
    """Reusable declarative configuration for a registered policy."""

    name: str
    kind: PolicyKind
    policy_name: str
    kwargs: Mapping[str, Any] = field(default_factory=dict)
    display_name: str = ""
    description: str = ""
    gui_visible: bool = True
    legacy_kwargs_adapter: PresetAdapter | None = None

    def expanded_kwargs(self) -> dict[str, Any]:
        return copy.deepcopy(dict(self.kwargs))


class PolicyRegistry:
    """Central registry for write, objective and constraint policies."""

    KINDS: tuple[PolicyKind, ...] = ("write", "objective", "constraint")

    def __init__(self) -> None:
        self._definitions: dict[PolicyKind, dict[str, PolicyDefinition]] = {
            kind: {} for kind in self.KINDS
        }
        self._canonical_names: dict[PolicyKind, list[str]] = {
            kind: [] for kind in self.KINDS
        }
        self._default_names: dict[PolicyKind, str | None] = {
            kind: None for kind in self.KINDS
        }
        self._presets: dict[PolicyKind, dict[str, PolicyPreset]] = {
            kind: {} for kind in self.KINDS
        }
        self._preset_names: dict[PolicyKind, list[str]] = {
            kind: [] for kind in self.KINDS
        }

    @classmethod
    def _normalize_kind(cls, kind: str) -> PolicyKind:
        normalized = str(kind).strip().lower()
        if normalized not in cls.KINDS:
            raise ValueError(
                f"Unknown policy kind: {kind!r}. Expected one of {list(cls.KINDS)}"
            )
        return normalized  # type: ignore[return-value]

    @staticmethod
    def _normalize_name(name: str) -> str:
        normalized = str(name).strip().lower()
        if not normalized:
            raise ValueError("Policy name cannot be empty")
        return normalized

    def register(self, definition: PolicyDefinition) -> None:
        kind = self._normalize_kind(definition.kind)
        canonical = self._normalize_name(definition.name)
        aliases = tuple(self._normalize_name(alias) for alias in definition.aliases)
        names = (canonical, *aliases)
        duplicate = next((name for name in names if name in self._definitions[kind]), None)
        if duplicate is not None:
            raise ValueError(f"Policy name already registered for {kind}: {duplicate!r}")
        if len(set(names)) != len(names):
            raise ValueError(f"Policy {canonical!r} defines duplicate aliases")
        if definition.is_default and self._default_names[kind] is not None:
            raise ValueError(
                f"Default {kind} policy already registered: {self._default_names[kind]!r}"
            )

        normalized_definition = replace(
            definition,
            name=canonical,
            kind=kind,
            aliases=aliases,
        )
        for name in names:
            self._definitions[kind][name] = normalized_definition
        self._canonical_names[kind].append(canonical)
        if definition.is_default:
            self._default_names[kind] = canonical

    def resolve(self, kind: str, name: str) -> PolicyDefinition:
        normalized_kind = self._normalize_kind(kind)
        normalized_name = self._normalize_name(name)
        try:
            return self._definitions[normalized_kind][normalized_name]
        except KeyError as exc:
            raise ValueError(
                f"Unknown {normalized_kind} policy: {normalized_name!r}"
            ) from exc

    def register_preset(self, preset: PolicyPreset) -> None:
        kind = self._normalize_kind(preset.kind)
        name = self._normalize_name(preset.name)
        policy_name = self.resolve(kind, preset.policy_name).name
        if name in self._presets[kind]:
            raise ValueError(f"Policy preset already registered for {kind}: {name!r}")
        normalized = replace(
            preset,
            name=name,
            kind=kind,
            policy_name=policy_name,
            kwargs=copy.deepcopy(dict(preset.kwargs)),
            display_name=preset.display_name.strip() or name,
        )
        # Fail fast when a built-in preset is declared incorrectly.
        self.validate(kind, policy_name, normalized.kwargs)
        self._presets[kind][name] = normalized
        self._preset_names[kind].append(name)

    def resolve_preset(self, kind: str, name: str) -> PolicyPreset:
        normalized_kind = self._normalize_kind(kind)
        normalized_name = self._normalize_name(name)
        try:
            return self._presets[normalized_kind][normalized_name]
        except KeyError as exc:
            raise ValueError(
                f"Unknown {normalized_kind} policy preset: {normalized_name!r}"
            ) from exc

    def preset_names(self, kind: str, *, gui_only: bool = False) -> tuple[str, ...]:
        normalized_kind = self._normalize_kind(kind)
        names = self._preset_names[normalized_kind]
        if gui_only:
            names = [
                name
                for name in names
                if self._presets[normalized_kind][name].gui_visible
            ]
        return tuple(names)

    def expand_preset(
        self,
        kind: str,
        name: str,
        legacy_kwargs: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        preset = self.resolve_preset(kind, name)
        kwargs = preset.expanded_kwargs()
        if legacy_kwargs is not None and preset.legacy_kwargs_adapter is not None:
            kwargs = dict(preset.legacy_kwargs_adapter(legacy_kwargs, kwargs))
        return {"name": preset.policy_name, "kwargs": kwargs}

    def contains(self, kind: str, name: str) -> bool:
        try:
            self.resolve(kind, name)
        except ValueError:
            return False
        return True

    def default_name(self, kind: str, *, gui_only: bool = False) -> str:
        normalized_kind = self._normalize_kind(kind)
        default_name = self._default_names[normalized_kind]
        if default_name is not None:
            definition = self._definitions[normalized_kind][default_name]
            if not gui_only or definition.gui_visible:
                return default_name
        names = self.names(normalized_kind, gui_only=gui_only)
        if not names:
            raise LookupError(f"No {normalized_kind} policies are registered")
        return names[0]

    def names(
        self,
        kind: str,
        *,
        include_aliases: bool = False,
        gui_only: bool = False,
    ) -> tuple[str, ...]:
        normalized_kind = self._normalize_kind(kind)
        canonical_names = self._canonical_names[normalized_kind]
        if gui_only:
            canonical_names = [
                name
                for name in canonical_names
                if self._definitions[normalized_kind][name].gui_visible
            ]
        if not include_aliases:
            return tuple(canonical_names)

        allowed_definition_ids = {
            id(self._definitions[normalized_kind][name]) for name in canonical_names
        }
        return tuple(
            name
            for name, definition in self._definitions[normalized_kind].items()
            if id(definition) in allowed_definition_ids
        )

    def build(
        self,
        kind: str,
        name: str,
        kwargs: Mapping[str, Any] | None = None,
    ) -> Any:
        return self.resolve(kind, name).build(kwargs)

    def validate(
        self,
        kind: str,
        name: str,
        kwargs: Mapping[str, Any] | None = None,
    ) -> None:
        """Construct a policy to validate its declarative configuration."""
        self.resolve(kind, name).build(kwargs)
