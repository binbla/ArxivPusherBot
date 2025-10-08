# arxiv.py
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import List, Optional
from arxiv_llm import PaperAI
import arxiv  # pip install arxiv
from arxiv_database import DatabaseManager  # 兼容 PostgreSQL JSON 类型
import asyncio


@dataclass
class PaperEntry:
    """统一论文数据结构"""
    arxiv_id: str
    title: str
    authors: List[str]
    summary: str
    published: str
    updated: str
    categories: List[str]
    link: str
    tags: Optional[List[str]] = None
    description: Optional[str] = None


class ArxivClient:
    """Arxiv API 客户端，使用 arxiv 包 + 数据库写入 (PostgreSQL)"""

    def __init__(self, config: dict, db: DatabaseManager, llm: PaperAI):
        self.max_results = config.get("arxiv", {}).get("max_results", 100)
        self.default_categories = config.get("arxiv", {}).get("default_categories", ["cs.AI"])
        self.db = db
        self.llm = llm

        logging.basicConfig(level=logging.INFO)
        self.logger = logging.getLogger("ArxivClient")

    # ---------------------------
    # 核心功能
    # ---------------------------
    def search(self, query: str, max_results: int = None) -> List[PaperEntry]:
        """根据关键词搜索论文"""
        max_results = max_results or self.max_results
        self.logger.info(f"Searching arXiv for query: {query}")

        try:
            search = arxiv.Search(query=query,
                                  max_results=max_results,
                                  sort_by=arxiv.SortCriterion.SubmittedDate,
                                  sort_order=arxiv.SortOrder.Descending)
            results = list(search.results())
        except Exception as e:
            # arxiv package/network error
            self.logger.error(f"arXiv search failed for query '{query}': {e}")
            return []

        papers = []
        for e in results:
            try:
                papers.append(self._entry_to_paper(e))
            except Exception as ex:
                self.logger.warning(f"Failed to convert arXiv entry to PaperEntry: {ex}")

        # 没在数据库中且 LLM 可用时，生成 tag 和 summary 并存库
        for p in papers:
            if self.db.paper_exists(p.arxiv_id):
                continue
            if self.llm:
                try:
                    p.tags = asyncio.run(self.llm.generate_tags(p.title, p.summary))
                except Exception as e:
                    self.logger.error(f"LLM tag generation failed for {p.arxiv_id}: {e}")
                    p.tags = []
                try:
                    p.description = asyncio.run(self.llm.summarize_cn(p.title, p.summary))
                except Exception as e:
                    self.logger.error(f"LLM summary generation failed for {p.arxiv_id}: {e}")
                    p.description = ""
                self._save_to_db(p)
        return papers

    def fetch_recent(self, category: str, max_results: int = None) -> List[PaperEntry]:
        """按分类抓取最新论文"""
        max_results = max_results or self.max_results
        query = f"cat:{category}"
        return self.search(query, max_results)

    def fetch_today_new(self, categories: Optional[List[str]] = None) -> List[PaperEntry]:
        """抓取当天的新论文"""
        if categories is None:
            categories = self.default_categories

        today = datetime.utcnow().date()
        today_papers = []

        for cat in categories:
            self.logger.info(f"Fetching new papers in {cat}")
            try:
                papers = self.fetch_recent(cat)
            except Exception as e:
                self.logger.error(f"Failed to fetch recent papers for {cat}: {e}")
                papers = []

            for p in papers:
                if not p or not getattr(p, 'published', None):
                    continue
                try:
                    pub_date = datetime.strptime(p.published, "%Y-%m-%dT%H:%M:%SZ").date()
                    if pub_date == today:
                        today_papers.append(p)
                except Exception as e:
                    self.logger.warning(f"Failed to parse published date '{p.published}': {e}")

        return today_papers

    # ---------------------------
    # 解析函数
    # ---------------------------
    def _entry_to_paper(self, entry: arxiv.Result) -> PaperEntry:
        arxiv_id = entry.entry_id.split("/")[-1]
        authors = [a.name for a in entry.authors]
        categories = entry.categories or []
        link = entry.pdf_url or entry.entry_id

        return PaperEntry(
            arxiv_id=arxiv_id,
            title=entry.title.strip(),
            authors=authors,
            summary=entry.summary.strip(),
            published=entry.published.strftime("%Y-%m-%dT%H:%M:%SZ") if entry.published else "",
            updated=entry.updated.strftime("%Y-%m-%dT%H:%M:%SZ") if entry.updated else "",
            categories=categories,
            link=link,
            tags=[],
            description="")

    # ---------------------------
    # 数据库存储
    # ---------------------------
    def _save_to_db(self, paper: List[PaperEntry]):
        if not self.db:
            self.logger.warning("No database configured; skipping saving papers.")
            return

        inserted = 0
        try:
            if self.db.paper_exists(paper.arxiv_id):
                return
            else:
                paper_dict = {
                    "arxiv_id": paper.arxiv_id,
                    "title": paper.title,
                    "authors": paper.authors,  # 直接存 JSON
                    "summary": paper.summary,
                    "published": paper.published,
                    "updated": paper.updated,
                    "link": paper.link,
                    "category": paper.categories,  # 直接存 JSON
                    "tags": paper.tags or [],  # JSON 列表
                    "description": paper.description or ""
                }
                if self.db.insert_paper(paper_dict):
                    inserted += 1
        except Exception as e:
            self.logger.error(f"DB error while inserting paper {paper.arxiv_id}: {e}")

        self.logger.info(f"Inserted new papers into database.")
