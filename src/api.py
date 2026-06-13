import sqlite3
from datetime import datetime, timedelta
from typing import Optional, List
from fastapi import FastAPI, HTTPException, Depends, Header, UploadFile, File, status
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import csv
import io
import os
from dotenv import load_dotenv

# Load API key from .env file
load_dotenv()
ADMIN_API_KEY = os.getenv("ADMIN_API_KEY", "change-this-secret-key")

# Create FastAPI app
app = FastAPI(title="Status Monitor API", version="1.0.0")

# Allow dashboard to call API from different port
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # In production, restrict this
    allow_methods=["*"],
    allow_headers=["*"],
)

# Database path
DB_PATH = "/var/lib/status-monitor/checks.db"

# For local development without sudo, use local file
if not os.path.exists("/var/lib/status-monitor"):
    DB_PATH = "checks.db"


# ============================================
# DATABASE HELPER FUNCTIONS
# ============================================

def get_db():
    """Get database connection"""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row  # Returns rows as dictionaries
    return conn

def init_db():
    """Create tables if they don't exist"""
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
    conn.execute("""
        CREATE TABLE IF NOT EXISTS daily_costs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date DATE NOT NULL,
            service_name TEXT NOT NULL,
            amount_usd DECIMAL(10,4) NOT NULL,
            currency TEXT DEFAULT 'USD',
            imported_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(date, service_name)
        )
    """)
    conn.commit()
    conn.close()

# Initialize database on startup
init_db()


# ============================================
# AUTHENTICATION
# ============================================

def verify_api_key(x_api_key: str = Header(...)):
    """Check if API key is valid"""
    if x_api_key != ADMIN_API_KEY:
        raise HTTPException(status_code=403, detail="Invalid API Key")
    return x_api_key


# ============================================
# PYDANTIC MODELS (Request/Response shapes)
# ============================================

class EndpointCreate(BaseModel):
    name: str
    url: str
    expected_status_code: int = 200
    timeout_seconds: int = 5
    interval_seconds: int = 60

class EndpointResponse(BaseModel):
    id: int
    name: str
    url: str
    current_status: str
    last_check: Optional[str]
    uptime_24h: float
    avg_latency_ms: Optional[int]


# ============================================
# API ENDPOINTS
# ============================================

@app.get("/")
def root():
    return {"message": "Status Monitor API", "docs": "/docs"}


@app.get("/health")
def health():
    """Health check for monitoring"""
    conn = get_db()
    try:
        # Check database
        conn.execute("SELECT 1").fetchone()
        db_status = "connected"
    except:
        db_status = "disconnected"
    conn.close()
    
    return {
        "status": "healthy" if db_status == "connected" else "degraded",
        "timestamp": datetime.utcnow().isoformat(),
        "database": db_status,
        "version": "1.0.0"
    }


@app.get("/api/endpoints")
def list_endpoints():
    """Get all monitored endpoints with current status"""
    conn = get_db()
    
    # Get all active endpoints
    endpoints = conn.execute("""
        SELECT id, name, url, expected_status_code, timeout_seconds, 
               check_interval_seconds, created_at
        FROM endpoints WHERE is_active = 1
    """).fetchall()
    
    result = []
    for ep in endpoints:
        # Get latest check
        latest = conn.execute("""
            SELECT status, latency_ms, checked_at, error_message
            FROM checks WHERE endpoint_id = ?
            ORDER BY checked_at DESC LIMIT 1
        """, (ep["id"],)).fetchone()
        
        # Calculate 24h uptime
        uptime = conn.execute("""
            SELECT 
                ROUND(100.0 * SUM(CASE WHEN status = 'up' THEN 1 ELSE 0 END) / COUNT(*), 2) as uptime
            FROM checks 
            WHERE endpoint_id = ? AND checked_at > datetime('now', '-1 day')
        """, (ep["id"],)).fetchone()
        
        # Calculate average latency (last 24h, only successful checks)
        avg_latency = conn.execute("""
            SELECT ROUND(AVG(latency_ms), 0) as avg_latency
            FROM checks 
            WHERE endpoint_id = ? AND status = 'up' AND checked_at > datetime('now', '-1 day')
        """, (ep["id"],)).fetchone()
        
        result.append({
            "id": ep["id"],
            "name": ep["name"],
            "url": ep["url"],
            "current_status": latest["status"] if latest else "unknown",
            "last_check": latest["checked_at"] if latest else None,
            "uptime_24h": uptime["uptime"] if uptime and uptime["uptime"] else 100.0,
            "avg_latency_ms": int(avg_latency["avg_latency"]) if avg_latency and avg_latency["avg_latency"] else None
        })
    
    conn.close()
    return {"endpoints": result}


