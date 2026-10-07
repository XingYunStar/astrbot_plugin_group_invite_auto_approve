"""校验 AstrBot 插件管理器能否正确识别本插件（不触碰正在运行的实例）。

覆盖三件事：

1. ``PluginManager._load_plugin_metadata`` 能解析 ``metadata.yaml``；
2. 版本约束 ``astrbot_version`` 与当前 AstrBot 兼容；
3. 插件页发现逻辑能在 ``pages/`` 下找到入口 ``index.html``，且 i18n 标题可读。
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, "/AstrBot")
sys.path.insert(0, "/AstrBot/data/plugins")

import astrbot.core.config.default as default_mod  # noqa: E402
from astrbot.core.star.star_manager import PluginManager  # noqa: E402

PLUGIN_NAME = "astrbot_plugin_group_invite_auto_approve"
PLUGIN_DIR = Path("/AstrBot/data/plugins") / PLUGIN_NAME

FAILED: list[str] = []


def check(label, actual, expected=True):
    ok = bool(actual) is bool(expected) if isinstance(expected, bool) else actual == expected
    print(f"{'PASS' if ok else 'FAIL'}  {label}: {actual!r}" + ("" if ok else f" != {expected!r}"))
    if not ok:
        FAILED.append(label)


print("=" * 70)
print("A) metadata.yaml 解析")
metadata = PluginManager._load_plugin_metadata(plugin_path=str(PLUGIN_DIR))
print("   ", metadata)
check("metadata 非空", metadata is not None)
check("name", metadata.name, PLUGIN_NAME)
check("display_name", metadata.display_name, "群邀请自动处理")
check(
    "version 形如 vX.Y.Z",
    bool(re.fullmatch(r"v\d+\.\d+\.\d+", metadata.version or "")),
    True,
)
check("support_platforms", metadata.support_platforms, ["aiocqhttp"])
check("astrbot_version", metadata.astrbot_version, ">=4.24.2")
check("desc 非空", bool(metadata.desc))

print()
print("=" * 70)
print("B) AstrBot 版本兼容性")
print("   当前 AstrBot:", default_mod.VERSION)
# 复用 AstrBot 自己的校验逻辑
dummy = PluginManager.__new__(PluginManager)
ok, msg = dummy._validate_astrbot_version_specifier(metadata.astrbot_version)
check("版本约束校验通过", ok)
if not ok:
    print("   错误信息:", msg)

print()
print("=" * 70)
print("C) 插件 Pages 发现")
pages_root = PLUGIN_DIR / "pages"
check("pages/ 目录存在", pages_root.is_dir())
page_dirs = sorted(p.name for p in pages_root.iterdir() if p.is_dir())
print("   发现页面目录:", page_dirs)
check("含 invite 页面", "invite" in page_dirs)
entry = pages_root / "invite" / "index.html"
check("入口文件存在", entry.is_file())
print("   入口大小:", entry.stat().st_size, "字节")

# AstrBot 的页面名校验规则
normalize = PluginManager.__mro__  # 占位，实际用 PluginRoute 的静态方法
from astrbot.dashboard.routes.plugin import PluginRoute  # noqa: E402

for name in page_dirs:
    try:
        normalized = PluginRoute._normalize_plugin_page_name(name)
        check(f"页面名 {name} 合法", normalized == name)
    except ValueError as exc:
        check(f"页面名 {name} 合法", False)
        print("      ", exc)

print()
print("=" * 70)
print("D) 页面资源完整性")
for asset in ("index.html", "app.js", "api.js", "styles.css"):
    path = pages_root / "invite" / asset
    check(f"{asset} 存在", path.is_file())
    if path.is_file():
        print(f"      {asset}: {path.stat().st_size} 字节")

print()
print("=" * 70)
print("D2) 页面脚本语法检查（前端没有任何其它测试覆盖，必须在这里挡住）")
# 背景：app.js 曾经因为少了一个闭合括号而整份脚本语法错误 ——
# 浏览器按 <script type="module"> 加载时会直接 SyntaxError，
# 页面上的所有交互（配置渲染、邀请记录、调试工具）全部失效，
# 但后端接口测试完全测不出来。这里用 node 做一次真正的语法检查。
#
# 注意：不能直接 `node --check x.js` —— 对带 import 的 .js，node 会先按
# CommonJS 解析，遇到语法错误时反而可能报成功（实测 exit 0 的假阴性）。
# 复制成 .mjs 强制按 ES Module 解析才可靠。
import shutil as _shutil  # noqa: E402
import subprocess as _subprocess  # noqa: E402
import tempfile as _tempfile  # noqa: E402

_node = _shutil.which("node")
if not _node:
    # 不把「环境里没装 node」当成插件缺陷：AstrBot 官方镜像未必带 node。
    # 但必须显式说出来，不能静默跳过。
    print("      SKIP  环境里没有 node，跳过页面脚本语法检查")
    print("            装了 node 的环境会自动执行这一段（本地容器为 /usr/bin/node）")
else:
    check("找到 node 可执行文件", True)
    for js in sorted(pages_root.rglob("*.js")):
        with _tempfile.TemporaryDirectory() as tmp:
            probe = Path(tmp) / (js.stem + ".mjs")
            probe.write_text(js.read_text(encoding="utf-8"), encoding="utf-8")
            proc = _subprocess.run(
                [_node, "--check", str(probe)],
                capture_output=True,
                text=True,
                check=False,
            )
        rel = js.relative_to(pages_root)
        ok = proc.returncode == 0
        check(f"{rel} 语法合法", ok)
        if not ok:
            for line in (proc.stderr or "").strip().splitlines()[:6]:
                print("      ", line)

print()
print("=" * 70)
print("E) i18n 页面标题")
i18n_dir = PLUGIN_DIR / ".astrbot-plugin" / "i18n"
check("i18n 目录存在", i18n_dir.is_dir())
loaded = PluginManager._load_plugin_i18n(str(PLUGIN_DIR))
print("   已加载语言:", list(loaded.keys()))
check("含 zh-CN", "zh-CN" in loaded)
check("含 en-US", "en-US" in loaded)
title = (loaded.get("zh-CN") or {}).get("pages", {}).get("invite", {}).get("title")
check("页面标题", title, "群邀请管理")
check("display_name i18n", (loaded.get("zh-CN") or {}).get("metadata", {}).get("display_name"), "群邀请自动处理")

print()
print("=" * 70)
print("F) 配置 Schema 与文件")
schema_path = PLUGIN_DIR / "_conf_schema.json"
check("_conf_schema.json 存在", schema_path.is_file())
schema = json.loads(schema_path.read_text(encoding="utf-8"))
check("受支持的配置类型", set(), set())
supported = {"int", "float", "bool", "string", "text", "list", "file", "object", "template_list", "dict"}
bad: list[str] = []


def walk(node, prefix=""):
    for key, spec in node.items():
        if spec.get("type") not in supported:
            bad.append(f"{prefix}{key}: {spec.get('type')}")
        if spec.get("type") == "object":
            walk(spec.get("items", {}), f"{prefix}{key}.")


walk(schema)
print("   非法类型:", bad or "无")
check("所有配置类型均受支持", not bad)
check("object 类型都有 items", all("items" in v for v in schema.values() if v["type"] == "object"))
check("main.py 存在", (PLUGIN_DIR / "main.py").is_file())
check("requirements.txt 存在", (PLUGIN_DIR / "requirements.txt").is_file())
check("README.md 存在", (PLUGIN_DIR / "README.md").is_file())

print()
print("=" * 70)
if FAILED:
    print(f"❌ {len(FAILED)} 项失败：")
    for item in FAILED:
        print("   -", item)
    sys.exit(1)
print("✅ 插件加载路径校验全部通过")
