import csv
import io
import os
import sys
import sqlite3
from datetime import datetime

# Ensure current directory is in sys.path
sys.path.append(os.path.abspath(os.path.dirname(__file__)))

from config import Config
from models import get_db, init_db, recalculate_inventory
from google_sheets_sync import (
    sync_sale_to_sheets,
    sync_goods_received_to_sheets,
    sync_layaway_to_sheets,
    check_connection_status
)

try:
    from flask import (
        Flask,
        render_template,
        request,
        redirect,
        url_for,
        flash,
        jsonify,
        Response
    )
except ImportError:
    Flask = None

def create_app():
    if not Flask:
        return None

    app = Flask(__name__)
    app.config.from_object(Config)

    # Initialize DB schema on startup
    with app.app_context():
        init_db()

    # 1. Dashboard View
    @app.route('/')
    def dashboard():
        conn = get_db()
        cur = conn.cursor()

        # Layaway KPI metrics
        cur.execute("SELECT COUNT(*) as total_orders, COALESCE(SUM(total_amount), 0) as total_rev, COALESCE(SUM(amount_paid), 0) as total_paid, COALESCE(SUM(balance_due), 0) as total_bal FROM layaway_orders WHERE status = 'Active'")
        layaway_row = cur.fetchone()

        total_orders = layaway_row['total_orders'] or 0
        total_rev = layaway_row['total_rev'] or 0
        total_paid = layaway_row['total_paid'] or 0
        total_bal = layaway_row['total_bal'] or 0
        realized_rate = (total_paid / total_rev * 100.0) if total_rev > 0 else 0.0
        pending_rate = (total_bal / total_rev * 100.0) if total_rev > 0 else 0.0

        # Finished Goods KPI metrics
        cur.execute("""
            SELECT 
                COUNT(*) as total_skus,
                COALESCE(SUM(store_stock + factory_stock + white_house_stock + upstairs_stock + goods_received), 0) as total_physical,
                COALESCE(SUM(layaway_reserved), 0) as total_layaway,
                COALESCE(SUM(total_sold), 0) as total_sold
            FROM products
        """)
        fg_row = cur.fetchone()
        
        total_physical = fg_row['total_physical'] or 0
        total_layaway = fg_row['total_layaway'] or 0
        total_sold = fg_row['total_sold'] or 0
        total_avail = total_physical - total_layaway - total_sold

        # Deficits / Shortages (where Layaway > Physical Stock)
        cur.execute("""
            SELECT category, item_code, description, 
                   (store_stock + factory_stock + white_house_stock + upstairs_stock + goods_received) as total_stock,
                   layaway_reserved,
                   (store_stock + factory_stock + white_house_stock + upstairs_stock + goods_received - layaway_reserved - total_sold) as available_stock
            FROM products
            WHERE layaway_reserved > (store_stock + factory_stock + white_house_stock + upstairs_stock + goods_received)
            ORDER BY (layaway_reserved - (store_stock + factory_stock + white_house_stock + upstairs_stock + goods_received)) DESC
        """)
        shortages = cur.fetchall()

        # Category summary
        cur.execute("""
            SELECT category,
                   COUNT(*) as sku_count,
                   COALESCE(SUM(store_stock + factory_stock + white_house_stock + upstairs_stock + goods_received), 0) as total_stock,
                   COALESCE(SUM(layaway_reserved), 0) as layaway_hold,
                   COALESCE(SUM(store_stock + factory_stock + white_house_stock + upstairs_stock + goods_received - layaway_reserved - total_sold), 0) as available_stock
            FROM products
            GROUP BY category
            ORDER BY category ASC
        """)
        category_summary = cur.fetchall()

        # Cleared orders ready for delivery (Active orders with zero balance)
        cur.execute("SELECT * FROM layaway_orders WHERE balance_due <= 0 AND status = 'Active' ORDER BY id DESC")
        cleared_orders = cur.fetchall()

        conn.close()

        return render_template(
            'dashboard.html',
            total_layaway_orders=total_orders,
            total_layaway_rev=total_rev,
            total_cash_collected=total_paid,
            total_receivables=total_bal,
            realized_rate=realized_rate,
            pending_rate=pending_rate,
            total_physical_stock=total_physical,
            total_layaway_units=total_layaway,
            total_available_stock=total_avail,
            shortages=shortages,
            category_summary=category_summary,
            cleared_orders=cleared_orders
        )

    # 2. Master Inventory & Category Filter View
    @app.route('/inventory')
    def inventory():
        category = request.args.get('category')
        conn = get_db()
        cur = conn.cursor()

        cur.execute("SELECT DISTINCT category FROM products WHERE category IS NOT NULL AND category != '' ORDER BY category ASC")
        categories = [r['category'] for r in cur.fetchall()]

        if category:
            cur.execute("SELECT * FROM products WHERE category = ? ORDER BY description ASC", (category,))
        else:
            cur.execute("SELECT * FROM products ORDER BY category ASC, description ASC")
        products = cur.fetchall()
        conn.close()

        return render_template('inventory.html', products=products, categories=categories, current_category=category)

    # 3. Master Layaways View
    @app.route('/layaways')
    def layaways():
        status_filter = request.args.get('status', 'Active')
        conn = get_db()
        cur = conn.cursor()

        if status_filter == 'All':
            cur.execute("SELECT * FROM layaway_orders ORDER BY date_created DESC, id DESC")
        else:
            cur.execute("SELECT * FROM layaway_orders WHERE status = ? ORDER BY date_created DESC, id DESC", (status_filter,))
        orders = cur.fetchall()

        cur.execute("SELECT COALESCE(SUM(total_amount), 0) as tot, COALESCE(SUM(amount_paid), 0) as pay, COALESCE(SUM(balance_due), 0) as bal FROM layaway_orders WHERE status = 'Active'")
        stats = cur.fetchone()
        conn.close()

        return render_template(
            'layaways.html',
            orders=orders,
            total_revenue=stats['tot'] or 0,
            total_paid=stats['pay'] or 0,
            total_balance=stats['bal'] or 0,
            current_status=status_filter
        )

    # 4. Goods Received (GRN) View
    @app.route('/goods-received')
    def goods_received_view():
        conn = get_db()
        cur = conn.cursor()
        cur.execute("SELECT * FROM goods_received ORDER BY date_received DESC, id DESC")
        receipts = cur.fetchall()
        conn.close()
        return render_template('goods_received.html', receipts=receipts)

    # 5. Direct Sales / POS View
    @app.route('/sales')
    def sales_view():
        conn = get_db()
        cur = conn.cursor()
        cur.execute("SELECT * FROM sales ORDER BY sale_date DESC, id DESC")
        sales_records = cur.fetchall()
        conn.close()
        return render_template('sales.html', sales_records=sales_records)

    # 6. Raw Materials View
    @app.route('/raw-materials')
    def raw_materials_view():
        conn = get_db()
        cur = conn.cursor()
        cur.execute("SELECT * FROM raw_materials ORDER BY description ASC")
        materials = cur.fetchall()
        conn.close()
        return render_template('raw_materials.html', materials=materials)

    # 7. Sync Hub View
    @app.route('/sync')
    def sync_view():
        conn = get_db()
        cur = conn.cursor()
        cur.execute("SELECT * FROM sync_logs ORDER BY timestamp DESC, id DESC LIMIT 50")
        logs = cur.fetchall()
        conn.close()
        status_info = check_connection_status()
        return render_template('sync.html', logs=logs, connection_status=status_info)

    # --- ACTION ENDPOINTS ---

    # Create Product
    @app.route('/api/products', methods=['POST'])
    def add_product():
        data = request.form
        category = data.get('category', 'Miscellaneous').strip()
        supplier = data.get('supplier', 'Local Supplier').strip()
        item_code = data.get('item_code', '').strip()
        description = data.get('description', '').strip()
        
        if not description:
            flash("Product description is required.", "error")
            return redirect(url_for('inventory'))

        try:
            store_stock = float(data.get('store_stock', 0) or 0)
            factory_stock = float(data.get('factory_stock', 0) or 0)
            white_house_stock = float(data.get('white_house_stock', 0) or 0)
            upstairs_stock = float(data.get('upstairs_stock', 0) or 0)
            unit_cost = float(data.get('unit_cost', 0) or 0)
            selling_price = float(data.get('selling_price', 0) or 0)
        except ValueError:
            flash("Invalid numeric format provided for stock or pricing.", "error")
            return redirect(url_for('inventory'))

        conn = get_db()
        cur = conn.cursor()
        try:
            cur.execute('''
                INSERT INTO products (category, supplier, item_code, description, store_stock, factory_stock, white_house_stock, upstairs_stock, unit_cost, selling_price, last_updated)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (category, supplier, item_code, description, store_stock, factory_stock, white_house_stock, upstairs_stock, unit_cost, selling_price, datetime.now().strftime('%Y-%m-%d')))
            conn.commit()
            flash(f"Product '{description}' added successfully.", "success")
        except Exception as e:
            conn.rollback()
            flash(f"Failed to add product: {str(e)}", "error")
        finally:
            conn.close()

        recalculate_inventory()
        return redirect(url_for('inventory'))

    # Update Stock / Physical Audit Adjustment
    @app.route('/api/products/adjust-stock', methods=['POST'])
    def adjust_product_stock():
        data = request.form
        product_id = data.get('product_id')
        try:
            store_stock = float(data.get('store_stock', 0) or 0)
            factory_stock = float(data.get('factory_stock', 0) or 0)
            white_house_stock = float(data.get('white_house_stock', 0) or 0)
            upstairs_stock = float(data.get('upstairs_stock', 0) or 0)
        except ValueError:
            flash("Invalid numbers provided for stock adjustment.", "error")
            return redirect(url_for('inventory'))

        conn = get_db()
        cur = conn.cursor()
        try:
            cur.execute('''
                UPDATE products 
                SET store_stock = ?, factory_stock = ?, white_house_stock = ?, upstairs_stock = ?, last_updated = ?
                WHERE id = ?
            ''', (store_stock, factory_stock, white_house_stock, upstairs_stock, datetime.now().strftime('%Y-%m-%d'), product_id))
            conn.commit()
            flash("Stock levels updated successfully.", "success")
        except Exception as e:
            conn.rollback()
            flash(f"Error adjusting stock: {str(e)}", "error")
        finally:
            conn.close()

        recalculate_inventory()
        return redirect(url_for('inventory'))

    # Record Direct Sale
    @app.route('/api/sales', methods=['POST'])
    def create_sale():
        data = request.form
        receipt_num = data.get('receipt_num', '').strip() or f"REC-{datetime.now().strftime('%Y%m%d%H%M%S')}"
        sale_date = data.get('sale_date', '').strip() or datetime.now().strftime('%Y-%m-%d')
        customer_name = data.get('customer_name', '').strip() or 'Walk-in Customer'
        contact_number = data.get('contact_number', '').strip()
        item_code = data.get('item_code', '').strip()
        item_description = data.get('item_description', '').strip()
        category = data.get('category', '').strip()
        payment_method = data.get('payment_method', 'Cash')
        fulfillment_location = data.get('fulfillment_location', 'STORE')
        sales_rep = data.get('sales_rep', '').strip()
        notes = data.get('notes', '').strip()

        if not item_description:
            flash("Item description is required for sales record.", "error")
            return redirect(url_for('sales_view'))

        try:
            quantity_sold = float(data.get('quantity_sold', 1) or 1)
            unit_price = float(data.get('unit_price', 0) or 0)
        except ValueError:
            flash("Invalid numeric value for quantity or unit price.", "error")
            return redirect(url_for('sales_view'))

        if quantity_sold <= 0:
            flash("Quantity sold must be greater than zero.", "error")
            return redirect(url_for('sales_view'))

        total_amount = quantity_sold * unit_price

        conn = get_db()
        cur = conn.cursor()
        try:
            cur.execute('''
                INSERT INTO sales (receipt_num, sale_date, customer_name, contact_number, item_code, item_description, category, quantity_sold, unit_price, total_amount, payment_method, fulfillment_location, sales_rep, notes)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (receipt_num, sale_date, customer_name, contact_number, item_code, item_description, category, quantity_sold, unit_price, total_amount, payment_method, fulfillment_location, sales_rep, notes))
            conn.commit()
            flash(f"Sale {receipt_num} recorded and stock updated.", "success")
        except Exception as e:
            conn.rollback()
            flash(f"Error recording sale: {str(e)}", "error")
        finally:
            conn.close()

        # Recalculate inventory
        recalculate_inventory()

        # Write-back to Google Sheet
        sync_sale_to_sheets(dict(data, total_amount=total_amount))

        return redirect(url_for('sales_view'))

    # Log Goods Received (GRN)
    @app.route('/api/goods-received', methods=['POST'])
    def create_grn():
        data = request.form
        grn_number = data.get('grn_number', '').strip() or f"GRN-{datetime.now().strftime('%Y%m%d%H%M%S')}"
        date_received = data.get('date_received', '').strip() or datetime.now().strftime('%Y-%m-%d')
        supplier = data.get('supplier', 'Supplier').strip()
        item_code = data.get('item_code', '').strip()
        item_description = data.get('item_description', '').strip()
        destination_location = data.get('destination_location', 'STORE')
        received_by = data.get('received_by', '').strip()
        notes = data.get('notes', '').strip()

        if not item_description:
            flash("Item description is required for GRN.", "error")
            return redirect(url_for('goods_received_view'))

        try:
            quantity_received = float(data.get('quantity_received', 0) or 0)
            unit_cost = float(data.get('unit_cost', 0) or 0)
        except ValueError:
            flash("Invalid quantity or unit cost.", "error")
            return redirect(url_for('goods_received_view'))

        if quantity_received <= 0:
            flash("Quantity received must be greater than zero.", "error")
            return redirect(url_for('goods_received_view'))

        total_cost = quantity_received * unit_cost

        conn = get_db()
        cur = conn.cursor()
        try:
            cur.execute('''
                INSERT INTO goods_received (grn_number, date_received, supplier, item_code, item_description, destination_location, quantity_received, unit_cost, total_cost, received_by, notes)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (grn_number, date_received, supplier, item_code, item_description, destination_location, quantity_received, unit_cost, total_cost, received_by, notes))
            conn.commit()
            flash(f"GRN {grn_number} logged successfully. Inventory stock increased by {quantity_received:g} units.", "success")
        except Exception as e:
            conn.rollback()
            flash(f"Error saving GRN: {str(e)}", "error")
        finally:
            conn.close()

        # Recalculate inventory
        recalculate_inventory()

        # Write-back to Google Sheet
        sync_goods_received_to_sheets(dict(data, total_cost=total_cost))

        return redirect(url_for('goods_received_view'))

    # Create Layaway Agreement
    @app.route('/api/layaways', methods=['POST'])
    def create_layaway():
        data = request.form
        invoice_num = data.get('invoice_num', '').strip()
        date_created = data.get('date_created', '').strip() or datetime.now().strftime('%Y-%m-%d')
        customer_name = data.get('customer_name', '').strip()
        contact_number = data.get('contact_number', '').strip()
        items_reserved_text = data.get('items_reserved_text', '').strip()

        if not invoice_num or not customer_name or not items_reserved_text:
            flash("Invoice #, Customer Name, and Items Reserved are required.", "error")
            return redirect(url_for('layaways'))

        try:
            total_amount = float(data.get('total_amount', 0) or 0)
            amount_paid = float(data.get('amount_paid', 0) or 0)
        except ValueError:
            flash("Invalid monetary amounts provided.", "error")
            return redirect(url_for('layaways'))

        if total_amount <= 0:
            flash("Total contract amount must be greater than zero.", "error")
            return redirect(url_for('layaways'))

        balance_due = max(0.0, total_amount - amount_paid)
        status = "Active"

        conn = get_db()
        cur = conn.cursor()
        try:
            cur.execute('''
                INSERT INTO layaway_orders (invoice_num, date_created, customer_name, contact_number, items_reserved_text, total_amount, amount_paid, balance_due, status)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (invoice_num, date_created, customer_name, contact_number, items_reserved_text, total_amount, amount_paid, balance_due, status))
            order_id = cur.lastrowid

            if amount_paid > 0:
                cur.execute('''
                    INSERT INTO layaway_payments (order_id, invoice_num, payment_date, amount, payment_method, received_by, receipt_num)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                ''', (order_id, invoice_num, date_created, amount_paid, 'Initial Deposit', 'Front Desk', f"DEP-{invoice_num}"))

            conn.commit()
            flash(f"Layaway agreement #{invoice_num} for {customer_name} created successfully.", "success")
        except sqlite3.IntegrityError:
            conn.rollback()
            flash(f"Invoice #{invoice_num} already exists in the system.", "error")
            conn.close()
            return redirect(url_for('layaways'))
        except Exception as e:
            conn.rollback()
            flash(f"Error creating layaway agreement: {str(e)}", "error")
            conn.close()
            return redirect(url_for('layaways'))
        finally:
            conn.close()

        # Recalculate inventory & layaway reservations
        recalculate_inventory()

        # Write-back to Google Sheet
        sync_layaway_to_sheets(dict(data, balance_due=balance_due))

        return redirect(url_for('layaways'))

    # Record Installment Payment
    @app.route('/api/layaways/payment', methods=['POST'])
    def record_payment():
        data = request.form
        invoice_num = data.get('invoice_num', '').strip()
        payment_method = data.get('payment_method', 'Cash')
        receipt_num = data.get('receipt_num', '').strip() or f"PAY-{datetime.now().strftime('%Y%m%d%H%M')}"
        received_by = data.get('received_by', '').strip() or 'Cashier'
        payment_date = datetime.now().strftime('%Y-%m-%d')

        try:
            amount = float(data.get('amount', 0) or 0)
        except ValueError:
            flash("Invalid payment amount entered.", "error")
            return redirect(url_for('layaways'))

        if amount <= 0:
            flash("Payment amount must be greater than zero.", "error")
            return redirect(url_for('layaways'))

        conn = get_db()
        cur = conn.cursor()
        try:
            cur.execute("SELECT id, amount_paid, total_amount, balance_due FROM layaway_orders WHERE invoice_num = ?", (invoice_num,))
            order = cur.fetchone()

            if not order:
                flash(f"Invoice #{invoice_num} not found.", "error")
                return redirect(url_for('layaways'))

            new_paid = order['amount_paid'] + amount
            new_bal = max(0.0, order['total_amount'] - new_paid)
            
            cur.execute('''
                UPDATE layaway_orders SET amount_paid = ?, balance_due = ? WHERE invoice_num = ?
            ''', (new_paid, new_bal, invoice_num))
            
            cur.execute('''
                INSERT INTO layaway_payments (order_id, invoice_num, payment_date, amount, payment_method, received_by, receipt_num)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            ''', (order['id'], invoice_num, payment_date, amount, payment_method, received_by, receipt_num))
            
            conn.commit()
            flash(f"Payment of JMD ${amount:,.2f} recorded for Invoice #{invoice_num}. New balance: JMD ${new_bal:,.2f}.", "success")
        except Exception as e:
            conn.rollback()
            flash(f"Failed to record payment: {str(e)}", "error")
        finally:
            conn.close()

        return redirect(url_for('layaways'))

    # Mark Layaway Order as Dispatched / Released (Releases Stock Hold)
    @app.route('/api/layaways/dispatch', methods=['POST'])
    def dispatch_layaway():
        invoice_num = request.form.get('invoice_num', '').strip()
        conn = get_db()
        cur = conn.cursor()
        try:
            cur.execute("SELECT id, customer_name, balance_due FROM layaway_orders WHERE invoice_num = ?", (invoice_num,))
            order = cur.fetchone()
            if not order:
                flash(f"Order #{invoice_num} not found.", "error")
            else:
                cur.execute("UPDATE layaway_orders SET status = 'Dispatched' WHERE invoice_num = ?", (invoice_num,))
                conn.commit()
                flash(f"Order #{invoice_num} for {order['customer_name']} marked as Dispatched. Stock hold has been released.", "success")
        except Exception as e:
            conn.rollback()
            flash(f"Error dispatching order: {str(e)}", "error")
        finally:
            conn.close()

        recalculate_inventory()
        return redirect(url_for('layaways'))

    # Create Raw Material Item (Fixed previously missing endpoint!)
    @app.route('/api/raw-materials', methods=['POST'])
    def add_raw_material():
        data = request.form
        item_code = data.get('item_code', '').strip()
        unit_type = data.get('unit_type', 'Boxes').strip() or 'Boxes'
        description = data.get('description', '').strip()

        if not description:
            flash("Description of goods is required.", "error")
            return redirect(url_for('raw_materials_view'))

        try:
            opening_stock = float(data.get('opening_stock', 0) or 0)
            unit_cost = float(data.get('unit_cost', 0) or 0)
            reorder_point = float(data.get('reorder_point', 5) or 5)
        except ValueError:
            flash("Invalid numeric value for stock or unit cost.", "error")
            return redirect(url_for('raw_materials_view'))

        current_stock = opening_stock

        conn = get_db()
        cur = conn.cursor()
        try:
            cur.execute('''
                INSERT INTO raw_materials (item_code, description, unit_cost, opening_stock, current_stock, goods_received, unit_type, reorder_point)
                VALUES (?, ?, ?, ?, ?, 0, ?, ?)
            ''', (item_code, description, unit_cost, opening_stock, current_stock, unit_type, reorder_point))
            conn.commit()
            flash(f"Raw material '{description}' added successfully.", "success")
        except Exception as e:
            conn.rollback()
            flash(f"Error adding raw material: {str(e)}", "error")
        finally:
            conn.close()

        recalculate_inventory()
        return redirect(url_for('raw_materials_view'))

    # Export Data to CSV
    @app.route('/api/export/<dataset>', methods=['GET'])
    def export_csv(dataset):
        conn = get_db()
        cur = conn.cursor()
        output = io.StringIO()
        writer = csv.writer(output)

        filename = f"TNCF_{dataset}_{datetime.now().strftime('%Y%m%d')}.csv"

        if dataset == 'inventory':
            writer.writerow(['Category', 'Item Code', 'Description', 'Store', 'Factory', 'White House', 'Upstairs', 'Goods Received', 'Total Physical Stock', 'Layaway Reserved', 'Total Sold', 'Net Available', 'Unit Cost (JMD)', 'Selling Price (JMD)'])
            cur.execute('''
                SELECT category, item_code, description, store_stock, factory_stock, white_house_stock, upstairs_stock, goods_received,
                       (store_stock + factory_stock + white_house_stock + upstairs_stock + goods_received) as physical,
                       layaway_reserved, total_sold,
                       (store_stock + factory_stock + white_house_stock + upstairs_stock + goods_received - layaway_reserved - total_sold) as avail,
                       unit_cost, selling_price
                FROM products ORDER BY category ASC, description ASC
            ''')
            for r in cur.fetchall():
                writer.writerow([r['category'], r['item_code'] or '', r['description'], r['store_stock'], r['factory_stock'], r['white_house_stock'], r['upstairs_stock'], r['goods_received'], r['physical'], r['layaway_reserved'], r['total_sold'], r['avail'], r['unit_cost'], r['selling_price']])

        elif dataset == 'layaways':
            writer.writerow(['Date', 'Invoice #', 'Customer Name', 'Contact Number', 'Items Reserved', 'Total Amount (JMD)', 'Amount Paid (JMD)', 'Balance Due (JMD)', 'Status'])
            cur.execute("SELECT date_created, invoice_num, customer_name, contact_number, items_reserved_text, total_amount, amount_paid, balance_due, status FROM layaway_orders ORDER BY id DESC")
            for r in cur.fetchall():
                writer.writerow([r['date_created'], r['invoice_num'], r['customer_name'], r['contact_number'] or '', r['items_reserved_text'], r['total_amount'], r['amount_paid'], r['balance_due'], r['status']])

        elif dataset == 'sales':
            writer.writerow(['Date', 'Receipt #', 'Customer Name', 'Phone', 'Item Code', 'Description', 'Category', 'Quantity Sold', 'Unit Price', 'Total Amount', 'Payment Method', 'Location', 'Sales Rep'])
            cur.execute("SELECT sale_date, receipt_num, customer_name, contact_number, item_code, item_description, category, quantity_sold, unit_price, total_amount, payment_method, fulfillment_location, sales_rep FROM sales ORDER BY id DESC")
            for r in cur.fetchall():
                writer.writerow([r['sale_date'], r['receipt_num'], r['customer_name'], r['contact_number'] or '', r['item_code'] or '', r['item_description'], r['category'] or '', r['quantity_sold'], r['unit_price'], r['total_amount'], r['payment_method'], r['fulfillment_location'], r['sales_rep'] or ''])

        elif dataset == 'goods-received':
            writer.writerow(['Date', 'GRN #', 'Supplier', 'Item Code', 'Description', 'Destination', 'Quantity Received', 'Unit Cost', 'Total Cost', 'Received By'])
            cur.execute("SELECT date_received, grn_number, supplier, item_code, item_description, destination_location, quantity_received, unit_cost, total_cost, received_by FROM goods_received ORDER BY id DESC")
            for r in cur.fetchall():
                writer.writerow([r['date_received'], r['grn_number'], r['supplier'], r['item_code'] or '', r['item_description'], r['destination_location'], r['quantity_received'], r['unit_cost'], r['total_cost'], r['received_by'] or ''])

        elif dataset == 'raw-materials':
            writer.writerow(['Item Code', 'Description', 'Unit Type', 'Opening Stock', 'Goods Received', 'Current Stock', 'Unit Cost', 'Total Valuation', 'Reorder Point'])
            cur.execute("SELECT item_code, description, unit_type, opening_stock, goods_received, current_stock, unit_cost, (unit_cost * current_stock) as val, reorder_point FROM raw_materials ORDER BY description ASC")
            for r in cur.fetchall():
                writer.writerow([r['item_code'] or '', r['description'], r['unit_type'], r['opening_stock'], r['goods_received'], r['current_stock'], r['unit_cost'], r['val'], r['reorder_point']])

        else:
            conn.close()
            return "Unknown dataset", 404

        conn.close()
        output.seek(0)
        return Response(
            output.getvalue(),
            mimetype="text/csv",
            headers={"Content-Disposition": f"attachment;filename={filename}"}
        )

    # Manual Re-calculation
    @app.route('/api/sync/recalculate', methods=['POST'])
    def trigger_recalculate():
        recalculate_inventory()
        flash("Full inventory recalculation and layaway multiplier tally completed.", "success")
        return redirect(url_for('sync_view'))

    return app

app = create_app()

if __name__ == '__main__':
    if app:
        app.run(host='0.0.0.0', port=5000, debug=True)
    else:
        print("Flask is not installed in the current environment. To run locally, run 'pip install -r requirements.txt && python app.py'")
