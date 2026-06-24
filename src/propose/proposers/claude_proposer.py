"""Claude 제안자."""
from __future__ import annotations

import os
from typing import Optional

from .base import BaseProposer


class ClaudeProposer(BaseProposer):
    name = "claude"

    def __init__(self, *, model: str, api_key: Optional[str] = None, max_stocks: int = 8,
                 max_tokens: int = 2000):
        super().__init__(model=model, max_stocks=max_stocks)
        self.api_key = api_key or os.getenv("ANTHROPIC_API_KEY")
        self.max_tokens = max_tokens

    def available(self) -> bool:
        return bool(self.api_key)

    def _complete(self, system: str, prompt: str) -> str:
        import anthropic

        client = anthropic.Anthropic(api_key=self.api_key, max_retries=3)
        msg = client.messages.create(
            model=self.model,
            max_tokens=self.max_tokens,
            system=system,
            messages=[{"role": "user", "content": prompt}],
        )
        return "".join(
            getattr(b, "text", "") for b in msg.content
            if getattr(b, "type", None) == "text"
        )
