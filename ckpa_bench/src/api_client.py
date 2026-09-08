"""OpenAI-compatible API client wrapping DeepSeek Flash."""

import json
import logging
import time
from typing import Optional

from openai import OpenAI

from config import APIConfig

logger = logging.getLogger(__name__)


class LLMClient:
    """Thin wrapper around OpenAI-compatible API with retry logic."""

    def __init__(self, cfg: APIConfig):
        self.cfg = cfg
        self.client = OpenAI(api_key=cfg.api_key, base_url=cfg.base_url)
        self._call_count = 0

    def call(
        self,
        system_prompt: str,
        user_prompt: str,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        response_format: Optional[dict] = None,
    ) -> str:
        """Send a chat completion request with retry."""
        temp = temperature if temperature is not None else self.cfg.temperature
        tokens = max_tokens if max_tokens is not None else self.cfg.max_tokens

        for attempt in range(self.cfg.max_retries):
            try:
                kwargs = {
                    "model": self.cfg.model,
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                }
                # gpt-5 on NATIVE OpenAI rejects max_tokens (needs max_completion_tokens)
                # and only allows default temperature=1. The gateway shim still accepts the old params.
                is_gpt5_openai = ("gpt-5" in self.cfg.model) and ("api.openai.com" in self.cfg.base_url)
                if is_gpt5_openai:
                    kwargs["max_completion_tokens"] = tokens
                else:
                    kwargs["temperature"] = temp
                    kwargs["max_tokens"] = tokens
                if response_format:
                    kwargs["response_format"] = response_format

                resp = self.client.chat.completions.create(**kwargs)
                self._call_count += 1
                return resp.choices[0].message.content

            except Exception as e:
                wait = self.cfg.retry_delay * (2 ** attempt)
                logger.warning(
                    "API call failed (attempt %d/%d): %s. Retrying in %.1fs",
                    attempt + 1, self.cfg.max_retries, e, wait
                )
                time.sleep(wait)

        raise RuntimeError(f"API call failed after {self.cfg.max_retries} retries")

    def call_json(
        self,
        system_prompt: str,
        user_prompt: str,
        temperature: Optional[float] = None,
    ) -> dict:
        """Call API and parse JSON response."""
        raw = self.call(
            system_prompt,
            user_prompt,
            temperature=temperature,
            response_format={"type": "json_object"},
        )
        # Try to extract JSON from response
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            # Try to find JSON block in markdown
            if "```json" in raw:
                block = raw.split("```json")[1].split("```")[0].strip()
                return json.loads(block)
            if "```" in raw:
                block = raw.split("```")[1].split("```")[0].strip()
                return json.loads(block)
            raise

    @property
    def total_calls(self) -> int:
        return self._call_count
