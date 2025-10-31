import asyncio
import httpx
import re
from typing import List


class BaseLLMClient:
    """通用大语言模型客户端接口（HTTP / 本地 LLM）"""

    def __init__(self,
                 endpoint: str,
                 api_token: str = None,
                 model: str = "default-model",
                 timeout: int = 30,
                 max_retries: int = 3):
        self.endpoint = endpoint
        self.api_token = api_token
        self.model = model
        self.timeout = timeout
        self.max_retries = max_retries

    async def generate(self, messages: list) -> str:
        """异步调用 LLM 生成文本，带重试"""
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_token}" if self.api_token else ""
        }
        payload = {"model": self.model, "messages": messages}

        for attempt in range(self.max_retries):
            try:
                async with httpx.AsyncClient(timeout=self.timeout) as client:
                    response = await client.post(self.endpoint, json=payload, headers=headers)
                    response.raise_for_status()
                    data = response.json()
                    return data['choices'][0]['message']['content'].strip()
            except (httpx.RequestError, httpx.HTTPStatusError) as e:
                if attempt < self.max_retries - 1:
                    await asyncio.sleep(2**attempt)
                else:
                    raise RuntimeError(f"LLM request failed: {e}")


class PaperAI:
    """为论文提供 AI tag、中文总结和摘要翻译模块"""

    def __init__(self,
                 llm_client: BaseLLMClient,
                 max_tags_prompt: int = 5,
                 max_concurrency: int = 5):
        self.llm_client = llm_client
        self.max_tags_prompt = max_tags_prompt
        self.semaphore = asyncio.Semaphore(max_concurrency)

    # -----------------
    # 单篇论文处理
    # -----------------
    async def enrich_paper(self, paper) -> None:
        """为单篇论文生成 tags、中文 summary 和摘要翻译"""
        async with self.semaphore:
            tag_task = asyncio.create_task(self.generate_tags(paper.title, paper.summary))
            summary_task = asyncio.create_task(self.summarize_cn(paper.title, paper.summary))
            translate_task = asyncio.create_task(self.translate_abstract(paper.summary))

            try:
                paper.tags, paper.description, paper.translation = await asyncio.gather(
                    tag_task, summary_task, translate_task)
            except Exception:
                paper.tags = []
                paper.description = ""
                paper.translation = ""

    # -----------------
    # 自动 tag 功能
    # -----------------
    async def generate_tags(self, title: str, abstract: str) -> List[str]:
        messages = self._build_tag_messages(title, abstract)
        raw_output = await self.llm_client.generate(messages)
        return self._parse_tags(raw_output)

    def _build_tag_messages(self, title: str, abstract: str) -> list:
        return [{
            "role": "system",
            "content": "你是一个学术论文分析助手。"
        }, {
            "role":
            "user",
            "content":
            f"请根据以下论文标题和摘要生成不超过{self.max_tags_prompt}个标签，简短且用中文，以逗号分隔输出。\n标题：{title}\n摘要：{abstract}"
        }]

    def _parse_tags(self, output: str) -> List[str]:
        tags = re.split(r"[,;\n]+", output)
        return [t.strip() for t in tags if t.strip()][:self.max_tags_prompt]

    # -----------------
    # 中文总结功能
    # -----------------
    async def summarize_cn(self, title: str, abstract: str) -> str:
        messages = self._build_summary_messages(title, abstract)
        return (await self.llm_client.generate(messages)).strip()

    def _build_summary_messages(self, title: str, abstract: str) -> list:
        # 修改提示词：总结不超过三句话
        return [{
            "role": "system",
            "content": "你是一个学术论文分析助手。"
        }, {
            "role": "user",
            "content": f"请将以下论文标题和摘要总结为中文，不超过三句话，保持学术风格，纯文本，仅输出总结内容。\n标题：{title}\n摘要：{abstract}"
        }]

    # -----------------
    # 摘要翻译功能
    # -----------------
    async def translate_abstract(self, abstract: str) -> str:
        messages = self._build_translation_messages(abstract)
        return (await self.llm_client.generate(messages)).strip()

    def _build_translation_messages(self, abstract: str) -> list:
        return [{
            "role": "system",
            "content": "你是一个学术论文翻译助手。"
        }, {
            "role": "user",
            "content": f"请将以下英文摘要翻译成中文，保持学术风格，纯文本输出，仅翻译内容，不要增加评论。\n摘要：{abstract}"
        }]

    # -----------------
    # 批量处理功能
    # -----------------
    async def enrich_papers_batch(self, papers: list) -> None:
        """并发处理多篇论文，内部使用 semaphore 控制并发数"""
        tasks = [asyncio.create_task(self.enrich_paper(p)) for p in papers]
        await asyncio.gather(*tasks)
