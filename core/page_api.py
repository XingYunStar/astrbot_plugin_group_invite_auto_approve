"""管理页的后端接口。

AstrBot 会把 ``context.register_web_api`` 注册的路由挂到 ``/api/plug/<插件名>/<路径>``，
插件页里的 ``bridge.apiGet("bootstrap")`` 正好请求到这里，因此路由必须以插件名开头。
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any, cast

from astrbot.api import logger
from astrbot.core.star.context import Context

try:  # AstrBot Dashboard 基于 Quart
    from quart import jsonify as quart_jsonify
    from quart import request as quart_request_obj
except ImportError:  # pragma: no cover
    quart_jsonify = None
    quart_request_obj = None

from .config import PluginConfig
from .decision import decide
from .group_info import GroupInfo, fetch_group_info
from .history import InviteHistory

PLUGIN_NAME = "astrbot_plugin_group_invite_auto_approve"


class PageController:
    """插件管理页的所有 HTTP 接口。"""

    def __init__(
        self,
        context: Context,
        cfg: PluginConfig,
        history: InviteHistory,
    ) -> None:
        self.context = context
        self.cfg = cfg
        self.history = history

    # ------------------------------------------------------------------
    # 路由注册
    # ------------------------------------------------------------------
    def register_routes(self) -> None:
        routes: list[tuple[str, Callable[[], Awaitable], list[str], str]] = [
            ("/ping", self.page_ping, ["GET"], "Page ping"),
            (
                "/bootstrap",
                self.page_bootstrap,
                ["GET"],
                "Load management page bootstrap data",
            ),
            ("/config", self.page_get_config, ["GET"], "Read plugin config"),
            ("/config", self.page_save_config, ["POST"], "Save plugin config"),
            ("/history", self.page_history, ["GET"], "List invite records"),
            (
                "/history/clear",
                self.page_clear_history,
                ["POST"],
                "Clear invite records",
            ),
            ("/scan", self.page_scan, ["POST"], "Fetch live group info"),
            ("/simulate", self.page_simulate, ["POST"], "Dry-run the decision rules"),
        ]
        for path, handler, methods, desc in routes:
            self.context.register_web_api(
                f"/{PLUGIN_NAME}{path}",
                self._wrap(handler),
                methods,
                desc,
            )

    # ------------------------------------------------------------------
    # 基础设施
    # ------------------------------------------------------------------
    @staticmethod
    def _check_quart() -> None:
        if quart_jsonify is None or quart_request_obj is None:
            raise RuntimeError("Web framework is unavailable")

    @classmethod
    def _jsonify(cls, payload: dict[str, Any]):
        cls._check_quart()
        return cast(Callable[[dict[str, Any]], Any], quart_jsonify)(payload)

    @classmethod
    def _request(cls):
        cls._check_quart()
        return cast(Any, quart_request_obj)

    @classmethod
    def _ok(cls, data: Any = None, message: str = ""):
        return cls._jsonify({"ok": True, "message": message, "data": data})

    @classmethod
    def _fail(cls, message: str, data: Any = None):
        # 统一返回 200，把错误放在 body 里，避免 axios 抛出通用错误而丢掉具体原因
        return cls._jsonify(
            {"ok": False, "status": "error", "message": message, "data": data}
        )

    def _wrap(self, handler: Callable[[], Awaitable]):
        async def wrapped():
            self._check_quart()
            try:
                return await handler()
            except ValueError as exc:
                return self._fail(str(exc))
            except Exception as exc:  # noqa: BLE001
                logger.exception("[group_invite] 管理页接口异常")
                return self._fail(f"{type(exc).__name__}: {exc}")

        wrapped.__name__ = getattr(handler, "__name__", "handler")
        return wrapped

    # ------------------------------------------------------------------
    # 接口实现
    # ------------------------------------------------------------------
    async def page_ping(self):
        return self._ok({"pong": True})

    async def page_bootstrap(self):
        return self._ok(
            {
                "meta": {
                    "pluginName": PLUGIN_NAME,
                    "displayName": "群邀请自动处理",
                    "version": "v1.0.0",
                },
                "config": self.cfg.to_dict(),
                "policy": self.cfg.policy_summary(),
                "stats": self.history.stats(),
                "platforms": self._list_platforms(),
                "history": self.history.list(20),
            }
        )

    async def page_get_config(self):
        return self._ok(
            {"config": self.cfg.to_dict(), "policy": self.cfg.policy_summary()}
        )

    async def page_save_config(self):
        payload = await self._request().get_json(force=True, silent=True) or {}
        incoming = payload.get("config") if "config" in payload else payload
        if not isinstance(incoming, dict):
            return self._fail("请求体格式错误：缺少 config 对象")
        saved = self.cfg.apply_payload(incoming)
        return self._ok(
            {"config": saved, "policy": self.cfg.policy_summary()},
            message="配置已保存",
        )

    async def page_history(self):
        raw_limit = self._request().args.get("limit", "50")
        try:
            limit = int(raw_limit)
        except (TypeError, ValueError):
            limit = 50
        return self._ok(
            {
                "records": self.history.list(limit),
                "stats": self.history.stats(),
            }
        )

    async def page_clear_history(self):
        self.history.clear()
        return self._ok({"stats": self.history.stats()}, message="记录已清空")

    async def page_scan(self):
        payload = await self._request().get_json(force=True, silent=True) or {}
        group_id = _to_int(payload.get("group_id"))
        if not group_id:
            return self._fail("请填写有效的群号")

        client = self._pick_client()
        if client is None:
            return self._fail("没有可用的 aiocqhttp 平台连接，无法查询群信息")

        # 管理页可以指定是否读取群公告 / 成员名单
        fetch_notice = bool(payload.get("fetch_notice", self.cfg.fetch_notice))
        fetch_roster = bool(payload.get("fetch_roster", True))

        info = await fetch_group_info(
            client,
            group_id,
            fetch_notice=fetch_notice,
            fetch_roster=fetch_roster,
            no_cache=True,
        )
        decision = decide(self.cfg, info)
        return self._ok({"group": info.to_dict(), "decision": decision.to_dict()})

    async def page_simulate(self):
        """用管理页手填的数据离线试算规则，不产生任何真实操作。"""
        payload = await self._request().get_json(force=True, silent=True) or {}
        group_id = _to_int(payload.get("group_id")) or 123456789
        info = GroupInfo(
            group_id=group_id,
            name=str(payload.get("name") or "").strip(),
            remark=str(payload.get("remark") or "").strip(),
            memo=str(payload.get("memo") or "").strip(),
            member_count=_to_int(payload.get("member_count")),
            max_member_count=_to_int(payload.get("max_member_count")) or 3000,
            notices=_as_text_list(payload.get("notices")),
        )
        decision = decide(self.cfg, info)
        return self._ok({"group": info.to_dict(), "decision": decision.to_dict()})

    # ------------------------------------------------------------------
    # 平台
    # ------------------------------------------------------------------
    def _iter_aiocqhttp_platforms(self) -> list[Any]:
        try:
            insts = list(self.context.platform_manager.platform_insts)
        except AttributeError:
            return []
        result: list[Any] = []
        for inst in insts:
            client = getattr(inst, "bot", None)
            if client is not None and hasattr(client, "call_action"):
                result.append(inst)
        return result

    def _pick_client(self) -> Any | None:
        insts = self._iter_aiocqhttp_platforms()
        return getattr(insts[0], "bot", None) if insts else None

    def _list_platforms(self) -> list[dict[str, Any]]:
        platforms: list[dict[str, Any]] = []
        for inst in self._iter_aiocqhttp_platforms():
            try:
                meta = inst.meta()
                platforms.append(
                    {
                        "id": getattr(meta, "id", ""),
                        "name": getattr(meta, "name", ""),
                        "self_id": str(getattr(inst, "client_self_id", "") or ""),
                    }
                )
            except Exception:  # noqa: BLE001
                continue
        return platforms


def _to_int(value: Any) -> int:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return 0


def _as_text_list(value: Any) -> list[str]:
    if isinstance(value, str):
        return [part.strip() for part in value.splitlines() if part.strip()]
    if isinstance(value, (list, tuple)):
        return [str(item).strip() for item in value if str(item).strip()]
    return []
