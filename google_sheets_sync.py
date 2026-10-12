"""
Google Sheets Bi-directional Synchronization Module for The New City Furniture Inventory System.
Supports real-time write-back, status checks, and resilient logging.
"""

import os
import json
from datetime import datetime
from config import Config
from models import get_db, recalculate_inventory

def get_sheets_client():
    """Initializes and returns gspread client if service_account.json exists and is valid."""
    try:
        import gspread
        from google.oauth2.service_account import Credentials
        
        scopes = [
            'https://www.googleapis.com/auth/spreadsheets',
            'https://www.googleapis.com/auth/drive'
        ]
        
        if os.path.exists(Config.GOOGLE_CREDENTIALS_FILE):
            creds = Credentials.from_service_account_file(Config.GOOGLE_CREDENTIALS_FILE, scopes=scopes)
            client = gspread.authorize(creds)
            return client
        else:
            return None
    except Exception as e:
        print(f"Sheets client initialization note: {e}")
        return None

def check_connection_status():
    """Checks Google Sheets connectivity status for display in the Sync Hub."""
    if not os.path.exists(Config.GOOGLE_CREDENTIALS_FILE):
        return {
            "connected": False,
            "message": "Service account credentials file (service_account.json) not detected. Operating in local SQLite mode."
        }
    client = get_sheets_client()
    if not client:
        return {
            "connected": False,
            "message": "Credentials file detected but authorization failed."
        }
    try:
        sh = client.open_by_key(Config.GOOGLE_SHEET_ID)
        return {
            "connected": True,
            "message": f"Successfully connected to workbook '{sh.title}'"
        }
    except Exception as e:
        return {
            "connected": False,
            "message": f"Connection error: {str(e)}"
        }

def sync_sale_to_sheets(sale_data):
    """Appends a new direct sale to the 'Sales' sheet in real time."""
    client = get_sheets_client()
    if not client:
        log_sync("SALE_WRITEBACK", "LOCAL_ONLY", 1, f"Recorded receipt {sale_data.get('receipt_num')} locally in SQLite (cloud credentials pending).")
        return False
        
    try:
        sh = client.open_by_key(Config.GOOGLE_SHEET_ID)
        worksheet = sh.worksheet("Sales")
        next_row = worksheet.row_count + 1
        row = [
            sale_data.get('sale_date', datetime.now().strftime('%Y-%m-%d')),
            sale_data.get('receipt_num', ''),
            sale_data.get('customer_name', ''),
            sale_data.get('contact_number', ''),
            sale_data.get('item_code', ''),
            sale_data.get('item_description', ''),
            sale_data.get('category', ''),
            float(sale_data.get('quantity_sold', 1) or 1),
            float(sale_data.get('unit_price', 0) or 0),
            f"=H{next_row}*I{next_row}",
            sale_data.get('payment_method', 'Cash'),
            sale_data.get('fulfillment_location', 'STORE'),
            sale_data.get('sales_rep', ''),
            sale_data.get('notes', '')
        ]
        worksheet.append_row(row, value_input_option='USER_ENTERED')
        log_sync("SALE_WRITEBACK", "SUCCESS", 1, f"Appended receipt {sale_data.get('receipt_num')} to Google Sheet.")
        return True
    except Exception as e:
        log_sync("SALE_WRITEBACK", "ERROR", 0, f"Error appending sale: {str(e)}")
        return False

def sync_goods_received_to_sheets(gr_data):
    """Appends an incoming shipment to the 'Goods Received' sheet in real time."""
    client = get_sheets_client()
    if not client:
        log_sync("GRN_WRITEBACK", "LOCAL_ONLY", 1, f"Recorded GRN {gr_data.get('grn_number')} locally in SQLite (cloud credentials pending).")
        return False
        
    try:
        sh = client.open_by_key(Config.GOOGLE_SHEET_ID)
        worksheet = sh.worksheet("Goods Received")
        next_row = worksheet.row_count + 1
        row = [
            gr_data.get('date_received', datetime.now().strftime('%Y-%m-%d')),
            gr_data.get('grn_number', ''),
            gr_data.get('supplier', ''),
            gr_data.get('item_code', ''),
            gr_data.get('item_description', ''),
            gr_data.get('destination_location', 'STORE'),
            float(gr_data.get('quantity_received', 1) or 1),
            float(gr_data.get('unit_cost', 0) or 0),
            f"=G{next_row}*H{next_row}",
            gr_data.get('received_by', ''),
            gr_data.get('notes', '')
        ]
        worksheet.append_row(row, value_input_option='USER_ENTERED')
        log_sync("GRN_WRITEBACK", "SUCCESS", 1, f"Appended GRN {gr_data.get('grn_number')} to Google Sheet.")
        return True
    except Exception as e:
        log_sync("GRN_WRITEBACK", "ERROR", 0, f"Error appending GRN: {str(e)}")
        return False

def sync_layaway_to_sheets(layaway_data):
    """Appends a new layaway agreement to the 'Master Lay-A-Way Ledger' sheet in real time."""
    client = get_sheets_client()
    if not client:
        log_sync("LAYAWAY_WRITEBACK", "LOCAL_ONLY", 1, f"Recorded Agreement #{layaway_data.get('invoice_num')} locally in SQLite (cloud credentials pending).")
        return False
        
    try:
        sh = client.open_by_key(Config.GOOGLE_SHEET_ID)
        worksheet = sh.worksheet("Master Lay-A-Way Ledger")
        row = [
            layaway_data.get('date_created', datetime.now().strftime('%Y-%m-%d')),
            layaway_data.get('invoice_num', ''),
            layaway_data.get('customer_name', ''),
            layaway_data.get('contact_number', ''),
            layaway_data.get('items_reserved_text', ''),
            float(layaway_data.get('total_amount', 0) or 0),
            float(layaway_data.get('amount_paid', 0) or 0),
            float(layaway_data.get('balance_due', 0) or 0),
            layaway_data.get('status', 'Active')
        ]
        worksheet.append_row(row, value_input_option='USER_ENTERED')
        log_sync("LAYAWAY_WRITEBACK", "SUCCESS", 1, f"Appended Invoice #{layaway_data.get('invoice_num')} to Google Sheet.")
        return True
    except Exception as e:
        log_sync("LAYAWAY_WRITEBACK", "ERROR", 0, f"Error appending layaway: {str(e)}")
        return False

def log_sync(sync_type, status, records, details):
    """Records synchronization event into SQLite sync_logs."""
    try:
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO sync_logs (sync_type, status, records_synced, details)
            VALUES (?, ?, ?, ?)
        ''', (sync_type, status, records, details))
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"Failed to log sync: {e}")
