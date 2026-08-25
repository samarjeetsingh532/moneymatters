import calendar
import io
import sqlite3
from datetime import date, datetime

from flask import (
    Flask,
    abort,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    send_file,
    session,
    url_for,
)
from openpyxl import Workbook
from openpyxl.styles import Font
from werkzeug.security import check_password_hash

from database.db import create_user, get_db, get_user_by_email, init_db, seed_db
from database.queries import (
    delete_expense_by_id,
    delete_income_by_id,
    get_all_transactions,
    get_category_breakdown,
    get_expense_by_id,
    get_income_by_id,
    get_recent_transactions,
    get_summary_stats,
    get_user_by_id,
    insert_expense,
    insert_income,
    update_expense,
    update_income,
)

app = Flask(__name__)
app.secret_key = "dev-secret-key"

CATEGORIES = [
    "Food",
    "Transport",
    "Bills",
    "Health",
    "Entertainment",
    "Shopping",
    "Other",
]

INCOME_SOURCES = [
    "Salary",
    "Freelance",
    "Business",
    "Investment",
    "Gift",
    "Other",
]

with app.app_context():
    init_db()
    seed_db()


def _parse_date(val):
    try:
        datetime.strptime(val, "%Y-%m-%d")
        return val
    except (ValueError, TypeError):
        return None


def _months_ago(today, n):
    m, y = today.month - n, today.year
    while m <= 0:
        m += 12
        y -= 1
    return date(y, m, 1).isoformat()


def _build_transactions_workbook(transactions):
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Transactions"

    sheet.append(["Date", "Description", "Category", "Type", "Amount"])
    for cell in sheet[1]:
        cell.font = Font(bold=True)

    for tx in transactions:
        sheet.append(
            [
                datetime.strptime(tx["date"], "%Y-%m-%d").date(),
                tx["description"],
                tx["category"],
                "Income" if tx["type"] == "income" else "Expense",
                tx["amount"],
            ]
        )

    for row in sheet.iter_rows(min_row=2, min_col=1, max_col=1):
        row[0].number_format = "yyyy-mm-dd"
    for row in sheet.iter_rows(min_row=2, min_col=5, max_col=5):
        row[0].number_format = "#,##0.00"

    widths = {"A": 14, "B": 34, "C": 16, "D": 10, "E": 14}
    for column, width in widths.items():
        sheet.column_dimensions[column].width = width

    return workbook


def _send_workbook(workbook, filename):
    buffer = io.BytesIO()
    workbook.save(buffer)
    buffer.seek(0)
    return send_file(
        buffer,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        as_attachment=True,
        download_name=filename,
    )


# ------------------------------------------------------------------ #
# Routes                                                              #
# ------------------------------------------------------------------ #


@app.route("/")
def landing():
    return render_template("landing.html")


