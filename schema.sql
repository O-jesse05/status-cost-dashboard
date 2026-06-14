-- ============================================
-- Database Schema: Status Monitor
-- SQLite 3.35+
-- ============================================

-- Enable WAL mode for concurrent reads/writes
-- (Agent writes every 60s while dashboard reads)
PRAGMA journal_mode=WAL;
PRAGMA synchronous=NORMAL;
PRAGMA foreign_keys=ON;

-- ============================================
-- Table 1: endpoints
-- Stores what you're monitoring
-- ============================================

CREATE TABLE IF NOT EXISTS endpoints (
    -- Primary key
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    
    -- Human readable name (e.g., "GitHub API")
    name TEXT NOT NULL,
    
    -- Full URL to monitor (e.g., "https://api.github.com/zen")
    url TEXT NOT NULL,
    
    -- What HTTP status code means "up" (usually 200)
    expected_status_code INTEGER DEFAULT 200,
    
    -- How long to wait before giving up (seconds)
    timeout_seconds INTEGER DEFAULT 5,
    
    -- How often to check this endpoint (seconds)
    check_interval_seconds INTEGER DEFAULT 60,
    
    -- Soft delete (1 = active, 0 = deleted)
    is_active BOOLEAN DEFAULT 1,
    
    -- Timestamps
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    
    -- Ensure same URL isn't added twice
    UNIQUE(url)
);

-- ============================================
-- Table 2: checks
-- Stores every single check result (time series)
-- This table grows by ~7,200 rows/day (5 endpoints × 1440 checks)
-- ============================================

CREATE TABLE IF NOT EXISTS checks (
    -- Primary key
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    
    -- Which endpoint was checked
    endpoint_id INTEGER NOT NULL,
    
    -- When the check happened
    checked_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    
    -- Result: 'up' or 'down'
    status TEXT NOT NULL CHECK(status IN ('up', 'down')),
    
    -- How long the request took (milliseconds)
    latency_ms INTEGER,
    
    -- HTTP status code returned (e.g., 200, 404, 500)
    status_code INTEGER,
    
    -- If failed, why? (e.g., "Connection timeout")
    error_message TEXT,
    
    -- Delete checks if their endpoint is deleted
    FOREIGN KEY (endpoint_id) REFERENCES endpoints(id) ON DELETE CASCADE
);

-- ============================================
-- Table 3: daily_costs
-- Stores AWS cost data from CSV imports
-- ============================================

CREATE TABLE IF NOT EXISTS daily_costs (
    -- Primary key
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    
    -- Date of the cost (YYYY-MM-DD)
    date DATE NOT NULL,
    
    -- AWS service name (e.g., "AmazonEC2", "AmazonS3")
    service_name TEXT NOT NULL,
    
    -- Cost in USD (e.g., 0.42 for 42 cents)
    amount_usd DECIMAL(10,4) NOT NULL,
    
    -- Currency code (usually USD)
    currency TEXT DEFAULT 'USD',
    
    -- When this record was imported
    imported_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    
    -- Don't duplicate same service on same day
    UNIQUE(date, service_name)
);

-- ============================================
-- Table 4: incidents (Bonus - for future)
-- Tracks downtime events automatically
-- ============================================

CREATE TABLE IF NOT EXISTS incidents (
    -- Primary key
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    
    -- Which endpoint had an incident
    endpoint_id INTEGER NOT NULL,
    
    -- When the downtime started
    started_at DATETIME NOT NULL,
    
    -- When the downtime ended (NULL if still happening)
    ended_at DATETIME,
    
    -- Calculated duration in seconds (ended_at - started_at)
    duration_seconds INTEGER,
    
    -- Foreign key
    FOREIGN KEY (endpoint_id) REFERENCES endpoints(id) ON DELETE CASCADE
);

-- ============================================
-- Views (Pre-calculated queries for the API)
-- ============================================

-- View 1: Daily uptime summary (makes API queries faster)
CREATE VIEW IF NOT EXISTS uptime_summary AS
SELECT 
    endpoint_id,
    DATE(checked_at) as check_date,
    COUNT(*) as total_checks,
    SUM(CASE WHEN status = 'up' THEN 1 ELSE 0 END) as successful_checks,
    ROUND(100.0 * SUM(CASE WHEN status = 'up' THEN 1 ELSE 0 END) / COUNT(*), 2) as uptime_percentage,
    ROUND(AVG(CASE WHEN status = 'up' THEN latency_ms ELSE NULL END), 0) as avg_latency_ms
FROM checks
GROUP BY endpoint_id, DATE(checked_at);

-- View 2: Current status of all active endpoints
CREATE VIEW IF NOT EXISTS current_status AS
SELECT 
    e.id as endpoint_id,
    e.name,
    e.url,
    c.status as current_status,
    c.latency_ms as last_latency_ms,
    c.checked_at as last_check_time,
    c.error_message as last_error,
    julianday('now') - julianday(c.checked_at) as minutes_since_check
