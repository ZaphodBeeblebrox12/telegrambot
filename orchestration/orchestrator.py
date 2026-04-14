"""Main Orchestrator - Config-driven pipeline with Transactional Outbox"""
from typing import Optional, Dict, Any, List
import asyncio

from config.config_loader import config
from core.models import OCRResult, ParsedCommand, Trade, MessageMapping
from core.services import get_trade_service
from core.repositories import RepositoryFactory
from core.outbox import get_outbox
from ocr.gemini_ocr import get_ocr_service
from orchestration.command_router import get_command_router
from orchestration.config_executor import get_executor
from orchestration.formatter import get_formatter
from messaging.message_mapping_service import get_mapping_service

class TradingBotOrchestrator:
    """Main orchestrator with Transactional Outbox"""

    def __init__(self):
        self.cfg = config
        self.ocr = get_ocr_service()
        self.router = get_command_router()
        self.executor = get_executor()
        self.formatter = get_formatter()
        self.trade_service = get_trade_service()
        self.mapping_service = get_mapping_service()
        self.outbox = get_outbox()
        self.db = RepositoryFactory.get_database()

    async def process_image(
        self,
        image_bytes: bytes,
        admin_channel_id: int,
        message_id: int
    ) -> Dict[str, Any]:
        """Process image with transactional outbox (FIX 3)"""
        result = {
            'success': False,
            'ocr_result': None,
            'trade': None,
            'outbox_ids': [],
            'errors': []
        }

        session = self.db.get_session()
        try:
            # 1. OCR Processing
            ocr_result = self.ocr.process_image(image_bytes)
            result['ocr_result'] = ocr_result

            if not ocr_result.is_valid:
                result['errors'].append("OCR did not find valid trade setup")
                session.close()
                return result

            # 2. Create Trade
            trade = self.trade_service.create_trade_from_ocr(ocr_result)
            if not trade:
                result['errors'].append("Failed to create trade")
                session.close()
                return result

            result['trade'] = trade

            # 3. Format & Queue to outbox IN SAME TRANSACTION (FIX 3)
            msg_type_cfg = config.get_message_type('trade_setup')
            if msg_type_cfg:
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

                if msg_type_cfg.get('platform_rules', {}).get('telegram'):
                    from publishers.telegram_publisher import get_telegram_publisher
                    tg_publisher = get_telegram_publisher()
                    for dest in tg_publisher.get_destination_channels():
                        outbox_id = self.outbox.enqueue_in_transaction(
                            session=session,
                            destination='telegram',
                            message_type='trade_setup',
                            payload={
                                'channel_id': dest['channel_id'],
                                'text': tg_text,
                                'photo': image_bytes
                            }
                        )
                        result['outbox_ids'].append({'platform': 'telegram', 'id': outbox_id})

            # 4. Create message mapping
            mapping = self.mapping_service.create_mapping(
                main_msg_id=message_id,
                tg_channel=admin_channel_id,
                trade_id=trade.trade_id,
                ocr_symbol=trade.symbol,
                asset_class=trade.asset_class,
                leverage_multiplier=trade.leverage_multiplier
            )

            result['mapping'] = mapping
            result['success'] = True

            # 5. COMMIT TRANSACTION (atomic: trade + outbox messages)
            session.commit()

            # 6. Process outbox
            await self.outbox.run_once()

        except Exception as e:
            session.rollback()
            result['errors'].append(str(e))
        finally:
            session.close()

        return result

    async def process_command(
        self,
        command_text: str,
        reply_to_message_id: Optional[int],
        admin_channel_id: int
    ) -> Dict[str, Any]:
        """Process command with transactional outbox"""
        result = {
            'success': False,
            'parsed': None,
            'execution': None,
            'formatted': {},
            'outbox_ids': [],
            'errors': []
        }

        session = self.db.get_session()
        try:
            parsed = self.router.parse_update_command(command_text)
            if not parsed:
                result['errors'].append("Could not parse command")
                session.close()
                return result

            result['parsed'] = parsed

            parent_mapping = None
            if reply_to_message_id:
                parent_mapping = self.mapping_service.get_mapping(reply_to_message_id)

            if not parent_mapping:
                result['errors'].append("No parent message found")
                session.close()
                return result

            trade = self.trade_service.get_trade(parent_mapping.trade_id)
            if not trade:
                result['errors'].append("Trade not found")
                session.close()
                return result

            execution_result = await self.executor.execute(trade, parsed)
            result['execution'] = execution_result

            if not execution_result.success:
                result['errors'].append(execution_result.error or "Execution failed")
                session.close()
                return result

            for platform in ['telegram']:
                formatted = self.formatter.format_message(
                    execution_result.message_type or 'position_update',
                    platform,
                    execution_result.variables,
                    execution_result.trade
                )
                result['formatted'][platform] = formatted

            tg_text = result['formatted'].get('telegram', '')
            outbox_id = self.outbox.enqueue_in_transaction(
                session=session,
                destination='telegram',
                message_type='position_update',
                payload={
                    'channel_id': admin_channel_id,
                    'text': tg_text,
                    'reply_to_message_id': reply_to_message_id
                }
            )
            result['outbox_ids'].append({'platform': 'telegram', 'id': outbox_id})

            result['success'] = True
            session.commit()
            await self.outbox.run_once()

        except Exception as e:
            session.rollback()
            result['errors'].append(str(e))
        finally:
            session.close()

        return result

    async def start_outbox_processor(self, interval: float = 5.0):
        await self.outbox.start_processor(interval)

    def stop_outbox_processor(self):
        self.outbox.stop_processor()

    def get_system_status(self) -> Dict[str, Any]:
        return {
            'config_version': config.system_config.get('version', 'unknown'),
            'handlers_registered': self.executor.list_handlers(),
            'trade_stats': self.trade_service.get_trade_statistics(),
            'mappings_count': len(self.mapping_service.get_all_mappings())
        }

_orchestrator = None

def get_orchestrator():
    global _orchestrator
    if _orchestrator is None:
        _orchestrator = TradingBotOrchestrator()
    return _orchestrator
