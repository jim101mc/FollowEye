from __future__ import annotations

import base64
import json
from typing import Any

import openai

from ..base import ModelError, ModelProvider, ModelRequest, ProviderField, extract_json

JSON_MODES = ("json_schema", "json_object", "prompt")


class OpenAICompatibleProvider(ModelProvider):
    """Any server that speaks the OpenAI chat-completions API: LM Studio, vLLM, llama.cpp, your own model."""

    provider_id = "openai_compatible"
    display_name = "Custom (OpenAI-compatible endpoint)"
    description = "Your own model server, e.g. LM Studio, vLLM, llama.cpp or a self-hosted model."
    fields = (
        ProviderField("base_url", "Base URL", "text", "", placeholder="http://localhost:1234/v1"),
        ProviderField("model", "Model", "text", "", placeholder="model name as the server lists it"),
        ProviderField("api_key", "API key", "secret", help="Leave empty if the server needs none."),
        ProviderField("vision", "Model accepts screenshots", "bool", False),
        ProviderField("json_mode", "JSON output mode", "choice", "json_object", choices=JSON_MODES,
                      help="Use 'prompt' if the server rejects response_format."),
    )
    default_api_key = "not-needed"

    def __init__(self, options, secrets):
        super().__init__(options, secrets)
        if not self.options["model"]:
            raise ModelError(f"{self.display_name}: set a model name.")
        try:
            self._client = openai.OpenAI(
                base_url=self._base_url() or None,
                api_key=self.secret("api_key") or self.default_api_key,
                timeout=60.0,
                max_retries=1,
            )
        except openai.OpenAIError as e:
            raise ModelError(f"{self.display_name}: {e}") from e

    def _base_url(self) -> str:
        return self.options.get("base_url", "")

    @property
    def supports_images(self) -> bool:
        return bool(self.options.get("vision"))

    def complete_json(self, request: ModelRequest) -> dict[str, Any]:
        user: list[dict[str, Any]] = [{"type": "text", "text": request.prompt}]
        if request.image_png and self.supports_images:
            data = base64.standard_b64encode(request.image_png).decode()
            user.insert(0, {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{data}"}})
        mode = self.options["json_mode"]
        system = request.system
        kwargs: dict[str, Any] = {}
        if mode == "json_schema":
            kwargs["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": "followeye_result", "schema": request.schema, "strict": True},
            }
        else:
            if mode == "json_object":
                kwargs["response_format"] = {"type": "json_object"}
            system += "\n\nReply with one JSON object matching this JSON schema:\n" + json.dumps(request.schema)
        try:
            response = self._client.chat.completions.create(
                model=self.options["model"],
                messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
                max_tokens=request.max_tokens,
                **kwargs,
            )
        except openai.AuthenticationError as e:
            raise ModelError(f"{self.display_name}: API key was rejected.") from e
        except openai.NotFoundError as e:
            raise ModelError(f"{self.display_name}: model or endpoint not found.") from e
        except openai.RateLimitError as e:
            raise ModelError(f"{self.display_name}: rate limit reached.") from e
        except openai.APIStatusError as e:
            raise ModelError(f"{self.display_name}: error {e.status_code}: {e.message}") from e
        except openai.APIConnectionError as e:
            raise ModelError(f"{self.display_name}: could not connect.") from e
        message = response.choices[0].message
        if getattr(message, "refusal", None):
            raise ModelError("The model declined this request.")
        return extract_json(message.content or "")


class OpenAIProvider(OpenAICompatibleProvider):
    provider_id = "openai"
    display_name = "OpenAI API"
    description = "OpenAI models through the OpenAI API."
    fields = (
        ProviderField("api_key", "API key", "secret", placeholder="sk-...",
                      help="Leave empty to use OPENAI_API_KEY."),
        ProviderField("model", "Model", "text", "", placeholder="e.g. a vision-capable GPT model"),
        ProviderField("vision", "Model accepts screenshots", "bool", True),
        ProviderField("json_mode", "JSON output mode", "choice", "json_schema", choices=JSON_MODES),
    )
    default_api_key = None

    def _base_url(self) -> str:
        return ""


class OllamaProvider(OpenAICompatibleProvider):
    provider_id = "ollama"
    display_name = "Ollama (local)"
    description = "Models served by Ollama on this PC. Pull a model first, e.g. `ollama pull llama3.2`."
    fields = (
        ProviderField("base_url", "Base URL", "text", "http://localhost:11434/v1"),
        ProviderField("model", "Model", "text", "", placeholder="e.g. llama3.2 or llama3.2-vision"),
        ProviderField("vision", "Model accepts screenshots", "bool", False),
        ProviderField("json_mode", "JSON output mode", "choice", "json_object", choices=JSON_MODES),
    )
    default_api_key = "ollama"
