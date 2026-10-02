from __future__ import annotations

from gsoi_assistant.config.settings import ModelProfile
from gsoi_assistant.llm.base import Capabilities, LLMProvider
from gsoi_assistant.llm.providers.openai_compat import OpenAICompatProvider
from gsoi_assistant.security.secrets import SecretStore


def build_provider(name: str, profile: ModelProfile, secrets: SecretStore) -> LLMProvider:
    caps = Capabilities(
        tool_calling=profile.tool_calling,
        structured_output=profile.structured_output,
        context_window=profile.max_context,
        input_cost_per_mtok=profile.input_cost_per_mtok,
        output_cost_per_mtok=profile.output_cost_per_mtok,
        is_local=profile.is_local,
    )
    api_key = secrets.get(profile.api_key_ref) if profile.api_key_ref else None
    if profile.adapter == "openai_compat":
        return OpenAICompatProvider(
            name=profile.provider,
            model=profile.model,
            base_url=profile.base_url,
            api_key=api_key,
            capabilities=caps,
            timeout_s=profile.timeout_s,
        )
    raise ValueError(f"unknown adapter '{profile.adapter}' for profile '{name}'")
