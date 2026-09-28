"""消息渲染与发送。

* 模板渲染：把 ``{group_name}`` 之类的占位符替换成真实值，未知占位符原样保留。
* 私聊邀请人：同意通知 / 未满足条件通知。
* 群内欢迎：进入群聊后发送文本，可选附带一张图片（本地路径或 URL）。
"""

from __future__ import annotations

import os
import re
from typing import Any

from astrbot.api import logger
from astrbot.api.message_components import Image

from .config import PluginConfig
from .group_info import GroupInfo
from .onebot import RETCODE_ASYNC, call_action

_PLACEHOLDER_RE = re.compile(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}")


def render(template: str, variables: dict[str, Any]) -> str:
    """替换 ``{key}`` 占位符；未知占位符保持原样，避免误伤用户文案。"""

    def _sub(match: re.Match[str]) -> str:
        key = match.group(1)
        if key not in variables:
            return match.group(0)
        value = variables[key]
        return "" if value is None else str(value)

    return _PLACEHOLDER_RE.sub(_sub, template or "")


def build_variables(
    info: GroupInfo | None,
    *,
    inviter_id: int = 0,
    inviter_name: str = "",
    reasons: str = "",
    keywords: list[str] | None = None,
) -> dict[str, Any]:
    """构造模板变量表。"""
    return {
        "group_name": (info.name if info else "") or "未知群名",
        "group_id": info.group_id if info else "",
        "group_remark": (info.remark if info else "") or "",
        "group_memo": (info.memo if info else "") or "",
        "member_count": (info.member_count if info else 0),
        "max_member_count": (info.max_member_count if info else 0),
        "create_time": (info.created_at_text() if info else ""),
        "inviter": inviter_name or (str(inviter_id) if inviter_id else ""),
        "inviter_id": inviter_id,
        "keywords": "、".join(keywords or []),
        "reasons": reasons,
    }


def compose_unmet_message(cfg: PluginConfig, variables: dict[str, Any]) -> str:
    """拼装「未满足条件」通知：模板 + {reasons} + 自定义额外消息。"""
    template = cfg.unmet_notify_message or ""
    reasons = str(variables.get("reasons", ""))
    text = render(template, variables)
    # 用户没写 {reasons} 时，自动把原因列表追加到末尾
    if reasons and "{reasons}" not in template:
        text = f"{text}\n{reasons}" if text.strip() else reasons
    extra = cfg.unmet_notify_extra.strip()
    if extra:
        text = f"{text}\n{render(extra, variables)}" if text.strip() else extra
    return text.strip()


# ----------------------------------------------------------------------
# 发送
# ----------------------------------------------------------------------
def build_message_segments(text: str, image: str = "") -> list[dict[str, Any]]:
    """构造 OneBot v11 消息段数组（array 格式）。"""
    segments: list[dict[str, Any]] = []
    if text.strip():
        segments.append({"type": "text", "data": {"text": text}})
    if image:
        file_value = _resolve_image_file(image)
        if file_value:
            segments.append({"type": "image", "data": {"file": file_value}})
    return segments


def _resolve_image_file(image: str) -> str:
    """把配置里的图片值转换成协议端能接受的 ``file`` 字段。

    支持 http(s) URL、base64:// 以及本地文件路径；本地图片会被读成 base64，
    避免协议端与 AstrBot 容器路径不一致导致发图失败。
    """
    value = image.strip()
    if not value:
        return ""
    if value.startswith(("http://", "https://", "base64://")):
        return value
    if value.startswith("file:///"):
        value = value[8:]
    if os.path.isfile(value):
        return f"file:///{os.path.abspath(value)}"
    logger.warning(f"[group_invite] 图片配置无法识别或文件不存在，已跳过发送: {image}")
    return ""


async def send_private(
    client: Any,
    user_id: int,
    text: str,
    *,
    image: str = "",
) -> bool:
    """私聊发送。失败只记日志，不向上抛。"""
    if not user_id:
        return False
    segments = await _prepare(client, text, image)
    if not segments:
        return False
    result = await call_action(
        client, "send_private_msg", user_id=int(user_id), message=segments
    )
    if result.ok:
        return True
    logger.warning(f"[group_invite] 私聊 {user_id} 发送失败: {result.error}")
    return False


async def send_group(
    client: Any,
    group_id: int,
    text: str,
    *,
    image: str = "",
) -> bool:
    """群内发送。失败只记日志，不向上抛。"""
    if not group_id:
        return False
    segments = await _prepare(client, text, image)
    if not segments:
        return False
    result = await call_action(
        client, "send_group_msg", group_id=int(group_id), message=segments
    )
    if result.ok:
        return True
    logger.warning(f"[group_invite] 群 {group_id} 发送失败: {result.error}")
    return False


