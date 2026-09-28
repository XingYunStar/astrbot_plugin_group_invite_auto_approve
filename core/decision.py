"""群邀请处理决策。

决策优先级（严格按需求实现）::

    1. 「自动同意所有群邀请」开启  -> 无条件同意（优先级最高）
    2. 否则，逐一评估已开启的条件，判断关系为 AND：
         * 关键词：群名称 / 群介绍 / 群公告 包含任一配置关键词
         * 人数  ：群人数 大于 最小人数 且 少于 最大人数
       全部满足 -> 自动同意
       任一不满足 -> 默认「不处理」（可配置为自动拒绝）
    3. 所有条件开关都关闭 -> 不处理群邀请
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .config import PluginConfig
from .group_info import GroupInfo

#: 决策模式常量，管理页与日志共用。
MODE_APPROVE_ALL = "approve_all"
MODE_CONDITIONS = "conditions"
MODE_DISABLED = "disabled"
MODE_MANUAL = "manual"
MODE_JOIN = "join"
MODE_UNKNOWN_INFO = "unknown_info"
MODE_ALREADY_MEMBER = "already_member"


@dataclass
class ConditionResult:
    """单个条件的评估结果（三态）。

    * ``enabled=False``            —— 该条件没开启，不参与判断
    * ``evaluable=False``          —— **无法判定**（协议端没给出所需数据，例如
      机器人不在群里时人数恒为 0）。这类条件不能当成「不满足」，
      否则任何一个没进过的群都会被挡在门外。
    * ``passed``                   —— 仅在 ``evaluable`` 为真时有意义
    """

    key: str
    label: str
    enabled: bool
    passed: bool
    detail: str
    evaluable: bool = True

    @property
    def failed(self) -> bool:
        """明确不满足（而不是「不知道」）。"""
        return self.enabled and self.evaluable and not self.passed

    def to_dict(self) -> dict[str, object]:
        return {
            "key": self.key,
            "label": self.label,
            "enabled": self.enabled,
            "passed": self.passed,
            "evaluable": self.evaluable,
            "detail": self.detail,
        }


@dataclass
class Decision:
    """一次群邀请的完整决策。"""

    #: ``True`` 同意 / ``False`` 拒绝 / ``None`` 不处理（保持待审批）
    approve: bool | None = None
    mode: str = MODE_DISABLED
    reason: str = ""
    conditions: list[ConditionResult] = field(default_factory=list)
    matched_keywords: list[str] = field(default_factory=list)
    #: 是否要私聊通知邀请人（「全部关闭」与「信息不可用且策略为 skip」时为 False）
    notify: bool = True

    @property
    def unmet_conditions(self) -> list[ConditionResult]:
        """明确不满足的条件（不含「无法判定」的）。"""
        return [c for c in self.conditions if c.failed]

    @property
    def unknown_conditions(self) -> list[ConditionResult]:
        """无法判定的条件（协议端没给出所需数据）。"""
        return [c for c in self.conditions if c.enabled and not c.evaluable]

    @property
    def handled(self) -> bool:
        return self.approve is not None

    def reasons_text(self) -> str:
        """未满足条件的多行文本，用于私聊消息与日志。"""
        unmet = self.unmet_conditions
        if not unmet:
            return self.reason or "（无）"
        return "\n".join(f"- {item.label}：{item.detail}" for item in unmet)

    def to_dict(self) -> dict[str, object]:
        return {
            "approve": self.approve,
            "mode": self.mode,
            "reason": self.reason,
            "notify": self.notify,
            "matched_keywords": list(self.matched_keywords),
            "conditions": [c.to_dict() for c in self.conditions],
            "reasons_text": self.reasons_text(),
        }


def decide(cfg: PluginConfig, info: GroupInfo) -> Decision:
    """根据配置与群信息给出处理决策。"""
    # ---- 1. 自动同意所有群邀请（最高优先级）----
    if cfg.auto_approve_all:
        return Decision(
            approve=True,
            mode=MODE_APPROVE_ALL,
            reason="已开启「自动同意所有群邀请」，无条件同意",
        )

    # ---- 2. 逐条评估已开启的条件（AND）----
    conditions = [
        _check_keyword(cfg, info),
        _check_member_count(cfg, info),
    ]
    decision = Decision(mode=MODE_CONDITIONS, conditions=conditions)
    decision.matched_keywords = _matched_keywords(cfg, info)

    enabled = [c for c in conditions if c.enabled]
    if not enabled:
        decision.mode = MODE_DISABLED
        decision.approve = None
        decision.notify = False
        decision.reason = "「满足以下要求时」的条件开关全部关闭，不处理该群邀请"
        return decision

    # ---- 2.1 有「明确不满足」的条件：直接按未满足处理 ----
    # 注意区分两种「没通过」：
    #   * 明确不满足（failed）—— 例如读到了群名、但关键词确实不在里面
    #   * 无法判定（unknown）—— 例如机器人不在群里，人数恒为 0，根本没法判断
    # 后者不能当成不满足，否则任何一个没进过的群都会被挡在门外。
    unmet = decision.unmet_conditions
    if unmet:
        summary = "；".join(f"{c.label}（{c.detail}）" for c in unmet)
        if cfg.unmet_auto_reject:
            decision.approve = False
            decision.reason = f"未满足条件，已自动拒绝：{summary}"
        else:
            decision.approve = None
            decision.mode = MODE_MANUAL
            decision.reason = f"未满足条件，保持待处理：{summary}"
        return decision

    # ---- 2.2 有「无法判定」的条件：按 when_info_unavailable 策略处理 ----
    # 默认「进群试用」：先进群，再用真实资料复核，不合格就退 ——
    # 这样「人数未知」与「资料完全取不到」走的是同一条路。
    unknown = decision.unknown_conditions
    if unknown:
        decision.mode = MODE_UNKNOWN_INFO
        unknown_labels = "、".join(c.label for c in unknown)
        policy = cfg.when_info_unavailable
        if policy == PluginConfig.INFO_UNAVAILABLE_APPROVE:
            decision.approve = True
            decision.notify = True
            if cfg.info_policy_overridden:
                decision.reason = (
                    f"无法判定条件[{unknown_labels}]（机器人不在该群，协议端不提供"
                    f"所需数据）。原始配置选的是「不进群」，但已开启「入群后重新校验」"
                    f"——不进群就永远拿不到这些数据，因此按「进群试用」处理："
                    f"先进群，再用真实资料复核，不合格会自动退出"
                )
            else:
                decision.reason = (
                    f"无法判定条件[{unknown_labels}]（机器人不在该群，协议端不提供"
                    f"所需数据），按「进群试用」处理：先进群，再用真实资料复核，"
                    f"不合格会自动退出"
                )
        elif policy == PluginConfig.INFO_UNAVAILABLE_SKIP:
            decision.approve = None
            decision.notify = False
            decision.reason = (
                f"无法判定条件[{unknown_labels}]，按配置「不进群，也不回复邀请人」"
                f"处理，邀请保持待处理"
            )
        else:  # pragma: no cover - 配置层已把取值收敛为两种
            decision.approve = None
            decision.notify = False
            decision.reason = f"无法判定条件[{unknown_labels}]，不处理该邀请"
        return decision

    # ---- 2.3 全部可评估且都通过 ----
    decision.approve = True
    decision.reason = "已满足全部已开启的条件：" + "；".join(c.label for c in enabled)
    return decision


# ----------------------------------------------------------------------
# 各条件实现
# ----------------------------------------------------------------------
def _matched_keywords(cfg: PluginConfig, info: GroupInfo) -> list[str]:
    if not cfg.keyword_enable:
        return []
    return info.matched_keywords(cfg.keywords)


def _check_keyword(cfg: PluginConfig, info: GroupInfo) -> ConditionResult:
    enabled = cfg.keyword_enable
    keywords = cfg.keywords
    label = "关键词"
    if not enabled:
        return ConditionResult("keyword", label, False, True, "未开启")

    if not keywords:
        return ConditionResult(
            "keyword",
            label,
            True,
            False,
            "已开启关键词条件，但没有配置任何关键词",
        )

    hits = info.matched_keywords(keywords)
    keyword_text = "、".join(keywords)
    if hits:
        return ConditionResult(
            "keyword",
            label,
            True,
            True,
            f"命中关键词 {'、'.join(hits)}（匹配范围：群名称/群介绍/群公告）",
        )

    # 一点可匹配的文本都没有（机器人不在群里、协议端也没给出任何字段）
    # -> 这是「无法判定」，不能当成「不满足」
    if not info.keyword_corpus.strip():
        return ConditionResult(
            "keyword",
            label,
            True,
            False,
            f"拿不到群名称 / 群介绍 / 群公告，无法判断是否包含关键词：{keyword_text}",
            evaluable=False,
        )

    return ConditionResult(
        "keyword",
        label,
        True,
        False,
        f"群名称/群介绍/群公告均未包含关键词：{keyword_text}",
    )


def _check_member_count(cfg: PluginConfig, info: GroupInfo) -> ConditionResult:
    label = "人数"
    if not cfg.member_count_enable:
        return ConditionResult("member_count", label, False, True, "未开启")

    minimum = cfg.min_member_count
    maximum = cfg.max_member_count
    count = info.member_count

    # 群人数不可能是 0：协议端对「机器人不在群内」的群恒返回 0，
    # 这只说明**读不到**，不能当成「不满足」——否则任何没进过的群都会被挡住。
    if not info.member_count_known:
        return ConditionResult(
            "member_count",
            label,
            True,
            False,
            f"拿不到群人数（协议端返回 {count}），无法判断是否大于 {minimum} 且少于 {maximum}",
            evaluable=False,
        )

    if minimum > maximum:
        return ConditionResult(
            "member_count",
            label,
            True,
            False,
            f"配置有误：最小人数 {minimum} 大于最大人数 {maximum}",
        )

    passed = count > minimum and count < maximum
    if passed:
        detail = f"当前群人数 {count}，满足大于 {minimum} 且少于 {maximum}"
    else:
        detail = f"当前群人数 {count}，需大于 {minimum} 且少于 {maximum}"
    return ConditionResult("member_count", label, True, passed, detail)
