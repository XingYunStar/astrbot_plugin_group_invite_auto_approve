"""用 AstrBot 真实的事件类复现「入群通知」，验证本插件的过滤器与处理器。"""

from __future__ import annotations

import asyncio
import sys

sys.path.insert(0, "/AstrBot")
sys.path.insert(0, "/AstrBot/data/plugins")

from astrbot.api.platform import MessageType  # noqa: E402
from astrbot.core.config import AstrBotConfig  # noqa: E402
from astrbot.core.platform.astr_message_event import MessageSesion  # noqa: E402
from astrbot.core.platform.sources.aiocqhttp.aiocqhttp_message_event import (  # noqa: E402
    AiocqhttpMessageEvent,
)
from astrbot.core.star.filter.platform_adapter_type import (  # noqa: E402
    PlatformAdapterType,
    PlatformAdapterTypeFilter,
)

from astrbot_plugin_group_invite_auto_approve.main import (  # noqa: E402
    GroupControlEventFilter,
)

# SnowLuma 实际发出的入群通知负载（来自其 convertGroupMemberJoin）
NOTICE = {
    "time": 1790000000,
    "self_id": 2020987291,
    "post_type": "notice",
    "notice_type": "group_increase",
    "sub_type": "invite",
    "group_id": 970817189,
    "operator_id": 3275304521,
    "user_id": 2020987291,
}

# 对照：群邀请请求
REQUEST = {
    "time": 1790000000,
    "self_id": 2020987291,
    "post_type": "request",
    "request_type": "group",
    "sub_type": "invite",
    "group_id": 970817189,
    "user_id": 3275304521,
    "comment": "",
    "flag": "slreq:1:123:970817189:1:0",
}


class FakeObj:
    def __init__(self, raw):
        self.raw_message = raw
        self.type = MessageType.GROUP_MESSAGE
        self.group_id = str(raw.get("group_id", ""))
        self.session_id = str(raw.get("group_id", ""))
        self.self_id = str(raw.get("self_id", ""))
        self.message_str = ""
        self.message = []
        self.sender = type("S", (), {"user_id": str(raw.get("user_id", "")), "nickname": str(raw.get("user_id", ""))})()
        self.message_id = "x"
        self.timestamp = 0


def make_event(raw):
    meta = type("M", (), {"id": "FireflyFreeBot_5", "name": "aiocqhttp"})()
    return AiocqhttpMessageEvent(
        message_str="",
        message_obj=FakeObj(raw),
        platform_meta=meta,
        session_id=str(raw.get("group_id", "")),
        bot=None,
    )


FAILED = []


def check(label, actual, expected=True):
    ok = bool(actual) is bool(expected) if isinstance(expected, bool) else actual == expected
    print(f"{'PASS' if ok else 'FAIL'}  {label}: {actual!r}" + ("" if ok else f" != {expected!r}"))
    if not ok:
        FAILED.append(label)


print("=" * 72)
print("A) raw_message 的实际类型")
ev_n = make_event(NOTICE)
raw = ev_n.message_obj.raw_message
print("   type =", type(raw).__name__)
check("是 dict 子类", isinstance(raw, dict))
check("post_type", raw.get("post_type"), "notice")
check("notice_type", raw.get("notice_type"), "group_increase")

print()
print("=" * 72)
print("B) 本插件的过滤器是否放行入群通知")
cfg = AstrBotConfig(config_path="/tmp/no_such_config_xyz.json", default_config={})
f = GroupControlEventFilter(True)
print("   GroupControlEventFilter(通知) ->", f.filter(ev_n, cfg))
print("   GroupControlEventFilter(请求) ->", f.filter(make_event(REQUEST), cfg))
check("放行入群通知", f.filter(ev_n, cfg), True)
check("放行群邀请请求", f.filter(make_event(REQUEST), cfg), True)
check("不放行好友请求", f.filter(make_event({**REQUEST, "request_type": "friend"}), cfg), False)
check("不放行退群通知", f.filter(make_event({**NOTICE, "notice_type": "group_decrease"}), cfg), False)

pf = PlatformAdapterTypeFilter(PlatformAdapterType.AIOCQHTTP)
print("   PlatformAdapterTypeFilter ->", pf.filter(ev_n, cfg))
check("平台过滤器放行", pf.filter(ev_n, cfg), True)

print()
print("=" * 72)
print("C) 处理器的分发与早退条件")
check("get_self_id()", ev_n.get_self_id(), "2020987291")
from astrbot_plugin_group_invite_auto_approve.core.handler import _to_int  # noqa: E402

check("user_id == self_id", _to_int(raw.get("user_id")) == _to_int(ev_n.get_self_id()), True)
check("group_id", _to_int(raw.get("group_id")), 970817189)

print()
print("=" * 72)
print("D) 真正跑一遍 dispatch（用假 client，只看是否进入通知分支）")
from astrbot_plugin_group_invite_auto_approve.core.config import PluginConfig  # noqa: E402
from astrbot_plugin_group_invite_auto_approve.core.history import InviteHistory  # noqa: E402
from astrbot_plugin_group_invite_auto_approve.core.handler import InviteHandler  # noqa: E402
from pathlib import Path  # noqa: E402
import tempfile  # noqa: E402

conf = AstrBotConfig(config_path="/tmp/no_such_config_xyz.json", default_config={})
h = InviteHandler(PluginConfig(conf), InviteHistory(Path(tempfile.mkdtemp())))
entered = {"notice": False, "request": False}

orig_notice = h.on_group_increase_notice
orig_request = h.on_request_event


async def spy_notice(event):
    entered["notice"] = True
    return await orig_notice(event)


async def spy_request(event):
    entered["request"] = True
    return await orig_request(event)


h.on_group_increase_notice = spy_notice
h.on_request_event = spy_request

asyncio.run(h.dispatch(make_event(NOTICE)))
asyncio.run(h.dispatch(make_event(REQUEST)))
check("dispatch 进入了通知分支", entered["notice"], True)
check("dispatch 进入了请求分支", entered["request"], True)

print()
print("=" * 72)
if FAILED:
    print(f"❌ {len(FAILED)} 项失败：")
    for x in FAILED:
        print("   -", x)
    sys.exit(1)
print("✅ 过滤器与分发全部正常 —— 问题不在插件这一侧")
