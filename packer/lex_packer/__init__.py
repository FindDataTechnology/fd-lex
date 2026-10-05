"""lex-packer：识律交付包（Distribution）构建器。

spec ``lex-private-distribution``：
- 可私有化不变量 + CI 云专属依赖守卫（guard.py）
- 数据三档分层（tiers.py）
- bundle 版本化 + 组件 digest 锁定 + provenance（manifest.py）
- 模型产物 lineage 审计出货门槛（model_gate.py，lex-model-assets）

CLI：``python -m lex_packer`` / 安装后 ``lex-dist``。
"""

from .errors import BuildError, GuardViolationError, TierGateError, VerifyError
from .version import BUNDLE_PREFIX, parse_bundle_version, bundle_id

__all__ = [
    "BuildError",
    "GuardViolationError",
    "TierGateError",
    "VerifyError",
    "BUNDLE_PREFIX",
    "parse_bundle_version",
    "bundle_id",
]

__version__ = "0.1.0"