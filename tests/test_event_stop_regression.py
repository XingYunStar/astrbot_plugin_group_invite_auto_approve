"""回归测试：入群事件被其它插件 stop_event 截断时的两种保障。

背景（线上真实故障）：
    AstrBot 的 ``star_request`` 处理器循环是::

        for handler in activated_handlers:
            if event.is_stopped():
                break

    而本地已装的 ``astrbot_plugin_relationship`` 在「机器人被拉进群」时会判定为
    self-notice 并调用 ``event.stop_event()``。排在它后面的处理器会被直接跳过，
    导致本插件的入群欢迎与入群后复核完全失效。

本测试覆盖两条独立保障：
    1. 监听器带高优先级，保证排到最前（其它插件没机会先 stop）；
    2. 同意邀请后启动轮询兜底，完全不依赖事件管道。
"""

from __future__ import annotations

import asyncio
import inspect
import sys

sys.path.insert(0, "/AstrBot")
sys.path.insert(0, "/AstrBot/data/plugins")

from astrbot_plugin_group_invite_auto_approve import main as plugin_main  # noqa: E402
from astrbot_plugin_group_invite_auto_approve.core import handler as handler_mod  # noqa: E402

FAILED: list[str] = []


def check(label, actual, expected=True):
    ok = bool(actual) is bool(expected) if isinstance(expected, bool) else actual == expected
    print(f"{'PASS' if ok else 'FAIL'}  {label}: {actual!r}" + ("" if ok else f" != {expected!r}"))
    if not ok:
        FAILED.append(label)


print("=" * 70)
print("A) 监听器是否带高优先级")
check("模块导出了 HANDLER_PRIORITY", hasattr(plugin_main, "HANDLER_PRIORITY"))
priority = getattr(plugin_main, "HANDLER_PRIORITY", 0)
print("   HANDLER_PRIORITY =", priority)
check("优先级 > 0", priority > 0)

src = inspect.getsource(plugin_main.GroupInviteAutoApprovePlugin.on_group_control_event)
print("   装饰器源码:")
for line in src.splitlines()[:4]:
    print("     ", line)
check("装饰器里传入了 priority", "priority=HANDLER_PRIORITY" in src)

print()
print("=" * 70)
print("B) 高优先级确实能排到其它插件前面")
from astrbot.core.star.star_handler import (  # noqa: E402
    EventType,
    StarHandlerMetadata,
    star_handlers_registry,
)


class _Dummy:
    """模拟一个 priority=0 的第三方插件处理器（如 relationship 的 on_notice）。"""

    async def handler(self):
        return None


dummy = StarHandlerMetadata(
    event_type=EventType.AdapterMessageEvent,
    handler_full_name="dummy_plugin.on_notice",
    handler_name="on_notice",
    handler_module_path="astrbot_plugin_relationship.main",
    handler=_Dummy().handler,
    event_filters=[],
)
star_handlers_registry.append(dummy)
print("   dummy priority =", dummy.extras_configs.get("priority"))

order = [
    (h.handler_full_name, h.extras_configs.get("priority", 0))
    for h in star_handlers_registry._handlers
    if h.event_type == EventType.AdapterMessageEvent
]
print("   注册表顺序（前 8 个）:")
for name, prio in order[:8]:
    print(f"     [{prio:>4}] {name}")

mine = [
    i
    for i, (name, _p) in enumerate(order)
    if "group_invite_auto_approve" in name
]
dummy_idx = [i for i, (name, _p) in enumerate(order) if name == "dummy_plugin.on_notice"]
print("   本插件 handler 位置:", mine[:2], " dummy 位置:", dummy_idx)
if mine and dummy_idx:
    check("本插件的监听器排在普通插件前面", mine[0] < dummy_idx[0])

print()
print("=" * 70)
print("C) 轮询兜底：不依赖事件管道也能完成入群复核")

from astrbot_plugin_group_invite_auto_approve.core.config import PluginConfig  # noqa: E402
from astrbot_plugin_group_invite_auto_approve.core.history import InviteHistory  # noqa: E402
from astrbot_plugin_group_invite_auto_approve.core.handler import InviteHandler  # noqa: E402
from pathlib import Path  # noqa: E402
import tempfile  # noqa: E402

REAL = {
    "group_id": 555000111,
    "group_name": "随便聊聊群",
    "group_memo": "闲聊",
    "member_count": 12,
    "max_member_count": 200,
    "group_create_time": 1755432728,
    "group_level": 0,
    "group_all_shut": 0,
}


class FakeClient:
    """群列表一开始不含目标群，第 2 次查询后才出现（模拟入群延迟）。"""

    def __init__(self, appear_after=1, group_payload=None):
        self.group_payload = group_payload or REAL
        self.calls = []
        self.group_msgs = []
        self.private_msgs = []
        self.leaves = []
        self.appear_after = appear_after
        self.group_queries = 0

    async def call_action(self, api, **kwargs):
        self.calls.append((api, kwargs))
        if api == "get_group_list":
            self.group_queries += 1
            if self.group_queries >= self.appear_after:
                return [{"group_id": 555000111, "group_name": "随便聊聊群"}]
            return []
        if api in ("get_group_info", "get_group_info_ex"):
            return dict(self.group_payload)
        if api == "get_group_member_list":
            return []
        if api == "send_group_msg":
            self.group_msgs.append((kwargs["group_id"], kwargs["message"]))
            return {"message_id": 1}
        if api == "send_private_msg":
            self.private_msgs.append((kwargs["user_id"], kwargs["message"]))
            return {"message_id": 2}
        if api == "set_group_leave":
            self.leaves.append(kwargs["group_id"])
            return None
        from aiocqhttp.exceptions import ActionFailed

        exc = ActionFailed("failed")
        exc.result = {"status": "failed", "retcode": 1404, "wording": "unknown action"}
        raise exc


