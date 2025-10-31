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
    pdf_link: str
    comment: str
    tags: Optional[List[str]] = None
    description: Optional[str] = None
    translation: Optional[str] = None  # 中文摘要翻译


class ArxivClient:
    """Arxiv API 客户端，使用 arxiv 包 + 数据库写入 (PostgreSQL)"""

    def __init__(self, config: dict, db: DatabaseManager, llm: PaperAI):
        self.max_results = config.get("arxiv", {}).get("max_results", 20)
        self.client = arxiv.Client()
        self.db = db
        self.llm = llm

        logging.basicConfig(level=logging.INFO)
        self.logger = logging.getLogger("ArxivClient")

    # ---------------------------
    # 核心功能
    # ---------------------------
    async def search(self, query: str, max_results: int = None) -> List[PaperEntry]:
        """根据关键词搜索论文"""
        max_results = max_results or self.max_results
        self.logger.info(f"Searching arXiv for query: {query}")

        try:
            search = arxiv.Search(query=query,
                                  max_results=max_results,
                                  sort_by=arxiv.SortCriterion.SubmittedDate,
                                  sort_order=arxiv.SortOrder.Descending)
            results = self.client.results(search)
        except Exception as e:
            self.logger.error(f"arXiv search failed for query '{query}': {e}")
            return []

        papers = []
        new_papers = []
        for e in results:
            # 分流处理数据库已有和新论文
            try:
                paper = self._entry_to_paper(e)
                # 检查数据库是否已有
                if self.db.paper_exists(paper.arxiv_id):
                    # 从数据库读取 tags, description, translation
                    db_paper = self.db.get_paper_data(paper.arxiv_id)
                    paper.tags = db_paper["tags"]
                    paper.description = db_paper["description"]
                    paper.translation = db_paper["translation"]
                else:
                    new_papers.append(paper)
                papers.append(paper)  # 无论新旧，都加入返回列表
            except Exception as ex:
                self.logger.warning(f"Failed to convert arXiv entry to PaperEntry: {ex}")

        # 异步生成 tags, description, translation，仅对新论文
        if self.llm and new_papers:
            try:
                await self.llm.enrich_papers_batch(new_papers)
            except Exception as e:
                self.logger.error(f"LLM enrichment failed: {e}")

        # 保存新论文到数据库
        for p in new_papers:
            self._save_to_db(p)

        return papers

    async def fetch_recent(self, category: str, max_results: int = None) -> List[PaperEntry]:
        """按分类抓取最新论文"""
        max_results = max_results or self.max_results
        query = f"cat:{category}"
        return await self.search(query, max_results)

    async def fetch_today_new(self, categories: Optional[List[str]] = None) -> List[PaperEntry]:
        """抓取当天的新论文"""
        if categories is None:
            categories = getattr(self, 'default_categories', [])

        today = datetime.utcnow().date()
        today_papers = []

        for cat in categories:
            self.logger.info(f"Fetching new papers in {cat}")
            try:
                papers = await self.fetch_recent(cat)
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

        link = entry.entry_id
        pdf_link = entry.pdf_url

        return PaperEntry(
            arxiv_id=arxiv_id,
            title=entry.title.strip(),
            authors=authors,
            summary=entry.summary.strip(),
            published=entry.published.strftime("%Y-%m-%dT%H:%M:%SZ") if entry.published else "",
            updated=entry.updated.strftime("%Y-%m-%dT%H:%M:%SZ") if entry.updated else "",
            categories=categories,
            link=link,
            pdf_link=pdf_link or "",
            comment=entry.comment or "",
            tags=[],
            description="",
            translation="")

    # ---------------------------
    # 数据库存储
    # ---------------------------
    def _save_to_db(self, paper: PaperEntry):
        if not self.db:
            self.logger.warning("No database configured; skipping saving papers.")
            return

        try:
            if self.db.paper_exists(paper.arxiv_id):
                return
            paper_dict = {
                "arxiv_id": paper.arxiv_id,
                "title": paper.title,
                "authors": paper.authors,
                "summary": paper.summary,
                "published": paper.published,
                "updated": paper.updated,
                "category": paper.categories,
                "link": paper.link,
                "pdf_link": paper.pdf_link,
                "comment": paper.comment or "",
                "tags": paper.tags or [],
                "description": paper.description or "",
                "translation": paper.translation or ""
            }
            if self.db.insert_paper(paper_dict):
                self.logger.info(f"Inserted new papers into database: {paper.arxiv_id}")
        except Exception as e:
            self.logger.error(f"DB error while inserting paper {paper.arxiv_id}: {e}")