async def _prepare(client: Any, text: str, image: str) -> list[dict[str, Any]]:
    """构造消息段；图片优先走 base64，保证跨容器路径可用。"""
    segments = build_message_segments(text)
    if image:
        segment = await _image_segment(image)
        if segment:
            segments.append(segment)
    return segments


async def _image_segment(image: str) -> dict[str, Any] | None:
    value = image.strip()
    if not value:
        return None
    try:
        component = Image(file=value)
        base64_data = await component.convert_to_base64()
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"[group_invite] 图片处理失败，已跳过发送: {value} ({exc})")
        return None
    if not base64_data:
        return None
    return {"type": "image", "data": {"file": f"base64://{base64_data}"}}


def legacy_invite_flag(group_id: int, inviter_id: int = 0) -> str:
    """把群邀请转成 SnowLuma 认识的 legacy flag 形式 ``invite:<群号>:<uid>``。

    为什么需要这个：SnowLuma 处理群邀请审批时分三条路（``handleGroupAddRequest``）——

    * flag 以 ``slreq:`` 开头（canonical）时，**直接使用 flag 里的 eventType**，
      群邀请的 eventType 是 ``1``，而 QQ 服务器**只接受**「私聊群邀请卡片里的
      msgseq + eventType=2」，于是必然返回
      ``OIDB error 120161001 ... handle async message fail``；
    * flag 是 legacy 形式 ``invite:<群号>:<uid>`` 时，它会先去查
      该群**捕获到的卡片序列**，命中就用 ``eventType=2`` —— 这条才是服务器接受的路径。

    SnowLuma 源码注释原文：
        "a private "qun.invite" ark card ... carries the only sequence the server
         accepts when the bot later approves the invite via 0x10c8 — applied with
         eventType=2 / filtered=false. The MSF invite push (PkgType 87) never carries
         it. See issue #125."

    所以「退群后被重新邀请」这类只走 MSF push 的场景，用 canonical flag 审批必定失败。
    """
    return f"invite:{int(group_id)}:{int(inviter_id) or 0}"


async def approve_request(
    client: Any,
    flag: str,
    *,
    reason: str = "",
    group_id: int = 0,
    inviter_id: int = 0,
) -> bool:
    """同意群邀请（OneBot v11 ``set_group_add_request``）。"""
    return await _set_group_add_request(
        client,
        flag,
        approve=True,
        reason=reason,
        group_id=group_id,
        inviter_id=inviter_id,
    )


async def reject_request(client: Any, flag: str, *, reason: str = "") -> bool:
    """拒绝群邀请。"""
    return await _set_group_add_request(client, flag, approve=False, reason=reason)


async def leave_group(client: Any, group_id: int) -> bool:
    """主动退出群聊（用于「先进群后校验、不满足条件则退出」）。"""
    if not group_id:
        return False
    result = await call_action(client, "set_group_leave", group_id=int(group_id))
    if result.ok:
        return True
    logger.warning(f"[group_invite] 退出群 {group_id} 失败: {result.error}")
    return False


async def _set_group_add_request(
    client: Any,
    flag: str,
    *,
    approve: bool,
    reason: str = "",
    group_id: int = 0,
    inviter_id: int = 0,
) -> bool:
    if not flag:
        logger.warning("[group_invite] 群邀请缺少 flag，无法处理")
        return False

    def _kwargs(use_flag: str) -> dict[str, Any]:
        data: dict[str, Any] = {
            "flag": use_flag,
            "sub_type": "invite",
            "approve": approve,
        }
        if reason:
            data["reason"] = reason
        return data

    result = await call_action(client, "set_group_add_request", **_kwargs(flag))
    if result.ok:
        return True

    if result.retcode == RETCODE_ASYNC:
        # OneBot v11: retcode 1 表示协议端已接受异步处理，最终结果无法获知。
        # 此时重试没有意义，按「已提交」处理并如实记录。
        logger.warning(
            f"[group_invite] 群邀请已提交协议端异步处理，结果未知：{result.error}"
        )
        return True

    # SnowLuma 的 canonical flag（slreq: 开头）审群邀请必然失败：
    # 它会直接使用 flag 里的 eventType=1，而服务器只认「卡片序列 + eventType=2」。
    # 换成 legacy 形式重试一次，让它走查卡片序列的正确路径。
    if approve and group_id and flag.startswith("slreq:"):
        fallback = legacy_invite_flag(group_id, inviter_id)
        logger.info(
            f"[group_invite] 协议端拒绝了 canonical flag（{result.error}）；"
            f"改用 legacy 形式重试：{fallback}"
        )
        retry = await call_action(client, "set_group_add_request", **_kwargs(fallback))
        if retry.ok:
            logger.info(
                "[group_invite] legacy flag 重试成功（协议端走 eventType=2 的卡片路径）"
            )
            return True
        logger.error(
            f"[group_invite] legacy flag 重试同样失败: {retry.error}"
        )

    logger.error(
        f"[group_invite] 处理群邀请失败 (approve={approve}): {result.error}"
    )
    return False
