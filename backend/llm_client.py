"""Unified LLM client routed through OpenRouter.

All model traffic — including OpenAI, Anthropic, Google, Meta, xAI, DeepSeek,
etc. — flows through a single OpenRouter HTTP endpoint. Selecting a provider
is therefore reduced to choosing the correct OpenRouter model identifier
(e.g. ``openai/gpt-4o-mini``, ``anthropic/claude-sonnet-4``).
"""

import logging
import os
import threading
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Reasoning-effort values accepted by OpenRouter's `reasoning` parameter.
VALID_REASONING_EFFORTS = {"xhigh", "high", "medium", "low", "minimal", "none"}

# LLM prompt/response trace log.
LOG_DIR = os.path.join(os.path.dirname(__file__), "logs")
LLM_LOG_FILE = os.path.join(LOG_DIR, "llm_log.txt")
_log_lock = threading.Lock()


def _log_llm_interaction(model: str,
                         messages: List[Dict[str, str]],
                         response_content: str,
                         input_tokens: int,
                         output_tokens: int) -> None:
    """Append a single prompt/response trace to the on-disk log file."""
    os.makedirs(LOG_DIR, exist_ok=True)
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
    separator = "=" * 80

    lines = [
        separator,
        f"[{timestamp}] Model: {model} | Tokens: in={input_tokens} out={output_tokens}",
        "-" * 40 + " PROMPT " + "-" * 40,
    ]
    for msg in messages:
        lines.append(f"[{msg.get('role', 'unknown')}]")
        lines.append(msg.get("content", ""))
        lines.append("")
    lines.append("-" * 40 + " RESPONSE " + "-" * 38)
    lines.append(response_content)
    lines.append(separator)
    lines.append("")

    text = "\n".join(lines)
    with _log_lock:
        with open(LLM_LOG_FILE, "a", encoding="utf-8") as f:
            f.write(text)
            f.flush()


@dataclass
class LLMResponse:
    """Normalised response returned by :class:`LLMClient`."""
    content: str
    input_tokens: int
    output_tokens: int
    finish_reason: str = "stop"


class LLMClient:
    """Thin async wrapper around the OpenRouter chat-completions API."""

    def __init__(self, openrouter_client) -> None:
        if openrouter_client is None:
            raise ValueError(
                "LLMClient requires an OpenRouter HTTP client. "
                "Check that OPENROUTER_API_KEY is configured."
            )
        self.openrouter = openrouter_client

    # ------------------------------------------------------------------ #
    # Parameter building                                                  #
    # ------------------------------------------------------------------ #

    @staticmethod
    def _get_param(params: Any, attr: str) -> Any:
        """Read ``attr`` from either a Pydantic model or a plain dict."""
        if params is None:
            return None
        if hasattr(params, attr):
            return getattr(params, attr)
        if isinstance(params, dict):
            return params.get(attr)
        return None

    def build_params(self, model_params: Any) -> tuple[Dict[str, Any], Optional[Dict[str, str]]]:
        """Translate :class:`LLMModelParams` into OpenRouter call arguments.

        Returns:
            ``(api_params, reasoning_config)``. ``reasoning_config`` is
            ``None`` when no reasoning effort is requested.
        """
        api_params: Dict[str, Any] = {}
        reasoning_config: Optional[Dict[str, str]] = None

        for src_attr, dst_key in (
            ("temperature", "temperature"),
            ("maxCompletionTokens", "max_completion_tokens"),
            ("topP", "top_p"),
            ("frequencyPenalty", "frequency_penalty"),
            ("presencePenalty", "presence_penalty"),
        ):
            value = self._get_param(model_params, src_attr)
            if value is not None:
                api_params[dst_key] = value

        reasoning_effort = self._get_param(model_params, "reasoningEffort")
        if reasoning_effort and reasoning_effort in VALID_REASONING_EFFORTS:
            reasoning_config = {"effort": reasoning_effort}

        # Defaults that match the experiments reported in the dissertation.
        api_params.setdefault("temperature", 0.3)
        api_params.setdefault("max_completion_tokens", 16384)

        return api_params, reasoning_config

    # ------------------------------------------------------------------ #
    # Chat                                                                #
    # ------------------------------------------------------------------ #

    async def chat(self,
                   model: str,
                   messages: List[Dict[str, str]],
                   params: Any = None,
                   json_mode: bool = True,
                   temperature_override: Optional[float] = None) -> LLMResponse:
        """Send a chat-completions request through OpenRouter.

        Args:
            model: OpenRouter model identifier
                (e.g. ``"openai/gpt-4o-mini"``, ``"anthropic/claude-sonnet-4"``).
            messages: Chat messages in the standard OpenAI message format.
            params: A :class:`LLMModelParams` instance or equivalent dict.
            json_mode: Request a JSON response when ``True``.
            temperature_override: Optional one-off override for designer
                creativity (typically ``0.7``).

        Returns:
            A populated :class:`LLMResponse`.
        """
        api_params, reasoning_config = self.build_params(params)

        if temperature_override is not None:
            api_params["temperature"] = temperature_override

        request_body: Dict[str, Any] = {
            "model": model or "openai/gpt-4o-mini",
            "messages": messages,
            **api_params,
        }
        if json_mode:
            request_body["response_format"] = {"type": "json_object"}
        if reasoning_config:
            request_body["reasoning"] = reasoning_config

        response = await self.openrouter.post("/chat/completions", json=request_body)
        response_json = response.json()

        usage = response_json.get("usage") or {}
        input_tokens = int(usage.get("prompt_tokens", 0) or 0)
        output_tokens = int(usage.get("completion_tokens", 0) or 0)
        if not usage:
            logger.warning("OpenRouter response missing 'usage' field (model: %s)", model)
        else:
            logger.info(
                "OpenRouter tokens — input: %d, output: %d (model: %s)",
                input_tokens, output_tokens, model,
            )

        try:
            choice = response_json["choices"][0]
        except (KeyError, IndexError) as exc:
            raise RuntimeError(
                f"OpenRouter response missing choices array (model: {model}): {response_json}"
            ) from exc

        content = choice["message"]["content"]
        finish_reason = choice.get("finish_reason") or "stop"
        if not content:
            raise RuntimeError("LLM returned an empty response")
        if finish_reason == "length":
            logger.warning("OpenRouter response truncated (model: %s)", model)

        _log_llm_interaction(model, messages, content, input_tokens, output_tokens)
        return LLMResponse(
            content=content,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            finish_reason=finish_reason,
        )
