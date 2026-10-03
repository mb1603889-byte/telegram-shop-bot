import os
import sqlite3
import logging
from pathlib import Path
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler, MessageHandler,
    ContextTypes, filters
)

BOT_TOKEN = os.environ["BOT_TOKEN"]
ADMIN_ID = int(os.environ["ADMIN_ID"])
DB_PATH = os.environ.get("DB_PATH", "shop.db")
PRODUCT_DIR = Path("products")
PRODUCT_DIR.mkdir(exist_ok=True)

logging.basicConfig(level=logging.INFO)

def db():
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    return con

def init_db():
    con = db()
    cur = con.cursor()
    cur.executescript("""
    CREATE TABLE IF NOT EXISTS users(
        user_id INTEGER PRIMARY KEY,
        username TEXT,
        balance REAL DEFAULT 0
    );
    CREATE TABLE IF NOT EXISTS products(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        price REAL NOT NULL,
        description TEXT DEFAULT '',
        delivery TEXT DEFAULT '',
        file_id TEXT DEFAULT '',
        active INTEGER DEFAULT 1
    );
    CREATE TABLE IF NOT EXISTS deposits(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        amount REAL,
        trx TEXT,
        status TEXT DEFAULT 'pending'
    );
    CREATE TABLE IF NOT EXISTS orders(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        product_id INTEGER,
        price REAL,
        status TEXT DEFAULT 'completed'
    );
    """)
    con.commit()
    con.close()

def ensure_user(tg_user):
    con = db()
    con.execute(
        "INSERT OR IGNORE INTO users(user_id, username) VALUES(?,?)",
        (tg_user.id, tg_user.username or "")
    )
    con.execute("UPDATE users SET username=? WHERE user_id=?",
                (tg_user.username or "", tg_user.id))
    con.commit()
    con.close()

def main_menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🛍️ Products", callback_data="products")],
        [InlineKeyboardButton("💰 Balance", callback_data="balance"),
         InlineKeyboardButton("➕ Add Money", callback_data="addmoney")],
        [InlineKeyboardButton("📦 My Orders", callback_data="orders"),
         InlineKeyboardButton("👤 Profile", callback_data="profile")]
    ])

def admin_menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ Add Product", callback_data="admin_add")],
        [InlineKeyboardButton("📦 Products", callback_data="admin_products")],
        [InlineKeyboardButton("💳 Pending Deposits", callback_data="admin_deposits")],
        [InlineKeyboardButton("📋 Orders", callback_data="admin_orders")]
    ])

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ensure_user(update.effective_user)
    context.user_data.clear()
    await update.message.reply_text(
        "🛒 Welcome to the Shop Bot!\n\nপণ্য কিনতে Products নির্বাচন করুন।",
        reply_markup=main_menu()
    )

async def admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("❌ Admin only.")
        return
    context.user_data.clear()
    await update.message.reply_text("👑 Admin Panel", reply_markup=admin_menu())

async def show_products(update, context):
    con = db()
    rows = con.execute(
        "SELECT * FROM products WHERE active=1 ORDER BY id DESC"
    ).fetchall()
    con.close()
    if not rows:
        await update.effective_message.reply_text(
            "📭 এখন কোনো পণ্য নেই।", reply_markup=main_menu()
        )
        return
    buttons = [
        [InlineKeyboardButton(f"{r['name']} — {r['price']:.2f}৳",
                              callback_data=f"view:{r['id']}")]
        for r in rows
    ]
    buttons.append([InlineKeyboardButton("🔙 Back", callback_data="home")])
    await update.effective_message.reply_text(
        "🛍️ Available Products:", reply_markup=InlineKeyboardMarkup(buttons)
    )

