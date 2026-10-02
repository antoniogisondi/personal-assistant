from __future__ import annotations


class GsoiError(Exception):
    """Base class for all application errors."""


class NotFoundError(GsoiError):
    pass


class UnknownProfileError(GsoiError):
    pass


class LLMError(GsoiError):
    """Base class for errors coming from (or blocked before) an LLM provider."""


class RetryableLLMError(LLMError):
    """Transient failure: the gateway may retry or fall back."""


class ProviderUnavailableError(RetryableLLMError):
    pass


class RateLimitedError(RetryableLLMError):
    pass


class ProviderAuthError(LLMError):
    pass


class ProviderBadRequestError(LLMError):
    pass


class CapabilityError(LLMError):
    """The selected model does not support what the request needs (e.g. tools)."""


class EgressDeniedError(LLMError):
    """Data classification forbids sending this request to the selected provider."""
