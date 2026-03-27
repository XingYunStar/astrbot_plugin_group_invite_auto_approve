import asyncio

from astrbot.api import logger
from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, register
from astrbot.core.platform.sources.aiocqhttp.aiocqhttp_message_event import AiocqhttpMessageEvent


@register(
    "astrbot_plugin_group_invite_auto_approve",
    "星陨",
    "根据群信息自动进群",
    "1.0.6",
    "https://github.com/XingYunStar/astrbot_plugin_group_invite_auto_approve"
)
class GroupInvitePlugin(Star):
    def __init__(self, context: Context, config: dict):
        super().__init__(context)
        # config 直接就是配置字典，结构扁平
        self.config = config
        logger.info(f"群邀请插件已加载，撤回时间: {self.config.get('recall_time', 'N/A')}")
        logger.info(f"配置详情: {self._get_config_summary()}")
    
    def _get_config_summary(self) -> str:
        """获取配置摘要"""
        keywords = self.config.get("keywords", [])
        auto_join = self.config.get("auto_join", True)
        check_memo = self.config.get("check_group_memo", True)
        return f"关键词={keywords}, 自动进群={'是' if auto_join else '否'}, 检查群介绍={'是' if check_memo else '否'}"
    
    def get_config(self, key: str, default=None):
        """获取配置值"""
        return self.config.get(key, default)
    
    def contains_keywords(self, text: str) -> bool:
        """检查文本是否包含任何关键词"""
        if not text:
            return False
        
        text_lower = text.lower()
        keywords = self.get_config("keywords", [])
        
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
    
    async def get_group_info(self, client, group_id: int) -> tuple:
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
            group_id = raw_message.get("group_id")
            inviter_id = raw_message.get("user_id")
            
            # 确保是整数类型
            if isinstance(group_id, str):
                group_id = int(group_id)
            if isinstance(inviter_id, str):
                inviter_id = int(inviter_id)
            
            # 检查是否忽略自己邀请
            if self.get_config("ignore_bot_self", True):
                try:
                    login_info = await client.get_login_info()
                    self_uin = login_info.get("user_id")
                    if inviter_id == self_uin:
                        if self.get_config("enable_log", True):
                            logger.info(f"忽略自己发起的邀请: 群{group_id}")
                        return
                except Exception:
                    pass
            
            if self.get_config("enable_log", True):
                logger.info(f"收到群邀请: 群ID={group_id}, 邀请人={inviter_id}")
            
            try:
                group_name, group_memo = await self.get_group_info(client, group_id)
                
                if self.get_config("enable_log", True):
                    logger.info(f"群名称: {group_name}")
                    if self.get_config("check_group_memo", True):
                        logger.info(f"群介绍: {group_memo}")
                
                # 检查群名或群介绍是否包含关键词（任一即可）
                if self.contains_keywords_in_group(group_name, group_memo):
                    # 同意进群邀请
                    await client.api.call_action(
                        "set_group_add_request",
                        flag=flag,
                        sub_type="invite",
                        approve=True
                    )
                    
                    if self.get_config("enable_log", True):
                        logger.info(f"已同意进群邀请: 群{group_id}")
                    
                    # 发送私聊消息给邀请人
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
                    
                    # 等待进群同步
                    delay = self.get_config("delay_after_join", 2)
                    if delay > 0:
                        await asyncio.sleep(delay)
                    
                    # 发送群欢迎消息
                    if self.get_config("auto_join", True):
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
        
        config_text = (
            "📋 群邀请插件当前配置:\n\n"
            f"🔑 关键词: {', '.join(keywords) if keywords else '无'}\n"
            f"🚪 自动进群: {'✅ 开启' if auto_join else '❌ 关闭'}\n"
            f"📝 检查群介绍: {'✅ 开启' if check_memo else '❌ 关闭'}\n"
            f"💬 群欢迎消息: {welcome_msg if welcome_msg else '不发送'}\n"
            f"💬 私聊回复: {private_msg if private_msg else '不发送'}\n"
            f"⏰ 进群延迟: {delay}秒\n"
            f"🔄 失败重试: {'✅ 开启' if retry else '❌ 关闭'}\n"
            f"🙈 忽略自己邀请: {'✅ 开启' if ignore_self else '❌ 关闭'}\n\n"
            "📌 匹配规则: 群名或群介绍包含任意关键词即自动进群\n"
            "💡 提示: 请在插件管理页面修改配置"
        )
        yield event.plain_result(config_text)
    
    async def terminate(self):
        logger.info("群邀请插件已卸载")
