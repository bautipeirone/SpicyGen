"""Stateless OpenAI Responses API client for AG2.

AG2 0.11's built-in Responses clients (``api_type: responses`` and
``responses_v2``) chain requests with ``previous_response_id`` *and* resend the
whole chat history, so every turn re-appends the conversation to the
server-side context. In a refinement loop the spec ends up in the context once
per turn.

This client instead sends the full history on every request with
``store=False``, like Chat Completions does. Reasoning is carried across tool
calls by asking for ``reasoning.encrypted_content`` and replaying each turn's
raw output items (reasoning + function calls) verbatim on the next request.
Stable prefixes keep OpenAI's prompt cache warm, so the resent history is billed
at the cached-input rate.
"""

from typing import Any
from uuid import uuid4

from openai import OpenAI

from src.logger import logger

# Request keys AG2 builds that map directly onto the Responses API.
_PASSTHROUGH_PARAMS = frozenset({
    "model", "parallel_tool_calls", "temperature", "top_p", "service_tier",
    "metadata", "user", "prompt_cache_key", "safety_identifier", "truncation",
})
# AG2 adds these internally; the Responses API has no equivalent.
_IGNORED_PARAMS = frozenset({"stream", "n"})
# Constructor-only keys from the AG2 config entry.
_CLIENT_ONLY_CONFIG = frozenset({"model_client_cls", "api_type", "tags", "price", "stream"})


def _content_text(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            part.get("text", "") if isinstance(part, dict) else str(part)
            for part in content
        )
    return str(content)


def _turn_key(message: dict[str, Any]) -> str:
    """Identify an assistant turn as AG2 stores it in chat history."""
    tool_calls = message.get("tool_calls") or []
    if tool_calls:
        return "calls:" + ",".join(str(call.get("id")) for call in tool_calls)
    return "text:" + _content_text(message.get("content"))


