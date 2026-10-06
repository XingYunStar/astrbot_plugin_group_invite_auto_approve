"""Integration test inside the AstrBot container: real AstrBotConfig + fake OneBot client."""

from __future__ import annotations

import asyncio
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, "/AstrBot")
sys.path.insert(0, "/AstrBot/data/plugins")

from astrbot.core.config.astrbot_config import AstrBotConfig  # noqa: E402

from aiocqhttp.exceptions import ActionFailed  # noqa: E402

from astrbot_plugin_group_invite_auto_approve.core.config import (  # noqa: E402
    PluginConfig,
)
from astrbot_plugin_group_invite_auto_approve.core.handler import InviteHandler  # noqa: E402
from astrbot_plugin_group_invite_auto_approve.core.history import InviteHistory  # noqa: E402

PLUGIN_DIR = Path("/AstrBot/data/plugins/astrbot_plugin_group_invite_auto_approve")
SCHEMA = json.loads((PLUGIN_DIR / "_conf_schema.json").read_text(encoding="utf-8-sig"))

FAILED: list[str] = []


def check(label, actual, expected):
    ok = actual == expected
    print(f"{'PASS' if ok else 'FAIL'}  {label}: {actual!r}" + ("" if ok else f" != {expected!r}"))
    if not ok:
        FAILED.append(label)


# ------------------------------------------------------------------ schema load
print("=" * 70)
print("A) 真实 AstrBotConfig 能否按 _conf_schema.json 生成默认配置")
tmp = Path(tempfile.mkdtemp())
cfg_path = tmp / "astrbot_plugin_group_invite_auto_approve_config.json"
raw = AstrBotConfig(config_path=str(cfg_path), schema=SCHEMA)
print("   生成文件:", cfg_path.exists())
print("   内容:", json.dumps(json.loads(cfg_path.read_text(encoding="utf-8-sig")), ensure_ascii=False))
cfg = PluginConfig(raw)
check("schema 解析成功", cfg.auto_approve_all, False)
check("keywords 默认值来自 schema", cfg.keywords, ["原神", "星穹铁道", "绝区零"])
check("min/max 默认值", (cfg.min_member_count, cfg.max_member_count), (0, 3000))


# ------------------------------------------------------------------ fakes
class FakeEvent:
    def __init__(self, raw, bot, self_id="2020987291"):
        self.message_obj = type("MO", (), {"raw_message": raw})()
        self.bot = bot
        self._self_id = self_id

    def get_self_id(self):
        return self._self_id


class FakeClient:
    """精确模拟 aiocqhttp 的 call_action 语义：

    成功时直接返回应答体的 ``data`` 字段；``status == "failed"`` 时抛出
    ``ActionFailed``（异常对象上挂着完整应答体）。
    """

    def __init__(
        self,
        group_payload=None,
        notice=None,
        member_list=None,
        fail_notice=True,
        inbox=None,
        fail_inbox=False,
        group_list=None,
        approve_fails=False,
        group_detail=None,
    ):
        self.calls: list[tuple[str, dict]] = []
        self.private_msgs: list[tuple[int, list]] = []
        self.group_msgs: list[tuple[int, list]] = []
        self.group_payload = group_payload or {}
        self.notice = notice
        self.member_list = member_list or []
        self.fail_notice = fail_notice
        self.inbox = inbox or []
        self.fail_inbox = fail_inbox
        self.group_list = group_list or []
        self.approve_fails = approve_fails
        self.canonical_flag_fails = False
        self.leaves: list[int] = []
        # 群详情接口：模拟 NapCat 的 get_group_detail_info。
        # None 表示协议端不支持（会返回 retcode 1404）。
        self.group_detail = group_detail

    @staticmethod
    def _failed(retcode, wording):
        exc = ActionFailed(f"Action failed: {wording}")
        exc.result = {
            "status": "failed",
            "retcode": retcode,
            "data": None,
            "wording": wording,
        }
        raise exc

    async def call_action(self, api, **kwargs):
        self.calls.append((api, kwargs))
        if api in ("get_group_info", "get_group_info_ex"):
            return dict(self.group_payload)
        if api == "get_group_detail_info":
            if self.group_detail is None:
                self._failed(1404, "unknown action")
            return dict(self.group_detail)
        if api in ("_get_group_notice", "get_group_notice"):
            if self.fail_notice:
                self._failed(1404, "unknown action")
            return self.notice or []
        if api == "get_group_member_list":
            return self.member_list
        if api == "get_group_system_msg":
            if self.fail_inbox:
                self._failed(1404, "unknown action")
            return self.inbox
        if api == "get_group_ignored_notifies":
            self._failed(1404, "unknown action")
        if api == "get_stranger_info":
            return {"user_id": kwargs.get("user_id"), "nickname": f"陌生人{kwargs.get('user_id')}"}
        if api in ("get_group_at_all_remain", "get_group_honor_info"):
            self._failed(1404, "unknown action")
        if api == "set_group_add_request":
            flag = kwargs.get("flag", "")
            if self.canonical_flag_fails and flag.startswith("slreq:"):
                # 复现 SnowLuma 的真实行为：canonical flag -> eventType=1 -> 服务器拒绝
                self._failed(100, "OIDB error 120161001 on 0x10c8_1: handle async message fail")
            if self.approve_fails:
                self._failed(100, "OIDB error 120161001 on 0x10c8_1: handle async message fail")
            return None
        if api == "get_group_list":
            return self.group_list
        if api == "set_group_leave":
            self.leaves.append(kwargs.get("group_id"))
            return None
        if api == "send_private_msg":
            self.private_msgs.append((kwargs["user_id"], kwargs["message"]))
            return {"message_id": 1}
        if api == "send_group_msg":
            self.group_msgs.append((kwargs["group_id"], kwargs["message"]))
            return {"message_id": 2}
        self._failed(1404, "unknown action")


def make_plugin_config(**overrides):
    """用全新临时文件构造 PluginConfig，取到的是 _conf_schema.json 的纯默认值。"""
    p = Path(tempfile.mkdtemp()) / "fresh_config.json"
    conf = AstrBotConfig(config_path=str(p), schema=SCHEMA)
    if overrides:
        data = json.loads(p.read_text(encoding="utf-8-sig"))
        for path, value in overrides.items():
            keys = path.split(".")
            cur = data
            for k in keys[:-1]:
                cur = cur[k]
            cur[keys[-1]] = value
        conf.update(data)
    return PluginConfig(conf)


def make_handler(**overrides):
    data = json.loads(cfg_path.read_text(encoding="utf-8-sig"))
    for path, value in overrides.items():
        keys = path.split(".")
        cur = data
        for k in keys[:-1]:
            cur = cur[k]
        cur[keys[-1]] = value
    conf = AstrBotConfig(config_path=str(cfg_path), schema=SCHEMA)
    conf.update(data)
    history = InviteHistory(Path(tempfile.mkdtemp()))
    return InviteHandler(PluginConfig(conf), history), history


INVITE_EVENT = {
    "post_type": "request",
    "request_type": "group",
    "sub_type": "invite",
    "group_id": 970817189,
    "user_id": 3275304521,
    "comment": "求求了",
    "flag": "slreq:1:1790544757556637:970817189:1:0",
    "invited_id": 2020987291,
    "self_id": 2020987291,
}

GROUP_PAYLOAD = {
    "group_id": 970817189,
    "group_name": "原神交流群",
    "group_remark": "",
    "member_count": 456,
    "max_member_count": 1000,
    "group_create_time": 1755432728,
    "group_level": 3,
    "group_memo": "欢迎来到提瓦特，讨论原神与星穹铁道",
    "group_all_shut": 0,
}


# ------------------------------------------------------------------ 场景 1
print()
print("=" * 70)
print("B) 场景1：满足条件 -> 自动同意 + 通知邀请人")
handler, history = make_handler()
client = FakeClient(group_payload=GROUP_PAYLOAD)

asyncio.run(handler.dispatch(FakeEvent(INVITE_EVENT, client)))

