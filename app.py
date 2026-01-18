from flask import Flask, render_template, request, redirect, url_for, flash, send_file, make_response, jsonify
import sqlite3
import os
import io
import json
import time
from urllib.parse import quote_plus
from dotenv import load_dotenv
import razorpay

# Load environment variables
load_dotenv()

app = Flask(__name__)
app.secret_key = "dev_secret_key_change_in_production"  # Required for flash messages
DB = "cart.db"
PRODUCT_FILE = "products.txt"

# Razorpay Configuration
RAZORPAY_KEY_ID = os.getenv("RAZORPAY_KEY_ID", "")
RAZORPAY_KEY_SECRET = os.getenv("RAZORPAY_KEY_SECRET", "")

# Initialize Razorpay client
razorpay_client = None
if RAZORPAY_KEY_ID and RAZORPAY_KEY_SECRET:
    razorpay_client = razorpay.Client(auth=(RAZORPAY_KEY_ID, RAZORPAY_KEY_SECRET))
else:
    print("WARNING: Razorpay keys not found. Payment gateway will not work.")

# Unsplash API Configuration
# Get your free API key from: https://unsplash.com/developers
# Leave empty to use fallback placeholder images
UNSPLASH_ACCESS_KEY = "5ElYz9cuxORwhY9pfQLnBa6myl7C_4TCWTRmIMQ6SP8"  # PUT YOUR UNSPLASH API KEY HERE

# ---------- LOAD PRODUCTS ----------
def load_products():
    """Load products from CSV file with error handling."""
    products = {}
    
    if not os.path.exists(PRODUCT_FILE):
        print(f"WARNING: {PRODUCT_FILE} not found. Using empty product catalog.")
        return products
    
    try:
        with open(PRODUCT_FILE, "r", encoding="utf-8") as f:
            for line_num, line in enumerate(f, start=1):
                line = line.strip()
                
                # Skip empty lines
                if not line:
                    continue
                
                # Parse CSV with validation
                parts = line.split(",")
                if len(parts) != 3:
                    print(f"WARNING: Skipping malformed line {line_num} in {PRODUCT_FILE}: {line}")
                    continue
                
                barcode, name, price_str = parts
                
                # Validate barcode
                if not barcode.strip():
                    print(f"WARNING: Skipping line {line_num} - empty barcode")
                    continue
                
                # Validate price
                try:
                    price = int(price_str.strip())
                    if price < 0:
                        print(f"WARNING: Negative price for {barcode}, using absolute value")
                        price = abs(price)
                except ValueError:
                    print(f"WARNING: Invalid price '{price_str}' for {barcode}, skipping")
                    continue
                
                products[barcode.strip()] = {
                    "name": name.strip(),
                    "price": price
                }
    
    except Exception as e:
        print(f"ERROR: Failed to load products from {PRODUCT_FILE}: {e}")
        return {}
    
    return products

# ---------- DATABASE ----------
def get_db():
    """Get database connection."""
    try:
        return sqlite3.connect(DB)
    except sqlite3.Error as e:
        print(f"ERROR: Database connection failed: {e}")
        raise

def init_db():
    """Initialize database with cart table."""
    try:
        with get_db() as con:
            con.execute("""
            CREATE TABLE IF NOT EXISTS cart (
                barcode TEXT PRIMARY KEY,
                name TEXT,
                price INTEGER,
                qty INTEGER
            )
            """)
            con.execute("""
            CREATE TABLE IF NOT EXISTS orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                order_number TEXT UNIQUE,
                total INTEGER,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                status TEXT DEFAULT 'active'
            )
            """)
            con.commit()
    except sqlite3.Error as e:
        print(f"ERROR: Database initialization failed: {e}")
        raise

init_db()

# ---------- HELPER: Generate Order Number ----------
import random
import time

def generate_order_number():
    """Generate unique order number like #1001, #1002, etc."""
    timestamp_suffix = int(time.time()) % 1000
    random_suffix = random.randint(100, 999)
    return f"{timestamp_suffix}{random_suffix}"