class StatelessResponsesClient:
    """AG2 ``ModelClient`` that talks to ``/v1/responses`` without server state."""

    RESPONSE_USAGE_KEYS: list[str] = ["prompt_tokens", "completion_tokens", "total_tokens", "cost", "model"]

    def __init__(
        self,
        config: dict[str, Any],
        price: tuple[float, float] | None = None,
        cached_price: float | None = None,
        turn_items: dict[str, list[dict[str, Any]]] | None = None,
    ) -> None:
        client_kwargs = {
            key: value for key, value in config.items()
            if key in {"api_key", "base_url", "organization", "project", "timeout", "max_retries", "default_headers"}
        }
        self._oai_client = OpenAI(**client_kwargs)
        self._price = price
        # Without an explicit cached rate, bill cached tokens at full price.
        self._cached_price = cached_price if cached_price is not None else (price[0] if price else None)
        # Raw output items per assistant turn, replayed on later requests.
        self._turn_items = turn_items if turn_items is not None else {}
        # One key per client routes this agent's requests to the same cache.
        self._prompt_cache_key = f"grammar-generator-{uuid4()}"

    # ------------------------------------------------------------------ request

    def _convert_messages(self, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        for message in messages:
            role = message.get("role", "user")
            if role == "tool":
                items.append({
                    "type": "function_call_output",
                    "call_id": message["tool_call_id"],
                    "output": _content_text(message.get("content")),
                })
            elif role == "assistant":
                replay = self._turn_items.get(_turn_key(message))
                if replay is not None:
                    items.extend(replay)
                    continue
                text = _content_text(message.get("content"))
                if text:
                    items.append({"role": "assistant", "content": text})
                for call in message.get("tool_calls") or []:
                    items.append({
                        "type": "function_call",
                        "call_id": call["id"],
                        "name": call["function"]["name"],
                        "arguments": call["function"]["arguments"],
                    })
            elif role in {"system", "developer", "user"}:
                items.append({"role": role, "content": _content_text(message.get("content"))})
            else:
                raise ValueError(f"Unsupported message role for the Responses API: {role}")
        return items

    @staticmethod
    def _convert_tools(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
        converted = []
        for tool in tools:
            function = tool.get("function", tool)
            converted.append({
                "type": "function",
                "name": function["name"],
                "description": function.get("description", ""),
                "parameters": function.get("parameters", {"type": "object", "properties": {}}),
                "strict": False,
            })
        return converted

    def build_request(self, params: dict[str, Any]) -> dict[str, Any]:
        params = dict(params)
        request: dict[str, Any] = {
            "input": self._convert_messages(params.pop("messages", [])),
            "store": False,
            "include": ["reasoning.encrypted_content"],
            "prompt_cache_key": self._prompt_cache_key,
        }
        if tools := params.pop("tools", None):
            request["tools"] = self._convert_tools(tools)
        if (effort := params.pop("reasoning_effort", None)) is not None:
            request["reasoning"] = {"effort": effort}
        if (max_tokens := params.pop("max_tokens", None)) is not None:
            # Reasoning tokens count against this limit on the Responses API.
            request["max_output_tokens"] = max_tokens
        if (verbosity := params.pop("verbosity", None)) is not None:
            request["text"] = {"verbosity": verbosity}
        if params.pop("seed", None) is not None:
            logger.warning("The Responses API has no `seed` parameter; ignoring it.")
        if params.pop("response_format", None) is not None:
            raise ValueError("response_format is not supported by StatelessResponsesClient")

        for key, value in params.items():
            if key in _PASSTHROUGH_PARAMS:
                request[key] = value
            elif key in _IGNORED_PARAMS or key in _CLIENT_ONLY_CONFIG:
                continue
            else:
                raise ValueError(f"Unsupported parameter for the Responses API: {key}")
        return request

    def create(self, params: dict[str, Any]) -> Any:
        return self._oai_client.responses.create(**self.build_request(params))

    # ----------------------------------------------------------------- response

    def message_retrieval(self, response: Any) -> list[dict[str, Any]]:
        """Return one chat-format assistant message and remember its raw items."""
        raw_items: list[dict[str, Any]] = []
        text_parts: list[str] = []
        tool_calls: list[dict[str, Any]] = []
        for item in getattr(response, "output", None) or []:
            data = item.model_dump(mode="json", exclude_none=True) if hasattr(item, "model_dump") else dict(item)
            raw_items.append(data)
            if data.get("type") == "message":
                text_parts.extend(
                    block.get("text", "") for block in data.get("content", [])
                    if block.get("type") == "output_text"
                )
            elif data.get("type") == "function_call":
                tool_calls.append({
                    "id": data["call_id"],
                    "type": "function",
                    "function": {"name": data["name"], "arguments": data.get("arguments") or "{}"},
                })

        if getattr(response, "status", "completed") == "incomplete":
            details = getattr(response, "incomplete_details", None)
            logger.warning("Responses API returned an incomplete response: %s", getattr(details, "reason", details))

        message: dict[str, Any] = {"role": "assistant", "content": "".join(text_parts) or None}
        if tool_calls:
            message["tool_calls"] = tool_calls
        self._turn_items[_turn_key(message)] = raw_items
        return [message]

    @staticmethod
    def _token_counts(response: Any) -> dict[str, int]:
        usage = getattr(response, "usage", None)
        if usage is None:
            return {"input": 0, "cached": 0, "output": 0, "reasoning": 0, "total": 0}
        input_details = getattr(usage, "input_tokens_details", None)
        output_details = getattr(usage, "output_tokens_details", None)
        return {
            "input": usage.input_tokens or 0,
            "cached": getattr(input_details, "cached_tokens", 0) or 0,
            "output": usage.output_tokens or 0,
            "reasoning": getattr(output_details, "reasoning_tokens", 0) or 0,
            "total": usage.total_tokens or 0,
        }

    def cost(self, response: Any) -> float:
        if self._price is None:
            return 0.0
        tokens = self._token_counts(response)
        uncached = tokens["input"] - tokens["cached"]
        return (
            uncached * self._price[0]
            + tokens["cached"] * self._cached_price
            + tokens["output"] * self._price[1]
        ) / 1000

    def get_usage(self, response: Any) -> dict[str, Any]:
        tokens = self._token_counts(response)
        logger.debug(
            "Responses usage: input=%s (cached=%s) output=%s (reasoning=%s)",
            tokens["input"], tokens["cached"], tokens["output"], tokens["reasoning"],
        )
        cost = getattr(response, "cost", None)
        return {
            "prompt_tokens": tokens["input"],
            "completion_tokens": tokens["output"],
            "total_tokens": tokens["total"],
            "cost": cost if cost is not None else self.cost(response),
            "model": getattr(response, "model", ""),
            "cached_tokens": tokens["cached"],
            "reasoning_tokens": tokens["reasoning"],
        }
