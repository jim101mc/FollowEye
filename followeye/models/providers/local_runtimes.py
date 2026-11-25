from __future__ import annotations

import json
from typing import Any

import openai

from ..base import ModelError, ModelProvider, ModelRequest, ProviderField, extract_json
from .openai_compatible import JSON_MODES, OpenAICompatibleProvider


class FoundryLocalProvider(OpenAICompatibleProvider):
    provider_id = "foundry_local"
    display_name = "Foundry Local (Microsoft)"
    description = "On-device models through Microsoft Foundry Local. Install it with `winget install Microsoft.FoundryLocal`."
    fields = (
        ProviderField("alias", "Model alias", "text", "phi-3.5-mini", help="Run `foundry model list` to see aliases."),
        ProviderField("vision", "Model accepts screenshots", "bool", False),
        ProviderField("json_mode", "JSON output mode", "choice", "json_object", choices=JSON_MODES),
    )

    def __init__(self, options, secrets):
        ModelProvider.__init__(self, options, secrets)
        try:
            from foundry_local import FoundryLocalManager
        except ImportError as e:
            raise ModelError("Foundry Local support is not installed (package foundry-local-sdk).") from e
        alias = self.options["alias"]
        try:
            manager = FoundryLocalManager(alias)  # starts the service and loads the model
            self.options["model"] = manager.get_model_info(alias).id
            self._client = openai.OpenAI(base_url=manager.endpoint, api_key=manager.api_key, timeout=120.0)
        except Exception as e:  # the SDK raises plain exceptions for service/model problems
            raise ModelError(f"Foundry Local could not start model '{alias}': {e}") from e


class OnnxGenAIProvider(ModelProvider):
    provider_id = "onnx_genai"
    display_name = "ONNX Runtime GenAI (local)"
    description = "Runs an ONNX GenAI model folder directly in-process, e.g. a Phi-3/Phi-4 mini ONNX export. Text only."
    fields = (
        ProviderField("model_dir", "Model folder", "path", "", help="Folder containing genai_config.json."),
        ProviderField("prompt_template", "Prompt template", "text",
                      "<|system|>\n{system}<|end|>\n<|user|>\n{prompt}<|end|>\n<|assistant|>\n",
                      help="Chat template of the model; {system} and {prompt} are filled in."),
        ProviderField("max_new_tokens", "Max new tokens", "int", 512),
    )

    def __init__(self, options, secrets):
        super().__init__(options, secrets)
        try:
            import onnxruntime_genai as og
        except ImportError as e:
            raise ModelError("ONNX Runtime GenAI is not installed (package onnxruntime-genai).") from e
        if not self.options["model_dir"]:
            raise ModelError("ONNX Runtime GenAI: choose a model folder.")
        self._og = og
        try:
            self._model = og.Model(self.options["model_dir"])
        except Exception as e:  # onnxruntime_genai raises plain exceptions
            raise ModelError(f"Could not load ONNX model: {e}") from e
        self._tokenizer = og.Tokenizer(self._model)

    def complete_json(self, request: ModelRequest) -> dict[str, Any]:
        og = self._og
        system = request.system + "\n\nReply with one JSON object matching this JSON schema:\n" + json.dumps(request.schema)
        prompt = self.options["prompt_template"].format(system=system, prompt=request.prompt)
        tokens = self._tokenizer.encode(prompt)
        params = og.GeneratorParams(self._model)
        params.set_search_options(max_length=len(tokens) + int(self.options["max_new_tokens"]))
        generator = og.Generator(self._model, params)
        generator.append_tokens(tokens)
        new_tokens: list[int] = []
        while not generator.is_done():
            generator.generate_next_token()
            new_tokens.append(int(generator.get_next_tokens()[0]))
        return extract_json(self._tokenizer.decode(new_tokens))
