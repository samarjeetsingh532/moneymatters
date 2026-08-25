import os
import sqlite3

from werkzeug.security import generate_password_hash

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "spendly.db")

DEFAULT_ACCOUNT_NAME = "Cash"
DEFAULT_ACCOUNT_TYPE = "Cash"
DEFAULT_CURRENCY = "INR"


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db():
    conn = get_db()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            name          TEXT    NOT NULL,
            email         TEXT    UNIQUE NOT NULL,
            password_hash TEXT    NOT NULL,
            base_currency TEXT    NOT NULL DEFAULT 'INR',
            created_at    TEXT    DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS accounts (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id         INTEGER NOT NULL REFERENCES users(id),
            name            TEXT    NOT NULL,
            account_type    TEXT    NOT NULL,
            currency        TEXT    NOT NULL DEFAULT 'INR',
            opening_balance REAL    NOT NULL DEFAULT 0,
            current_balance REAL    NOT NULL DEFAULT 0,
            description     TEXT,
            is_active       INTEGER NOT NULL DEFAULT 1,
            created_at      TEXT    DEFAULT (datetime('now')),
            updated_at      TEXT    DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS expenses (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id     INTEGER NOT NULL REFERENCES users(id),
            account_id  INTEGER REFERENCES accounts(id),
            amount      REAL    NOT NULL,
            currency    TEXT    NOT NULL DEFAULT 'INR',
            category    TEXT    NOT NULL,
            date        TEXT    NOT NULL,
            description TEXT,
            created_at  TEXT    DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS income (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id     INTEGER NOT NULL REFERENCES users(id),
            account_id  INTEGER REFERENCES accounts(id),
            amount      REAL    NOT NULL,
            currency    TEXT    NOT NULL DEFAULT 'INR',
            source      TEXT    NOT NULL,
            date        TEXT    NOT NULL,
            description TEXT,
            created_at  TEXT    DEFAULT (datetime('now'))
        );
    """)
    conn.commit()
    conn.close()
    migrate_db()


def _column_names(conn, table):
    return {row["name"] for row in conn.execute("PRAGMA table_info({})".format(table))}


def _create_default_account(conn, user_id):
    cursor = conn.execute(
        "INSERT INTO accounts (user_id, name, account_type, currency, "
        "opening_balance, current_balance, description, is_active) "
        "VALUES (?, ?, ?, ?, 0, 0, ?, 1)",
        (
            user_id,
            DEFAULT_ACCOUNT_NAME,
            DEFAULT_ACCOUNT_TYPE,
            DEFAULT_CURRENCY,
            "Auto-created default account",
        ),
    )
    return cursor.lastrowid


def migrate_db():
    """Bring a pre-existing database (from before accounts/currency support)
    up to the current schema: add missing columns, then backfill every
    account-less expense/income row onto a per-user default account so old
    data stays fully visible and usable."""
    conn = get_db()

    users_columns = _column_names(conn, "users")
    if "base_currency" not in users_columns:
        conn.execute(
            "ALTER TABLE users ADD COLUMN base_currency TEXT NOT NULL DEFAULT 'INR'"
        )
    if "is_admin" not in users_columns:
        conn.execute(
            "ALTER TABLE users ADD COLUMN is_admin INTEGER NOT NULL DEFAULT 0"
        )

    for table, extra_column in (("expenses", "category"), ("income", "source")):
        columns = _column_names(conn, table)
        if "account_id" not in columns:
            conn.execute(
                "ALTER TABLE {} ADD COLUMN account_id INTEGER REFERENCES accounts(id)".format(
                    table
                )
            )
        if "currency" not in columns:
            conn.execute(
                "ALTER TABLE {} ADD COLUMN currency TEXT NOT NULL DEFAULT 'INR'".format(
                    table
                )
            )
    conn.commit()

    orphan_user_ids = set()
    for table in ("expenses", "income"):
        rows = conn.execute(
            "SELECT DISTINCT user_id FROM {} WHERE account_id IS NULL".format(table)
        ).fetchall()
        orphan_user_ids.update(row["user_id"] for row in rows)

    for user_id in orphan_user_ids:
        default_account_id = _create_default_account(conn, user_id)
        conn.execute(
            "UPDATE expenses SET account_id = ?, currency = 'INR' "
            "WHERE user_id = ? AND account_id IS NULL",
            (default_account_id, user_id),
        )
        conn.execute(
            "UPDATE income SET account_id = ?, currency = 'INR' "
            "WHERE user_id = ? AND account_id IS NULL",
            (default_account_id, user_id),
        )
    conn.commit()

    account_rows = conn.execute("SELECT id FROM accounts").fetchall()
    conn.close()
    for row in account_rows:
        recalculate_account_balance(row["id"])


def recalculate_account_balance(account_id):
    conn = get_db()
    account = conn.execute(
        "SELECT opening_balance FROM accounts WHERE id = ?", (account_id,)
    ).fetchone()
    if account is None:
        conn.close()
        return

    income_total = conn.execute(
        "SELECT COALESCE(SUM(amount), 0) AS total FROM income WHERE account_id = ?",
        (account_id,),
    ).fetchone()["total"]
    expense_total = conn.execute(
        "SELECT COALESCE(SUM(amount), 0) AS total FROM expenses WHERE account_id = ?",
        (account_id,),
    ).fetchone()["total"]

    new_balance = account["opening_balance"] + income_total - expense_total
    conn.execute(
        "UPDATE accounts SET current_balance = ?, updated_at = datetime('now') "
        "WHERE id = ?",
        (new_balance, account_id),
    )
    conn.commit()
    conn.close()


def create_user(name, email, password):
    conn = get_db()
    cursor = conn.execute(
        "INSERT INTO users (name, email, password_hash) VALUES (?, ?, ?)",
        (name, email, generate_password_hash(password)),
    )
    conn.commit()
    user_id = cursor.lastrowid
    _create_default_account(conn, user_id)
    conn.commit()
    conn.close()
    return user_id


def get_user_by_email(email):
    conn = get_db()
    user = conn.execute(
        "SELECT * FROM users WHERE email = ?", (email,)
    ).fetchone()
    conn.close()
    return user


def seed_db():
    conn = get_db()

    row = conn.execute("SELECT COUNT(*) FROM users").fetchone()
    if row[0] > 0:
        conn.close()
        return

    cursor = conn.execute(
        "INSERT INTO users (name, email, password_hash) VALUES (?, ?, ?)",
        ("Demo User", "demo@spendly.com", generate_password_hash("demo123")),
    )
    user_id = cursor.lastrowid

    def _add_account(name, account_type, currency, opening_balance):
        acc_cursor = conn.execute(
            "INSERT INTO accounts (user_id, name, account_type, currency, "
            "opening_balance, current_balance, description, is_active) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, 1)",
            (user_id, name, account_type, currency, opening_balance, opening_balance, ""),
        )
        return acc_cursor.lastrowid

    hdfc_id = _add_account("HDFC Bank", "Bank Account", "INR", 10000.00)
    cash_id = _add_account("Cash", "Cash", "INR", 2000.00)
    paypal_id = _add_account("PayPal", "Wallet", "USD", 0.00)

    expenses = [
        (user_id, hdfc_id, 450.00,  "INR", "Food",          "2026-04-01", "Groceries from D-Mart"),
        (user_id, cash_id, 120.00,  "INR", "Transport",     "2026-04-02", "Metro card recharge"),
        (user_id, hdfc_id, 1200.00, "INR", "Bills",         "2026-04-03", "Electricity bill"),
        (user_id, cash_id, 350.00,  "INR", "Health",        "2026-04-05", "Pharmacy — vitamins"),
        (user_id, hdfc_id, 500.00,  "INR", "Entertainment", "2026-04-06", "Movie tickets"),
        (user_id, hdfc_id, 800.00,  "INR", "Shopping",      "2026-04-07", "New earphones"),
        (user_id, cash_id, 200.00,  "INR", "Other",         "2026-04-08", "Miscellaneous"),
        (user_id, hdfc_id, 180.00,  "INR", "Food",          "2026-04-08", "Lunch with colleagues"),
    ]

    conn.executemany(
        "INSERT INTO expenses (user_id, account_id, amount, currency, category, date, description) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        expenses,
    )

    income = [
        (user_id, hdfc_id,   45000.00, "INR", "Salary",     "2026-04-01", "April salary"),
        (user_id, paypal_id, 500.00,   "USD", "Freelance",  "2026-04-10", "Logo design project"),
        (user_id, hdfc_id,   1200.00,  "INR", "Investment", "2026-04-15", "Dividend payout"),
    ]

    conn.executemany(
        "INSERT INTO income (user_id, account_id, amount, currency, source, date, description) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        income,
    )
    conn.commit()
    conn.close()

    for account_id in (hdfc_id, cash_id, paypal_id):
        recalculate_account_balance(account_id)


ADMIN_EMAIL = "admin"
ADMIN_PASSWORD = "J@JwutH123"


def ensure_admin_user():
    conn = get_db()
    existing = conn.execute(
        "SELECT id FROM users WHERE email = ?", (ADMIN_EMAIL,)
    ).fetchone()
    if existing is None:
        conn.execute(
            "INSERT INTO users (name, email, password_hash, is_admin) VALUES (?, ?, ?, 1)",
            ("Admin", ADMIN_EMAIL, generate_password_hash(ADMIN_PASSWORD)),
        )
        conn.commit()
    conn.close()
