import asyncio
import logging
from datetime import datetime
from typing import List

import requests
import markdown

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)


class MatrixBot:
    """Matrix Bot - 定时抓取 Arxiv 并推送到指定房间"""

    def __init__(self, config, db, arxiv_client):
        self.config = config
        self.db = db
        self.arxiv_client = arxiv_client

        # Matrix 配置
        matrix_cfg = config["matrix"]
        self.homeserver = matrix_cfg["homeserver"]
        self.user = matrix_cfg["user"]
        self.device_id = matrix_cfg.get("device_id")
        self.password = matrix_cfg.get("password")
        self.access_token = matrix_cfg.get("access_token")
        self.room_id = matrix_cfg["room_id"]

        self.arxiv_queries = matrix_cfg.get("arxiv_queries", [])

        # 记录已推送过的论文 arxiv_id
        self.sent_ids = set()

    # ---------------- Matrix API ----------------

    def _send_request(self, method, path, params=None, json_data=None):
        url = f"{self.homeserver}{path}"
        headers = {}
        if self.access_token:
            params = params or {}
            params["access_token"] = self.access_token

        resp = requests.request(method, url, params=params, json=json_data, headers=headers)
        resp.raise_for_status()
        return resp.json()

    def send_message_old(self, message: str):
        """发送消息到房间"""
        path = f"/_matrix/client/r0/rooms/{self.room_id}/send/m.room.message"
        data = {"msgtype": "m.text", "body": message}
        try:
            self._send_request("POST", path, json_data=data)
            logger.info(f"Message sent to room {self.room_id}")
        except Exception as e:
            logger.error(f"Failed to send message: {e}")

    def send_message(self, message: str, formatted_message: str):
        """发送消息到房间"""
        path = f"/_matrix/client/r0/rooms/{self.room_id}/send/m.room.message"
        data = {
            "msgtype": "m.text",  # 普通文本消息
            "body": message,  # 未格式化的消息
            "format": "org.matrix.custom.html",  # 表示消息体是HTML格式
            "formatted_body": formatted_message,  # 这里是HTML格式的消息
        }
        try:
            self._send_request("POST", path, json_data=data)
            logger.info(f"Message sent to room {self.room_id}")
        except Exception as e:
            logger.error(f"Failed to send message: {e}")

    # ---------------- Paper Message ----------------

    async def build_message_old(self, paper):
        """构造消息文本"""
        ar5iv_link = f"https://ar5iv.labs.arxiv.org/html/{paper.arxiv_id}"
        msg_lines = [
            f"**{paper.title}**",
            f"Authors: {', '.join(paper.authors)}",
            f"Published: **{paper.published}**",
        ]
        if getattr(paper, "tags", None):
            msg_lines.append(f"Tags: {', '.join(paper.tags)}")
        if getattr(paper, "description", None):
            msg_lines.append(f"Summary: **{paper.description}**")
        msg_lines.append(f"Comment: {paper.comment}")
        msg_lines.append(f"Categories: {', '.join(paper.categories)}")
        msg_lines.append(
            f"Continue: [Links]({paper.link}) | [PDF]({paper.pdf_link}) | [Ar5iv]({ar5iv_link})")
        return "\n".join(msg_lines)

    async def build_message(self, paper):
        """构造消息文本"""
        ar5iv_link = f"https://ar5iv.labs.arxiv.org/html/{paper.arxiv_id}"
        msg_lines = [
            f"**{paper.title}**",
            f"Authors: {', '.join(paper.authors)}",
            f"Published: **{paper.published}**",
        ]
        if getattr(paper, "tags", None):
            msg_lines.append(f"Tags: {', '.join(paper.tags)}")
        if getattr(paper, "description", None):
            msg_lines.append(f"Summary: **{paper.description}**")
        msg_lines.append(f"Comment: {paper.comment}")
        msg_lines.append(f"Categories: {', '.join(paper.categories)}")
        msg_lines.append(
            f"Continue: [Links]({paper.link}) | [PDF]({paper.pdf_link}) | [Ar5iv]({ar5iv_link})")

        # 未格式化的普通文本
        plain_text = "\n".join(msg_lines)

        # 使用 markdown 转换为 HTML 格式
        html_message = markdown.markdown("\n".join(msg_lines))

        return plain_text, html_message

    # ---------------- Fetch & Push ----------------

    async def fetch_and_send_old(self):
        """抓取所有检索式并发送到 Matrix"""
        for query_cfg in self.arxiv_queries:
            query_text = query_cfg["query"]
            max_results = query_cfg.get("max_results", 5)

            # 调用同步搜索放线程
            papers = await asyncio.to_thread(self.arxiv_client.search, query_text, max_results)

            for paper in papers:
                if paper.arxiv_id in self.sent_ids:
                    continue
                msg = await self.build_message(paper)
                self.send_message(msg)
                self.sent_ids.add(paper.arxiv_id)
                # 可选: 更新数据库记录已推送
                await asyncio.to_thread(self.db.update_user_notify, paper.arxiv_id, self.room_id)

    async def fetch_and_send(self):
        """抓取所有检索式并发送到 Matrix"""
        for query_cfg in self.arxiv_queries:
            query_text = query_cfg["query"]
            max_results = query_cfg.get("max_results", 5)

            # 调用同步搜索放线程
            papers = await asyncio.to_thread(self.arxiv_client.search, query_text, max_results)

            for paper in papers:
                if paper.arxiv_id in self.sent_ids:
                    continue
                plain_msg, formatted_msg = await self.build_message(paper)
                self.send_message(plain_msg, formatted_msg)
                self.sent_ids.add(paper.arxiv_id)
                # 可选: 更新数据库记录已推送
                await asyncio.to_thread(self.db.update_user_notify, paper.arxiv_id, self.room_id)

    # ---------------- Background Loop ----------------

    async def start_loop(self, interval_minutes: int = 60):
        """定时抓取循环"""
        logger.info("Matrix bot started fetch loop")
        while True:
            try:
                logger.info(f"[{datetime.now()}] Fetching papers...")
                await self.fetch_and_send()
            except Exception as e:
                logger.error(f"Error in fetch loop: {e}")
            await asyncio.sleep(interval_minutes * 60)