approve_calls = [c for c in client.calls if c[0] == "set_group_add_request"]
check("调用了 set_group_add_request", len(approve_calls), 1)
check("approve=True", approve_calls[0][1]["approve"], True)
check("sub_type=invite", approve_calls[0][1]["sub_type"], "invite")
check("flag 透传", approve_calls[0][1]["flag"], INVITE_EVENT["flag"])
check("向邀请人发送了私聊", client.private_msgs, [(3275304521, [{"type": "text", "data": {"text": "已同意进群~"}}])])
stats = history.stats()
check("统计·邀请 +1", stats["invites"], 1)
check("统计·同意 +1", stats["approved"], 1)

# ------------------------------------------------------------------ 场景 2
print()
print("=" * 70)
print("C) 场景2：关键词不满足 -> 不处理 + 通知邀请人")
handler2, history2 = make_handler(**{"conditions.keywords": ["星穹铁道"]})
client2 = FakeClient(group_payload={**GROUP_PAYLOAD, "group_name": "闲聊群", "group_memo": "随便聊聊"})

asyncio.run(handler2.dispatch(FakeEvent(INVITE_EVENT, client2)))

check("未满足 -> 不调用 set_group_add_request", [c for c in client2.calls if c[0] == "set_group_add_request"], [])
check("未满足 -> 发送了说明私聊", len(client2.private_msgs), 1)
if client2.private_msgs:
    print("   ---- 私聊内容 ----")
    print("   " + client2.private_msgs[0][1][0]["data"]["text"].replace("\n", "\n   "))
check("统计·未处理 +1", history2.stats()["ignored"], 1)

# ------------------------------------------------------------------ 场景 3
print()
print("=" * 70)
print("D) 场景3：未满足 + 开启自动拒绝 -> 拒绝邀请")
handler3, history3 = make_handler(
    **{"conditions.keywords": ["星穹铁道"], "notify.unmet_auto_reject": True}
)
client3 = FakeClient(group_payload={**GROUP_PAYLOAD, "group_name": "闲聊群", "group_memo": "随便聊聊"})

asyncio.run(handler3.dispatch(FakeEvent(INVITE_EVENT, client3)))

rej = [c for c in client3.calls if c[0] == "set_group_add_request"]
check("调用了 set_group_add_request", len(rej), 1)
check("approve=False", rej[0][1]["approve"], False)
check("带上了拒绝理由", bool(rej[0][1].get("reason")), True)
check("统计·拒绝 +1", history3.stats()["rejected"], 1)

# ------------------------------------------------------------------ 场景 4
print()
print("=" * 70)
print("E) 场景4：总开关开启 -> 无视条件直接同意")
handler4, _ = make_handler(
    **{
        "auto_approve_all": True,
        "conditions.keywords": ["绝对不存在的关键词"],
        "conditions.member_count_enable": True,
        "conditions.min_member_count": 99999,
    }
)
client4 = FakeClient(group_payload=GROUP_PAYLOAD)
asyncio.run(handler4.dispatch(FakeEvent(INVITE_EVENT, client4)))
check("总开关 -> approve=True", [c for c in client4.calls if c[0] == "set_group_add_request"][0][1]["approve"], True)

# ------------------------------------------------------------------ 场景 5
print()
print("=" * 70)
print("F) 场景5：入群通知 -> 发送欢迎消息 + 图片")
handler5, history5 = make_handler(**{"welcome.image": "https://example.com/x.png"})
client5 = FakeClient(group_payload=GROUP_PAYLOAD, member_list=[
    {"user_id": 111, "role": "owner", "nickname": "群主", "card": ""},
    {"user_id": 222, "role": "admin", "nickname": "管理员", "card": "小管"},
])
# 先标记刚同意的邀请
handler5._mark_pending_welcome(970817189)
join_notice = {
    "post_type": "notice",
    "notice_type": "group_increase",
    "sub_type": "invite",
    "group_id": 970817189,
    "user_id": 2020987291,
    "operator_id": 3275304521,
    "self_id": 2020987291,
}
asyncio.run(handler5.dispatch(FakeEvent(join_notice, client5)))

check("向群发送了欢迎消息", len(client5.group_msgs), 1)
if client5.group_msgs:
    gid, segs = client5.group_msgs[0]
    check("目标群号", gid, 970817189)
    print("   ---- 发送内容 ----")
    for seg in segs:
        desc = seg["data"]["text"] if seg["type"] == "text" else f"<图片 {seg['data']['file'][:40]}...>"
        print(f"   [{seg['type']}] {desc}")
    check("第一段是文本", segs[0]["type"], "text")
    check("第二段是图片", segs[1]["type"], "image")
    check("图片使用 base64", segs[1]["data"]["file"].startswith("base64://"), True)
check("群成员接口被调用（补全群主/管理员）", any(c[0] == "get_group_member_list" for c in client5.calls), True)

# ------------------------------------------------------------------ 场景 6
print()
print("=" * 70)
print("G) 场景6：非本插件同意的入群 —— 复核通过后留在群里并欢迎")
handler6, _ = make_handler(
    **{"conditions.verify_after_join_delay": 0}  # GROUP_PAYLOAD 命中默认关键词
)
client6 = FakeClient(group_payload=GROUP_PAYLOAD)
asyncio.run(handler6.dispatch(FakeEvent(join_notice, client6)))
check("复核通过 -> 留在群里", client6.leaves, [])
check("复核通过 -> 发送了欢迎消息", len(client6.group_msgs), 1)

# ------------------------------------------------------------------ 场景 7
print()
print("=" * 70)
print("H) 场景7：全部条件关闭 -> 完全不处理")
handler7, history7 = make_handler(
    **{"conditions.keyword_enable": False, "conditions.member_count_enable": False}
)
client7 = FakeClient(group_payload=GROUP_PAYLOAD)
asyncio.run(handler7.dispatch(FakeEvent(INVITE_EVENT, client7)))
check("没有 approve/reject 调用", [c for c in client7.calls if c[0] == "set_group_add_request"], [])
check("没有私聊邀请人", client7.private_msgs, [])
check("统计·未处理 +1", history7.stats()["ignored"], 1)

# ------------------------------------------------------------------ 场景 8
print()
print("=" * 70)
print("I) 场景8：好友申请事件不应被处理")
handler8, _ = make_handler()
client8 = FakeClient()
friend_req = {
    "post_type": "request",
    "request_type": "friend",
    "user_id": 123,
    "comment": "加个好友",
    "flag": "abc",
    "self_id": 2020987291,
}
asyncio.run(handler8.dispatch(FakeEvent(friend_req, client8)))
check("好友申请 -> 无任何接口调用", client8.calls, [])
check("好友申请 -> 无私聊", client8.private_msgs, [])

# ------------------------------------------------------------------ 场景 9
print()
print("=" * 70)
print("J) 场景9：好友申请但 post_type=request 且 request_type=group 但 sub_type=add")
handler9, _ = make_handler()
client9 = FakeClient()
add_req = {**INVITE_EVENT, "sub_type": "add"}
asyncio.run(handler9.dispatch(FakeEvent(add_req, client9)))
check("sub_type=add -> 不处理", client9.calls, [])

# ------------------------------------------------------------------ 场景 10
print()
print("=" * 70)
print("K) 场景10：从申请收件箱补全群名称与邀请人昵称（事件本身不含）")
inbox = [
    {
        "group_id": 970817189,
        "group_name": "收件箱里的群名",
        "request_id": 1790544757556637,
        "requester_uin": 2020987291,
        "requester_nick": "FireflyFreeBot",
        "invitor_uin": 3275304521,
        "invitor_nick": "星陨",
        "message": "求求了",
        "checked": False,
        "flag": "slreq:1:1790544757556637:970817189:1:0",
    }
]
handler10, history10 = make_handler()
# 群信息接口返回空（模拟非成员群查不到）
client10 = FakeClient(group_payload={"group_id": 970817189}, inbox=inbox)
asyncio.run(handler10.dispatch(FakeEvent(INVITE_EVENT, client10)))

check("调用了 get_group_system_msg", any(c[0] == "get_group_system_msg" for c in client10.calls), True)
check("收件箱里的群名被采用", client10.private_msgs != [], True)
rec = (history10.list(5) or [None])[0] or {}
check("记录里群名来自收件箱", rec.get("group", {}).get("name"), "收件箱里的群名")
check("记录里邀请人昵称", rec.get("group", {}).get("inviter_name"), "星陨")
check("记录了待处理状态", "待处理" in json.dumps(rec.get("group", {}).get("extra", {}), ensure_ascii=False), True)

