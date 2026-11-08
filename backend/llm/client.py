"""OpenAI 兼容 LLM 客户端。

设计要点：
- JSON 结构化输出（response_format=json_object），换模型在设置页修改即可
- 超时 + 单次重试
- 任何失败抛 LLMError，由上层决定 fail-closed 回退策略
- 进程内语义缓存（prompt 哈希 → 响应，TTL 可配）：同 prompt 重复调用零成本；
  self-consistency 采样等需要多样性的调用可显式关闭（use_cache=False）
"""
from __future__ import annotations

import hashlib
import json
import logging
import time

from ..config import LLMSettings

logger = logging.getLogger(__name__)


class LLMError(Exception):
    pass


class LLMClient:
    def __init__(self, config: LLMSettings):
        self.config = config
        if not config.model:
            raise LLMError("未配置模型 ID，请在「设置 → LLM 配置」中填写后重试")
        if not config.api_key_resolved:
            raise LLMError(f"未配置 API Key（环境变量 {config.api_key_env} 或设置页 llm.api_key），LLM 不可用")
        from openai import OpenAI
        self.client = OpenAI(base_url=config.base_url, api_key=config.api_key_resolved,
                             timeout=config.timeout_seconds)
        self._cache: dict[str, tuple[float, str]] = {}
        self._cache_ttl = config.cache_ttl_minutes * 60
        self.cache_hits = 0
        self.cache_misses = 0

    def _cache_get(self, key: str) -> str | None:
        if self._cache_ttl <= 0:
            return None
        hit = self._cache.get(key)
        if hit and time.time() - hit[0] < self._cache_ttl:
            self.cache_hits += 1
            return hit[1]
        if hit:
            self._cache.pop(key, None)
        self.cache_misses += 1
        return None

    def _cache_put(self, key: str, content: str) -> None:
        if self._cache_ttl > 0:
            if len(self._cache) > 256:  # 防膨胀：淘汰最旧
                oldest = min(self._cache, key=lambda k: self._cache[k][0])
                self._cache.pop(oldest, None)
            self._cache[key] = (time.time(), content)

    def chat_json(self, system_prompt: str, user_prompt: str,
                  temperature: float = 0.3, use_cache: bool = True) -> dict:
        """请求 LLM 并解析 JSON 输出，失败时抛 LLMError。"""
        cache_key = hashlib.sha256(
            f"{self.config.model}|{temperature}|{system_prompt}|{user_prompt}".encode()).hexdigest()
        if use_cache:
            cached = self._cache_get(cache_key)
            if cached is not None:
                logger.info("LLM 缓存命中（%d/%d）", self.cache_hits, self.cache_hits + self.cache_misses)
                return json.loads(cached)

        last_err: Exception | None = None
        for attempt in (1, 2):
            try:
                resp = self.client.chat.completions.create(
                    model=self.config.model,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                    response_format={"type": "json_object"},
                    temperature=temperature,
                )
                content = resp.choices[0].message.content or ""
                self._cache_put(cache_key, content)
                return json.loads(content)
            except Exception as e:  # noqa: BLE001
                last_err = e
                logger.warning("LLM 调用失败（第 %d 次）: %s", attempt, e)
        raise LLMError(str(last_err))

    def chat_vision(self, prompt: str, mime: str, b64_image: str) -> str:
        """多模态视觉调用（持仓截图 OCR 导入），返回模型原始文本输出。"""
        resp = self.client.chat.completions.create(
            model=self.config.vision_model,
            messages=[{
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64_image}"}},
                ],
            }],
            temperature=0.0,
        )
        return resp.choices[0].message.content or ""
