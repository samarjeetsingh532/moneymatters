from datetime import datetime

from database.db import DEFAULT_CURRENCY, get_db, recalculate_account_balance

# ------------------------------------------------------------------ #
# Currency support                                                    #
# ------------------------------------------------------------------ #
# Fixed reference rates (units of that currency per 1 INR's worth is the
# inverse of this — this table is "how many INR is 1 unit of the currency
# worth"). These are static placeholders so the app can show one consistent
# converted total across accounts; swap CONVERT_AMOUNT's lookup for a live
# exchange-rate API call later without touching any caller.

CURRENCIES = ["INR", "USD", "EUR", "GBP", "AED", "CAD", "AUD", "JPY"]

CURRENCY_SYMBOLS = {
    "INR": "₹",
    "USD": "$",
    "EUR": "€",
    "GBP": "£",
    "AED": "AED ",
    "CAD": "CA$",
    "AUD": "A$",
    "JPY": "¥",
}

EXCHANGE_RATES_TO_INR = {
    "INR": 1.0,
    "USD": 83.0,
    "EUR": 90.0,
    "GBP": 105.0,
    "AED": 22.6,
    "CAD": 61.0,
    "AUD": 55.0,
    "JPY": 0.56,
}


def convert_amount(amount, from_currency, to_currency):
    if from_currency == to_currency:
        return amount
    from_rate = EXCHANGE_RATES_TO_INR.get(from_currency, 1.0)
    to_rate = EXCHANGE_RATES_TO_INR.get(to_currency, 1.0)
    return amount * from_rate / to_rate


def currency_symbol(currency):
    return CURRENCY_SYMBOLS.get(currency, currency + " ")


# ------------------------------------------------------------------ #
# Accounts                                                             #
# ------------------------------------------------------------------ #


def get_accounts_by_user(user_id, active_only=False):
    conn = get_db()
    query = "SELECT * FROM accounts WHERE user_id = ?"
    params = [user_id]
    if active_only:
        query += " AND is_active = 1"
    query += " ORDER BY is_active DESC, name ASC"
    rows = conn.execute(query, params).fetchall()
    conn.close()
    return [dict(row) for row in rows]


