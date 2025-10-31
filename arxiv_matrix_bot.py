import asyncio
import logging
from datetime import datetime
from typing import List
import requests
import markdown
import hashlib

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)


def room_id_to_int(room_id: str, digits: int = 9) -> int:
    """
    将任意字符串 room_id 映射为指定长度的整数，用于数据库 integer 字段
    """
    h = hashlib.sha256(room_id.encode("utf-8")).hexdigest()
    num = int(h[:12], 16)
    return num % (10**digits)


class MatrixBot:
    """Matrix Bot - 定时抓取 Arxiv 并推送到指定房间"""

    def __init__(self, config, db, arxiv_client, interval_minutes: int = 60):
        self.config = config
        self.db = db
        self.arxiv_client = arxiv_client
        self.interval_minutes = interval_minutes

        # Matrix 配置
        matrix_cfg = config["matrix"]
        self.homeserver = matrix_cfg["homeserver"]
        self.user = matrix_cfg["user"]
        self.device_id = matrix_cfg.get("device_id")
        self.password = matrix_cfg.get("password")
        self.access_token = matrix_cfg.get("access_token")
        self.room_id = matrix_cfg["room_id"]
        self.room_id_db = room_id_to_int(self.room_id)  # 数据库用整数 ID
        self.arxiv_queries = matrix_cfg.get("arxiv_queries", [])

        # 将 room_id_db 当作“用户”加入或更新用户表
        try:
            user_cfg = self.db.get_user_config(self.room_id_db)
            self.db.insert_or_update_user(self.room_id_db, {
                "search_queries": self.arxiv_queries,
                "platform": "matrix"
            })
            if not user_cfg:
                logger.info(f"Room {self.room_id} (db_id={self.room_id_db}) added as a user in DB")
            else:
                logger.info(f"Room {self.room_id} (db_id={self.room_id_db}) updated in DB")
        except Exception as e:
            logger.error(f"Failed to add/update room {self.room_id} as user: {e}")

        # 启动后台抓取任务
        self._running = True
        # 注意: 初始化时不要直接 create_task，需在已有事件循环中启动
        self._background_task = None

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

    def send_message(self, message: str, formatted_message: str):
        """发送消息到房间（Matrix API 使用原始 room_id）"""
        path = f"/_matrix/client/r0/rooms/{self.room_id}/send/m.room.message"
        data = {
            "msgtype": "m.text",
            "body": message,
            "format": "org.matrix.custom.html",
            "formatted_body": formatted_message,
        }
        try:
            self._send_request("POST", path, json_data=data)
            logger.info(f"Message sent to room {self.room_id}")
        except Exception as e:
            logger.error(f"Failed to send message: {e}")

    # ---------------- Paper Message ----------------

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
        if getattr(paper, "translation", None):
            msg_lines.append(f"Translation: {paper.translation}")
        msg_lines.append(f"Comment: {paper.comment}")
        msg_lines.append(f"Categories: {', '.join(paper.categories)}")
        msg_lines.append(
            f"Continue: [Links]({paper.link}) | [PDF]({paper.pdf_link}) | [Ar5iv]({ar5iv_link})")

        plain_text = "\n".join(msg_lines)
        html_message = markdown.markdown(plain_text)
        return plain_text, html_message

    # ---------------- Fetch & Push ----------------

    async def fetch_and_send(self):
        """抓取所有检索式并发送到 Matrix"""
        for query_cfg in self.arxiv_queries:
            query_text = query_cfg["query"]
            max_results = query_cfg.get("max_results", 5)

            try:
                papers = await asyncio.to_thread(self.arxiv_client.search, query_text, max_results)
            except Exception as e:
                logger.error(f"Failed to fetch papers for query {query_text}: {e}")
                continue

            for paper in papers:
                try:
                    already_sended = await asyncio.to_thread(self.db.is_sended, paper.arxiv_id,
                                                             self.room_id_db)
                    if already_sended:
                        continue

                    plain_msg, formatted_msg = await self.build_message(paper)
                    self.send_message(plain_msg, formatted_msg)

                    await asyncio.to_thread(self.db.sended, paper.arxiv_id, self.room_id_db)
                except Exception as e:
                    logger.error(f"Failed to send paper {paper.arxiv_id}: {e}")

    # ---------------- Background Loop ----------------

    async def start_loop(self):
        """启动后台抓取任务（需在已有事件循环中调用）"""
        if self._background_task is None:
            self._background_task = asyncio.create_task(self._background_fetch_loop())

    async def _background_fetch_loop(self):
        """后台抓取循环"""
        logger.info("Matrix bot background fetch loop started")
        while self._running:
            try:
                logger.info(f"[{datetime.now()}] Fetching papers...")
                await self.fetch_and_send()
            except Exception as e:
                logger.error(f"Error in background fetch loop: {e}")
            await asyncio.sleep(self.interval_minutes * 60)

    # ---------------- Graceful Shutdown ----------------

    async def stop(self):
        """停止后台抓取任务"""
        logger.info("Stopping MatrixBot background task...")
        self._running = False
        if self._background_task:
            self._background_task.cancel()
            try:
                await self._background_task
            except asyncio.CancelledError:
                logger.info("Background task cancelled")
        logger.info("MatrixBot stopped successfully")
