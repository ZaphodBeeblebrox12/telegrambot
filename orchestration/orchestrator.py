"""Main Orchestrator - Config-driven pipeline"""
from typing import Optional, Dict, Any, List
import asyncio

from config.config_loader import config
from core.models import OCRResult, ParsedCommand, Trade, MessageMapping
from core.services import get_trade_service
from core.repositories import RepositoryFactory
from ocr.gemini_ocr import get_ocr_service
from orchestration.command_router import get_command_router
from orchestration.config_executor import get_executor
from orchestration.formatter import get_formatter
from messaging.message_mapping_service import get_mapping_service
from publishers.telegram_publisher import get_telegram_publisher
from publishers.twitter_publisher import get_twitter_publisher


class TradingBotOrchestrator:
    """Main orchestrator - CONFIG → ROUTER → EXECUTOR → SERVICE → FORMATTER → MAPPING → PUBLISHER"""

    def __init__(self):
        self.cfg = config
        self.ocr = get_ocr_service()
        self.router = get_command_router()
        self.executor = get_executor()
        self.formatter = get_formatter()
        self.trade_service = get_trade_service()
        self.mapping_service = get_mapping_service()
        self.tg_publisher = get_telegram_publisher()
        self.tw_publisher = get_twitter_publisher()

    async def process_image(
        self,
        image_bytes: bytes,
        admin_channel_id: int,
        message_id: int
    ) -> Dict[str, Any]:
        """Process image through full pipeline"""
        result = {
            'success': False,
            'ocr_result': None,
            'trade': None,
            'mappings': [],
            'errors': []
        }

        try:
            # 1. OCR Processing
            ocr_result = self.ocr.process_image(image_bytes)
            result['ocr_result'] = ocr_result

            if not ocr_result.is_valid:
                result['errors'].append("OCR did not find valid trade setup")
                return result

            # 2. Create Trade
            trade = self.trade_service.create_trade_from_ocr(ocr_result)
            if not trade:
                result['errors'].append("Failed to create trade")
                return result

            result['trade'] = trade

            # 3. Format Messages
            msg_type_cfg = config.get_message_type('trade_setup')
            if msg_type_cfg:
                # Telegram format
                tg_text = self.formatter.format_message(
                    'trade_setup',
                    'telegram',
                    {
                        'symbol': trade.symbol,
                        'asset_class': trade.asset_class,
                        'side': trade.side,
                        'entry': trade.entry_price,
                        'target': trade.target,
                        'stop_loss': trade.stop_loss,
                        'leverage_multiplier': trade.leverage_multiplier
                    },
                    trade
                )

                # Twitter format
                tw_text = self.formatter.format_message(
                    'trade_setup',
                    'twitter',
                    {
                        'symbol': trade.symbol,
                        'asset_class': trade.asset_class,
                        'side': trade.side,
                        'entry': trade.entry_price,
                        'target': trade.target,
                        'stop_loss': trade.stop_loss,
                        'leverage_multiplier': trade.leverage_multiplier
                    },
                    trade
                )

                # 4. Publish to destinations
                # Telegram
                if msg_type_cfg.platform_rules.get('telegram'):
                    for dest in self.tg_publisher.get_destination_channels():
                        try:
                            tg_result = await self.tg_publisher.send_photo(
                                channel_id=dest['channel_id'],
                                photo=image_bytes,
                                caption=tg_text
                            )
                            result['mappings'].append({
                                'platform': 'telegram',
                                'channel_id': dest['channel_id'],
                                'message_id': tg_result['message_id']
                            })
                        except Exception as e:
                            result['errors'].append(f"Telegram publish failed: {e}")

                # Twitter
                if msg_type_cfg.platform_rules.get('twitter'):
                    for account in self.tw_publisher.get_destination_accounts():
                        try:
                            # Upload media
                            media_id = await self.tw_publisher.upload_media(
                                image_bytes,
                                account['credentials_key']
                            )
                            # Send tweet
                            tw_result = await self.tw_publisher.send_tweet(
                                text=tw_text,
                                account_key=account['credentials_key'],
                                media_ids=[media_id] if media_id else None
                            )
                            result['mappings'].append({
                                'platform': 'twitter',
                                'account': account['account_id'],
                                'tweet_id': tw_result['tweet_id']
                            })
                        except Exception as e:
                            result['errors'].append(f"Twitter publish failed: {e}")

            # 5. Create message mapping
            mapping = self.mapping_service.create_mapping(
                main_msg_id=message_id,
                tg_channel=admin_channel_id,
                trade_id=trade.trade_id,
                ocr_symbol=trade.symbol,
                asset_class=trade.asset_class,
                leverage_multiplier=trade.leverage_multiplier,
                gemini_result=ocr_result.to_dict() if hasattr(ocr_result, 'to_dict') else None
            )

            result['mapping'] = mapping
            result['success'] = True

        except Exception as e:
            result['errors'].append(str(e))

        return result

    async def process_command(
        self,
        command_text: str,
        reply_to_message_id: Optional[int],
        admin_channel_id: int
    ) -> Dict[str, Any]:
        """Process command through pipeline"""
        result = {
            'success': False,
            'parsed': None,
            'execution': None,
            'formatted': {},
            'published': [],
            'errors': []
        }

        try:
            # 1. Parse command
            parsed = self.router.parse_update_command(command_text)
            if not parsed:
                result['errors'].append("Could not parse command")
                return result

            result['parsed'] = parsed

            # 2. Find parent trade
            parent_mapping = None
            if reply_to_message_id:
                parent_mapping = self.mapping_service.get_mapping(reply_to_message_id)

            if not parent_mapping:
                result['errors'].append("No parent message found")
                return result

            trade = self.trade_service.get_trade(parent_mapping.trade_id)
            if not trade:
                result['errors'].append("Trade not found")
                return result

            # 3. Execute command
            execution_result = self.executor.execute(trade, parsed)
            result['execution'] = execution_result

            if not execution_result.success:
                result['errors'].append(execution_result.error or "Execution failed")
                return result

            # 4. Format for platforms
            platforms = ['telegram', 'twitter']
            for platform in platforms:
                formatted = self.formatter.format_message(
                    execution_result.message_type or 'position_update',
                    platform,
                    execution_result.variables,
                    execution_result.trade
                )
                result['formatted'][platform] = formatted

            # 5. Publish updates
            # Reply to parent in admin channel
            if parent_mapping:
                try:
                    tg_text = result['formatted'].get('telegram', '')
                    tg_result = await self.tg_publisher.send_message(
                        channel_id=admin_channel_id,
                        text=tg_text,
                        reply_to_message_id=reply_to_message_id
                    )
                    result['published'].append({
                        'platform': 'telegram',
                        'channel': 'admin',
                        'message_id': tg_result['message_id']
                    })
                except Exception as e:
                    result['errors'].append(f"Admin reply failed: {e}")

            result['success'] = True

        except Exception as e:
            result['errors'].append(str(e))

        return result

    def get_system_status(self) -> Dict[str, Any]:
        """Get system status"""
        return {
            'config_version': config.system.version,
            'handlers_registered': self.executor.list_handlers(),
            'trade_stats': self.trade_service.get_trade_statistics(),
            'mappings_count': len(self.mapping_service.get_all_mappings())
        }


# Singleton
_orchestrator: Optional[TradingBotOrchestrator] = None

def get_orchestrator() -> TradingBotOrchestrator:
    global _orchestrator
    if _orchestrator is None:
        _orchestrator = TradingBotOrchestrator()
    return _orchestrator
