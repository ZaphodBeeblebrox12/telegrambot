-- Production Trading Bot Schema
-- Compatible with MySQL 8.0+ and PostgreSQL 14+

-- For MySQL: Use this file as-is
-- For PostgreSQL: Replace AUTO_INCREMENT with BIGSERIAL

-- Drop tables if they exist (clean install)
-- DROP TABLE IF EXISTS idempotency_keys;
-- DROP TABLE IF EXISTS outbox_messages;
-- DROP TABLE IF EXISTS message_mappings;
-- DROP TABLE IF EXISTS trade_events;
-- DROP TABLE IF EXISTS trade_snapshots;
-- DROP TABLE IF EXISTS trade_entries;
-- DROP TABLE IF EXISTS trades;

-- Core trades table
CREATE TABLE IF NOT EXISTS trades (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    -- id BIGSERIAL PRIMARY KEY,  -- Use this for PostgreSQL
    trade_id VARCHAR(20) UNIQUE NOT NULL,
    symbol VARCHAR(20) NOT NULL,
    side VARCHAR(10) NOT NULL CHECK (side IN ('LONG', 'SHORT')),
    asset_class VARCHAR(20) NOT NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'OPEN',
    target DECIMAL(20, 8),
    stop_loss DECIMAL(20, 8),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_trade_id (trade_id),
    INDEX idx_symbol_status (symbol, status),
    INDEX idx_status (status)
);

-- Trade entries for FIFO (pyramiding support)
CREATE TABLE IF NOT EXISTS trade_entries (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    -- id BIGSERIAL PRIMARY KEY,  -- Use this for PostgreSQL
    trade_id BIGINT NOT NULL,
    entry_price DECIMAL(20, 8) NOT NULL,
    size DECIMAL(20, 8) NOT NULL,
    closed_size DECIMAL(20, 8) DEFAULT 0.0,
    entry_type VARCHAR(20) NOT NULL CHECK (entry_type IN ('INITIAL', 'PYRAMID')),
    sequence INT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (trade_id) REFERENCES trades(id) ON DELETE CASCADE,
    INDEX idx_trade_sequence (trade_id, sequence)
);

-- Event log for audit and idempotency
CREATE TABLE IF NOT EXISTS trade_events (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    -- id BIGSERIAL PRIMARY KEY,  -- Use this for PostgreSQL
    trade_id BIGINT NOT NULL,
    event_type VARCHAR(50) NOT NULL,
    payload JSON,
    idempotency_key VARCHAR(255) UNIQUE NOT NULL,
    processed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (trade_id) REFERENCES trades(id) ON DELETE CASCADE,
    INDEX idx_idempotency (idempotency_key),
    INDEX idx_trade_events (trade_id, event_type)
);

-- Trade snapshots (computed state)
CREATE TABLE IF NOT EXISTS trade_snapshots (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    -- id BIGSERIAL PRIMARY KEY,  -- Use this for PostgreSQL
    trade_id BIGINT UNIQUE NOT NULL,
    weighted_avg_entry DECIMAL(20, 8) NOT NULL,
    total_size DECIMAL(20, 8) NOT NULL,
    remaining_size DECIMAL(20, 8) NOT NULL,
    current_stop DECIMAL(20, 8),
    current_target DECIMAL(20, 8),
    locked_profit DECIMAL(20, 8) DEFAULT 0.0,
    total_booked_pnl DECIMAL(20, 8) DEFAULT 0.0,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    -- updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,  -- Use this for PostgreSQL
    FOREIGN KEY (trade_id) REFERENCES trades(id) ON DELETE CASCADE
);

-- Message mappings (reply threading)
CREATE TABLE IF NOT EXISTS message_mappings (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    -- id BIGSERIAL PRIMARY KEY,  -- Use this for PostgreSQL
    trade_id BIGINT NOT NULL,
    platform VARCHAR(50) NOT NULL,
    message_id VARCHAR(100) NOT NULL,
    channel_id VARCHAR(100),
    message_type VARCHAR(50) NOT NULL,
    parent_tg_msg_id VARCHAR(100),
    parent_main_msg_id VARCHAR(100),
    reply_to_message_id VARCHAR(100),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (trade_id) REFERENCES trades(id) ON DELETE CASCADE,
    UNIQUE KEY unique_platform_msg (platform, message_id),
    INDEX idx_trade_platform (trade_id, platform)
);

-- Outbox pattern for reliable messaging
CREATE TABLE IF NOT EXISTS outbox_messages (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    -- id BIGSERIAL PRIMARY KEY,  -- Use this for PostgreSQL
    message_id VARCHAR(50) UNIQUE NOT NULL,
    destination VARCHAR(50) NOT NULL,
    channel_id VARCHAR(100),
    message_type VARCHAR(50) NOT NULL,
    payload JSON NOT NULL,
    status VARCHAR(20) DEFAULT 'pending',
    retry_count INT DEFAULT 0,
    max_retries INT DEFAULT 3,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    processed_at TIMESTAMP NULL,
    error TEXT,
    INDEX idx_status_created (status, created_at)
);

-- Idempotency key tracking
CREATE TABLE IF NOT EXISTS idempotency_keys (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    -- id BIGSERIAL PRIMARY KEY,  -- Use this for PostgreSQL
    key_hash VARCHAR(64) UNIQUE NOT NULL,
    key_type VARCHAR(50) NOT NULL,
    expires_at TIMESTAMP NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_key_hash (key_hash),
    INDEX idx_expires (expires_at)
);
