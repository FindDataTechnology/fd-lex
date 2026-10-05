"""构建器错误类型：构建红（非 0 退出）一律带可指名的违规对象与修复方向。"""

from __future__ import annotations


class BuildError(Exception):
    """构建失败的基类。"""


class GuardViolationError(BuildError):
    """CI 云专属依赖守卫命中：构建拒绝并指名违规组件与修复方向。"""

    def __init__(self, violations: list[str]) -> None:
        self.violations = violations
        detail = "；".join(violations)
        super().__init__(f"cloud-locked dependency guard rejected the bundle: {detail}")


class ModelGateError(BuildError):
    """模型产物 lineage 审计门槛：审计记录缺失/未过 → 拒绝收录并指明缺失项。"""

    def __init__(self, rejected: list[dict]) -> None:
        self.rejected = rejected
        detail = "；".join(
            f"{r['name']}（缺失：{'/'.join(r['missing']) or r['reason']}）" for r in rejected
        )
        super().__init__(f"model lineage audit gate rejected model assets: {detail}")


class TierGateError(BuildError):
    """数据档门槛：文书包无法务过审记录 / 必需输入缺席 → 拒绝打包。"""


class VerifyError(BuildError):
    """manifest 校验失败（坏包语义的构建侧对应）。"""