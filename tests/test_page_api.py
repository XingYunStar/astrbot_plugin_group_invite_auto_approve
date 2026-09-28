"""管理页 Web API 的端到端测试。

使用 AstrBot 自己的路由匹配函数 ``_match_registered_web_api`` 与 Quart 的测试客户端，
真实地走一遍 ``/api/plug/<插件名>/<路径>`` 这条链路。
"""

from __future__ import annotations

import asyncio
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, "/AstrBot")
sys.path.insert(0, "/AstrBot/data/plugins")

from quart import Quart, jsonify, request  # noqa: E402

from astrbot.core.config.astrbot_config import AstrBotConfig  # noqa: E402
from astrbot.dashboard.server import _match_registered_web_api  # noqa: E402

from astrbot_plugin_group_invite_auto_approve.core.config import PluginConfig  # noqa: E402
from astrbot_plugin_group_invite_auto_approve.core.history import InviteHistory  # noqa: E402
from astrbot_plugin_group_invite_auto_approve.core.page_api import (  # noqa: E402
    PLUGIN_NAME,
    PageController,
)

PLUGIN_DIR = Path("/AstrBot/data/plugins/astrbot_plugin_group_invite_auto_approve")
SCHEMA = json.loads((PLUGIN_DIR / "_conf_schema.json").read_text(encoding="utf-8"))

FAILED: list[str] = []


def check(label, actual, expected):
    ok = actual == expected
    print(f"{'PASS' if ok else 'FAIL'}  {label}: {actual!r}" + ("" if ok else f" != {expected!r}"))
    if not ok:
        FAILED.append(label)


# ---------------------------------------------------------------- 伪造 Context
class FakeContext:
    def __init__(self):
        self.registered_web_apis = []
        self.platform_manager = type("PM", (), {"platform_insts": []})()

    def register_web_api(self, route, view_handler, methods, desc):
        self.registered_web_apis.append((route, view_handler, methods, desc))


class FakeClient:
    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    async def call_action(self, api, **kw):
        self.calls.append((api, kw))
        if api in ("get_group_info", "get_group_info_ex"):
            return dict(self.payload)
        if api == "_get_group_notice":
            return [{"notice_id": "1", "message": {"text": "群公告内容：原神版本更新"}}]
        if api == "get_group_member_list":
            return [
                {"user_id": 1, "role": "owner", "nickname": "群主", "card": ""},
                {"user_id": 2, "role": "admin", "nickname": "管理", "card": ""},
            ]
        from aiocqhttp.exceptions import ActionFailed

        exc = ActionFailed("failed")
        exc.result = {"status": "failed", "retcode": 1404, "wording": "unknown action"}
        raise exc


class FakePlatform:
    def __init__(self, bot, pid="FireflyFreeBot_5", name="aiocqhttp"):
        self.bot = bot
        self.client_self_id = "2020987291"
        self._meta = type("M", (), {"id": pid, "name": name})()

    def meta(self):
        return self._meta


# ---------------------------------------------------------------- 搭建应用
tmp = Path(tempfile.mkdtemp())
cfg_path = tmp / f"{PLUGIN_NAME}_config.json"
raw_cfg = AstrBotConfig(config_path=str(cfg_path), schema=SCHEMA)
cfg = PluginConfig(raw_cfg)
history = InviteHistory(tmp)

fake_client = FakeClient(
    {
        "group_id": 970817189,
        "group_name": "原神交流群",
        "group_memo": "提瓦特旅行者聚集地",
        "member_count": 456,
        "max_member_count": 1000,
        "group_create_time": 1755432728,
        "group_level": 3,
        "group_all_shut": 0,
    }
)
ctx = FakeContext()
ctx.platform_manager = type("PM", (), {"platform_insts": [FakePlatform(fake_client)]})()

controller = PageController(ctx, cfg, history)
controller.register_routes()

