"""Unified LLM client for OpenAI and OpenRouter APIs.

Eliminates duplicated API call code and MockResponse classes.
"""

import json
import logging
import os
import threading
from dataclasses import dataclass
from datetime import datetime
from typing import List, Dict, Any, Optional, Set

logger = logging.getLogger(__name__)

# Global OpenAI model list cache
openai_models_cache: Set[str] = set()
openai_models_cache_lock = threading.Lock()

VALID_REASONING_EFFORTS = {"xhigh", "high", "medium", "low", "minimal", "none"}

# LLM prompt/response log file
LOG_DIR = os.path.join(os.path.dirname(__file__), "logs")
LLM_LOG_FILE = os.path.join(LOG_DIR, "llm_log.txt")
_log_lock = threading.Lock()


def _log_llm_interaction(model: str, messages: List[Dict[str, str]],
                         response_content: str, input_tokens: int,
                         output_tokens: int, api_type: str):
    """Write prompt and response to log file in real-time."""
    os.makedirs(LOG_DIR, exist_ok=True)
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
    separator = "=" * 80

    lines = [
        separator,
        f"[{timestamp}] Model: {model} | API: {api_type} | Tokens: in={input_tokens} out={output_tokens}",
        "-" * 40 + " PROMPT " + "-" * 40,
    ]
    for msg in messages:
        role = msg.get("role", "unknown")
        content = msg.get("content", "")
        lines.append(f"[{role}]")
        lines.append(content)
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
    """Unified response from any LLM API."""
    content: str
    input_tokens: int
    output_tokens: int
    finish_reason: str = "stop"