def make_handler(**overrides):
    from astrbot.core.config.astrbot_config import AstrBotConfig
    import json

    schema_path = Path(
        "/AstrBot/data/plugins/astrbot_plugin_group_invite_auto_approve/_conf_schema.json"
    )
    schema = json.loads(schema_path.read_text(encoding="utf-8-sig"))
    p = Path(tempfile.mkdtemp()) / "cfg.json"
    conf = AstrBotConfig(config_path=str(p), schema=schema)
    if overrides:
        data = json.loads(p.read_text(encoding="utf-8-sig"))
        for path, value in overrides.items():
            keys = path.split(".")
            cur = data
            for k in keys[:-1]:
                cur = cur[k]
            cur[keys[-1]] = value
        conf.update(data)
    return InviteHandler(PluginConfig(conf), InviteHistory(Path(tempfile.mkdtemp())))


async def scenario_verify_leave():
    """复核不通过 -> 轮询兜底也要退群。"""
    h = make_handler(
        **{
            "conditions.keywords": ["原神"],
            "conditions.verify_after_join": True,
            "conditions.verify_after_join_delay": 0,
        }
    )
    handler_mod.JOIN_WATCH_INTERVAL = 0.05
    handler_mod.JOIN_WATCH_INTERVAL_MAX = 0.1
    handler_mod.JOIN_WATCH_TIMEOUT = 5.0

    c = FakeClient(appear_after=2)
    h._mark_pending_welcome(555000111, 3275304521)
    h.spawn_join_watch(c, 555000111, 3275304521)
    await asyncio.sleep(0.4)

    check("轮询发现了入群", c.group_queries >= 2, True)
    check("轮询兜底 -> 复核不通过退群", c.leaves, [555000111])
    check("轮询兜底 -> 不发欢迎消息", c.group_msgs, [])
    check("轮询兜底 -> 通知了邀请人", len(c.private_msgs), 1)
    h.cancel_join_watches()


async def scenario_idempotent():
    """轮询与入群通知同时到达时只处理一次。"""
    h = make_handler(
        **{
            "conditions.keywords": ["原神"],
            "conditions.verify_after_join": True,
            "conditions.verify_after_join_delay": 0,
        }
    )
    handler_mod.JOIN_WATCH_INTERVAL = 0.05
    handler_mod.JOIN_WATCH_TIMEOUT = 5.0

    c = FakeClient(appear_after=1)
    h._mark_pending_welcome(555000111, 3275304521)
    h.spawn_join_watch(c, 555000111, 3275304521)
    # 同时模拟入群通知先到
    claimed, inviter = h._claim_join(555000111)
    check("入群通知抢到了处理权", claimed, True)
    check("带出了邀请人", inviter, 3275304521)
    again, _ = h._claim_join(555000111)
    check("第二次认领失败（已处理过）", again, False)
    await asyncio.sleep(0.35)
    check("轮询发现已被认领后不再重复处理", c.leaves, [])
    check("群列表查询次数有限（及时退出）", c.group_queries <= 3, True)
    h.cancel_join_watches()


async def scenario_pass():
    """复核通过 -> 留在群里并欢迎。"""
    h = make_handler(
        **{
            "conditions.verify_after_join": True,
            "conditions.verify_after_join_delay": 0,
        }
    )
    handler_mod.JOIN_WATCH_INTERVAL = 0.05
    handler_mod.JOIN_WATCH_TIMEOUT = 5.0

    c = FakeClient(appear_after=1, group_payload={**REAL, "group_name": "原神交流群"})
    h._mark_pending_welcome(555000111, 3275304521)
    h.spawn_join_watch(c, 555000111, 3275304521)
    await asyncio.sleep(0.4)

    check("复核通过 -> 不退群", c.leaves, [])
    check("复核通过 -> 发送欢迎消息", len(c.group_msgs), 1)
    h.cancel_join_watches()


async def scenario_no_join():
    """一直没入群 -> 超时后放弃，不误报。"""
    h = make_handler(**{"conditions.verify_after_join": True})
    handler_mod.JOIN_WATCH_INTERVAL = 0.05
    handler_mod.JOIN_WATCH_TIMEOUT = 0.3

    c = FakeClient(appear_after=999)
    h._mark_pending_welcome(555000111, 3275304521)
    h.spawn_join_watch(c, 555000111, 3275304521)
    await asyncio.sleep(0.6)

    check("超时后未退群", c.leaves, [])
    check("超时后未发消息", c.group_msgs + c.private_msgs, [])
    check("超时后清掉了待处理标记", h._is_pending(555000111), False)
    h.cancel_join_watches()


async def main_async():
    await scenario_verify_leave()
    print()
    await scenario_idempotent()
    print()
    await scenario_pass()
    print()
    await scenario_no_join()


asyncio.run(main_async())

print()
print("=" * 70)
if FAILED:
    print(f"❌ {len(FAILED)} 项失败：")
    for item in FAILED:
        print("   -", item)
    sys.exit(1)
print("✅ 事件截断回归测试全部通过")