print()
print("=" * 70)
print("L) 场景11：收件箱不可用时退化为 get_stranger_info 取昵称")
handler11, history11 = make_handler()
client11 = FakeClient(group_payload=GROUP_PAYLOAD, fail_inbox=True)
asyncio.run(handler11.dispatch(FakeEvent(INVITE_EVENT, client11)))
check("退化调用 get_stranger_info", any(c[0] == "get_stranger_info" for c in client11.calls), True)
rec11 = (history11.list(5) or [None])[0] or {}
check("昵称来自 get_stranger_info", rec11.get("group", {}).get("inviter_name"), "陌生人3275304521")

print()
print("=" * 70)
print("M) 场景12：群名称已在事件/群信息中时不覆盖")
handler12, _ = make_handler()
client12 = FakeClient(group_payload=GROUP_PAYLOAD, inbox=inbox)
asyncio.run(handler12.dispatch(FakeEvent(INVITE_EVENT, client12)))
rec12 = (handler12.history.list(5) or [None])[0] or {}
check("保留 get_group_info 的群名", rec12.get("group", {}).get("name"), "原神交流群")

# ------------------------------------------------------------------ 场景 13
print()
print("=" * 70)
print("N) 场景13：机器人已在该群内 -> 跳过重复同意，但通知邀请人已完成")
handler13, history13 = make_handler()
client13 = FakeClient(
    group_payload=GROUP_PAYLOAD,
    group_list=[{"group_id": 970817189, "group_name": "原神交流群"}],
)
asyncio.run(handler13.dispatch(FakeEvent(INVITE_EVENT, client13)))
check("不调用 set_group_add_request", [c for c in client13.calls if c[0] == "set_group_add_request"], [])
check("仍然通知邀请人", len(client13.private_msgs), 1)
if client13.private_msgs:
    check("通知内容是「已同意」", client13.private_msgs[0][1][0]["data"]["text"], "已同意进群~")
rec13 = (history13.list(5) or [None])[0] or {}
check("记录里标记已在群内", "已在该群内" in " ".join(rec13.get("group", {}).get("extra", {}).values()), True)
check("决策模式为 already_member", rec13.get("mode"), "already_member")
check("已在群内时不再评估条件（避免自相矛盾）", rec13.get("conditions"), [])

# 关键回归：已在群内 + 条件本来不满足 -> 不能出现「未满足条件」却又回「已同意进群~」
h13b, hist13b = make_handler(
    **{"conditions.keywords": ["绝对不存在的关键词"], "conditions.member_count_enable": False}
)
c13b = FakeClient(
    group_payload=GROUP_PAYLOAD,
    group_list=[{"group_id": 970817189, "group_name": "原神交流群"}],
)
asyncio.run(h13b.dispatch(FakeEvent(INVITE_EVENT, c13b)))
rec13b = (hist13b.list(5) or [None])[0] or {}
reason13b = rec13b.get("reason") or ""
print("   reason:", reason13b)
check("reason 不再出现「未满足条件」", "未满足条件" not in reason13b, True)
check("reason 说明已在群内", "已在该群内" in reason13b, True)
check("回复邀请人「已同意」而非「不满足条件」",
      c13b.private_msgs[0][1][0]["data"]["text"] if c13b.private_msgs else "", "已同意进群~")
check("没有调用审批接口", [c for c in c13b.calls if c[0] == "set_group_add_request"], [])

# ------------------------------------------------------------------ 场景 14
print()
print("=" * 70)
print("O) 场景14：set_group_add_request 失败 -> 不谎报「已同意」")
handler14, history14 = make_handler()
client14 = FakeClient(group_payload=GROUP_PAYLOAD, approve_fails=True)
asyncio.run(handler14.dispatch(FakeEvent(INVITE_EVENT, client14)))
check(
    "调用了 set_group_add_request（canonical 失败后 legacy 重试，共 2 次）",
    len([c for c in client14.calls if c[0] == "set_group_add_request"]),
    2,
)
check("没有给邀请人发「已同意」", client14.private_msgs, [])
rec14 = (history14.list(5) or [None])[0] or {}
check("记录里写明同意失败", "同意群邀请失败" in (rec14.get("actions") or []), True)

# ------------------------------------------------------------------ 场景 15
print()
print("=" * 70)
print("P) 场景15：群资料取不到时的三种策略")
EMPTY_SHELL = {"group_id": 1012766700}  # SnowLuma 对非成员群返回的空壳
for policy, expect_approve, expect_notify in [
    ("approve", True, True),
    ("skip", None, False),
]:
    h, hist = make_handler(
        **{
            "conditions.when_info_unavailable": policy,
            "conditions.verify_after_join": False,
        }
    )
    c = FakeClient(group_payload=EMPTY_SHELL, fail_inbox=True)
    asyncio.run(h.dispatch(FakeEvent(INVITE_EVENT, c)))
    setreq = [x for x in c.calls if x[0] == "set_group_add_request"]
    got_approve = setreq[0][1]["approve"] if setreq else None
    check(f"策略 {policy} -> approve", got_approve, expect_approve)
    check(f"策略 {policy} -> 通知邀请人", len(c.private_msgs) > 0, expect_notify)
    rec = (hist.list(5) or [None])[0] or {}
    if policy in ("unmet", "approve"):
        check(
            f"策略 {policy} -> 记录原因",
            "无法判定条件" in (rec.get("reason") or ""),
            True,
        )
        if policy == "skip":
            check("skip 的 reason 说明不回复邀请人", "不回复" in (rec.get("reason") or ""), True)
    if policy == "skip":
        check(f"策略 {policy} -> 完全没动作", " ".join(rec.get("actions") or []), "")

# ------------------------------------------------------------------ 场景 16
print()
print("=" * 70)
print("Q) 场景16：取不到群资料时日志要写明原因，而不是只显示 0")
h16, _ = make_handler(
    **{"conditions.when_info_unavailable": "skip", "conditions.verify_after_join": False}
)
c16 = FakeClient(group_payload=EMPTY_SHELL, fail_inbox=True)
info16 = asyncio.run(
    __import__(
        "astrbot_plugin_group_invite_auto_approve.core.group_info",
        fromlist=["fetch_group_info"],
    ).fetch_group_info(c16, 1012766700, fetch_notice=False)
)
check("info_available=False", info16.info_available, False)
check("errors 里写明了原因", any("机器人不在该群" in e for e in info16.errors), True)
print("   ", " | ".join(info16.errors))

# ------------------------------------------------------------------ 场景 17
print()
print("=" * 70)
print("R) 场景17：入群后复核（先进群、再用真实资料判断、不满足则退群）")

# 邀请时拿不到群资料 -> 策略 approve 先进群
JOIN_NOTICE = {
    "post_type": "notice",
    "notice_type": "group_increase",
    "sub_type": "invite",
    "group_id": 1012766700,
    "user_id": 2020987291,
    "operator_id": 3275304521,
    "self_id": 2020987291,
}
REAL_PAYLOAD = {
    "group_id": 1012766700,
    "group_name": "随便聊聊群",
    "group_memo": "闲聊",
    "member_count": 12,
    "max_member_count": 200,
    "group_create_time": 1755432728,
    "group_level": 0,
    "group_all_shut": 0,
}

# R1: 复核通过 -> 留在群里并欢迎（群名命中默认关键词）
h17, hist17 = make_handler(
    **{"conditions.verify_after_join": True, "conditions.verify_after_join_delay": 0}
)
c17 = FakeClient(group_payload={**REAL_PAYLOAD, "group_name": "原神交流群"})
h17._mark_pending_welcome(1012766700, 3275304521)
asyncio.run(h17.dispatch(FakeEvent(JOIN_NOTICE, c17)))
check("复核不通过时不退群", c17.leaves, [])
check("发送了欢迎消息", len(c17.group_msgs), 1)

# R2: 复核不通过 -> 退群 + 通知邀请人
h18, hist18 = make_handler(
    **{
        "conditions.keywords": ["原神"],
        "conditions.verify_after_join": True,
        "conditions.verify_after_join_delay": 0,
    }
)
c18 = FakeClient(group_payload=REAL_PAYLOAD)
h18._mark_pending_welcome(1012766700, 3275304521)
asyncio.run(h18.dispatch(FakeEvent(JOIN_NOTICE, c18)))
check("复核不通过 -> 退群", c18.leaves, [1012766700])
check("复核不通过 -> 不发欢迎", c18.group_msgs, [])
check("复核不通过 -> 通知邀请人", len(c18.private_msgs), 1)
if c18.private_msgs:
    print("   ---- 给邀请人的说明 ----")
    print("   " + c18.private_msgs[0][1][0]["data"]["text"].replace("\n", "\n   "))
