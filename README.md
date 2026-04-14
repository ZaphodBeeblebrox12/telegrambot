# Trading Bot Pipeline

Complete trading signal processing pipeline.

## Pipeline Flow

```
IMAGE / COMMAND → OCR → CONFIG → SERVICE → DB → FORMAT → TELEGRAM
```

## Quick Start

1. **Install Dependencies**
   ```bash
   pip install -r requirements.txt
   ```

2. **Configure Environment**
   ```bash
   cp .env.example .env
   # Edit .env with your settings
   ```

3. **Run the Bot**
   ```bash
   python main.py
   ```

## Architecture

### Core Layer (`core/`)
- **models.py**: Domain models (Trade, TradeEntry, TradeEvent)
- **db.py**: SQLAlchemy database models
- **services.py**: TradeService business logic
- **fifo.py**: FIFO position closing engine
- **snapshot.py**: Snapshot calculations
- **repositories.py**: Data access layer

### Orchestration Layer (`orchestration/`)
- **orchestrator.py**: Main TradingPipeline
- **command_router.py**: Command parsing
- **config_executor.py**: Command execution
- **formatter.py**: Message formatting

### Messaging Layer (`messaging/`)
- **message_mapping_service.py**: Trade-to-message mapping

### OCR Layer (`ocr/`)
- **ocr_service.py**: Image analysis (placeholder)

### Bot Layer (`bot/`)
- **telegram_bot.py**: Telegram integration

## Database

Supports MySQL and PostgreSQL. Tables:
- `trades`: Trade headers
- `trade_entries`: Entry records with FIFO tracking
- `trade_events`: Event log with idempotency
- `trade_snapshots`: Computed state
- `message_mappings`: Platform message linking

## Commands

Reply to trade messages with:
- `trail <price>` - Update stop loss
- `close <price>` - Close full position
- `partial <price>` - Close 25%
- `closehalf <price>` - Close 50%

## Critical Design Features

1. **No UUID**: Uses base36 timestamp IDs
2. **Deterministic Idempotency**: Prevents duplicate processing
3. **DB-First Snapshots**: Always load from DB before update
4. **O(n) FIFO**: Linear time partial closes
5. **Threaded Replies**: Message mapping for conversation threading
