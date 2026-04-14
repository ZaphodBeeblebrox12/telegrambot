"""Main Orchestrator - Config-driven pipeline with Outbox Pattern"""
from typing import Optional, Dict, Any, List
import asyncio

from config.config_loader import config
from core.models import OCRResult, ParsedCommand, Trade, MessageMapping
from core.services import get_trade_service
from core.repositories import RepositoryFactory
from core.outbox import get_outbox, OutboxManager
from ocr.gemini_ocr import get_ocr_service
from orchestration.command_router import get_command_router
from orchestration.config_executor import get_executor
from orchestration.formatter import get_formatter
from messaging.message_mapping_service import get_mapping_service
from publishers.telegram_publisher import get_telegram_publisher
from publishers.twitter_publisher import get_twitter_publisher


class TradingBotOrchestrator:
    """Main orchestrator with Outbox Pattern for reliability"""

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
        self.outbox = get_outbox()

        # Register outbox handlers
        self._setup_outbox_handlers()

    def _setup_outbox_handlers(self):
        """Register destination handlers with outbox"""
        self.outbox.register_handler('telegram', self._handle_telegram_outbox)
        self.outbox.register_handler('twitter', self._handle_twitter_outbox)

    async def _handle_telegram_outbox(self, payload: Dict[str, Any]):
        """Handle Telegram messages from outbox"""
        channel_id = payload.get('channel_id')
        text = payload.get('text')
        photo = payload.get('photo')
        reply_to = payload.get('reply_to_message_id')

        if photo:
            await self.tg_publisher.send_photo(
                channel_id=channel_id,
                photo=photo,
                caption=text,
                reply_to_message_id=reply_to
            )
        else:
            await self.tg_publisher.send_message(
                channel_id=channel_id,
                text=text,
                reply_to_message_id=reply_to
            )

    async def _handle_twitter_outbox(self, payload: Dict[str, Any]):
        """Handle Twitter messages from outbox"""
        account_key = payload.get('account_key')
        text = payload.get('text')
        media_bytes = payload.get('media_bytes')
        reply_to = payload.get('reply_to_tweet_id')

        media_ids = None
        if media_bytes:
            media_id = await self.tw_publisher.upload_media(
                media_bytes, account_key
            )
            if media_id:
                media_ids = [media_id]

        await self.tw_publisher.send_tweet(
            text=text,
            account_key=account_key,
            reply_to_tweet_id=reply_to,
            media_ids=media_ids
        )

    async def process_image(
        self,
        image_bytes: bytes,
        admin_channel_id: int,
        message_id: int
    ) -> Dict[str, Any]:
        """Process image through full pipeline with outbox"""
        result = {
            'success': False,
            'ocr_result': None,
            'trade': None,
            'outbox_ids': [],
            'errors': []
        }

        try:
            # 1. OCR Processing
            ocr_result = self.ocr.process_image(image_bytes)
            result['ocr_result'] = ocr_result

            if not ocr_result.is_valid:
                result['errors'].append("OCR did not find valid trade setup")
                return result

            # 2. Create Trade (with deterministic ID)
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
                    'trade_setup', 'telegram',
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
                    'trade_setup', 'twitter',
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

                # 4. Queue to outbox (reliable async)
                if msg_type_cfg.platform_rules.get('telegram'):
                    for dest in self.tg_publisher.get_destination_channels():
                        outbox_id = await self.outbox.enqueue(
                            destination='telegram',
                            message_type='trade_setup',
                            payload={
                                'channel_id': dest['channel_id'],
                                'text': tg_text,
                                'photo': image_bytes
                            }
                        )
                        result['outbox_ids'].append({
                            'platform': 'telegram',
                            'id': outbox_id
                        })

                if msg_type_cfg.platform_rules.get('twitter'):
                    for account in self.tw_publisher.get_destination_accounts():
                        outbox_id = await self.outbox.enqueue(
                            destination='twitter',
                            message_type='trade_setup',
                            payload={
                                'account_key': account['credentials_key'],
                                'text': tw_text,
                                'media_bytes': image_bytes
                            }
                        )
                        result['outbox_ids'].append({
                            'platform': 'twitter',
                            'id': outbox_id
                        })

            # 5. Create message mapping
            mapping = self.mapping_service.create_mapping(
                main_msg_id=message_id,
                tg_channel=admin_channel_id,
                trade_id=trade.trade_id,
                ocr_symbol=trade.symbol,
                asset_class=trade.asset_class,
                leverage_multiplier=trade.leverage_multiplier,
                gemini_result={
                    'symbol': ocr_result.symbol,
                    'asset_class': ocr_result.asset_class,
                    'side': ocr_result.side,
                    'entry': ocr_result.entry,
                    'target': ocr_result.target,
                    'stop_loss': ocr_result.stop_loss
                }
            )

            result['mapping'] = mapping
            result['success'] = True

            # 6. Process outbox immediately (or let background task handle)
            await self.outbox.run_once()

        except Exception as e:
            result['errors'].append(str(e))

        return result

    async def process_command(
        self,
        command_text: str,
        reply_to_message_id: Optional[int],
        admin_channel_id: int
    ) -> Dict[str, Any]:
        """Process command through pipeline with outbox"""
        result = {
            'success': False,
            'parsed': None,
            'execution': None,
            'formatted': {},
            'outbox_ids': [],
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

            # 3. Execute command (DYNAMIC via reflection)
            execution_result = await self.executor.execute(trade, parsed)
            result['execution'] = execution_result

            if not execution_result.success:
                result['errors'].append(execution_result.error or "Execution failed")
                return result

            # 4. Format for platforms
            for platform in ['telegram', 'twitter']:
                formatted = self.formatter.format_message(
                    execution_result.message_type or 'position_update',
                    platform,
                    execution_result.variables,
                    execution_result.trade
                )
                result['formatted'][platform] = formatted

            # 5. Queue to outbox
            if parent_mapping:
                tg_text = result['formatted'].get('telegram', '')
                outbox_id = await self.outbox.enqueue(
                    destination='telegram',
                    message_type='position_update',
                    payload={
                        'channel_id': admin_channel_id,
                        'text': tg_text,
                        'reply_to_message_id': reply_to_message_id
                    }
                )
                result['outbox_ids'].append({
                    'platform': 'telegram',
                    'id': outbox_id
                })

            result['success'] = True

            # 6. Process outbox
            await self.outbox.run_once()

        except Exception as e:
            result['errors'].append(str(e))

        return result

    async def start_outbox_processor(self, interval: float = 5.0):
        """Start background outbox processor"""
        await self.outbox.start_processor(interval)

    def stop_outbox_processor(self):
        """Stop background outbox processor"""
        self.outbox.stop_processor()

    def get_system_status(self) -> Dict[str, Any]:
        """Get system status"""
        return {
            'config_version': config.system.version,
            'handlers_registered': self.executor.list_handlers(),
            'trade_stats': self.trade_service.get_trade_statistics(),
            'mappings_count': len(self.mapping_service.get_all_mappings()),
            'outbox_pending': len(self.outbox.store.get_pending())
        }


# Singleton
_orchestrator: Optional[TradingBotOrchestrator] = None

def get_orchestrator() -> TradingBotOrchestrator:
    global _orchestrator
    if _orchestrator is None:
        _orchestrator = TradingBotOrchestrator()
    return _orchestrator
