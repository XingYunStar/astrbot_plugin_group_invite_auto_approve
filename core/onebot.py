"""OneBot v11 动作调用封装。

``aiocqhttp`` 的 ``call_action`` 已经做过一层处理：

* ``status == "failed"`` 时抛出 ``ActionFailed``（里面带着完整的应答体）
* 成功时直接返回应答体里的 ``data`` 字段

所以插件里不能假设拿到的是 ``{"status": ..., "data": ...}`` 这种完整结构。
本模块把「成功 / 失败 / 协议端不支持」统一成一个 ``ActionResult``，
让上层不必到处写 try/except，也不会因为某个可选接口不存在就中断整个流程。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from astrbot.api import logger

try:  # aiocqhttp 在非 aiocqhttp 平台下可能不存在
    from aiocqhttp.exceptions import ActionFailed, ApiNotAvailable
except ImportError:  # pragma: no cover

    class ActionFailed(Exception):  # type: ignore[no-redef]
        result: dict[str, Any] | None = None

    class ApiNotAvailable(Exception):  # type: ignore[no-redef]
        pass


#: OneBot 通用错误码
RETCODE_UNSUPPORTED = 1404

#: OneBot v11 中 ``status == "async"`` / ``retcode == 1`` 表示
#: 「请求已提交异步处理，最终成功与否无法获知」，**不等于**「已处理过」。
RETCODE_ASYNC = 1


@dataclass
class ActionResult:
    """一次 OneBot 动作调用的结果。"""

    ok: bool
    data: Any = None
    error: str = ""
    retcode: int | None = None
    unsupported: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "error": self.error,
            "retcode": self.retcode,
            "unsupported": self.unsupported,
        }


def _error_from_payload(payload: Any, fallback: str) -> tuple[str, int | None, bool]:
    """从错误应答里解析出可读信息、retcode 与「不支持」标记。"""
    if not isinstance(payload, dict):
        return fallback, None, False
    retcode = payload.get("retcode")
    wording = (
        payload.get("wording")
        or payload.get("message")
        or payload.get("msg")
        or fallback
    )
    unsupported = retcode == RETCODE_UNSUPPORTED
    if unsupported:
        wording = f"{wording} (协议端不支持该接口)"
    return f"retcode={retcode} {wording}" if retcode is not None else str(wording), (
        retcode if isinstance(retcode, int) else None
    ), unsupported


async def call_action(client: Any, action: str, /, **params: Any) -> ActionResult:
    """调用协议端动作，永不抛异常。"""
    if client is None or not hasattr(client, "call_action"):
        return ActionResult(False, None, "协议端不支持 call_action", None, False)

    try:
        result = await client.call_action(action, **params)
    except ActionFailed as exc:
        payload = getattr(exc, "result", None)
        message, retcode, unsupported = _error_from_payload(payload, str(exc))
        return ActionResult(False, None, message, retcode, unsupported)
    except ApiNotAvailable:
        return ActionResult(False, None, "协议端未连接 (ApiNotAvailable)", None, False)
    except Exception as exc:  # noqa: BLE001 - 任何协议端异常都不应影响主流程
        return ActionResult(False, None, f"{type(exc).__name__}: {exc}", None, False)

    # 兜底：若某个实现直接返回了完整的 OneBot 应答体，这里替它解包一次
    if (
        isinstance(result, dict)
        and "status" in result
        and ("retcode" in result or "data" in result)
    ):
        if result.get("status") == "failed":
            message, retcode, unsupported = _error_from_payload(result, "调用失败")
            return ActionResult(False, None, message, retcode, unsupported)
        return ActionResult(True, result.get("data"), "", result.get("retcode"), False)

    return ActionResult(True, result, "", 0, False)


async def call_data(client: Any, action: str, /, **params: Any) -> Any:
    """调用动作并只返回 ``data``；失败返回 ``None``（可选接口的默认用法）。"""
    result = await call_action(client, action, **params)
    return result.data if result.ok else None


async def try_actions(
    client: Any,
    actions: tuple[str, ...],
    /,
    **params: Any,
) -> tuple[str, ActionResult] | tuple[None, None]:
    """依次尝试多个等价接口名，返回第一个成功的。

    不同协议端对同一能力可能用不同扩展名（例如群公告在 SnowLuma / NapCat 上叫
    ``_get_group_notice``），这里做兼容。
    """
    last: ActionResult | None = None
    for action in actions:
        result = await call_action(client, action, **params)
        if result.ok:
            return action, result
        last = result
        if not result.unsupported:
            # 接口存在但报错（例如机器人不在群里），换名字也没用
            break
    if last is not None and last.error:
        logger.debug(f"[group_invite] {actions[0]} 调用失败: {last.error}")
    return None, None
