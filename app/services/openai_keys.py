"""多 OpenAI API Key：轮询起始 + 失败时换 Key；无 Key / 全部失败时抛明确异常。"""
from __future__ import annotations

import re
import threading
from typing import Callable, TypeVar

from app.config import settings

T = TypeVar("T")


class OpenAINoKeysError(RuntimeError):
    """未配置任何可用 Key。"""


class OpenAIAllKeysFailedError(RuntimeError):
    """已配置的 Key 均不可用或本次请求全部失败。"""


_rr_lock = threading.Lock()
_rr_start = 0


def list_openai_keys() -> list[str]:
    """合并 OPENAI_API_KEYS（逗号/换行/空格分隔）与 OPENAI_API_KEY（单 Key，可与多 Key 同时使用）。"""
    keys: list[str] = []
    bulk = (getattr(settings, "openai_api_keys", None) or "").strip()
    if bulk:
        for part in re.split(r"[\s,;|]+", bulk):
            p = part.strip()
            if p:
                keys.append(p)
    single = (settings.openai_api_key or "").strip()
    if single and single not in keys:
        keys.insert(0, single)
    # 去重且保序
    seen: set[str] = set()
    out: list[str] = []
    for k in keys:
        if k not in seen:
            seen.add(k)
            out.append(k)
    return out


def _billing_or_quota_related(exc: BaseException) -> bool:
    """
    同一错误在换账号（另一 API Key）后可能恢复，例如 DALL·E 返回：
    400 billing_hard_limit_reached / insufficient_quota。
    """
    s = str(exc).lower()
    if any(
        x in s
        for x in (
            "billing_hard_limit",
            "insufficient_quota",
            "billing_not_active",
            "your account is not active",
            "image_generation_user_error",
        )
    ):
        return True
    body = getattr(exc, "body", None)
    if isinstance(body, dict):
        err = body.get("error")
        if isinstance(err, dict):
            code = str(err.get("code") or "").lower()
            typ = str(err.get("type") or "").lower()
            if code in (
                "billing_hard_limit_reached",
                "insufficient_quota",
                "billing_not_active",
            ):
                return True
            if typ == "image_generation_user_error" and "billing" in code:
                return True
    code_attr = getattr(exc, "code", None)
    if isinstance(code_attr, str) and code_attr.lower() in (
        "billing_hard_limit_reached",
        "insufficient_quota",
    ):
        return True
    return False


def round_robin_key_order() -> list[str]:
    """
    返回本轮尝试顺序：起始 Key 按请求轮询递增，失败时顺延列表中后续 Key。
    """
    keys = list_openai_keys()
    if not keys:
        return []
    global _rr_start
    with _rr_lock:
        start = _rr_start % len(keys)
        _rr_start += 1
    return [keys[(start + i) % len(keys)] for i in range(len(keys))]


def is_retryable_key_error(exc: BaseException) -> bool:
    """401/403/429、以及计费/硬顶额等 400 视为可换 Key 重试。"""
    try:
        import openai

        if isinstance(exc, (openai.AuthenticationError, openai.PermissionDeniedError, openai.RateLimitError)):
            return True
        if isinstance(exc, openai.APIStatusError) and exc.status_code in (401, 403, 429):
            return True
        if isinstance(exc, openai.BadRequestError) and _billing_or_quota_related(exc):
            return True
        if isinstance(exc, openai.APIStatusError) and exc.status_code == 400 and _billing_or_quota_related(exc):
            return True
    except ImportError:
        pass

    cause = getattr(exc, "__cause__", None)
    if cause is not None and cause is not exc:
        return is_retryable_key_error(cause)

    inner = getattr(exc, "error", None)
    if inner is not None and inner is not exc:
        return is_retryable_key_error(inner)

    resp = getattr(exc, "response", None)
    if resp is not None:
        code = getattr(resp, "status_code", None)
        if code in (401, 403, 429):
            return True

    msg = str(exc).lower()
    if "401" in msg or "403" in msg or "429" in msg:
        if "invalid" in msg or "authentication" in msg or "permission" in msg or "rate" in msg:
            return True
    if _billing_or_quota_related(exc):
        return True
    return False


def run_with_key_rotation(fn: Callable[[str], T], *, what: str = "OpenAI") -> T:
    """
    同步：按轮询顺序依次用 api_key 调用 fn(key)，遇可重试错误则换 Key。
    """
    order = round_robin_key_order()
    if not order:
        raise OpenAINoKeysError("未配置 OPENAI_API_KEY 或 OPENAI_API_KEYS，无法调用大模型。")
    last: BaseException | None = None
    for api_key in order:
        try:
            return fn(api_key)
        except Exception as e:
            last = e
            if is_retryable_key_error(e):
                continue
            raise
    raise OpenAIAllKeysFailedError(f"{what}：全部 API Key 均失败（{last!s}）") from last


async def async_run_with_key_rotation(afn: Callable, *, what: str = "OpenAI"):
    """
    异步：afn 须为 async (api_key: str) -> T。
    """
    order = round_robin_key_order()
    if not order:
        raise OpenAINoKeysError("未配置 OPENAI_API_KEY 或 OPENAI_API_KEYS，无法调用 OpenAI 接口。")
    last: BaseException | None = None
    for api_key in order:
        try:
            return await afn(api_key)
        except Exception as e:
            last = e
            if is_retryable_key_error(e):
                continue
            raise
    raise OpenAIAllKeysFailedError(f"{what}：全部 API Key 均失败（{last!s}）") from last
