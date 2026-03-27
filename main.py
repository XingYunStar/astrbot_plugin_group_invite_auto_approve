import asyncio
from typing import Any, Dict, List, Optional, Tuple, Union


from astrbot.api import logger
from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, register
from astrbot.core.platform.sources.aiocqhttp.aiocqhttp_message_event import AiocqhttpMessageEvent


@register(
    "astrbot_plugin_group_invite_auto_approve",
    "星陨",
    "根据群信息自动进群",
    "1.0.5",
    "https://github.com/XingYunStar/astrbot_plugin_group_invite_auto_approve"
)
class GroupInvitePlugin(Star):
    """群邀请自动处理插件"""
    
    def __init__(self, context: Context, config: dict):
        super().__init__(context)
        self.config = self._validate_and_normalize_config(config)
        logger.info(f"群邀请插件已加载")
        logger.info(f"配置详情: {self._get_config_summary()}")
    
    def _validate_and_normalize_config(self, config: dict) -> dict:
        """验证并规范化配置"""
        normalized = config.copy()
        
        # 确保 delay_after_join 是有效整数
        delay = normalized.get("delay_after_join", 2)
        try:
            delay_int = int(delay)
            if delay_int < 0:
                delay_int = 0
            elif delay_int > 10:
                delay_int = 10
                logger.warning(f"delay_after_join 超过最大值10，已调整为10")
            normalized["delay_after_join"] = delay_int
        except (ValueError, TypeError):
            logger.warning(f"delay_after_join 配置值无效: {delay}，使用默认值2")
            normalized["delay_after_join"] = 2
        
        # 确保 auto_join 是布尔值
        if "auto_join" in normalized:
            auto_join = normalized["auto_join"]
            if isinstance(auto_join, str):
                normalized["auto_join"] = auto_join.lower() in ("true", "yes", "1", "on")
            elif not isinstance(auto_join, bool):
                normalized["auto_join"] = bool(auto_join)
        
        # 确保 enable_log 是布尔值
        if "enable_log" in normalized:
            enable_log = normalized["enable_log"]
            if isinstance(enable_log, str):
                normalized["enable_log"] = enable_log.lower() in ("true", "yes", "1", "on")
            elif not isinstance(enable_log, bool):
                normalized["enable_log"] = bool(enable_log)
        
        # 确保 retry_on_failure 是布尔值
        if "retry_on_failure" in normalized:
            retry = normalized["retry_on_failure"]
            if isinstance(retry, str):
                normalized["retry_on_failure"] = retry.lower() in ("true", "yes", "1", "on")
            elif not isinstance(retry, bool):
                normalized["retry_on_failure"] = bool(retry)
        
        # 确保 ignore_bot_self 是布尔值
        if "ignore_bot_self" in normalized:
            ignore = normalized["ignore_bot_self"]
            if isinstance(ignore, str):
                normalized["ignore_bot_self"] = ignore.lower() in ("true", "yes", "1", "on")
            elif not isinstance(ignore, bool):
                normalized["ignore_bot_self"] = bool(ignore)
        
        # 确保 check_group_memo 是布尔值
        if "check_group_memo" in normalized:
            check = normalized["check_group_memo"]
            if isinstance(check, str):
                normalized["check_group_memo"] = check.lower() in ("true", "yes", "1", "on")
            elif not isinstance(check, bool):
                normalized["check_group_memo"] = bool(check)
        
        return normalized
    
    def _safe_int_convert(self, value: Any, key: str, default: int = 0) -> int:
        """安全地将值转换为整数"""
        try:
            if value is None:
                return default
            return int(value)
        except (ValueError, TypeError):
            if self.get_config("enable_log", True):
                logger.warning(f"{key} 转换失败: {value}，使用默认值 {default}")
            return default
    
    def _get_config_summary(self) -> str:
        """获取配置摘要"""
        keywords = self.get_config("keywords", [])
        auto_join = self.get_config("auto_join", True)
        check_memo = self.get_config("check_group_memo", True)
        return f"关键词={keywords}, 自动进群={'是' if auto_join else '否'}, 检查群介绍={'是' if check_memo else '否'}"
    
    def get_config(self, key: str, default=None):
        """获取配置值，对消息类配置自动转换换行符"""
        value = self.config.get(key, default)
        
        # 对消息类配置进行换行符转换
        if key in ["group_welcome_message", "private_reply_message"] and isinstance(value, str):
            value = value.replace("\\n", "\n")
        
        return value
    
    def contains_keywords(self, text: str) -> bool:
        """检查文本是否包含任何关键词"""
        if not text:
            return False
        
        text_lower = text.lower()
        keywords = self.get_config("keywords", [])
        
        # 确保 keywords 是列表
        if isinstance(keywords, str):
            keywords = [k.strip() for k in keywords.split(",") if k.strip()]
        elif not isinstance(keywords, list):
            keywords = []
        
        for keyword in keywords:
            if keyword and keyword.lower() in text_lower:
                if self.get_config("enable_log", True):
                    logger.info(f"文本包含关键词: {keyword}")
                return True
        return False
    
    def contains_keywords_in_group(self, group_name: str, group_memo: str) -> bool:
        """检查群名或群介绍是否包含关键词（或关系）"""
        if self.contains_keywords(group_name):
            return True
        if self.get_config("check_group_memo", True) and self.contains_keywords(group_memo):
            return True
        return False
    
    async def get_group_info(self, client, group_id: int) -> Tuple[str, str]:
        """获取群信息和群介绍"""
        try:
            result = await client.get_group_info(group_id=group_id)
            group_name = result.get("group_name", "")
            group_memo = result.get("group_memo", result.get("description", ""))
            return group_name, group_memo
        except Exception as e:
            logger.error(f"获取群信息失败: {e}")
            return "", ""
    
    async def send_message_with_retry(self, send_func, max_retries: int = 1):
        """带重试的消息发送"""
        for attempt in range(max_retries + 1):
            try:
                return await send_func()
            except Exception as e:
                if attempt < max_retries and self.get_config("retry_on_failure", True):
                    logger.warning(f"发送失败，重试 {attempt + 1}/{max_retries}: {e}")
                    await asyncio.sleep(1)
                else:
                    raise e
    
    async def _check_is_self_invite(self, client, inviter_id: int) -> bool:
        """检查是否是机器人自己发起的邀请"""
        if not self.get_config("ignore_bot_self", True):
            return False
        
        try:
            login_info = await client.get_login_info()
            self_uin = login_info.get("user_id")
            return inviter_id == self_uin
        except Exception as e:
            # 记录调试日志，但不中断流程
            if self.get_config("enable_log", True):
                logger.debug(f"获取自身信息失败，无法判断是否自己邀请: {e}")
            return False
    
    @filter.platform_adapter_type(filter.PlatformAdapterType.AIOCQHTTP)
    async def event_monitoring(self, event: AstrMessageEvent):
        """监听所有 AIOCQHTTP 平台的事件"""
        raw_message = getattr(event.message_obj, 'raw_message', None)
        
        if not isinstance(raw_message, dict) or raw_message.get("post_type") != "request":
            return
        
        if not isinstance(event, AiocqhttpMessageEvent):
            return
        
        client = event.bot
        flag = raw_message.get("flag")
        request_type = raw_message.get("request_type")
        sub_type = raw_message.get("sub_type")
        
        if request_type == "group" and sub_type == "invite":
            # 安全转换 group_id 和 inviter_id
            group_id = self._safe_int_convert(raw_message.get("group_id"), "group_id")
            inviter_id = self._safe_int_convert(raw_message.get("user_id"), "inviter_id")
            
            # 转换失败则跳过处理
            if group_id == 0:
                if self.get_config("enable_log", True):
                    logger.warning(f"收到无效的群邀请，group_id 无效: {raw_message.get('group_id')}")
                return
            
            if inviter_id == 0:
                if self.get_config("enable_log", True):
                    logger.warning(f"收到无效的群邀请，inviter_id 无效: {raw_message.get('user_id')}")
                return
            
            # 检查是否忽略自己邀请
            if await self._check_is_self_invite(client, inviter_id):
                if self.get_config("enable_log", True):
                    logger.info(f"忽略自己发起的邀请: 群{group_id}")
                return
            
            if self.get_config("enable_log", True):
                logger.info(f"收到群邀请: 群ID={group_id}, 邀请人={inviter_id}")
            
            try:
                group_name, group_memo = await self.get_group_info(client, group_id)
                
                if self.get_config("enable_log", True):
                    logger.info(f"群名称: {group_name}")
                    if self.get_config("check_group_memo", True):
                        logger.info(f"群介绍: {group_memo}")
                
                # 检查群名或群介绍是否包含关键词
                if self.contains_keywords_in_group(group_name, group_memo):
                    # ========== 关键修复：根据 auto_join 决定是否进群 ==========
                    auto_join = self.get_config("auto_join", True)
                    
                    if auto_join:
                        # 同意进群邀请
                        await client.api.call_action(
                            "set_group_add_request",
                            flag=flag,
                            sub_type="invite",
                            approve=True
                        )
                        if self.get_config("enable_log", True):
                            logger.info(f"已同意进群邀请: 群{group_id}")
                    else:
                        if self.get_config("enable_log", True):
                            logger.info(f"auto_join 为 false，仅发送通知，不进群: 群{group_id}")
                    
                    # 发送私聊消息给邀请人（无论是否进群都发送）
                    private_msg = self.get_config("private_reply_message", "")
                    if private_msg:
                        try:
                            await self.send_message_with_retry(
                                lambda: client.api.call_action(
                                    "send_private_msg",
                                    user_id=inviter_id,
                                    message=private_msg
                                )
                            )
                            if self.get_config("enable_log", True):
                                logger.info(f"已发送私聊消息给邀请人 {inviter_id}")
                        except Exception as e:
                            logger.error(f"发送私聊消息失败: {e}")
                    
                    # 仅当 auto_join 为 True 且成功进群后，才发送群欢迎消息
                    if auto_join:
                        # 等待进群同步
                        delay = self.get_config("delay_after_join", 2)
                        # delay 已经是整数（在初始化时已验证）
                        if delay > 0:
                            await asyncio.sleep(delay)
                        
                        # 发送群欢迎消息
                        group_msg = self.get_config("group_welcome_message", "")
                        if group_msg:
                            try:
                                await self.send_message_with_retry(
                                    lambda: client.api.call_action(
                                        "send_group_msg",
                                        group_id=group_id,
                                        message=group_msg
                                    )
                                )
                                if self.get_config("enable_log", True):
                                    logger.info(f"已在群{group_id}发送欢迎消息")
                            except Exception as e:
                                logger.error(f"发送群欢迎消息失败: {e}")
                else:
                    if self.get_config("enable_log", True):
                        reason = []
                        if not self.contains_keywords(group_name):
                            reason.append("群名无关键词")
                        if self.get_config("check_group_memo", True) and not self.contains_keywords(group_memo):
                            reason.append("群介绍无关键词")
                        logger.info(f"群邀请不符合关键词条件，不处理: {group_name} ({', '.join(reason)})")
                        
            except Exception as e:
                logger.error(f"处理群邀请时发生错误: {e}")
                import traceback
                logger.error(traceback.format_exc())
    
    @filter.command("invite_config")
    async def config_command(self, event: AstrMessageEvent):
        """查看当前配置"""
        keywords = self.get_config("keywords", [])
        auto_join = self.get_config("auto_join", True)
        welcome_msg = self.get_config("group_welcome_message", "")
        private_msg = self.get_config("private_reply_message", "")
        check_memo = self.get_config("check_group_memo", True)
        delay = self.get_config("delay_after_join", 2)
        retry = self.get_config("retry_on_failure", True)
        ignore_self = self.get_config("ignore_bot_self", True)
        
        # 确保 keywords 是列表
        if isinstance(keywords, str):
            keywords = [k.strip() for k in keywords.split(",") if k.strip()]
        
        # 显示时把换行符转义显示
        welcome_display = welcome_msg.replace("\n", "\\n") if welcome_msg else "不发送"
        private_display = private_msg.replace("\n", "\\n") if private_msg else "不发送"
        
        config_text = (
            "📋 群邀请插件当前配置:\n\n"
            f"🔑 关键词: {', '.join(keywords) if keywords else '无'}\n"
            f"🚪 自动进群: {'✅ 开启（会实际进群）' if auto_join else '❌ 关闭（仅发送私聊通知）'}\n"
            f"📝 检查群介绍: {'✅ 开启' if check_memo else '❌ 关闭'}\n"
            f"💬 群欢迎消息: {welcome_display}\n"
            f"💬 私聊回复: {private_display}\n"
            f"⏰ 进群延迟: {delay}秒\n"
            f"🔄 失败重试: {'✅ 开启' if retry else '❌ 关闭'}\n"
            f"🙈 忽略自己邀请: {'✅ 开启' if ignore_self else '❌ 关闭'}\n\n"
            "📌 匹配规则: 群名或群介绍包含任意关键词即触发\n"
            "📌 行为说明:\n"
            "   - auto_join = true: 自动进群 + 发送群欢迎消息\n"
            "   - auto_join = false: 不进群，仅发送私聊通知邀请人\n"
            "💡 提示: 使用 \\n 可以在消息中换行"
        )
        yield event.plain_result(config_text)
    
    async def terminate(self):
        """插件卸载时的清理工作"""
        logger.info("群邀请插件已卸载")
