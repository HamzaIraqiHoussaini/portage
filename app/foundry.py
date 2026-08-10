"""Compatibility shim — prefer app.providers."""

from .providers import (  # noqa: F401
    FoundryError,
    ProviderError,
    apply_saved_config_to_settings,
    chat_completion,
    load_bridge_config,
    provider_connected,
    save_bridge_config,
    test_connection,
)
