"""插件配置的读写与校验封装。

`_conf_schema.json` 定义了配置结构，AstrBot 会把它注入为一个 ``AstrBotConfig``
（``dict`` 子类）。这里再做一层强类型视图，避免插件各处散落 ``.get()`` 与类型转换，
并给管理页提供一份「收敛过」的配置读写接口。
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from astrbot.api import logger
from astrbot.core.config.astrbot_config import AstrBotConfig

DEFAULT_KEYWORDS: list[str] = ["原神", "星穹铁道", "绝区零"]
DEFAULT_UNMET_MESSAGE = "不满足以下条件：\n{reasons}"
DEFAULT_APPROVE_MESSAGE = "已同意进群~"
DEFAULT_WELCOME_MESSAGE = "我是机器人，欢迎使用"

#: 管理页允许修改的配置项白名单： ``(分组, 键, 类型)``。
#: 类型为 ``bool`` / ``int`` / ``str`` / ``list[str]`` / ``raw``。
FIELD_SPECS: dict[str, dict[str, tuple[str, Any]]] = {
    "auto_approve_all": {},
    "conditions": {
        "keyword_enable": ("bool", True),
        "keywords": ("str_list", DEFAULT_KEYWORDS),
        "member_count_enable": ("bool", True),
        "min_member_count": ("int", 0),
        "max_member_count": ("int", 3000),
        "when_info_unavailable": ("str", "approve"),
        "verify_after_join": ("bool", True),
        "verify_after_join_delay": ("int", 3),
        "verify_exempt_users": ("str_list", []),
        "force_join_max_members": ("int", 50),
    },
    "notify": {
        "approve_enable": ("bool", True),
        "approve_message": ("str", DEFAULT_APPROVE_MESSAGE),
        "unmet_enable": ("bool", True),
        "unmet_message": ("str", DEFAULT_UNMET_MESSAGE),
        "unmet_extra": ("str", ""),
        "unmet_auto_reject": ("bool", False),
    },
    "welcome": {
        "enable": ("bool", True),
        "message": ("str", DEFAULT_WELCOME_MESSAGE),
        "image": ("str", ""),
    },
    "advanced": {
        "log_group_detail": ("bool", True),
        "fetch_notice": ("bool", True),
        "history_limit": ("int", 100),
    },
}

#: 顶层布尔开关（``auto_approve_all`` 是唯一一个不带值的顶层开关）。
TOP_LEVEL_BOOLS = ("auto_approve_all",)

_INT_BOUNDS: dict[tuple[str, str], tuple[int, int]] = {
    ("conditions", "min_member_count"): (0, 1_000_000),
    ("conditions", "max_member_count"): (0, 1_000_000),
    ("conditions", "verify_after_join_delay"): (0, 600),
    ("conditions", "force_join_max_members"): (0, 1_000_000),
    ("advanced", "history_limit"): (10, 5000),
}


def _as_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"1", "true", "yes", "on", "y"}:
            return True
        if lowered in {"0", "false", "no", "off", "n", ""}:
            return False
    return default


def _as_int(value: Any, default: int = 0) -> int:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        try:
            return int(float(value.strip()))
        except ValueError:
            return default
    return default


def _as_str(value: Any, default: str = "") -> str:
    if value is None:
        return default
    if isinstance(value, str):
        return value
    return str(value)


def _as_str_list(value: Any, default: list[str] | None = None) -> list[str]:
    """把任意输入规范成去重、去空白的字符串列表。"""
    if isinstance(value, str):
        # 兼容前端/用户用换行、逗号或顿号分隔的写法
        for sep in ("\n", "，", ",", "、", ";", "；"):
            value = value.replace(sep, "\n")
        items: list[Any] = value.split("\n")
    elif isinstance(value, (list, tuple, set)):
        items = list(value)
    else:
        return list(default or [])

    result: list[str] = []
    seen: set[str] = set()
    for item in items:
        text = _as_str(item).strip()
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return result


class PluginConfig:
    """``AstrBotConfig`` 的强类型只读视图 + 管理页写入口。"""

    def __init__(self, raw: AstrBotConfig) -> None:
        self.raw = raw

    # ------------------------------------------------------------------
    # 底层工具
    # ------------------------------------------------------------------
    def _section(self, group: str) -> Mapping[str, Any]:
        value = self.raw.get(group)
        return value if isinstance(value, Mapping) else {}

    def _get(self, group: str, key: str) -> Any:
        spec = FIELD_SPECS.get(group, {}).get(key)
        if spec is None:
            return None
        kind, default = spec
        raw_value = self._section(group).get(key)
        if raw_value is None:
            raw_value = default
        if kind == "bool":
            return _as_bool(raw_value, default)
        if kind == "int":
            return _as_int(raw_value, default)
        if kind == "str":
            return _as_str(raw_value, default)
        if kind == "str_list":
            return _as_str_list(raw_value, default)
        return raw_value

    # ------------------------------------------------------------------
    # 顶层开关
    # ------------------------------------------------------------------
    @property
    def auto_approve_all(self) -> bool:
        """自动同意所有群邀请（优先级最高）。"""
        return _as_bool(self.raw.get("auto_approve_all"), False)

    # ------------------------------------------------------------------
    # 条件：关键词
    # ------------------------------------------------------------------
    @property
    def keyword_enable(self) -> bool:
        return bool(self._get("conditions", "keyword_enable"))

    @property
    def keywords(self) -> list[str]:
        return list(self._get("conditions", "keywords"))

    # ------------------------------------------------------------------
    # 条件：人数
    # ------------------------------------------------------------------
    @property
    def member_count_enable(self) -> bool:
        return bool(self._get("conditions", "member_count_enable"))

    @property
    def min_member_count(self) -> int:
        return int(self._get("conditions", "min_member_count"))

    @property
    def max_member_count(self) -> int:
        return int(self._get("conditions", "max_member_count"))

    #: 群资料取不到（机器人不在该群）时的处理策略。
    #: 只有两种：进群试用 / 不进群也不回复。本项只回答一个问题 ——
    #: 「要不要先放机器人进去看一眼」；「要不要回复邀请人」不由本项控制。
    INFO_UNAVAILABLE_APPROVE = "approve"
    INFO_UNAVAILABLE_SKIP = "skip"
    #: 早期版本用过、现已废弃的值，加载时一律迁移为 ``skip``。
    INFO_UNAVAILABLE_LEGACY_UNMET = "unmet"

    def _stored_info_policy(self) -> str:
        """配置文件里实际存的值（原样，不迁移）。"""
        section = self.raw.get("conditions")
        if not isinstance(section, Mapping):
            return ""
        return str(section.get("when_info_unavailable", "")).strip().lower()

    @property
    def info_policy_raw(self) -> str:
        """迁移后的策略值（未做「复核接管」归一化）。"""
        if self._stored_info_policy() == self.INFO_UNAVAILABLE_APPROVE:
            return self.INFO_UNAVAILABLE_APPROVE
        # skip / 已废弃的 unmet / 任何未知值，一律按「不进群」处理
        return self.INFO_UNAVAILABLE_SKIP

    @property
    def info_policy_legacy_migrated(self) -> bool:
        """配置里存的是已废弃的 ``unmet``（行为等价于现在的 ``skip``）。"""
        return self._stored_info_policy() == self.INFO_UNAVAILABLE_LEGACY_UNMET

    @property
    def when_info_unavailable(self) -> str:
        """群资料取不到时的**实际生效**策略。

        ``skip``（不进群）意味着机器人永远收不到 ``group_increase`` 通知，
        ``verify_after_join`` 也就永远不会被触发。因此一旦开启入群后复核，
        这里会归一化成 ``approve``（先进群再复核），避免出现
        「复核开关是开的、却永远不执行」这种死配置。
        归一化结果通过 :attr:`info_policy_overridden` 暴露，供日志与页面提示。
        """
        raw = self.info_policy_raw
        if self.verify_after_join and raw != self.INFO_UNAVAILABLE_APPROVE:
            return self.INFO_UNAVAILABLE_APPROVE
        return raw

    @property
    def info_policy_overridden(self) -> bool:
        """「群名/人数拿不到时怎么办」是否因为开启了入群后复核而被强制改写。"""
        return self.verify_after_join and (
            self.info_policy_raw != self.INFO_UNAVAILABLE_APPROVE
        )

    @property
    def verify_after_join(self) -> bool:
        """入群后用真实群资料重新校验，不满足条件则自动退群。"""
        return bool(self._get("conditions", "verify_after_join"))

    @property
    def verify_after_join_delay(self) -> int:
        """入群后等待多少秒再校验（留给协议端同步群资料的时间）。"""
        return int(self._get("conditions", "verify_after_join_delay"))

    @property
    def force_join_max_members(self) -> int:
        """「可被 QQ 强行拉人」的群人数上限（默认 50，0 表示关闭该行为）。

        QQ 对少于 50 人的群不需要机器人同意就会直接拉人。收到「重复邀请」
        （机器人已在群里）时，若群人数小于此值，说明该群属于「可被强行拉人」的那类，
        插件会按配置条件重新复核，不满足就退群。
        """
        return int(self._get("conditions", "force_join_max_members"))

    @property
    def verify_exempt_users(self) -> set[int]:
        """豁免名单：这些人拉机器人进群时不做「不合格就退群」。

        主要用于应对 QQ 的「少于 50 人的群可直接被拉进去」行为 ——
        把信任的账号填进来，避免自己手动拉群也被退掉。
        """
        result: set[int] = set()
        for item in self._get("conditions", "verify_exempt_users"):
            text = str(item).strip()
            if text.isdigit():
                result.add(int(text))
        return result

    # ------------------------------------------------------------------
    # 通知：同意 / 未满足
    # ------------------------------------------------------------------
    @property
    def approve_notify_enable(self) -> bool:
        return bool(self._get("notify", "approve_enable"))

    @property
    def approve_notify_message(self) -> str:
        return str(self._get("notify", "approve_message"))

    @property
    def unmet_notify_enable(self) -> bool:
        return bool(self._get("notify", "unmet_enable"))

    @property
    def unmet_notify_message(self) -> str:
        return str(self._get("notify", "unmet_message"))

    @property
    def unmet_notify_extra(self) -> str:
        return str(self._get("notify", "unmet_extra"))

    @property
    def unmet_auto_reject(self) -> bool:
        return bool(self._get("notify", "unmet_auto_reject"))

    # ------------------------------------------------------------------
    # 进群欢迎
    # ------------------------------------------------------------------
    @property
    def welcome_enable(self) -> bool:
        return bool(self._get("welcome", "enable"))

    @property
    def welcome_message(self) -> str:
        return str(self._get("welcome", "message"))

    @property
    def welcome_image(self) -> str:
        return str(self._get("welcome", "image")).strip()

    # ------------------------------------------------------------------
    # 高级
    # ------------------------------------------------------------------
    @property
    def log_group_detail(self) -> bool:
        return bool(self._get("advanced", "log_group_detail"))

    @property
    def fetch_notice(self) -> bool:
        return bool(self._get("advanced", "fetch_notice"))

    @property
    def history_limit(self) -> int:
        return int(self._get("advanced", "history_limit"))

    # ------------------------------------------------------------------
    # 管理页读写
    # ------------------------------------------------------------------
    def to_dict(self) -> dict[str, Any]:
        """导出完整配置（供管理页渲染）。"""
        data: dict[str, Any] = {
            name: _as_bool(self.raw.get(name), False) for name in TOP_LEVEL_BOOLS
        }
        for group, fields in FIELD_SPECS.items():
            if not fields:
                continue
            data[group] = {key: self._get(group, key) for key in fields}
        # 下拉框只认 approve / skip：这里回传迁移后的值，避免配置里残留旧值
        # （例如已废弃的 unmet）时管理页下拉框显示空白
        data["conditions"]["when_info_unavailable"] = self.info_policy_raw
        return data

    def policy_summary(self) -> dict[str, Any]:
        """给管理页用的策略摘要：当前会不会自动同意、依据是什么。"""
        if self.auto_approve_all:
            return {
                "mode": "approve_all",
                "label": "自动同意所有群邀请",
                "detail": "已开启「自动同意所有群邀请」，所有群邀请都会被无条件同意。",
                "any_condition": True,
            }

        enabled: list[str] = []
        if self.keyword_enable:
            enabled.append("关键词")
        if self.member_count_enable:
            enabled.append("人数")

        if not enabled:
            return {
                "mode": "disabled",
                "label": "不处理群邀请",
                "detail": "「满足以下要求时」的条件开关全部关闭，插件不会自动同意，也不会自动拒绝任何群邀请。",
                "any_condition": False,
            }

        parts: list[str] = []
        if self.keyword_enable:
            keywords = self.keywords
            parts.append(
                "群名称 / 群介绍 / 群公告包含关键词："
                + ("、".join(keywords) if keywords else "（未配置关键词，该条件恒不满足）")
            )
        if self.member_count_enable:
            parts.append(
                f"群人数大于 {self.min_member_count} 且少于 {self.max_member_count}"
            )

        detail = "需同时满足（AND）：" + "；".join(parts)

        if self.verify_after_join:
            detail += (
                "。群名 / 人数拿不到时按「进群试用」处理：先进群，"
                f"等 {self.verify_after_join_delay} 秒后用真实资料复核，"
                "不合格自动退出"
            )
            if self.info_policy_overridden:
                detail += "（「群名/人数拿不到时怎么办」已按此逻辑固定为「进群试用」）"
        elif self.when_info_unavailable == self.INFO_UNAVAILABLE_SKIP:
            detail += (
                "。群名 / 人数拿不到时「不进群，也不回复邀请人」，"
                "邀请保持待处理"
            )
        else:
            # 复核已关闭 + 进群试用 = 进去就不管了，这是有风险的状态，必须讲明
            detail += (
                "。群名 / 人数拿不到时「直接同意并留在群里」—— "
                "复核已关闭，不会再按条件退出，请谨慎使用"
            )

        return {
            "mode": "conditions",
            "label": "满足条件时自动同意",
            "detail": detail,
            "any_condition": True,
            "reject": self.unmet_auto_reject,
            "verify_after_join": self.verify_after_join,
            "info_policy": self.when_info_unavailable,
            "info_policy_raw": self.info_policy_raw,
            "info_policy_overridden": self.info_policy_overridden,
            "info_policy_legacy_migrated": self.info_policy_legacy_migrated,
        }

    def apply_payload(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        """把管理页提交的数据写回配置并落盘，返回规范化后的配置。

        只接受白名单内的键，非法值会被忽略或收敛，避免管理页写坏配置文件。
        """
        updated: dict[str, Any] = {}

        for name in TOP_LEVEL_BOOLS:
            if name in payload:
                updated[name] = _as_bool(payload[name], self.auto_approve_all)

        for group, fields in FIELD_SPECS.items():
            if not fields:
                continue
            incoming = payload.get(group)
            if not isinstance(incoming, Mapping):
                continue
            current = self.raw.get(group)
            target = dict(current) if isinstance(current, Mapping) else {}
            for key, (kind, _default) in fields.items():
                if key not in incoming:
                    continue
                value = incoming[key]
                if kind == "bool":
                    target[key] = _as_bool(value)
                elif kind == "int":
                    low, high = _INT_BOUNDS.get((group, key), (0, 10**9))
                    target[key] = max(low, min(high, _as_int(value)))
                elif kind == "str":
                    text = _as_str(value)
                    # when_info_unavailable 只允许 approve / skip，其余一律按 skip
                    if (group, key) == ("conditions", "when_info_unavailable"):
                        text = (
                            self.INFO_UNAVAILABLE_APPROVE
                            if text.strip().lower() == self.INFO_UNAVAILABLE_APPROVE
                            else self.INFO_UNAVAILABLE_SKIP
                        )
                    target[key] = text
                elif kind == "str_list":
                    target[key] = _as_str_list(value)
                else:
                    target[key] = value
            updated[group] = target

        self.save(updated)
        return self.to_dict()

    def save(self, updated: Mapping[str, Any]) -> None:
        """合并写入 ``updated`` 并持久化。"""
        for key, value in updated.items():
            self.raw[key] = value
        try:
            self.raw.save_config()
        except Exception as exc:  # pragma: no cover - 落盘失败不应中断插件
            logger.error(f"[group_invite] 配置保存失败: {exc}")
            raise
        logger.info("[group_invite] 配置已更新并保存")