rec18 = (hist18.list(5) or [None])[0] or {}
check("记录里写明已退群", "已退出该群" in (rec18.get("actions") or []), True)

# R3: 复核开关关闭 -> 即使不满足也留在群里
h19, _ = make_handler(
    **{"conditions.keywords": ["原神"], "conditions.verify_after_join": False}
)
c19 = FakeClient(group_payload=REAL_PAYLOAD)
h19._mark_pending_welcome(1012766700, 3275304521)
asyncio.run(h19.dispatch(FakeEvent(JOIN_NOTICE, c19)))
check("开关关闭 -> 不退群", c19.leaves, [])
check("开关关闭 -> 仍然欢迎", len(c19.group_msgs), 1)

# R4: 「不请自来」的入群（QQ 对 <50 人的群会直接拉人）也要参与复核
h20, hist20 = make_handler(
    **{
        "conditions.keywords": ["原神"],
        "conditions.verify_after_join": True,
        "conditions.verify_after_join_delay": 0,
    }
)
c20 = FakeClient(group_payload=REAL_PAYLOAD)   # 群名「随便聊聊群」，不含关键词
asyncio.run(h20.dispatch(FakeEvent(JOIN_NOTICE, c20)))
check("非本插件同意 + 不合格 -> 退群", c20.leaves, [1012766700])
check("非本插件同意 + 不合格 -> 不发欢迎消息", c20.group_msgs, [])
rec20 = (hist20.list(5) or [None])[0] or {}
check("记录里写明已退群", "已退出该群" in (rec20.get("actions") or []), True)

# R4b: 非本插件同意 + 复核通过 -> 安静留下，不发欢迎消息
h20b, hist20b = make_handler(
    **{
        "conditions.verify_after_join": True,
        "conditions.verify_after_join_delay": 0,
    }
)
c20b = FakeClient(group_payload={**REAL_PAYLOAD, "group_name": "原神交流群"})
asyncio.run(h20b.dispatch(FakeEvent(JOIN_NOTICE, c20b)))
check("非本插件同意 + 合格 -> 不退群", c20b.leaves, [])
check("非本插件同意 + 合格 -> 会发欢迎消息（最终留在群里就该欢迎）", len(c20b.group_msgs), 1)
rec20b = (hist20b.list(5) or [None])[0] or {}
check("记录里写明复核通过保留", "复核通过，保留在群内" in (rec20b.get("actions") or []), True)

# R4c: 豁免名单里的人拉群 -> 即使不合格也不退
h20c, _ = make_handler(
    **{
        "conditions.keywords": ["原神"],
        "conditions.verify_after_join": True,
        "conditions.verify_after_join_delay": 0,
        "conditions.verify_exempt_users": ["3275304521"],
    }
)
c20c = FakeClient(group_payload=REAL_PAYLOAD)
asyncio.run(h20c.dispatch(FakeEvent(JOIN_NOTICE, c20c)))
check("豁免账号拉群 -> 不退群", c20c.leaves, [])

# R4d: 同一次入群被通知 + 轮询同时触发，只处理一次
h20d, _ = make_handler(
    **{
        "conditions.keywords": ["原神"],
        "conditions.verify_after_join": True,
        "conditions.verify_after_join_delay": 0,
    }
)
c20d = FakeClient(group_payload=REAL_PAYLOAD)
asyncio.run(h20d.dispatch(FakeEvent(JOIN_NOTICE, c20d)))
asyncio.run(h20d.dispatch(FakeEvent(JOIN_NOTICE, c20d)))
check("重复通知只处理一次（只退群一次）", c20d.leaves, [1012766700])

# R5: 总开关开启 -> 不退群
h21, _ = make_handler(
    **{
        "auto_approve_all": True,
        "conditions.keywords": ["原神"],
        "conditions.verify_after_join": True,
        "conditions.verify_after_join_delay": 0,
    }
)
c21 = FakeClient(group_payload=REAL_PAYLOAD)
h21._mark_pending_welcome(1012766700, 3275304521)
asyncio.run(h21.dispatch(FakeEvent(JOIN_NOTICE, c21)))
check("总开关开启 -> 不退群", c21.leaves, [])

# R6: 完整链路 —— 邀请时资料不可用 + approve 策略 + 入群复核退群
h22, _ = make_handler(
    **{
        "conditions.keywords": ["原神"],
        "conditions.when_info_unavailable": "approve",
        "conditions.verify_after_join": True,
        "conditions.verify_after_join_delay": 0,
    }
)
INVITE_EVENT_2 = {**INVITE_EVENT, "group_id": 1012766700, "flag": "slreq:1:999:1012766700:1:0"}
c22 = FakeClient(group_payload={"group_id": 1012766700}, fail_inbox=True)
asyncio.run(h22.dispatch(FakeEvent(INVITE_EVENT_2, c22)))
check("链路①：资料不可用但同意了邀请",
      [x for x in c22.calls if x[0] == "set_group_add_request"][0][1]["approve"], True)
c22.group_payload = REAL_PAYLOAD  # 进群后协议端能返回真实资料了
asyncio.run(h22.dispatch(FakeEvent(JOIN_NOTICE, c22)))
check("链路②：进群后复核不通过 -> 退群", c22.leaves, [1012766700])
print("   ✅ 完整链路：资料不可用 -> 先进群 -> 真实资料不满足 -> 自动退群")

# ------------------------------------------------------------------ 场景 18
print()
print("=" * 70)
print("S) 场景18：默认值 + 「复核」与「资料不可用策略」的相互作用")

cfg_default = make_plugin_config()
check("verify_after_join 默认开启", cfg_default.verify_after_join, True)
check("when_info_unavailable 默认 approve", cfg_default.when_info_unavailable, "approve")
check("默认无覆盖", cfg_default.info_policy_overridden, False)

# 复核开启时，skip 会被归一化成 approve（否则复核永远不触发）
c = make_plugin_config(
    **{"conditions.when_info_unavailable": "skip", "conditions.verify_after_join": True}
)
check("复核开启 + skip -> 生效策略", c.when_info_unavailable, "approve")
check("复核开启 + skip -> 标记为已覆盖", c.info_policy_overridden, True)
h, _hist = make_handler(
    **{"conditions.when_info_unavailable": "skip", "conditions.verify_after_join": True,
       "conditions.verify_after_join_delay": 0}
)
cl = FakeClient(group_payload={"group_id": 1012766700}, fail_inbox=True)
asyncio.run(h.dispatch(FakeEvent(INVITE_EVENT, cl)))
got = [x for x in cl.calls if x[0] == "set_group_add_request"]
check("复核开启 + skip -> 真的同意了邀请", bool(got) and got[0][1]["approve"], True)

# 复核关闭时，两种策略保持原样
for raw, expect in [("skip", None), ("approve", True)]:
    c = make_plugin_config(
        **{"conditions.when_info_unavailable": raw, "conditions.verify_after_join": False}
    )
    check(f"复核关闭 + {raw} -> 生效策略", c.when_info_unavailable, raw)
    check(f"复核关闭 + {raw} -> 无覆盖标记", c.info_policy_overridden, False)

# 已废弃的 unmet 必须被安全迁移为 skip
legacy = make_plugin_config(
    **{"conditions.when_info_unavailable": "unmet", "conditions.verify_after_join": False}
)
check("废弃的 unmet -> 迁移为 skip", legacy.when_info_unavailable, "skip")
check("废弃的 unmet -> 标记为已迁移", legacy.info_policy_legacy_migrated, True)
check("正常值不会被标记为已迁移",
      make_plugin_config(
          **{"conditions.when_info_unavailable": "skip", "conditions.verify_after_join": False}
      ).info_policy_legacy_migrated, False)

# 复核开启但策略本来就是 approve -> 不算覆盖
c = make_plugin_config(
    **{"conditions.when_info_unavailable": "approve", "conditions.verify_after_join": True}
)
check("approve + 复核 -> 无覆盖标记", c.info_policy_overridden, False)

