"""平台断言校验（change converge-line-identity-and-keys 2.1 / 2.2）。

识律线接受两类身份：
  - 人：Logto ID token（``console_api/auth.py``，浏览器 PKCE 铸造）；
  - 代做：壹座服务主体签的成员级断言（本模块）。

本模块只做**校验**，且刻意离线：公钥来自 JWKS 或本地 PEM，不回调平台。
兑换端点的语义是"这份断言是谁、能做什么、到什么时候"，不是另铸一枚本线 token——
另铸一枚会立刻长出一条自己的续期链，正是本 change 要消掉的东西。

三档 JIT（本模块只管判定，建档/授权行由调用方落地）：
  off          未登记用户一律拒（默认）
  first-use    首次由用户本人确认后建立绑定
  consent      除绑定外还需一条可审计的授权行

铁律：**身份永不自动创建**。``jit_policy=off`` 时连"要不要建"这个问题都不问。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Protocol

__all__ = [
    "AssertionRejected",
    "AssertionIdentity",
    "JitPolicy",
    "ReplayStore",
    "verify_assertion",
]

#: exp 与 iat 之差的上限（spec: 不超过 5 分钟）。
MAX_TTL_SECONDS = 300


class JitPolicy:
    OFF = "off"
    FIRST_USE = "first-use"
    CONSENT = "consent"

    ALL = (OFF, FIRST_USE, CONSENT)


class AssertionRejected(Exception):
    """校验失败。``reason`` 是稳定码，供调用方与测试断言（不要断言文案）。"""

    #: 稳定原因码 → HTTP 状态由路由层决定
    NOT_CONFIGURED = "not_configured"
    TOKEN_MALFORMED = "token_malformed"
    BAD_SIGNATURE = "bad_signature"
    WRONG_ISSUER = "wrong_issuer"
    WRONG_AUDIENCE = "wrong_audience"
    EXPIRED = "expired"
    TTL_TOO_LONG = "ttl_too_long"
    MISSING_CLAIM = "missing_claim"
    REPLAYED = "replayed"
    JIT_DISABLED = "jit_disabled"
    UNKNOWN_IDENTITY = "unknown_identity"
    CONSENT_REQUIRED = "consent_required"

    def __init__(self, reason: str, detail: str = "") -> None:
        super().__init__(f"{reason}: {detail}" if detail else reason)
        self.reason = reason
        self.detail = detail


@dataclass(frozen=True)
class AssertionIdentity:
    """已校验的断言内容（只含路由层要用的字段）。"""

    sub: str
    scope: tuple[str, ...]
    issuer: str
    audience: str
    expires_at: int
    jti: str


class ReplayStore(Protocol):
    """jti 一次性台账。``claim(jti, expires_at)`` 返回 True 表示本次首次见到。"""

    def claim(self, jti: str, expires_at: int) -> bool: ...


@dataclass(frozen=True)
class AssertionSettings:
    issuer: str
    audience: str
    jwks_uri: str = ""
    public_key_pem: str = ""
    algorithms: tuple[str, ...] = ("ES384", "RS256", "EdDSA")
    max_ttl_seconds: int = MAX_TTL_SECONDS
    jit_policy: str = JitPolicy.OFF

    @property
    def configured(self) -> bool:
        return bool(self.issuer and self.audience and (self.jwks_uri or self.public_key_pem))


def _decode(
    token: str,
    settings: AssertionSettings,
    *,
    jwks_client_factory: Callable[[str], Any] | None,
    now: int | None,
) -> dict[str, Any]:
    try:
        import jwt as pyjwt
    except ImportError as exc:  # pragma: no cover - 依赖缺失属部署错误
        raise AssertionRejected(AssertionRejected.NOT_CONFIGURED, "PyJWT not installed") from exc

    key: Any
    if settings.public_key_pem:
        key = settings.public_key_pem
    elif settings.jwks_uri and jwks_client_factory is not None:
        try:
            key = jwks_client_factory(settings.jwks_uri).get_signing_key_from_jwt(token).key
        except Exception as exc:
            raise AssertionRejected(
                AssertionRejected.BAD_SIGNATURE, f"jwks lookup failed: {type(exc).__name__}"
            ) from exc
    else:
        raise AssertionRejected(AssertionRejected.NOT_CONFIGURED, "no verification key source")

    try:
        return pyjwt.decode(
            token,
            key,
            algorithms=list(settings.algorithms),
            issuer=settings.issuer,
            audience=settings.audience,
            options={"verify_exp": True, "verify_aud": True, "verify_iss": True},
        )
    except pyjwt.ExpiredSignatureError as exc:
        raise AssertionRejected(AssertionRejected.EXPIRED) from exc
    except pyjwt.InvalidAudienceError as exc:
        raise AssertionRejected(
            AssertionRejected.WRONG_AUDIENCE, f"expected {settings.audience}"
        ) from exc
    except pyjwt.InvalidIssuerError as exc:
        raise AssertionRejected(AssertionRejected.WRONG_ISSUER, f"expected {settings.issuer}") from exc
    except pyjwt.DecodeError as exc:
        raise AssertionRejected(AssertionRejected.TOKEN_MALFORMED) from exc
    except Exception as exc:  # 签名不符、算法不符等
        raise AssertionRejected(
            AssertionRejected.BAD_SIGNATURE, type(exc).__name__
        ) from exc


def verify_assertion(
    token: str,
    *,
    settings: AssertionSettings,
    replay: ReplayStore | None = None,
    jwks_client_factory: Callable[[str], Any] | None = None,
    now: int | None = None,
) -> AssertionIdentity:
    """校验一份成员级断言。失败一律抛 :class:`AssertionRejected`。

    顺序有意如此：先验签（不可信输入最贵的一步最后做便宜判定会浪费），
    再判 aud/iss，再判有效期窗口，最后判 jti 一次性——重放检查会写台账，
    不能让它被一个本该被拒的断言触发。
    """
    if not settings.configured:
        raise AssertionRejected(AssertionRejected.NOT_CONFIGURED, "issuer/audience/key incomplete")
    if not isinstance(token, str) or not token.strip():
        raise AssertionRejected(AssertionRejected.TOKEN_MALFORMED, "empty token")

    claims = _decode(token, settings, jwks_client_factory=jwks_client_factory, now=now)

    sub = claims.get("sub")
    if not isinstance(sub, str) or not sub.strip():
        raise AssertionRejected(AssertionRejected.MISSING_CLAIM, "sub")
    jti = claims.get("jti")
    if not isinstance(jti, str) or not jti.strip():
        raise AssertionRejected(AssertionRejected.MISSING_CLAIM, "jti")

    exp = claims.get("exp")
    iat = claims.get("iat")
    if not isinstance(exp, int):
        raise AssertionRejected(AssertionRejected.MISSING_CLAIM, "exp")
    if isinstance(iat, int) and exp - iat > settings.max_ttl_seconds:
        # PyJWT 只管"有没有过期"，不管"给的时间太长"——后者才是断言被当成
        # 长期凭据的入口。
        raise AssertionRejected(
            AssertionRejected.TTL_TOO_LONG, f"{exp - iat}s > {settings.max_ttl_seconds}s"
        )

    if settings.jit_policy == JitPolicy.OFF:
        # off 档：不接受任何平台代做（连"这个人登记过没有"都不问）。
        raise AssertionRejected(AssertionRejected.JIT_DISABLED, "jit_policy=off")

    if replay is not None and not replay.claim(jti, exp):
        raise AssertionRejected(AssertionRejected.REPLAYED, jti)

    scope_value = claims.get("scope") or ""
    scope = tuple(str(scope_value).split()) if scope_value else ()
    return AssertionIdentity(
        sub=sub.strip(),
        scope=scope,
        issuer=str(claims.get("iss") or ""),
        audience=str(claims.get("aud") or ""),
        expires_at=exp,
        jti=jti,
    )
