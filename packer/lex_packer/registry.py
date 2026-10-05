"""组件注册表：每个组件的可打包分类（packable / cloud_locked）。

spec lex-private-distribution「可私有化不变量」：
- 交付包每个客户可见组件必须可打包（packable）；
- 无法打包的云端专属依赖（cloud_locked）必须被包内替代换掉或移出 bundle；
- 未登记组件 = 构建红（fail-closed，禁止悄悄引入未分类依赖）。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

PACKABLE = "packable"
CLOUD_LOCKED = "cloud_locked"

DEFAULT_REGISTRY_PATH = Path(__file__).with_name("registry_components.json")


@dataclass(frozen=True)
class ComponentSpec:
    """组件分类条目。"""

    name: str
    kind: str
    klass: str
    substitute: str | None
    note: str

    @property
    def packable(self) -> bool:
        return self.klass == PACKABLE

    def to_dict(self) -> dict[str, str | None]:
        return {
            "name": self.name,
            "kind": self.kind,
            "class": self.klass,
            "substitute": self.substitute,
            "note": self.note,
        }


class ComponentRegistry:
    """组件分类表（JSON 文件加载；默认随包 registry_components.json）。"""

    def __init__(self, specs: list[ComponentSpec], *, source: str = "") -> None:
        self._specs = {spec.name: spec for spec in specs}
        self.source = source

    @classmethod
    def load(cls, path: Path | str | None = None) -> "ComponentRegistry":
        target = Path(path) if path else DEFAULT_REGISTRY_PATH
        payload = json.loads(target.read_text(encoding="utf-8"))
        specs = []
        for raw in payload.get("components", []):
            klass = raw.get("class")
            if klass not in (PACKABLE, CLOUD_LOCKED):
                raise ValueError(
                    f"component {raw.get('name')!r} has invalid class {klass!r}"
                    " (must be packable|cloud_locked)"
                )
            specs.append(
                ComponentSpec(
                    name=str(raw["name"]),
                    kind=str(raw.get("kind", "unknown")),
                    klass=str(klass),
                    substitute=(str(raw["substitute"]) if raw.get("substitute") else None),
                    note=str(raw.get("note", "")),
                )
            )
        return cls(specs, source=str(target))

    def get(self, name: str) -> ComponentSpec | None:
        return self._specs.get(name)

    def names(self) -> list[str]:
        return sorted(self._specs)

    def require(self, name: str) -> ComponentSpec:
        """取组件分类；未登记即 KeyError（调用侧转构建红）。"""
        if name not in self._specs:
            raise KeyError(name)
        return self._specs[name]


@lru_cache(maxsize=8)
def load_registry(path: str | None = None) -> ComponentRegistry:
    """缓存加载（路径为 None 时取随包默认表）。"""
    return ComponentRegistry.load(path)


def describe_registry(registry: ComponentRegistry) -> dict:
    """排障/目录用：分类一览（按 class 分组）。"""
    grouped: dict[str, list[dict]] = {PACKABLE: [], CLOUD_LOCKED: []}
    for name in registry.names():
        spec = registry.require(name)
        grouped[spec.klass].append(spec.to_dict())
    return {"schema": "lex.distribution.component-registry/v1",
            "source": registry.source, "components": grouped}