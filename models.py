import sqlite3
import os
import re
from datetime import datetime
from config import Config

def get_db():
    db_path = Config.DATABASE_PATH
    db_dir = os.path.dirname(db_path)
    if db_dir and not os.path.exists(db_dir):
        os.makedirs(db_dir, exist_ok=True)
    conn = sqlite3.connect(db_path, timeout=15.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 15000")
    return conn

def init_db():
    conn = get_db()
    cursor = conn.cursor()

    # 1. Products Table (Master Inventory)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS products (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            category TEXT NOT NULL,
            supplier TEXT DEFAULT 'Local Supplier',
            item_code TEXT,
            description TEXT NOT NULL,
            store_stock REAL DEFAULT 0,
            factory_stock REAL DEFAULT 0,
            white_house_stock REAL DEFAULT 0,
            upstairs_stock REAL DEFAULT 0,
            goods_received REAL DEFAULT 0,
            total_sold REAL DEFAULT 0,
            layaway_reserved REAL DEFAULT 0,
            unit_cost REAL DEFAULT 0,
            selling_price REAL DEFAULT 0,
            reorder_point REAL DEFAULT 0,
            last_updated TEXT
        )
    ''')

    # 2. Layaway Orders Table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS layaway_orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date_created TEXT,
            invoice_num TEXT UNIQUE,
            customer_name TEXT NOT NULL,
            contact_number TEXT,
            items_reserved_text TEXT NOT NULL,
            total_amount REAL NOT NULL,
            amount_paid REAL DEFAULT 0,
            balance_due REAL NOT NULL,
            status TEXT DEFAULT 'Active',
            notes TEXT
        )
    ''')

    # 3. Layaway Payments History Table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS layaway_payments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            order_id INTEGER,
            invoice_num TEXT NOT NULL,
            payment_date TEXT NOT NULL,
            amount REAL NOT NULL,
            payment_method TEXT,
            received_by TEXT,
            receipt_num TEXT,
            notes TEXT,
            FOREIGN KEY (order_id) REFERENCES layaway_orders(id) ON DELETE CASCADE
        )
    ''')

    # 4. Goods Received Log Table (GRN)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS goods_received (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            grn_number TEXT,
            date_received TEXT NOT NULL,
            supplier TEXT,
            item_code TEXT,
            item_description TEXT NOT NULL,
            destination_location TEXT NOT NULL,
            quantity_received REAL NOT NULL,
            unit_cost REAL DEFAULT 0,
            total_cost REAL DEFAULT 0,
            received_by TEXT,
            notes TEXT
        )
    ''')

    # 5. Direct Sales Table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS sales (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            receipt_num TEXT,
            sale_date TEXT NOT NULL,
            customer_name TEXT,
            contact_number TEXT,
            item_code TEXT,
            item_description TEXT NOT NULL,
            category TEXT,
            quantity_sold REAL NOT NULL,
            unit_price REAL NOT NULL,
            total_amount REAL NOT NULL,
            payment_method TEXT,
            fulfillment_location TEXT,
            sales_rep TEXT,
            notes TEXT
        )
    ''')

    # 6. Raw Materials Table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS raw_materials (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            item_code TEXT,
            description TEXT NOT NULL,
            unit_cost REAL DEFAULT 0,
            opening_stock REAL DEFAULT 0,
            current_stock REAL DEFAULT 0,
            goods_received REAL DEFAULT 0,
            unit_type TEXT DEFAULT 'Boxes',
            reorder_point REAL DEFAULT 0,
            notes TEXT
        )
    ''')

    # 7. Sync Logs Table (aligned with google_sheets_sync & sync.html)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS sync_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            sync_type TEXT NOT NULL,
            status TEXT DEFAULT 'Success',
            records_synced INTEGER DEFAULT 0,
            details TEXT,
            timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    conn.commit()
    conn.close()

