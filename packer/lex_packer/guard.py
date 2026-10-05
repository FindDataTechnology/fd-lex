"""CI 云专属依赖守卫。

spec lex-private-distribution「可私有化不变量」场景「CI 守卫拒绝云专属依赖」：
构建器检测到某组件存在无法打包的云端专属依赖 → 构建失败并指名违规组件与修复方向。

守卫语义（fail-closed）：
1. bundle 声明的每个组件必须在注册表中登记；未登记 = 违规（禁止未分类依赖混入）。
2. 登记为 ``cloud_locked`` 的组件出现在 bundle = 违规；修复方向：
   - 有包内替代 → 指名替代组件（如 siliconflow-embedding-api → lex-embed-local）；
   - 无替代 → 从 bundle 组件清单移除（如 sub2api-billing）。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .registry import CLOUD_LOCKED, ComponentRegistry

FIX_SUBSTITUTE = "改用包内替代 {substitute!r}（否则违反可私有化不变量 lex-private-distribution）"
FIX_REMOVE = "从 bundle 组件清单移除（该组件无可打包替代，客户机房不可用）"
FIX_CLASSIFY = "在组件注册表中登记其可打包分类（packable|cloud_locked）后再构建"


@dataclass
class GuardReport:
    """守卫报告：violations 非空即构建红。"""

    checked: list[dict] = field(default_factory=list)
    violations: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.violations

    def to_dict(self) -> dict:
        return {
            "ok": self.ok,
            "checked": self.checked,
            "violations": self.violations,
        }


def check_components(
    registry: ComponentRegistry, component_names: list[str]
) -> GuardReport:
    """校验 bundle 声明的组件清单；返回报告（不抛异常，便于 CLI 打印全部违规）。"""
    report = GuardReport()
    for name in component_names:
        try:
            spec = registry.require(name)
        except KeyError:
            report.violations.append(
                f"未登记组件 {name!r}：无法判定是否可打包；修复方向：{FIX_CLASSIFY}"
            )
            report.checked.append({"name": name, "class": "unregistered", "ok": False})
            continue
        if spec.klass == CLOUD_LOCKED:
            fix = (
                FIX_SUBSTITUTE.format(substitute=spec.substitute)
                if spec.substitute
                else FIX_REMOVE
            )
            report.violations.append(
                f"云端专属依赖组件 {name!r}（用途：{spec.note or spec.kind}）无法打包；"
                f"修复方向：{fix}"
            )
            report.checked.append({"name": name, "class": spec.klass, "ok": False})
            continue
        report.checked.append({"name": name, "class": spec.klass, "ok": True})
    return report


def assert_components_packable(
    registry: ComponentRegistry, component_names: list[str]
) -> GuardReport:
    """守卫硬门：违规即抛 GuardViolationError（指名全部违规组件与修复方向）。"""
    from .errors import GuardViolationError

    report = check_components(registry, component_names)
    if not report.ok:
        raise GuardViolationError(report.violations)
    return report