@app.get("/api/status/{endpoint_id}")
def get_status(endpoint_id: int):
    """Get detailed status for one endpoint"""
    conn = get_db()
    
    # Check if endpoint exists
    endpoint = conn.execute("""
        SELECT id, name FROM endpoints WHERE id = ? AND is_active = 1
    """, (endpoint_id,)).fetchone()
    
    if not endpoint:
        raise HTTPException(status_code=404, detail="Endpoint not found")
    
    # Latest check
    latest = conn.execute("""
        SELECT status, latency_ms, status_code, checked_at, error_message
        FROM checks WHERE endpoint_id = ?
        ORDER BY checked_at DESC LIMIT 1
    """, (endpoint_id,)).fetchone()
    
    # Uptime percentages
    uptime_24h = conn.execute("""
        SELECT ROUND(100.0 * SUM(CASE WHEN status = 'up' THEN 1 ELSE 0 END) / COUNT(*), 2) as uptime
        FROM checks WHERE endpoint_id = ? AND checked_at > datetime('now', '-1 day')
    """, (endpoint_id,)).fetchone()
    
    uptime_7d = conn.execute("""
        SELECT ROUND(100.0 * SUM(CASE WHEN status = 'up' THEN 1 ELSE 0 END) / COUNT(*), 2) as uptime
        FROM checks WHERE endpoint_id = ? AND checked_at > datetime('now', '-7 days')
    """, (endpoint_id,)).fetchone()
    
    uptime_30d = conn.execute("""
        SELECT ROUND(100.0 * SUM(CASE WHEN status = 'up' THEN 1 ELSE 0 END) / COUNT(*), 2) as uptime
        FROM checks WHERE endpoint_id = ? AND checked_at > datetime('now', '-30 days')
    """, (endpoint_id,)).fetchone()
    
    # Count checks
    total = conn.execute("""
        SELECT COUNT(*) as total, SUM(CASE WHEN status = 'down' THEN 1 ELSE 0 END) as failed
        FROM checks WHERE endpoint_id = ? AND checked_at > datetime('now', '-1 day')
    """, (endpoint_id,)).fetchone()
    
    conn.close()
    
    return {
        "endpoint_id": endpoint_id,
        "name": endpoint["name"],
        "current_status": latest["status"] if latest else "unknown",
        "last_check": latest["checked_at"] if latest else None,
        "last_latency_ms": latest["latency_ms"] if latest else None,
        "last_status_code": latest["status_code"] if latest else None,
        "uptime_24h": uptime_24h["uptime"] if uptime_24h and uptime_24h["uptime"] else 100.0,
        "uptime_7d": uptime_7d["uptime"] if uptime_7d and uptime_7d["uptime"] else 100.0,
        "uptime_30d": uptime_30d["uptime"] if uptime_30d and uptime_30d["uptime"] else 100.0,
        "total_checks_24h": total["total"] if total else 0,
        "failed_checks_24h": total["failed"] if total else 0,
        "last_error": latest["error_message"] if latest and latest["status"] == "down" else None
    }


@app.get("/api/history/{endpoint_id}")
def get_history(endpoint_id: int, hours: int = 24):
    """Get time series data for graphs"""
    conn = get_db()
    
    # Verify endpoint exists
    endpoint = conn.execute("SELECT id FROM endpoints WHERE id = ?", (endpoint_id,)).fetchone()
    if not endpoint:
        raise HTTPException(status_code=404, detail="Endpoint not found")
    
    # Get checks for last N hours
    checks = conn.execute("""
        SELECT 
            strftime('%Y-%m-%dT%H:%M:00Z', checked_at) as timestamp,
            status,
            latency_ms
        FROM checks 
        WHERE endpoint_id = ? AND checked_at > datetime('now', ? || ' hours')
        ORDER BY checked_at ASC
    """, (endpoint_id, f"-{hours}")).fetchall()
    
    conn.close()
    
    return {
        "endpoint_id": endpoint_id,
        "hours": hours,
        "resolution": "minute",
        "timestamps": [c["timestamp"] for c in checks],
        "latencies_ms": [c["latency_ms"] for c in checks],
        "statuses": [c["status"] for c in checks]
    }


