"""Twitter Publisher"""
from typing import List, Dict, Any, Optional
from config.config_loader import config

class TwitterPublisher:
    """Publishes messages to Twitter"""

    def get_destination_accounts(self) -> List[Dict[str, Any]]:
        accounts = []
        for dest_id, dest in config.destinations.items():
            if dest.get('platform') == 'twitter':
                accounts.append({
                    'dest_id': dest_id,
                    'account_id': dest.get('account_id'),
                    'credentials_key': dest.get('credentials_key')
                })
        return accounts

    async def send_tweet(
        self,
        text: str,
        account_key: str,
        reply_to_tweet_id: Optional[str] = None,
        media_ids: Optional[List[str]] = None
    ) -> Optional[str]:
        print(f"[TWITTER] From {account_key}: {text[:50]}...")
        return "tweet_12345"

    async def upload_media(self, media_bytes: bytes, account_key: str) -> Optional[str]:
        print(f"[TWITTER] Media upload for {account_key}")
        return "media_12345"

_tw_publisher = None

def get_twitter_publisher():
    global _tw_publisher
    if _tw_publisher is None:
        _tw_publisher = TwitterPublisher()
    return _tw_publisher
