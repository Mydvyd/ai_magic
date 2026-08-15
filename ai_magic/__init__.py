from .api import chat, code
from .client import AsyncAIMagic
from .config import Settings
from .dto import ChatCompletion, ChatCompletionRequest, ChatMessage, Choice, Usage
from .exceptions import AIMagicError, AllKeysUnavailableError, AuthenticationError, ConfigurationError, ProviderError, RateLimitError
from .providers import ProviderConfig, ProviderRegistry
from .state import Credential, KeyManager

__all__ = [
    "AsyncAIMagic", "Settings", "chat", "code", "ChatCompletion", "ChatCompletionRequest",
    "ChatMessage", "Choice", "Usage", "AIMagicError", "ConfigurationError",
    "ProviderError", "RateLimitError", "AuthenticationError", "AllKeysUnavailableError",
    "ProviderConfig", "ProviderRegistry", "Credential", "KeyManager",
]
