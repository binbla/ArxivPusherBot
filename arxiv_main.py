# main.py
from time import sleep
import yaml
import os
import logging
import asyncio
import multiprocessing
from arxiv_database import DatabaseManager
from arxiv_client import ArxivClient
from arxiv_tgbot import TgBot
from arxiv_llm import BaseLLMClient, PaperAI
from arxiv_matrix_bot import MatrixBot

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)


def load_config(config_file: str = "config.yaml") -> dict:
    if not os.path.exists(config_file):
        raise FileNotFoundError(f"Config file not found: {config_file}")
    with open(config_file, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def setup_network_proxy(config: dict):
    network_cfg = config.get("network", {})
    if network_cfg.get("use_proxy", False):
        os.environ["HTTP_PROXY"] = network_cfg.get("http_proxy", "")
        os.environ["HTTPS_PROXY"] = network_cfg.get("https_proxy", "")
        if network_cfg.get("socks5_proxy"):
            os.environ["ALL_PROXY"] = network_cfg["socks5_proxy"]
        logger.info("Proxy enabled:")
        logger.info(f"HTTP_PROXY={os.environ.get('HTTP_PROXY')}")
        logger.info(f"HTTPS_PROXY={os.environ.get('HTTPS_PROXY')}")
        logger.info(f"ALL_PROXY={os.environ.get('ALL_PROXY')}")


def init_arxiv_client(config: dict):
    """每个进程单独初始化 ArxivClient"""
    llm_client = BaseLLMClient(**config["llm"])
    arxiv_llm = PaperAI(llm_client, **config["llm_generation"])
    arxiv_client = ArxivClient(config, db=None, llm=arxiv_llm)
    return arxiv_client


def run_telegram_bot(config):
    """Telegram Bot 进程"""
    db = DatabaseManager(config["database"])  # PostgreSQL 单独连接
    arxiv_client = init_arxiv_client(config)
    arxiv_client.db = db

    bot = TgBot(config, db, arxiv_client)
    logger.info("Telegram bot initialized. Starting polling...")
    bot.run()  # 同步阻塞


def run_matrix_bot(config):
    """Matrix Bot 进程"""
    sleep(2)  # 避免与 Telegram 进程同时初始化数据库连接引起冲突
    db = DatabaseManager(config["database"])  # PostgreSQL 单独连接
    arxiv_client = init_arxiv_client(config)
    arxiv_client.db = db

    matrix_bot = MatrixBot(config, db, arxiv_client)  # 初始化时已启动后台抓取
    logger.info("Matrix bot initialized. Background fetch loop started.")
    try:
        # 主进程保持存活
        asyncio.run(_matrix_main_loop())
    except KeyboardInterrupt:
        logger.info("Matrix bot stopping...")
        asyncio.run(matrix_bot.stop())


async def _matrix_main_loop():
    """让主进程保持存活，实际抓取在 MatrixBot 内部异步循环"""
    while True:
        await asyncio.sleep(3600)


def main():
    config = load_config("config.yaml")
    logger.info("Configuration loaded.")

    setup_network_proxy(config)

    telegram_process = multiprocessing.Process(target=run_telegram_bot, args=(config, ))
    # matrix_process = multiprocessing.Process(target=run_matrix_bot, args=(config, ))

    telegram_process.start()
    # matrix_process.start()

    telegram_process.join()
    # matrix_process.join()


if __name__ == "__main__":
    main()
