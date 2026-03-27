[![AstrBot](https://img.shields.io/badge/AstrBot-Plugin-ff69b4?style=for-the-badge)](https://github.com/AstrBotDevs/AstrBot)

# 根据群信息自动进群 (group_invite_auto_approve)

AstrBot 自动同意加群插件 - 根据群名称或群简介关键词自动同意加群邀请

## 📖 功能简介

当机器人收到群聊邀请时，该插件会自动：
1. 获取目标群聊的**群名称**和**群介绍**
2. 检查是否包含配置的**关键词**
3. 如果包含关键词，则自动同意进群邀请
4. 可选：私聊回复邀请人
5. 可选：进群后自动发送欢迎消息

## ✨ 特性

- ✅ 支持自定义关键词列表（群名/群介绍任意包含即触发）
- ✅ 支持检查群介绍（可单独关闭）
- ✅ 自定义私聊回复消息
- ✅ 自定义群欢迎消息
- ✅ 支持消息换行（使用 `\n`）
- ✅ 自动忽略自己发起的邀请
- ✅ 失败自动重试
- ✅ 完整的日志输出

## 📦 安装方法

### 方法一：通过 插件市场 安装（推荐）

1. 打开 AstrBot WebUI
2. 进入 **插件市场** 页面
3. 在插件市场搜索 `group_invite_auto_approve`
4. 点击安装

### 方法二：通过 仓库链接 安装

1. 打开 AstrBot WebUI
2. 进入 **AstrBot 插件** 页面
3. 点击右下角 **+**
4. 从链接安装
5. 输入 **https://github.com/XingYunStar/astrbot_plugin_group_invite_auto_approve**

## ⚙️ 配置说明

在 AstrBot WebUI 的插件管理页面，找到本插件并点击 **配置** 按钮，可修改以下配置：

| 配置项 | 类型 | 默认值 | 说明 |
|--------|------|--------|------|
| keywords | 列表 | ["原神", "星穹铁道", "绝区零"] | 触发自动进群的关键词列表。群名或群介绍包含任意关键词即自动进群 |
| auto_join | 布尔 | true | 是否自动进群。关闭后只发送私聊通知，不会自动进群 |
| check_group_memo | 布尔 | true | 是否检查群介绍。关闭后只检查群名 |
| group_welcome_message | 字符串 | 我是机器人，欢迎使用。 | 进群后自动发送的欢迎消息。使用 \n 换行 |
| private_reply_message | 字符串 | 已同意进群~ | 同意邀请后私聊回复邀请人的消息。使用 \n 换行 |
| enable_log | 布尔 | true | 是否输出详细日志，便于调试 |
| delay_after_join | 整数 | 2 | 进群成功后延迟多少秒再发送欢迎消息（秒），部分协议端进群需要时间同步 |
| retry_on_failure | 布尔 | true | 发送消息失败时是否重试一次 |
| ignore_bot_self | 布尔 | true | 是否忽略机器人自己发起的邀请 |

### 消息换行示例

在配置消息时，可以使用 \n 实现换行：

欢迎入群！\n请遵守群规，文明交流。

实际发送效果：
欢迎入群！
请遵守群规，文明交流。

## 🚀 使用命令

| 命令 | 说明 |
|------|------|
| /invite_config | 查看当前配置 |

## 📝 工作流程

收到群邀请
    ↓
获取群名称和群介绍
    ↓
检查是否包含关键词（或关系）
    ↓
├── 包含 → 同意邀请 → 私聊通知邀请人 → 延迟等待 → 发送群欢迎消息
│
└── 不包含 → 不处理，记录日志

## 🔧 开发信息

- 作者：星陨
- 仓库：https://github.com/XingYunStar/astrbot_plugin_group_invite_auto_approve
- 许可证：MIT

## 🤝 贡献

欢迎提交 Issue 和 Pull Request！

## 💬 反馈

如有问题或建议，请在 GitHub Issues 中反馈。

## ⭐ Star

如果这个插件对你有帮助，欢迎给个 Star ⭐