@app.route("/register", methods=["GET", "POST"])
def register():
    if session.get("user_id"):
        return redirect(url_for("profile"))
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip()
        password = request.form.get("password", "")
        confirm_password = request.form.get("confirm_password", "")

        if not all([name, email, password, confirm_password]):
            flash("All fields are required.", "error")
            return render_template("register.html")

        if password != confirm_password:
            flash("Passwords do not match.", "error")
            return render_template("register.html")

        try:
            create_user(name, email, password)
        except sqlite3.IntegrityError:
            flash("Email already registered.", "error")
            return render_template("register.html")

        flash("Account created! Please sign in.", "success")
        return redirect(url_for("login"))

    return render_template("register.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if session.get("user_id"):
        return redirect(url_for("profile"))
    if request.method == "POST":
        email = request.form.get("email", "").strip()
        password = request.form.get("password", "")

        user = get_user_by_email(email)
        if not user or not check_password_hash(user["password_hash"], password):
            flash("Invalid email or password.", "error")
            return render_template("login.html")

        session["user_id"] = user["id"]
        session["user_name"] = user["name"]
        return redirect(url_for("profile"))

    return render_template("login.html")


# ------------------------------------------------------------------ #
# Placeholder routes — students will implement these                  #
# ------------------------------------------------------------------ #


@app.route("/terms")
def terms():
    return render_template("terms.html")


@app.route("/privacy")
def privacy():
    return render_template("privacy.html")


@app.route("/logout")
def logout():
    session.clear()
    flash("You have been logged out.", "success")
    return redirect(url_for("landing"))


@app.route("/profile")
def profile():
    if not session.get("user_id"):
        return redirect(url_for("login"))

    uid = session["user_id"]
    today = date.today()

    date_from = _parse_date(request.args.get("date_from"))
    date_to = _parse_date(request.args.get("date_to"))

    if date_from and date_to and date_from > date_to:
        flash("Start date must be before end date.", "error")
        date_from = date_to = None

    today_str = today.isoformat()
    this_month_from = today.replace(day=1).isoformat()
    this_month_to = today.replace(
        day=calendar.monthrange(today.year, today.month)[1]
    ).isoformat()

    presets = {
        "this_month": {"date_from": this_month_from, "date_to": this_month_to},
        "last_3": {"date_from": _months_ago(today, 3), "date_to": today_str},
        "last_6": {"date_from": _months_ago(today, 6), "date_to": today_str},
    }

    return render_template(
        "profile.html",
        user=get_user_by_id(uid),
        stats=get_summary_stats(uid, date_from, date_to),
        transactions=get_recent_transactions(uid, date_from=date_from, date_to=date_to),
        categories=get_category_breakdown(uid, date_from, date_to),
        date_from=date_from,
        date_to=date_to,
        presets=presets,
        month_names=list(calendar.month_name)[1:],
        export_years=list(range(today.year - 5, today.year + 1)),
        current_month=today.month,
        current_year=today.year,
    )


@app.route("/analytics")
def analytics():
    if not session.get("user_id"):
        return redirect(url_for("login"))
    return render_template("analytics.html")


@app.route("/expenses/add", methods=["GET", "POST"])
def add_expense():
    if not session.get("user_id"):
        return redirect(url_for("login"))

    today = date.today().isoformat()

    if request.method == "POST":
        amount_raw = request.form.get("amount", "").strip()
        category = request.form.get("category", "").strip()
        expense_date = request.form.get("date", "").strip()
        description = request.form.get("description", "").strip()

        try:
            amount = float(amount_raw)
            if amount <= 0:
                raise ValueError
        except ValueError:
            flash("Amount must be a positive number.", "error")
            return render_template(
                "add_expense.html",
                categories=CATEGORIES,
                form=request.form,
                today=today,
            )

        if category not in CATEGORIES:
            flash("Please select a valid category.", "error")
            return render_template(
                "add_expense.html",
                categories=CATEGORIES,
                form=request.form,
                today=today,
            )

        if not _parse_date(expense_date):
            flash("Please enter a valid date.", "error")
            return render_template(
                "add_expense.html",
                categories=CATEGORIES,
                form=request.form,
                today=today,
            )

        insert_expense(session["user_id"], amount, category, expense_date, description)
        flash("Expense added.", "success")
        return redirect(url_for("profile"))

    return render_template(
        "add_expense.html", categories=CATEGORIES, form={}, today=today
    )


@app.route("/expenses/<int:id>/edit", methods=["GET", "POST"])
def edit_expense(id):
    if not session.get("user_id"):
        return redirect(url_for("login"))

    expense = get_expense_by_id(id, session["user_id"])
    if expense is None:
        abort(404)

    if request.method == "GET":
        return render_template(
            "edit_expense.html",
            expense=expense,
            categories=CATEGORIES,
            form={},
        )

    amount_raw = request.form.get("amount", "").strip()
    category = request.form.get("category", "").strip()
    expense_date = request.form.get("date", "").strip()
    description = request.form.get("description", "").strip()

    try:
        amount = float(amount_raw)
        if amount <= 0:
            raise ValueError
    except ValueError:
        flash("Amount must be a positive number.", "error")
        return render_template(
            "edit_expense.html",
            expense=expense,
            categories=CATEGORIES,
            form=request.form,
        )

    if category not in CATEGORIES:
        flash("Please select a valid category.", "error")
        return render_template(
            "edit_expense.html",
            expense=expense,
            categories=CATEGORIES,
            form=request.form,
        )

    if not _parse_date(expense_date):
        flash("Please enter a valid date.", "error")
        return render_template(
            "edit_expense.html",
            expense=expense,
            categories=CATEGORIES,
            form=request.form,
        )

    update_expense(id, session["user_id"], amount, category, expense_date, description)
    flash("Expense updated.", "success")
    return redirect(url_for("profile"))


@app.route("/expenses/<int:id>/delete", methods=["POST"])
def delete_expense(id):
    if not session.get("user_id"):
        return redirect(url_for("login"))

    expense = get_expense_by_id(id, session["user_id"])
    if expense is None:
        abort(404)

    delete_expense_by_id(id, session["user_id"])
    return redirect(url_for("profile"))


@app.route("/income/add", methods=["GET", "POST"])
def add_income():
    if not session.get("user_id"):
        return redirect(url_for("login"))

    today = date.today().isoformat()

    if request.method == "POST":
        amount_raw = request.form.get("amount", "").strip()
        source = request.form.get("source", "").strip()
        income_date = request.form.get("date", "").strip()
        description = request.form.get("description", "").strip()

        try:
            amount = float(amount_raw)
            if amount <= 0:
                raise ValueError
        except ValueError:
            flash("Amount must be a positive number.", "error")
            return render_template(
                "add_income.html",
                sources=INCOME_SOURCES,
                form=request.form,
                today=today,
            )

        if source not in INCOME_SOURCES:
            flash("Please select a valid source.", "error")
            return render_template(
                "add_income.html",
                sources=INCOME_SOURCES,
                form=request.form,
                today=today,
            )

        if not _parse_date(income_date):
            flash("Please enter a valid date.", "error")
            return render_template(
                "add_income.html",
                sources=INCOME_SOURCES,
                form=request.form,
                today=today,
            )

        insert_income(session["user_id"], amount, source, income_date, description)
        flash("Income added.", "success")
        return redirect(url_for("profile"))

    return render_template(
        "add_income.html", sources=INCOME_SOURCES, form={}, today=today
    )


@app.route("/income/<int:id>/edit", methods=["GET", "POST"])
def edit_income(id):
    if not session.get("user_id"):
        return redirect(url_for("login"))

    income = get_income_by_id(id, session["user_id"])
    if income is None:
        abort(404)

    if request.method == "GET":
        return render_template(
            "edit_income.html",
            income=income,
            sources=INCOME_SOURCES,
            form={},
        )

    amount_raw = request.form.get("amount", "").strip()
    source = request.form.get("source", "").strip()
    income_date = request.form.get("date", "").strip()
    description = request.form.get("description", "").strip()

    try:
        amount = float(amount_raw)
        if amount <= 0:
            raise ValueError
    except ValueError:
        flash("Amount must be a positive number.", "error")
        return render_template(
            "edit_income.html",
            income=income,
            sources=INCOME_SOURCES,
            form=request.form,
        )

    if source not in INCOME_SOURCES:
        flash("Please select a valid source.", "error")
        return render_template(
            "edit_income.html",
            income=income,
            sources=INCOME_SOURCES,
            form=request.form,
        )

    if not _parse_date(income_date):
        flash("Please enter a valid date.", "error")
        return render_template(
            "edit_income.html",
            income=income,
            sources=INCOME_SOURCES,
            form=request.form,
        )

    update_income(id, session["user_id"], amount, source, income_date, description)
    flash("Income updated.", "success")
    return redirect(url_for("profile"))


@app.route("/income/<int:id>/delete", methods=["POST"])
def delete_income(id):
    if not session.get("user_id"):
        return redirect(url_for("login"))

    income = get_income_by_id(id, session["user_id"])
    if income is None:
        abort(404)

    delete_income_by_id(id, session["user_id"])
    return redirect(url_for("profile"))


# Export routes respond with JSON errors instead of redirecting to /login,
# since they're called via fetch() from the download buttons, not page navigation.


@app.route("/export/monthly")
def export_monthly():
    if not session.get("user_id"):
        return jsonify({"error": "Please sign in to export your data."}), 401

    try:
        year = int(request.args.get("year", ""))
        month = int(request.args.get("month", ""))
        if not (1 <= month <= 12):
            raise ValueError
    except ValueError:
        return jsonify({"error": "Please select a valid month and year."}), 400

    date_from = date(year, month, 1).isoformat()
    date_to = date(year, month, calendar.monthrange(year, month)[1]).isoformat()

    transactions = get_all_transactions(
        session["user_id"], date_from=date_from, date_to=date_to
    )
    workbook = _build_transactions_workbook(transactions)
    filename = "expenses_income_{:04d}_{:02d}.xlsx".format(year, month)
    return _send_workbook(workbook, filename)


@app.route("/export/full")
def export_full():
    if not session.get("user_id"):
        return jsonify({"error": "Please sign in to export your data."}), 401

    transactions = get_all_transactions(session["user_id"])
    workbook = _build_transactions_workbook(transactions)
    return _send_workbook(workbook, "expenses_income_full.xlsx")


if __name__ == "__main__":
    app.run(debug=True, port=5001)