# ---------- HOME / SCAN ----------
@app.route("/", methods=["GET", "POST"])
def home():
    products = load_products()

    if request.method == "POST":
        barcode = request.form.get("barcode", "").strip()
        # Get quantity from form, default to 1 if not provided (for barcode scanner)
        try:
            qty = int(request.form.get("qty", "1"))
            if qty < 1:
                qty = 1
        except (ValueError, TypeError):
            qty = 1

        # Validate barcode input
        if not barcode:
            # Empty barcode, just refresh page (common when scanner misfires)
            return redirect(url_for("home"))

        if barcode in products:
            p = products[barcode]
            try:
                with get_db() as con:
                    cur = con.cursor()
                    cur.execute("SELECT qty FROM cart WHERE barcode=?", (barcode,))
                    row = cur.fetchone()

                    if row:
                        cur.execute(
                            "UPDATE cart SET qty = qty + ? WHERE barcode=?",
                            (qty, barcode)
                        )
                    else:
                        cur.execute(
                            "INSERT INTO cart VALUES (?, ?, ?, ?)",
                            (barcode, p["name"], p["price"], qty)
                        )
                    con.commit()
            except sqlite3.Error as e:
                print(f"ERROR: Database operation failed for barcode {barcode}: {e}")
                flash("Error adding item to cart. Please try again.", "error")
        else:
            # Unknown barcode - could log this for inventory management
            print(f"WARNING: Unknown barcode scanned: {barcode}")
            flash(f"Product not found: {barcode}", "warning")

        return redirect(url_for("home"))

    # GET request - display cart
    try:
        with get_db() as con:
            cart = con.execute("SELECT * FROM cart").fetchall()
            total = sum(i[2] * i[3] for i in cart) if cart else 0
    except sqlite3.Error as e:
        print(f"ERROR: Failed to load cart: {e}")
        cart = []
        total = 0
        flash("Error loading cart. Please refresh.", "error")

    return render_template("cart.html", cart=cart, total=total, order_number=generate_order_number())

# ---------- SHOP (Product Gallery) ----------
@app.route("/shop")
def shop():
    """Display product gallery for manual testing/shopping."""
    from datetime import datetime
    products = load_products()
    
    # Convert products dict to list for template
    product_list = []
    for barcode, details in products.items():
        product_list.append({
            "barcode": barcode,
            "name": details["name"],
            "price": details["price"]
        })
    
    # Get current date and time
    now = datetime.now()
    current_date = now.strftime("%a, %d %b %Y")
    current_time = now.strftime("%I:%M %p")
    
    return render_template("shop.html", products=product_list, unsplash_key=UNSPLASH_ACCESS_KEY, 
                          current_date=current_date, current_time=current_time)

@app.route("/add-to-cart/<barcode>")
def add_to_cart(barcode):
    """Add product to cart from shop page (same logic as barcode scanning)."""
    products = load_products()
    
    if barcode not in products:
        flash(f"Product not found: {barcode}", "error")
        return redirect(url_for("shop"))
    
    product = products[barcode]
    
    try:
        with get_db() as con:
            # Check if already in cart
            existing = con.execute("SELECT qty FROM cart WHERE barcode=?", (barcode,)).fetchone()
            
            if existing:
                # Increment quantity
                con.execute("UPDATE cart SET qty = qty + 1 WHERE barcode=?", (barcode,))
                flash(f"Added another {product['name']} to cart!", "success")
            else:
                # Add new item
                con.execute(
                    "INSERT INTO cart (barcode, name, price, qty) VALUES (?, ?, ?, ?)",
                    (barcode, product["name"], product["price"], 1)
                )
                flash(f"Added {product['name']} to cart!", "success")
            
            con.commit()
    except sqlite3.Error as e:
        print(f"ERROR: Failed to add to cart: {e}")
        flash("Error adding to cart. Please try again.", "error")
    
    return redirect(url_for("shop"))

# ---------- PRODUCT IMAGE (Local endpoint with optional Unsplash caching) ----------
def _svg_placeholder(text: str) -> bytes:
    """Generate a simple SVG placeholder with gradient background and centered text."""
    safe_text = (text or "").replace("<", "&lt;").replace(">", "&gt;")
    svg = f"""
    <svg xmlns='http://www.w3.org/2000/svg' width='400' height='300'>
      <defs>
        <linearGradient id='g' x1='0' x2='1' y1='0' y2='1'>
          <stop offset='0%' stop-color='#667eea'/>
          <stop offset='100%' stop-color='#764ba2'/>
        </linearGradient>
      </defs>
      <rect width='100%' height='100%' fill='url(#g)'/>
      <text x='50%' y='50%' dominant-baseline='middle' text-anchor='middle'
            font-family='Arial, Helvetica, sans-serif' font-size='24' fill='#ffffff'>
        {safe_text}
      </text>
    </svg>
    """.strip()
    return svg.encode("utf-8")