print("=" * 70)
print("已注册路由：")
for route, _h, methods, desc in ctx.registered_web_apis:
    print(f"   {methods[0]:5} {route}   ({desc})")

# 用 AstrBot 自己的匹配器 + Quart 复刻 /api/plug/<subpath>
app = Quart(__name__)


@app.route("/api/plug/<path:subpath>", methods=["GET", "POST"])
async def srv_plug_route(subpath):
    matched = _match_registered_web_api(
        ctx.registered_web_apis, subpath, request.method
    )
    if matched:
        view_handler, path_values = matched
        return await view_handler(**path_values)
    return jsonify({"status": "error", "message": "未找到该路由"})


client = app.test_client()


async def get(endpoint, params=None):
    query = "?" + "&".join(f"{k}={v}" for k, v in (params or {}).items()) if params else ""
    resp = await client.get(f"/api/plug/{PLUGIN_NAME}{endpoint}{query}")
    return resp.status_code, await resp.get_json()


async def post(endpoint, body=None):
    resp = await client.post(f"/api/plug/{PLUGIN_NAME}{endpoint}", json=body or {})
    return resp.status_code, await resp.get_json()


async def _run_all():
    print()
    print("=" * 70)
    print("1) GET /ping")
    status, data = await get("/ping")
    check("HTTP 200", status, 200)
    check("ok=True", data.get("ok"), True)

    print()
    print("=" * 70)
    print("2) GET /bootstrap")
    status, data = await get("/bootstrap")
    check("HTTP 200", status, 200)
    check("ok=True", data.get("ok"), True)
    payload = data.get("data") or {}
    check("包含 config", "config" in payload, True)
    check("包含 policy", "policy" in payload, True)
    check("包含 stats", "stats" in payload, True)
    check("包含 platforms", "platforms" in payload, True)
    check("平台列表长度", len(payload.get("platforms") or []), 1)
    check("keywords 默认", payload["config"]["conditions"]["keywords"], ["原神", "星穹铁道", "绝区零"])
    print("   policy:", json.dumps(payload.get("policy"), ensure_ascii=False))

    print()
    print("=" * 70)
    print("3) POST /config 保存配置")
    new_config = json.loads(json.dumps(payload["config"]))
    new_config["auto_approve_all"] = True
    new_config["conditions"]["keywords"] = ["鸣潮", "原神"]
    new_config["conditions"]["min_member_count"] = 20
    new_config["welcome"]["image"] = "https://example.com/a.png"
    status, data = await post("/config", {"config": new_config})
    check("HTTP 200", status, 200)
    check("ok=True", data.get("ok"), True)
    saved = data["data"]["config"]
    check("auto_approve_all 已保存", saved["auto_approve_all"], True)
    check("keywords 已保存", saved["conditions"]["keywords"], ["鸣潮", "原神"])
    check("min 已保存", saved["conditions"]["min_member_count"], 20)
    check("welcome.image 已保存", saved["welcome"]["image"], "https://example.com/a.png")
    check("policy 变为总开关", data["data"]["policy"]["mode"], "approve_all")

    # 落盘校验
    on_disk = json.loads(cfg_path.read_text(encoding="utf-8-sig"))
    check("配置文件已写入磁盘", on_disk["auto_approve_all"], True)
    check("磁盘 keywords", on_disk["conditions"]["keywords"], ["鸣潮", "原神"])

    print()
    print("=" * 70)
    print("4) GET /config 读取配置")
    status, data = await get("/config")
    check("ok=True", data.get("ok"), True)
    check("读回 auto_approve_all", data["data"]["config"]["auto_approve_all"], True)

    print()
    print("=" * 70)
    print("5) POST /config 传非法数据")
    status, data = await post("/config", {"config": "not-a-dict"})
    check("ok=False", data.get("ok"), False)
    check("有错误信息", bool(data.get("message")), True)
    status, data = await post("/config", {"conditions": {"max_member_count": "abc", "keywords": 12345}})
    check("非法类型被收敛后仍成功", data.get("ok"), True)
    check("max 回落为 0 或原值", isinstance(data["data"]["config"]["conditions"]["max_member_count"], int), True)
    check("keywords 非列表回落为空", data["data"]["config"]["conditions"]["keywords"], [])

    print()
    print("=" * 70)
    print("6) POST /scan 群号体检（含群公告与成员名单）")
    cfg.apply_payload({"conditions": {"keywords": ["原神", "星穹铁道", "绝区零"], "keyword_enable": True, "member_count_enable": True, "min_member_count": 0, "max_member_count": 3000}, "auto_approve_all": False})
    status, data = await post("/scan", {"group_id": 970817189})
    check("ok=True", data.get("ok"), True)
    group = data["data"]["group"]
    check("群名称", group["name"], "原神交流群")
    check("群介绍", group["memo"], "提瓦特旅行者聚集地")
    check("群人数", group["member_count"], 456)
    check("群公告已读到", group["notices"], ["群公告内容：原神版本更新"])
    check("群主已读到", group["owner_id"], 1)
    check("管理员已读到", group["admin_ids"], [2])
    check("决策为同意", data["data"]["decision"]["approve"], True)

    print()
    print("=" * 70)
    print("7) POST /scan 群号非法")
    status, data = await post("/scan", {"group_id": "abc"})
    check("ok=False", data.get("ok"), False)
    check("提示填写群号", "群号" in (data.get("message") or ""), True)

    print()
    print("=" * 70)
    print("8) POST /simulate 规则试算")
    status, data = await post(
        "/simulate",
        {
            "group_id": 1,
            "name": "随便聊聊",
            "memo": "闲聊",
            "member_count": 5,
            "max_member_count": 500,
            "notices": ["公告"],
        },
    )
    check("ok=True", data.get("ok"), True)
    check("试算为不处理", data["data"]["decision"]["approve"], None)
    check("未满足原因含关键词", "未包含关键词" in data["data"]["decision"]["reasons_text"], True)
    print("   reasons:", data["data"]["decision"]["reasons_text"].replace("\n", " | "))

    status, data = await post("/simulate", {"name": "原神群", "member_count": 100, "max_member_count": 500})
    check("试算为同意", data["data"]["decision"]["approve"], True)

    print()
    print("=" * 70)
    print("9) GET /history + POST /history/clear")
    cfg2 = PluginConfig(raw_cfg)
    cfg2.apply_payload({"notify": {"unmet_auto_reject": False}})
    status, data = await get("/history", {"limit": 10})
    check("ok=True", data.get("ok"), True)
    check("包含 records", isinstance(data["data"]["records"], list), True)
    check("包含 stats", isinstance(data["data"]["stats"], dict), True)

    history.add({"approve": True, "group": {"group_id": 1, "name": "x"}, "actions": []}, 10)
    status, data = await get("/history", {"limit": 10})
    check("记录写入成功", len(data["data"]["records"]), 1)
    status, data = await post("/history/clear", {})
    check("清空成功", data["data"]["stats"]["invites"], 0)

    print()
    print("=" * 70)
    print("10) 未注册路由返回 404 语义")
    status, data = await get("/does-not-exist")
    check("未匹配到路由", data.get("message"), "未找到该路由")

    print()
    print("=" * 70)
    print("11) 无平台时 /scan 的降级提示")
    ctx.platform_manager = type("PM", (), {"platform_insts": []})()
    status, data = await post("/scan", {"group_id": 123})
    check("ok=False", data.get("ok"), False)
    check("提示无可用平台", "aiocqhttp" in (data.get("message") or ""), True)


asyncio.run(_run_all())

print()
print("=" * 70)
if FAILED:
    print(f"❌ {len(FAILED)} 项失败：")
    for item in FAILED:
        print("   -", item)
    sys.exit(1)
print("✅ 管理页 API 测试全部通过")
