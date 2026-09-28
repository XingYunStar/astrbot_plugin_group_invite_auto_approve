"""群邀请自动处理插件的内部模块。"""

from .config import PluginConfig
from .decision import Decision, decide
from .group_info import (
    GroupInfo,
    InviteContext,
    fetch_group_info,
    fetch_invite_context,
    log_group_info,
)
from .handler import InviteHandler
from .history import InviteHistory
from .notify import (
    approve_request,
    build_variables,
    compose_unmet_message,
    reject_request,
    render,
    send_group,
    send_private,
)
from .onebot import ActionResult, call_action, call_data, try_actions
from .page_api import PageController

__all__ = [
    "ActionResult",
    "Decision",
    "GroupInfo",
    "InviteContext",
    "InviteHandler",
    "InviteHistory",
    "PageController",
    "PluginConfig",
    "approve_request",
    "build_variables",
    "call_action",
    "call_data",
    "compose_unmet_message",
    "decide",
    "fetch_group_info",
    "fetch_invite_context",
    "log_group_info",
    "reject_request",
    "render",
    "send_group",
    "send_private",
    "try_actions",
]
