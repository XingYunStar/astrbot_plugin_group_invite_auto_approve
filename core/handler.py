"""群邀请事件的处理编排。

把「取信息 -> 决策 -> 执行 -> 通知 -> 记录」串起来，主插件类只负责事件分发。
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

from astrbot.api import logger

from .config import PluginConfig
from .decision import (
    MODE_ALREADY_MEMBER,
    MODE_JOIN,
    Decision,
    decide,
)
from .group_info import (
    GroupInfo,
    check_membership,
    fetch_group_info,
    fetch_invite_context,
    log_group_info,
)
from .history import InviteHistory
from .notify import (
    approve_request,
    build_variables,
    compose_unmet_message,
    leave_group,
    reject_request,
    render,
    send_group,
    send_private,
)

LOG_PREFIX = "[group_invite]"

#: 「刚同意进群」状态的保留时间，用于把群邀请与随后的入群关联起来。
WELCOME_TTL_SECONDS = 600

#: 同一个群在这个时间内只发一次欢迎消息，避免「入群通知 + 重复邀请」两条路各发一次。
WELCOME_DEDUPE_SECONDS = 600

#: 入群轮询兜底：首次间隔 / 间隔上限 / 总时长（秒）。
JOIN_WATCH_INTERVAL = 2.0
JOIN_WATCH_INTERVAL_MAX = 5.0
JOIN_WATCH_TIMEOUT = 90.0


class InviteHandler:
    """群邀请 + 入群欢迎的业务实现。"""

    def __init__(
        self,
        cfg: PluginConfig,
        history: InviteHistory,
        exempt_users: set[int] | None = None,
    ) -> None:
        self.cfg = cfg
        self.history = history
        #: 豁免名单：这些人拉群时不做「不合格就退群」。
        #: = 插件配置里的名单 ∪ main.py 注入的 AstrBot 数字管理员。
        #: 以配置为准，注入是叠加，保证单独构造 handler（如测试）也不会漏掉配置。
        self.exempt_users: set[int] = set(exempt_users or set()) | cfg.verify_exempt_users
        #: group_id -> (同意时间, 邀请人 QQ)，用于把入群与刚同意的邀请关联起来
        self._pending_welcome: dict[int, tuple[float, int]] = {}
        #: 已经处理过的入群，用于去重（通知与轮询可能都到）
        self._handled_joins: dict[int, float] = {}
        #: group_id -> 上次发欢迎消息的时间，用于避免重复欢迎
        self._welcomed: dict[int, float] = {}
        #: 入群轮询兜底任务，插件卸载时需要取消
        self._join_watch_tasks: set[asyncio.Task] = set()

    # ------------------------------------------------------------------
    # 事件入口
    # ------------------------------------------------------------------
    async def dispatch(self, event: Any) -> None:
        """按原始事件类型分发（群邀请请求 / 机器人入群通知）。"""
        raw = getattr(event.message_obj, "raw_message", None)
        if not isinstance(raw, dict):
            return
        post_type = raw.get("post_type")
        if post_type == "request":
            await self.on_request_event(event)
        elif post_type == "notice":
            await self.on_group_increase_notice(event)

    async def on_request_event(self, event: Any) -> None:
        """处理 OneBot v11 ``request`` 事件（仅群邀请）。"""
        raw = getattr(event.message_obj, "raw_message", None)
        if not isinstance(raw, dict):
            return

        if raw.get("post_type") != "request":
            return
        if raw.get("request_type") != "group":
            return

        sub_type = str(raw.get("sub_type") or "invite")
        if sub_type != "invite":
            # sub_type == "add" 表示机器人主动申请加群，不在本插件职责内
            return

        client = getattr(event, "bot", None)
        if client is None:
            logger.warning(f"{LOG_PREFIX} 事件缺少 bot 客户端，忽略群邀请")
            return

        group_id = _to_int(raw.get("group_id"))
        inviter_id = _to_int(raw.get("user_id"))
        flag = str(raw.get("flag") or "")
        comment = _to_text(raw.get("comment"))
        invited_id = _to_int(raw.get("invited_id")) or _to_int(raw.get("invited_uin"))
        self_id = _to_int(event.get_self_id())

        if not group_id:
            logger.warning(f"{LOG_PREFIX} 群邀请缺少 group_id，已忽略: {raw}")
            return

        if invited_id and self_id and invited_id != self_id:
            logger.info(
                f"{LOG_PREFIX} 忽略非本账号({self_id})的群邀请：invited_id={invited_id}"
            )
            return

        logger.info(
            f"{LOG_PREFIX} 收到群邀请：群 {group_id}，邀请人 {inviter_id}，"
            f"验证信息「{comment or '无'}」"
        )

        # 1) 采集群聊全部可用信息
        info = await fetch_group_info(
            client,
            group_id,
            fetch_notice=self.cfg.fetch_notice,
            fetch_roster=False,  # 尚未入群，成员名单必然取不到
        )

        # 1.1) 判断机器人是否已经在该群（重复邀请 / 邀请已过时）
        info.is_member = await check_membership(client, group_id)

        # 1.2) 群邀请事件不携带群名称与邀请人昵称，从申请收件箱补齐
        info.inviter_id = inviter_id
        invite_ctx = await fetch_invite_context(
            client, group_id=group_id, flag=flag, inviter_id=inviter_id
        )
        if invite_ctx.found:
            info.inviter_name = invite_ctx.inviter_nick
            if not info.name and invite_ctx.group_name:
                info.name = invite_ctx.group_name
                info.extra["群名称来源"] = invite_ctx.source
            if not inviter_id and invite_ctx.inviter_id:
                inviter_id = invite_ctx.inviter_id
                info.inviter_id = inviter_id
            if invite_ctx.checked is False:
                info.extra["申请状态"] = "收件箱显示该申请仍待处理 (checked=false)"
        if info.is_member:
            info.extra["成员状态"] = "机器人已在该群内（可能是重复邀请）"

        # 2) 在日志打印此群所有信息
        if self.cfg.log_group_detail:
            log_group_info(info, prefix=LOG_PREFIX)
        else:
            logger.info(
                f"{LOG_PREFIX} 群 {group_id} 名称「{info.name or '未知'}」"
                f" 人数 {info.member_count}"
            )

        # 3) 决策
        # 「机器人已在该群内」时不需要再调用审批接口。但要区分两种情形：
        #   (a) 群人数 >= 阈值：机器人只可能是正常审批/管理员拉进去的，
        #       不去质疑它，直接按「已进群」回应；
        #   (b) 群人数 <  阈值：QQ 对少于 50 人的群可以不经同意直接拉人，
        #       「重复邀请」很可能就是被强行拉入的结果 —— 这种要按配置条件复核，
        #       不合格就退群，否则「人数下限」等条件对小群形同虚设。
        # （曾经为了消除「决策说未满足、却回复已同意」的矛盾，把 (b) 也跳过了，
        #   结果是小群永远不会被清理。现在改为条件只服务复核、不影响回执文案。）
        force_joinable = self._is_force_joinable(info)
        if info.is_member:
            decision = Decision(
                approve=True,
                mode=MODE_ALREADY_MEMBER,
                reason=(
                    "机器人已在该群内（重复邀请或邀请已过时），本次无需审批；"
                    + (
                        f"该群人数 {info.member_count} 少于 "
                        f"{self.cfg.force_join_max_members}，属于「QQ 可强行拉人」的群，"
                        f"将按配置条件复核，不合格则退群"
                        if force_joinable
                        else "群人数不低于「可强行拉人」的阈值，不判断关键词 / 人数"
                    )
                ),
            )
            if force_joinable:
                # 顺便把条件明细也算出来，方便日志里看到复核依据
                decision.conditions = decide(self.cfg, info).conditions
        else:
            decision = decide(self.cfg, info)

        logger.info(
            f"{LOG_PREFIX} 决策：{decision.mode} -> "
            f"{_approve_text(decision.approve)}；{decision.reason}"
        )
        for condition in decision.conditions:
            if not condition.enabled:
                continue
            state = (
                "满足"
                if condition.passed
                else ("不满足" if condition.evaluable else "无法判定")
            )
            logger.info(
                f"{LOG_PREFIX}   条件[{condition.label}] {state}：{condition.detail}"
            )

        # 4) 执行
        variables = build_variables(
            info,
            inviter_id=inviter_id,
            inviter_name=info.inviter_name,
            reasons=decision.reasons_text(),
            keywords=self.cfg.keywords,
        )
        actions: list[str] = []
        approved_by_us = False
        #: 邀请人最终看到的结果：True 已进群 / False 未进群 / None 未处理
        outcome: bool | None = None
        approve_or_reject_failed = False
        #: 非空 => 机器人此刻已经在这个群里，收尾时按「已同意进群」流程补发欢迎消息。
        #: 「重复邀请」与「本插件同意邀请」两条路最终都汇到同一个收尾块，
        #: 因此不需要各自写一遍欢迎 / 通知逻辑。
        welcome_reason = ""

        if info.is_member:
            # 机器人已经在该群里，再调用 set_group_add_request 会被 QQ 拒绝
            # （OIDB error 120161001: handle async message fail），且毫无意义。
            # 邀请人想要的结果（机器人在群里）已经达成，因此对邀请人按「已进群」回应。
            outcome = True
            actions.append("机器人已在该群内，跳过重复同意")
            logger.info(
                f"{LOG_PREFIX} 机器人已在群 {group_id} 内，跳过同意操作"
                f"（重复邀请或邀请已过时）"
            )
            # 已经在群里，不会再收到 group_increase 通知，无需登记待欢迎状态。
            #
            # 「重复邀请」很可能是 QQ 强行拉人的结果：QQ 对少于 50 人的群
            # 不需要机器人同意就会直接拉进去，随后才送来请求事件。
            # 这种群要按配置条件复核，不合格就退 —— 否则「人数下限」等条件
            # 对被强行拉入的小群完全无效。
            if force_joinable:
                logger.info(
                    f"{LOG_PREFIX} 群 {group_id} 人数 {info.member_count} < "
                    f"{self.cfg.force_join_max_members}，属于「QQ 可强行拉人」的群；"
                    f"本次为重复邀请，按配置条件复核"
                )
                left = await self._verify_after_join(
                    client, info, inviter_id, approved_by_us=False
                )
                if left:
                    # 已退群：把结果交给统一收尾，由它按「未满足条件」通知邀请人
                    outcome = False
                    actions.append("该群可被 QQ 强行拉人，复核不通过，已退群")
                else:
                    welcome_reason = "重复邀请：该群可由 QQ 强行拉人，复核通过后保留"
            # 群人数不低于阈值：机器人只可能是正常审批 / 管理员拉进去的，
            # 不质疑它的存在，也不补发欢迎消息（避免对老群刷屏）。
        elif decision.approve is True:
            if await approve_request(
                client, flag, group_id=group_id, inviter_id=inviter_id
            ):
                actions.append("已同意群邀请")
                approved_by_us = True
                outcome = True
                self._mark_pending_welcome(group_id, inviter_id)
                # 兜底：万一 group_increase 通知被别的插件截断，靠轮询也能补上复核
                self.spawn_join_watch(client, group_id, inviter_id)
                # 欢迎消息等真正入群后由 _process_join 发送 —— 同一个 _send_welcome
            else:
                actions.append("同意群邀请失败")
                approve_or_reject_failed = True
        elif decision.approve is False:
            reason = decision.reasons_text()
            if await reject_request(client, flag, reason=reason):
                actions.append("已拒绝群邀请")
                outcome = False
            else:
                actions.append("拒绝群邀请失败")
                approve_or_reject_failed = True

        # 5) 统一收尾：机器人已经在群里就先补欢迎，然后按实际结果通知邀请人
        if welcome_reason:
            actions.extend(
                await self._send_welcome(client, info, reason=welcome_reason)
            )
        actions.extend(
            await self._notify_inviter(
                client,
                info,
                decision,
                variables,
                outcome=outcome,
                failed=approve_or_reject_failed,
            )
        )

        # 6) 记录
        self._record(
            info=info,
            inviter_id=inviter_id,
            comment=comment,
            decision=decision,
            actions=actions,
            source="invite",
        )

        # 7) 收尾日志（按最终结果，不再依赖中间标记）
        if info.is_member and outcome is False:
            logger.info(
                f"{LOG_PREFIX} 群 {group_id} 可被 QQ 强行拉人且复核未通过，已退群"
            )
        elif info.is_member:
            logger.info(
                f"{LOG_PREFIX} 机器人已在群 {group_id} 内，本次为重复邀请；"
                f"未调用审批接口，已按「已进群」回应邀请人"
            )
        elif approved_by_us:
            logger.info(f"{LOG_PREFIX} 已同意群 {group_id} 的邀请，等待入群后发送欢迎消息")
        elif decision.approve is False and outcome is False:
            logger.info(f"{LOG_PREFIX} 已拒绝群 {group_id} 的邀请")
        elif approve_or_reject_failed:
            logger.error(f"{LOG_PREFIX} 群 {group_id} 的邀请处理失败，邀请仍保持待处理")
        elif decision.approve is None:
            logger.info(f"{LOG_PREFIX} 群 {group_id} 的邀请保持待处理（未做任何操作）")

    async def on_group_increase_notice(self, event: Any) -> None:
        """处理 ``group_increase`` 通知：机器人自己入群后补全信息 / 欢迎 / 复核。"""
        raw = getattr(event.message_obj, "raw_message", None)
        if not isinstance(raw, dict):
            return
        if raw.get("notice_type") != "group_increase":
            return

        group_id = _to_int(raw.get("group_id"))
        user_id = _to_int(raw.get("user_id"))
        self_id = _to_int(event.get_self_id())
        if not group_id or not user_id or user_id != self_id:
            return

        client = getattr(event, "bot", None)
        if client is None:
            return

        operator_id = _to_int(raw.get("operator_id"))

        approved_by_us = self._is_pending(group_id)
        if not approved_by_us:
            # 不是本插件同意的入群。最常见的来源是 QQ 的硬性规则：
            # **少于 50 人的群，不需要机器人同意就会被直接拉进去**，
            # 随后才收到请求事件（表现为「重复邀请」）。
            # 这类入群同样要参与复核，否则「人数下限」等条件形同虚设。
            logger.info(
                f"{LOG_PREFIX} 机器人被拉入群 {group_id}（非本插件同意的邀请，"
                f"操作者 {operator_id or '未知'}；可能是 QQ 自动拉入的小群）"
            )

        await self._process_join(
            client,
            group_id,
            operator_id,
            source="notice",
            approved_by_us=approved_by_us,
        )

    async def _process_join(
        self,
        client: Any,
        group_id: int,
        operator_id: int = 0,
        *,
        source: str = "notice",
        approved_by_us: bool = True,
    ) -> None:
        """机器人确认入群后的统一处理：补全信息 → 复核 → 欢迎。

        入群通知与轮询兜底都会走到这里；谁先到谁处理（``_claim_join`` 保证只处理一次）。

        Args:
            approved_by_us: 这次入群是不是本插件同意了邀请。``False`` 表示机器人
                是「不请自来」的（QQ 对少于 50 人的群会直接拉人、或他人手动拉群），
                这种情况同样参与复核，但不发送入群欢迎消息。
        """
        claimed, pending_inviter = self._claim_join(group_id)
        if not claimed:
            # 已被另一条路径处理过
            return
        if not operator_id:
            operator_id = pending_inviter
        if source != "notice":
            logger.info(f"{LOG_PREFIX} 机器人已加入群 {group_id}（{source}）")

        # 入群后可以拿到完整信息（含群公告、群主、管理员）
        info = await fetch_group_info(
            client,
            group_id,
            fetch_notice=self.cfg.fetch_notice,
            fetch_roster=True,
        )
        info.is_member = True
        info.inviter_id = operator_id
        if self.cfg.log_group_detail:
            logger.info(f"{LOG_PREFIX} 入群后补全群聊完整信息")
            log_group_info(info, prefix=LOG_PREFIX)

        # 入群后复核：用真实群资料重新判断，不满足就退出
        if await self._verify_after_join(
            client,
            info,
            operator_id,
            approved_by_us=approved_by_us,
        ):
            # 复核已退群：统一走「未满足条件」通知出口，告诉对方为什么没留下
            if await self._notify_unmet(
                client, info, operator_id, decide(self.cfg, info).reasons_text()
            ):
                logger.info(f"{LOG_PREFIX} 已向 {operator_id} 说明退群原因")
            return

        # 复核通过 => 机器人最终留在这个群里，统一发欢迎消息。
        # 「发不发欢迎」取决于最终是否留在群里，而不是取决于谁点的同意，
        # 因此这里不再区分 approved_by_us，只是记录里的措辞不同。
        if approved_by_us:
            reason = "同意邀请后入群"
            prefix: list[str] = []
        else:
            reason = "非本插件同意的入群，复核通过后保留"
            prefix = ["复核通过，保留在群内"]
            logger.info(
                f"{LOG_PREFIX} 群 {group_id}「{info.name or '未知'}」复核通过，保留在群内"
            )

        actions = [*prefix, *await self._send_welcome(client, info, reason=reason)]
        self._record(
            info=info,
            inviter_id=operator_id,
            comment="",
            decision=Decision(approve=True, mode=MODE_JOIN, reason=reason),
            actions=actions,
            source="join",
            count_as_invite=False,
        )

    async def _verify_after_join(
        self,
        client: Any,
        info: GroupInfo,
        operator_id: int,
        *,
        approved_by_us: bool = True,
    ) -> bool:
        """入群后复核条件，不满足则主动退群。

        这是「机器人不在群里就拿不到群名 / 人数」的根本解法（同样是
        ``astrbot_plugin_relationship`` 采用的做法）：先进群拿到真实资料，
        再按关键词与人数重新判断，不合格就退出。

        两种情况都会复核：
        * 本插件同意了邀请后进来的；
        * 「不请自来」的 —— QQ 对**少于 50 人的群**不需要机器人同意就直接拉人，
          以及他人手动拉群。不处理这类入群的话，「人数下限」等条件形同虚设。

        豁免：``verify_exempt_users`` 里列出的账号（以及 AstrBot 数字管理员）
        拉群时不触发退群。

        Returns:
            ``True`` 表示已退群（调用方不应再发欢迎消息）。
        """
        if not self.cfg.verify_after_join or self.cfg.auto_approve_all:
            return False

        # 「不请自来」的入群可以豁免：名单里的人（以及 AstrBot 数字管理员）
        # 手动拉群时不动它，避免和自己人打架
        if not approved_by_us and self._is_exempt(operator_id):
            logger.info(
                f"{LOG_PREFIX} 群 {info.group_id} 由豁免名单账号 {operator_id} 拉入，"
                f"跳过入群后复核"
            )
            return False

        delay = self.cfg.verify_after_join_delay
        if delay > 0:
            await asyncio.sleep(delay)
            refreshed = await fetch_group_info(
                client,
                info.group_id,
                fetch_notice=self.cfg.fetch_notice,
                fetch_roster=True,
            )
            refreshed.is_member = True
            refreshed.inviter_id = info.inviter_id
            refreshed.inviter_name = info.inviter_name
            info = refreshed

        decision = decide(self.cfg, info)
        unmet = decision.unmet_conditions
        enabled = [c for c in decision.conditions if c.enabled]

        # 入群后理论上资料齐全了；若仍有条件读不到数据，说明协议端异常。
        # 此时不做「退群」这种破坏性操作，保守保留并记下来。
        unknown = decision.unknown_conditions
        if unknown:
            logger.warning(
                f"{LOG_PREFIX} 入群后仍有条件无法判定（"
                f"{'、'.join(c.label for c in unknown)}），保守保留在群内"
            )

        if not enabled or not unmet:
            logger.info(
                f"{LOG_PREFIX} 入群后复核通过：群 {info.group_id}"
                f"「{info.name or '未知'}」人数 {info.member_count}"
            )
            return False

        summary = "；".join(f"{c.label}（{c.detail}）" for c in unmet)
        logger.info(
            f"{LOG_PREFIX} 入群后复核未通过，退出群 {info.group_id}"
            f"「{info.name or '未知'}」：{summary}"
        )

        left = await leave_group(client, info.group_id)
        actions = ["入群后复核未通过"]
        actions.append("已退出该群" if left else "退出该群失败")

        self._record(
            info=info,
            inviter_id=operator_id,
            comment="",
            decision=Decision(
                approve=False,
                mode=MODE_JOIN,
                reason=f"入群后复核未通过：{summary}",
                conditions=decision.conditions,
            ),
            actions=actions,
            source="join",
            count_as_invite=False,
        )
        return left

    # ------------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------------
    async def _notify_unmet(
        self,
        client: Any,
        info: GroupInfo,
        target_id: int,
        reasons: str,
        *,
        target_name: str = "",
    ) -> bool:
        """「未满足条件」的唯一出口：私聊告诉对方为什么没留下。

        ``_notify_inviter``（邀请阶段）与 ``_process_join``（入群后复核退群）
        共用这一处，避免两条路各写一份文案与开关判断。
        """
        if not target_id or not self.cfg.unmet_notify_enable:
            return False
        variables = build_variables(
            info,
            inviter_id=target_id,
            inviter_name=target_name or info.inviter_name,
            reasons=reasons,
            keywords=self.cfg.keywords,
        )
        text = compose_unmet_message(self.cfg, variables)
        if not text.strip():
            return False
        return await send_private(client, target_id, text)

    async def _notify_inviter(
        self,
        client: Any,
        info: GroupInfo,
        decision: Decision,
        variables: dict[str, Any],
        *,
        outcome: bool | None = None,
        failed: bool = False,
    ) -> list[str]:
        """按**实际结果**通知邀请人，避免「操作失败却告诉对方已同意」。"""
        # 「条件开关全部关闭」或「信息不可用且策略为 skip」= 明确要求不通知
        if not decision.notify:
            return []

        inviter_id = _to_int(variables.get("inviter_id"))
        if not inviter_id:
            return []

        if failed:
            # 协议端拒绝/失败：不要谎报成功，也不要发「不满足条件」误导对方
            logger.error(
                f"{LOG_PREFIX} 群邀请处理失败，已跳过对邀请人 {inviter_id} 的通知"
            )
            return ["处理失败，未通知邀请人"]

        if outcome is True:
            if not self.cfg.approve_notify_enable:
                return []
            text = render(self.cfg.approve_notify_message, variables)
            if not text.strip():
                return []
            ok = await send_private(client, inviter_id, text)
            if ok:
                logger.info(f"{LOG_PREFIX} 已向邀请人 {inviter_id} 发送「已同意」通知")
                return ["已通知邀请人（已同意）"]
            return ["通知邀请人失败"]

        # 未满足条件 / 保持待处理：走唯一的未满足通知出口
        ok = await self._notify_unmet(
            client,
            info,
            inviter_id,
            decision.reasons_text(),
        )
        if ok:
            logger.info(f"{LOG_PREFIX} 已向邀请人 {inviter_id} 发送「未满足条件」通知")
            return ["已通知邀请人（未满足条件）"]
        return ["通知邀请人失败"]

    async def _send_welcome(
        self,
        client: Any,
        info: GroupInfo,
        *,
        reason: str = "",
    ) -> list[str]:
        if not self.cfg.welcome_enable and not self.cfg.welcome_image:
            return ["未开启入群欢迎消息"]

        # 同一群短时间内只发一次：入群通知与重复邀请两条路可能都走到这里
        if not self._claim_welcome(info.group_id):
            logger.info(
                f"{LOG_PREFIX} 群 {info.group_id} 近期已发过欢迎消息，跳过重复发送"
            )
            return ["近期已发过欢迎消息，跳过"]

        variables = build_variables(info, keywords=self.cfg.keywords)
        text = render(self.cfg.welcome_message, variables) if self.cfg.welcome_enable else ""
        image = self.cfg.welcome_image

        if not text.strip() and not image:
            return ["入群欢迎内容为空，未发送"]

        ok = await send_group(client, info.group_id, text, image=image)
        if not ok:
            return ["发送入群欢迎消息失败"]

        pieces = []
        if text.strip():
            pieces.append("文本")
        if image:
            pieces.append("图片")
        logger.info(
            f"{LOG_PREFIX} 已在群 {info.group_id} 发送入群欢迎消息"
            f"（{' + '.join(pieces)}）{f'：{reason}' if reason else ''}"
        )
        return [f"已发送入群欢迎消息（{' + '.join(pieces)}）"]

    def _record(
        self,
        *,
        info: GroupInfo,
        inviter_id: int,
        comment: str,
        decision: Decision,
        actions: list[str],
        source: str,
        count_as_invite: bool = True,
    ) -> None:
        record = {
            "timestamp": time.time(),
            "time_text": time.strftime("%Y-%m-%d %H:%M:%S"),
            "source": source,
            "group": info.to_dict(),
            "inviter_id": inviter_id,
            "comment": comment,
            "approve": decision.approve,
            "mode": decision.mode,
            "reason": decision.reason,
            "matched_keywords": list(decision.matched_keywords),
            "conditions": [c.to_dict() for c in decision.conditions],
            "actions": actions,
        }
        if count_as_invite:
            self.history.add(record, self.cfg.history_limit)
        else:
            # 入群通知只作为补充记录，不重复计入邀请总数
            self.history.add(record, self.cfg.history_limit, count_stats=False)

    # ---- 待处理状态 ----
    def _mark_pending_welcome(self, group_id: int, inviter_id: int = 0) -> None:
        """记录「这个群是我们刚同意进去的」，用于识别随后的入群。"""
        self._prune_pending()
        self._pending_welcome[int(group_id)] = (time.time(), int(inviter_id or 0))

    def _is_pending(self, group_id: int) -> bool:
        """该群是否处于「本插件刚同意了邀请」的待处理状态。"""
        self._prune_pending()
        return int(group_id) in self._pending_welcome

    def _is_force_joinable(self, info: GroupInfo) -> bool:
        """该群是否属于「QQ 可以不经同意直接拉人」的类型（人数低于阈值）。

        QQ 的规则是少于 50 人不需要机器人同意，所以「重复邀请 + 人数很小」
        基本就意味着机器人是被强行拉进去的，此时才需要复核。
        人数未知（0）时不做判断，避免误退。
        """
        limit = self.cfg.force_join_max_members
        if limit <= 0 or not self.cfg.verify_after_join or self.cfg.auto_approve_all:
            return False
        return 0 < info.member_count < limit

    def _claim_welcome(self, group_id: int) -> bool:
        """认领「给这个群发欢迎消息」的资格；去重窗口内已经发过则返回 False。"""
        self._prune_pending()
        gid = int(group_id)
        if gid in self._welcomed:
            return False
        self._welcomed[gid] = time.time()
        return True

    def _is_exempt(self, user_id: int) -> bool:
        """该账号是否豁免（拉群不触发自动退群）。"""
        return bool(user_id) and int(user_id) in self.exempt_users

    def _claim_join(self, group_id: int) -> tuple[bool, int]:
        """认领「该群的入群处理」，返回 ``(是否由我处理, 待处理表里的邀请人)``。

        入群通知与轮询兜底可能同时到达，这里做两件事：
        * 用 pop 保证只有一方拿到待处理条目，避免重复发欢迎 / 重复复核；
        * 用 ``_handled_joins`` 对「非本插件同意」的入群去重，防止同一次入群
          被处理两遍。
        """
        self._prune_pending()
        gid = int(group_id)
        if gid in self._handled_joins:
            return False, 0
        self._handled_joins[gid] = time.time()
        entry = self._pending_welcome.pop(gid, None)
        return True, (entry[1] if entry else 0)

    def _prune_pending(self) -> None:
        deadline = time.time() - WELCOME_TTL_SECONDS
        for gid in [
            g for g, (ts, _inviter) in self._pending_welcome.items() if ts < deadline
        ]:
            self._pending_welcome.pop(gid, None)
        for gid in [
            g for g, ts in self._handled_joins.items() if ts < deadline
        ]:
            self._handled_joins.pop(gid, None)
        welcome_deadline = time.time() - WELCOME_DEDUPE_SECONDS
        for gid in [
            g for g, ts in self._welcomed.items() if ts < welcome_deadline
        ]:
            self._welcomed.pop(gid, None)

    # ---- 入群轮询兜底 ----
    def spawn_join_watch(self, client: Any, group_id: int, inviter_id: int = 0) -> None:
        """同意邀请后启动一个轮询任务，作为「收不到入群通知」的兜底。

        为什么需要它：``group_increase`` 是通过 AstrBot 的普通事件管道分发的，
        而**同一个事件可能被其它插件用 ``event.stop_event()`` 截断**
        （例如本地已装的 ``astrbot_plugin_relationship`` 就会在机器人被拉进群时
        停止事件传播），导致排在后面的处理器根本收不到通知。
        轮询直接查群列表，不依赖事件管道，因此不受影响。
        """
        task = asyncio.create_task(
            self._watch_join(client, int(group_id), int(inviter_id or 0))
        )
        self._join_watch_tasks.add(task)
        task.add_done_callback(self._join_watch_tasks.discard)

    async def _watch_join(self, client: Any, group_id: int, inviter_id: int) -> None:
        deadline = time.time() + JOIN_WATCH_TIMEOUT
        interval = JOIN_WATCH_INTERVAL
        try:
            while time.time() < deadline:
                await asyncio.sleep(interval)
                if not self._is_pending(group_id):
                    # 入群通知已处理，收工
                    return
                if await check_membership(client, group_id) is True:
                    logger.info(
                        f"{LOG_PREFIX} 轮询确认机器人已进入群 {group_id}"
                        f"（未收到 group_increase 通知，可能是被其它插件截断了）"
                    )
                    await self._process_join(
                        client, group_id, inviter_id, source="轮询兜底"
                    )
                    return
                interval = min(interval + 1.0, JOIN_WATCH_INTERVAL_MAX)
            if self._is_pending(group_id):
                logger.warning(
                    f"{LOG_PREFIX} 等待 {int(JOIN_WATCH_TIMEOUT)} 秒仍未确认进入群 "
                    f"{group_id}，放弃后续欢迎与复核"
                )
                self._pending_welcome.pop(int(group_id), None)
        except asyncio.CancelledError:  # pragma: no cover - 插件卸载时
            raise
        except Exception as exc:  # noqa: BLE001 - 兜底任务不应影响主流程
            logger.warning(f"{LOG_PREFIX} 入群轮询任务异常: {exc}")

    def cancel_join_watches(self) -> None:
        for task in list(self._join_watch_tasks):
            task.cancel()
        self._join_watch_tasks.clear()


def _approve_text(approve: bool | None) -> str:
    if approve is True:
        return "同意"
    if approve is False:
        return "拒绝"
    return "不处理"


def _to_int(value: Any) -> int:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return 0


def _to_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()
