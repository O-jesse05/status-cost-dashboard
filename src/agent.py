#!/usr/bin/env python3
"""
Status Monitor Agent
Runs every 60 seconds via cron to check endpoints and store results

Usage:
    python src/agent.py
"""

import sqlite3
import requests
import time
from datetime import datetime
import os
import sys

# ============================================
# CONFIGURATION
# ============================================

# Database path (same as API uses)
DB_PATH = "/var/lib/status-monitor/checks.db"

# For local development without sudo, use local file
if not os.path.exists("/var/lib/status-monitor"):
    DB_PATH = "checks.db"

# ============================================
# DATABASE FUNCTIONS
# ============================================

def get_db():
    """Get database connection"""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    """Create tables if they don't exist (same as API)"""
    conn = get_db()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS endpoints (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            url TEXT NOT NULL,
            expected_status_code INTEGER DEFAULT 200,
            timeout_seconds INTEGER DEFAULT 5,
            check_interval_seconds INTEGER DEFAULT 60,
            is_active BOOLEAN DEFAULT 1,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS checks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            endpoint_id INTEGER NOT NULL,
            checked_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            status TEXT NOT NULL,
            latency_ms INTEGER,
            status_code INTEGER,
            error_message TEXT
        )
    """)
    conn.commit()
    conn.close()

# ============================================
# CHECK FUNCTION (The core logic)
# ============================================

def check_endpoint(endpoint):
    """
    Check a single endpoint
    Returns: (status, latency_ms, status_code, error_message)
    """
    url = endpoint['url']
    timeout = endpoint['timeout_seconds']
    expected_status = endpoint['expected_status_code']
    
    start_time = time.time()
    
    try:
        # Make the HTTP request
        response = requests.get(
            url, 
            timeout=timeout,
            headers={'User-Agent': 'Status-Monitor/1.0'}
        )
        
        # Calculate latency in milliseconds
        latency_ms = int((time.time() - start_time) * 1000)
        
        # Check if status code matches expected
        if response.status_code == expected_status:
            status = 'up'
            error_message = None
        else:
            status = 'down'
            error_message = f"Expected {expected_status}, got {response.status_code}"
        
        return (status, latency_ms, response.status_code, error_message)
        
    except requests.exceptions.Timeout:
        latency_ms = int((time.time() - start_time) * 1000)
        return ('down', latency_ms, None, f"Timeout after {timeout} seconds")
        
    except requests.exceptions.ConnectionError:
        latency_ms = int((time.time() - start_time) * 1000)
        return ('down', latency_ms, None, "Connection error (DNS or network)")
        
    except requests.exceptions.RequestException as e:
        latency_ms = int((time.time() - start_time) * 1000)
        return ('down', latency_ms, None, str(e)[:200])  # Truncate long errors

# ============================================
# SAVE RESULTS
# ============================================

def save_check(endpoint_id, status, latency_ms, status_code, error_message):
    """Save check result to database"""
    conn = get_db()
    conn.execute("""
        INSERT INTO checks (endpoint_id, status, latency_ms, status_code, error_message)
        VALUES (?, ?, ?, ?, ?)
    """, (endpoint_id, status, latency_ms, status_code, error_message))
    conn.commit()
    conn.close()

# ============================================
# MAIN FUNCTION (Runs every minute)
# ============================================

def main():
    """Main function - check all active endpoints"""
    
    # Ensure database exists
    init_db()
    
    # Get all active endpoints
    conn = get_db()
    endpoints = conn.execute("""
        SELECT id, name, url, expected_status_code, timeout_seconds
        FROM endpoints 
        WHERE is_active = 1
    """).fetchall()
    conn.close()
    
    if not endpoints:
        print(f"[{datetime.now().isoformat()}] No endpoints to check")
        return
    
    print(f"[{datetime.now().isoformat()}] Checking {len(endpoints)} endpoints...")
    
    # Check each endpoint
    for endpoint in endpoints:
        print(f"  Checking {endpoint['name']} ({endpoint['url']})...", end=" ")
        
        status, latency_ms, status_code, error = check_endpoint(endpoint)
        
        # Save result
        save_check(endpoint['id'], status, latency_ms, status_code, error)
        
        # Print result
        if status == 'up':
            print(f"✅ UP ({latency_ms}ms)")
        else:
            print(f"❌ DOWN - {error}")
    
    print(f"[{datetime.now().isoformat()}] Done.")

# ============================================
# RUN
# ============================================

if __name__ == "__main__":
    main()