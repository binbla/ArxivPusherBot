# main.py
import yaml
import os
import logging
import asyncio
import multiprocessing

from arxiv_database import DatabaseManager
from arxiv_client import ArxivClient
from arxiv_bot import ArxivBot
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


def run_telegram_bot(config, db, arxiv_client):
    """启动 Telegram Bot"""
    bot = ArxivBot(config, db, arxiv_client)
    bot.run()  # 直接同步运行 Telegram bot


def run_matrix_bot(config, db, arxiv_client):
    """启动 Matrix Bot"""
    matrix_bot = MatrixBot(config, db, arxiv_client)
    matrix_bot.start_loop(interval_minutes=60)  # 每小时抓取一次，非阻塞


def main():
    # 1. 加载配置
    config = load_config("config.yaml")
    logger.info("Configuration loaded.")

    # 2. 设置网络代理（可选）
    setup_network_proxy(config)

    # 3. 初始化数据库
    db = DatabaseManager(config["database"])
    logger.info("Database initialized.")

    # 4. 初始化 LLM 客户端
    llm_client = BaseLLMClient(**config["llm"])
    arxiv_llm = PaperAI(llm_client, **config["llm_generation"])
    logger.info("LLM client initialized.")

    # 5. 初始化 Arxiv 客户端
    arxiv_client = ArxivClient(config, db, arxiv_llm)
    logger.info("Arxiv client initialized.")

    # 6. 初始化 Telegram Bot
    bot = ArxivBot(config, db, arxiv_client)
    logger.info("Telegram bot initialized. Starting...")
    matrix_bot = MatrixBot(config, db, arxiv_client)
    logger.info("Matrix bot initialized. Starting...")

    # 6. 使用多进程启动 Telegram 和 Matrix 机器人
    telegram_process = multiprocessing.Process(target=run_telegram_bot,
                                               args=(config, db, arxiv_client))
    matrix_process = multiprocessing.Process(target=run_matrix_bot, args=(config, db, arxiv_client))

    telegram_process.start()
    matrix_process.start()

    # 等待进程结束
    telegram_process.join()
    matrix_process.join()

    # bot.run()


if __name__ == "__main__":
    main()
