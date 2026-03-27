import asyncio
from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, register
from astrbot.api import logger
from astrbot.core.platform.sources.aiocqhttp.aiocqhttp_message_event import AiocqhttpMessageEvent


@register(
    "astrbot_plugin_group_invite",
    "你的名字",
    "根据群名和群介绍中的关键词自动处理群邀请",
    "1.0.0",
    "https://github.com/你的用户名/astrbot_plugin_group_invite"
)
class GroupInvitePlugin(Star):
    def __init__(self, context: Context):
        super().__init__(context)
        # 配置会在加载时自动注入到 context 中
        # 可以通过 self.get_config() 或 self.context.get_config() 获取
        
    def get_config(self, key: str, default=None):
        """获取配置值"""
        # 从 AstrBot 的配置系统中获取
        config = self.context.get_plugin_config()
        if config and key in config:
            return config[key]
        # 使用 metadata.yaml 中的默认值
        return self.get_default_config().get(key, default)
    
    def get_default_config(self) -> dict:
        """获取默认配置"""
        return {
            "keywords": ["原神", "星穹铁道", "绝区零"],
            "auto_join": True,
            "group_welcome_message": "我是机器人，欢迎使用。",
            "private_reply_message": "已同意进群~",
            "enable_log": True
        }
    
    def contains_keywords(self, text: str) -> bool:
        if not text:
            return False
        text_lower = text.lower()
        keywords = self.get_config("keywords", [])
        for keyword in keywords:
            if keyword.lower() in text_lower:
                if self.get_config("enable_log", True):
                    logger.info(f"文本包含关键词: {keyword}")
                return True
        return False
    
    async def get_group_info(self, client, group_id: int) -> tuple:
        """获取群信息和群介绍"""
        try:
            result = await client.api.call_action(
                action="get_group_info",
                params={"group_id": group_id}
            )
            group_name = result.get("group_name", "")
            group_memo = result.get("group_memo", result.get("description", ""))
            return group_name, group_memo
        except Exception as e:
            logger.error(f"获取群信息失败: {e}")
            return "", ""
    
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
        
        if (raw_message.get("request_type") == "group" and 
            raw_message.get("sub_type") == "invite"):
            
            group_id = raw_message.get("group_id")
            inviter_id = raw_message.get("user_id")
            
            if self.get_config("enable_log", True):
                logger.info(f"收到群邀请: 群ID={group_id}, 邀请人={inviter_id}")
            
            try:
                group_name, group_memo = await self.get_group_info(client, group_id)
                
                if self.get_config("enable_log", True):
                    logger.info(f"群名称: {group_name}")
                    logger.info(f"群介绍: {group_memo}")
                
                group_info = f"{group_name} {group_memo}"
                
                if self.contains_keywords(group_info):
                    # 同意进群邀请
                    await client.api.call_action(
                        action="set_group_add_request",
                        params={
                            "flag": flag,
                            "sub_type": "invite",
                            "approve": True
                        }
                    )
                    
                    if self.get_config("enable_log", True):
                        logger.info(f"已同意进群邀请: 群{group_id}")
                    
                    # 发送私聊消息
                    private_msg = self.get_config("private_reply_message", "已同意进群~")
                    try:
                        await client.api.call_action(
                            action="send_private_msg",
                            params={
                                "user_id": inviter_id,
                                "message": private_msg
                            }
                        )
                        if self.get_config("enable_log", True):
                            logger.info(f"已发送私聊消息给邀请人 {inviter_id}")
                    except Exception as e:
                        logger.error(f"发送私聊消息失败: {e}")
                    
                    await asyncio.sleep(2)
                    
                    # 发送群欢迎消息
                    if self.get_config("auto_join", True):
                        group_msg = self.get_config("group_welcome_message", "我是机器人，欢迎使用。")
                        try:
                            await client.api.call_action(
                                action="send_group_msg",
                                params={
                                    "group_id": group_id,
                                    "message": group_msg
                                }
                            )
                            if self.get_config("enable_log", True):
                                logger.info(f"已在群{group_id}发送欢迎消息")
                        except Exception as e:
                            logger.error(f"发送群欢迎消息失败: {e}")
                else:
                    if self.get_config("enable_log", True):
                        logger.info(f"群邀请不符合关键词条件，不处理: {group_name}")
                        
            except Exception as e:
                logger.error(f"处理群邀请时发生错误: {e}")
                import traceback
                logger.error(traceback.format_exc())
    
    @filter.command("invite_config")
    async def config_command(self, event: AstrMessageEvent):
        """配置插件参数（命令方式）"""
        args = event.message_str.strip().split()
        if len(args) < 2:
            yield event.plain_result(
                "📝 群邀请插件配置命令\n\n"
                "⚠️ 推荐在 AstrBot WebUI 中配置（插件管理 → 群邀请插件 → 配置）\n\n"
                "命令行方式：\n"
                "/invite_config list - 查看当前配置\n"
                "/invite_config add 关键词 - 添加关键词\n"
                "/invite_config remove 关键词 - 移除关键词\n"
                "/invite_config toggle_join - 切换自动进群开关"
            )
            return
        
        action = args[1].lower()
        
        if action == "list":
            keywords = self.get_config("keywords", [])
            auto_join = self.get_config("auto_join", True)
            welcome_msg = self.get_config("group_welcome_message", "我是机器人，欢迎使用。")
            private_msg = self.get_config("private_reply_message", "已同意进群~")
            
            config_text = (
                f"📋 当前配置:\n"
                f"🔑 关键词: {', '.join(keywords)}\n"
                f"🚪 自动进群: {'✅ 开启' if auto_join else '❌ 关闭'}\n"
                f"💬 群欢迎消息: {welcome_msg}\n"
                f"💬 私聊回复: {private_msg}"
            )
            yield event.plain_result(config_text)
            
        elif action == "add" and len(args) >= 3:
            keyword = args[2]
            keywords = self.get_config("keywords", [])
            if keyword not in keywords:
                keywords.append(keyword)
                # 更新配置
                self.context.update_plugin_config({"keywords": keywords})
                yield event.plain_result(f"✅ 已添加关键词: {keyword}")
            else:
                yield event.plain_result(f"⚠️ 关键词已存在: {keyword}")
                
        elif action == "remove" and len(args) >= 3:
            keyword = args[2]
            keywords = self.get_config("keywords", [])
            if keyword in keywords:
                keywords.remove(keyword)
                self.context.update_plugin_config({"keywords": keywords})
                yield event.plain_result(f"✅ 已移除关键词: {keyword}")
            else:
                yield event.plain_result(f"⚠️ 未找到关键词: {keyword}")
                
        elif action == "toggle_join":
            auto_join = not self.get_config("auto_join", True)
            self.context.update_plugin_config({"auto_join": auto_join})
            status = "✅ 开启" if auto_join else "❌ 关闭"
            yield event.plain_result(f"自动进群已{status}")
        else:
            yield event.plain_result("❌ 无效的命令格式，使用 /invite_config 查看帮助")
    
    async def terminate(self):
        logger.info("群邀请插件已卸载")
