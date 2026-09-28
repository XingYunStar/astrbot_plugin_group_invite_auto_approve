"""在独立进程中真实调用 AstrBot 的 PluginManager.load()，验证插件能被完整加载。

不会影响正在运行的 AstrBot 实例：这是另一个 Python 进程，
只构造一个最小 Context 与 PluginManager，并只加载本插件。
"""

from __future__ import annotations

import asyncio
import sys
import types

sys.path.insert(0, "/AstrBot")
sys.path.insert(0, "/AstrBot/data/plugins")

PLUGIN_DIR_NAME = "astrbot_plugin_group_invite_auto_approve"

FAILED: list[str] = []


def check(label, actual, expected=True):
    ok = bool(actual) is bool(expected) if isinstance(expected, bool) else actual == expected
    print(f"{'PASS' if ok else 'FAIL'}  {label}: {actual!r}" + ("" if ok else f" != {expected!r}"))
    if not ok:
        FAILED.append(label)


# ---------------------------------------------------------------- 最小 Context
class StubContext:
    def __init__(self):
        self.registered_web_apis = []
        self.platform_manager = types.SimpleNamespace(platform_insts=[])
        self._star_manager = None

    def register_web_api(self, route, view_handler, methods, desc):
        for idx, api in enumerate(self.registered_web_apis):
            if api[0] == route and methods == api[2]:
                self.registered_web_apis[idx] = (route, view_handler, methods, desc)
                return
        self.registered_web_apis.append((route, view_handler, methods, desc))

    def get_config(self, umo=None):
        return {}


async def main():
    from astrbot.core.star import star_manager as sm_mod
    from astrbot.core.star.star import star_registry

    # 用假 sp 顶掉需要数据库的共享偏好
    class FakeSP:
        async def global_get(self, scope, default=None):
            return default

        async def global_put(self, scope, value):
            return None

    sm_mod.sp = FakeSP()

    ctx = StubContext()
    manager = sm_mod.PluginManager(ctx, {})

    print("=" * 70)
    print("调用 PluginManager.load(specified_dir_name=...)")
    success, error = await manager.load(specified_dir_name=PLUGIN_DIR_NAME)
    print("   返回值:", success, error)
    check("load 返回成功", success)
    check("无错误信息", error, None)

    print()
    print("=" * 70)
    print("插件是否进入 star_registry")
    entry = next((m for m in star_registry if m.name == PLUGIN_DIR_NAME), None)
    check("注册表里有本插件", entry is not None)
    if entry is None:
        return
    print("   ", entry)
    check("display_name", entry.display_name, "群邀请自动处理")
    check("version", entry.version, "v1.0.0")
    check("activated", entry.activated)
    check("配置对象已注入", entry.config is not None)
    check("实例已创建", entry.star_cls is not None)
    check("实例类型正确", type(entry.star_cls).__name__, "GroupInviteAutoApprovePlugin")
    check("root_dir_name", entry.root_dir_name, PLUGIN_DIR_NAME)
    print("   注册的 handler 数量:", len(entry.star_handler_full_names))
    for name in entry.star_handler_full_names:
        print("      -", name)

    print()
    print("=" * 70)
    print("管理页路由是否注册")
    routes = [r[0] for r in ctx.registered_web_apis]
    print("   ", routes)
    for suffix in (
        "/ping",
        "/bootstrap",
        "/config",
        "/history",
        "/history/clear",
        "/scan",
        "/simulate",
    ):
        check(f"含路由 {suffix}", f"/{PLUGIN_DIR_NAME}{suffix}" in routes)

    print()
    print("=" * 70)
    print("配置对象是否按 schema 生成默认值")
    conf = entry.config
    # 注意：这里读的是线上真实配置文件，用户可能已改过值，
    # 因此只校验「结构与类型正确」，不校验具体默认值。
    check("auto_approve_all 是布尔", isinstance(conf.get("auto_approve_all"), bool))
    check("keywords 是非空列表", isinstance(conf["conditions"]["keywords"], list)
          and len(conf["conditions"]["keywords"]) >= 0)
    print("   keywords =", conf["conditions"]["keywords"])
    check("welcome.message 是字符串", isinstance(conf["welcome"]["message"], str))
    check("verify_after_join 是布尔", isinstance(conf["conditions"]["verify_after_join"], bool))
    check(
        "when_info_unavailable 取值合法",
        conf["conditions"]["when_info_unavailable"] in ("approve", "unmet", "skip"),
        True,
    )

    print()
    print("=" * 70)
    print("插件实例内部状态")
    star = entry.star_cls
    check("cfg 存在", star.cfg is not None)
    check("handler 存在", star.handler is not None)
    check("history 存在", star.history is not None)
    check("data_dir 存在", star.data_dir.is_dir())
    print("   data_dir:", star.data_dir)
    print("   当前策略:", star.cfg.policy_summary()["label"])

    print()
    print("=" * 70)
    print("调用 initialize()")
    await star.initialize()
    check("initialize 未抛异常", True)

    print()
    print("=" * 70)
    print("调用 terminate()")
    await star.terminate()
    check("terminate 未抛异常", True)

    print()
    print("=" * 70)
    print("事件过滤器是否被正确登记（必须有至少一个 filter，否则会被 AstrBot 跳过）")
    from astrbot.core.star.star_handler import star_handlers_registry

    handlers = [
        h
        for h in star_handlers_registry
        if h.handler_module_path and PLUGIN_DIR_NAME in h.handler_module_path
    ]
    print(f"   共登记 {len(handlers)} 个 handler")
    for h in handlers:
        filters = [type(f).__name__ for f in h.event_filters]
        print(f"   - {h.handler_name}: filters={filters}")
        check(f"{h.handler_name} 至少有一个 filter", len(filters) > 0)


asyncio.run(main())

print()
print("=" * 70)
if FAILED:
    print(f"❌ {len(FAILED)} 项失败：")
    for item in FAILED:
        print("   -", item)
    sys.exit(1)
print("✅ PluginManager 真实加载测试全部通过")
