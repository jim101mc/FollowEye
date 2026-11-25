from __future__ import annotations

import importlib.util
import inspect
import logging
from pathlib import Path

from ..config import ProviderConfig
from .base import ModelError, ModelProvider, ProviderSpec, SecretGetter
from .providers.anthropic_provider import AnthropicProvider
from .providers.local_runtimes import FoundryLocalProvider, OnnxGenAIProvider
from .providers.openai_compatible import OllamaProvider, OpenAICompatibleProvider, OpenAIProvider

log = logging.getLogger(__name__)

BUILTIN_PROVIDERS: tuple[type[ModelProvider], ...] = (
    OllamaProvider,
    FoundryLocalProvider,
    OnnxGenAIProvider,
    AnthropicProvider,
    OpenAIProvider,
    OpenAICompatibleProvider,
)

PLUGIN_HELP = """\
Drop a .py file in the plugins folder that defines a subclass of
followeye.models.base.ModelProvider with a unique provider_id, display_name,
fields and a complete_json() method. It appears in this list after a restart.
Plugins run with your user rights: only add files you trust."""


class ProviderRegistry:
    def __init__(self) -> None:
        self._specs: dict[str, ProviderSpec] = {}
        for cls in BUILTIN_PROVIDERS:
            self.register(cls)

    def register(self, cls: type[ModelProvider], source: str = "built-in") -> None:
        if cls.provider_id in self._specs:
            raise ValueError(f"Provider id '{cls.provider_id}' is already registered.")
        self._specs[cls.provider_id] = ProviderSpec(cls, source)

    def specs(self) -> list[ProviderSpec]:
        return list(self._specs.values())

    def get(self, provider_id: str) -> ProviderSpec | None:
        return self._specs.get(provider_id)

    def create(self, config: ProviderConfig, secrets: SecretGetter) -> ModelProvider | None:
        if not config.provider_id:
            return None
        spec = self._specs.get(config.provider_id)
        if spec is None:
            raise ModelError(f"Unknown model provider '{config.provider_id}'.")
        return spec.cls(config.options, secrets)

    def load_plugins(self, folder: Path) -> list[str]:
        """Import every .py file in `folder` and register the providers it defines. Returns errors."""
        errors = []
        folder.mkdir(parents=True, exist_ok=True)
        for path in sorted(folder.glob("*.py")):
            try:
                spec = importlib.util.spec_from_file_location(f"followeye_plugin_{path.stem}", path)
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
                for _, obj in inspect.getmembers(module, inspect.isclass):
                    if (
                        issubclass(obj, ModelProvider)
                        and obj.__module__ == module.__name__
                        and not inspect.isabstract(obj)
                    ):
                        self.register(obj, source=path.name)
            except Exception as e:  # a broken plugin must not stop the app
                log.exception("Failed to load plugin %s", path)
                errors.append(f"{path.name}: {e}")
        return errors
