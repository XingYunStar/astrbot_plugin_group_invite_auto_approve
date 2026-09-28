"""群邀请处理记录。

管理页需要展示「最近发生了什么」，所以这里维护一个带上限的环形记录，
并落盘到插件数据目录，重启 AstrBot 后依然能看到历史。
"""

from __future__ import annotations

import json
import time
from collections import deque
from pathlib import Path
from typing import Any

from astrbot.api import logger

MAX_RECORDS_HARD_LIMIT = 5000


class InviteHistory:
    """群邀请记录存储（内存环形队列 + JSON 落盘）。"""

    def __init__(self, data_dir: Path, filename: str = "invite_history.json") -> None:
        self.path = Path(data_dir) / filename
        self._records: deque[dict[str, Any]] = deque()
        self.total_invites = 0
        self.total_approved = 0
        self.total_rejected = 0
        self.total_ignored = 0
        self.load()

    # ------------------------------------------------------------------
    # 持久化
    # ------------------------------------------------------------------
    def load(self) -> None:
        if not self.path.is_file():
            return
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning(f"[group_invite] 邀请记录读取失败，已忽略: {exc}")
            return

        records = payload.get("records")
        if isinstance(records, list):
            self._records.extend(r for r in records if isinstance(r, dict))
        stats = payload.get("stats")
        if isinstance(stats, dict):
            self.total_invites = int(stats.get("invites", 0) or 0)
            self.total_approved = int(stats.get("approved", 0) or 0)
            self.total_rejected = int(stats.get("rejected", 0) or 0)
            self.total_ignored = int(stats.get("ignored", 0) or 0)

    def _flush(self) -> None:
        payload = {
            "updated_at": time.time(),
            "stats": {
                "invites": self.total_invites,
                "approved": self.total_approved,
                "rejected": self.total_rejected,
                "ignored": self.total_ignored,
            },
            "records": list(self._records),
        }
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            tmp.replace(self.path)
        except OSError as exc:
            logger.warning(f"[group_invite] 邀请记录写入失败: {exc}")

    # ------------------------------------------------------------------
    # 读写
    # ------------------------------------------------------------------
    def add(
        self,
        record: dict[str, Any],
        limit: int,
        *,
        count_stats: bool = True,
    ) -> None:
        """追加一条记录。

        Args:
            record: 记录内容。
            limit: 保留的最大条数。
            count_stats: 是否计入「邀请总数」等统计（入群通知等补充记录传 ``False``）。
        """
        limit = max(1, min(int(limit or 100), MAX_RECORDS_HARD_LIMIT))
        record.setdefault("timestamp", time.time())
        self._records.appendleft(record)
        while len(self._records) > limit:
            self._records.pop()

        if count_stats:
            self.total_invites += 1
            approve = record.get("approve")
            if approve is True:
                self.total_approved += 1
            elif approve is False:
                self.total_rejected += 1
            else:
                self.total_ignored += 1
        self._flush()

    def list(self, limit: int = 50) -> list[dict[str, Any]]:
        limit = max(0, min(int(limit or 0), MAX_RECORDS_HARD_LIMIT))
        return list(self._records)[:limit] if limit else []

    def stats(self) -> dict[str, int]:
        return {
            "invites": self.total_invites,
            "approved": self.total_approved,
            "rejected": self.total_rejected,
            "ignored": self.total_ignored,
            "kept": len(self._records),
        }

    def clear(self, *, reset_stats: bool = True) -> None:
        """清空历史记录；``reset_stats=True`` 时同时归零累计统计。"""
        self._records.clear()
        if reset_stats:
            self.total_invites = 0
            self.total_approved = 0
            self.total_rejected = 0
            self.total_ignored = 0
        self._flush()
