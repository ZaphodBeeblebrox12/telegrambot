"""Main Orchestrator - Config-driven pipeline with Transactional Outbox
FIXED: Preserves original class name (TradingBotOrchestrator) and import paths.
FIXED: Transaction safety - ONLY ONE session.commit() at end of orchestrator.
FIXED: Idempotency with session.begin_nested() for SAVEPOINT.
FIXED: Removed run_once() from request path - outbox processing happens separately.
ADDED: Photo forwarding, admin caption editing support, target message tracking via trade_id, reply threading.
ADDED: Better debug logging for command parsing and execution.
"""
from typing import Optional, Dict, Any, List
import asyncio
import logging

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

logger = logging.getLogger(__name__)

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
        logger.info("TradingBotOrchestrator initialized")

    async def process_image(
        self,
        image_bytes: bytes,
        admin_channel_id: int,
        message_id: int,
        photo_path: str = None
    ) -> Dict[str, Any]:
        """Process image with transactional outbox.

        ADDED: photo_path param for image forwarding to target channels.
        ADDED: Returns formatted_text for admin caption editing.
        ADDED: Passes trade_id to outbox for target message tracking.

        CRITICAL FIXES:
        - Uses session.begin_nested() for idempotency
        - Only ONE session.commit() at the end
        - NO commit inside SAVEPOINT block
        - mapping + trade update + outbox enqueue in same transaction
        """
        result = {
            'success': False,
            'ocr_result': None,
            'trade': None,
            'outbox_ids': [],
            'errors': [],
            'formatted_text': None,
            'mapping_id': None
        }

        session = self.db.get_session()
        try:
            # 1. OCR Processing (async-safe, outside transaction)
            ocr_result = await self.ocr.process_image_async(image_bytes)
            result['ocr_result'] = ocr_result

            if not ocr_result.is_valid:
                result['errors'].append("OCR did not find valid trade setup")
                session.close()
                return result

            # 2. Create Trade (inside transaction)
            trade = self.trade_service.create_trade_from_ocr(ocr_result)
            if not trade:
                result['errors'].append("Failed to create trade")
                session.close()
                return result

            result['trade'] = trade
            logger.info(f"Created trade: {trade.trade_id} for {trade.symbol}")

            # 3. Format message
            msg_type_cfg = config.get_message_type('trade_setup')
            tg_text = None
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
            result['formatted_text'] = tg_text

            # 4. Create message mapping WITH IDEMPOTENCY (SAVEPOINT)
            mapping = None
            try:
                # Use nested transaction (SAVEPOINT) for idempotency
                nested = session.begin_nested()
                try:
                    mapping = self.mapping_service.create_mapping(
                        main_msg_id=message_id,
                        tg_channel=admin_channel_id,
                        trade_id=trade.trade_id,
                        ocr_symbol=trade.symbol,
                        asset_class=trade.asset_class,
                        leverage_multiplier=trade.leverage_multiplier
                    )
                    result['mapping'] = mapping
                    nested.commit()
                except Exception as e:
                    nested.rollback()
                    # Check if mapping already exists (idempotency)
                    existing = self.mapping_service.get_mapping(message_id)
                    if existing:
                        logger.info(f"Mapping already exists for message {message_id}, continuing...")
                        mapping = existing
                    else:
                        raise
            except Exception as e:
                logger.error(f"Message mapping creation failed: {e}")
                result['errors'].append(f"Mapping failed: {e}")

            # 5. Queue to outbox IN SAME TRANSACTION (NO commit inside)
            if msg_type_cfg and msg_type_cfg.get('platform_rules', {}).get('telegram'):
                from publishers.telegram_publisher import get_telegram_publisher
                tg_publisher = get_telegram_publisher()
                for dest in tg_publisher.get_destination_channels():
                    payload = {
                        'channel_id': dest['channel_id'],
                        'text': tg_text,
                        'trade_id': trade.trade_id  # For target message tracking
                    }
                    if photo_path:
                        payload['photo_path'] = photo_path

                    outbox_id = self.outbox.enqueue_in_transaction(
                        session=session,
                        destination='telegram',
                        message_type='trade_setup',
                        payload=payload
                    )
                    if outbox_id:
                        result['outbox_ids'].append({'platform': 'telegram', 'id': outbox_id})

            result['success'] = True

            # 6. COMMIT TRANSACTION (atomic: trade + outbox messages)
            # This is the ONLY commit in this method
            session.commit()
            logger.info(f"Image processing complete for trade {trade.trade_id}")

            # 7. CRITICAL FIX: DO NOT call run_once() here
            # Outbox processing happens separately via start_processor()

        except Exception as e:
            session.rollback()
            result['errors'].append(str(e))
            logger.error(f"Error in process_image: {e}", exc_info=True)
        finally:
            session.close()

        return result

    async def process_command(
        self,
        command_text: str,
        reply_to_message_id: Optional[int],
        admin_channel_id: int,
        photo_path: str = None,
        is_image_update: bool = False
    ) -> Dict[str, Any]:
        """Process command with transactional outbox.

        ADDED: photo_path for image+command forwarding.
        ADDED: is_image_update to skip admin text when caption will be edited.
        ADDED: Target channel sends with reply threading via trade_id.
        ADDED: Debug logging for command parsing and execution.

        CRITICAL FIXES:
        - Uses session.begin_nested() for idempotency
        - Only ONE session.commit() at the end
        - NO commit inside SAVEPOINT block
        - mapping + trade update + outbox enqueue in same transaction
        """
        logger.info(f"=== PROCESS COMMAND START ===")
        logger.info(f"Command text: '{command_text}'")
        logger.info(f"Reply to message ID: {reply_to_message_id}")

        result = {
            'success': False,
            'parsed': None,
            'execution': None,
            'formatted': {},
            'formatted_text': None,
            'outbox_ids': [],
            'errors': []
        }

        session = self.db.get_session()
        try:
            # 1. Parse command
            parsed = self.router.parse_update_command(command_text)
            if not parsed:
                result['errors'].append("Could not parse command")
                logger.warning(f"Command parsing failed for: '{command_text}'")
                session.close()
                return result

            result['parsed'] = parsed
            logger.info(f"Parsed command: subcommand={parsed.subcommand}, price={parsed.price}, percentage={parsed.percentage}")

            # 2. Resolve trade from reply
            parent_mapping = None
            if reply_to_message_id:
                parent_mapping = self.mapping_service.get_mapping(reply_to_message_id)
                logger.info(f"Parent mapping for reply_to {reply_to_message_id}: {parent_mapping}")

            if not parent_mapping:
                result['errors'].append("No parent message found - must reply to trade message")
                logger.warning(f"No parent mapping found for reply_to {reply_to_message_id}")
                session.close()
                return result

            trade = self.trade_service.get_trade(parent_mapping.trade_id)
            if not trade:
                result['errors'].append("Trade not found")
                logger.error(f"Trade not found: {parent_mapping.trade_id}")
                session.close()
                return result

            logger.info(f"Resolved trade: {trade.trade_id} ({trade.symbol})")

            # 3. Execute command WITH IDEMPOTENCY (SAVEPOINT)
            nested = session.begin_nested()
            try:
                execution_result = await self.executor.execute(trade, parsed)
                nested.commit()
                logger.info(f"Execution result: success={execution_result.success}, message_type={execution_result.message_type}")
            except Exception as e:
                nested.rollback()
                logger.error(f"Command execution failed: {e}", exc_info=True)
                result['errors'].append(f"Execution failed: {e}")
                session.close()
                return result

            result['execution'] = execution_result

            if not execution_result.success:
                result['errors'].append(execution_result.error or "Execution failed")
                logger.warning(f"Execution failed: {execution_result.error}")
                session.close()
                return result

            # 4. Format for each platform
            for platform in ['telegram']:
                formatted = self.formatter.format_message(
                    execution_result.message_type or 'position_update',
                    platform,
                    execution_result.variables,
                    execution_result.trade
                )
                result['formatted'][platform] = formatted
                logger.debug(f"Formatted for {platform}: {formatted[:100]}...")

            tg_text = result['formatted'].get('telegram', '')
            result['formatted_text'] = tg_text

            # 5. Send to admin channel via outbox (SKIP for image updates — caption edit handles it)
            if not is_image_update:
                outbox_id = self.outbox.enqueue_in_transaction(
                    session=session,
                    destination='telegram',
                    message_type=execution_result.message_type or 'position_update',
                    payload={
                        'channel_id': admin_channel_id,
                        'text': tg_text,
                        'reply_to_message_id': reply_to_message_id,
                        'trade_id': trade.trade_id
                    }
                )
                if outbox_id:
                    result['outbox_ids'].append({'platform': 'telegram', 'id': outbox_id})
                    logger.info(f"Enqueued admin message: {outbox_id}")

            # 6. Send to telegram target channels with reply threading
            from publishers.telegram_publisher import get_telegram_publisher
            tg_publisher = get_telegram_publisher()
            for dest in tg_publisher.get_destination_channels():
                # Find last message in this channel for this trade
                last_msg_id = self.mapping_service.get_last_target_message(
                    trade.trade_id, dest['channel_id']
                )
                logger.debug(f"Last target message for {trade.trade_id} in {dest['channel_id']}: {last_msg_id}")

                payload = {
                    'channel_id': dest['channel_id'],
                    'text': tg_text,
                    'reply_to_message_id': last_msg_id,
                    'trade_id': trade.trade_id
                }
                if photo_path:
                    payload['photo_path'] = photo_path

                outbox_id = self.outbox.enqueue_in_transaction(
                    session=session,
                    destination='telegram',
                    message_type=execution_result.message_type or 'position_update',
                    payload=payload
                )
                if outbox_id:
                    result['outbox_ids'].append({'platform': 'telegram_target', 'id': outbox_id})
                    logger.info(f"Enqueued target channel message: {outbox_id}")

            result['success'] = True

            # 7. COMMIT TRANSACTION (atomic: trade update + outbox messages)
            session.commit()
            logger.info(f"=== PROCESS COMMAND COMPLETE ===")

        except Exception as e:
            session.rollback()
            result['errors'].append(str(e))
            logger.error(f"Error in process_command: {e}", exc_info=True)
        finally:
            session.close()

        return result

    async def start_outbox_processor(self, interval: float = 5.0):
        """Start the outbox processor in background."""
        await self.outbox.start_processor(interval)

    def stop_outbox_processor(self):
        """Stop the outbox processor."""
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
