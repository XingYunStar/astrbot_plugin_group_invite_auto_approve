import asyncio
import json
import os

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
        self.config_file = os.path.join(os.path.dirname(__file__), "config.json")
        self.config = self.load_config()
        
    def load_config(self) -> dict:
        default_config = {
            "keywords": ["原神", "星穹铁道", "绝区零"],
            "auto_join": True,
            "group_welcome_message": "我是机器人，欢迎使用。",
            "private_reply_message": "已同意进群~",
            "enable_log": True
        }
        
        if os.path.exists(self.config_file):
            try:
                with open(self.config_file, 'r', encoding='utf-8') as f:
                    config = json.load(f)
                    for key in default_config:
                        if key not in config:
                            config[key] = default_config[key]
                    return config
            except Exception as e:
                logger.error(f"加载配置文件失败: {e}")
                return default_config
        else:
            try:
                with open(self.config_file, 'w', encoding='utf-8') as f:
                    json.dump(default_config, f, ensure_ascii=False, indent=4)
                logger.info(f"已创建默认配置文件: {self.config_file}")
            except Exception as e:
                logger.error(f"创建配置文件失败: {e}")
            return default_config
    
    def save_config(self):
        try:
            with open(self.config_file, 'w', encoding='utf-8') as f:
                json.dump(self.config, f, ensure_ascii=False, indent=4)
        except Exception as e:
            logger.error(f"保存配置文件失败: {e}")
    
    def contains_keywords(self, text: str) -> bool:
        if not text:
            return False
        text_lower = text.lower()
        for keyword in self.config["keywords"]:
            if keyword.lower() in text_lower:
                if self.config["enable_log"]:
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
            # 支持 group_memo 和 description 两种字段名
            group_memo = result.get("group_memo", result.get("description", ""))
            return group_name, group_memo
        except Exception as e:
            logger.error(f"获取群信息失败: {e}")
            return "", ""
    
    @filter.platform_adapter_type(filter.PlatformAdapterType.AIOCQHTTP)
    async def event_monitoring(self, event: AstrMessageEvent):
        """监听所有 AIOCQHTTP 平台的事件"""
        raw_message = getattr(event.message_obj, 'raw_message', None)
        
        # 只处理请求类型的事件
        if not isinstance(raw_message, dict) or raw_message.get("post_type") != "request":
            return
        
        if not isinstance(event, AiocqhttpMessageEvent):
            return
        
        client = event.bot
        flag = raw_message.get("flag")
        
        # 处理群邀请
        if (raw_message.get("request_type") == "group" and 
            raw_message.get("sub_type") == "invite"):
            
            group_id = raw_message.get("group_id")
            inviter_id = raw_message.get("user_id")
            
            if self.config["enable_log"]:
                logger.info(f"收到群邀请: 群ID={group_id}, 邀请人={inviter_id}")
            
            try:
                # 获取群名称和群介绍
                group_name, group_memo = await self.get_group_info(client, group_id)
                
                if self.config["enable_log"]:
                    logger.info(f"群名称: {group_name}")
                    logger.info(f"群介绍: {group_memo}")
                
                # 检查群名和群介绍是否包含关键词
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
                    
                    if self.config["enable_log"]:
                        logger.info(f"已同意进群邀请: 群{group_id}")
                    
                    # 发送私聊消息给邀请人
                    try:
                        await client.api.call_action(
                            action="send_private_msg",
                            params={
                                "user_id": inviter_id,
                                "message": self.config["private_reply_message"]
                            }
                        )
                        if self.config["enable_log"]:
                            logger.info(f"已发送私聊消息给邀请人 {inviter_id}")
                    except Exception as e:
                        logger.error(f"发送私聊消息失败: {e}")
                    
                    # 等待进群成功
                    await asyncio.sleep(2)
                    
                    # 发送群欢迎消息
                    if self.config["auto_join"]:
                        try:
                            await client.api.call_action(
                                action="send_group_msg",
                                params={
                                    "group_id": group_id,
                                    "message": self.config["group_welcome_message"]
                                }
                            )
                            if self.config["enable_log"]:
                                logger.info(f"已在群{group_id}发送欢迎消息")
                        except Exception as e:
                            logger.error(f"发送群欢迎消息失败: {e}")
                else:
                    if self.config["enable_log"]:
                        logger.info(f"群邀请不符合关键词条件，不处理: {group_name}")
                        
            except Exception as e:
                logger.error(f"处理群邀请时发生错误: {e}")
                import traceback
                logger.error(traceback.format_exc())
    
    @filter.command("invite_config")
    async def config_command(self, event: AstrMessageEvent):
        """配置插件参数"""
        args = event.message_str.strip().split()
        if len(args) < 2:
            yield event.plain_result(
                "📝 群邀请插件配置命令\n\n"
                "/invite_config list - 查看当前配置\n"
                "/invite_config add 关键词 - 添加关键词\n"
                "/invite_config remove 关键词 - 移除关键词\n"
                "/invite_config set_welcome 欢迎语 - 设置群欢迎消息\n"
                "/invite_config set_reply 私聊回复 - 设置私聊回复消息\n"
                "/invite_config toggle_join - 切换自动进群开关"
            )
            return
        
        action = args[1].lower()
        
        if action == "list":
            config_text = (
                f"📋 当前配置:\n"
                f"🔑 关键词: {', '.join(self.config['keywords'])}\n"
                f"🚪 自动进群: {'✅ 开启' if self.config['auto_join'] else '❌ 关闭'}\n"
                f"💬 群欢迎消息: {self.config['group_welcome_message']}\n"
                f"💬 私聊回复: {self.config['private_reply_message']}"
            )
            yield event.plain_result(config_text)
            
        elif action == "add" and len(args) >= 3:
            keyword = args[2]
            if keyword not in self.config["keywords"]:
                self.config["keywords"].append(keyword)
                self.save_config()
                yield event.plain_result(f"✅ 已添加关键词: {keyword}")
            else:
                yield event.plain_result(f"⚠️ 关键词已存在: {keyword}")
                
        elif action == "remove" and len(args) >= 3:
            keyword = args[2]
            if keyword in self.config["keywords"]:
                self.config["keywords"].remove(keyword)
                self.save_config()
                yield event.plain_result(f"✅ 已移除关键词: {keyword}")
            else:
                yield event.plain_result(f"⚠️ 未找到关键词: {keyword}")
                
        elif action == "set_welcome" and len(args) >= 3:
            welcome_msg = " ".join(args[2:])
            self.config["group_welcome_message"] = welcome_msg
            self.save_config()
            yield event.plain_result(f"✅ 已设置群欢迎消息: {welcome_msg}")
            
        elif action == "set_reply" and len(args) >= 3:
            reply_msg = " ".join(args[2:])
            self.config["private_reply_message"] = reply_msg
            self.save_config()
            yield event.plain_result(f"✅ 已设置私聊回复: {reply_msg}")
            
        elif action == "toggle_join":
            self.config["auto_join"] = not self.config["auto_join"]
            self.save_config()
            status = "✅ 开启" if self.config["auto_join"] else "❌ 关闭"
            yield event.plain_result(f"自动进群已{status}")
        else:
            yield event.plain_result("❌ 无效的命令格式，使用 /invite_config 查看帮助")
    
    async def terminate(self):
        logger.info("群邀请插件已卸载")