@app.post("/api/endpoints", status_code=status.HTTP_201_CREATED)
def create_endpoint(endpoint: EndpointCreate, api_key=Depends(verify_api_key)):
    """Add new endpoint to monitor (requires API key)"""
    conn = get_db()
    
    # Check if URL already exists
    existing = conn.execute("SELECT id FROM endpoints WHERE url = ?", (endpoint.url,)).fetchone()
    if existing:
        raise HTTPException(status_code=409, detail="Endpoint with this URL already exists")
    
    # Insert new endpoint
    cursor = conn.execute("""
        INSERT INTO endpoints (name, url, expected_status_code, timeout_seconds, check_interval_seconds)
        VALUES (?, ?, ?, ?, ?)
    """, (endpoint.name, endpoint.url, endpoint.expected_status_code, 
          endpoint.timeout_seconds, endpoint.interval_seconds))
    
    conn.commit()
    new_id = cursor.lastrowid
    conn.close()
    
    return {
        "id": new_id,
        "name": endpoint.name,
        "url": endpoint.url,
        "expected_status_code": endpoint.expected_status_code,
        "timeout_seconds": endpoint.timeout_seconds,
        "interval_seconds": endpoint.interval_seconds,
        "is_active": True,
        "created_at": datetime.utcnow().isoformat()
    }


@app.delete("/api/endpoints/{endpoint_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_endpoint(endpoint_id: int, api_key=Depends(verify_api_key)):
    """Delete endpoint (requires API key)"""
    conn = get_db()
    
    # Soft delete (set is_active = 0)
    result = conn.execute("""
        UPDATE endpoints SET is_active = 0 WHERE id = ?
    """, (endpoint_id,))
    
    conn.commit()
    conn.close()
    
    if result.rowcount == 0:
        raise HTTPException(status_code=404, detail="Endpoint not found")
    
    return None


@app.get("/api/costs/daily")
def get_daily_costs(days: int = 7):
    """Get daily cost data for charts"""
    conn = get_db()
    
    costs = conn.execute("""
        SELECT date, service_name, amount_usd
        FROM daily_costs
        WHERE date >= date('now', ? || ' days')
        ORDER BY date ASC, service_name ASC
    """, (f"-{days}",)).fetchall()
    
    conn.close()
    
    if not costs:
        return {
            "dates": [],
            "services": {},
            "totals": [],
            "currency": "USD",
            "message": "No cost data available. Import CSV first."
        }
    
    # Build response
    dates = sorted(set(c["date"] for c in costs))
    services = {}
    totals = []
    
    for date in dates:
        daily_total = 0
        for cost in costs:
            if cost["date"] == date:
                if cost["service_name"] not in services:
                    services[cost["service_name"]] = []
                daily_total += float(cost["amount_usd"])
        
        # Append to each service list (handle missing days)
        for service in services:
            service_costs = [c for c in costs if c["service_name"] == service and c["date"] == date]
            if service_costs:
                # Already added, but we need to maintain order
                pass
        totals.append(round(daily_total, 2))
    
    # Rebuild services dict with arrays in date order
    result_services = {}
    for service in services:
        result_services[service] = []
        for date in dates:
            cost = next((c for c in costs if c["service_name"] == service and c["date"] == date), None)
            result_services[service].append(float(cost["amount_usd"]) if cost else 0)
    
    return {
        "dates": dates,
        "services": result_services,
        "totals": totals,
        "currency": "USD"
    }


@app.post("/api/costs/import", status_code=status.HTTP_202_ACCEPTED)
async def import_costs(file: UploadFile = File(...), api_key=Depends(verify_api_key)):
    """Import CSV cost data (requires API key)"""
    
    if not file.filename.endswith('.csv'):
        raise HTTPException(status_code=400, detail="Only CSV files allowed")
    
    # Read file content
    content = await file.read()
    text = content.decode('utf-8')
    csv_reader = csv.DictReader(io.StringIO(text))
    
    conn = get_db()
    rows_processed = 0
    rows_inserted = 0
    rows_skipped = 0
    skipped_reasons = []
    
    for row in csv_reader:
        rows_processed += 1
        try:
            # Expected columns: Service, Cost, Currency, Date
            service = row.get('Service', row.get('service', ''))
            cost = float(row.get('Cost', row.get('cost', 0)))
            currency = row.get('Currency', row.get('currency', 'USD'))
            date = row.get('Date', row.get('date', ''))
            
            if not service or not date:
                rows_skipped += 1
                skipped_reasons.append(f"Row {rows_processed}: Missing service or date")
                continue
            
            # Insert or replace
            conn.execute("""
                INSERT OR REPLACE INTO daily_costs (date, service_name, amount_usd, currency)
                VALUES (?, ?, ?, ?)
            """, (date, service, cost, currency))
            rows_inserted += 1
            
        except Exception as e:
            rows_skipped += 1
            skipped_reasons.append(f"Row {rows_processed}: {str(e)}")
    
    conn.commit()
    conn.close()
    
    return {
        "message": "Import completed",
        "rows_processed": rows_processed,
        "rows_inserted": rows_inserted,
        "rows_skipped": rows_skipped,
        "skipped_reasons": skipped_reasons[:5]  # Return first 5 reasons
    }


# ============================================
# RUN THE SERVER
# ============================================

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)