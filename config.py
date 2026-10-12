import os
import sqlite3

BASE_DIR = os.path.abspath(os.path.dirname(__file__))

def get_default_db_path():
    env_path = os.environ.get('DATABASE_PATH')
    if env_path:
        return env_path
    
    primary_path = os.path.join(BASE_DIR, 'inventory.db')
    # Test if SQLite can write and lock in BASE_DIR; if filesystem returns disk I/O error (e.g. network mount), use fallback in /tmp
    try:
        test_dir = os.path.dirname(primary_path)
        if test_dir and not os.path.exists(test_dir):
            os.makedirs(test_dir, exist_ok=True)
        test_conn = sqlite3.connect(primary_path, timeout=1.0)
        test_conn.execute("CREATE TABLE IF NOT EXISTS _lock_test (id INTEGER PRIMARY KEY)")
        test_conn.commit()
        test_conn.close()
        return primary_path
    except Exception:
        fallback_path = '/tmp/tncf_inventory.db'
        return fallback_path

class Config:
    SECRET_KEY = os.environ.get('SECRET_KEY', 'tncf-secret-key-2026-kingston')
    DATABASE_PATH = get_default_db_path()
    GOOGLE_SHEET_ID = os.environ.get('GOOGLE_SHEET_ID', '181JZpOItrdXFTJNYYjug8HRWKaJu_EsoHB3jT_R0DrA')
    GOOGLE_CREDENTIALS_FILE = os.environ.get('GOOGLE_CREDENTIALS_FILE', os.path.join(BASE_DIR, 'service_account.json'))
