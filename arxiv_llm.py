import asyncio
import httpx


class BaseLLMClient:
    """通用大语言模型客户端接口（HTTP / 本地 LLM）"""

    def __init__(self,
                 endpoint: str,
                 api_token: str = None,
                 model: str = "ep-sdfadfasdfasdf-asdfr",
                 timeout: int = 30):
        self.endpoint = endpoint
        self.api_token = api_token
        self.model = model
        self.timeout = timeout

    async def generate(self, messages: list) -> str:
        """异步调用 LLM 生成文本"""
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_token}",
        }

        payload = {"model": self.model, "messages": messages}

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            try:
                # 发送 POST 请求到 LLM 服务
                response = await client.post(self.endpoint, json=payload, headers=headers)
                response.raise_for_status()  # 如果请求失败，会抛出异常
                data = response.json()
                # 假设返回的数据包含 'choices'，其中有 'message' 字段
                return data['choices'][0]['message']['content'].strip()
            except httpx.HTTPStatusError as e:
                return f"HTTP error occurred: {e.response.status_code}"
            except httpx.RequestError as e:
                return f"Request error occurred: {str(e)}"


class PaperAI:
    """
    为论文提供 AI tag 和中文总结的模块
    """

    def __init__(self, llm_client: BaseLLMClient, max_tags_prompt: int = 5):
        self.llm_client = llm_client
        self.max_tags_prompt = max_tags_prompt

    # -----------------
    # 自动 tag 功能
    # -----------------
    async def generate_tags(self, title: str, abstract: str) -> list[str]:
        max_tags = self.max_tags_prompt
        messages = self._build_tag_messages(title, abstract, max_tags)
        raw_output = await self.llm_client.generate(messages)
        tags = self._parse_tags(raw_output, max_tags)
        return tags

    def _build_tag_messages(self, title: str, abstract: str, max_tags: int) -> list:
        # 构建适合 chat 模型的消息列表
        return [{
            "role": "system",
            "content": "你是一个学术论文分析助手。"
        }, {
            "role":
            "user",
            "content":
            f"请根据以下论文标题和摘要生成不超过{max_tags}个标签，简短且用中文，"
            f"以逗号分隔输出。标题：{title}\n摘要：{abstract}"
        }]

    def _parse_tags(self, output: str, max_tags: int) -> list[str]:
        tags = [t.strip() for t in output.replace("\n", ",").split(",") if t.strip()]
        return tags[:max_tags]

    # -----------------
    # 中文总结功能
    # -----------------
    async def summarize_cn(self, title: str, abstract: str) -> str:
        messages = self._build_summary_messages(title, abstract)
        summary = await self.llm_client.generate(messages)
        return summary.strip()

    def _build_summary_messages(self, title: str, abstract: str) -> list:
        # 构建适合 chat 模型的消息列表
        return [{
            "role": "system",
            "content": "你是一个学术论文分析助手。"
        }, {
            "role": "user",
            "content": f"请将以下论文标题和摘要总结为中文，3-5句话，保持学术风格, 纯文本，仅输出总结内容。\n标题：{title}\n摘要：{abstract}"
        }]
