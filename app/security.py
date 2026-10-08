"""不依赖外部认证服务的本地安全原语。

密码使用带随机盐的 PBKDF2；会话 Cookie 只保存用户 ID 和过期时间，并使用
HMAC 防篡改。该方案适合单机 MVP，生产环境仍应配合 HTTPS、密钥托管和限流。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import time

from app.config import settings


# 较高迭代数提高离线破解成本；每个密码另有独立随机盐。
PBKDF2_ITERATIONS = 240_000


def hash_password(password: str) -> str:
    """生成可自描述的 PBKDF2 密码摘要，不存储明文密码。"""

    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, PBKDF2_ITERATIONS)
    return f"pbkdf2_sha256${PBKDF2_ITERATIONS}${_encode(salt)}${_encode(digest)}"


def verify_password(password: str, encoded: str) -> bool:
    """恒定时间比较密码摘要，并将畸形摘要视为验证失败。"""

    try:
        algorithm, iterations, salt, expected = encoded.split("$", 3)
        if algorithm != "pbkdf2_sha256":
            return False
        digest = hashlib.pbkdf2_hmac(
            "sha256", password.encode(), _decode(salt), int(iterations)
        )
        return hmac.compare_digest(digest, _decode(expected))
    except (ValueError, TypeError):
        return False


def create_session_token(user_id: int) -> str:
    """创建包含绝对过期时间的签名会话 token。"""

    expires_at = int(time.time()) + settings.session_max_age
    payload = f"{user_id}:{expires_at}"
    signature = _sign(payload)
    return _encode(f"{payload}:{signature}".encode())


def read_session_token(token: str | None) -> int | None:
    """验证会话签名和过期时间，失败时统一返回 ``None``。"""

    if not token:
        return None
    try:
        payload = _decode(token).decode()
        user_id, expires_at, signature = payload.split(":", 2)
        if int(expires_at) < int(time.time()):
            return None
        if not hmac.compare_digest(signature, _sign(f"{user_id}:{expires_at}")):
            return None
        return int(user_id)
    except (ValueError, UnicodeDecodeError):
        return None


def csrf_token(user_id: int | None = None) -> str:
    """为登录页或指定用户生成与服务器密钥绑定的 CSRF token。"""

    subject = str(user_id) if user_id is not None else "login"
    return _sign(f"csrf:{subject}")


def validate_csrf(token: str, user_id: int | None = None) -> bool:
    """使用恒定时间比较校验表单 token，降低时序侧信道风险。"""

    return bool(token) and hmac.compare_digest(token, csrf_token(user_id))


def _sign(value: str) -> str:
    return hmac.new(settings.secret_key.encode(), value.encode(), hashlib.sha256).hexdigest()


def _encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode().rstrip("=")


def _decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + padding)