async def buy_product(query, product_id):
    user_id = query.from_user.id
    con = db()
    product = con.execute(
        "SELECT * FROM products WHERE id=? AND active=1", (product_id,)
    ).fetchone()
    user = con.execute("SELECT balance FROM users WHERE user_id=?", (user_id,)).fetchone()

    if not product:
        con.close()
        await query.answer("Product not found.", show_alert=True)
        return

    if not user or user["balance"] < product["price"]:
        con.close()
        await query.answer("❌ পর্যাপ্ত balance নেই।", show_alert=True)
        return

    new_balance = user["balance"] - product["price"]
    cur = con.cursor()
    cur.execute("UPDATE users SET balance=? WHERE user_id=?",
                (new_balance, user_id))
    cur.execute(
        "INSERT INTO orders(user_id, product_id, price) VALUES(?,?,?)",
        (user_id, product_id, product["price"])
    )
    order_id = cur.lastrowid
    con.commit()
    con.close()

    await query.answer("✅ Purchase successful!")
    await query.message.reply_text(
        f"✅ Order #{order_id} successful!\n"
        f"🛍️ {product['name']}\n"
        f"💰 Paid: {product['price']:.2f}৳\n"
        f"💳 Remaining balance: {new_balance:.2f}৳"
    )

    if product["file_id"]:
        try:
            await query.message.reply_document(
                document=product["file_id"],
                caption=f"📦 আপনার পণ্য — Order #{order_id}"
            )
        except Exception:
            await query.message.reply_text("⚠️ File delivery failed. Admin-এর সাথে যোগাযোগ করুন।")
    elif product["delivery"]:
        await query.message.reply_text(
            f"📦 Product Delivery:\n\n{product['delivery']}"
        )
    else:
        await query.message.reply_text("📦 Product content পাওয়া যায়নি।")

    try:
        await query.get_bot().send_message(
            ADMIN_ID,
            f"🛒 New Order #{order_id}\n"
            f"User: {query.from_user.id}\n"
            f"Product: {product['name']}\n"
            f"Price: {product['price']:.2f}৳"
        )
    except Exception:
        pass

async def callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    ensure_user(q.from_user)
    data = q.data

    if data == "home":
        await q.message.edit_text("🏠 Main Menu", reply_markup=main_menu())

    elif data == "products":
        await show_products(update, context)

    elif data.startswith("view:"):
        pid = int(data.split(":")[1])
        con = db()
        p = con.execute("SELECT * FROM products WHERE id=? AND active=1", (pid,)).fetchone()
        con.close()
        if not p:
            await q.message.reply_text("❌ Product not found.")
            return
        text = f"🛍️ {p['name']}\n\n💰 Price: {p['price']:.2f}৳\n"
        if p["description"]:
            text += f"\n📝 {p['description']}\n"
        kb = [
            [InlineKeyboardButton("🛒 BUY NOW", callback_data=f"buy:{pid}")],
            [InlineKeyboardButton("🔙 Products", callback_data="products")]
        ]
        await q.message.reply_text(text, reply_markup=InlineKeyboardMarkup(kb))

    elif data.startswith("buy:"):
        await buy_product(q, int(data.split(":")[1]))

    elif data == "balance":
        con = db()
        r = con.execute("SELECT balance FROM users WHERE user_id=?", (q.from_user.id,)).fetchone()
        con.close()
        await q.message.reply_text(f"💰 Your Balance: {r['balance']:.2f}৳",
                                   reply_markup=main_menu())

    elif data == "profile":
        con = db()
        r = con.execute("SELECT * FROM users WHERE user_id=?", (q.from_user.id,)).fetchone()
        con.close()
        await q.message.reply_text(
            f"👤 Profile\n\n🆔 ID: {q.from_user.id}\n"
            f"👤 Username: @{r['username'] or 'none'}\n"
            f"💰 Balance: {r['balance']:.2f}৳",
            reply_markup=main_menu()
        )

    elif data == "orders":
        con = db()
        rows = con.execute("""
            SELECT o.id, p.name, o.price, o.status
            FROM orders o JOIN products p ON p.id=o.product_id
            WHERE o.user_id=? ORDER BY o.id DESC LIMIT 20
        """, (q.from_user.id,)).fetchall()
        con.close()
        if not rows:
            await q.message.reply_text("📭 কোনো order নেই.", reply_markup=main_menu())
            return
        text = "📦 My Orders\n\n" + "\n".join(
            f"#{r['id']} • {r['name']} • {r['price']:.2f}৳ • {r['status']}"
            for r in rows
        )
        await q.message.reply_text(text, reply_markup=main_menu())

    elif data == "addmoney":
        context.user_data["state"] = "deposit_amount"
        await q.message.reply_text(
            "➕ Add Money\n\nকত টাকা add করবেন লিখুন।\nউদাহরণ: 100"
        )

    elif data.startswith("dep:") and q.from_user.id == ADMIN_ID:
        dep_id = int(data.split(":")[1])
        action = data.split(":")[2]
        await process_deposit(q, dep_id, action)

    elif data == "admin_add" and q.from_user.id == ADMIN_ID:
        context.user_data.clear()
        context.user_data["state"] = "product_name"
        await q.message.reply_text("Product name লিখুন:")

    elif data == "admin_products" and q.from_user.id == ADMIN_ID:
        await admin_products(q)

    elif data == "admin_deposits" and q.from_user.id == ADMIN_ID:
        await admin_deposits(q)

    elif data == "admin_orders" and q.from_user.id == ADMIN_ID:
        await admin_orders(q)

