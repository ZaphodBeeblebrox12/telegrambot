"""Config-driven Twitter Publisher"""
import os
from typing import Optional, Dict, Any
import tweepy

from config.config_loader import config


class TwitterPublisher:
    """Publishes messages to Twitter - Config-driven"""

    def __init__(self):
        self.cfg = config
        self.destinations = config.destinations
        self._clients: Dict[str, tweepy.Client] = {}
        self._apis: Dict[str, tweepy.API] = {}

    def _get_credentials(self, account_key: str) -> Dict[str, str]:
        """Get Twitter credentials for account"""
        prefix = account_key.upper()
        return {
            'bearer_token': os.getenv(f'{prefix}_BEARER_TOKEN'),
            'api_key': os.getenv(f'{prefix}_API_KEY'),
            'api_secret': os.getenv(f'{prefix}_API_SECRET'),
            'access_token': os.getenv(f'{prefix}_ACCESS_TOKEN'),
            'access_secret': os.getenv(f'{prefix}_ACCESS_SECRET'),
        }

    def _get_client(self, account_key: str) -> tweepy.Client:
        """Get or create Twitter client"""
        if account_key not in self._clients:
            creds = self._get_credentials(account_key)
            self._clients[account_key] = tweepy.Client(
                bearer_token=creds['bearer_token'],
                consumer_key=creds['api_key'],
                consumer_secret=creds['api_secret'],
                access_token=creds['access_token'],
                access_token_secret=creds['access_secret']
            )
        return self._clients[account_key]

    def _get_api(self, account_key: str) -> tweepy.API:
        """Get or create Twitter API (for media upload)"""
        if account_key not in self._apis:
            creds = self._get_credentials(account_key)
            auth = tweepy.OAuthHandler(creds['api_key'], creds['api_secret'])
            auth.set_access_token(creds['access_token'], creds['access_secret'])
            self._apis[account_key] = tweepy.API(auth)
        return self._apis[account_key]

    def get_destination_accounts(self, pair: str = 'pair1') -> List[Dict[str, Any]]:
        """Get Twitter accounts for destination pair"""
        accounts = []
        platform_cfg = self.cfg.platform_settings.get(pair, {})

        for dest_id, dest_cfg in self.destinations.items():
            if dest_cfg.platform == 'twitter':
                accounts.append({
                    'id': dest_id,
                    'account_id': dest_cfg.account_id,
                    'credentials_key': dest_cfg.credentials_key,
                    'display_name': dest_cfg.display_name
                })

        return accounts

    async def send_tweet(
        self,
        text: str,
        account_key: str,
        reply_to_tweet_id: Optional[str] = None,
        media_ids: Optional[List[str]] = None,
        **kwargs
    ) -> Dict[str, Any]:
        """Send tweet"""
        client = self._get_client(account_key)

        response = client.create_tweet(
            text=text,
            in_reply_to_tweet_id=reply_to_tweet_id,
            media_ids=media_ids
        )

        return {
            'tweet_id': str(response.data['id']),
            'text': text
        }

    async def upload_media(
        self,
        media_bytes: bytes,
        account_key: str,
        mime_type: str = 'image/jpeg'
    ) -> Optional[str]:
        """Upload media to Twitter"""
        try:
            api = self._get_api(account_key)
            # Save to temp file for upload
            import tempfile
            with tempfile.NamedTemporaryFile(delete=False, suffix='.jpg') as f:
                f.write(media_bytes)
                temp_path = f.name

            media = api.media_upload(temp_path)

            # Cleanup
            os.unlink(temp_path)

            return media.media_id_string
        except Exception as e:
            print(f"Media upload failed: {e}")
            return None

    def should_post_to_twitter(self, message_type: str, pair: str = 'pair1') -> bool:
        """Check if message type should post to Twitter"""
        msg_type_cfg = config.get_message_type(message_type)
        if not msg_type_cfg:
            return False

        platform_rules = msg_type_cfg.platform_rules
        return platform_rules.get('twitter', False)


# Singleton
_publisher: Optional[TwitterPublisher] = None

def get_twitter_publisher() -> TwitterPublisher:
    global _publisher
    if _publisher is None:
        _publisher = TwitterPublisher()
    return _publisher
