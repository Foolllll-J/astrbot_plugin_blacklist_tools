import json
from os import path
import sys
from datetime import datetime, timedelta
from astrbot.api.event import filter, AstrMessageEvent, MessageChain
from astrbot.api.star import Context, Star, register, StarTools
from astrbot.api import logger, AstrBotConfig
import astrbot.api.message_components as Comp
from .utils.text_to_image import text_to_image
from .database import BlacklistDatabase


@register(
    "astrbot_plugin_blacklist_tools",
    "ctrlkk",
    "允许管理员和 LLM 将用户添加到黑名单中，阻止他们的消息，自动拉黑！",
    "1.6",
)
class MyPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        data_dir = StarTools.get_data_dir()
        self.db_path = path.join(data_dir, "blacklist.db")

        # 黑名单最长时长
        self.max_blacklist_duration = config.get(
            "max_blacklist_duration", 1 * 24 * 60 * 60
        )
        # 是否允许永久黑名单
        self.allow_permanent_blacklist = config.get("allow_permanent_blacklist", True)
        # 是否向被拉黑用户显示拉黑状态
        self.show_blacklist_status = config.get("show_blacklist_status", True)
        # 黑名单提示消息
        self.blacklist_message = config.get("blacklist_message", "[连接已中断]")
        # 自动删除过期多久的黑名单
        self.auto_delete_expired_after = config.get("auto_delete_expired_after", 86400)
        # 是否允许拉黑管理员
        self.allow_blacklist_admin = config.get("allow_blacklist_admin", False)

        self.db = BlacklistDatabase(self.db_path, self.auto_delete_expired_after)

    async def initialize(self):
        """可选择实现异步的插件初始化方法，当实例化该插件类之后会自动调用该方法。"""
        await self.db.initialize()

    async def terminate(self):
        """可选择实现异步的插件销毁方法，当插件被卸载/停用时会调用。"""
        await self.db.terminate()

    def _format_datetime(
        self, iso_datetime_str, show_remaining=False, check_expire=False
    ):
        """统一格式化日期时间字符串
        Args:
            iso_datetime_str: ISO格式的日期时间字符串
            show_remaining: 是否显示剩余时间
            check_expire: 是否检查是否过期（仅对过期时间有效）
        """
        if not iso_datetime_str:
            return "永久"
        try:
            datetime_obj = datetime.fromisoformat(iso_datetime_str)
            formatted_time = datetime_obj.strftime("%Y-%m-%d %H:%M:%S")

            if check_expire:
                if datetime.now() > datetime_obj:
                    return "已过期"

            if show_remaining:
                if datetime.now() > datetime_obj:
                    return "已过期"
                else:
                    remaining_time = datetime_obj - datetime.now()
                    days = remaining_time.days
                    hours, remainder = divmod(remaining_time.seconds, 3600)
                    minutes, _ = divmod(remainder, 60)
                    return (
                        f"{formatted_time} (剩余: {days}天 {hours}小时 {minutes}分钟)"
                    )
            else:
                return formatted_time
        except Exception as e:
            logger.error(f"格式化日期时间时出错：{e}")
            return "格式错误"

    @filter.event_message_type(filter.EventMessageType.ALL, priority=sys.maxsize - 1)
    async def on_all_message(self, event: AstrMessageEvent):
        if not event.is_at_or_wake_command:
            return

        sender_id = event.get_sender_id()
        try:
            if event.is_admin() and not self.allow_blacklist_admin:
                return

            if await self.db.is_user_blacklisted(sender_id):
                event.stop_event()
                if not event.get_messages():
                    pass
                elif self.show_blacklist_status:
                    await event.send(MessageChain().message(self.blacklist_message))

        except Exception as e:
            logger.error(f"检查黑名单时出错：{e}")

    @filter.command_group("blacklist", alias={"black", "bl"})
    def blacklist():
        pass

    @filter.permission_type(filter.PermissionType.ADMIN)
    @blacklist.command("ls")
    async def ls(self, event: AstrMessageEvent, page: int = 1, page_size: int = 10):
        """列出黑名单中的所有用户（支持分页）
        Args:
            page: 页码，从1开始
            page_size: 每页显示的数量
        """
        try:
            total_count = await self.db.get_blacklist_count()

            if total_count == 0:
                yield event.plain_result("黑名单为空。")
                return

            # 计算分页参数
            total_pages = (total_count + page_size - 1) // page_size
            if page < 1:
                page = 1
            elif page > total_pages:
                page = total_pages

            users = await self.db.get_blacklist_users(page, page_size)

            result = "黑名单列表\n"
            result += "=" * 60 + "\n\n"

            result += f"{'ID':<20} {'加入时间':<20} {'过期时间':<20} {'原因':<20}\n"
            result += "-" * 80 + "\n"

            for user in users:
                user_id, ban_time, expire_time, reason = user
                ban_time_str = self._format_datetime(ban_time, check_expire=False)
                expire_time_str = self._format_datetime(expire_time, check_expire=True)
                reason_str = reason if reason else "无"
                result += f"{user_id:<20} {ban_time_str:<20} {expire_time_str:<20} {reason_str:<20}\n"

            result += "-" * 80 + "\n"
            result += f"第 {page}/{total_pages} 页，共 {total_count} 条记录\n"
            result += f"每页显示 {page_size} 条记录\n"

            if page > 1:
                result += f"使用 `/black ls {page - 1} {page_size}` 查看上一页\n"
            if page < total_pages:
                result += f"使用 `/black ls {page + 1} {page_size}` 查看下一页\n"

            image_data = await text_to_image(result)
            if image_data:
                yield event.chain_result([Comp.Image.fromBase64(image_data)])
            else:
                yield event.plain_result(result)
        except Exception as e:
            logger.error(f"列出黑名单时出错：{e}")
            yield event.plain_result("列出黑名单时出错。")

    @filter.permission_type(filter.PermissionType.ADMIN)
    @blacklist.command("rm")
    async def rm(self, event: AstrMessageEvent, user_id: str):
        """从黑名单中移除用户"""
        try:
            user = await self.db.get_user_info(user_id)

            if not user:
                yield event.plain_result(f"用户 {user_id} 不在黑名单中。")
                return

            if await self.db.remove_user(user_id):
                yield event.plain_result(f"用户 {user_id} 已解除拉黑。")
            else:
                yield event.plain_result("解除拉黑用户时出错。")
        except Exception as e:
            logger.error(f"解除拉黑用户 {user_id} 时出错：{e}")
            yield event.plain_result("解除拉黑用户时出错。")

    @filter.permission_type(filter.PermissionType.ADMIN)
    @blacklist.command("add")
    async def add(
        self, event: AstrMessageEvent, user_id: str, duration: int = 0, reason: str = ""
    ):
        """添加用户到黑名单"""
        try:
            ban_time = datetime.now().isoformat()
            expire_time = None

            if duration > 0:
                expire_time = (datetime.now() + timedelta(seconds=duration)).isoformat()

            if await self.db.add_user(user_id, ban_time, expire_time, reason):
                if duration > 0:
                    yield event.plain_result(
                        f"用户 {user_id} 已被加入黑名单，时长 {duration} 秒。"
                    )
                else:
                    yield event.plain_result(f"用户 {user_id} 已被永久加入黑名单。")
            else:
                yield event.plain_result("添加用户到黑名单时出错。")

        except Exception as e:
            logger.error(f"添加用户 {user_id} 到黑名单时出错：{e}")
            yield event.plain_result("添加用户到黑名单时出错。")

    @filter.permission_type(filter.PermissionType.ADMIN)
    @blacklist.command("clear")
    async def clear(self, event: AstrMessageEvent):
        """清空黑名单"""
        try:
            count = await self.db.get_blacklist_count()

            if count == 0:
                yield event.plain_result("黑名单已经为空。")
                return

            if await self.db.clear_blacklist():
                yield event.plain_result(f"黑名单已清空，共移除 {count} 个用户。")
            else:
                yield event.plain_result("清空黑名单时出错。")
        except Exception as e:
            logger.error(f"清空黑名单时出错：{e}")
            yield event.plain_result("清空黑名单时出错。")

    @filter.permission_type(filter.PermissionType.ADMIN)
    @blacklist.command("info")
    async def info(self, event: AstrMessageEvent, user_id: str):
        """查看特定用户的黑名单信息"""
        try:
            user = await self.db.get_user_info(user_id)

            if not user:
                yield event.plain_result(f"用户 {user_id} 不在黑名单中。")
                return

            user_id, ban_time, expire_time, reason = user
            ban_time_str = self._format_datetime(ban_time, check_expire=False)
            expire_time_str = self._format_datetime(
                expire_time, show_remaining=True, check_expire=True
            )
            reason_str = reason if reason else "无"

            result = f"用户 {user_id} 的黑名单信息：\n"
            result += "=" * 40 + "\n"
            result += f"加入时间: {ban_time_str}\n"
            result += f"过期时间: {expire_time_str}\n"
            result += f"原因: {reason_str}\n"

            image_data = await text_to_image(result)
            if image_data:
                yield event.chain_result([Comp.Image.fromBase64(image_data)])
            else:
                yield event.plain_result(result)
        except Exception as e:
            logger.error(f"查看用户 {user_id} 黑名单信息时出错：{e}")
            yield event.plain_result("查看用户黑名单信息时出错。")

    @filter.llm_tool(name="block_user")
    async def block_user(
        self, event: AstrMessageEvent, user_id: str = None, duration: int = 0, reason: str = ""
    ) -> str:
        """
        将指定用户加入黑名单。加入后，该用户的所有消息将被忽略。
        如果未提供 user_id，则默认拉黑当前发送者。
        
        Args:
            user_id (string): 要拉黑的用户 ID。可选，默认为当前发送者。
            duration (number): 拉黑时长（秒）。0 表示永久拉黑。默认为 0。
            reason (string): 拉黑原因。
        """
        target_id = user_id if user_id else event.get_sender_id()
        try:
            # 权限检查
            sender_id = event.get_sender_id()
            self_id = event.get_self_id()
            is_admin = event.is_admin()
            
            # 权限逻辑：
            # 1. 如果是拉黑自己（当前消息发送者），允许（Bot 自卫/用户自首）
            # 2. 如果是管理员在操作，允许
            # 3. 如果是 Bot 自身发起的决策（针对当前对话者），允许
            # 4. 只有在“非管理员”尝试拉黑“其他人”时，才拒绝
            is_self_defense = (target_id == sender_id)
            
            if not is_admin and not is_self_defense and sender_id != self_id:
                 return json.dumps({
                    "success": False,
                    "message": f"权限不足。您({sender_id})没有权限拉黑其他用户({target_id})。"
                }, ensure_ascii=False)
            
            # 安全检查：不允许拉黑管理员（除非配置允许）
            if target_id == sender_id and is_admin and not self.allow_blacklist_admin:
                return json.dumps({
                    "success": False,
                    "message": "不能拉黑管理员。"
                }, ensure_ascii=False)

            # 检查用户是否已在黑名单中（这会自动清理过期记录）
            if await self.db.is_user_blacklisted(target_id):
                return json.dumps({
                    "success": True,
                    "message": f"用户 {target_id} 已在黑名单中，无需重复添加。",
                    "user_id": target_id
                }, ensure_ascii=False)

            ban_time = datetime.now().isoformat()
            expire_time = None
            actual_duration = duration

            # 如果不允许永久黑名单，则使用默认时长
            if duration == 0 and not self.allow_permanent_blacklist:
                actual_duration = self.max_blacklist_duration

            # 超出使用最大时间
            if actual_duration > self.max_blacklist_duration:
                actual_duration = self.max_blacklist_duration

            if actual_duration > 0:
                expire_time = (
                    datetime.now() + timedelta(seconds=actual_duration)
                ).isoformat()

            await self.db.add_user(target_id, ban_time, expire_time, reason)
            logger.info(f"用户 {target_id} 已由 {sender_id} 通过 LLM 工具拉黑。时长: {actual_duration if actual_duration > 0 else '永久'}, 原因: {reason}")
            
            return json.dumps({
                "success": True,
                "message": f"用户 {target_id} 已拉黑。",
                "user_id": target_id,
                "duration": actual_duration if actual_duration > 0 else "永久",
                "reason": reason
            }, ensure_ascii=False)

        except Exception as e:
            logger.error(f"添加用户 {target_id} 到黑名单时出错：{e}")
            return json.dumps({
                "success": False,
                "message": f"操作失败：{str(e)}"
            }, ensure_ascii=False)

    @filter.llm_tool(name="unblock_user")
    async def unblock_user(
        self, event: AstrMessageEvent, user_id: str
    ) -> str:
        """
        从黑名单移除用户。
        
        Args:
            user_id (string): 要从黑名单移除的用户ID
        """
        try:
            sender_id = event.get_sender_id()
            self_id = event.get_self_id()
            is_admin = event.is_admin()
            
            # 解封逻辑：必须是管理员，或者 Bot 自身决策
            if not is_admin and sender_id != self_id:
                return json.dumps({
                    "success": False,
                    "message": "权限不足。只有管理员可以解除拉黑。"
                }, ensure_ascii=False)

            user = await self.db.get_user_info(user_id)

            if not user:
                return json.dumps({
                    "success": True,
                    "message": f"用户 {user_id} 不在黑名单中。",
                    "user_id": user_id
                }, ensure_ascii=False)

            if await self.db.remove_user(user_id):
                logger.info(f"用户 {user_id} 已由 {sender_id} 通过 LLM 工具解除拉黑。")
                return json.dumps({
                    "success": True,
                    "message": f"用户 {user_id} 已解除拉黑。",
                    "user_id": user_id
                }, ensure_ascii=False)
            else:
                return json.dumps({
                    "success": False,
                    "message": "解除拉黑用户时失败。"
                }, ensure_ascii=False)
        except Exception as e:
            logger.error(f"从黑名单移除用户 {user_id} 时出错：{e}")
            return json.dumps({
                "success": False,
                "message": f"操作失败：{str(e)}"
            }, ensure_ascii=False)

    @filter.llm_tool(name="list_blacklist")
    async def list_blacklist(
        self, event: AstrMessageEvent, page: int = 1, page_size: int = 10
    ) -> str:
        """
        获取当前黑名单列表。
        
        Args:
            page (number): 页码，从1开始，默认为1
            page_size (number): 每页显示的数量，默认为10
        """
        try:
            total_count = await self.db.get_blacklist_count()

            if total_count == 0:
                return json.dumps({
                    "total_count": 0,
                    "users": []
                }, ensure_ascii=False)

            total_pages = (total_count + page_size - 1) // page_size
            if page < 1: page = 1
            elif page > total_pages: page = total_pages

            users_data = await self.db.get_blacklist_users(page, page_size)
            
            users = []
            for user in users_data:
                user_id, ban_time, expire_time, reason = user
                users.append({
                    "user_id": user_id,
                    "ban_time": ban_time,
                    "expire_time": expire_time if expire_time else "永久",
                    "reason": reason if reason else "无"
                })

            return json.dumps({
                "total_count": total_count,
                "total_pages": total_pages,
                "current_page": page,
                "page_size": page_size,
                "users": users
            }, ensure_ascii=False)
        except Exception as e:
            logger.error(f"列出黑名单时出错：{e}")
            return json.dumps({
                "error": f"查询失败：{str(e)}"
            }, ensure_ascii=False)

    @filter.llm_tool(name="get_blacklist_status")
    async def get_blacklist_status(self, event: AstrMessageEvent, user_id: str = None) -> str:
        """
        查询特定用户的黑名单状态。
        
        Args:
            user_id (string): 要查询的用户 ID。可选，默认为当前发送者。
        """
        target_id = user_id if user_id else event.get_sender_id()
        try:
            user_info = await self.db.get_user_info(target_id)
            if user_info:
                user_id, ban_time, expire_time, reason = user_info
                return json.dumps({
                    "is_blacklisted": True,
                    "user_id": user_id,
                    "ban_time": ban_time,
                    "expire_time": expire_time if expire_time else "永久",
                    "reason": reason if reason else "无"
                }, ensure_ascii=False)
            else:
                return json.dumps({
                    "is_blacklisted": False,
                    "user_id": target_id
                }, ensure_ascii=False)
        except Exception as e:
            logger.error(f"查询用户 {target_id} 黑名单状态时出错：{e}")
            return json.dumps({
                "error": f"查询失败：{str(e)}"
            }, ensure_ascii=False)
