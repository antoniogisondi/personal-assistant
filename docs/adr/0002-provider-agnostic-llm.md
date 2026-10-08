# ADR 0002 — Provider-agnostic LLM access via profiles and a gateway

Status: accepted

- Model ids live only in configuration, as named *profiles* (`reasoning`, `private`, ...).
- One `LLMProvider` Protocol; one OpenAI-compatible adapter covers DeepSeek, OpenAI, Ollama,
  vLLM and llama.cpp. Other protocols get their own adapter.
- All calls go through `LLMGateway`: egress policy (SECRET never reaches a non-local model),
  capability checks, retry/backoff, fallback profile, cost, and `model_calls` recording.
- API keys are referenced by name (`api_key_ref`) and resolved by a `SecretStore`.