# 策略摘要要把两者串起来讲清楚
summ = make_plugin_config().policy_summary()
print("   policy.detail:", summ["detail"])
check("摘要提到入群后复核", "复核" in summ["detail"], True)
check("摘要标记复核已开启", summ["verify_after_join"], True)
check("默认策略无覆盖", summ["info_policy_overridden"], False)

# 覆盖发生时，摘要要讲明
summ_ov = make_plugin_config(
    **{"conditions.when_info_unavailable": "unmet", "conditions.verify_after_join": True}
).policy_summary()
print("   (发生覆盖)", summ_ov["detail"])
check("覆盖时摘要带上说明", "固定为「进群试用」" in summ_ov["detail"], True)
check("覆盖时标记为已覆盖", summ_ov["info_policy_overridden"], True)
check("覆盖时原始值可查", summ_ov["info_policy_raw"], "skip")

summ2 = make_plugin_config(
    **{"conditions.when_info_unavailable": "unmet", "conditions.verify_after_join": False}
).policy_summary()
print("   (复核关闭)", summ2["detail"])
check("复核关闭时摘要说明不进群", "「不进群" in summ2["detail"], True)

# skip 与 approve 的摘要必须能一眼看出差别
k = make_plugin_config(
    **{"conditions.when_info_unavailable": "skip", "conditions.verify_after_join": False}
).policy_summary()["detail"]
a = make_plugin_config(
    **{"conditions.when_info_unavailable": "approve", "conditions.verify_after_join": False}
).policy_summary()["detail"]
print("   skip    摘要:", k)
print("   approve 摘要:", a)
check("skip 摘要写明不进群且不回复邀请人", "不回复邀请人" in k, True)
check("approve(无复核)摘要写明风险", "直接同意并留在群里" in a, True)
a2 = make_plugin_config(
    **{"conditions.when_info_unavailable": "approve", "conditions.verify_after_join": True}
).policy_summary()["detail"]
check("approve(有复核)摘要写明进群试用", "进群试用" in a2, True)
check("两者摘要确实不同", k != a, True)

# --- 文案一致性：决策 reason 不能让人误以为是「无差别同意」 ---
h23, hist23 = make_handler(
    **{"conditions.when_info_unavailable": "approve", "conditions.verify_after_join": True,
       "conditions.verify_after_join_delay": 0}
)
c23 = FakeClient(group_payload={"group_id": 1012766700}, fail_inbox=True)
asyncio.run(h23.dispatch(FakeEvent(INVITE_EVENT, c23)))
rec23 = (hist23.list(5) or [None])[0] or {}
reason23 = rec23.get("reason") or ""
print("   reason:", reason23)
check("reason 用「进群试用」措辞", "进群试用" in reason23, True)
check("reason 说明不合格会退出", "退出" in reason23, True)

summ3 = make_plugin_config().policy_summary()
check("摘要用「进群试用」措辞", "进群试用" in summ3["detail"], True)
check("摘要说明不合格自动退出", "退出" in summ3["detail"], True)

# ------------------------------------------------------------------ 场景 19
print()
print("=" * 70)
print("T) 场景19：群介绍( group_memo )的识别与公告实体还原")
from astrbot_plugin_group_invite_auto_approve.core.group_info import (  # noqa: E402
    GroupInfo as GI,
    _notice_text,
    fetch_group_info,
)

# QQ 会在群主未单独填写群介绍时，用公告预览填充 group_memo
g_preview = GI(
    group_id=1,
    name="星穹之下",
    memo="60星琼＋燃料兑换码：omega",
    notices=["uid：\n我小号：156461234", "60星琼＋燃料兑换码：omega", "微博超话签到四天"],
)
check("识别为公告预览", g_preview.memo_is_notice_preview, True)
check("命中公告序号", g_preview.memo_notice_index(), 1)
log_text = g_preview.to_log_text()
check("日志里标注了真实来源", "其实是群公告第 2 条的截断预览" in log_text, True)
check("日志里说明是协议端映射问题", "announcement || description" in log_text, True)
check("日志里提示升级协议端版本", "升级到 SnowLuma >= 1.14.21" in log_text, True)
check("未返回 group_description 时 intro 退回 memo", g_preview.intro, g_preview.memo)
check("未返回 group_description 时标记为公告", g_preview.intro_is_announcement, True)

# SnowLuma >= 1.14.21：协议端原生返回 group_description
g_described = GI(
    group_id=6,
    name="萤",
    memo="【napcat新版功能缺失的恢复方法】\n…\ndocker exec napcat",
    description="来来米哈游！",
    notices=["【napcat新版功能缺失的恢复方法】\n…\ndocker exec napcat sed -i 'x' /app/napcat/napcat.mjs"],
)
check("有 group_description 时 intro 用真群简介", g_described.intro, "来来米哈游！")
check("有 group_description 时不再标记为公告", g_described.intro_is_announcement, False)
check("有 group_description 时日志说明来源", "协议端 group_description" in g_described.to_log_text(), True)
check("memo 仍保留原语义（公告）", g_described.memo.startswith("【napcat"), True)
check("真群简介进入关键词语料", g_described.matched_keywords(["米哈游"]), ["米哈游"])
check("to_dict 带出真群简介", g_described.to_dict()["intro"], "来来米哈游！")

# {group_memo} 占位符与日志里的「群介绍」保持同一口径：
# 优先真群简介，取不到时才退回 group_memo 原值
from astrbot_plugin_group_invite_auto_approve.core.notify import (  # noqa: E402
    build_variables,
)

check(
    "{group_memo} 无 group_description 时退回 memo",
    build_variables(g_preview)["group_memo"],
    g_preview.memo,
)
check(
    "{group_memo} 有 group_description 时用真群简介",
    build_variables(g_described)["group_memo"],
    "来来米哈游！",
)
check(
    "{group_memo} 与日志「群介绍」同口径",
    build_variables(g_described)["group_memo"],
    g_described.intro,
)

# ---------------------------------------------------------------------------
# T2) 跨协议端：同一套采集逻辑要同时吃下 SnowLuma 与 NapCat 的返回形状
# ---------------------------------------------------------------------------
print()
print("=" * 70)
print("T2) 场景19b：SnowLuma / NapCat 跨协议端字段兼容")
print("    NapCat v4.18.19 的 get_group_info 不返回任何群简介字段，")
print("    只能补问 get_group_detail_info（字段名是 fingerMemo）。")

# ① SnowLuma >= 1.14.21：group_memo（公告）与 group_description（真简介）并存
c_sl = FakeClient(group_payload={
    "group_id": 10001,
    "group_name": "示例群",
    "group_remark": "",
    "member_count": 456,
    "max_member_count": 1000,
    "group_create_time": 1600000000,
    "group_level": 3,
    "group_all_shut": 0,
    "group_memo": "【公告】群规第一条：不许刷屏",
    "group_description": "这里是真正的群简介",
})
sl = asyncio.run(fetch_group_info(c_sl, 10001, fetch_notice=False))
check("SnowLuma 新版：用 group_description", sl.intro, "这里是真正的群简介")
check("SnowLuma 新版：来源标注", sl.description_source, "group_description")
check("SnowLuma 新版：memo 保留原语义", sl.memo, "【公告】群规第一条：不许刷屏")
check(
    "SnowLuma 新版：不额外补问详情",
    [a for a, _ in c_sl.calls if a == "get_group_detail_info"],
    [],
)

# ② SnowLuma <= 1.14.20：只有 group_memo，且它其实是公告的截断预览
notice_line = "【公告】群规第一条：不许刷屏"
c_sl20 = FakeClient(
    group_payload={
        "group_id": 10002,
        "group_name": "老版示例群",
        "member_count": 100,
        "max_member_count": 500,
        "group_all_shut": 0,
        "group_memo": notice_line,
    },
    notice=[notice_line, "第二条公告"],
    fail_notice=False,
)
sl20 = asyncio.run(fetch_group_info(c_sl20, 10002, fetch_notice=True))
check("SnowLuma 老版：退回 group_memo", sl20.intro, notice_line)
check("SnowLuma 老版：仍标记为公告预览", sl20.intro_is_announcement, True)
check("SnowLuma 老版：description 为空", sl20.description, "")

