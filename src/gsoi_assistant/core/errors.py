from __future__ import annotations


class GsoiError(Exception):
    """Base class for all application errors."""


class NotFoundError(GsoiError):
    pass


class DatabaseConfigError(GsoiError):
    """The configured database cannot be used (bad path, missing folder, permissions)."""


class UnknownProfileError(GsoiError):
    pass


class ConflictError(GsoiError):
    """The request conflicts with the current state (e.g. approval already decided)."""


class BadRequestError(GsoiError):
    pass


class BudgetExceededError(GsoiError):
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


class ToolError(GsoiError):
    """A tool failed in a way the *model and user may be told about*.

    The message is authored by us (never raw upstream text), so the executor can return it as is.
    """


class ConnectorNotConnectedError(ToolError):
    """The external account (e.g. Google) is not connected or needs to be reconnected."""