@app.route("/product-image/<barcode>")
def product_image(barcode: str):
    """Serve a product image without relying on cross-origin image hosts.

    Strategy:
      1) If a cached image exists in static/cache_images/<barcode>.jpg -> serve it
      2) If UNSPLASH_ACCESS_KEY is provided and 'requests' is available -> fetch, cache, serve
      3) Otherwise, serve a local SVG placeholder with the product name
    """
    products = load_products()
    details = products.get(barcode)

    # Fallback text if product not found
    name = details.get("name") if details else "Product"

    cache_dir = os.path.join("static", "cache_images")
    os.makedirs(cache_dir, exist_ok=True)
    cache_path = os.path.join(cache_dir, f"{barcode}.jpg")

    # 1) Serve cached image if present
    if os.path.exists(cache_path):
        try:
            return send_file(cache_path, mimetype="image/jpeg")
        except Exception as e:
            print(f"WARNING: Failed to send cached image {cache_path}: {e}")

    # 2) Try to fetch from Unsplash if key provided and 'requests' available
    if UNSPLASH_ACCESS_KEY:
        try:
            import requests  # Lazy import in case it's not installed

            query = quote_plus(name)
            api_url = (
                f"https://api.unsplash.com/search/photos?query={query}&per_page=1&content_filter=high"
                f"&orientation=landscape&client_id={UNSPLASH_ACCESS_KEY}"
            )
            r = requests.get(api_url, timeout=8)
            if r.status_code == 200:
                data = r.json()
                results = data.get("results", [])
                if results:
                    image_url = results[0].get("urls", {}).get("small") or results[0].get("urls", {}).get("regular")
                    if image_url:
                        img = requests.get(image_url, timeout=10)
                        if img.status_code == 200:
                            try:
                                with open(cache_path, "wb") as f:
                                    f.write(img.content)
                                return send_file(cache_path, mimetype="image/jpeg")
                            except Exception as e:
                                print(f"WARNING: Failed to cache image for {barcode}: {e}")
        except Exception as e:
            # Do not crash the app; fall back to SVG
            print(f"WARNING: Unsplash fetch failed for '{name}': {e}")

    # 3) Fallback: Serve SVG placeholder with product name
    svg_bytes = _svg_placeholder(name)
    resp = make_response(svg_bytes)
    resp.headers["Content-Type"] = "image/svg+xml"
    return resp

# ---------- INC / DEC ----------
@app.route("/update/<barcode>/<action>")
def update(barcode, action):
    """Update item quantity in cart."""
    # Validate action
    if action not in ["inc", "dec"]:
        flash(f"Invalid action: {action}", "error")
        return redirect(url_for("home"))
    
    try:
        with get_db() as con:
            if action == "inc":
                con.execute(
                    "UPDATE cart SET qty = qty + 1 WHERE barcode=?", (barcode,)
                )
            elif action == "dec":
                con.execute(
                    "UPDATE cart SET qty = qty - 1 WHERE barcode=?", (barcode,)
                )
                con.execute("DELETE FROM cart WHERE qty <= 0")
            con.commit()
    except sqlite3.Error as e:
        print(f"ERROR: Failed to update quantity for {barcode}: {e}")
        flash("Error updating cart. Please try again.", "error")

    return redirect(url_for("home"))

# ---------- DELETE ----------
@app.route("/delete/<barcode>")
def delete(barcode):
    """Remove item from cart entirely."""
    try:
        with get_db() as con:
            con.execute("DELETE FROM cart WHERE barcode=?", (barcode,))
            con.commit()
    except sqlite3.Error as e:
        print(f"ERROR: Failed to delete item {barcode}: {e}")
        flash("Error removing item from cart.", "error")
    
    return redirect(url_for("home"))

# ---------- CHECKOUT ----------
@app.route("/checkout", methods=["GET"])
def checkout():
    """Display checkout page."""
    try:
        with get_db() as con:
            cart = con.execute("SELECT * FROM cart").fetchall()
            total = sum(i[2] * i[3] for i in cart) if cart else 0
    except sqlite3.Error as e:
        print(f"ERROR: Failed to load cart for checkout: {e}")
        flash("Error loading checkout. Please try again.", "error")
        return redirect(url_for("home"))

    # Handle empty cart
    if not cart:
        flash("Your cart is empty. Please add items before checkout.", "info")
        return redirect(url_for("home"))

    # Calculate final amount (subtotal + 10% tax - 5% discount)
    final_amount = int(total * 1.05)  # Convert to paise for Razorpay (multiply by 100)
    
    return render_template("checkout.html", cart=cart, total=total, 
                         final_amount=final_amount, razorpay_key_id=RAZORPAY_KEY_ID)