def get_account_by_id(account_id, user_id):
    conn = get_db()
    row = conn.execute(
        "SELECT * FROM accounts WHERE id = ? AND user_id = ?", (account_id, user_id)
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def get_default_account(user_id):
    """The user's first active account — used to pre-fill forms and as the
    fallback when a transaction is created without an explicit account."""
    conn = get_db()
    row = conn.execute(
        "SELECT * FROM accounts WHERE user_id = ? AND is_active = 1 "
        "ORDER BY id ASC LIMIT 1",
        (user_id,),
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def insert_account(user_id, name, account_type, currency, opening_balance, description):
    conn = get_db()
    cursor = conn.execute(
        "INSERT INTO accounts (user_id, name, account_type, currency, "
        "opening_balance, current_balance, description, is_active) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, 1)",
        (
            user_id,
            name,
            account_type,
            currency,
            opening_balance,
            opening_balance,
            description or None,
        ),
    )
    conn.commit()
    account_id = cursor.lastrowid
    conn.close()
    return account_id


def update_account(
    account_id, user_id, name, account_type, currency, opening_balance, description, is_active
):
    conn = get_db()
    conn.execute(
        "UPDATE accounts SET name = ?, account_type = ?, currency = ?, "
        "opening_balance = ?, description = ?, is_active = ?, updated_at = datetime('now') "
        "WHERE id = ? AND user_id = ?",
        (
            name,
            account_type,
            currency,
            opening_balance,
            description or None,
            1 if is_active else 0,
            account_id,
            user_id,
        ),
    )
    conn.commit()
    conn.close()
    recalculate_account_balance(account_id)


def count_account_transactions(account_id):
    conn = get_db()
    expense_count = conn.execute(
        "SELECT COUNT(*) AS c FROM expenses WHERE account_id = ?", (account_id,)
    ).fetchone()["c"]
    income_count = conn.execute(
        "SELECT COUNT(*) AS c FROM income WHERE account_id = ?", (account_id,)
    ).fetchone()["c"]
    conn.close()
    return expense_count + income_count


def delete_account_by_id(account_id, user_id):
    """Hard-delete an account with no transaction history. An account that
    already has expenses/income is deactivated instead, so that history
    stays intact and visible rather than orphaned or destroyed."""
    if count_account_transactions(account_id) > 0:
        conn = get_db()
        conn.execute(
            "UPDATE accounts SET is_active = 0, updated_at = datetime('now') "
            "WHERE id = ? AND user_id = ?",
            (account_id, user_id),
        )
        conn.commit()
        conn.close()
        return "deactivated"

    conn = get_db()
    conn.execute(
        "DELETE FROM accounts WHERE id = ? AND user_id = ?", (account_id, user_id)
    )
    conn.commit()
    conn.close()
    return "deleted"


def get_account_breakdown(user_id, date_from=None, date_to=None):
    date_clause, date_params = _build_date_filter(date_from, date_to)

    conn = get_db()
    accounts = conn.execute(
        "SELECT id, name, account_type, currency, opening_balance, current_balance, "
        "is_active FROM accounts WHERE user_id = ? ORDER BY is_active DESC, name ASC",
        (user_id,),
    ).fetchall()

    result = []
    for acc in accounts:
        income_total = conn.execute(
            "SELECT COALESCE(SUM(amount), 0) AS total FROM income "
            "WHERE account_id = ? " + date_clause,
            [acc["id"]] + date_params,
        ).fetchone()["total"]
        expense_total = conn.execute(
            "SELECT COALESCE(SUM(amount), 0) AS total FROM expenses "
            "WHERE account_id = ? " + date_clause,
            [acc["id"]] + date_params,
        ).fetchone()["total"]

        result.append(
            {
                "id": acc["id"],
                "name": acc["name"],
                "account_type": acc["account_type"],
                "currency": acc["currency"],
                "current_balance": acc["current_balance"],
                "is_active": bool(acc["is_active"]),
                "income": income_total,
                "expenses": expense_total,
            }
        )
    conn.close()
    return result


# ------------------------------------------------------------------ #
# Expenses                                                             #
# ------------------------------------------------------------------ #


def _resolve_account_and_currency(conn, user_id, account_id, currency):
    if account_id is None:
        default_account = get_default_account(user_id)
        account_id = default_account["id"] if default_account else None
    if currency is None:
        if account_id is not None:
            acc = conn.execute(
                "SELECT currency FROM accounts WHERE id = ?", (account_id,)
            ).fetchone()
            currency = acc["currency"] if acc else DEFAULT_CURRENCY
        else:
            currency = DEFAULT_CURRENCY
    return account_id, currency


def get_expense_by_id(expense_id, user_id):
    conn = get_db()
    row = conn.execute(
        "SELECT e.id, e.amount, e.currency, e.category, e.date, e.description, "
        "e.account_id, a.name AS account_name "
        "FROM expenses e LEFT JOIN accounts a ON a.id = e.account_id "
        "WHERE e.id = ? AND e.user_id = ?",
        (expense_id, user_id),
    ).fetchone()
    conn.close()
    if row is None:
        return None
    return {
        "id": row["id"],
        "amount": row["amount"],
        "currency": row["currency"],
        "category": row["category"],
        "date": row["date"],
        "description": row["description"] or "",
        "account_id": row["account_id"],
        "account_name": row["account_name"] or "—",
    }


def update_expense(
    expense_id, user_id, amount, category, expense_date, description, account_id=None, currency=None
):
    conn = get_db()
    existing = conn.execute(
        "SELECT account_id FROM expenses WHERE id = ? AND user_id = ?",
        (expense_id, user_id),
    ).fetchone()
    old_account_id = existing["account_id"] if existing else None

    if account_id is None:
        account_id = old_account_id
    account_id, currency = _resolve_account_and_currency(conn, user_id, account_id, currency)

    conn.execute(
        "UPDATE expenses SET amount = ?, currency = ?, category = ?, date = ?, "
        "description = ?, account_id = ? WHERE id = ? AND user_id = ?",
        (amount, currency, category, expense_date, description or None, account_id, expense_id, user_id),
    )
    conn.commit()
    conn.close()

    if old_account_id is not None:
        recalculate_account_balance(old_account_id)
    if account_id is not None and account_id != old_account_id:
        recalculate_account_balance(account_id)


def delete_expense_by_id(expense_id, user_id):
    conn = get_db()
    row = conn.execute(
        "SELECT account_id FROM expenses WHERE id = ? AND user_id = ?",
        (expense_id, user_id),
    ).fetchone()
    account_id = row["account_id"] if row else None

    conn.execute(
        "DELETE FROM expenses WHERE id = ? AND user_id = ?",
        (expense_id, user_id),
    )
    conn.commit()
    conn.close()

    if account_id is not None:
        recalculate_account_balance(account_id)


def insert_expense(user_id, amount, category, expense_date, description, account_id=None, currency=None):
    conn = get_db()
    account_id, currency = _resolve_account_and_currency(conn, user_id, account_id, currency)

    cursor = conn.execute(
        "INSERT INTO expenses (user_id, account_id, amount, currency, category, date, description)"
        " VALUES (?, ?, ?, ?, ?, ?, ?)",
        (user_id, account_id, amount, currency, category, expense_date, description or None),
    )
    conn.commit()
    expense_id = cursor.lastrowid
    conn.close()

    if account_id is not None:
        recalculate_account_balance(account_id)
    return expense_id


# ------------------------------------------------------------------ #
# Income                                                               #
# ------------------------------------------------------------------ #


def get_income_by_id(income_id, user_id):
    conn = get_db()
    row = conn.execute(
        "SELECT i.id, i.amount, i.currency, i.source, i.date, i.description, "
        "i.account_id, a.name AS account_name "
        "FROM income i LEFT JOIN accounts a ON a.id = i.account_id "
        "WHERE i.id = ? AND i.user_id = ?",
        (income_id, user_id),
    ).fetchone()
    conn.close()
    if row is None:
        return None
    return {
        "id": row["id"],
        "amount": row["amount"],
        "currency": row["currency"],
        "source": row["source"],
        "date": row["date"],
        "description": row["description"] or "",
        "account_id": row["account_id"],
        "account_name": row["account_name"] or "—",
    }


def update_income(
    income_id, user_id, amount, source, income_date, description, account_id=None, currency=None
):
    conn = get_db()
    existing = conn.execute(
        "SELECT account_id FROM income WHERE id = ? AND user_id = ?",
        (income_id, user_id),
    ).fetchone()
    old_account_id = existing["account_id"] if existing else None

    if account_id is None:
        account_id = old_account_id
    account_id, currency = _resolve_account_and_currency(conn, user_id, account_id, currency)

    conn.execute(
        "UPDATE income SET amount = ?, currency = ?, source = ?, date = ?, "
        "description = ?, account_id = ? WHERE id = ? AND user_id = ?",
        (amount, currency, source, income_date, description or None, account_id, income_id, user_id),
    )
    conn.commit()
    conn.close()

    if old_account_id is not None:
        recalculate_account_balance(old_account_id)
    if account_id is not None and account_id != old_account_id:
        recalculate_account_balance(account_id)


def delete_income_by_id(income_id, user_id):
    conn = get_db()
    row = conn.execute(
        "SELECT account_id FROM income WHERE id = ? AND user_id = ?",
        (income_id, user_id),
    ).fetchone()
    account_id = row["account_id"] if row else None

    conn.execute(
        "DELETE FROM income WHERE id = ? AND user_id = ?",
        (income_id, user_id),
    )
    conn.commit()
    conn.close()

    if account_id is not None:
        recalculate_account_balance(account_id)


def insert_income(user_id, amount, source, income_date, description, account_id=None, currency=None):
    conn = get_db()
    account_id, currency = _resolve_account_and_currency(conn, user_id, account_id, currency)

    cursor = conn.execute(
        "INSERT INTO income (user_id, account_id, amount, currency, source, date, description)"
        " VALUES (?, ?, ?, ?, ?, ?, ?)",
        (user_id, account_id, amount, currency, source, income_date, description or None),
    )
    conn.commit()
    income_id = cursor.lastrowid
    conn.close()

    if account_id is not None:
        recalculate_account_balance(account_id)
    return income_id


# ------------------------------------------------------------------ #
# Shared helpers                                                       #
# ------------------------------------------------------------------ #


def _build_date_filter(date_from, date_to):
    if date_from and date_to:
        return "AND date BETWEEN ? AND ?", [date_from, date_to]
    return "", []


def _build_filters(date_from, date_to, account_id=None, currency=None):
    date_clause, params = _build_date_filter(date_from, date_to)
    clauses = [date_clause] if date_clause else []
    if account_id:
        clauses.append("AND account_id = ?")
        params.append(account_id)
    if currency:
        clauses.append("AND currency = ?")
        params.append(currency)
    return " ".join(clauses), params


def get_user_by_id(user_id):
    conn = get_db()
    row = conn.execute(
        "SELECT id, name, email, base_currency, created_at FROM users WHERE id = ?",
        (user_id,),
    ).fetchone()
    conn.close()

    if row is None:
        return None

    name = row["name"]
    initials = "".join(w[0].upper() for w in name.split() if w)
    member_since = datetime.strptime(row["created_at"], "%Y-%m-%d %H:%M:%S").strftime(
        "%B %Y"
    )

    return {
        "name": name,
        "email": row["email"],
        "initials": initials,
        "member_since": member_since,
        "base_currency": row["base_currency"],
    }


def get_all_users():
    conn = get_db()
    rows = conn.execute(
        "SELECT id, name, email, base_currency, is_admin, created_at FROM users "
        "ORDER BY is_admin DESC, name ASC"
    ).fetchall()
    conn.close()
    return [dict(row) for row in rows]


def get_user_for_admin(user_id):
    conn = get_db()
    row = conn.execute(
        "SELECT id, name, email, base_currency, is_admin, created_at FROM users WHERE id = ?",
        (user_id,),
    ).fetchone()
    conn.close()

    if row is None:
        return None

    member_since = datetime.strptime(row["created_at"], "%Y-%m-%d %H:%M:%S").strftime(
        "%B %Y"
    )

    return {
        "id": row["id"],
        "name": row["name"],
        "email": row["email"],
        "base_currency": row["base_currency"],
        "is_admin": bool(row["is_admin"]),
        "member_since": member_since,
    }


def update_user_base_currency(user_id, currency):
    conn = get_db()
    conn.execute(
        "UPDATE users SET base_currency = ? WHERE id = ?", (currency, user_id)
    )
    conn.commit()
    conn.close()


def _account_name_lookup(conn, rows):
    account_ids = {row["account_id"] for row in rows if row["account_id"] is not None}
    if not account_ids:
        return {}
    placeholders = ",".join("?" for _ in account_ids)
    acc_rows = conn.execute(
        "SELECT id, name FROM accounts WHERE id IN ({})".format(placeholders),
        list(account_ids),
    ).fetchall()
    return {r["id"]: r["name"] for r in acc_rows}


def _transaction_sql(filters, tx_type):
    expense_sql = (
        "SELECT id, date, description, category, amount, currency, account_id, "
        "'expense' AS type FROM expenses WHERE user_id = ? " + filters
    )
    income_sql = (
        "SELECT id, date, description, source AS category, amount, currency, account_id, "
        "'income' AS type FROM income WHERE user_id = ? " + filters
    )
    if tx_type == "expense":
        return expense_sql, False
    if tx_type == "income":
        return income_sql, False
    return expense_sql + " UNION ALL " + income_sql, True


def get_recent_transactions(
    user_id, limit=10, date_from=None, date_to=None, account_id=None, tx_type=None, currency=None
):
    filters, filter_params = _build_filters(date_from, date_to, account_id, currency)

    sql, is_union = _transaction_sql(filters, tx_type)
    sql += " ORDER BY date DESC, id DESC LIMIT ?"
    params = [user_id] + filter_params
    if is_union:
        params += [user_id] + filter_params
    params += [limit]

    conn = get_db()
    rows = conn.execute(sql, params).fetchall()
    account_names = _account_name_lookup(conn, rows)
    conn.close()

    return [
        {
            "id": row["id"],
            "date": datetime.strptime(row["date"], "%Y-%m-%d").strftime("%d %b %Y"),
            "description": row["description"],
            "category": row["category"],
            "amount": "{:,.2f}".format(row["amount"]),
            "currency": row["currency"],
            "account_id": row["account_id"],
            "account_name": account_names.get(row["account_id"], "—"),
            "type": row["type"],
        }
        for row in rows
    ]


def get_all_transactions(
    user_id, date_from=None, date_to=None, account_id=None, tx_type=None, currency=None
):
    filters, filter_params = _build_filters(date_from, date_to, account_id, currency)

    sql, is_union = _transaction_sql(filters, tx_type)
    sql += " ORDER BY date ASC, id ASC"
    params = [user_id] + filter_params
    if is_union:
        params += [user_id] + filter_params

    conn = get_db()
    rows = conn.execute(sql, params).fetchall()
    account_names = _account_name_lookup(conn, rows)
    conn.close()

    return [
        {
            "date": row["date"],
            "description": row["description"] or "",
            "category": row["category"],
            "amount": row["amount"],
            "currency": row["currency"],
            "account_name": account_names.get(row["account_id"], "—"),
            "type": row["type"],
        }
        for row in rows
    ]


def get_summary_stats(
    user_id,
    date_from=None,
    date_to=None,
    base_currency="INR",
    account_id=None,
    tx_type=None,
    currency=None,
):
    filters, filter_params = _build_filters(date_from, date_to, account_id, currency)

    conn = get_db()

    expense_rows = []
    if tx_type != "income":
        expense_rows = conn.execute(
            "SELECT category, currency, amount FROM expenses WHERE user_id = ? " + filters,
            [user_id] + filter_params,
        ).fetchall()

    income_rows = []
    if tx_type != "expense":
        income_rows = conn.execute(
            "SELECT currency, amount FROM income WHERE user_id = ? " + filters,
            [user_id] + filter_params,
        ).fetchall()
    conn.close()

    total_expense = sum(
        convert_amount(r["amount"], r["currency"], base_currency) for r in expense_rows
    )
    total_income = sum(
        convert_amount(r["amount"], r["currency"], base_currency) for r in income_rows
    )

    category_totals = {}
    for r in expense_rows:
        converted = convert_amount(r["amount"], r["currency"], base_currency)
        category_totals[r["category"]] = category_totals.get(r["category"], 0) + converted
    top_category = max(category_totals, key=category_totals.get) if category_totals else "—"

    rates_used = {}
    for r in list(expense_rows) + list(income_rows):
        if r["currency"] != base_currency and r["currency"] not in rates_used:
            rates_used[r["currency"]] = convert_amount(1, r["currency"], base_currency)

    balance = total_income - total_expense

    return {
        "total": "{:,.2f}".format(total_expense),
        "count": len(expense_rows),
        "top_category": top_category,
        "total_income": "{:,.2f}".format(total_income),
        "balance": "{:,.2f}".format(balance),
        "balance_raw": balance,
        "base_currency": base_currency,
        "rates_used": rates_used,
    }


def get_category_breakdown(
    user_id, date_from=None, date_to=None, base_currency="INR", account_id=None, currency=None
):
    filters, filter_params = _build_filters(date_from, date_to, account_id, currency)

    conn = get_db()
    rows = conn.execute(
        "SELECT category, currency, amount FROM expenses WHERE user_id = ? " + filters,
        [user_id] + filter_params,
    ).fetchall()
    conn.close()

    totals = {}
    for r in rows:
        converted = convert_amount(r["amount"], r["currency"], base_currency)
        totals[r["category"]] = totals.get(r["category"], 0) + converted

    grand_total = sum(totals.values())
    if grand_total == 0:
        return []

    names = list(totals.keys())
    names.sort(key=lambda n: totals[n], reverse=True)
    pcts = [int(totals[n] / grand_total * 100) for n in names]
    pcts[0] += 100 - sum(pcts)

    return [
        {
            "name": name,
            "amount": "{:,.2f}".format(totals[name]),
            "percent": pct,
        }
        for name, pct in zip(names, pcts)
    ]
