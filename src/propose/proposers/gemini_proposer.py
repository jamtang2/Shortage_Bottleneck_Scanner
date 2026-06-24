"""Gemini(Google) 제안자."""
from __future__ import annotations

import os
from typing import Optional

from .base import BaseProposer


class GeminiProposer(BaseProposer):
    name = "gemini"

    def __init__(self, *, model: str, api_key: Optional[str] = None, max_stocks: int = 8,
                 max_tokens: int = 2000):
        super().__init__(model=model, max_stocks=max_stocks)
        self.api_key = api_key or os.getenv("GOOGLE_API_KEY")
        self.max_tokens = max_tokens

    def available(self) -> bool:
        return bool(self.api_key)

    def _complete(self, system: str, prompt: str) -> str:
        from google import genai
        from google.genai import types

        client = genai.Client(api_key=self.api_key)
        cfg_kwargs = dict(
            system_instruction=system,
            response_mime_type="application/json",
            max_output_tokens=self.max_tokens,
        )
        # flash 계열 thinking 모델은 추론 토큰이 출력 예산을 잠식해 본문이 비는 일이
        # 잦다. 구조화 제안엔 추론이 불필요하므로 flash에 한해 thinking을 끈다.
        # (pro 모델은 thinking 필수 — budget 0이면 400. 끄지 않고 둔다.)
        if "flash" in self.model.lower():
            try:
                cfg_kwargs["thinking_config"] = types.ThinkingConfig(thinking_budget=0)
            except Exception:  # noqa: BLE001 — 구버전/미지원이면 생략
                pass

        resp = client.models.generate_content(
            model=self.model,
            contents=prompt,
            config=types.GenerateContentConfig(**cfg_kwargs),
        )
        return resp.text or ""
