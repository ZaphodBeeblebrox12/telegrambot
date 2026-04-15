"""Twitter Style Manager - Event Type Filtering"""
import os
from typing import Set, Optional, List
from enum import Enum

class EventType(Enum):
    """Trade event types for Twitter filtering."""
    TRADE_SETUP = "trade_setup"
    POSITION_UPDATE = "position_update"
    PARTIAL_CLOSE = "partial_close"
    CLOSE_HALF = "close_half"
    TRAIL_UPDATE = "trail_update"
    TARGET_HIT = "target_hit"
    STOPPED_OUT = "stopped_out"
    BREAKEVEN = "breakeven"
    PYRAMID = "pyramid"
    NOTE = "note"
    CANCEL = "cancel"

class TwitterStyleManager:
    """
    Manages which event types should be posted to Twitter.
    Configurable via environment variable or config.
    """

    # Default events to post (conservative - only major events)
    DEFAULT_EVENTS = {
        EventType.TRADE_SETUP,
        EventType.TARGET_HIT,
        EventType.STOPPED_OUT,
        EventType.BREAKEVEN,
    }

    # All possible events
    ALL_EVENTS = set(EventType)

    def __init__(self):
        self._event_cache = None
        self._cache_time = 0

    def should_post(self, event_type: str) -> bool:
        """
        Check if event type should be posted to Twitter.

        Args:
            event_type: String like 'trade_setup', 'position_update', etc.
        """
        # Parse event type
        try:
            event = EventType(event_type.lower())
        except ValueError:
            # Unknown event types default to False (safe)
            return False

        # Check environment override
        env_filter = os.getenv('TWITTER_EVENT_FILTER', '').lower()

        if env_filter == 'all':
            return True
        if env_filter == 'none':
            return False
        if env_filter:
            # Comma-separated list of events to INCLUDE
            allowed = {e.strip() for e in env_filter.split(',')}
            return event.value in allowed

        # Use default conservative set
        return event in self.DEFAULT_EVENTS

    def get_allowed_events(self) -> Set[str]:
        """Get current set of allowed event type strings."""
        env_filter = os.getenv('TWITTER_EVENT_FILTER', '').lower()

        if env_filter == 'all':
            return {e.value for e in self.ALL_EVENTS}
        if env_filter == 'none':
            return set()
        if env_filter:
            return {e.strip() for e in env_filter.split(',')}

        return {e.value for e in self.DEFAULT_EVENTS}

    def set_allowed_events(self, events: List[str]):
        """Set allowed events via environment (runtime only)."""
        os.environ['TWITTER_EVENT_FILTER'] = ','.join(events)

    def allow_all(self):
        """Allow all events."""
        os.environ['TWITTER_EVENT_FILTER'] = 'all'

    def allow_none(self):
        """Block all events."""
        os.environ['TWITTER_EVENT_FILTER'] = 'none'

    @classmethod
    def should_post_event(cls, event_type: str) -> bool:
        """Class method for quick check."""
        try:
            event = EventType(event_type.lower())
        except ValueError:
            return False

        env_filter = os.getenv('TWITTER_EVENT_FILTER', '').lower()

        if env_filter == 'all':
            return True
        if env_filter == 'none':
            return False
        if env_filter:
            allowed = {e.strip() for e in env_filter.split(',')}
            return event.value in allowed

        return event in cls.DEFAULT_EVENTS

# Singleton instance
_style_manager = None

def get_twitter_style_manager() -> TwitterStyleManager:
    global _style_manager
    if _style_manager is None:
        _style_manager = TwitterStyleManager()
    return _style_manager

def should_post_to_twitter(event_type: str) -> bool:
    """Quick check if event should be posted."""
    return TwitterStyleManager.should_post_event(event_type)
