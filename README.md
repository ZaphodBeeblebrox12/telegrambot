# Trading Bot - Production Ready (Option A)

Production-ready trading bot with SQL-based storage and FIFO partial close logic.

## Critical Fixes Applied

### 🔴 CRITICAL FIX 1: SQL-Based Storage
- Replaced JSON repositories with SQLAlchemy-based repositories
- `RepositoryFactory.get_trade_repository()` returns `SQLTradeRepository`
- `RepositoryFactory.get_mapping_repository()` returns `SQLMessageMappingRepository`
- SQL is now SINGLE SOURCE OF TRUTH

### 🔴 CRITICAL FIX 2: FIFO Partial Close Logic
- Uses `entry.closed_size` as source of truth
- Multiple partial closes accumulate correctly via `closed_size +=`
- Weighted average recalculated from remaining position

### 🔴 CRITICAL FIX 3: SQL-Based Outbox
- Moved outbox storage from JSON file to SQL table
- Transactional consistency with trade updates

### 🟡 FIX 4: Removed Dead OCR Code
- Removed dummy OCR service
- Only Gemini OCR is used

### 🟡 FIX 5: Consolidated Trade Logic
- All trade calculations in `TradeService`
- `SnapshotBuilder` is helper-only

### 🟡 FIX 6: Message Mapping Consistency
- SQL-based mapping persistence
- Reply chain works reliably

## Architecture Flow

```
CONFIG → ROUTER → EXECUTOR → SERVICE → SQL → FORMATTER → MAPPING → OUTBOX → PUBLISHER
```

## Setup

```bash
pip install -r requirements.txt
cp .env.example .env
python main.py
```

## Database Schema

- **trades**: Core trade data
- **trade_entries**: Entry prices with closed_size (FIFO source of truth)
- **message_mappings**: Message relationships
- **outbox_messages**: Pending messages with retry logic
