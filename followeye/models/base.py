from __future__ import annotations

import json
from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, ClassVar, Literal

FieldKind = Literal["text", "secret", "path", "int", "bool", "choice"]


class ModelError(Exception):
    pass


@dataclass(frozen=True)
class ProviderField:
    key: str
    label: str
    kind: FieldKind = "text"
    default: Any = ""
    choices: tuple[str, ...] = ()
    placeholder: str = ""
    help: str = ""


@dataclass
class ModelRequest:
    system: str
    prompt: str
    schema: dict[str, Any]
    image_png: bytes | None = None
    max_tokens: int = 2048


SecretGetter = Callable[[str], str | None]


class ModelProvider(ABC):
    """A model backend. Subclass it (also from a plugin file) to add a new local or cloud model."""

    provider_id: ClassVar[str]
    display_name: ClassVar[str]
    description: ClassVar[str] = ""
    fields: ClassVar[tuple[ProviderField, ...]] = ()

    def __init__(self, options: dict[str, Any], secrets: SecretGetter):
        self.options = {f.key: options.get(f.key, f.default) for f in self.fields}
        self._secrets = secrets

    def secret(self, key: str) -> str | None:
        return self._secrets(secret_name(self.provider_id, key))

    @property
    def supports_images(self) -> bool:
        return False

    @abstractmethod
    def complete_json(self, request: ModelRequest) -> dict[str, Any]:
        """Run the request and return the parsed JSON object. Raise ModelError on failure."""

    def test(self) -> str:
        result = self.complete_json(
            ModelRequest(
                system="Reply with JSON only.",
                prompt='Return {"ok": true}.',
                schema={
                    "type": "object",
                    "properties": {"ok": {"type": "boolean"}},
                    "required": ["ok"],
                    "additionalProperties": False,
                },
                max_tokens=256,
            )
        )
        return f"Connected: {json.dumps(result)}"


def secret_name(provider_id: str, key: str) -> str:
    return f"provider.{provider_id}.{key}"


def extract_json(text: str) -> dict[str, Any]:
    """Return the first JSON object in `text` (models often wrap JSON in prose or code fences)."""
    decoder = json.JSONDecoder()
    for i, ch in enumerate(text):
        if ch != "{":
            continue
        try:
            value, _ = decoder.raw_decode(text, i)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    raise ModelError("Model did not return a JSON object.")


@dataclass(frozen=True)
class ProviderSpec:
    cls: type[ModelProvider]
    source: str = "built-in"