def parse_order_quantities(items_string):
    """
    Parses items reserved string handling +, x 2, x2, 2 x, etc.
    Returns list of dicts: [{'item_name': '...', 'quantity': float}]
    """
    if not items_string:
        return []
    
    parts = items_string.split('+')
    parsed_items = []
    
    for part in parts:
        part = part.strip()
        if not part:
            continue
        
        # Check prefix multiplier: "2 x WA190 Dresser" or "2X WA190 Dresser"
        prefix_mult = re.search(r'^(\d+(?:\.\d+)?)\s*x\s*(.+)', part, re.IGNORECASE)
        # Check suffix multiplier: "WA190 Dresser x 2" or "WA190 Dresser x2"
        suffix_mult = re.search(r'^(.*?)\s*x\s*(\d+(?:\.\d+)?)$', part, re.IGNORECASE)
        
        if prefix_mult:
            qty = float(prefix_mult.group(1))
            name = prefix_mult.group(2).strip()
            parsed_items.append({'item_name': name, 'quantity': qty})
        elif suffix_mult:
            qty = float(suffix_mult.group(2))
            name = suffix_mult.group(1).strip()
            parsed_items.append({'item_name': name, 'quantity': qty})
        else:
            # Check for pattern like "Dining Chairs x 2 + ..."
            inline_mult = re.search(r'(?:^|\s)x\s*(\d+(?:\.\d+)?)', part, re.IGNORECASE)
            if inline_mult:
                qty = float(inline_mult.group(1))
                name = re.sub(r'(?:^|\s)x\s*\d+(?:\.\d+)?', '', part, flags=re.IGNORECASE).strip()
                parsed_items.append({'item_name': name, 'quantity': qty})
            else:
                parsed_items.append({'item_name': part, 'quantity': 1.0})
            
    return parsed_items

def recalculate_inventory():
    """
    Recalculates goods_received, total_sold, and layaway_reserved across all products and materials.
    Ensures safe, idempotent updates without compounding raw materials or over-reserving inventory.
    """
    conn = get_db()
    cursor = conn.cursor()

    # Reset calculated values
    cursor.execute("UPDATE products SET goods_received = 0, total_sold = 0, layaway_reserved = 0")
    cursor.execute("UPDATE raw_materials SET goods_received = 0, current_stock = opening_stock")

    # 1. Update goods_received from GRN
    cursor.execute("SELECT item_code, item_description, SUM(quantity_received) as total_grn FROM goods_received GROUP BY item_code, item_description")
    for row in cursor.fetchall():
        code = (row['item_code'] or '').strip()
        desc = (row['item_description'] or '').strip()
        grn_qty = float(row['total_grn'] or 0)
        
        # Update Products
        if code:
            cursor.execute("UPDATE products SET goods_received = goods_received + ? WHERE item_code = ?", (grn_qty, code))
        elif desc:
            cursor.execute("UPDATE products SET goods_received = goods_received + ? WHERE description LIKE ?", (grn_qty, f"%{desc}%"))
            
        # Update Raw Materials
        if code:
            cursor.execute("UPDATE raw_materials SET goods_received = goods_received + ?, current_stock = current_stock + ? WHERE item_code = ?", (grn_qty, grn_qty, code))
        elif desc:
            cursor.execute("UPDATE raw_materials SET goods_received = goods_received + ?, current_stock = current_stock + ? WHERE description LIKE ?", (grn_qty, grn_qty, f"%{desc}%"))

    # 2. Update total_sold from Sales
    cursor.execute("SELECT item_code, item_description, SUM(quantity_sold) as total_sold FROM sales GROUP BY item_code, item_description")
    for row in cursor.fetchall():
        code = (row['item_code'] or '').strip()
        desc = (row['item_description'] or '').strip()
        sold_qty = float(row['total_sold'] or 0)
        if code:
            cursor.execute("UPDATE products SET total_sold = total_sold + ? WHERE item_code = ?", (sold_qty, code))
        elif desc:
            cursor.execute("UPDATE products SET total_sold = total_sold + ? WHERE description LIKE ?", (sold_qty, f"%{desc}%"))

    # 3. Update layaway_reserved from active layaway orders
    cursor.execute("SELECT items_reserved_text FROM layaway_orders WHERE status = 'Active'")
    for row in cursor.fetchall():
        items = parse_order_quantities(row['items_reserved_text'])
        for item in items:
            name = item['item_name'].strip()
            qty = float(item['quantity'])
            if not name:
                continue
            
            # Prioritize exact match on item_code, then exact match on description
            cursor.execute("SELECT id FROM products WHERE item_code = ? OR description = ?", (name, name))
            exact_matches = cursor.fetchall()
            if exact_matches:
                for match in exact_matches:
                    cursor.execute("UPDATE products SET layaway_reserved = layaway_reserved + ? WHERE id = ?", (qty, match['id']))
            else:
                # Fuzzy/substring match: allocate to the single best match to prevent multiple allocations
                cursor.execute("SELECT id FROM products WHERE description LIKE ? OR item_code LIKE ? ORDER BY LENGTH(description) ASC LIMIT 1", (f"%{name}%", f"%{name}%"))
                partial_match = cursor.fetchone()
                if partial_match:
                    cursor.execute("UPDATE products SET layaway_reserved = layaway_reserved + ? WHERE id = ?", (qty, partial_match['id']))

    conn.commit()
    conn.close()