# ---------- CREATE RAZORPAY ORDER ----------
@app.route("/create-order", methods=["POST"])
def create_order():
    """Create a Razorpay order for payment."""
    if not razorpay_client:
        return jsonify({"error": "Razorpay not configured"}), 500
    
    try:
        with get_db() as con:
            cart = con.execute("SELECT * FROM cart").fetchall()
            if not cart:
                return jsonify({"error": "Cart is empty"}), 400
            
            total = sum(i[2] * i[3] for i in cart)
            final_amount = int(total * 1.05)  # Subtotal + tax - discount
            
            # Get customer details from request
            customer_name = request.json.get("customer_name", "Customer")
            customer_phone = request.json.get("customer_phone", "")
            order_notes = request.json.get("order_notes", "")
            
            # Create order in Razorpay (amount in paise)
            order_amount = final_amount * 100  # Convert to paise
            
            order_data = {
                "amount": order_amount,
                "currency": "INR",
                "receipt": f"order_{generate_order_number()}",
                "notes": {
                    "customer_name": customer_name,
                    "customer_phone": customer_phone,
                    "order_notes": order_notes,
                    "items": len(cart)
                }
            }
            
            order = razorpay_client.order.create(data=order_data)
            
            return jsonify({
                "order_id": order["id"],
                "amount": order_amount,
                "currency": "INR",
                "key_id": RAZORPAY_KEY_ID
            })
            
    except Exception as e:
        print(f"ERROR: Failed to create Razorpay order: {e}")
        return jsonify({"error": str(e)}), 500

# ---------- PAYMENT SUCCESS CALLBACK ----------
@app.route("/payment-success", methods=["POST"])
def payment_success():
    """Handle successful payment callback from Razorpay."""
    try:
        # Get payment details from request
        payment_data = request.json
        razorpay_payment_id = payment_data.get("razorpay_payment_id")
        razorpay_order_id = payment_data.get("razorpay_order_id")
        razorpay_signature = payment_data.get("razorpay_signature")
        
        if not razorpay_client:
            return jsonify({"success": False, "error": "Payment gateway not configured.", "redirect": url_for("home")}), 500
        
        # Verify payment signature
        params_dict = {
            "razorpay_order_id": razorpay_order_id,
            "razorpay_payment_id": razorpay_payment_id,
            "razorpay_signature": razorpay_signature
        }
        
        try:
            razorpay_client.utility.verify_payment_signature(params_dict)
            
            # Payment verified - clear cart and save order
            with get_db() as con:
                cart = con.execute("SELECT * FROM cart").fetchall()
                total = sum(i[2] * i[3] for i in cart) if cart else 0
                final_amount = int(total * 1.05)
                
                # Save order to database
                order_number = generate_order_number()
                con.execute("""
                    INSERT INTO orders (order_number, total, status)
                    VALUES (?, ?, ?)
                """, (order_number, final_amount, "completed"))
                
                # Clear cart
                con.execute("DELETE FROM cart")
                con.commit()
            
            # Store success message in session for flash
            flash(f"Payment successful! Order #{order_number}. Amount: ₹{final_amount}", "success")
            return jsonify({
                "success": True,
                "message": f"Payment successful! Order #{order_number}",
                "redirect": url_for("home")
            })
            
        except razorpay.errors.SignatureVerificationError:
            flash("Payment verification failed. Please contact support.", "error")
            return jsonify({
                "success": False,
                "error": "Payment verification failed. Please contact support.",
                "redirect": url_for("checkout")
            }), 400
            
    except Exception as e:
        print(f"ERROR: Payment success handler failed: {e}")
        flash("Error processing payment. Please contact support.", "error")
        return jsonify({
            "success": False,
            "error": "Error processing payment. Please contact support.",
            "redirect": url_for("checkout")
        }), 500

# ---------- PAYMENT FAILURE CALLBACK ----------
@app.route("/payment-failure", methods=["POST"])
def payment_failure():
    """Handle failed payment callback from Razorpay."""
    try:
        error_data = request.json
        error_code = error_data.get("error", {}).get("code", "UNKNOWN")
        error_description = error_data.get("error", {}).get("description", "Payment failed")
        
        flash(f"Payment failed: {error_description}", "error")
        return jsonify({
            "success": False,
            "error": error_description,
            "redirect": url_for("checkout")
        })
        
    except Exception as e:
        print(f"ERROR: Payment failure handler failed: {e}")
        flash("Payment failed. Please try again.", "error")
        return jsonify({
            "success": False,
            "error": "Payment failed. Please try again.",
            "redirect": url_for("checkout")
        }), 500

if __name__ == "__main__":
    app.run(host="192.168.0.136", port=5000, debug=True)