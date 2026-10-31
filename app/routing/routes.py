"""Load configured aliases and resolve requested models to provider targets."""

from dataclasses import dataclass
from pathlib import Path

import yaml


@dataclass(frozen=True)
class ProviderTarget:
    """A concrete provider and model pair from the route configuration."""

    provider: str
    model: str


class RouteCatalog:
    """Alias catalog that resolves ordered provider/model fallback chains."""

    def __init__(self, config_path: str) -> None:
        """Load and validate alias targets from YAML."""
        raw = yaml.safe_load(Path(config_path).read_text(encoding="utf-8")) or {}
        aliases = raw.get("aliases", {})
        if not isinstance(aliases, dict):
            raise ValueError("routes.yaml aliases must be a mapping")
        self._aliases: dict[str, list[ProviderTarget]] = {}
        self._models: dict[str, ProviderTarget] = {}
        for alias, targets in aliases.items():
            if not isinstance(targets, list) or not targets:
                raise ValueError(f"Alias {alias} must have at least one target")
            parsed = [
                ProviderTarget(provider=item["provider"], model=item["model"]) for item in targets
            ]
            self._aliases[str(alias)] = parsed
            for target in parsed:
                self._models[target.model] = target

    def resolve(self, model: str | None, default_model: str) -> ProviderTarget:
        """Resolve an alias or concrete model to its current primary target."""
        requested = model or default_model
        if requested in self._aliases:
            return self._aliases[requested][0]
        if requested in self._models:
            return self._models[requested]
        if requested == "fake-model":
            return ProviderTarget(provider="fake", model=requested)
        raise KeyError(requested)

    def chain(self, model: str | None, default_model: str) -> list[ProviderTarget]:
        """Return all configured targets for an alias or one concrete target."""
        requested = model or default_model
        if requested in self._aliases:
            return list(self._aliases[requested])
        if requested in self._models:
            return [self._models[requested]]
        if requested == "fake-model":
            return [ProviderTarget(provider="fake", model=requested)]
        raise KeyError(requested)

    def list_models(self) -> list[str]:
        """Return aliases and distinct configured concrete model names."""
        return sorted(set(self._aliases) | set(self._models) | {"fake-model"})
