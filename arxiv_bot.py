# arxiv_bot.py
import asyncio
import logging
import asyncio
import re
import time
from typing import Dict, Any, Optional, List
from datetime import datetime, timedelta

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes, AIORateLimiter, CallbackQueryHandler, ConversationHandler, MessageHandler, filters

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

# 定义对话状态
SETTING_KEYWORDS, ADDING_KEYWORD, ADDING_MAX_RESULTS, DELETING_KEYWORD = range(4)


def escape_markdown_v2(text: str) -> str:
    """
    转义 Telegram MarkdownV2 所有特殊字符
    Telegram MarkdownV2 特殊字符: _ * [ ] ( ) ~ > # + - = | { } . !
    """
    if not text:
        return ""
    # 所有需要转义的字符
    escape_chars = r"_*[]()~>#+-=|{}.!"
    # 使用正则转义每个特殊字符
    return re.sub(f'([{re.escape(escape_chars)}])', r'\\\1', text)


class ArxivBot:
    """Telegram Arxiv Bot"""

    def __init__(self, config: dict, db, arxiv_client):
        self.config = config
        self.db = db
        self.arxiv_client = arxiv_client
        self.token = config["telegram"]["token"]
        self.fetch_interval_hours = config["arxiv"].get("fetch_interval_hours", 6)
        self.session_manager = SessionManager(timeout=180)  # 会话超时 180 秒

        # Register post_init on the builder before building the Application
        builder = ApplicationBuilder()\
            .token(self.token)\
            .rate_limiter(AIORateLimiter())\
            .post_init(self._start_background)
        self.app = builder.build()
        self._register_handlers()

    def _register_handlers(self):
        self.app.add_handler(CommandHandler("start", self.start))
        self.app.add_handler(CommandHandler("fetch_now", self.fetch_now))
        self.app.add_handler(CommandHandler("show", self.show))
        # Flow 命令
        self.app.add_handler(CommandHandler("set_keywords", self.handle_set_keywords))
        self.app.add_handler(CallbackQueryHandler(self.handle_callback))
        self.app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_message))

    async def handle_set_keywords(self, update, context):
        session = self.session_manager.get_or_create(update.effective_user.id)
        flow = SetKeywordsFlow(self.db, self.config)
        session.flow = flow
        await flow.start(update, context, session)

    async def handle_callback(self, update, context):
        session = self.session_manager.get_or_create(update.effective_user.id)
        if session.flow:
            await session.flow.on_callback(update, context, session)

    async def handle_message(self, update, context):
        session = self.session_manager.get_or_create(update.effective_user.id)
        if session.flow:
            await session.flow.on_message(update, context, session)
            # self.app.add_handler(self.get_conversation_handler())

    # ---------------------------
    # 命令处理函数
    # ---------------------------
    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """处理 /start 命令，发送欢迎消息"""
        message = "您好！我是您的 Arxiv 机器人。\n\n本机器人会定期为您推送最新的 **Arxiv** 论文。\n您只需要设定检索式，便可以开始接收推送。当前管理员设定的抓取间隔为 6 小时。\n\n我将通过API获取检索论文并使用AI为您生成标签和摘要。 \n\n*请注意，检索式请尽量使用all字段进行组合查询，title字段可能获取不到预期的结果。(跟网页查询存在出入)*\n我将按照发布时间降序推送。但都是最新的论文。请不用担心时间顺序。\n以下是检索式例子：\n\n`cat:cs.CV AND (all:\"object detection\")`\n"
        await update.message.reply_text(message, parse_mode="Markdown")

    async def show(self, update, context):
        """处理 /show 命令，显示当前用户的检索式"""
        user_id = update.effective_user.id
        user_config = await asyncio.to_thread(self.db.get_user_config, user_id)
        message_text = []
        message_text.append(f"当前管理员设定的抓取间隔为 {self.fetch_interval_hours} 小时。\n\n")
        if not user_config or not user_config.search_queries:
            message_text.append("您还没有设置任何检索式。使用 /set_keywords 来添加检索式。")
        else:
            existing_queries = user_config.search_queries
            message_text.append("📋 您当前的检索式：\n\n")
            for i, query_obj in enumerate(existing_queries, 1):
                message_text.append(
                    f"{i}. {query_obj['query']} (最大结果: {query_obj['max_results']})\n")
        message = "".join(message_text)
        await update.message.reply_text(message, parse_mode="Markdown")

    async def build_message(self, p):
        ar5iv_link = f"https://ar5iv.labs.arxiv.org/html/{p.arxiv_id}"
        msg_lines = [
            f"**{p.title}**", f"Authors: {', '.join(p.authors)}", f"Published: **{p.published}**"
        ]
        # 如果 AI 生成了 tags
        if p.tags:
            msg_lines.append(f"Tags: {', '.join(p.tags)}")
        # 如果 AI 生成了 description
        if p.description:
            msg_lines.append(f"Summary: **{p.description}**")
        msg_lines.append(f"Comment: {p.comment}")
        msg_lines.append(f"Categories: {', '.join(p.categories)}")
        msg_lines.append(
            f"Continue: [Links]({p.link}) | [PDF]({p.pdf_link}) | [Ar5iv]({ar5iv_link})")
        msg = "\n".join(msg_lines)
        return msg

    async def fetch_now(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Fetch latest papers based on user's saved search queries"""

        try:
            user_config = await asyncio.to_thread(self.db.get_user_config, update.effective_chat.id)
            search_queries = user_config.search_queries if user_config and user_config.search_queries else None
        except Exception as e:
            logger.error(f"Failed to get user config for {update.effective_chat.id}: {e}")
            await update.message.reply_text("无法加载您的配置。请稍后重试。")
            return

        papers: List = []

        try:
            if search_queries:
                # 遍历每条检索式
                for sq in search_queries:
                    query_text = sq.get("query")
                    max_results = sq.get("max_results", 10)
                    if not query_text:
                        continue
                    # 调用同步的 arxiv_client.search 放到线程中执行
                    res = await asyncio.to_thread(self.arxiv_client.search, query_text, max_results)
                    if res:
                        if isinstance(res, list):
                            papers.extend(res)
                        else:
                            logger.warning(
                                f"arxiv_client.search returned non-list for query {query_text}: {type(res)}"
                            )
            else:
                # 用户没有设置检索式，默认瞎回复
                await update.message.reply_text("请设置您的检索式。")
        except Exception as e:
            logger.error(f"Error fetching papers: {e}")
            await update.message.reply_text("获取论文时发生错误。请稍后重试。")
            return

        for p in papers:
            chat_id = update.effective_chat.id
            # 查询数据库中已通知用户
            notified_users = await asyncio.to_thread(self.db.get_user_notify, p.arxiv_id)
            if chat_id in notified_users:
                continue  # 已通知则跳过
            msg = await self.build_message(p)
            await update.message.reply_text(msg, parse_mode="Markdown")
            # 更新 user_notify
            await asyncio.to_thread(self.db.update_user_notify, p.arxiv_id, chat_id)

    # ---------------------------
    # 后台抓取任务
    # ---------------------------
    async def _background_fetch_loop(self):
        while True:
            try:
                users = await asyncio.to_thread(self.db.get_all_users)
                if not users:
                    await asyncio.sleep(1)
                    continue

                for user in users:
                    chat_id = user.user_id
                    search_queries = user.search_queries or []
                    papers: List = []

                    try:
                        if search_queries:
                            for sq in search_queries:
                                query = sq.get("query")
                                max_results = sq.get("max_results", 10)
                                res = await asyncio.to_thread(self.arxiv_client.search, query,
                                                              max_results)
                                if isinstance(res, list):
                                    papers.extend(res)
                                else:
                                    logger.warning(
                                        f"arxiv_client.search returned non-list for {query}: {type(res)}"
                                    )
                        else:
                            res = await asyncio.to_thread(self.arxiv_client.fetch_today_new)
                            if isinstance(res, list):
                                papers.extend(res)
                            else:
                                logger.warning(
                                    f"arxiv_client.fetch_today_new returned non-list: {type(res)}")
                    except Exception as e:
                        logger.error(f"Error fetching papers for user {chat_id}: {e}")
                        continue

                    # 只发送未通知过的论文
                    for p in papers:
                        try:
                            notified_users = await asyncio.to_thread(self.db.get_user_notify,
                                                                     p.arxiv_id)
                            if chat_id in notified_users:
                                continue  # 已通知则跳过

                            msg = await self.build_message(p)
                            await self.app.bot.send_message(
                                chat_id=chat_id,
                                text=msg,
                                parse_mode="Markdown",
                            )
                            # 更新 user_notify
                            await asyncio.to_thread(self.db.update_user_notify, p.arxiv_id, chat_id)
                        except Exception as e:
                            logger.error(f"Failed to send message to {chat_id}: {e}")

            except Exception as e:
                logger.error(f"Error in background fetch loop: {e}")

            await asyncio.sleep(self.fetch_interval_hours * 3600)

    # ---------------------------
    # 启动机器人
    # ---------------------------
    async def _start_background(self, app):
        """在事件循环中创建后台抓取任务"""
        # create_task will schedule the background loop on the application's event loop
        app.create_task(self._background_fetch_loop())

    def run(self):
        """启动机器人（同步）

        PTB 的 Application.run_polling() 是同步入口点；`arxiv_main.py` 以同步方式调用 bot.run()
        因此这里使用同步包装，避免调用者需要管理事件循环。
        """
        self.app.run_polling()


class UserSession:
    """管理单个用户的会话状态和交互消息"""

    def __init__(self, user_id: int, timeout: int = 180):
        self.user_id = user_id
        self.state: str | None = None
        self.tmp_data: Dict[str, Any] = {}
        self._messages_to_revoke: List[Dict[str, int]] = []
        self.timeout = timeout
        self.last_active = time.time()
        self.manager = None  # 在创建时注入

    def touch(self):
        """刷新活动时间"""
        self.last_active = time.time()

    def add_revoke_message(self, message):
        """记录需要撤回的消息"""
        if not message:
            return
        try:
            self._messages_to_revoke.append({
                "chat_id": message.chat.id,
                "message_id": message.message_id
            })
        except AttributeError:
            if isinstance(message, dict):
                self._messages_to_revoke.append(message)

    async def revoke_messages(self, bot):
        """撤回交互消息"""
        for msg in list(self._messages_to_revoke):
            try:
                await bot.delete_message(chat_id=msg["chat_id"], message_id=msg["message_id"])
            except Exception as e:
                logger.debug(f"撤回消息失败: {e}")
        self._messages_to_revoke.clear()

    def reset(self):
        """重置内部状态"""
        self.state = None
        self.tmp_data.clear()

    async def end(self, bot):
        """由 Flow 主动结束"""
        await self.revoke_messages(bot)
        self.reset()
        if self.manager:
            self.manager.remove(self.user_id)

    async def on_expire(self, bot):
        """由 Manager 触发的超时清理"""
        await self.revoke_messages(bot)
        try:
            await bot.send_message(chat_id=self.user_id, text="⚠️ 操作超时，已自动取消。")
        except Exception as e:
            logger.debug(f"发送超时提示失败: {e}")
        self.reset()


class SessionManager:
    """统一管理所有会话生命周期"""

    def __init__(self, timeout: int = 180, check_interval: float = 2.0):
        self.timeout = timeout
        self.check_interval = check_interval
        self._sessions: Dict[int, UserSession] = {}
        self._watchdog_task = None
        self._running = False
        self.bot = None

    def attach_bot(self, bot):
        """启动前注入 bot 实例"""
        self.bot = bot

    def start(self):
        """启动超时检测任务"""
        if not self._running:
            self._running = True
            self._watchdog_task = asyncio.create_task(self._watchdog_loop())

    def stop(self):
        self._running = False
        if self._watchdog_task:
            self._watchdog_task.cancel()

    def get_or_create(self, user_id: int) -> UserSession:
        session = self._sessions.get(user_id)
        if session is None:
            session = UserSession(user_id, timeout=self.timeout)
            session.manager = self
            self._sessions[user_id] = session
            logger.info(f"创建新会话: {user_id}")
        session.touch()
        return session

    def remove(self, user_id: int):
        """被 session.end() 调用"""
        if user_id in self._sessions:
            del self._sessions[user_id]
            logger.info(f"移除会话: {user_id}")

    async def _watchdog_loop(self):
        logger.info("Session 超时检测启动")
        try:
            while self._running:
                now = time.time()
                expired = []
                for uid, s in list(self._sessions.items()):
                    if now - s.last_active > s.timeout:
                        expired.append(s)

                for s in expired:
                    logger.info(f"会话超时: {s.user_id}")
                    if self.bot:
                        asyncio.create_task(self._handle_expire(s))
                await asyncio.sleep(self.check_interval)
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.exception("SessionManager 出错")

    async def _handle_expire(self, session: UserSession):
        try:
            await session.on_expire(self.bot)
        finally:
            self.remove(session.user_id)


class SetKeywordsFlow:
    """处理 /set_keywords 命令的完整交互流程"""

    def __init__(self, db, config):
        self.db = db
        self.config = config

    async def start(self, update, context, session):
        user_id = update.effective_user.id
        user_cfg = await asyncio.to_thread(self.db.get_user_config, user_id)
        session.touch()

        if not user_cfg or not user_cfg.search_queries:
            msg = await update.message.reply_text("您还没有设置任何检索式，请输入要添加的检索式：")
            session.state = "ADDING_KEYWORD"
            session.add_revoke_message(msg)
            return

        # 用户已有检索式
        text = "📋 当前检索式：\n\n"
        for i, q in enumerate(user_cfg.search_queries, 1):
            text += f"{i}. {q['query']} (最大结果: {q['max_results']})\n"

        keyboard = [[
            InlineKeyboardButton("➕ 新增", callback_data="add_keyword"),
            InlineKeyboardButton("🗑 删除", callback_data="delete_keyword"),
            InlineKeyboardButton("❌ 取消", callback_data="cancel")
        ]]
        msg = await update.message.reply_text(text + "\n请选择操作：",
                                              reply_markup=InlineKeyboardMarkup(keyboard))
        session.state = "SETTING_KEYWORDS"
        session.add_revoke_message(msg)

    async def on_callback(self, update, context, session):
        query = update.callback_query
        await query.answer()
        user_id = query.from_user.id
        session.touch()

        if query.data == "cancel":
            await query.edit_message_text("操作已取消。")
            await session.end(context.bot)
            return

        if query.data == "add_keyword":
            msg = await query.edit_message_text(
                "请输入新的检索式：\n例如：cat:cs.CV AND (all:\"object detection\")")
            session.state = "ADDING_KEYWORD"
            session.add_revoke_message(msg)
            return

        if query.data == "delete_keyword":
            user_cfg = await asyncio.to_thread(self.db.get_user_config, user_id)
            if not user_cfg or not user_cfg.search_queries:
                await query.edit_message_text("您还没有设置检索式。")
                await session.end(context.bot)
                return

            text = "请输入要删除的编号：\n"
            for i, q in enumerate(user_cfg.search_queries, 1):
                text += f"{i}. {q['query']} (最大结果: {q['max_results']})\n"
            msg = await query.edit_message_text(text)
            session.state = "DELETING_KEYWORD"
            session.add_revoke_message(msg)

    async def on_message(self, update, context, session):
        user_id = update.effective_user.id
        text = update.message.text.strip()
        session.touch()

        # 添加检索式
        if session.state == "ADDING_KEYWORD":
            if not text:
                await update.message.reply_text("检索式不能为空，请重新输入：")
                return

            session.tmp_data["new_keyword"] = text
            msg = await update.message.reply_text(
                f"检索式: {text}\n请输入最大结果数 (1-{self.config['arxiv']['max_results']})：")
            session.state = "ADDING_MAX_RESULTS"
            session.add_revoke_message(msg)
            return

        # 设置最大结果
        if session.state == "ADDING_MAX_RESULTS":
            try:
                max_results = int(text)
                if not (1 <= max_results <= self.config["arxiv"]["max_results"]):
                    await update.message.reply_text(
                        f"请输入1-{self.config['arxiv']['max_results']}之间的数字。")
                    return

                kw = session.tmp_data["new_keyword"]
                user_cfg = await asyncio.to_thread(self.db.get_user_config, user_id)
                queries = user_cfg.search_queries if user_cfg and user_cfg.search_queries else []
                if any(q["query"] == kw for q in queries):
                    await update.message.reply_text("该检索式已存在，请重新输入。")
                    session.state = "ADDING_KEYWORD"
                    return

                queries.append({"query": kw, "max_results": max_results})
                await asyncio.to_thread(self.db.insert_or_update_user, user_id,
                                        {"search_queries": queries})
                await update.message.reply_text(f"✅ 添加成功：{kw}（最大结果 {max_results}）")
                await session.end(context.bot)
            except ValueError:
                await update.message.reply_text("请输入有效的数字。")
            return

        # 删除检索式
        if session.state == "DELETING_KEYWORD":
            try:
                idx = int(text) - 1
                user_cfg = await asyncio.to_thread(self.db.get_user_config, user_id)
                queries = user_cfg.search_queries if user_cfg and user_cfg.search_queries else []
                if 0 <= idx < len(queries):
                    deleted = queries.pop(idx)
                    await asyncio.to_thread(self.db.insert_or_update_user, user_id,
                                            {"search_queries": queries})
                    await update.message.reply_text(f"🗑 已删除：{deleted['query']}")
                    await session.end(context.bot)
                else:
                    await update.message.reply_text("编号无效。")
            except ValueError:
                await update.message.reply_text("请输入数字编号。")
