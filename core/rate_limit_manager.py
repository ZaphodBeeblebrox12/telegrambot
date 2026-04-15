"""Rate Limit Manager - Production Safety Layer (Fixed Drop-In Version)"""
import time
import hashlib
import json
import os
from typing import Dict, Optional, Set, Tuple
from pathlib import Path

class RateLimitManager:
    """
    Production rate limiting for trading bot.
    Prevents API spam and duplicate updates WITHOUT blocking legit sequences.

    KEY FIX: Uses (trade_id, command_type) as key, not just trade_id.
    This allows: PYRAMID then TRAIL (different types, same trade)
    This blocks: TRAIL then TRAIL (same type, same trade within window)
    """

    def __init__(self, persistence_path: Optional[str] = None):
        # Per-trade-command cooldown tracking: (trade_id, command_type) -> last_update_timestamp
        self._trade_cooldowns: Dict[Tuple[str, str], float] = {}

        # Global message tracking for API rate limiting
        self._global_message_times: list = []
        self._global_limit = 30  # messages per minute
        self._global_window = 60  # seconds

        # Deduplication tracking: hash -> timestamp
        self._recent_commands: Dict[str, float] = {}
        self._dedup_window = 5  # seconds

        # Per-trade-command minimum interval (seconds)
        self._min_interval = 2.0

        # Track active trade updates to prevent concurrent modifications
        self._active_updates: Set[str] = set()

        # Persistence
        self._persistence_path = persistence_path or ".rate_limit_state.json"
        self._load_state()

    def _load_state(self):
        """Load rate limit state from disk (optional persistence)."""
        try:
            if os.path.exists(self._persistence_path):
                with open(self._persistence_path, 'r') as f:
                    data = json.load(f)
                    # Only load recent entries (< 1 hour old)
                    cutoff = time.time() - 3600
                    self._trade_cooldowns = {
                        tuple(k): v for k, v in data.get('cooldowns', {}).items()
                        if v > cutoff
                    }
        except Exception:
            pass  # Silent fail - stateless is acceptable

    def _save_state(self):
        """Save rate limit state to disk."""
        try:
            data = {
                'cooldowns': {list(k): v for k, v in self._trade_cooldowns.items()},
                'saved_at': time.time()
            }
            with open(self._persistence_path, 'w') as f:
                json.dump(data, f)
        except Exception:
            pass  # Silent fail

    def _extract_command_type(self, command_text: str) -> str:
        """Extract command type from command text (first word)."""
        parts = command_text.strip().lower().split()
        return parts[0] if parts else "unknown"

    def allow_trade_update(self, trade_id: str, command_text: str) -> bool:
        """
        Check if update is allowed for trade + command type.

        FIXED: Uses (trade_id, command_type) as key.
        This allows rapid updates of DIFFERENT types to same trade.
        """
        now = time.time()
        cmd_type = self._extract_command_type(command_text)
        key = (trade_id, cmd_type)

        # Check if already processing this trade (any command type)
        if trade_id in self._active_updates:
            return False

        # Check per-trade-command cooldown
        last_update = self._trade_cooldowns.get(key)
        if last_update:
            elapsed = now - last_update
            if elapsed < self._min_interval:
                return False

        return True

    def record_trade_update(self, trade_id: str, command_text: str):
        """Record that trade+command was updated (call after successful update)."""
        cmd_type = self._extract_command_type(command_text)
        key = (trade_id, cmd_type)
        self._trade_cooldowns[key] = time.time()
        self._save_state()
        self._cleanup_old_cooldowns()

    def get_cooldown_remaining(self, trade_id: str, command_text: str) -> float:
        """Get remaining cooldown seconds for trade+command."""
        cmd_type = self._extract_command_type(command_text)
        key = (trade_id, cmd_type)
        last_update = self._trade_cooldowns.get(key)
        if not last_update:
            return 0.0

        elapsed = time.time() - last_update
        remaining = self._min_interval - elapsed
        return max(0.0, remaining)

    def allow_global_send(self) -> bool:
        """Check if global message rate limit allows sending."""
        now = time.time()

        # Remove old entries outside window
        cutoff = now - self._global_window
        self._global_message_times = [t for t in self._global_message_times if t > cutoff]

        return len(self._global_message_times) < self._global_limit

    def record_global_send(self):
        """Record that a message was sent globally."""
        self._global_message_times.append(time.time())

    def is_duplicate(self, command_text: str, trade_id: str) -> bool:
        """
        Check if command is an EXACT duplicate (same text to same trade within window).
        """
        # Normalize: lowercase, strip whitespace
        normalized = command_text.lower().strip()
        content = f"{trade_id}:{normalized}"
        cmd_hash = hashlib.md5(content.encode()).hexdigest()

        now = time.time()

        # Check if seen recently
        if cmd_hash in self._recent_commands:
            last_seen = self._recent_commands[cmd_hash]
            if now - last_seen < self._dedup_window:
                return True

        # Record this command
        self._recent_commands[cmd_hash] = now
        self._cleanup_old_dedups()

        return False

    def acquire_update_lock(self, trade_id: str) -> bool:
        """Acquire lock for trade update (prevent concurrent updates to same trade)."""
        if trade_id in self._active_updates:
            return False
        self._active_updates.add(trade_id)
        return True

    def release_update_lock(self, trade_id: str):
        """Release lock for trade update."""
        self._active_updates.discard(trade_id)

    def _cleanup_old_cooldowns(self):
        """Remove cooldown entries older than 1 hour."""
        cutoff = time.time() - 3600
        self._trade_cooldowns = {
            k: v for k, v in self._trade_cooldowns.items() 
            if v > cutoff
        }

    def _cleanup_old_dedups(self):
        """Remove dedup entries older than window."""
        cutoff = time.time() - self._dedup_window
        self._recent_commands = {
            k: v for k, v in self._recent_commands.items() 
            if v > cutoff
        }

# Singleton instance
_rate_limit_manager = None

def get_rate_limit_manager() -> RateLimitManager:
    global _rate_limit_manager
    if _rate_limit_manager is None:
        _rate_limit_manager = RateLimitManager()
    return _rate_limit_manager