async def process_deposit(q, dep_id, action):
    con = db()
    dep = con.execute("SELECT * FROM deposits WHERE id=?", (dep_id,)).fetchone()
    if not dep or dep["status"] != "pending":
        con.close()
        await q.message.reply_text("⚠️ Request already processed/not found.")
        return
    if action == "approve":
        con.execute("UPDATE deposits SET status='approved' WHERE id=?", (dep_id,))
        con.execute("UPDATE users SET balance=balance+? WHERE user_id=?",
                    (dep["amount"], dep["user_id"]))
        con.commit()
        msg = f"✅ আপনার {dep['amount']:.2f}৳ Add Money approved হয়েছে।"
    else:
        con.execute("UPDATE deposits SET status='rejected' WHERE id=?", (dep_id,))
        con.commit()
        msg = f"❌ আপনার {dep['amount']:.2f}৳ Add Money request rejected হয়েছে।"
    con.close()
    await q.message.reply_text("✅ Done.")
    try:
        await q.get_bot().send_message(dep["user_id"], msg)
    except Exception:
        pass

async def admin_deposits(q):
    con = db()
    rows = con.execute(
        "SELECT * FROM deposits WHERE status='pending' ORDER BY id"
    ).fetchall()
    con.close()
    if not rows:
        await q.message.reply_text("📭 Pending deposit নেই.", reply_markup=admin_menu())
        return
    for r in rows:
        kb = [[
            InlineKeyboardButton("✅ Approve", callback_data=f"dep:{r['id']}:approve"),
            InlineKeyboardButton("❌ Reject", callback_data=f"dep:{r['id']}:reject")
        ]]
        await q.message.reply_text(
            f"💳 Deposit #{r['id']}\n👤 User: {r['user_id']}\n"
            f"💰 Amount: {r['amount']:.2f}৳\n🧾 TRX: {r['trx']}",
            reply_markup=InlineKeyboardMarkup(kb)
        )

async def admin_products(q):
    con = db()
    rows = con.execute("SELECT * FROM products ORDER BY id DESC").fetchall()
    con.close()
    if not rows:
        await q.message.reply_text("📭 Product নেই.", reply_markup=admin_menu())
        return
    text = "📦 Products\n\n" + "\n".join(
        f"#{r['id']} {r['name']} — {r['price']:.2f}৳ — {'ON' if r['active'] else 'OFF'}"
        for r in rows
    )
    await q.message.reply_text(text, reply_markup=admin_menu())

async def admin_orders(q):
    con = db()
    rows = con.execute("""
        SELECT o.id,o.user_id,p.name,o.price,o.status
        FROM orders o JOIN products p ON p.id=o.product_id
        ORDER BY o.id DESC LIMIT 30
    """).fetchall()
    con.close()
    if not rows:
        await q.message.reply_text("📭 Orders নেই.", reply_markup=admin_menu())
        return
    text = "📋 Recent Orders\n\n" + "\n".join(
        f"#{r['id']} • U:{r['user_id']} • {r['name']} • {r['price']:.2f}৳"
        for r in rows
    )
    await q.message.reply_text(text, reply_markup=admin_menu())

