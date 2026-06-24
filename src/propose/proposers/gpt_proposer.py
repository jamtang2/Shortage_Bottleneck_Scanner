"""GPT(OpenAI) 제안자."""
from __future__ import annotations

import os
from typing import Optional

from .base import BaseProposer


class GptProposer(BaseProposer):
    name = "gpt"

    def __init__(self, *, model: str, api_key: Optional[str] = None, max_stocks: int = 8,
                 max_tokens: int = 2000):
        super().__init__(model=model, max_stocks=max_stocks)
        self.api_key = api_key or os.getenv("OPENAI_API_KEY")
        self.max_tokens = max_tokens

    def available(self) -> bool:
        return bool(self.api_key)

    def _complete(self, system: str, prompt: str) -> str:
        from openai import OpenAI

        client = OpenAI(api_key=self.api_key, max_retries=3)
        resp = client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
            response_format={"type": "json_object"},
        )
        return resp.choices[0].message.content or ""