FROM endpoints e
LEFT JOIN checks c ON e.id = c.endpoint_id
WHERE c.checked_at = (
    SELECT MAX(checked_at) 
    FROM checks 
    WHERE endpoint_id = e.id
)
AND e.is_active = 1;

-- ============================================
-- Indexes (Make queries FAST)
-- ============================================

-- For GET /api/history (time-based queries)
CREATE INDEX IF NOT EXISTS idx_checks_endpoint_time ON checks(endpoint_id, checked_at);

-- For uptime calculations (last 24h, 7d, 30d)
CREATE INDEX IF NOT EXISTS idx_checks_timestamp ON checks(checked_at);

-- For filtering by status (e.g., count failures)
CREATE INDEX IF NOT EXISTS idx_checks_status ON checks(status);

-- For current_status view
CREATE INDEX IF NOT EXISTS idx_checks_latest ON checks(endpoint_id, checked_at DESC);

-- For cost queries by date range
CREATE INDEX IF NOT EXISTS idx_costs_date ON daily_costs(date);

-- For incident lookups
CREATE INDEX IF NOT EXISTS idx_incidents_endpoint ON incidents(endpoint_id);
CREATE INDEX IF NOT EXISTS idx_incidents_time ON incidents(started_at, ended_at);

-- ============================================
-- Triggers (Automate things)
-- ============================================

-- Trigger 1: Update 'updated_at' when endpoint changes
CREATE TRIGGER IF NOT EXISTS update_endpoints_timestamp 
AFTER UPDATE ON endpoints
BEGIN
    UPDATE endpoints SET updated_at = CURRENT_TIMESTAMP WHERE id = NEW.id;
END;

-- Trigger 2: Auto-create incident when first 'down' occurs
CREATE TRIGGER IF NOT EXISTS create_incident_on_downtime
AFTER INSERT ON checks
WHEN NEW.status = 'down' 
AND NOT EXISTS (
    SELECT 1 FROM incidents 
    WHERE endpoint_id = NEW.endpoint_id 
    AND ended_at IS NULL
)
BEGIN
    INSERT INTO incidents (endpoint_id, started_at)
    VALUES (NEW.endpoint_id, NEW.checked_at);
END;

-- Trigger 3: Auto-close incident when 'up' returns
CREATE TRIGGER IF NOT EXISTS close_incident_on_uptime
AFTER INSERT ON checks
WHEN NEW.status = 'up'
AND EXISTS (
    SELECT 1 FROM incidents 
    WHERE endpoint_id = NEW.endpoint_id 
    AND ended_at IS NULL
)
BEGIN
    UPDATE incidents 
    SET ended_at = NEW.checked_at,
        duration_seconds = CAST((julianday(NEW.checked_at) - julianday(started_at)) * 86400 AS INTEGER)
    WHERE endpoint_id = NEW.endpoint_id AND ended_at IS NULL;
END;

-- ============================================
-- Sample Data (For testing)
-- ============================================

-- Insert test endpoints (only if table is empty)
INSERT OR IGNORE INTO endpoints (name, url) VALUES 
    ('GitHub API', 'https://api.github.com/zen'),
    ('HTTPBin Delay', 'https://httpbin.org/delay/1'),
    ('Test Failure', 'https://thisurldoesnotexist.example.com'),
    ('Google DNS', 'https://dns.google/resolve?name=google.com');

-- Insert sample check data (last 24 hours of simulated data)
-- This helps you test the dashboard immediately
INSERT OR IGNORE INTO checks (endpoint_id, checked_at, status, latency_ms, status_code)
SELECT 
    1 as endpoint_id,
    datetime('now', '-' || (value) || ' minutes') as checked_at,
    'up' as status,
    abs(random() % 200) + 50 as latency_ms,
    200 as status_code
FROM generate_series(0, 1440) 
WHERE value % 10 != 0  -- 90% uptime for demo
AND NOT EXISTS (SELECT 1 FROM checks WHERE endpoint_id = 1 LIMIT 1);

-- Note: SQLite doesn't have generate_series by default.
-- For testing, manually insert a few rows:
INSERT OR IGNORE INTO checks (endpoint_id, checked_at, status, latency_ms, status_code) VALUES
    (1, datetime('now', '-5 minutes'), 'up', 145, 200),
    (1, datetime('now', '-10 minutes'), 'up', 148, 200),
    (1, datetime('now', '-15 minutes'), 'up', 152, 200),
    (2, datetime('now', '-5 minutes'), 'up', 1023, 200),
    (2, datetime('now', '-10 minutes'), 'up', 987, 200),
    (3, datetime('now', '-5 minutes'), 'down', NULL, NULL),
    (3, datetime('now', '-10 minutes'), 'down', NULL, NULL);