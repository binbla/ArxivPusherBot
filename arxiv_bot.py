# arxiv_bot.py
import asyncio
import logging
from typing import List

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes, AIORateLimiter, CallbackQueryHandler, ConversationHandler, MessageHandler, filters

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

# 定义对话状态
SETTING_KEYWORDS, ADDING_KEYWORD, ADDING_MAX_RESULTS, DELETING_KEYWORD = range(4)

import re


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
    """Telegram Arxiv Bot，兼容 PTB 22+"""

    def __init__(self, config: dict, db, arxiv_client):
        self.config = config
        self.db = db
        self.arxiv_client = arxiv_client
        self.token = config["telegram"]["token"]
        self.fetch_interval_hours = config["arxiv"].get("fetch_interval_hours", 6)

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
        self.app.add_handler(self.get_conversation_handler())

    # ---------------------------
    # 命令处理函数
    # ---------------------------
    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        welcome_message = f"您好！我是您的 Arxiv 机器人。\n\n本机器人会定期为您推送最新的 **Arxiv** 论文。\n您只需要设定检索式，便可以开始接收推送。当前管理员设定的抓取间隔为 {self.fetch_interval_hours} 小时。\n\n我将通过API获取检索论文并使用AI为您生成标签和摘要。 \n\n*请注意，检索式请尽量使用all字段进行组合查询，title字段可能获取不到预期的结果。(跟网页查询存在出入)*\n我将按照发布时间降序推送。但都是最新的论文。请不用担心时间顺序。\n以下是检索式例子：\n\n`cat:cs.CV AND (all:\"object detection\")`\n"
        await update.message.reply_text(welcome_message, parse_mode="Markdown")

    async def show(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """处理 /show 命令，显示当前用户的检索式"""
        user_id = update.effective_user.id

        # 获取用户配置
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
        await update.message.reply_text("".join(message_text))

    async def set_keywords(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """处理 /set_keywords 命令"""
        user_id = update.effective_user.id

        # 获取用户配置
        user_config = await asyncio.to_thread(self.db.get_user_config, user_id)

        if not user_config or not user_config.search_queries:
            # 没有现有的检索式，直接进入设置流程
            await update.message.reply_text("您还没有设置任何检索式。请输入您要添加的检索式：")
            # 保存状态到context
            context.user_data['setting_keywords'] = True
            return ADDING_KEYWORD
        else:
            # 显示现有的检索式并提供选项
            existing_queries = user_config.search_queries
            message_text = "📋 您当前的检索式：\n\n"

            for i, query_obj in enumerate(existing_queries, 1):
                message_text += f"{i}. {query_obj['query']} (最大结果: {query_obj['max_results']})\n"

            # 创建按钮
            keyboard = [[
                InlineKeyboardButton("➕ 新增检索式", callback_data="add_keyword"),
                InlineKeyboardButton("🗑️ 删除检索式", callback_data="delete_keyword")
            ]]
            reply_markup = InlineKeyboardMarkup(keyboard)

            await update.message.reply_text(message_text + "\n请选择操作：", reply_markup=reply_markup)
            return SETTING_KEYWORDS

    async def handle_setting_choice(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """处理用户选择新增或删除"""
        query = update.callback_query
        await query.answer()

        if query.data == "add_keyword":
            prompt = "你将要输入的是检索式，[帮助](https://zhuanlan.zhihu.com/p/679538991)\n这是一个例子：\n**cat:cs.CV AND (all:\"object detection\")**\n\n请输入您要添加的检索式："
            await query.edit_message_text(prompt, parse_mode="Markdown")
            context.user_data['setting_keywords'] = True
            return ADDING_KEYWORD
        elif query.data == "delete_keyword":
            user_id = query.from_user.id
            user_config = await asyncio.to_thread(self.db.get_user_config, user_id)

            if not user_config or not user_config.search_queries:
                await query.edit_message_text("您还没有设置任何检索式。")
                return ConversationHandler.END

            existing_queries = user_config.search_queries

            message_text = "请回复要删除的检索式编号：\n\n"
            for i, query_obj in enumerate(existing_queries, 1):
                message_text += f"{i}. {query_obj['query']} (最大结果: {query_obj['max_results']})\n"

            await query.edit_message_text(message_text)
            return DELETING_KEYWORD

    async def add_keyword(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """处理添加检索式 - 第一步：接收检索式文本"""
        keyword_text = update.message.text.strip()

        if not keyword_text:
            await update.message.reply_text("检索式不能为空，请重新输入：")
            return ADDING_KEYWORD

        # 保存检索式到context
        context.user_data['new_keyword'] = keyword_text

        await update.message.reply_text(f"检索式: {keyword_text}\n"
                                        f"每次检索的结果消息上限 (1-{self.config['arxiv']['max_results']})：")
        return ADDING_MAX_RESULTS

    async def add_max_results(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """处理添加检索式 - 第二步：接收最大结果数量"""
        user_id = update.effective_user.id

        try:
            max_results = int(update.message.text.strip())

            if max_results < 1 or max_results > self.config['arxiv']['max_results']:
                await update.message.reply_text(f"请输入1-{self.config['arxiv']['max_results']}之间的数字：")
                return ADDING_MAX_RESULTS

            # 获取保存的检索式
            keyword_text = context.user_data.get('new_keyword')

            if not keyword_text:
                await update.message.reply_text("发生错误，请重新开始设置流程。")
                return ConversationHandler.END

            # 获取现有的检索式
            user_config = await asyncio.to_thread(self.db.get_user_config, user_id)
            existing_queries = user_config.search_queries if user_config and user_config.search_queries else []

            # 检查是否已存在相同的检索式
            for query_obj in existing_queries:
                if query_obj['query'] == keyword_text:
                    await update.message.reply_text(f"检索式 '{keyword_text}' 已存在！请重新输入不同的检索式：")
                    # 清除保存的数据
                    context.user_data.pop('new_keyword', None)
                    return ADDING_KEYWORD

            # 创建新的检索式对象
            new_query = {"query": keyword_text, "max_results": max_results}

            # 添加到现有列表
            existing_queries.append(new_query)

            # 更新数据库
            await asyncio.to_thread(self.db.insert_or_update_user, user_id,
                                    {"search_queries": existing_queries})

            # 清除临时数据
            context.user_data.pop('new_keyword', None)

            await update.message.reply_text(f"✅ 检索式添加成功！\n"
                                            f"📝 检索式: {keyword_text}\n"
                                            f"📊 最大结果: {max_results}\n"
                                            f"📋 当前共有 {len(existing_queries)} 个检索式。")

        except ValueError:
            await update.message.reply_text("请输入有效的数字：")
            return ADDING_MAX_RESULTS

        return ConversationHandler.END

    async def delete_keyword(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """处理删除检索式"""
        user_id = update.effective_user.id
        try:
            # 尝试解析用户输入的数字
            delete_index = int(update.message.text.strip()) - 1

            # 获取现有的检索式
            user_config = await asyncio.to_thread(self.db.get_user_config, user_id)

            if not user_config or not user_config.search_queries:
                await update.message.reply_text("您还没有设置任何检索式。")
                return ConversationHandler.END

            existing_queries = user_config.search_queries

            # 检查索引是否有效
            if 0 <= delete_index < len(existing_queries):
                deleted_query = existing_queries.pop(delete_index)

                # 更新数据库
                await asyncio.to_thread(self.db.insert_or_update_user, user_id,
                                        {"search_queries": existing_queries})

                await update.message.reply_text(f"🗑️ 已删除检索式: {deleted_query['query']}\n"
                                                f"📋 剩余 {len(existing_queries)} 个检索式。")
            else:
                await update.message.reply_text("❌ 编号无效，请重新输入有效的编号：")
                return DELETING_KEYWORD

        except ValueError:
            await update.message.reply_text("❌ 请输入有效的数字编号：")
            return DELETING_KEYWORD

        return ConversationHandler.END

    async def cancel(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """取消操作"""
        # 清除临时数据
        context.user_data.pop('new_keyword', None)
        context.user_data.pop('setting_keywords', None)

        await update.message.reply_text("❌ 操作已取消。")
        return ConversationHandler.END

    def get_conversation_handler(self):
        """获取对话处理器"""
        return ConversationHandler(
            entry_points=[CommandHandler("set_keywords", self.set_keywords)],
            states={
                SETTING_KEYWORDS: [CallbackQueryHandler(self.handle_setting_choice)],
                ADDING_KEYWORD: [MessageHandler(filters.TEXT & ~filters.COMMAND, self.add_keyword)],
                ADDING_MAX_RESULTS:
                [MessageHandler(filters.TEXT & ~filters.COMMAND, self.add_max_results)],
                DELETING_KEYWORD:
                [MessageHandler(filters.TEXT & ~filters.COMMAND, self.delete_keyword)],
            },
            fallbacks=[CommandHandler("cancel", self.cancel)],
        )

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
