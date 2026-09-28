[![AstrBot](https://img.shields.io/badge/AstrBot-Plugin-ff69b4?style=for-the-badge)](https://github.com/AstrBotDevs/AstrBot)

# astrbot_plugin_group_invite_auto_approve · 群邀请自动处理

一个面向 **aiocqhttp（OneBot v11）** 平台、特别是 **SnowLuma** 协议端的 AstrBot 插件：
自动处理机器人收到的群邀请，并把群聊信息完整记录到日志与 WebUI 管理页。

> ### 🚨 部署提醒（只有一条，但容易忘）
>
> **每次重建 SnowLuma 容器后，都要重跑一次本地补丁**，否则插件拿到的「群介绍」
> 会是**群公告的截断预览**而不是真正的群简介。
>
> 原因：SnowLuma 把 NTQQ 里两个独立字段合并成了
> `memo: raw.info?.announcement || raw.info?.description`，公告一存在就把群简介盖掉。
>
> 补丁是**纯附加**的（只新增 `group_description`，不动既有字段），
> 不打也不影响插件运行，只是群介绍不准。完整步骤见
> **[安装 → 第 2 步](#第-2-步给-snowluma-打本地补丁可选但强烈建议)**，
> 脚本在 `tools/patch_snowluma_group_description.py`。

---

## 功能

### 1. 收到群邀请时打印此群所有信息

每收到一个群邀请，插件都会采集并在 AstrBot 日志中整块打印该群的可用信息：

```
============================================================
群号        : 970817189
群名称      : 原神交流群
群备注      : （无）
群人数      : 456 / 1000
群创建时间  : 2025-08-17 20:12:08
群等级      : 3
全员禁言    : 否
群主        : 群主昵称(111)
管理员      : 管理昵称(222)、另一个管理(333)
群介绍      : 欢迎来到提瓦特
群公告(2) : 
  第一条公告
  ---
  第二条公告
获取失败字段:
  ! 群公告: 读取失败（机器人尚未入群，或协议端不支持该接口）
============================================================
```

> **为什么有些字段是「未获取到」？** 收到群邀请时机器人**还没有进群**，因此只能拿到
> 公开可查的信息（群名、群介绍、人数、建群时间、等级、全员禁言）。
> 群主 / 管理员 / 群公告需要机器人已在群内才能读取，插件会在**成功入群后**自动补全这几项，
> 并把完整信息再打印一次。每个字段独立获取，任何一个接口失败都不影响其它字段，
> 失败原因会列在「获取失败字段」里。

### 2. 自动同意规则

| 优先级 | 开关 | 说明 |
| --- | --- | --- |
| 1（最高） | **自动同意所有群邀请** | 开启后无条件同意所有群邀请，**不再判断下方任何条件** |
| 2 | **满足以下要求时自动同意** | 只有**开启**的条件参与判断，多个条件同时开启时关系为 **AND（全部满足才同意）** |
| — | 条件全部关闭 | **不处理**该群邀请：不自动同意、不自动拒绝、也不发送任何消息 |

「满足以下要求时」包含两个可独立开关的条件：

- **关键词**：群名称、群介绍（群简介）、群公告**任意一处**包含配置的**任一**关键词即满足。
  默认关键词：`原神`、`星穹铁道`、`绝区零`。
- **人数**：需要**同时**满足「群人数 **大于** 最小人数」且「群人数 **少于** 最大人数」。
  默认 `0 < 人数 < 3000`（即不限制）。

#### 条件的三种状态：满足 / 不满足 / **无法判定**

判断条件时严格区分「明确不满足」与「拿不到数据」：

| 状态 | 什么时候出现 | 处理方式 |
| --- | --- | --- |
| ✅ 满足 | 读到了数据且符合 | 继续判断其它条件 |
| ❌ 不满足 | **读到了数据**但不符合（例如读到了群名、关键词确实不在里面） | 视为未满足 → 默认不处理（可配置自动拒绝） |
| ❓ **无法判定** | **协议端没给所需数据**：机器人不在群里时 `member_count` 恒为 0，群名/群介绍/群公告也可能全都拿不到 | **不当作不满足**，改走 `when_info_unavailable` 策略 |

> ⚠️ **群人数不可能是 0。** 协议端对「机器人不在群内」的群恒返回 `0`，
> 这只说明**读不到**，不能当成「人数不满足」—— 否则任何没进过的群都会被挡在门外
> （曾经就出现过：关键词明明命中了，却因为人数读不到而没进群）。

「无法判定」时的行为由 `conditions.when_info_unavailable`
（界面名：**群名 / 人数拿不到时怎么办**）决定：
`approve` **进群试用**（默认）/ `skip` 不进群也不回复。

这条路径和「入群后复核」是同一套逻辑 —— **先进群，用真实资料再判一次，不合格就退**：

```
收到群邀请（群名有、人数读不到）
  -> 关键词 ✅ 满足，人数 ❓ 无法判定
  -> 按「进群试用」：同意进群
  -> group_increase / 轮询兜底
  -> 读到真实人数（此时机器人已在群里，协议端会正常返回）
  -> 重新判断：满足就留下 + 欢迎；不满足就私聊说明原因后退群
```

> 💡 **默认已经是推荐组合**：`verify_after_join` 默认开启，
> 此时 `when_info_unavailable` 会被强制为 `approve`。详见下方「推荐组合」。

未满足条件时的默认行为是**保持待处理**（不做任何操作）；可在配置中开启
「未满足条件时自动拒绝群邀请」，此时才会主动调用 `set_group_add_request` 拒绝。

### 3. 消息通知

| 开关 | 默认值 | 说明 |
| --- | --- | --- |
| 同意进群后对邀请人发送消息 | 开 | 默认文案 `已同意进群~` |
| 未满足配置的开关条件时对邀请人发送消息 | 开 | 默认文案 `不满足以下条件：\n{reasons}` |
| 自定义额外消息 | 空 | 追加在「未满足条件」消息之后，留空不发送 |
| 当同意进群后发送消息 | 开 | 默认文案 `我是机器人，欢迎使用`，入群后在群内发送 |
| 当同意进群后同时发送图片 | 空 | 支持本地文件绝对路径或 http(s) 图片 URL，留空不发送图片 |

消息支持占位符：

| 占位符 | 含义 |
| --- | --- |
| `{group_name}` | 群名称 |
| `{group_id}` | 群号 |
| `{group_remark}` | 群备注 |
| `{group_memo}` | 群介绍 |
| `{member_count}` / `{max_member_count}` | 当前人数 / 人数上限 |
| `{create_time}` | 建群时间 |
| `{inviter}` / `{inviter_id}` | 邀请人昵称 / QQ |
| `{keywords}` | 当前配置的关键词列表 |
| `{reasons}` | 自动生成的「未满足条件」清单 |

> `{reasons}` 未写进模板时会自动追加到消息末尾。未知占位符会原样保留，不会报错。

**入群欢迎消息**只在「本插件同意了这次邀请、机器人随后入群」时发送。
如果机器人是被管理员手动同意或通过其它方式拉进群的，不会发送欢迎消息。

图片处理：本地路径、`http(s)://` URL、`base64://` 都会被转换成 base64 后再发送，
因此不受 AstrBot 容器与协议端容器之间路径差异的影响。

---

## 插件管理页

在 AstrBot WebUI 中进入 **插件 → 群邀请自动处理 → 打开 Pages**，
即可打开本插件自带的管理页（页面目录 `pages/invite`）。

管理页包含三个标签页：

- **配置**：可视化编辑上面全部开关与文案，支持关键词标签化编辑、图片 URL 预览、
  未保存修改提示、一键恢复默认关键词。顶部横幅实时显示**当前生效的策略**。
- **邀请记录**：统计卡片（收到邀请 / 已同意 / 已拒绝 / 未处理）+
  按时间倒序的邀请记录，每条记录可展开查看**该群的全部已获取信息**与命中的条件明细。
- **调试工具**：
  - **群号体检**：输入群号实时查询该群完整信息并试算规则，不产生任何同意/拒绝操作。
  - **规则试算**：手动构造群名称 / 人数 / 群介绍 / 群公告，离线验证规则是否符合预期。
  - **运行环境**：显示插件版本与已连接的 aiocqhttp 平台。

页面配置文件与 AstrBot 原生的插件配置页**读写同一份数据**
（`data/config/astrbot_plugin_group_invite_auto_approve_config.json`），两边改哪边都会同步。

---

## 指令

| 指令 | 权限 | 说明 |
| --- | --- | --- |
| `/群邀请状态` | 管理员 | 查看当前策略、已开启的条件与累计统计 |

---

## 安装

### 第 1 步：装插件

把插件目录放到 `AstrBot/data/plugins/astrbot_plugin_group_invite_auto_approve/`，
然后在 WebUI 插件页**重载插件**（或在插件市场安装后启用）。

- 无第三方依赖，`requirements.txt` 为空。
- 仅支持 `aiocqhttp` 平台。
- 需要 AstrBot `>= 4.24.2`（插件 Pages 功能）。

### 第 2 步：给 SnowLuma 打本地补丁（可选但强烈建议）

**一句话**：不打这个补丁，插件拿到的「群介绍」其实是**群公告的截断预览**，
而不是真正的群简介。打上之后才拿得到真群简介。

原因见下方「[关于「群介绍永远是群公告」](#关于群介绍永远是群公告)」：
SnowLuma 把 NTQQ 里两个独立字段合并了 ——
`memo: raw.info?.announcement || raw.info?.description`，公告一存在就把群简介盖掉。

实测效果（12 个群，真简介与公告 **0/12 相同**）：

| 群 | 不打补丁（=公告预览） | 打补丁后（真群简介） |
| --- | --- | --- |
| 萤 (970817189) | `【napcat新版功能缺失的恢复方法】…` | `来来米哈游！` |
| 流萤守护协会 | `为防止最坏情况发生，希望各位收藏备群…` | `这里是流萤守护协会 / 愿做你永远的港湾…` |
| 都是米哈游干的 | `群规 第1.不恶意刷屏…` | `米游聊天群，可聊崩铁，绝区零等米游…` |

**在宿主机上执行**（补丁脚本随插件一起分发，位于 `tools/`）：

```bash
cd /project/bot/AstrBot/firefly_data_2/plugins/astrbot_plugin_group_invite_auto_approve/tools

# 1) 取出 SnowLuma 的运行时
mkdir -p /tmp/snowluma_patch
docker cp snowluma:/app/runtime/index.mjs /tmp/snowluma_patch/index.mjs

# 2) 打补丁（幂等：已打过会提示无需重复执行；匹配不上会安全中止）
python3 patch_snowluma_group_description.py

# 3) 写回容器；并在容器内留一份原件（已存在则不覆盖，避免把真正的原版冲掉）
docker exec snowluma sh -c '[ -f /app/runtime/index.mjs.orig ] || cp -a /app/runtime/index.mjs /app/runtime/index.mjs.orig'
docker cp /tmp/snowluma_patch/index.mjs snowluma:/app/runtime/index.mjs
docker exec snowluma sh -c 'chown 1001:1001 /app/runtime/index.mjs'

# 4) 重启 SnowLuma（⚠️ QQ 可能需要手动登录，请留意）
docker restart snowluma

# 5) 验证：出现 group_description 即成功
curl -s -X POST http://127.0.0.1:3100/get_group_info \
  -H "Authorization: Bearer <access_token>" -H 'Content-Type: application/json' \
  -d '{"group_id":970817189}' | grep -o '"group_description":"[^"]*"'
```

补丁是**纯附加**的：`group_memo` 的语义一个字都不改，只新增 `group_description`，
因此不会影响任何依赖 `group_memo` 的既有插件（qqadmin、relationship 等）。
插件会自动优先使用新字段；**没打补丁也照常工作**，只是会在日志里标注出来。

#### ⚠️ 容器重建后需要重新打

`/app/runtime` 在**容器可写层**，不是挂载卷 —— 补丁**扛得住 `docker restart`，
但容器被重建（`docker compose up --force-recreate`、升级镜像、换 tag）就会丢失**。
**每次重建 SnowLuma 容器后，请重跑上面第 1~5 步。**

回滚：

```bash
docker exec snowluma sh -c 'cp -a /app/runtime/index.mjs.orig /app/runtime/index.mjs'
docker restart snowluma
```

#### 补丁的另一个已知边界

非成员群走的 proto（`OidbGroupDetailFlags`）里**根本没有** `description` 字段，
只有 `noticePreview` —— 所以「机器人还没进群」时，**打了补丁也拿不到真群简介**，
只能拿到公告预览。这也是「[进群试用](#推荐组合先进群再校验不合格就退)」
这条路依然有价值的原因之一。

## 平台与接口说明

插件通过 OneBot v11 接口工作，已在 **SnowLuma 1.14.20**（基于 NTQQ 进程注入的 OneBot v11 实现）上实测：

| 用途 | 接口 | 备注 |
| --- | --- | --- |
| 群信息 | `get_group_info`（兼容 `get_group_info_ex`） | 返回 `group_name` / `group_remark` / `group_memo`(群简介，官方描述为「群简介 / 公告预览」) / `member_count` / `max_member_count` / `group_create_time` / `group_level` / `group_all_shut` |
| 申请收件箱 | `get_group_system_msg`（兼容 `get_group_ignored_notifies`） | 补全**群名称**与**邀请人昵称**（群邀请事件本身不携带这两项）；失败时退化为 `get_stranger_info` |
| 群公告 | `_get_group_notice`（兼容 `get_group_notice`） | SnowLuma / NapCat 扩展名 |
| 成员名单 | `get_group_member_list` | 用于提取群主与管理员 |
| 处理邀请 | `set_group_add_request` | `sub_type=invite`；只有 `retcode=0` 才算成功，`retcode=1` 表示「已提交异步处理，结果未知」 |
| 入群后复核 | `get_group_member_list` / `set_group_leave` | 退群用于「先进群后复核不通过」 |
| 发消息 | `send_private_msg` / `send_group_msg` | 使用 OneBot 数组消息段 |

群邀请事件字段（SnowLuma 实测结构）：
`post_type=request`、`request_type=group`、`sub_type=invite`、
`group_id`、`user_id`（邀请人）、`comment`、`flag`，SnowLuma 额外提供 `invited_id`（被邀请的账号）。

> 事件里**没有**群名称、也没有邀请人昵称，所以插件会额外调用一次申请收件箱接口把这两项补齐，
> 再写进日志与「邀请记录」。

> 插件只处理 `sub_type == "invite"` 的群邀请。
> `sub_type == "add"`（机器人主动申请加群）与好友申请都会被忽略。

---

## 目录结构

```
astrbot_plugin_group_invite_auto_approve/
├── main.py                  # Star 插件入口：事件监听 + 管理指令
├── metadata.yaml
├── _conf_schema.json        # 原生配置页 schema
├── requirements.txt
├── core/
│   ├── config.py            # 配置强类型视图 + 写回校验
│   ├── onebot.py            # OneBot 动作调用封装（统一成功/失败/不支持）
│   ├── group_info.py        # 群信息采集与日志格式化
│   ├── decision.py          # 决策引擎（优先级 + AND 条件）
│   ├── notify.py            # 模板渲染与消息/图片发送
│   ├── handler.py           # 事件编排：取信息 → 决策 → 执行 → 通知 → 记录
│   ├── history.py           # 邀请记录（内存 + JSON 落盘）
│   └── page_api.py          # 管理页后端接口
├── pages/invite/            # 管理页前端（index.html / app.js / api.js / styles.css）
├── tools/                   # 本地补丁脚本：给 SnowLuma 暴露真群简介（group_description）
├── .astrbot-plugin/i18n/    # 页面标题与展示名国际化
└── tests/                   # 自测脚本（需在 AstrBot 运行环境内执行）
```

运行期数据写入 `data/plugin_data/astrbot_plugin_group_invite_auto_approve/invite_history.json`。

---

## 测试

六个测试脚本需要在 AstrBot 所在环境（容器）内执行，且**都不会影响正在运行的实例**
（各自是独立进程，只加载本插件、不启动服务）：

```bash
for t in test_plugin_manager_load test_plugin_load test_integration test_page_api test_event_stop_regression test_notice_routing; do
  docker exec astrbot_firefly_2 python3 \
    /AstrBot/data/plugins/astrbot_plugin_group_invite_auto_approve/tests/$t.py
done
```

| 脚本 | 覆盖内容 |
| --- | --- |
| `test_plugin_manager_load.py` | 用 AstrBot 真实的 `PluginManager.load()` 完整加载本插件：注册表、配置注入、实例化、8 条管理页路由、handler 过滤器、`initialize` / `terminate` |
| `test_plugin_load.py` | metadata 解析、版本兼容、Pages 发现、i18n、schema 类型合法性 |
| `test_integration.py` | 用**真实 `AstrBotConfig`** + 精确模拟 aiocqhttp（含 `ActionFailed` 语义）跑完 17 个业务场景 |
| `test_page_api.py` | 用 AstrBot 自己的 `_match_registered_web_api` 与 Quart 测试客户端真实走一遍 `/api/plug/<插件名>/<路径>` 链路 |
| `test_event_stop_regression.py` | 回归测试：处理器优先级确实排在普通插件之前；入群通知被截断时轮询兜底仍能完成复核；两条路径互斥不重复处理 |
| `test_notice_routing.py` | 用 AstrBot 真实的 `AiocqhttpMessageEvent` 构造入群通知/群邀请，验证本插件的过滤器与事件分发能正确放行（排查「通知没触发」时用得上） |

补丁脚本 `tools/patch_snowluma_group_description.py` 自带幂等保护：对已打过补丁的文件
再执行会提示「无需重复执行」，对不上上下文会中止且不改文件。可以这样自检：

```bash
python3 tools/patch_snowluma_group_description.py   # 已打过 -> 提示无需重复执行，退出码 0
```

---

## 关于「群名获取不到」

**这是 QQ / 协议端的限制，不是插件故障。** 实测（SnowLuma 1.14.20）：

```
# 机器人在群里
get_group_info(970817189) -> {"group_name":"萤","member_count":9,...}

# 机器人不在群里（未加入过，或已退出）
get_group_info(1012766700) -> {"group_name":"","member_count":0,...}   # retcode 仍是 0！
get_group_info_ex(1012766700)     -> data: null
get_group_detail_info(1012766700) -> data: null
```

也就是说，`get_group_info` 对**非成员群**依然返回 `retcode 0`，但所有字段都是空值
（「空壳」），协议端根本无法解析出群名。同时群邀请事件本身也**不携带群名称**
（SnowLuma 只比 OneBot 标准多一个 `invited_id`）。因此机器人收到「从未加入过的群」的邀请时，
拿不到群名是预期行为。

插件为此做了三件事：

1. **日志明确写出原因**：会打印
   `! 群资料不可用：机器人不在该群，协议端无法返回群名称 / 群介绍 / 群人数（这是 QQ 侧限制，不是插件故障）`，
   而不是只显示一个让人困惑的 `0`。
2. **补一层申请收件箱**：调用 `get_group_system_msg` / `get_group_ignored_notifies`，
   当协议端的请求扫描正常工作时，这两者**会带上 `group_name` 与 `invitor_nick`**，
   插件会用它们补齐群名与邀请人昵称（失败时退化为 `get_stranger_info` 只补昵称）。
3. **给你选择权**：`conditions.when_info_unavailable` 两种策略 ——
   `approve`（进群试用）/ `skip`（不进群，也不回复邀请人）。
4. **先进群后复核（推荐）**：`conditions.verify_after_join` —— 见下一节。

### 推荐组合：先进群，再校验，不合格就退

这也是本地已有插件 `astrbot_plugin_relationship` 的做法 ——
它在**请求阶段完全不做**关键词 / 人数判断，只在机器人**真的进了群之后**
（`group_increase` 通知里）读 `get_group_info`，此时群资料是完整的，
再按人数 / 黑名单 / 容量判断，不合格就 `set_group_leave` 退群。

本插件提供了同样的能力，配置成：

| 配置项 | 默认值 | 作用 |
| --- | --- | --- |
| `conditions.verify_after_join` | **开启** | 进群后用**真实资料**重新判断关键词与人数 |
| `conditions.when_info_unavailable` | `approve`（**进群试用**） | 资料拿不到时先放它进去看一眼 |
| `conditions.verify_after_join_delay` | `3` | 等协议端同步群资料 |
| `conditions.force_join_max_members` | `50` | 小于该人数的群视为「QQ 可强行拉人」；重复邀请时会复核，`0` 关闭 |
| `conditions.verify_exempt_users` | 空 | 豁免名单：这些人拉群不触发自动退群 |

### ⚠️ 「进群试用」≠「自动同意所有群邀请」

这两者经常被混淆，但完全不同：

| | 进群试用（`approve`） | 自动同意所有群邀请（`auto_approve_all`） |
| --- | --- | --- |
| 适用对象 | 只有**群名/人数确实拿不到**的那部分邀请 | **所有**群邀请 |
| 是否判定条件 | ✅ 进群后用真实资料逐条判断关键词与人数 | ❌ 完全不判断 |
| 不符合条件时 | ✅ **自动退出该群** | ❌ **永不退出**，一直留着 |

所以选「进群试用」是安全的：它只是把「判断」这一步挪到进群之后做，
而不是放弃判断。管理页会把当前选择的**实际效果**写在选项下方，随「入群后重新校验」开关实时变化。

### 本项与「回复邀请人」是两件事

`when_info_unavailable` **只回答一个问题：要不要先放机器人进去看一眼。**
「要不要回复邀请人」不由它控制，两种情形各有一个开关：

| 情形 | 由谁决定 | 会回复邀请人吗 |
| --- | --- | --- |
| **读不到**群名/人数 | 本项：`approve` 进群试用 / `skip` 不进群 | `skip` 时**不回复** |
| **读到了**，但关键词/人数不匹配 | 条件判断本身 | 由 `notify.unmet_enable` 控制 |

两者互不交叉。所以：

- 想让「读不到资料」和「条件不匹配」**都**不回复 → `skip` + 关掉 `notify.unmet_enable`
- 想让「读不到资料」不回复、但「条件不匹配」仍回复 → `skip` + 保持 `notify.unmet_enable` 开启

> 早期版本还有一个 `unmet` 选项（不进群、但回复邀请人），现已移除，只剩上面两项。
> 配置里若残留 `unmet`，加载时会自动按 `skip` 生效，并在启动日志里提示一次。

### 这两项为什么会互相影响

它们**不冲突，但会让彼此失效**，所以插件做了强制归一化：

| 群名/人数拿不到时怎么办 | 机器人会进群吗 | 「入群后复核」能触发吗 |
| --- | --- | --- |
| `approve` 进群试用 | ✅ 会 | ✅ 能 |
| `skip` 不进群 | ❌ 不会（邀请保持待处理） | ❌ **永远不会** |

原因很直接：复核的触发点是 `group_increase` 通知，而机器人**不进群就没有这个通知**。
所以 `skip` 一旦配上「复核开启」，复核开关就是个死配置。

**处理方式：只要 `verify_after_join` 是开启的，`when_info_unavailable` 一律按 `approve`（进群试用）生效。**
这不是静默修改 —— 插件会在三处明确告诉你：

1. **启动日志**：`配置提示：「群名/人数拿不到时怎么办」当前存的是「skip」（不进群），但「入群后重新校验」已开启……因此实际按「进群试用」生效`
2. **管理页**：该项的下拉框被禁用并显示说明横幅
3. **决策日志与记录**：reason 里写明「原始配置选的是不进群，但因已开启复核而按进群试用处理」

想恢复 `skip` 的保守行为，**把「入群后重新校验」关掉**即可，两项策略就能自由选择。
（关闭复核后再选 `approve`，就等于「资料拿不到就直接放进来且不再退出」，这是有风险的状态，插件会在策略摘要里明确警告。）

### ⚠️ 入群事件可能被其它插件截断（已内置两道保障）

AstrBot 的插件处理器循环是这样的（`core/pipeline/process_stage/method/star_request.py`）：

```python
for handler in activated_handlers:
    if event.is_stopped():
        break          # ← 前一个处理器 stop_event() 后，后面的全部被跳过
```

而本地已装的 **`astrbot_plugin_relationship`** 会把「机器人被拉进群」判定为 self-notice
（`user_id == self_id and operator_id != self_id`），处理完就调用 `event.stop_event()`。
结果就是：**排在它后面的插件根本收不到 `group_increase`**，
入群欢迎与「入群后复核」都会静默失效 —— 表现上看起来就像「直接同意、进去就不管了」。

本插件为此做了两道**互相独立**的保障：

| 保障 | 做法 | 覆盖什么 |
| --- | --- | --- |
| **① 监听器高优先级** | `@filter.custom_filter(..., priority=100)`，AstrBot 按 priority 降序执行，插件排到最前 | 正常收到通知，第一时间复核 |
| **② 入群轮询兜底** | 同意邀请后启动一个后台任务，每 2~5 秒查一次群列表，最多 90 秒 | 通知**被截断**、协议端**不发**通知等所有情况 |

两条路径通过 `_claim_join()` 的原子 pop 互斥，只会有一个真正执行，不会重复发欢迎消息或重复复核。
轮询超时后会放弃并清理状态，不会误判。

日志上可以区分是哪条路径生效的：

```
[group_invite] 入群后补全群聊完整信息                      ← ① 收到通知
[group_invite] 轮询确认机器人已进入群 xxx（未收到 group_increase 通知，可能是被其它插件截断了）  ← ② 兜底
```

行为链路：

```
收到群邀请
  -> get_group_info 返回空壳（机器人不在群里，拿不到群名/人数）
  -> 按策略 approve：同意进群
  -> 收到 group_increase 通知
  -> 等待 3 秒，重新读取完整资料（群名/群介绍/群公告/人数/群主/管理员）
  -> 用真实资料重新判断关键词与人数
       满足   -> 留在群里 + 发送欢迎消息
       不满足 -> 私聊告知邀请人原因 -> set_group_leave 退群
```

同一条链路也适用于「不请自来」的入群（QQ 自动拉小群），只是复核通过时不发欢迎消息。

### ⚠️ QQ 会「强行」把机器人拉进小群

**QQ 有一条硬性规则：少于 50 人的群，不需要机器人同意就会被直接拉进去。**
这类入群不是「重复邀请」，而是机器人**根本没机会审批**就被放进去了 ——
随之而来的请求事件看起来就像一次重复邀请。

如果不处理这类入群，「人数下限」这类条件就形同虚设
（机器人被拉进 9 人小群，却永远不会因为「人数不足」退出）。

所以本插件的入群复核**同时覆盖两种情况**：

| 入群来源 | 是否复核 | 复核通过 | 复核不通过 |
| --- | --- | --- | --- |
| 本插件同意了邀请 | ✅ | 留在群里 + **发欢迎消息** | 通知邀请人后退群 |
| **不请自来**（收到 `group_increase`：QQ 自动拉小群、他人手动拉群） | ✅ | 留在群里 + **发欢迎消息** | 通知操作者后退群 |
| **重复邀请**（机器人已在群里，且群人数小于阈值） | ✅ | 留在群里 + **发欢迎消息** | 通知邀请人后退群 |

> **欢迎消息的判定标准是「机器人最终是否留在群里」，而不是「谁点的同意」。**
> 三条路径都可能发，因此加了一个 10 分钟的同群去重窗口 ——
> 入群通知与重复邀请同时到达时也只发一次，不会刷屏。
> 复核不通过（退群）时自然不会发。

### 「重复邀请」也要复核

QQ 强行拉人之后，还会再送来一个请求事件。如果机器人在**收到通知之前**就已经在群里了，
`group_increase` 那条路就走不到（没有新的入群事件），于是「重复邀请」成了唯一的线索。

所以收到「重复邀请」时，插件会先看群人数：

| 群人数 | 判断 | 行为 |
| --- | --- | --- |
| **小于** `force_join_max_members`（默认 50） | 属于「QQ 可强行拉人」的群 | 按配置条件**重新复核**，不合格 → 私聊说明原因 → 退群 |
| 不小于阈值 | 只可能是正常审批 / 管理员拉进去的 | 不质疑，直接回「已进群」 |

阈值 `conditions.force_join_max_members` 设为 `0` 可关闭这个行为；
人数未知（接口返回 0）时不做判断，避免误退。

> 这也是 `astrbot_plugin_relationship` 的做法 —— 它对所有「被拉进群」都做检查，
> 只豁免审批员。

**安全边界**（避免误退）：
- `auto_approve_all`（自动同意所有群邀请）开启时**永不退群**。
- 所有条件开关都关闭时**不做任何处理**，也不会退群。
- **豁免名单**：`conditions.verify_exempt_users` 里填的 QQ 号（以及 AstrBot 配置里填的
  **数字**管理员账号）拉群时不做复核，不会被退。
- 「重复邀请」只在群人数**小于阈值**时才复核；正常加入的大群不会被质疑。用于避免自己人手动拉群也被退掉。
- 退群前会先按「未满足条件」的配置私聊通知操作者（可在配置里关掉）。
- 同一次入群只处理一次（入群通知与轮询兜底之间做了去重），不会重复退群 / 重复发消息。

## 关于「群介绍永远是群公告」

**结论：这是协议端 SnowLuma 的字段映射问题，已通过本地补丁修复。**

不是 AstrBot 的问题，不是「QQ 群主没填简介」，也不是本插件取错字段。

### 原因：SnowLuma 把公告和群简介合并成了一个字段

`/app/runtime/index.mjs`（SnowLuma 1.14.20）：

```js
memo: raw.info?.announcement || raw.info?.description || "",
//    ^^^^^^^^^^^^^^^^^^^^^^^ 优先取【公告】，公告为空才回落到【群简介】
```

而 NTQQ 的群数据里这两个是**独立字段**（同文件里的 protobuf 解码结果）：

```js
const _result = {
    groupName: _f4,
    description: _f5,      // ← 真正的群简介，被 SnowLuma 丢弃
    question: _f6,
    announcement: _f7,     // ← SnowLuma 只用了这个
    ...
};
```

实测 22 个群完全吻合：**18 个有公告的群** memo 全是某条公告的截断前缀
（`公告[0][:72] == memo` 逐字符精确成立），**2 个零公告的群** memo 才是真群简介 ——
因为公告为空，`||` 回落到了 `description`。

### 解决：本地补丁让 SnowLuma 额外返回 `group_description`

补丁脚本：`tools/patch_snowluma_group_description.py`

**纯附加**：`group_memo` 的语义一个字都不动，只新增一个 `group_description` 字段，
因此不会影响任何依赖 `group_memo` 的既有插件。

改动 4 处（`fetchGroupList` 存下 `description`，OneBot 的 `get_group_list` /
`get_group_info` 两个分支输出 `group_description`）。效果：

```jsonc
// 补丁前
{"group_name": "萤", "group_memo": "【napcat新版功能缺失的恢复方法】…docker exec napcat"}

// 补丁后
{"group_name": "萤",
 "group_memo":        "【napcat新版功能缺失的恢复方法】…docker exec napcat",  // 公告预览，语义不变
 "group_description": "来来米哈游！"}                                        // ← 真正的群简介
```

插件会自动优先使用 `group_description` 作为群介绍，日志里会标注来源：

```
群介绍      : 来来米哈游！
              └─ ✅ 来自真正的群简介字段（协议端 group_description）
```

**未打补丁时**插件照常工作，只是会标注出来并提示可以打补丁：

```
群介绍      : 【napcat新版功能缺失的恢复方法】…
              └─ ⚠ 该项其实是群公告第 1 条的截断预览：协议端把「公告」映射到了
                 group_memo（SnowLuma: announcement || description），真群简介未暴露
                 —— 可给本地 SnowLuma 打补丁取得（见 README）
```

### ⚠️ 补丁的注意事项

| 事项 | 说明 |
| --- | --- |
| **持久性** | `/app/runtime` 在**容器可写层**，不是挂载卷 —— 补丁**扛得住容器重启，但容器被重建（`--force-recreate`、升级镜像）就会丢失**，需要重新执行 `tools/` 下的脚本 |
| **重启后 QQ 可能要手动登录** | 本次实测重启后 QQ 未自动登录，需要人工登录一次 |
| **非成员群仍拿不到** | `OidbGroupDetailFlags` proto 里**没有** description 字段，只有 `noticePreview`，所以「机器人不在群里」时依旧只能拿到公告预览 |
| **第三方软件** | 属于本地非官方补丁；SnowLuma 升级后请重新执行脚本并验证（脚本会做匹配数校验，不匹配会安全中止） |
| **可回滚** | 容器内保留了 `index.mjs.orig`：`docker exec snowluma sh -c 'cp -a /app/runtime/index.mjs.orig /app/runtime/index.mjs'` 后重启即可 |

用法与回滚步骤见脚本头部的 docstring。

## 关于「处理群邀请失败：handle async message fail」

现象：

```
[ERRO] [group_invite] 处理群邀请失败 (approve=True):
       retcode=100 OIDB error 120161001 on 0x10c8_1: handle async message fail
```

**这是 SnowLuma 的 flag 处理路径问题，插件已自动绕过。**

### 原因

SnowLuma 处理审批时分三条路（`handleGroupAddRequest`）：

```js
// ① flag 以 slreq: 开头（canonical）—— 直接使用 flag 里的 eventType
if (parsed.kind === "canonical") {
    await applyGroupRequest(bridge, parsed, approve, reason);   // 群邀请的 eventType = 1
    return;
}
// ③ legacy 形式 invite:<群号>:<uid> —— 先去找「卡片里捕获到的序列」
if (parsed.requestType === "invite") {
    const cardSequence = getGroupInviteCardSequence(parsed.groupId);
    if (cardSequence) { /* 用 eventType=2 */ return; }
}
```

而 QQ 服务器**只接受**「私聊群邀请卡片里的 msgseq + `eventType=2`」。
SnowLuma 源码注释原文：

> a private "qun.invite" ark card ... carries **the only sequence the server accepts**
> when the bot later approves the invite via 0x10c8 — applied with
> **eventType=2 / filtered=false**. The MSF invite push (PkgType 87) never carries it.
> See **issue #125**.

也就是说：**走到 canonical 分支就注定失败**（它用的是 eventType=1），
只有 legacy 分支才会去查卡片序列、用服务器接受的 eventType=2。

「机器人退群 / 被踢之后被重新邀请」这类场景尤其容易碰上 ——
这种重新邀请往往只发 MSF push，事件里的 flag 就是 canonical 形式。

实测对照（同一台 SnowLuma 1.14.20）：

| 插件传的 flag | SnowLuma 判定 | 结果 |
| --- | --- | --- |
| `slreq:1:1790559064250306:702209896:1:0` | `eventType=1` | ❌ `handle async message fail` |
| `invite:702209896:3275304521` | `eventType=2` | ✅ `{"status":"ok","retcode":0}` |

### 插件怎么处理的

`approve_request` 在收到**以 `slreq:` 开头**的 flag 且审批失败时，
会**自动换成 legacy 形式 `invite:<群号>:<邀请人QQ>` 重试一次**：

```
[group_invite] 协议端拒绝了 canonical flag（... handle async message fail）；
               改用 legacy 形式重试：invite:702209896:3275304521
[group_invite] legacy flag 重试成功（协议端走 eventType=2 的卡片路径）
```

这样做的理由：

- **只对 `slreq:` 前缀生效** —— 这是 SnowLuma 专有格式，其它协议端（NapCat 等）
  的数字 flag 不会被误重试，标准行为不受影响；
- **canonical 先试** —— 万一 SnowLuma 以后修好了这条路径，也照常能用；
- **两次都失败就如实报错**，不会谎报成功、也不会给邀请人发「已同意进群~」。

### 仍然存在的边界

legacy 重试依赖 SnowLuma **已经捕获到该群的邀请卡片序列**。
如果邀请自始至终只走了 MSF push（没有卡片），两条路都会失败 ——
这是 QQ 服务端的限制（SnowLuma issue #125），插件侧无法绕过。
这种情况会保持邀请待处理，不会误报成功。

## 常见问题

**Q：机器人进群了，为什么没发「我是机器人，欢迎使用」？**
早期版本只在「本插件同意了邀请」时才发欢迎消息，导致 QQ 强行拉小群、
或「重复邀请」补录的入群都没有欢迎语。现在改为
**只要机器人最终留在群里就会发**（含上述三种来源），并带 10 分钟同群去重。
若仍没有，请确认「当同意进群后发送消息」开关是开的。

**Q：审批时报 `OIDB error 120161001 ... handle async message fail`？**
SnowLuma 对 `slreq:` 开头的 flag 会直接用 eventType=1，而 QQ 服务器只接受
「邀请卡片序列 + eventType=2」。插件已自动改用 legacy flag 重试，详见上方
「关于『处理群邀请失败：handle async message fail』」。

**Q：群介绍显示的是群公告内容？**
SnowLuma 把公告和群简介合并进了 `group_memo`（`announcement || description`），群简介被公告覆盖。**用 `tools/patch_snowluma_group_description.py` 打个本地补丁即可拿到真群简介**，详见上方「关于『群介绍永远是群公告』」。未打补丁时插件会自动退回旧字段并标注出来。

**Q：群公告一直读不到？**
群公告走的是 QQ 群网页接口，需要机器人**已经在群内**。收到邀请时还不在群里，
所以关键词无法匹配公告，只能匹配群名与群介绍。若关键词只写在公告里，
建议同时把关键词加到群名或群介绍，或直接开启「自动同意所有群邀请」。

**Q：群人数显示 0，导致人数条件不满足？**
机器人不在该群里时，协议端对非成员群恒返回 0 —— 群人数不可能是 0，这只是「读不到」。
插件会把这种情况判定为「**无法判定**」而不是「不满足」，并按 `when_info_unavailable`
策略走「进群试用」：先进群拿到真实人数，再复核，不合格就退。

**Q：邀请人收到了「不满足条件」，但其实插件只是没查到信息？**
升级后日志与私聊文案会分别说明是「真的不满足」还是「群资料不可用」。
如果希望这种情况直接进群，把 `when_info_unavailable` 设为 `approve`。

**Q：机器人已经在这个群里了，又收到一次邀请会怎样？**
插件会先用 `get_group_list` 确认成员身份，已在群里就**跳过** `set_group_add_request`
（否则 QQ 会返回 `OIDB error 120161001: handle async message fail`），
同时仍然通知邀请人「已同意进群~」，因为对方想要的结果已经达成。

**Q：为什么操作失败了却没给邀请人发消息？**
通知按**实际执行结果**发送，不会谎报成功。`set_group_add_request` 失败时只记错误日志，
避免让邀请人以为机器人马上会进群。

**Q：关键词条件开启了但没配关键词？**
该条件恒不满足，日志与私聊消息中都会提示「已开启关键词条件，但没有配置任何关键词」。

**Q：改了 `main.py` 需要重启吗？**
需要重载插件。只改 `pages/` 下的静态资源刷新页面即可。
