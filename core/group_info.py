"""群聊信息采集。

收到群邀请时，机器人自己还不在群里，因此只能拿到「公开可查」的字段。
本模块按「能拿到多少拿多少」的策略工作：每个字段独立 try/except，
任何一个接口失败都不会影响其它字段，失败原因记录在 ``errors`` 里供排查。

已在 OneBot v11 协议端（SnowLuma / NapCat）上实测通过的接口：

* ``get_group_info``      -> group_name / group_remark / member_count /
                             max_member_count / group_create_time /
                             group_level / group_memo / group_description /
                             group_all_shut
* ``get_group_detail_info`` -> 群简介补全（NapCat 的 ``get_group_info`` 不返回群简介）
* ``_get_group_notice``   -> 群公告列表（需要机器人已在群内）
* ``get_group_member_list`` -> 群主 / 管理员（需要机器人已在群内）
* ``get_group_at_all_remain`` / ``get_group_honor_info`` 等附加信息
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from astrbot.api import logger

from .onebot import call_action, try_actions

#: 会尝试读取、但允许失败的附加接口。
_OPTIONAL_APIS: tuple[str, ...] = (
    "get_group_at_all_remain",
    "get_group_honor_info",
)


@dataclass
class GroupInfo:
    """一个群聊的全部可获取信息。"""

    group_id: int
    name: str = ""
    remark: str = ""
    memo: str = ""
    """协议端的 ``group_memo`` 字段（只有 SnowLuma <= 1.14.20 会返回）。

    SnowLuma 把它填成 ``announcement || description``（公告优先），因此有公告的
    群里这里拿到的是**公告的截断预览**；真正的群简介见 :attr:`description`。
    NapCat 不返回该字段，此处恒为空。
    """

    description: str = ""
    """真正的群简介。

    跨协议端取第一个可用的字段：

    * SnowLuma **>= 1.14.21**：``group_description``
    * NapCat：``fingerMemo``（``richFingerMemo`` 为富文本版本，作兜底）

    都拿不到时为 ``""``，此时只能退回 :attr:`memo`。
    """

    description_source: str = ""
    """``description`` 是从哪个协议端字段取到的（用于日志与排查）。"""

    member_count: int = 0
    max_member_count: int = 0
    create_time: int = 0
    level: int = 0
    all_shut: bool = False

    notices: list[str] = field(default_factory=list)
    """群公告文本列表。"""

    owner_id: int = 0
    owner_name: str = ""
    admin_ids: list[int] = field(default_factory=list)
    admin_names: list[str] = field(default_factory=list)

    inviter_id: int = 0
    """本次群邀请的邀请人 QQ（由 handler 填充）。"""

    inviter_name: str = ""
    """本次群邀请的邀请人昵称（由 handler 填充）。"""

    is_member: bool | None = None
    """机器人当前是否已在该群内（由 handler 通过群列表判定）。"""

    extra: dict[str, Any] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)

    # ------------------------------------------------------------------
    # 派生信息
    # ------------------------------------------------------------------
    @property
    def intro(self) -> str:
        """真正可用的群介绍：优先真群简介，其次退回 ``group_memo``。"""
        return self.description or self.memo

    @property
    def intro_is_announcement(self) -> bool:
        """可用的「群介绍」是否其实来自公告。

        只有「协议端没返回真正的群简介」且「``group_memo`` 能在公告里找到」
        时才成立 —— 那说明协议端把公告映射到了 memo、且真群简介没暴露出来
        （SnowLuma <= 1.14.20 的行为）。NapCat 的 ``fingerMemo`` 本身就是群简介，
        不会走到这条判断。
        """
        return not self.description and self.memo_notice_index() >= 0

    @property
    def info_available(self) -> bool:
        """群资料是否真的取到了（而不是空壳）。"""
        return self.found

    @property
    def found(self) -> bool:
        """是否真的查到了这个群（而不是接口返回了一个空壳）。"""
        return bool(self.name) or self.member_count > 0

    @property
    def member_count_known(self) -> bool:
        return self.member_count > 0

    @property
    def keyword_corpus(self) -> str:
        """参与关键词匹配的全部文本。"""
        return "\n".join(
            part
            for part in (
                self.name,
                self.remark,
                self.description,
                self.memo,
                *self.notices,
            )
            if part
        )

    def memo_notice_index(self) -> int:
        """群介绍是否其实来自某条公告，返回公告序号（从 0 开始，未命中返回 -1）。

        **协议端把「公告」映射到了这个字段**：SnowLuma 解析群列表时用的是
        ``memo: raw.info?.announcement || raw.info?.description`` ——
        只要群里有公告，``group_memo`` 返回的就是**公告（的截断预览）**，
        真正的群简介被丢掉了；只有群里没有公告时，它才回落到真正的群简介。
        （非成员群走 ``noticePreview``，同样是公告预览。）

        实测 21 个群完全吻合：18 个有公告的群 memo 都是某条公告的前缀
        （``公告[0][:72] == memo`` 精确成立），2 个零公告的群 memo 与公告无关，
        那才是真正的群简介。

        因此：命中 => ``group_memo`` 是公告、真群简介**取不到**；
        未命中 => ``group_memo`` 就是真正的群简介。
        """
        memo = (self.memo or "").strip()
        if not memo:
            return -1
        # 协议端给的是公告的截断预览，所以用前缀/包含判断
        key = memo[:24]
        if not key:
            return -1
        for index, notice in enumerate(self.notices):
            if notice.startswith(memo) or key in notice:
                return index
        return -1

    @property
    def memo_is_notice_preview(self) -> bool:
        return self.memo_notice_index() >= 0

    def matched_keywords(self, keywords: list[str]) -> list[str]:
        """返回命中的关键词（保持配置顺序，去重）。"""
        corpus = self.keyword_corpus
        if not corpus:
            return []
        hits: list[str] = []
        for keyword in keywords:
            text = keyword.strip()
            if text and text not in hits and text in corpus:
                hits.append(text)
        return hits

    def created_at_text(self) -> str:
        if not self.create_time:
            return "未知"
        try:
            return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(self.create_time))
        except (OSError, ValueError, OverflowError):
            return str(self.create_time)

    # ------------------------------------------------------------------
    # 输出
    # ------------------------------------------------------------------
    def to_dict(self) -> dict[str, Any]:
        return {
            "group_id": self.group_id,
            "name": self.name,
            "remark": self.remark,
            "memo": self.memo,
            "description": self.description,
            "description_source": self.description_source,
            "intro": self.intro,
            "intro_is_announcement": self.intro_is_announcement,
            "member_count": self.member_count,
            "max_member_count": self.max_member_count,
            "create_time": self.create_time,
            "create_time_text": self.created_at_text(),
            "level": self.level,
            "all_shut": self.all_shut,
            "notices": list(self.notices),
            "owner_id": self.owner_id,
            "owner_name": self.owner_name,
            "admin_ids": list(self.admin_ids),
            "admin_names": list(self.admin_names),
            "inviter_id": self.inviter_id,
            "inviter_name": self.inviter_name,
            "is_member": self.is_member,
            "info_available": self.info_available,
            "extra": dict(self.extra),
            "errors": list(self.errors),
            "found": self.found,
        }

    def to_log_text(self) -> str:
        """构造「此群所有信息」的多行日志文本。"""
        member_text = (
            f"{self.member_count} / {self.max_member_count}"
            if self.max_member_count
            else str(self.member_count)
        )
        notice_count = len(self.notices)
        notice_text = self._block("\n---\n".join(self.notices))
        memo_lines = [f"群介绍      : {self._block(self.intro)}"]
        if self.description:
            memo_lines.append(
                f"              └─ ✅ 来自真正的群简介字段"
                f"（协议端 {self.description_source or 'group_description'}）"
            )
        elif self.memo:
            preview_index = self.memo_notice_index()
            if preview_index >= 0:
                memo_lines.append(
                    f"              └─ ⚠ 该项其实是群公告第 {preview_index + 1} 条的截断预览："
                    f"协议端把「公告」映射到了 group_memo（SnowLuma <= 1.14.20: "
                    f"announcement || description），真群简介未暴露 —— "
                    f"升级到 SnowLuma >= 1.14.21 即可返回真正的群简介"
                )
            else:
                memo_lines.append("              └─ ✅ 该群无公告，这里就是真正的群简介")
        lines = [
            "=" * 60,
            f"群号        : {self.group_id}",
            f"群名称      : {self.name or '（未获取到）'}",
            f"群备注      : {self.remark or '（无）'}",
            f"群人数      : {member_text}",
            f"群创建时间  : {self.created_at_text()}",
            f"群等级      : {self.level}",
            f"全员禁言    : {'是' if self.all_shut else '否'}",
            f"群主        : {self._person_text(self.owner_id, self.owner_name)}",
            f"管理员      : {self._admin_text()}",
            *memo_lines,
            f"群公告({notice_count}) : {notice_text}",
        ]
        if self.inviter_id or self.inviter_name:
            lines.append(
                f"邀请人      : {self._person_text(self.inviter_id, self.inviter_name)}"
            )
        if self.extra:
            lines.append("附加信息    :")
            for key, value in self.extra.items():
                lines.append(f"  - {key}: {self._block(str(value), indent=4)}")
        if self.errors:
            lines.append("获取失败字段:")
            for err in self.errors:
                lines.append(f"  ! {err}")
        lines.append("=" * 60)
        return "\n".join(lines)

    @staticmethod
    def _person_text(user_id: int, name: str) -> str:
        if not user_id and not name:
            return "（未获取到）"
        return f"{name or '未知昵称'}({user_id})"

    def _admin_text(self) -> str:
        if not self.admin_ids and not self.admin_names:
            return "（未获取到）"
        pairs = [
            f"{name or '未知昵称'}({uid})"
            for uid, name in zip(self.admin_ids, self.admin_names, strict=False)
        ]
        if not pairs:
            pairs = [str(uid) for uid in self.admin_ids] or list(self.admin_names)
        return "、".join(pairs)

    @staticmethod
    def _block(text: str, indent: int = 0) -> str:
        if not text:
            return "（无）"
        pad = " " * indent
        body = text.replace("\r\n", "\n").strip()
        if "\n" not in body:
            return body
        return "\n" + "\n".join(f"{pad}  {line}" for line in body.split("\n"))


async def check_membership(client: Any, group_id: int) -> bool | None:
    """查询机器人当前是否已在 ``group_id`` 群内。

    这是判断「重复邀请 / 邀请已过时」的可靠依据：
    ``get_group_info`` 对未加入的群只会返回空壳，无法区分「群不存在」与「不是成员」，
    因此必须用群列表（当前花名册）来判定。

    Returns:
        ``True`` 已在群内；``False`` 不在群内；``None`` 无法确定。
    """
    result = await call_action(client, "get_group_list", no_cache=True)
    if not result.ok or not isinstance(result.data, list):
        return None
    for item in result.data:
        if isinstance(item, dict) and _int(item.get("group_id")) == int(group_id):
            return True
    return False


async def fetch_group_info(
    client: Any,
    group_id: int,
    *,
    fetch_notice: bool = True,
    fetch_roster: bool = False,
    no_cache: bool = True,
) -> GroupInfo:
    """采集 ``group_id`` 的全部可用信息，永不抛异常。

    Args:
        client: aiocqhttp 的 ``CQHttp`` 实例（``event.bot``）。
        group_id: 群号。
        fetch_notice: 是否尝试读取群公告（机器人不在群里时通常失败）。
        fetch_roster: 是否尝试读取群主/管理员（需要机器人已在群内）。
        no_cache: 是否强制跳过协议端缓存。
    """
    info = GroupInfo(group_id=int(group_id))

    api, payload = await try_actions(
        client,
        ("get_group_info", "get_group_info_ex"),
        group_id=int(group_id),
        no_cache=no_cache,
    )
    if payload is None:
        info.errors.append(
            "get_group_info 调用失败（协议端不支持，或该群号不存在 / 群信息不可见）"
        )
    elif isinstance(payload.data, dict):
        _apply_group_payload(info, payload.data)
        if not api.startswith("get_group_info"):
            info.errors.append(f"使用的是兼容接口 {api}")
    else:
        info.errors.append(f"{api} 返回了非预期的数据结构: {type(payload.data).__name__}")

    # 群简介补全：NapCat 在「机器人已在群里」时 get_group_info 不含群简介字段，
    # 只有再问一次群详情接口才拿得到（SnowLuma 上这一步不会产生额外开销）。
    if not info.intro:
        await _fill_description(client, info, no_cache=no_cache)

    if not info.found:
        # 这是 SnowLuma / NapCat 这一类 NTQQ 协议端的既有行为：
        # 机器人不在群里时 get_group_info 依然返回 retcode 0，但所有字段都是空值。
        info.errors.append(
            "群资料不可用：机器人不在该群，协议端无法返回群名称 / 群介绍 / 群人数"
            "（get_group_info 返回空壳；这是 QQ 侧限制，不是插件故障）"
        )

    if fetch_notice:
        await _fill_notices(client, info)

    if fetch_roster:
        await _fill_roster(client, info)

    for api_name in _OPTIONAL_APIS:
        result = await call_action(client, api_name, group_id=int(group_id))
        if result.ok and result.data not in (None, {}, []):
            info.extra[api_name] = result.data

    return info


async def scan_group(client: Any, group_id: int) -> GroupInfo:
    """管理页「群聊体检」用：尽量多取信息（含群公告与成员名单）。"""
    return await fetch_group_info(
        client,
        group_id,
        fetch_notice=True,
        fetch_roster=True,
        no_cache=True,
    )


# ----------------------------------------------------------------------
# 申请收件箱（补全群名与邀请人昵称）
# ----------------------------------------------------------------------
@dataclass
class InviteContext:
    """来自协议端「群系统消息」收件箱的补充信息。

    群邀请事件本身不携带群名称与邀请人昵称（SnowLuma 只额外提供 ``invited_id``），
    因此这里通过 ``get_group_system_msg`` / ``get_group_ignored_notifies``
    把这两项补齐，让日志与私聊文案更完整。
    """

    found: bool = False
    group_name: str = ""
    inviter_nick: str = ""
    inviter_id: int = 0
    message: str = ""
    checked: bool | None = None
    flag: str = ""
    source: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "found": self.found,
            "group_name": self.group_name,
            "inviter_nick": self.inviter_nick,
            "inviter_id": self.inviter_id,
            "message": self.message,
            "checked": self.checked,
            "flag": self.flag,
            "source": self.source,
        }


async def fetch_invite_context(
    client: Any,
    *,
    group_id: int,
    flag: str = "",
    inviter_id: int = 0,
) -> InviteContext:
    """在申请收件箱里查找本次邀请，取回群名称与邀请人昵称。"""
    for api in ("get_group_system_msg", "get_group_ignored_notifies"):
        result = await call_action(client, api)
        if not result.ok or not isinstance(result.data, list):
            continue
        entry = _match_request_entry(result.data, group_id, flag, inviter_id)
        if entry is None:
            continue
        return InviteContext(
            found=True,
            group_name=_text(entry.get("group_name")),
            inviter_nick=_text(entry.get("invitor_nick"))
            or _text(entry.get("requester_nick")),
            inviter_id=_int(entry.get("invitor_uin")) or _int(entry.get("requester_uin")),
            message=_text(entry.get("message")),
            checked=entry.get("checked")
            if isinstance(entry.get("checked"), bool)
            else None,
            flag=_text(entry.get("flag")),
            source=api,
        )

    # 收件箱里没有（例如协议端不支持）：退化为查昵称
    if inviter_id:
        info = await call_action(client, "get_stranger_info", user_id=int(inviter_id))
        if info.ok and isinstance(info.data, dict):
            return InviteContext(
                found=True,
                inviter_nick=_text(info.data.get("nickname")),
                inviter_id=int(inviter_id),
                source="get_stranger_info",
            )
    return InviteContext()


def _match_request_entry(
    entries: list[Any],
    group_id: int,
    flag: str,
    inviter_id: int,
) -> dict[str, Any] | None:
    """按 flag → (群号 + 邀请人) → 群号 的优先级匹配申请记录。"""
    candidates = [e for e in entries if isinstance(e, dict)]
    if flag:
        for entry in candidates:
            if _text(entry.get("flag")) == flag:
                return entry
    if group_id:
        same_group = [e for e in candidates if _int(e.get("group_id")) == group_id]
        if inviter_id:
            for entry in same_group:
                if _int(entry.get("invitor_uin")) == inviter_id or _int(
                    entry.get("requester_uin")
                ) == inviter_id:
                    return entry
        if same_group:
            return same_group[0]
    return None


# ----------------------------------------------------------------------
# 内部工具
# ----------------------------------------------------------------------
async def _fill_notices(client: Any, info: GroupInfo) -> None:
    """读取群公告。优先 ``_get_group_notice``（SnowLuma / NapCat 扩展）。"""
    api, result = await try_actions(
        client,
        ("_get_group_notice", "get_group_notice"),
        group_id=info.group_id,
    )
    if result is None:
        info.errors.append(
            "群公告: 读取失败（机器人尚未入群，或协议端不支持该接口）"
        )
        return
    if not isinstance(result.data, list):
        info.errors.append("群公告: 返回了非预期的数据结构")
        return
    for item in result.data:
        text = _notice_text(item)
        if text:
            info.notices.append(text)
    if not info.notices:
        info.errors.append("群公告: 该群暂无公告")
    elif api != "_get_group_notice":
        info.errors.append(f"群公告: 使用的是兼容接口 {api}")


def _notice_text(item: Any) -> str:
    if isinstance(item, str):
        return _normalize_text(item)
    if not isinstance(item, dict):
        return ""
    message = item.get("message")
    if isinstance(message, dict):
        return _normalize_text(message.get("text"))
    if isinstance(message, str):
        return _normalize_text(message)
    return _normalize_text(item.get("content"))


#: QQ 群公告里常见的 HTML 实体，还原后日志可读性更好，关键词也更容易命中。
_HTML_ENTITIES: tuple[tuple[str, str], ...] = (
    ("&#10;", "\n"),
    ("&#13;", "\r"),
    ("&nbsp;", " "),
    ("&quot;", '"'),
    ("&#039;", "'"),
    ("&#39;", "'"),
    ("&apos;", "'"),
    ("&lt;", "<"),
    ("&gt;", ">"),
    ("&amp;", "&"),
)


def _normalize_text(value: Any) -> str:
    """把公告文本里的 HTML 实体还原成正常字符。"""
    text = _text(value)
    if not text or "&" not in text:
        return text
    for entity, char in _HTML_ENTITIES:
        text = text.replace(entity, char)
    return text.strip()


async def _fill_roster(client: Any, info: GroupInfo) -> None:
    """读取群主与管理员（需要机器人已在群内）。"""
    result = await call_action(
        client, "get_group_member_list", group_id=info.group_id
    )
    if not result.ok:
        info.errors.append(f"成员名单: 读取失败（{result.error}）")
        return
    if not isinstance(result.data, list):
        info.errors.append("成员名单: 返回了非预期的数据结构")
        return
    for member in result.data:
        if not isinstance(member, dict):
            continue
        role = _text(member.get("role")).lower()
        user_id = _int(member.get("user_id"))
        name = _text(member.get("card")) or _text(member.get("nickname"))
        if role == "owner":
            info.owner_id, info.owner_name = user_id, name
        elif role == "admin":
            info.admin_ids.append(user_id)
            info.admin_names.append(name)


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    return str(value).strip()


def _first_text(data: dict[str, Any], *keys: str) -> tuple[str, str]:
    """按顺序取第一个非空的文本字段，返回 ``(值, 命中的键名)``。"""
    for key in keys:
        value = _text(data.get(key))
        if value:
            return value, key
    return "", ""


def _apply_group_payload(
    info: GroupInfo,
    data: dict[str, Any],
    *,
    only_missing: bool = False,
) -> None:
    """把协议端返回的群信息映射进 ``GroupInfo``。

    不同协议端的字段名并不统一，这里统一做兼容：

    * ``group_name`` / ``group_remark`` / ``member_count`` / ``max_member_count``
      / ``group_all_shut``：OneBot v11 标准字段，SnowLuma 与 NapCat 一致；
    * **群简介**：SnowLuma >= 1.14.21 用 ``group_description``，
      NapCat 用 ``fingerMemo``（``richFingerMemo`` 是富文本版本）；
    * ``group_memo``：SnowLuma <= 1.14.20 的旧字段
      （``announcement || description``，公告优先），NapCat 不返回；
    * ``group_create_time`` / ``group_level``：目前只有 SnowLuma 返回，
      NapCat 两者都没有，只能保持 0 / 未知。

    Args:
        only_missing: 为 ``True`` 时只填补空白字段，不覆盖已有数据
            （用于「主接口已返回、只是缺群简介」时的补问）。
    """
    description, description_source = _first_text(
        data, "group_description", "fingerMemo", "richFingerMemo"
    )
    values: dict[str, Any] = {
        "name": _text(data.get("group_name")),
        "remark": _text(data.get("group_remark")),
        "memo": _text(data.get("group_memo")),
        "description": description,
        "description_source": description_source,
        "member_count": _int(data.get("member_count")),
        "max_member_count": _int(data.get("max_member_count")),
        "create_time": _int(data.get("group_create_time")),
        "level": _int(data.get("group_level")),
    }
    for field_name, value in values.items():
        if not value:
            continue
        if only_missing and getattr(info, field_name):
            continue
        setattr(info, field_name, value)

    # 全员禁言是布尔值，False 也是有效数据，因此单独处理；
    # 用「或」合并，补问的结果只可能把状态补全，不会把已确认的禁言状态抹掉。
    info.all_shut = info.all_shut or _int(data.get("group_all_shut")) != 0


async def _fill_description(client: Any, info: GroupInfo, *, no_cache: bool) -> None:
    """群简介缺失时，补问一次群详情接口。

    部分协议端（实测 NapCat v4.18.19）的 ``get_group_info`` 在「机器人已在该群」
    时会直接从内存花名册构造结果，只返回 6 个基础字段、**不含任何群简介**；
    只有群详情接口才会真正去查 QQ 详情数据。SnowLuma 上该接口是
    ``get_group_info`` 的同义接口（返回同一份 schema），所以补问不会更差。
    """
    result = await call_action(
        client,
        "get_group_detail_info",
        group_id=info.group_id,
        no_cache=no_cache,
    )
    if result.ok and isinstance(result.data, dict):
        _apply_group_payload(info, result.data, only_missing=True)
    elif not result.unsupported and result.error:
        logger.debug(f"[group_invite] get_group_detail_info 补问群简介失败: {result.error}")


def _int(value: Any) -> int:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return 0


def log_group_info(info: GroupInfo, *, prefix: str = "[group_invite]") -> None:
    """把「此群所有信息」整块打进 AstrBot 日志。"""
    header = f"{prefix} 群邀请 · 群聊完整信息"
    try:
        logger.info(f"{header}\n{info.to_log_text()}")
    except Exception as exc:  # pragma: no cover - 日志本身不应炸
        logger.warning(f"{prefix} 打印群信息失败: {exc}")