async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ensure_user(update.effective_user)
    state = context.user_data.get("state")
    text = update.message.text.strip()

    if state == "deposit_amount":
        try:
            amount = float(text)
            if amount <= 0: raise ValueError
        except ValueError:
            await update.message.reply_text("❌ সঠিক amount দিন। যেমন: 100")
            return
        context.user_data["deposit_amount"] = amount
        context.user_data["state"] = "deposit_trx"
        await update.message.reply_text(
            "এখন payment করার পর Transaction ID লিখুন:"
        )
        return

    if state == "deposit_trx":
        amount = context.user_data["deposit_amount"]
        con = db()
        cur = con.cursor()
        cur.execute(
            "INSERT INTO deposits(user_id,amount,trx) VALUES(?,?,?)",
            (update.effective_user.id, amount, text)
        )
        dep_id = cur.lastrowid
        con.commit()
        con.close()
        context.user_data.clear()
        await update.message.reply_text(
            f"✅ Deposit request #{dep_id} submitted.\nAdmin approve করলে balance যোগ হবে।",
            reply_markup=main_menu()
        )
        await context.bot.send_message(
            ADMIN_ID,
            f"💳 New Deposit #{dep_id}\nUser: {update.effective_user.id}\n"
            f"Amount: {amount:.2f}৳\nTRX: {text}",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("✅ Approve", callback_data=f"dep:{dep_id}:approve"),
                InlineKeyboardButton("❌ Reject", callback_data=f"dep:{dep_id}:reject")
            ]])
        )
        return

    # Admin product creation wizard
    if update.effective_user.id == ADMIN_ID:
        if state == "product_name":
            context.user_data["product_name"] = text
            context.user_data["state"] = "product_price"
            await update.message.reply_text("Product price লিখুন:")
            return
        if state == "product_price":
            try:
                price = float(text)
                if price < 0: raise ValueError
            except ValueError:
                await update.message.reply_text("❌ সঠিক price দিন।")
                return
            context.user_data["product_price"] = price
            context.user_data["state"] = "product_description"
            await update.message.reply_text("Product description লিখুন (না চাইলে - লিখুন):")
            return
        if state == "product_description":
            context.user_data["product_description"] = "" if text == "-" else text
            context.user_data["state"] = "product_delivery"
            await update.message.reply_text(
                "Delivery content/link লিখুন। File দিতে চাইলে এখন একটি file পাঠাতে পারবেন।"
            )
            return
        if state == "product_delivery":
            save_product(context, delivery=text, file_id="")
            await update.message.reply_text("✅ Product added.", reply_markup=admin_menu())
            return

async def file_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    if context.user_data.get("state") != "product_delivery":
        return

    file_id = ""
    if update.message.document:
        file_id = update.message.document.file_id
    elif update.message.photo:
        file_id = update.message.photo[-1].file_id
    elif update.message.video:
        file_id = update.message.video.file_id

    if not file_id:
        await update.message.reply_text("❌ এই file type support করা হয়নি।")
        return

    save_product(context, delivery="", file_id=file_id)
    await update.message.reply_text("✅ File product added.", reply_markup=admin_menu())

def save_product(context, delivery="", file_id=""):
    con = db()
    con.execute(
        "INSERT INTO products(name,price,description,delivery,file_id) VALUES(?,?,?,?,?)",
        (
            context.user_data["product_name"],
            context.user_data["product_price"],
            context.user_data.get("product_description", ""),
            delivery,
            file_id
        )
    )
    con.commit()
    con.close()
    context.user_data.clear()

async def error_handler(update, context):
    logging.exception("Bot error", exc_info=context.error)

def main():
    init_db()
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("admin", admin))
    app.add_handler(CallbackQueryHandler(callback))
    app.add_handler(MessageHandler(filters.Document.ALL | filters.PHOTO | filters.VIDEO, file_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))
    app.add_error_handler(error_handler)
    print("Bot is running...")
    app.run_polling()

if __name__ == "__main__":
    main()