# ③ NapCat 已入群：get_group_info 只有 6 个基础字段 -> 补问 get_group_detail_info
c_nc = FakeClient(
    group_payload={
        "group_id": 20002,
        "group_name": "纳猫群",
        "group_remark": "我的备注",
        "member_count": 688,
        "max_member_count": 1000,
        "group_all_shut": 0,
    },
    group_detail={
        "group_id": 20002,
        "group_name": "纳猫群",
        "group_remark": "",
        "member_count": 688,
        "max_member_count": 1000,
        "group_all_shut": 0,
        "fingerMemo": "每日新资讯，新瓜，活动分享",
        "richFingerMemo": "每日新资讯，新瓜，活动分享（富文本）",
    },
)
nc = asyncio.run(fetch_group_info(c_nc, 20002, fetch_notice=False))
check("NapCat 已入群：补问后拿到群简介", nc.intro, "每日新资讯，新瓜，活动分享")
check("NapCat 已入群：来源标注 fingerMemo", nc.description_source, "fingerMemo")
check(
    "NapCat 已入群：确实补问了群详情接口",
    [a for a, _ in c_nc.calls if a == "get_group_detail_info"],
    ["get_group_detail_info"],
)
check("NapCat 已入群：补问不覆盖已有群名", nc.name, "纳猫群")
check("NapCat 已入群：补问不覆盖已有备注", nc.remark, "我的备注")
check("NapCat 已入群：memo 为空（该端不返回 group_memo）", nc.memo, "")
check("NapCat 已入群：不再误判为公告", nc.intro_is_announcement, False)

# ④ NapCat 未入群：get_group_info 直接展开原始详情，fingerMemo 就在里面 -> 无需补问
c_nc2 = FakeClient(group_payload={
    "group_id": 20003,
    "groupName": "未入群的群",
    "group_name": "未入群的群",
    "group_remark": "",
    "member_count": 300,
    "max_member_count": 500,
    "group_all_shut": 0,
    "fingerMemo": "米游聊天群，可聊崩铁、绝区零",
})
nc2 = asyncio.run(fetch_group_info(c_nc2, 20003, fetch_notice=False))
check("NapCat 未入群：直接用 fingerMemo", nc2.intro, "米游聊天群，可聊崩铁、绝区零")
check(
    "NapCat 未入群：无需补问",
    [a for a, _ in c_nc2.calls if a == "get_group_detail_info"],
    [],
)

# ⑤ 只有 richFingerMemo 时也要兜住
c_nc3 = FakeClient(group_payload={
    "group_id": 20004,
    "group_name": "只有富文本简介",
    "member_count": 50,
    "max_member_count": 200,
    "group_all_shut": 0,
    "richFingerMemo": "富文本群简介",
})
nc3 = asyncio.run(fetch_group_info(c_nc3, 20004, fetch_notice=False))
check("NapCat：richFingerMemo 兜底", nc3.intro, "富文本群简介")
check("NapCat：来源标注 richFingerMemo", nc3.description_source, "richFingerMemo")

# ⑥ 两个接口都拿不到群简介时，不能报错、不能崩
c_none = FakeClient(
    group_payload={
        "group_id": 20005,
        "group_name": "没有简介的群",
        "member_count": 10,
        "max_member_count": 200,
        "group_all_shut": 0,
    },
    group_detail={"group_id": 20005, "group_name": "没有简介的群"},
)
none_info = asyncio.run(fetch_group_info(c_none, 20005, fetch_notice=False))
check("都拿不到时 intro 为空", none_info.intro, "")
check("都拿不到时仍拿到群名", none_info.name, "没有简介的群")
check("都拿不到时不产生错误条目", none_info.errors, [])

# 真正独立的群介绍不应被误判
g_real = GI(
    group_id=2,
    name="爱莉小窝",
    memo="无论何时何地，爱莉希雅都会回应你的期待～\n①群572196296",
    notices=["抢红包", "禁止刷屏"],
)
check("独立群介绍不误判", g_real.memo_is_notice_preview, False)
check("独立群介绍命中 -1", g_real.memo_notice_index(), -1)
check("真群简介的日志是正面标注", "就是真正的群简介" in g_real.to_log_text(), True)
check("真群简介不会被误标注为公告", "截断预览" not in g_real.to_log_text(), True)
check("空群介绍不误判", GI(group_id=3, notices=["abc"]).memo_notice_index(), -1)
check("无公告时不误判", GI(group_id=4, memo="简介").memo_notice_index(), -1)

# 公告里的 HTML 实体要还原，否则换行被当成 &\#10; 影响可读性与关键词匹配
raw_notice = {"message": {"text": "第一行&#10;第二行&nbsp;缩进&quot;引号&quot; &amp; &#039;单引&#039;"}}
decoded = _notice_text(raw_notice)
print("   还原结果:", repr(decoded))
check("&#10; 还原为换行", "\n" in decoded and "&#10;" not in decoded, True)
check("&nbsp; 还原为空格", "&nbsp;" not in decoded, True)
check("引号/& 还原", '"引号"' in decoded and " & " in decoded, True)

# 实体还原的实际收益：&nbsp; 会把词拆开，之前含空格的短语关键词匹配不到
raw_phrase = {"message": {"text": "解决方法：docker&nbsp;exec&nbsp;napcat"}}
before = raw_phrase["message"]["text"]          # 未还原时的原文
after = _notice_text(raw_phrase)                # 还原后
check("原文里 docker exec 被 &nbsp; 拆开", "docker exec" not in before, True)
check("还原后 docker exec 可被匹配", "docker exec" in after, True)
check("短语关键词能命中", "docker exec" in after, True)

g_entity = GI(group_id=5, name="测试群", notices=[after])
check("短语关键词在群信息里命中", g_entity.matched_keywords(["docker exec"]), ["docker exec"])

# ------------------------------------------------------------------ 场景 20
print()
print("=" * 70)
print("U) 场景20：「重复邀请 + 小群」-> 按条件复核，不合格退群")

SMALL_GROUP = {
    "group_id": 970817189,
    "group_name": "萤",
    "group_memo": "来来米哈游！",
    "member_count": 9,
    "max_member_count": 200,
    "group_create_time": 1755432728,
    "group_level": 0,
    "group_all_shut": 0,
}
INVITE_SMALL = {**INVITE_EVENT, "group_id": 970817189}

# U1: 小群 + 条件不满足 -> 退群（这就是「QQ 强行拉进小群」的补救）
h30, hist30 = make_handler(
    **{
        "conditions.keywords": ["绝对不存在的词"],
        "conditions.member_count_enable": False,
        "conditions.verify_after_join": True,
        "conditions.verify_after_join_delay": 0,
        "conditions.force_join_max_members": 50,
    }
)
c30 = FakeClient(
    group_payload=SMALL_GROUP,
    group_list=[{"group_id": 970817189, "group_name": "萤"}],
)
asyncio.run(h30.dispatch(FakeEvent(INVITE_SMALL, c30)))
check("小群重复邀请 -> 退群", c30.leaves, [970817189])
check("小群重复邀请 -> 不调用审批接口", [c for c in c30.calls if c[0] == "set_group_add_request"], [])
check("小群重复邀请 -> 给邀请人说明原因", len(c30.private_msgs), 1)
if c30.private_msgs:
    txt = c30.private_msgs[0][1][0]["data"]["text"]
    check("说明里不是「已同意进群」", "已同意进群" not in txt, True)
rec30 = (hist30.list(5) or [None])[0] or {}
check("决策模式已是 already_member", rec30.get("mode"), "already_member")
check("reason 说明会复核", "将按配置条件复核" in (rec30.get("reason") or ""), True)
check("记录了已退群", "已退群" in " ".join(rec30.get("actions") or []), True)

# U2: 小群 + 条件满足 -> 留在群里，仍按「已进群」回应
h31, _ = make_handler(
    **{
        "conditions.keywords": ["米哈游"],
        "conditions.member_count_enable": False,
        "conditions.verify_after_join": True,
        "conditions.verify_after_join_delay": 0,
    }
)
c31 = FakeClient(
    group_payload=SMALL_GROUP,
    group_list=[{"group_id": 970817189, "group_name": "萤"}],
)
asyncio.run(h31.dispatch(FakeEvent(INVITE_SMALL, c31)))
check("小群 + 条件满足 -> 不退群", c31.leaves, [])
check("小群 + 条件满足 -> 正常回应", c31.private_msgs[0][1][0]["data"]["text"], "已同意进群~")

