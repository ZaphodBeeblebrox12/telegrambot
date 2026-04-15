"""Twitter Toggle Manager - Enable/Disable Twitter Publishing"""
import os
from typing import Optional

class TwitterToggleManager:
    """
    Global Twitter publishing toggle.
    Checks environment variable and config for enabled status.
    """

    def __init__(self):
        self._enabled = None
        self._env_checked = False

    def is_enabled(self) -> bool:
        """Check if Twitter publishing is globally enabled."""
        # Check environment variable first (override)
        env_val = os.getenv('TWITTER_ENABLED', '').lower()
        if env_val in ('true', '1', 'yes', 'on'):
            return True
        if env_val in ('false', '0', 'no', 'off'):
            return False

        # Default to True if not explicitly disabled
        # (maintains backward compatibility)
        return True

    def enable(self):
        """Enable Twitter (runtime only, not persistent)."""
        os.environ['TWITTER_ENABLED'] = 'true'

    def disable(self):
        """Disable Twitter (runtime only, not persistent)."""
        os.environ['TWITTER_ENABLED'] = 'false'

    @classmethod
    def check_before_send(cls) -> bool:
        """Class method for quick check before sending."""
        env_val = os.getenv('TWITTER_ENABLED', '').lower()
        if env_val in ('false', '0', 'no', 'off'):
            return False
        return True

# Singleton instance
_toggle_manager = None

def get_twitter_toggle_manager() -> TwitterToggleManager:
    global _toggle_manager
    if _toggle_manager is None:
        _toggle_manager = TwitterToggleManager()
    return _toggle_manager

def is_twitter_enabled() -> bool:
    """Quick check if Twitter is enabled."""
    return TwitterToggleManager.check_before_send()
