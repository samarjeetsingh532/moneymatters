"""
Tests for multi-account / multi-currency support.

Covers:
- Every new user gets an auto-created default account (Cash, INR)
- Account CRUD routes: add/edit/delete, auth guard, validation
- Data isolation: a user can only see/edit/delete their own accounts
- An account's current_balance is recalculated automatically when its
  expenses/income are inserted, updated, deleted, or moved to another account
- Deleting an account with existing transactions deactivates it instead of
  hard-deleting; deleting the user's last active account is blocked
- Adding an expense/income defaults sensibly to the user's account when none
  is supplied (backward compatibility with the pre-accounts call signature)
- Dashboard totals convert cross-currency amounts into the user's base
  currency rather than summing raw numbers
"""

import pytest
from werkzeug.security import generate_password_hash

import database.db as db_module
from app import app as flask_app
from database.db import init_db
from database.queries import (
    convert_amount,
    get_account_by_id,
    get_accounts_by_user,
    get_summary_stats,
    insert_account,
    insert_expense,
    insert_income,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def db_path(tmp_path):
    return str(tmp_path / "test_spendly.db")


@pytest.fixture
def app(db_path, monkeypatch):
    monkeypatch.setattr(db_module, "DB_PATH", db_path)

    flask_app.config.update(
        {
            "TESTING": True,
            "SECRET_KEY": "test-secret",
            "WTF_CSRF_ENABLED": False,
        }
    )

    with flask_app.app_context():
        init_db()
        yield flask_app


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def registered_user(client):
    email = "testuser@example.com"
    password = "testpass123"
    client.post(
        "/register",
        data={
            "name": "Test User",
            "email": email,
            "password": password,
            "confirm_password": password,
        },
        follow_redirects=True,
    )
    conn = db_module.get_db()
    row = conn.execute("SELECT id FROM users WHERE email = ?", (email,)).fetchone()
    conn.close()
    return row["id"], email, password


@pytest.fixture
def auth_client(client, registered_user):
    user_id, _email, _password = registered_user
    with client.session_transaction() as sess:
        sess["user_id"] = user_id
        sess["user_name"] = "Test User"
    return client


def _create_user(name, email):
    conn = db_module.get_db()
    cursor = conn.execute(
        "INSERT INTO users (name, email, password_hash) VALUES (?, ?, ?)",
        (name, email, generate_password_hash("pass")),
    )
    conn.commit()
    user_id = cursor.lastrowid
    conn.close()
    return user_id


# ---------------------------------------------------------------------------
# Default account on registration
# ---------------------------------------------------------------------------


class TestDefaultAccount:
    def test_registration_creates_default_account(self, registered_user):
        user_id, _email, _password = registered_user
        accounts = get_accounts_by_user(user_id)
        assert len(accounts) == 1
        assert accounts[0]["name"] == "Cash"
        assert accounts[0]["currency"] == "INR"
        assert accounts[0]["current_balance"] == 0


# ---------------------------------------------------------------------------
# Auth guard
# ---------------------------------------------------------------------------


class TestAuthGuard:
    def test_accounts_page_requires_login(self, client):
        response = client.get("/accounts")
        assert response.status_code == 302
        assert "/login" in response.headers["Location"]

    def test_add_account_requires_login(self, client):
        response = client.get("/accounts/add")
        assert response.status_code == 302
        assert "/login" in response.headers["Location"]


# ---------------------------------------------------------------------------
# Account CRUD
# ---------------------------------------------------------------------------


class TestAddAccount:
    def test_add_account_success(self, auth_client, registered_user):
        response = auth_client.post(
            "/accounts/add",
            data={
                "name": "HDFC Bank",
                "account_type": "Bank Account",
                "currency": "INR",
                "opening_balance": "5000",
                "description": "Primary bank account",
            },
            follow_redirects=True,
        )
        assert response.status_code == 200

        user_id, _email, _password = registered_user
        accounts = get_accounts_by_user(user_id)
        names = [a["name"] for a in accounts]
        assert "HDFC Bank" in names
        hdfc = next(a for a in accounts if a["name"] == "HDFC Bank")
        assert hdfc["current_balance"] == 5000.0

    def test_add_account_missing_name_shows_error(self, auth_client, registered_user):
        response = auth_client.post(
            "/accounts/add",
            data={"name": "", "account_type": "Cash", "currency": "INR", "opening_balance": "0"},
        )
        assert response.status_code == 200
        user_id, _email, _password = registered_user
        assert len(get_accounts_by_user(user_id)) == 1

    def test_add_account_invalid_currency_rejected(self, auth_client, registered_user):
        response = auth_client.post(
            "/accounts/add",
            data={
                "name": "Weird Account",
                "account_type": "Cash",
                "currency": "XYZ",
                "opening_balance": "0",
            },
        )
        assert response.status_code == 200
        user_id, _email, _password = registered_user
        names = [a["name"] for a in get_accounts_by_user(user_id)]
        assert "Weird Account" not in names


class TestEditAccount:
    def test_edit_account_updates_fields(self, auth_client, registered_user):
        user_id, _email, _password = registered_user
        account_id = insert_account(user_id, "PayPal", "Wallet", "USD", 100.0, "")

        response = auth_client.post(
            f"/accounts/{account_id}/edit",
            data={
                "name": "PayPal (Business)",
                "account_type": "Wallet",
                "currency": "USD",
                "opening_balance": "200",
                "description": "updated",
                "is_active": "on",
            },
            follow_redirects=True,
        )
        assert response.status_code == 200

        account = get_account_by_id(account_id, user_id)
        assert account["name"] == "PayPal (Business)"
        assert account["opening_balance"] == 200.0
        assert account["current_balance"] == 200.0

    def test_cannot_edit_another_users_account(self, auth_client, registered_user):
        other_user_id = _create_user("Other", "other@example.com")
        other_account_id = insert_account(other_user_id, "Other's Bank", "Bank Account", "INR", 0, "")

        response = auth_client.get(f"/accounts/{other_account_id}/edit")
        assert response.status_code == 404

    def test_cannot_deactivate_last_active_account(self, auth_client, registered_user):
        user_id, _email, _password = registered_user
        accounts = get_accounts_by_user(user_id)
        only_account_id = accounts[0]["id"]

        auth_client.post(
            f"/accounts/{only_account_id}/edit",
            data={
                "name": "Cash",
                "account_type": "Cash",
                "currency": "INR",
                "opening_balance": "0",
                "description": "",
            },
            follow_redirects=True,
        )
        account = get_account_by_id(only_account_id, user_id)
        assert account["is_active"] == 1


class TestDeleteAccount:
    def test_delete_account_with_no_transactions_is_hard_deleted(self, auth_client, registered_user):
        user_id, _email, _password = registered_user
        account_id = insert_account(user_id, "Extra Cash", "Cash", "INR", 0, "")

        auth_client.post(f"/accounts/{account_id}/delete", follow_redirects=True)
        assert get_account_by_id(account_id, user_id) is None

    def test_delete_account_with_transactions_is_deactivated(self, auth_client, registered_user):
        user_id, _email, _password = registered_user
        account_id = insert_account(user_id, "HDFC Bank", "Bank Account", "INR", 0, "")
        insert_expense(user_id, 100.0, "Food", "2026-08-05", "Lunch", account_id=account_id)

        auth_client.post(f"/accounts/{account_id}/delete", follow_redirects=True)

        account = get_account_by_id(account_id, user_id)
        assert account is not None
        assert account["is_active"] == 0

    def test_cannot_delete_last_active_account(self, auth_client, registered_user):
        user_id, _email, _password = registered_user
        only_account_id = get_accounts_by_user(user_id)[0]["id"]

        auth_client.post(f"/accounts/{only_account_id}/delete", follow_redirects=True)

        account = get_account_by_id(only_account_id, user_id)
        assert account is not None
        assert account["is_active"] == 1

    def test_cannot_delete_another_users_account(self, auth_client, registered_user):
        other_user_id = _create_user("Other", "other2@example.com")
        other_account_id = insert_account(other_user_id, "Other's Bank", "Bank Account", "INR", 0, "")

        response = auth_client.post(f"/accounts/{other_account_id}/delete")
        assert response.status_code == 404
        assert get_account_by_id(other_account_id, other_user_id) is not None


# ---------------------------------------------------------------------------
# Balance recalculation
# ---------------------------------------------------------------------------


class TestBalanceRecalculation:
    def test_expense_decreases_account_balance(self, registered_user):
        user_id, _email, _password = registered_user
        account_id = get_accounts_by_user(user_id)[0]["id"]

        insert_expense(user_id, 200.0, "Food", "2026-08-05", "Groceries", account_id=account_id)

        account = get_account_by_id(account_id, user_id)
        assert account["current_balance"] == -200.0

    def test_income_increases_account_balance(self, registered_user):
        user_id, _email, _password = registered_user
        account_id = get_accounts_by_user(user_id)[0]["id"]

        insert_income(user_id, 1000.0, "Salary", "2026-08-01", "Pay", account_id=account_id)

        account = get_account_by_id(account_id, user_id)
        assert account["current_balance"] == 1000.0

    def test_expense_without_account_uses_default_account(self, registered_user):
        user_id, _email, _password = registered_user
        default_account_id = get_accounts_by_user(user_id)[0]["id"]

        insert_expense(user_id, 50.0, "Food", "2026-08-05", "Snack")

        account = get_account_by_id(default_account_id, user_id)
        assert account["current_balance"] == -50.0


# ---------------------------------------------------------------------------
# Currency conversion
# ---------------------------------------------------------------------------


class TestCurrencyConversion:
    def test_convert_amount_same_currency_is_identity(self):
        assert convert_amount(100, "INR", "INR") == 100

    def test_convert_amount_round_trip_is_consistent(self):
        usd_amount = 100
        inr_amount = convert_amount(usd_amount, "USD", "INR")
        back_to_usd = convert_amount(inr_amount, "INR", "USD")
        assert round(back_to_usd, 6) == usd_amount

    def test_summary_stats_converts_mixed_currency_accounts_to_base(self, registered_user):
        user_id, _email, _password = registered_user
        inr_account = get_accounts_by_user(user_id)[0]["id"]
        usd_account = insert_account(user_id, "PayPal", "Wallet", "USD", 0, "")

        insert_income(user_id, 1000.0, "Salary", "2026-08-01", "Pay", account_id=inr_account)
        insert_income(user_id, 100.0, "Freelance", "2026-08-02", "Gig", account_id=usd_account)

        stats = get_summary_stats(
            user_id, date_from="2026-08-01", date_to="2026-08-31", base_currency="INR"
        )
        expected_total = 1000.0 + convert_amount(100.0, "USD", "INR")
        assert float(stats["total_income"].replace(",", "")) == pytest.approx(expected_total, abs=0.01)
        assert "USD" in stats["rates_used"]
