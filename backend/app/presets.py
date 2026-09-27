"""YAML presets (``backend/presets/*.yaml``) and workspace import/export.

A preset is a partial ``AgentConfig`` plus an optional ``description``; any field
left out takes the default. Drop a new file in the folder and it shows up in the UI.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from .schemas import AgentConfig
from .settings import settings


def _load(path: Path) -> dict[str, Any]:
    data = yaml.safe_load(path.read_text()) or {}
    if not isinstance(data, dict):
        raise ValueError(f"{path.name}: preset must be a mapping")
    return data


def list_presets() -> list[dict]:
    out = []
    for p in sorted(settings.presets_dir.glob("*.yaml")):
        try:
            d = _load(p)
        except (ValueError, yaml.YAMLError) as e:
            out.append({"name": p.stem, "error": str(e)})
            continue
        out.append({"name": p.stem, "title": d.get("name", p.stem), "description": d.get("description", ""),
                    "provider": (d.get("model") or {}).get("provider")})
    return out


def preset_config(name: str) -> AgentConfig:
    path = (settings.presets_dir / f"{name}.yaml").resolve()
    if settings.presets_dir.resolve() not in path.parents or not path.exists():
        raise KeyError(f"preset {name!r} not found")
    d = _load(path)
    d.pop("description", None)
    d.pop("id", None)  # every agent created from a preset gets a fresh id
    return AgentConfig.model_validate(d)


def export_yaml(configs: list[AgentConfig]) -> str:
    return yaml.safe_dump({"agents": [c.model_dump(mode="json") for c in configs]}, sort_keys=False, allow_unicode=True)


def import_yaml(text: str) -> list[AgentConfig]:
    data = yaml.safe_load(text) or {}
    items = data.get("agents") if isinstance(data, dict) else data
    if not isinstance(items, list):
        raise ValueError("expected a list of agents (or {agents: [...]})")
    out = []
    for item in items:
        if not isinstance(item, dict):
            raise ValueError("each agent must be a mapping")
        item = dict(item)
        item.pop("id", None)
        item.pop("description", None)
        out.append(AgentConfig.model_validate(item))
    return out