# U3: 大群重复邀请 -> 不复核、不退群（不质疑正常加入的群）
BIG_GROUP = {**SMALL_GROUP, "member_count": 800, "max_member_count": 2000}
h32, hist32 = make_handler(
    **{
        "conditions.keywords": ["绝对不存在的词"],
        "conditions.member_count_enable": False,
        "conditions.verify_after_join": True,
        "conditions.verify_after_join_delay": 0,
    }
)
c32 = FakeClient(
    group_payload=BIG_GROUP,
    group_list=[{"group_id": 970817189, "group_name": "萤"}],
)
asyncio.run(h32.dispatch(FakeEvent(INVITE_SMALL, c32)))
check("大群重复邀请 -> 不退群", c32.leaves, [])
rec32 = (hist32.list(5) or [None])[0] or {}
check("大群 reason 说明不判断条件", "不判断关键词" in (rec32.get("reason") or ""), True)

# U4: 阈值设为 0 -> 关闭该行为
h33, _ = make_handler(
    **{
        "conditions.keywords": ["绝对不存在的词"],
        "conditions.member_count_enable": False,
        "conditions.verify_after_join": True,
        "conditions.verify_after_join_delay": 0,
        "conditions.force_join_max_members": 0,
    }
)
c33 = FakeClient(
    group_payload=SMALL_GROUP,
    group_list=[{"group_id": 970817189, "group_name": "萤"}],
)
asyncio.run(h33.dispatch(FakeEvent(INVITE_SMALL, c33)))
check("阈值 0 -> 关闭复核", c33.leaves, [])

# U5: 豁免账号 -> 不退群
h34, _ = make_handler(
    **{
        "conditions.keywords": ["绝对不存在的词"],
        "conditions.member_count_enable": False,
        "conditions.verify_after_join": True,
        "conditions.verify_after_join_delay": 0,
        "conditions.verify_exempt_users": ["3275304521"],
    }
)
c34 = FakeClient(
    group_payload=SMALL_GROUP,
    group_list=[{"group_id": 970817189, "group_name": "萤"}],
)
asyncio.run(h34.dispatch(FakeEvent(INVITE_SMALL, c34)))
check("豁免账号 -> 不退群", c34.leaves, [])

# U6: 总开关开启 -> 不退群
h35, _ = make_handler(
    **{
        "auto_approve_all": True,
        "conditions.keywords": ["绝对不存在的词"],
        "conditions.verify_after_join": True,
        "conditions.verify_after_join_delay": 0,
    }
)
c35 = FakeClient(
    group_payload=SMALL_GROUP,
    group_list=[{"group_id": 970817189, "group_name": "萤"}],
)
asyncio.run(h35.dispatch(FakeEvent(INVITE_SMALL, c35)))
check("总开关开启 -> 不退群", c35.leaves, [])

# U7: 人数未知（0）-> 不误退
h36, _ = make_handler(
    **{
        "conditions.keywords": ["绝对不存在的词"],
        "conditions.member_count_enable": False,
        "conditions.verify_after_join": True,
        "conditions.verify_after_join_delay": 0,
    }
)
c36 = FakeClient(
    group_payload={"group_id": 970817189, "group_name": "", "member_count": 0},
    group_list=[{"group_id": 970817189}],
)
asyncio.run(h36.dispatch(FakeEvent(INVITE_SMALL, c36)))
check("人数未知 -> 不误退", c36.leaves, [])

# ------------------------------------------------------------------ 场景 21
print()
print("=" * 70)
print("V) 场景21：复核通过后必须发欢迎消息（含重复邀请与不请自来）")

WELCOME_TEXT = "我是机器人，欢迎使用"


def welcome_msgs(client):
    out = []
    for _gid, segs in client.group_msgs:
        for seg in segs:
            if seg.get("type") == "text":
                out.append(seg["data"]["text"])
    return out


# V1: 重复邀请 + 小群 + 复核通过 -> 要发欢迎
h40, _ = make_handler(
    **{
        "conditions.keywords": ["米哈游"],
        "conditions.member_count_enable": False,
        "conditions.verify_after_join": True,
        "conditions.verify_after_join_delay": 0,
        "welcome.enable": True,
        "welcome.message": WELCOME_TEXT,
    }
)
c40 = FakeClient(
    group_payload=SMALL_GROUP,
    group_list=[{"group_id": 970817189, "group_name": "萤"}],
)
asyncio.run(h40.dispatch(FakeEvent(INVITE_SMALL, c40)))
check("重复邀请复核通过 -> 发了欢迎消息", welcome_msgs(c40), [WELCOME_TEXT])
check("重复邀请复核通过 -> 不退群", c40.leaves, [])

# V2: 不请自来的入群 + 复核通过 -> 也要发欢迎
h41, _ = make_handler(
    **{
        "conditions.member_count_enable": False,
        "conditions.verify_after_join": True,
        "conditions.verify_after_join_delay": 0,
        "welcome.enable": True,
        "welcome.message": WELCOME_TEXT,
    }
)
c41 = FakeClient(
    group_payload={**REAL_PAYLOAD, "group_name": "原神交流群"},
    group_list=[{"group_id": 1012766700, "group_name": "原神交流群"}],
)
asyncio.run(h41.dispatch(FakeEvent(JOIN_NOTICE, c41)))
check("不请自来复核通过 -> 发了欢迎消息", welcome_msgs(c41), [WELCOME_TEXT])

# V3: 入群通知与重复邀请同时到 -> 只发一次
h42, _ = make_handler(
    **{
        "conditions.keywords": ["米哈游"],
        "conditions.member_count_enable": False,
        "conditions.verify_after_join": True,
        "conditions.verify_after_join_delay": 0,
        "welcome.enable": True,
        "welcome.message": WELCOME_TEXT,
    }
)
c42 = FakeClient(
    group_payload=SMALL_GROUP,
    group_list=[{"group_id": 970817189, "group_name": "萤"}],
)
h42._mark_pending_welcome(970817189, 3275304521)
asyncio.run(h42.dispatch(FakeEvent({**JOIN_NOTICE, "group_id": 970817189}, c42)))
asyncio.run(h42.dispatch(FakeEvent(INVITE_SMALL, c42)))
check("通知 + 重复邀请 -> 欢迎消息只发一次", welcome_msgs(c42), [WELCOME_TEXT])

# V4: 复核不通过 -> 不发欢迎
h43, _ = make_handler(
    **{
        "conditions.keywords": ["绝对不存在的词"],
        "conditions.member_count_enable": False,
        "conditions.verify_after_join": True,
        "conditions.verify_after_join_delay": 0,
    }
)
c43 = FakeClient(
    group_payload=SMALL_GROUP,
    group_list=[{"group_id": 970817189, "group_name": "萤"}],
)
asyncio.run(h43.dispatch(FakeEvent(INVITE_SMALL, c43)))
check("复核不通过 -> 不发欢迎消息", welcome_msgs(c43), [])
check("复核不通过 -> 已退群", c43.leaves, [970817189])

# V5: 大群重复邀请（不复核）-> 不补发欢迎，避免刷屏
h44, _ = make_handler(
    **{
        "conditions.member_count_enable": False,
        "conditions.verify_after_join": True,
        "welcome.enable": True,
        "welcome.message": WELCOME_TEXT,
    }
)
c44 = FakeClient(
    group_payload=BIG_GROUP,
    group_list=[{"group_id": 970817189, "group_name": "萤"}],
)
asyncio.run(h44.dispatch(FakeEvent(INVITE_SMALL, c44)))
check("大群重复邀请 -> 不补发欢迎消息", welcome_msgs(c44), [])

# ------------------------------------------------------------------ 场景 22
print()
print("=" * 70)
print("W) 场景22：只有群名、人数未知 -> 不能当成「不满足」，要走「进群试用」")

# 复现线上那一幕：群名从申请收件箱补到了，但人数读不到（协议端对非成员群返回 0）
NAME_ONLY = {"group_id": 702209896, "group_name": "～太阳", "member_count": 0}
INVITE_NAME_ONLY = {**INVITE_EVENT, "group_id": 702209896, "flag": "slreq:1:1:702209896:1:0"}

