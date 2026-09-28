"""astrbot_plugin_group_invite_auto_approve - 群邀请自动处理。

功能概览：

1. 收到群邀请时，在日志中打印该群的全部可用信息（群名 / 群介绍 / 群公告 /
   人数 / 建群时间 / 等级 / 全员禁言 …）。
2. 「自动同意所有群邀请」开关，优先级高于其它全部条件。
3. 「满足以下要求时自动同意」：关键词与人数两个开关，判断关系为 AND；
   全部关闭时不处理群邀请。
4. 可配置对邀请人的私聊通知（同意 / 未满足条件 + 自定义额外消息）。
5. 同意进群后在群内发送欢迎消息，可同时发送一张本地图片或网络图片。
6. 提供 WebUI 插件管理页（``pages/invite``），可视化读写配置并查看邀请记录。
"""

from __future__ import annotations

from astrbot.api import logger
from astrbot.api.event import filter
from astrbot.api.star import Context, Star
from astrbot.core.config.astrbot_config import AstrBotConfig
from astrbot.core.platform.sources.aiocqhttp.aiocqhttp_message_event import (
    AiocqhttpMessageEvent,
)
from astrbot.core.star.filter.custom_filter import CustomFilter
from astrbot.core.star.filter.permission import PermissionType
from astrbot.core.star.filter.platform_adapter_type import PlatformAdapterType
from astrbot.core.star.star_tools import StarTools

from .core.config import PluginConfig
from .core.handler import InviteHandler
from .core.history import InviteHistory
from .core.page_api import PageController

PLUGIN_NAME = "astrbot_plugin_group_invite_auto_approve"
LOG_PREFIX = "[group_invite]"

#: 处理器优先级。AstrBot 按 priority 降序执行处理器，且前一个处理器调用
#: ``event.stop_event()`` 后，后面的处理器会被直接跳过。群邀请/入群这类事件
#: 很容易被别的插件截断，所以这里用高优先级保证先执行。
HANDLER_PRIORITY = 100


class GroupControlEventFilter(CustomFilter):
    """只放行「群邀请请求」与「机器人入群通知」。

    这两类事件在 AstrBot 中是普通的消息事件（``group_id`` 存在时类型为
    ``GROUP_MESSAGE``）。如果只按消息类型过滤，插件会参与到每一条群消息的
    唤醒判定里，因此这里精确匹配 OneBot 原始事件类型，把影响面收敛到最小。
    """

    def filter(self, event, cfg) -> bool:
        raw = getattr(event.message_obj, "raw_message", None)
        if not isinstance(raw, dict):
            return False

        post_type = raw.get("post_type")
        if post_type in ("request", "notice"):
            # DEBUG 级诊断：把「到了插件这一层」的请求/通知事件原样打出来。
            # 排查「入群通知没触发」这类问题时，把 AstrBot 的 log_level 调到 DEBUG
            # 就能看到事件到底有没有送达插件。
            logger.debug(f"{LOG_PREFIX} 事件送达插件 {post_type}: {raw}")

        if post_type == "request":
            return raw.get("request_type") == "group"
        if post_type == "notice":
            return raw.get("notice_type") == "group_increase"
        return False


class GroupInviteAutoApprovePlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.context = context
        self.cfg = PluginConfig(config)
        self.data_dir = StarTools.get_data_dir(PLUGIN_NAME)
        self.history = InviteHistory(self.data_dir)
        self.handler = InviteHandler(
            self.cfg,
            self.history,
            exempt_users=self._exempt_users(),
        )

        # 注册管理页后端接口（``/api/plug/<插件名>/...``）
        self.page = PageController(context, self.cfg, self.history)
        self.page.register_routes()

    def _exempt_users(self) -> set[int]:
        """收集豁免账号：AstrBot 配置里的**数字**管理员 + 插件自己的豁免名单。

        AstrBot 的 ``admins_id`` 里可能填的是控制台用户名（如 ``astrbot``）
        而不是 QQ 号，这类条目会被忽略 —— 所以插件另外提供
        ``conditions.verify_exempt_users`` 让使用者显式指定 QQ 号
        （该名单由 ``InviteHandler`` 自己从配置读取，这里只补充管理员）。
        """
        ids: set[int] = set()
        try:
            raw = self.context.get_config().get("admins_id", []) or []
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"{LOG_PREFIX} 读取 admins_id 失败: {exc}")
            raw = []
        for item in raw:
            text = str(item).strip()
            if text.isdigit():
                ids.add(int(text))
        return ids

    async def initialize(self) -> None:
        policy = self.cfg.policy_summary()
        logger.info(f"{LOG_PREFIX} 群邀请自动处理插件已加载")
        logger.info(f"{LOG_PREFIX} 当前策略：{policy['label']} —— {policy['detail']}")
        if self.cfg.info_policy_legacy_migrated:
            logger.warning(
                f"{LOG_PREFIX} 配置迁移：「群名/人数拿不到时怎么办」中已废弃的取值 "
                f"「unmet」已按「不进群，也不回复邀请人」生效；"
                f"下次在管理页保存后会写回新值。"
            )
        if self.cfg.info_policy_overridden:
            logger.warning(
                f"{LOG_PREFIX} 配置提示：「群名/人数拿不到时怎么办」当前存的是 "
                f"「{self.cfg.info_policy_raw}」（不进群），但「入群后重新校验」已开启。"
                f"机器人不进群就永远拿不到群名 / 人数，复核也无从触发，"
                f"因此实际按「进群试用」生效：先进群，再用真实资料复核，"
                f"不合格自动退出。"
            )
        if self.handler.exempt_users:
            logger.info(
                f"{LOG_PREFIX} 入群复核豁免账号："
                f"{'、'.join(str(i) for i in sorted(self.handler.exempt_users))}"
            )
        logger.info(
            f"{LOG_PREFIX} 管理页：WebUI → 插件 → 群邀请自动处理 → 打开 Pages"
        )

    async def terminate(self) -> None:
        self.handler.cancel_join_watches()
        logger.info(f"{LOG_PREFIX} 群邀请自动处理插件已卸载")

    # ------------------------------------------------------------------
    # 核心监听
    # ------------------------------------------------------------------
    @filter.platform_adapter_type(PlatformAdapterType.AIOCQHTTP)
    @filter.custom_filter(GroupControlEventFilter, priority=HANDLER_PRIORITY)
    async def on_group_control_event(self, event: AiocqhttpMessageEvent):
        """监听群邀请请求与机器人入群通知。

        必须抢在其它插件前面：AstrBot 的处理器循环一旦发现 ``event.is_stopped()``
        就 ``break``，而本地已装的 ``astrbot_plugin_relationship`` 会在
        「机器人被拉进群」时调用 ``event.stop_event()``，排在它后面的处理器
        会完全收不到这个事件（入群欢迎与入群后复核都会失效）。
        这里用 priority 把自己排到最前，另外 ``InviteHandler`` 还有轮询兜底。
        """
        try:
            await self.handler.dispatch(event)
        except Exception as exc:  # noqa: BLE001 - 单条事件失败不应影响插件
            logger.exception(f"{LOG_PREFIX} 处理群事件失败: {exc}")

    # ------------------------------------------------------------------
    # 管理指令
    # ------------------------------------------------------------------
    @filter.permission_type(PermissionType.ADMIN)
    @filter.command("群邀请状态")
    async def invite_status(self, event: AiocqhttpMessageEvent):
        """查看当前群邀请自动处理策略与统计"""
        policy = self.cfg.policy_summary()
        stats = self.history.stats()
        lines = [
            "【群邀请自动处理】",
            f"当前策略：{policy['label']}",
            f"说明：{policy['detail']}",
            "",
            "已开启的条件："
            + (
                "、".join(
                    name
                    for name, on in (
                        ("关键词", self.cfg.keyword_enable),
                        ("人数", self.cfg.member_count_enable),
                    )
                    if on
                )
                or "无"
            ),
        ]
        if self.cfg.keyword_enable:
            lines.append(f"关键词：{'、'.join(self.cfg.keywords) or '（未配置）'}")
        if self.cfg.member_count_enable:
            lines.append(
                f"人数：大于 {self.cfg.min_member_count} 且少于 "
                f"{self.cfg.max_member_count}"
            )
        lines += [
            "",
            f"累计群邀请：{stats['invites']}",
            f"已同意：{stats['approved']}",
            f"已拒绝：{stats['rejected']}",
            f"未处理：{stats['ignored']}",
        ]
        yield event.plain_result("\n".join(lines))
