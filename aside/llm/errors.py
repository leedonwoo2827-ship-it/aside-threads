# -*- coding: utf-8 -*-
"""프로바이더 예외 — 사내 LLM 브릿지와 같은 계층.

문자열 매칭 대신 타입으로 갈라야 호출부가 "다시 로그인" 과 "잠시 후 재시도" 를
구별해 안내할 수 있다.

`token_revoked`(HTTP 401)가 `NotAuthenticated` 로 오는 것이 특히 중요하다 —
일반 실패로 두면 배치가 재시도로 남은 구독 할당량을 태운다.
"""
from __future__ import annotations


class ProviderError(RuntimeError):
    """일반 실패."""


class NotAuthenticated(ProviderError):
    """로그인이 안 되어 있거나 만료. → 터미널에서 `codex login`."""


class QuotaExceeded(ProviderError):
    """사용량·레이트리밋. → 잠시 후 재시도."""
