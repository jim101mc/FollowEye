from __future__ import annotations

import base64
import json
from typing import Any

import anthropic

from ..base import ModelError, ModelProvider, ModelRequest, ProviderField

FALLBACK_BETA = "server-side-fallback-2026-07-01"


class AnthropicProvider(ModelProvider):
    provider_id = "anthropic"
    display_name = "Claude (Anthropic API)"
    description = "Claude models through the Anthropic API. Supports screenshots."
    fields = (
        ProviderField("api_key", "API key", "secret", placeholder="sk-ant-...",
                      help="Leave empty to use ANTHROPIC_API_KEY or an `ant auth login` profile."),
        ProviderField("model", "Model", "text", "claude-opus-5-5"),
        ProviderField("effort", "Effort", "choice", "medium", choices=("low", "medium", "high")),
        ProviderField("refusal_fallback", "Retry declined requests on a fallback model", "bool", True),
    )

    def __init__(self, options, secrets):
        super().__init__(options, secrets)
        self._client = anthropic.Anthropic(api_key=self.secret("api_key") or None, timeout=60.0, max_retries=1)

    @property
    def supports_images(self) -> bool:
        return True

    def complete_json(self, request: ModelRequest) -> dict[str, Any]:
        content: list[dict[str, Any]] = []
        if request.image_png:
            content.append({
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": "image/png",
                    "data": base64.standard_b64encode(request.image_png).decode(),
                },
            })
        content.append({"type": "text", "text": request.prompt})
        extra: dict[str, Any] = {}
        if self.options["refusal_fallback"]:
            extra = {"betas": [FALLBACK_BETA], "fallbacks": "default"}
        try:
            response = self._client.beta.messages.create(
                model=self.options["model"],
                max_tokens=max(request.max_tokens, 16000),
                system=request.system,
                messages=[{"role": "user", "content": content}],
                output_config={
                    "effort": self.options["effort"],
                    "format": {"type": "json_schema", "schema": request.schema},
                },
                **extra,
            )
        except anthropic.AuthenticationError as e:
            raise ModelError("Anthropic API key was rejected.") from e
        except anthropic.NotFoundError as e:
            raise ModelError(f"Model '{self.options['model']}' was not found.") from e
        except anthropic.RateLimitError as e:
            raise ModelError("Anthropic rate limit reached; try again shortly.") from e
        except anthropic.APIStatusError as e:
            raise ModelError(f"Anthropic API error {e.status_code}: {e.message}") from e
        except anthropic.APIConnectionError as e:
            raise ModelError("Could not reach the Anthropic API.") from e
        if response.stop_reason == "refusal":
            raise ModelError("The model declined this request.")
        text = next((b.text for b in response.content if b.type == "text"), "")
        try:
            return json.loads(text)
        except json.JSONDecodeError as e:
            raise ModelError("Claude returned invalid JSON.") from e
