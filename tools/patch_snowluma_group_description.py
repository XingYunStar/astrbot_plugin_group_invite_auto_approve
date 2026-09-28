#!/usr/bin/env python3
"""给本地 SnowLuma 打补丁：把被丢弃的「真·群简介」暴露出来。

## 背景

SnowLuma 解析群列表时是这样填 ``group_memo`` 的（``/app/runtime/index.mjs``）::

    memo: raw.info?.announcement || raw.info?.description || ""

NTQQ 的群数据里 ``announcement``（公告）和 ``description``（群简介）是**两个独立字段**，
SnowLuma 用 ``||`` 合并成一个 ``memo``，于是**只要有公告，群简介就永远看不到**
（实测 22 个群：18 个有公告的群 memo 全是某条公告的截断前缀，
2 个零公告的群 memo 才是真群简介）。

## 这个补丁做什么

**纯附加**：既有的 ``group_memo`` 语义一个字都不动，只新增一个
``group_description`` 字段，因此不会影响任何依赖 ``group_memo`` 的既有插件。

改动 4 处：

1. ``fetchGroupList``：把 ``raw.info?.description`` 也存进群对象
2. OneBot ``get_group_list``：输出 ``group_description``
3. OneBot ``get_group_info``（成员群分支）：输出 ``group_description``
4. OneBot ``get_group_info``（非成员群分支）：输出空字符串

改动后本插件会自动优先使用 ``group_description`` 作为群介绍
（见 ``core/group_info.py`` 的 ``GroupInfo.intro``）。

## ⚠️ 重要限制

* ``/app/runtime`` 在**容器可写层**，不是挂载卷 —— 补丁**能扛住容器重启，
  但容器被重建（``docker compose up --force-recreate``、升级镜像等）就会丢失**，
  需要重新执行本脚本。
* 非成员群的 proto（``OidbGroupDetailFlags``）里**根本没有** description 字段，
  只有 ``noticePreview`` —— 所以「机器人不在群里」时依然拿不到真群简介。
* 这是改第三方软件，属于本地非官方补丁；SnowLuma 升级后请重新执行并验证。
* 重启 SnowLuma 后 **QQ 可能需要手动登录**（本次实测就需要），请留意。

## 用法

在**宿主机**上执行::

    # 1) 取出当前文件
    mkdir -p /tmp/snowluma_patch
    docker cp snowluma:/app/runtime/index.mjs /tmp/snowluma_patch/index.mjs

    # 2) 打补丁（脚本会自行备份为 index.mjs.orig）
    python3 patch_snowluma_group_description.py

    # 3) 写回容器；已存在 .orig 则不覆盖（避免冲掉真正的原版）
    docker exec snowluma sh -c '[ -f /app/runtime/index.mjs.orig ] || \
        cp -a /app/runtime/index.mjs /app/runtime/index.mjs.orig'
    docker cp /tmp/snowluma_patch/index.mjs snowluma:/app/runtime/index.mjs
    docker exec snowluma sh -c 'chown 1001:1001 /app/runtime/index.mjs'

    # 4) 重启（QQ 可能需要手动登录）
    docker restart snowluma

    # 5) 验证
    curl -s -X POST http://127.0.0.1:3100/get_group_info \\
      -H "Authorization: Bearer <token>" -H 'Content-Type: application/json' \\
      -d '{"group_id":970817189}' | grep -o '"group_description":"[^"]*"'

## 回滚

    docker exec snowluma sh -c 'cp -a /app/runtime/index.mjs.orig /app/runtime/index.mjs'
    docker restart snowluma
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

SRC = Path("/tmp/snowluma_patch/index.mjs")
MARKER = "group_description"

EDITS: list[tuple[str, str, str]] = [
    (
        "① 群列表解析：把 description 也存进群对象",
        '\t\t\tmemo: raw.info?.announcement || raw.info?.description || "",\n'
        "\t\t\tallMuted: isGroupAllMuted(raw.info?.shutUpAllTimestamp)\n",
        '\t\t\tmemo: raw.info?.announcement || raw.info?.description || "",\n'
        '\t\t\tdescription: raw.info?.description ?? "",\n'
        "\t\t\tallMuted: isGroupAllMuted(raw.info?.shutUpAllTimestamp)\n",
    ),
    (
        "② OneBot get_group_list：输出 group_description",
        '\t\tgroup_memo: g.memo ?? "",\n\t\tgroup_all_shut: g.allMuted ? -1 : 0\n\t}));\n',
        '\t\tgroup_memo: g.memo ?? "",\n'
        '\t\tgroup_description: g.description ?? "",\n'
        "\t\tgroup_all_shut: g.allMuted ? -1 : 0\n\t}));\n",
    ),
    (
        "③ OneBot get_group_info（成员群分支）",
        "\t\tgroup_level: await getGroupLevel(bridge, groupId, noCache),\n"
        '\t\tgroup_memo: g.memo ?? "",\n'
        "\t\tgroup_all_shut: g.allMuted ? -1 : 0\n\t};\n",
        "\t\tgroup_level: await getGroupLevel(bridge, groupId, noCache),\n"
        '\t\tgroup_memo: g.memo ?? "",\n'
        '\t\tgroup_description: g.description ?? "",\n'
        "\t\tgroup_all_shut: g.allMuted ? -1 : 0\n\t};\n",
    ),
    (
        "④ OneBot get_group_info（非成员群分支）",
        "\t\t\t\tgroup_level: detail.level ?? 0,\n"
        '\t\t\t\tgroup_memo: detail.memo ?? "",\n'
        "\t\t\t\tgroup_all_shut: detail.allMuted ? -1 : 0\n",
        "\t\t\t\tgroup_level: detail.level ?? 0,\n"
        '\t\t\t\tgroup_memo: detail.memo ?? "",\n'
        '\t\t\t\tgroup_description: "",\n'
        "\t\t\t\tgroup_all_shut: detail.allMuted ? -1 : 0\n",
    ),
]


def main() -> int:
    if not SRC.is_file():
        print(f"找不到待补丁文件: {SRC}")
        print("请先按脚本头部说明把 index.mjs 复制到 /tmp/snowluma_patch/")
        return 2

    text = SRC.read_text(encoding="utf-8")

    if MARKER in text:
        print(f"检测到 {MARKER}：该文件已经打过补丁，无需重复执行。")
        return 0

    original_len = len(text)
    print(f"原始大小: {original_len} 字节")
    print("=" * 70)

    for label, old, new in EDITS:
        count = text.count(old)
        if count != 1:
            print(f"FAIL  {label}: 匹配到 {count} 处（应为 1），已中止，文件未改动")
            print("      SnowLuma 版本可能已变化，请核对脚本头部的上下文说明。")
            return 1
        text = text.replace(old, new, 1)
        print(f"OK    {label}")

    print("=" * 70)
    print(f"补丁后大小: {len(text)} 字节 (+{len(text) - original_len})")

    backup = SRC.with_suffix(".mjs.orig")
    if not backup.exists():
        shutil.copy2(SRC, backup)
        print(f"已备份原文件到 {backup}")

    SRC.write_text(text, encoding="utf-8")
    print("✅ 补丁已写入，请按脚本头部说明写回容器并重启")
    return 0


if __name__ == "__main__":
    sys.exit(main())