class LLMClient:
    """Unified LLM client that routes to OpenAI or OpenRouter."""

    def __init__(self, openai_client=None, openrouter_client=None):
        self.openai = openai_client
        self.openrouter = openrouter_client

    def is_openai_model(self, model_name: str) -> bool:
        """Check if a model should use the OpenAI API."""
        if model_name.startswith("openai_official/"):
            return True
        clean_model_name = model_name.replace("openai_official/", "")
        with openai_models_cache_lock:
            return clean_model_name in openai_models_cache

    def build_params(self, model_params) -> tuple[Dict[str, Any], Optional[Dict[str, str]]]:
        """Build API call parameters from LLMModelParams.

        Returns:
            Tuple of (api_params, reasoning_config).
            reasoning_config is None if not set, or {"effort": "high"} etc.
        """
        params = {}
        reasoning_config = None

        if model_params:
            if hasattr(model_params, 'temperature') and model_params.temperature is not None:
                params["temperature"] = model_params.temperature
            elif isinstance(model_params, dict) and model_params.get('temperature') is not None:
                params["temperature"] = model_params['temperature']

            if hasattr(model_params, 'maxCompletionTokens') and model_params.maxCompletionTokens is not None:
                params["max_completion_tokens"] = model_params.maxCompletionTokens
            elif isinstance(model_params, dict) and model_params.get('maxCompletionTokens') is not None:
                params["max_completion_tokens"] = model_params['maxCompletionTokens']

            if hasattr(model_params, 'topP') and model_params.topP is not None:
                params["top_p"] = model_params.topP
            elif isinstance(model_params, dict) and model_params.get('topP') is not None:
                params["top_p"] = model_params['topP']

            if hasattr(model_params, 'frequencyPenalty') and model_params.frequencyPenalty is not None:
                params["frequency_penalty"] = model_params.frequencyPenalty
            elif isinstance(model_params, dict) and model_params.get('frequencyPenalty') is not None:
                params["frequency_penalty"] = model_params['frequencyPenalty']

            if hasattr(model_params, 'presencePenalty') and model_params.presencePenalty is not None:
                params["presence_penalty"] = model_params.presencePenalty
            elif isinstance(model_params, dict) and model_params.get('presencePenalty') is not None:
                params["presence_penalty"] = model_params['presencePenalty']

            # Extract reasoning effort (handled separately as top-level param)
            reasoning_effort = None
            if hasattr(model_params, 'reasoningEffort') and model_params.reasoningEffort is not None:
                reasoning_effort = model_params.reasoningEffort
            elif isinstance(model_params, dict) and model_params.get('reasoningEffort') is not None:
                reasoning_effort = model_params['reasoningEffort']

            if reasoning_effort and reasoning_effort in VALID_REASONING_EFFORTS:
                reasoning_config = {"effort": reasoning_effort}

        # Set defaults
        if "temperature" not in params:
            params["temperature"] = 0.3
        if "max_completion_tokens" not in params:
            params["max_completion_tokens"] = 16384

        return params, reasoning_config

    async def chat(self, model: str, messages: List[Dict[str, str]],
                   params=None,
                   json_mode: bool = True,
                   temperature_override: Optional[float] = None) -> LLMResponse:
        """Send a chat completion request to the appropriate API.

        Args:
            model: Model name (e.g., "openai_official/gpt-4o" or "anthropic/claude-3.5-sonnet")
            messages: Chat messages in OpenAI format
            params: LLMModelParams or dict with model parameters
            json_mode: Whether to request JSON output format
            temperature_override: Override temperature (e.g., 0.7 for designer creativity)

        Returns:
            LLMResponse with content, input_tokens, output_tokens
        """
        api_params, reasoning_config = self.build_params(params)

        if temperature_override is not None:
            api_params["temperature"] = temperature_override

        if json_mode:
            response_format = {"type": "json_object"}
        else:
            response_format = None

        if self.is_openai_model(model):
            return await self._call_openai(model, messages, api_params, response_format, reasoning_config)
        else:
            return await self._call_openrouter(model, messages, api_params, response_format, reasoning_config)

    async def _call_openai(self, model: str, messages: List[Dict[str, str]],
                            api_params: Dict[str, Any],
                            response_format: Optional[Dict],
                            reasoning_config: Optional[Dict] = None) -> LLMResponse:
        """Call the OpenAI API."""
        if not self.openai:
            raise Exception("OpenAI API client not initialized. Check OPENAI_API_KEY.")

        clean_model_name = model.replace("openai_official/", "")

        kwargs = {
            "model": clean_model_name,
            "messages": messages,
            **api_params,
        }
        if response_format:
            kwargs["response_format"] = response_format
        if reasoning_config and reasoning_config.get("effort"):
            kwargs["reasoning_effort"] = reasoning_config["effort"]

        response = await self.openai.chat.completions.create(**kwargs)

        content = response.choices[0].message.content
        finish_reason = response.choices[0].finish_reason or "stop"
        input_tokens = 0
        output_tokens = 0
        if hasattr(response, 'usage') and response.usage:
            input_tokens = response.usage.prompt_tokens or 0
            output_tokens = response.usage.completion_tokens or 0
            logger.info(f"OpenAI API tokens: input {input_tokens}, output {output_tokens}, finish_reason: {finish_reason}")

        if not content:
            raise Exception("LLM returned empty response")

        _log_llm_interaction(clean_model_name, messages, content, input_tokens, output_tokens, "OpenAI")
        return LLMResponse(content=content, input_tokens=input_tokens, output_tokens=output_tokens, finish_reason=finish_reason)

    async def _call_openrouter(self, model: str, messages: List[Dict[str, str]],
                                api_params: Dict[str, Any],
                                response_format: Optional[Dict],
                                reasoning_config: Optional[Dict] = None) -> LLMResponse:
        """Call the OpenRouter API."""
        if not self.openrouter:
            raise Exception("OpenRouter API client not initialized. Check OPENROUTER_API_KEY.")

        request_body = {
            "model": model or "openai/gpt-4o-mini",
            "messages": messages,
            **api_params,
        }
        if response_format:
            request_body["response_format"] = response_format
        if reasoning_config:
            request_body["reasoning"] = reasoning_config

        response_data = await self.openrouter.post("/chat/completions", json=request_body)
        response_json = response_data.json()

        # Extract token usage
        input_tokens = 0
        output_tokens = 0
        if 'usage' in response_json:
            usage = response_json['usage']
            input_tokens = usage.get('prompt_tokens', 0)
            output_tokens = usage.get('completion_tokens', 0)
            logger.info(f"OpenRouter API tokens: input {input_tokens}, output {output_tokens}")
        else:
            logger.warning(f"OpenRouter API response missing 'usage' field, model: {model}")

        content = response_json['choices'][0]['message']['content']
        finish_reason = response_json['choices'][0].get('finish_reason', 'stop') or 'stop'
        if not content:
            raise Exception("LLM returned empty response")

        if finish_reason == 'length':
            logger.warning(f"OpenRouter response truncated (finish_reason=length), model: {model}")

        _log_llm_interaction(model, messages, content, input_tokens, output_tokens, "OpenRouter")
        return LLMResponse(content=content, input_tokens=input_tokens, output_tokens=output_tokens, finish_reason=finish_reason)
