"""방어적 외부 호출 헬퍼.

호출 사이 짧은 지연(rate limit 존중) + 429/5xx에 대한 지수 backoff 재시도.
최종 실패해도 예외를 던지지 않고 None을 반환해 파이프라인이 죽지 않게 한다.
"""
from __future__ import annotations

import logging
import time
from typing import Optional

import requests

logger = logging.getLogger(__name__)

RETRY_STATUS = {429, 500, 502, 503, 504}


def get_with_retry(
    url: str,
    *,
    headers: Optional[dict] = None,
    params: Optional[dict] = None,
    max_retries: int = 3,
    backoff_base: float = 1.0,
    timeout: float = 10.0,
    pause: float = 0.3,
) -> Optional[requests.Response]:
    """GET을 backoff 재시도와 함께 수행. 최종 실패 시 None."""
    for attempt in range(max_retries + 1):
        try:
            resp = requests.get(url, headers=headers, params=params, timeout=timeout)
            if resp.status_code in RETRY_STATUS:
                raise requests.HTTPError(f"retryable status {resp.status_code}")
            resp.raise_for_status()
            time.sleep(pause)  # 호출 사이 짧은 지연
            return resp
        except requests.RequestException as exc:
            logger.warning(
                "GET %s 실패 (시도 %d/%d): %s", url, attempt + 1, max_retries + 1, exc
            )
            if attempt < max_retries:
                time.sleep(backoff_base * (2 ** attempt))
    logger.error("GET %s 최종 실패 — None 반환", url)
    return None