h50, hist50 = make_handler(
    **{
        "conditions.keywords": ["太阳"],
        "conditions.min_member_count": 300,
        "conditions.max_member_count": 3000,
        "conditions.verify_after_join": True,
        "conditions.verify_after_join_delay": 0,
    }
)
c50 = FakeClient(group_payload=NAME_ONLY, fail_inbox=True)
asyncio.run(h50.dispatch(FakeEvent(INVITE_NAME_ONLY, c50)))

setreq = [x for x in c50.calls if x[0] == "set_group_add_request"]
check("人数未知 -> 仍然同意进群（进群试用）", bool(setreq) and setreq[0][1]["approve"], True)
rec50 = (hist50.list(5) or [None])[0] or {}
print("   reason:", rec50.get("reason"))
check("reason 不再说「未满足条件」", "未满足条件" not in (rec50.get("reason") or ""), True)
check("决策模式为 unknown_info", rec50.get("mode"), "unknown_info")
conds = {c["key"]: c for c in (rec50.get("conditions") or [])}
check("关键词判定为满足", conds["keyword"]["passed"], True)
check("人数标记为无法判定", conds["member_count"]["evaluable"], False)
check("人数不算作不满足", conds["member_count"]["passed"], False)

# 对照：关键词明确不匹配时，仍然是「不满足」，不进群
h51, hist51 = make_handler(
    **{
        "conditions.keywords": ["绝对不存在的词"],
        "conditions.min_member_count": 300,
        "conditions.verify_after_join": True,
        "conditions.verify_after_join_delay": 0,
    }
)
c51 = FakeClient(group_payload=NAME_ONLY, fail_inbox=True)
asyncio.run(h51.dispatch(FakeEvent(INVITE_NAME_ONLY, c51)))
check("关键词明确不匹配 -> 不调用审批接口", [x for x in c51.calls if x[0] == "set_group_add_request"], [])
rec51 = (hist51.list(5) or [None])[0] or {}
check("关键词明确不匹配 -> reason 说未满足", "未满足条件" in (rec51.get("reason") or ""), True)

# 对照：群名也拿不到（资料完全不可用）-> 同样走「进群试用」
h52, _ = make_handler(
    **{
        "conditions.keywords": ["太阳"],
        "conditions.min_member_count": 300,
        "conditions.verify_after_join": True,
        "conditions.verify_after_join_delay": 0,
    }
)
c52 = FakeClient(group_payload={"group_id": 702209896}, fail_inbox=True)
asyncio.run(h52.dispatch(FakeEvent(INVITE_NAME_ONLY, c52)))
setreq52 = [x for x in c52.calls if x[0] == "set_group_add_request"]
check("资料完全不可用 -> 仍然进群试用", bool(setreq52) and setreq52[0][1]["approve"], True)

# 进群后人数已知且不满足 -> 退群
h53, _ = make_handler(
    **{
        "conditions.keywords": ["太阳"],
        "conditions.min_member_count": 300,
        "conditions.max_member_count": 3000,
        "conditions.verify_after_join": True,
        "conditions.verify_after_join_delay": 0,
    }
)
c53 = FakeClient(group_payload=NAME_ONLY, fail_inbox=True)
asyncio.run(h53.dispatch(FakeEvent(INVITE_NAME_ONLY, c53)))
c53.group_payload = {**NAME_ONLY, "member_count": 120, "max_member_count": 500}
c53.group_list = [{"group_id": 702209896, "group_name": "～太阳"}]
asyncio.run(h53.dispatch(FakeEvent({**JOIN_NOTICE, "group_id": 702209896}, c53)))
check("进群后真实人数 120 < 300 -> 退群", c53.leaves, [702209896])

# 配置写反（min > max）属于明确错误，仍按不满足处理
h54, hist54 = make_handler(
    **{
        "conditions.member_count_enable": True,
        "conditions.min_member_count": 3000,
        "conditions.max_member_count": 100,
        "conditions.verify_after_join": True,
        "conditions.verify_after_join_delay": 0,
    }
)
c54 = FakeClient(group_payload={**NAME_ONLY, "member_count": 500}, fail_inbox=True)
asyncio.run(h54.dispatch(FakeEvent(INVITE_NAME_ONLY, c54)))
rec54 = (hist54.list(5) or [None])[0] or {}
check("配置写反 -> 明确不满足", "配置有误" in (rec54.get("reason") or ""), True)

# ------------------------------------------------------------------ 场景 23
print()
print("=" * 70)
print("X) 场景23：SnowLuma 的 canonical flag 被拒 -> 自动换 legacy flag 重试")

from astrbot_plugin_group_invite_auto_approve.core.notify import (  # noqa: E402
    legacy_invite_flag,
)

check("legacy flag 格式", legacy_invite_flag(702209896, 3275304521), "invite:702209896:3275304521")
check("无邀请人时也能构造", legacy_invite_flag(702209896), "invite:702209896:0")

CANONICAL = {**INVITE_EVENT, "group_id": 702209896, "flag": "slreq:1:1790559064250306:702209896:1:0"}

# X1: canonical flag 被拒 -> 自动用 legacy 重试并成功
h60, hist60 = make_handler(**{"conditions.keywords": ["原神"], "conditions.verify_after_join_delay": 0})
c60 = FakeClient(group_payload=GROUP_PAYLOAD)
c60.canonical_flag_fails = True
asyncio.run(h60.dispatch(FakeEvent(CANONICAL, c60)))

setreq = [c for c in c60.calls if c[0] == "set_group_add_request"]
check("调用了两次审批接口（canonical + legacy）", len(setreq), 2)
check("第一次用 canonical flag", setreq[0][1]["flag"].startswith("slreq:"), True)
check("第二次用 legacy flag", setreq[1][1]["flag"], "invite:702209896:3275304521")
rec60 = (hist60.list(5) or [None])[0] or {}
check("最终判定为已同意", rec60.get("approve"), True)
check("记录了已同意群邀请", "已同意群邀请" in (rec60.get("actions") or []), True)

# X2: 非 canonical flag（例如 NapCat 的数字 flag）失败时不应乱重试
NON_CANONICAL = {**INVITE_EVENT, "flag": "1790559064250306"}
h61, _ = make_handler(**{"conditions.keywords": ["原神"], "conditions.verify_after_join_delay": 0})
c61 = FakeClient(group_payload=GROUP_PAYLOAD, approve_fails=True)
asyncio.run(h61.dispatch(FakeEvent(NON_CANONICAL, c61)))
setreq61 = [c for c in c61.calls if c[0] == "set_group_add_request"]
check("非 canonical flag -> 只调一次", len(setreq61), 1)
check("非 canonical flag -> 不会用 legacy 重试",
      any(c[1].get("flag", "").startswith("invite:") for c in setreq61), False)

# X3: canonical flag 本身成功时不应重试（例如协议端修好了）
h62, _ = make_handler(**{"conditions.keywords": ["原神"], "conditions.verify_after_join_delay": 0})
c62 = FakeClient(group_payload=GROUP_PAYLOAD)
asyncio.run(h62.dispatch(FakeEvent(CANONICAL, c62)))
setreq62 = [c for c in c62.calls if c[0] == "set_group_add_request"]
check("canonical 成功 -> 只调一次", len(setreq62), 1)

# X4: canonical 与 legacy 都失败 -> 如实报错、不谎报成功
h63, hist63 = make_handler(**{"conditions.keywords": ["原神"], "conditions.verify_after_join_delay": 0})
c63 = FakeClient(group_payload=GROUP_PAYLOAD, approve_fails=True)
c63.canonical_flag_fails = True
asyncio.run(h63.dispatch(FakeEvent(CANONICAL, c63)))
setreq63 = [c for c in c63.calls if c[0] == "set_group_add_request"]
check("两次都失败 -> 调用了两次", len(setreq63), 2)
check("都失败 -> 不给邀请人发「已同意」", c63.private_msgs, [])
rec63 = (hist63.list(5) or [None])[0] or {}
check("都失败 -> 记录里写明同意失败", "同意群邀请失败" in (rec63.get("actions") or []), True)

print()
print("=" * 70)
if FAILED:
    print(f"❌ {len(FAILED)} 项失败：")
    for item in FAILED:
        print("   -", item)
    sys.exit(1)
print("✅ 容器内集成测试全部通过")
