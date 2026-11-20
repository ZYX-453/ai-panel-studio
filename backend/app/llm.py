"""模型客户端（OpenAI 兼容 /chat/completions）。

这一层只做三件事：发请求、超时、把各种失败归一成两种可判断的异常。
它不做 prompt 拼装，也不解析业务语义 —— 那些属于 caller 的职责，
混在一起会让"模型到底错在哪一步"变得不可判断。

Key 只从环境变量读，且任何异常信息里都不得出现 Key。
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass


class LLMError(RuntimeError):
    """模型调用失败。retryable 决定上层是重试还是降级。"""

    def __init__(self, message: str, *, retryable: bool) -> None:
        super().__init__(message)
        self.retryable = retryable


@dataclass(frozen=True, slots=True)
class Usage:
    prompt_tokens: int = 0
    completion_tokens: int = 0

    @property
    def total(self) -> int:
        return self.prompt_tokens + self.completion_tokens


@dataclass(frozen=True, slots=True)
class ChatResult:
    content: str
    usage: Usage
    latency_ms: int
    model: str


#: 默认走 DeepSeek 官方端点。换供应商只改环境变量，不改代码。
DEFAULT_BASE = "https://api.deepseek.com"
DEFAULT_MODEL = "deepseek-chat"

#: 讨论里的每句发言都短，800 足够；给大反而更容易让模型跑题写成小作文
DEFAULT_MAX_TOKENS = 800


class LLMClient:
    def __init__(
        self,
        *,
        key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
        timeout: float = 60.0,
        max_retries: int = 2,
    ) -> None:
        self._key = key if key is not None else os.environ.get("PANEL_LLM_KEY", "")
        self.base_url = (
            base_url or os.environ.get("PANEL_LLM_BASE", "") or DEFAULT_BASE
        ).rstrip("/")
        self.model = model or os.environ.get("PANEL_LLM_MODEL", "") or DEFAULT_MODEL
        self.timeout = timeout
        self.max_retries = max_retries

    @property
    def configured(self) -> bool:
        return bool(self._key)

    def describe(self) -> dict[str, object]:
        """给 /api/health 用。只报端点与模型名，绝不包含 Key。"""
        return {
            "endpoint": self.base_url,
            "model": self.model,
            "key_configured": self.configured,
        }

    def chat(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float = 0.9,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        json_mode: bool = False,
    ) -> ChatResult:
        """发一次对话请求，内部已含重试。失败抛 LLMError。"""
        if not self.configured:
            raise LLMError("未配置 PANEL_LLM_KEY", retryable=False)

        payload: dict[str, object] = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": False,
        }
        # 共识抽取需要严格 JSON。用 response_format 而不是"请在 prompt 里写 JSON"，
        # 因为后者模型仍会包一层 ```json 围栏。
        if json_mode:
            payload["response_format"] = {"type": "json_object"}

        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        last: LLMError | None = None

        for attempt in range(self.max_retries + 1):
            started = time.monotonic()
            try:
                raw = self._post(body)
            except LLMError as exc:
                last = exc
                if not exc.retryable or attempt >= self.max_retries:
                    raise
                # 指数退避，避免在供应商限流时把重试打成新的压力
                time.sleep(0.6 * (2**attempt))
                continue

            latency = int((time.monotonic() - started) * 1000)
            return self._parse(raw, latency)

        raise last or LLMError("未知失败", retryable=True)

    # ---------------------------------------------------------------- 传输层

    def _post(self, body: bytes) -> dict:
        req = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=body,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self._key}",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            # 4xx 里只有 429 值得重试；401 重试一万次也还是 401
            retryable = exc.code == 429 or exc.code >= 500
            raise LLMError(f"HTTP {exc.code}", retryable=retryable) from None
        except urllib.error.URLError as exc:
            raise LLMError(f"连接失败: {exc.reason}", retryable=True) from None
        except TimeoutError:
            raise LLMError("请求超时", retryable=True) from None
        except json.JSONDecodeError:
            raise LLMError("响应不是合法 JSON", retryable=True) from None

    def _parse(self, raw: dict, latency_ms: int) -> ChatResult:
        try:
            content = raw["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError):
            raise LLMError("响应缺少 choices[0].message.content", retryable=True) from None

        usage_raw = raw.get("usage") or {}
        usage = Usage(
            prompt_tokens=int(usage_raw.get("prompt_tokens") or 0),
            completion_tokens=int(usage_raw.get("completion_tokens") or 0),
        )
        return ChatResult(
            content=str(content or "").strip(),
            usage=usage,
            latency_ms=latency_ms,
            model=str(raw.get("model") or self.model),
        )


def strip_code_fence(text: str) -> str:
    """去掉 ```json ... ``` 围栏。

    即便要求了 json_mode，某些供应商仍会包围栏。
    在解析侧兜一次，比在 prompt 里反复强调"不要围栏"更可靠。
    """
    s = text.strip()
    if not s.startswith("```"):
        return s
    lines = s.splitlines()
    if lines and lines[0].startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip().startswith("```"):
        lines = lines[:-1]
    return "\n".join(lines).strip()
