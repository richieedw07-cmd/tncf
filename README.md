# The New City Furniture - Operations, Inventory & Layaway Web App

A cross-platform web application (Progressive Web App) engineered for **The New City Furniture Co. Ltd.** to manage multi-location inventory, master customer layaways, goods received (GRN), point-of-sale direct sales, and raw materials, with bidirectional Google Sheets synchronization.

---

## Key Features & Operational Capabilities

* **Cross-Platform & Mobile PWA**: Runs in any browser on iPhone, iPad, Android, and PC. Can be installed directly to home screens via "Add to Home Screen" for a full-screen, native app experience.
* **Executive KPI Dashboard**: Live tracking of contracted revenue, cash collected, outstanding receivables, physical stock, dynamic realization rates, and instant deficit alerts for prioritized production.
* **Master Layaway Management**:
  * Tracks customer installment balances, deposit history, and balance due.
  * Intelligent multi-format multiplier parsing (`x 2`, `2 x`, `+`, inline sets).
  * Status lifecycle management: Active orders hold reserved stock; paid orders can be marked **Dispatched**, immediately releasing inventory allocations.
* **Multi-Location Inventory**: Live stock levels across `STORE`, `FACTORY`, `WHITE HOUSE`, and `UPSTAIRS`, with category filtering and an in-app physical stock adjustment modal for warehouse floor audits.
* **Goods Received (GRN Log)**: Record incoming factory batches and supplier shipments with automatic inventory replenishment.
* **Point of Sale (POS)**: Process cash, card, bank transfer, and WiPay direct retail sales with automatic stock deduction.
* **Raw Materials Tracking**: Monitor manufacturing supplies (mattress tape, staples, springs, adhesives) with opening stock, current balance, reorder thresholds, and inventory valuation.
* **Universal CSV Data Export**: 1-click download of live datasets (Master Inventory, Layaway Orders, Sales History, Goods Received, and Raw Materials) directly to CSV/Excel spreadsheets.
* **Google Sheets Real-Time Sync**: Bi-directional integration with your master Google Spreadsheet (`181JZpOItrdXFTJNYYjug8HRWKaJu_EsoHB3jT_R0DrA`) with connection status diagnostics and logging.
* **Resilient Architecture**: Automatic database locking fallback and idempotence protection against double-counting.

---

## Quick Start (Local Setup)

1. **Extract and Navigate to Directory**:
   ```bash
   cd tncf_inventory_app
   ```

2. **Install Dependencies**:
   ```bash
   pip install -r requirements.txt
   ```

3. **Seed Initial Database**:
   ```bash
   python seed_data.py
   ```

4. **Run Automated Test Suite**:
   ```bash
   python test_tncf_app.py
   ```

5. **Launch Application**:
   ```bash
   python app.py
   ```
   Open your browser to `http://localhost:5000`.

---

## 1-Click Cloud Deployment (Render / Railway / Cloud)

1. Upload the `tncf_inventory_app` repository to GitHub.
2. Link the repository to **Render** or **Railway** as a Web Service.
3. The build command will automatically run `pip install -r requirements.txt && python seed_data.py`, and start with `gunicorn app:app`.

---

## Installing on iPhone & Android (PWA)

* **iOS (iPhone / iPad)**: Open Safari, navigate to the web app URL, tap the **Share** button, and select **"Add to Home Screen"**.
* **Android**: Open Chrome, navigate to the web app URL, tap the **Three Dots Menu**, and select **"Install app"** or **"Add to Home screen"**.
