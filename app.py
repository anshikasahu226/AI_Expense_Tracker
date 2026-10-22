import csv
from io import StringIO

import os

import mysql.connector
from dotenv import load_dotenv
from flask import Flask, render_template, request, redirect, url_for, flash
from flask_login import (
    LoginManager,
    UserMixin,
    current_user,
    login_user,
    logout_user,
    login_required,
)
from werkzeug.security import generate_password_hash, check_password_hash

load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "dev-key-change-later")

login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = "login"
login_manager.login_message = "Please log in to access your dashboard."


# ---------------- DATABASE CONNECTION ----------------

def get_db_connection():
    return mysql.connector.connect(
        host=os.getenv("MYSQL_HOST", "localhost"),
        port=int(os.getenv("MYSQL_PORT", "3306")),
        user=os.getenv("MYSQL_USER", "root"),
        password=os.getenv("MYSQL_PASSWORD", ""),
        database=os.getenv("MYSQL_DATABASE", "ai_expense_tracker"),
    )


def initialize_database():
    connection = get_db_connection()
    cursor = connection.cursor()

    try:
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id INT AUTO_INCREMENT PRIMARY KEY,
                name VARCHAR(100) NOT NULL,
                email VARCHAR(255) NOT NULL UNIQUE,
                password_hash VARCHAR(255) NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS transactions (
                id INT AUTO_INCREMENT PRIMARY KEY,
                user_id INT NULL,
                description VARCHAR(255) NOT NULL,
                amount DECIMAL(12, 2) NOT NULL,
                transaction_type ENUM('income', 'expense') NOT NULL,
                category VARCHAR(100) NOT NULL,
                transaction_date DATE NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
   
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS monthly_budgets (
                id INT AUTO_INCREMENT PRIMARY KEY,
                user_id INT NOT NULL,
                budget_month DATE NOT NULL,
                budget_amount DECIMAL(12, 2) NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE KEY unique_user_budget_month (user_id, budget_month),
                FOREIGN KEY (user_id) REFERENCES users(id)
                    ON DELETE CASCADE
            )
        """)
        
        
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS daily_spending_limits (
                id INT AUTO_INCREMENT PRIMARY KEY,
                user_id INT NOT NULL UNIQUE,
                daily_limit DECIMAL(12, 2) NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(id)
                    ON DELETE CASCADE
        )
    """)


        # Add user_id if the transactions table already existed
        # without this column.
        cursor.execute("SHOW COLUMNS FROM transactions LIKE 'user_id'")
        if cursor.fetchone() is None:
            cursor.execute(
                "ALTER TABLE transactions ADD COLUMN user_id INT NULL"
            )

        connection.commit()

    finally:
        cursor.close()
        connection.close()


# ---------------- USER MODEL ----------------

class User(UserMixin):
    def __init__(self, user_id, name, email, password_hash):
        self.id = user_id
        self.name = name
        self.email = email
        self.password_hash = password_hash


# ---------------- LOGIN SESSION LOADER ----------------

@login_manager.user_loader
def load_user(user_id):
    connection = None
    cursor = None

    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)

        # Use user_id here, NOT current_user.id.
        cursor.execute("""
            SELECT id, name, email, password_hash
            FROM users
            WHERE id = %s
        """, (user_id,))

        user_data = cursor.fetchone()

        if user_data:
            return User(
                user_data["id"],
                user_data["name"],
                user_data["email"],
                user_data["password_hash"],
            )

        return None

    finally:
        if cursor is not None:
            cursor.close()
        if connection is not None and connection.is_connected():
            connection.close()


# ---------------- AI CATEGORY PREDICTION ----------------

def predict_category(description):
    text = (description or "").lower()

    categories = {
        "Groceries": [
            "grocery", "groceries", "vegetable", "vegetables",
            "milk", "fruit", "fruits", "supermarket",
        ],
        "Food": [
            "restaurant", "food", "pizza", "burger", "coffee",
            "lunch", "dinner", "breakfast", "snack",
        ],
        "Travel": [
            "uber", "ola", "taxi", "cab", "bus", "train",
            "flight", "petrol", "fuel", "diesel", "metro", "travel",
        ],
        "Shopping": [
            "clothes", "shopping", "shoes", "dress",
            "amazon", "flipkart", "purchase",
        ],
        "Bills": [
            "electricity", "water bill", "internet", "wifi",
            "recharge", "rent", "phone bill", "gas bill",
        ],
        "Health": [
            "medicine", "doctor", "hospital", "pharmacy", "medical",
        ],
        "Entertainment": [
            "movie", "cinema", "netflix", "concert", "game",
            "subscription",
        ],
    }

    for category, keywords in categories.items():
        if any(keyword in text for keyword in keywords):
            return category

    return "Other"


# ---------------- REGISTRATION ----------------

@app.route("/register", methods=["GET", "POST"])
def register():
    if current_user.is_authenticated:
        return redirect(url_for("dashboard"))

    if request.method == "POST":
        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")

        if not name or not email or not password:
            flash("Please complete all fields.", "error")
            return redirect(url_for("register"))

        if len(password) < 8:
            flash("Password must be at least 8 characters.", "error")
            return redirect(url_for("register"))

        connection = None
        cursor = None

        try:
            connection = get_db_connection()
            cursor = connection.cursor(dictionary=True)

            cursor.execute(
                "SELECT id FROM users WHERE email = %s",
                (email,),
            )

            if cursor.fetchone():
                flash("An account with this email already exists.", "error")
                return redirect(url_for("register"))

            password_hash = generate_password_hash(password)

            cursor.execute("""
                INSERT INTO users (name, email, password_hash)
                VALUES (%s, %s, %s)
            """, (name, email, password_hash))

            connection.commit()

        except mysql.connector.Error:
            app.logger.exception("Registration database error")
            flash("Could not create your account. Please try again.", "error")
            return redirect(url_for("register"))

        finally:
            if cursor is not None:
                cursor.close()
            if connection is not None and connection.is_connected():
                connection.close()

        flash("Account created successfully! Please log in.", "success")
        return redirect(url_for("login"))

    return render_template("register.html")


# ---------------- LOGIN ----------------

@app.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("dashboard"))

    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")

        connection = None
        cursor = None
        user_data = None

        try:
            connection = get_db_connection()
            cursor = connection.cursor(dictionary=True)

            cursor.execute("""
                SELECT id, name, email, password_hash
                FROM users
                WHERE email = %s
            """, (email,))

            user_data = cursor.fetchone()

        except mysql.connector.Error:
            app.logger.exception("Login database error")
            flash("Unable to log in. Please check your database connection.", "error")
            return redirect(url_for("login"))

        finally:
            if cursor is not None:
                cursor.close()
            if connection is not None and connection.is_connected():
                connection.close()

        if user_data and check_password_hash(
            user_data["password_hash"], password
        ):
            user = User(
                user_data["id"],
                user_data["name"],
                user_data["email"],
                user_data["password_hash"],
            )

            login_user(user)
            flash("Welcome back!", "success")
            return redirect(url_for("dashboard"))

        flash("Invalid email or password.", "error")
        return redirect(url_for("login"))

    return render_template("login.html")


# ---------------- LOGOUT ----------------

@app.route("/logout")
@login_required
def logout():
    logout_user()
    flash("You have been logged out successfully.", "success")
    return redirect(url_for("login"))


# ---------------- DASHBOARD + AI SPENDING INSIGHTS ----------------

@app.route("/")
@login_required
def dashboard():
    connection = None
    cursor = None

    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)

        # All-time income and expenses
        cursor.execute("""
            SELECT
                COALESCE(SUM(
                    CASE WHEN transaction_type = 'income'
                    THEN amount ELSE 0 END
                ), 0) AS income,
                COALESCE(SUM(
                    CASE WHEN transaction_type = 'expense'
                    THEN amount ELSE 0 END
                ), 0) AS expenses
            FROM transactions
            WHERE user_id = %s
        """, (current_user.id,))

        totals = cursor.fetchone()

        # This month's income and expenses
        cursor.execute("""
            SELECT
                COALESCE(SUM(
                    CASE WHEN transaction_type = 'income'
                    THEN amount ELSE 0 END
                ), 0) AS monthly_income,
                COALESCE(SUM(
                    CASE WHEN transaction_type = 'expense'
                    THEN amount ELSE 0 END
                ), 0) AS monthly_expenses
            FROM transactions
            WHERE user_id = %s
              AND transaction_date >= DATE_FORMAT(CURDATE(), '%Y-%m-01')
              AND transaction_date < DATE_FORMAT(
                  DATE_ADD(CURDATE(), INTERVAL 1 MONTH), '%Y-%m-01'
              )
        """, (current_user.id,))

        monthly_totals = cursor.fetchone()

        # Highest-spending category this month
        cursor.execute("""
            SELECT category, SUM(amount) AS category_total
            FROM transactions
            WHERE user_id = %s
              AND transaction_type = 'expense'
              AND transaction_date >= DATE_FORMAT(CURDATE(), '%Y-%m-01')
              AND transaction_date < DATE_FORMAT(
                  DATE_ADD(CURDATE(), INTERVAL 1 MONTH), '%Y-%m-01'
              )
            GROUP BY category
            ORDER BY category_total DESC
            LIMIT 1
        """, (current_user.id,))

        highest_category = cursor.fetchone()

        # Last month's expenses
        cursor.execute("""
            SELECT COALESCE(SUM(amount), 0) AS previous_expenses
            FROM transactions
            WHERE user_id = %s
              AND transaction_type = 'expense'
              AND transaction_date >= DATE_FORMAT(
                  DATE_SUB(CURDATE(), INTERVAL 1 MONTH), '%Y-%m-01'
              )
              AND transaction_date < DATE_FORMAT(CURDATE(), '%Y-%m-01')
        """, (current_user.id,))

        previous_month = cursor.fetchone()

        # Spending by category for the dashboard
        # This must be inside dashboard(), not load_user().
        cursor.execute("""
            SELECT category, SUM(amount) AS total
            FROM transactions
            WHERE user_id = %s
              AND transaction_type = 'expense'
              AND transaction_date >= DATE_FORMAT(CURDATE(), '%Y-%m-01')
              AND transaction_date < DATE_FORMAT(
                  DATE_ADD(CURDATE(), INTERVAL 1 MONTH), '%Y-%m-01'
              )
            GROUP BY category
            ORDER BY total DESC
        """, (current_user.id,))

        category_spending = cursor.fetchall()


        # Get this user's monthly budget
        cursor.execute("""
            SELECT budget_amount
            FROM monthly_budgets
            WHERE user_id = %s
              AND budget_month = DATE_FORMAT(CURDATE(), '%Y-%m-01')
        """, (current_user.id,))

        monthly_budget_record = cursor.fetchone()

        # Get this user's daily spending limit
        cursor.execute("""
            SELECT daily_limit
            FROM daily_spending_limits
            WHERE user_id = %s
        """, (current_user.id,))

        daily_limit_record = cursor.fetchone()

        # Calculate today's expenses
        cursor.execute("""
            SELECT COALESCE(SUM(amount), 0) AS total
            FROM transactions
            WHERE user_id = %s
              AND transaction_type = 'expense'
              AND transaction_date = CURDATE()
        """, (current_user.id,))

        today_expenses_record = cursor.fetchone()

        # Recent transactions
        cursor.execute("""
            SELECT id, description, amount, transaction_type,
                   category, transaction_date
            FROM transactions
            WHERE user_id = %s
            ORDER BY transaction_date DESC, id DESC
            LIMIT 10
        """, (current_user.id,))

        transactions = cursor.fetchall()

    except mysql.connector.Error:
        app.logger.exception("Dashboard database error")
        flash("Could not load dashboard data. Please check the database.", "error")

        return render_template(
            "index.html",
            income=0,
            expenses=0,
            balance=0,
            transactions=[],
            monthly_income=0,
            monthly_expenses=0,
            insights=[],
            category_percentage=0,
            saving_suggestion="Unable to calculate a suggestion right now.",
            category_spending=[],
        )

    finally:
        if cursor is not None:
            cursor.close()
        if connection is not None and connection.is_connected():
            connection.close()

    income = float(totals["income"] or 0)
    expenses = float(totals["expenses"] or 0)
    monthly_income = float(monthly_totals["monthly_income"] or 0)
    monthly_expenses = float(monthly_totals["monthly_expenses"] or 0)
    previous_expenses = float(previous_month["previous_expenses"] or 0)

    # Highest category's percentage of monthly expenses
    category_percentage = 0

    if highest_category and monthly_expenses > 0:
        category_amount = float(highest_category["category_total"])
        category_percentage = round(
            category_amount / monthly_expenses * 100, 1
        )

    # Personalized saving suggestion
    if highest_category and monthly_expenses > 0:
        category_name = highest_category["category"]
        possible_saving = float(highest_category["category_total"]) * 0.10

        saving_suggestion = (
            f"Consider reducing your {category_name} spending by 10%. "
            f"You could save approximately ₹{possible_saving:,.2f}."
        )
    else:
        saving_suggestion = (
            "Record some expenses this month to receive a personalized saving suggestion."
        )


    # ---------------- DASHBOARD NOTIFICATIONS ----------------

    notifications = []

    monthly_budget = (
        float(monthly_budget_record["budget_amount"])
        if monthly_budget_record else 0
    )

    daily_limit = (
        float(daily_limit_record["daily_limit"])
        if daily_limit_record else 0
    )

    today_expenses = float(today_expenses_record["total"] or 0)

    # Monthly budget notifications
    if monthly_budget > 0:
        monthly_percentage = (monthly_expenses / monthly_budget) * 100

        if monthly_percentage >= 100:
            notifications.append({
                "level": "danger",
                "title": "Monthly Budget Exceeded",
                "message": (
                    f"You have spent ₹{monthly_expenses:,.2f} "
                    f"against your ₹{monthly_budget:,.2f} monthly budget."
                )
            })
        elif monthly_percentage >= 80:
            notifications.append({
                "level": "warning",
                "title": "Monthly Budget Warning",
                "message": (
                    f"You have used {monthly_percentage:.1f}% of your "
                    f"monthly budget."
                )
            })

    # Daily spending notifications
    if daily_limit > 0:
        daily_percentage = (today_expenses / daily_limit) * 100

        if daily_percentage >= 100:
            notifications.append({
                "level": "danger",
                "title": "Daily Spending Limit Reached",
                "message": (
                    f"You have spent ₹{today_expenses:,.2f} today "
                    f"against your ₹{daily_limit:,.2f} daily limit."
                )
            })
        elif daily_percentage >= 80:
            notifications.append({
                "level": "warning",
                "title": "Daily Spending Warning",
                "message": (
                    f"You have used {daily_percentage:.1f}% of your "
                    f"daily spending limit."
                )
            })

    # Generate spending insights
    insights = []

    if highest_category and float(highest_category["category_total"]) > 0:
        category_name = highest_category["category"]
        category_amount = float(highest_category["category_total"])

        insights.append({
            "title": "Highest Spending Category",
            "message": (
                f"{category_name} is your highest-spending category this month, "
                f"at ₹{category_amount:,.2f}."
            ),
        })
    else:
        insights.append({
            "title": "Spending Categories",
            "message": "No expenses recorded for this month yet.",
        })

    if monthly_expenses > monthly_income:
        insights.append({
            "title": "Income and Expenses",
            "message": (
                "Your expenses are higher than your recorded income this month. "
                "Review your spending to help manage your balance."
            ),
        })
    else:
        insights.append({
            "title": "Income and Expenses",
            "message": "Your expenses are within your recorded income this month.",
        })

    if previous_expenses > 0:
        change = (
            (monthly_expenses - previous_expenses) / previous_expenses
        ) * 100

        if change > 0:
            message = f"Your expenses are {change:.1f}% higher than last month."
        elif change < 0:
            message = f"Your expenses are {abs(change):.1f}% lower than last month."
        else:
            message = "Your expenses are unchanged from last month."

        insights.append({
            "title": "Monthly Spending Trend",
            "message": message,
        })

    elif monthly_expenses > 0:
        insights.append({
            "title": "Monthly Spending Trend",
            "message": (
                "You have expenses this month, but no expenses were recorded "
                "last month for comparison."
            ),
        })

    return render_template(
        "index.html",
        income=income,
        expenses=expenses,
        balance=income - expenses,
        transactions=transactions,
        monthly_income=monthly_income,
        monthly_expenses=monthly_expenses,
        insights=insights,
        category_percentage=category_percentage,
        saving_suggestion=saving_suggestion,
        category_spending=category_spending,
        notifications=notifications,
    )


# ---------------- ADD TRANSACTION ----------------

@app.route("/add", methods=["POST"])
@login_required
def add_transaction():
    description = request.form.get("description", "").strip()
    amount_text = request.form.get("amount", "").strip()
    transaction_type = request.form.get("transaction_type", "")
    transaction_date = request.form.get("transaction_date", "")
    category = request.form.get("category", "").strip()

    if not description or not amount_text or not transaction_date:
        flash("Please complete all required fields.", "error")
        return redirect(url_for("dashboard"))

    try:
        amount = float(amount_text)
        if amount <= 0:
            raise ValueError
    except ValueError:
        flash("Enter an amount greater than zero.", "error")
        return redirect(url_for("dashboard"))

    if transaction_type not in ("income", "expense"):
        flash("Choose income or expense.", "error")
        return redirect(url_for("dashboard"))

    if transaction_type == "expense":
        category = category or predict_category(description)
    else:
        category = category or "Income"

    connection = None
    cursor = None

    try:
        connection = get_db_connection()
        cursor = connection.cursor()

        cursor.execute("""
            INSERT INTO transactions
                (user_id, description, amount, transaction_type,
                 category, transaction_date)
            VALUES (%s, %s, %s, %s, %s, %s)
        """, (
            current_user.id,
            description,
            amount,
            transaction_type,
            category,
            transaction_date,
        ))

        connection.commit()
        flash("Transaction added successfully!", "success")

    except mysql.connector.Error:
        app.logger.exception("Add transaction database error")
        flash("Could not add the transaction. Please try again.", "error")

    finally:
        if cursor is not None:
            cursor.close()
        if connection is not None and connection.is_connected():
            connection.close()

    return redirect(url_for("dashboard"))


# ---------------- DELETE TRANSACTION ----------------

@app.route("/delete/<int:transaction_id>", methods=["POST"])
@login_required
def delete_transaction(transaction_id):
    connection = None
    cursor = None

    try:
        connection = get_db_connection()
        cursor = connection.cursor()

        cursor.execute("""
            DELETE FROM transactions
            WHERE id = %s AND user_id = %s
        """, (transaction_id, current_user.id))

        connection.commit()
        flash("Transaction deleted.", "success")

    except mysql.connector.Error:
        app.logger.exception("Delete transaction database error")
        flash("Could not delete the transaction.", "error")

    finally:
        if cursor is not None:
            cursor.close()
        if connection is not None and connection.is_connected():
            connection.close()

    return redirect(url_for("dashboard"))


# ---------------- EDIT TRANSACTION ----------------

@app.route("/edit/<int:transaction_id>", methods=["POST"])
@login_required
def edit_transaction(transaction_id):
    description = request.form.get("description", "").strip()
    amount_text = request.form.get("amount", "").strip()
    category = request.form.get("category", "").strip()
    transaction_date = request.form.get("transaction_date", "").strip()
    transaction_type = request.form.get("transaction_type", "")

    if not description or not amount_text or not category or not transaction_date:
        flash("Please complete all fields.", "error")
        return redirect(url_for("dashboard"))

    try:
        amount = float(amount_text)
        if amount <= 0:
            raise ValueError
    except ValueError:
        flash("Enter an amount greater than zero.", "error")
        return redirect(url_for("dashboard"))

    if transaction_type not in ("income", "expense"):
        flash("Choose income or expense.", "error")
        return redirect(url_for("dashboard"))

    connection = None
    cursor = None

    try:
        connection = get_db_connection()
        cursor = connection.cursor()

        cursor.execute("""
            UPDATE transactions
            SET description = %s,
                amount = %s,
                category = %s,
                transaction_date = %s,
                transaction_type = %s
            WHERE id = %s AND user_id = %s
        """, (
            description,
            amount,
            category,
            transaction_date,
            transaction_type,
            transaction_id,
            current_user.id,
        ))

        connection.commit()
        flash("Transaction updated successfully!", "success")

    except mysql.connector.Error:
        app.logger.exception("Edit transaction database error")
        flash("Could not update the transaction.", "error")

    finally:
        if cursor is not None:
            cursor.close()
        if connection is not None and connection.is_connected():
            connection.close()

    return redirect(url_for("dashboard"))


# ---------------- REPORTS AND CHARTS ----------------

@app.route("/reports")
@login_required
def reports():
    connection = None
    cursor = None

    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)

        # Overall financial summary
        cursor.execute("""
            SELECT
                COALESCE(SUM(
                    CASE WHEN transaction_type = 'income'
                    THEN amount ELSE 0 END
                ), 0) AS total_income,
                COALESCE(SUM(
                    CASE WHEN transaction_type = 'expense'
                    THEN amount ELSE 0 END
                ), 0) AS total_expenses
            FROM transactions
            WHERE user_id = %s
        """, (current_user.id,))

        summary = cursor.fetchone()

        # Expenses grouped by category for the current month
        cursor.execute("""
            SELECT category, SUM(amount) AS total
            FROM transactions
            WHERE user_id = %s
              AND transaction_type = 'expense'
              AND transaction_date >= DATE_FORMAT(CURDATE(), '%Y-%m-01')
              AND transaction_date < DATE_FORMAT(
                  DATE_ADD(CURDATE(), INTERVAL 1 MONTH), '%Y-%m-01'
              )
            GROUP BY category
            ORDER BY total DESC
        """, (current_user.id,))

        category_rows = cursor.fetchall()

        # Income and expenses by month for the last six calendar months
        cursor.execute("""
            SELECT
                DATE_FORMAT(transaction_date, '%Y-%m') AS month_key,
                DATE_FORMAT(transaction_date, '%b %Y') AS month_label,
                COALESCE(SUM(
                    CASE WHEN transaction_type = 'income'
                    THEN amount ELSE 0 END
                ), 0) AS income,
                COALESCE(SUM(
                    CASE WHEN transaction_type = 'expense'
                    THEN amount ELSE 0 END
                ), 0) AS expenses
            FROM transactions
            WHERE user_id = %s
              AND transaction_date >= DATE_FORMAT(
                  DATE_SUB(CURDATE(), INTERVAL 5 MONTH), '%Y-%m-01'
              )
              AND transaction_date < DATE_FORMAT(
                  DATE_ADD(CURDATE(), INTERVAL 1 MONTH), '%Y-%m-01'
              )
            GROUP BY month_key, month_label
            ORDER BY month_key
        """, (current_user.id,))

        monthly_rows = cursor.fetchall()

        total_income = float(summary["total_income"] or 0)
        total_expenses = float(summary["total_expenses"] or 0)

        category_labels = [
            row["category"] for row in category_rows
        ]
        category_values = [
            float(row["total"]) for row in category_rows
        ]

        month_labels = [
            row["month_label"] for row in monthly_rows
        ]
        income_values = [
            float(row["income"]) for row in monthly_rows
        ]
        expense_values = [
            float(row["expenses"]) for row in monthly_rows
        ]

    except mysql.connector.Error:
        app.logger.exception("Reports database error")
        flash("Unable to load reports. Please check your database connection.", "error")
        return redirect(url_for("dashboard"))

    finally:
        if cursor is not None:
            cursor.close()
        if connection is not None and connection.is_connected():
            connection.close()

    return render_template(
        "reports.html",
        total_income=total_income,
        total_expenses=total_expenses,
        balance=total_income - total_expenses,
        category_labels=category_labels,
        category_values=category_values,
        month_labels=month_labels,
        income_values=income_values,
        expense_values=expense_values,
    )


@app.route("/api/monthly-spending")
@login_required
def monthly_spending():
    connection = get_db_connection()

    if connection is None:
        return {"error": "Database connection failed"}, 500

    cursor = None

    try:
        cursor = connection.cursor(dictionary=True)

        cursor.execute(
            """
            SELECT
                DATE_FORMAT(transaction_date, '%Y-%m') AS month,
                SUM(amount) AS total
            FROM transactions
            WHERE user_id = %s
              AND transaction_type = 'expense'
            GROUP BY DATE_FORMAT(transaction_date, '%Y-%m')
            ORDER BY month ASC
            """,
            (current_user.id,)
        )

        results = cursor.fetchall()

        return {
            "labels": [row["month"] for row in results],
            "values": [float(row["total"]) for row in results]
        }

    except Exception as error:
        print("Monthly spending error:", error)
        return {"error": "Could not load monthly spending"}, 500

    finally:
        if cursor is not None:
            cursor.close()
        connection.close()


# ---------------- MONTHLY BUDGET ----------------

@app.route("/budget", methods=["GET", "POST"])
@login_required
def budget():
    connection = None
    cursor = None

    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)

        if request.method == "POST":
            try:
                budget_amount = float(request.form.get("budget_amount", 0))

                if budget_amount <= 0:
                    flash("Please enter a budget greater than zero.", "error")
                    return redirect(url_for("budget"))

                cursor.execute("""
                    INSERT INTO monthly_budgets
                        (user_id, budget_month, budget_amount)
                    VALUES
                        (%s, DATE_FORMAT(CURDATE(), '%Y-%m-01'), %s)
                    ON DUPLICATE KEY UPDATE
                        budget_amount = VALUES(budget_amount)
                """, (current_user.id, budget_amount))

                connection.commit()
                flash("Monthly budget saved successfully!", "success")
                return redirect(url_for("budget"))

            except ValueError:
                flash("Please enter a valid budget amount.", "error")
                return redirect(url_for("budget"))

        # Get this user's budget for the current month
        cursor.execute("""
            SELECT budget_amount
            FROM monthly_budgets
            WHERE user_id = %s
              AND budget_month = DATE_FORMAT(CURDATE(), '%Y-%m-01')
        """, (current_user.id,))

        budget_record = cursor.fetchone()
        budget_amount = (
            float(budget_record["budget_amount"])
            if budget_record else 0
        )

        # Calculate this month's expenses
        cursor.execute("""
            SELECT COALESCE(SUM(amount), 0) AS total_expenses
            FROM transactions
            WHERE user_id = %s
              AND transaction_type = 'expense'
              AND transaction_date >= DATE_FORMAT(CURDATE(), '%Y-%m-01')
              AND transaction_date < DATE_FORMAT(
                  DATE_ADD(CURDATE(), INTERVAL 1 MONTH), '%Y-%m-01'
              )
        """, (current_user.id,))

        expense_record = cursor.fetchone()
        monthly_expenses = float(expense_record["total_expenses"] or 0)

        remaining_budget = budget_amount - monthly_expenses

        percentage_used = (
            round((monthly_expenses / budget_amount) * 100, 1)
            if budget_amount > 0 else 0
        )

    except mysql.connector.Error:
        app.logger.exception("Monthly budget database error")
        flash("Unable to load the budget. Please check the database.", "error")
        return redirect(url_for("dashboard"))

    finally:
        if cursor is not None:
            cursor.close()
        if connection is not None and connection.is_connected():
            connection.close()

    return render_template(
        "budget.html",
        budget_amount=budget_amount,
        monthly_expenses=monthly_expenses,
        remaining_budget=remaining_budget,
        percentage_used=percentage_used
    )


@app.route("/export-transactions")
@login_required
def export_transactions():
    connection = get_db_connection()

    if connection is None:
        flash("Could not connect to the database.", "error")
        return redirect(url_for("dashboard"))

    try:
        cursor = connection.cursor(dictionary=True)

        cursor.execute(
            """
            SELECT description, amount, transaction_type,
                   category, transaction_date
            FROM transactions
            WHERE user_id = %s
            ORDER BY transaction_date DESC
            """,
            (current_user.id,)
        )

        transactions = cursor.fetchall()

        output = StringIO()
        writer = csv.writer(output)

        writer.writerow([
            "Description",
            "Amount",
            "Type",
            "Category",
            "Date"
        ])

        for transaction in transactions:
            writer.writerow([
                transaction["description"],
                transaction["amount"],
                transaction["transaction_type"],
                transaction["category"],
                transaction["transaction_date"]
            ])

        from flask import Response

        return Response(
            output.getvalue(),
            mimetype="text/csv",
            headers={
                "Content-Disposition":
                    "attachment; filename=transactions.csv"
            }
        )

    except Exception as error:
        print("CSV export error:", error)
        flash("Could not export transactions.", "error")
        return redirect(url_for("dashboard"))

    finally:
        if "cursor" in locals():
            cursor.close()
        connection.close()


@app.route("/daily-limit", methods=["GET", "POST"])
@login_required
def daily_limit():
    connection = get_db_connection()

    if connection is None:
        flash("Could not connect to the database.", "error")
        return redirect(url_for("dashboard"))

    cursor = None

    try:
        cursor = connection.cursor(dictionary=True)
        user_id = current_user.id

        if request.method == "POST":
            try:
                limit_amount = float(request.form.get("daily_limit", 0))

                if limit_amount <= 0:
                    flash("Please enter a daily limit greater than zero.", "error")
                else:
                    cursor.execute(
                        """
                        INSERT INTO daily_spending_limits (user_id, daily_limit)
                        VALUES (%s, %s)
                        ON DUPLICATE KEY UPDATE daily_limit = %s
                        """,
                        (user_id, limit_amount, limit_amount)
                    )
                    connection.commit()
                    flash("Daily spending limit saved successfully.", "success")

            except (ValueError, TypeError):
                flash("Please enter a valid amount.", "error")

            return redirect(url_for("daily_limit"))

        cursor.execute(
            """
            SELECT daily_limit
            FROM daily_spending_limits
            WHERE user_id = %s
            """,
            (user_id,)
        )
        saved_limit = cursor.fetchone()

        daily_limit_amount = (
            float(saved_limit["daily_limit"]) if saved_limit else 0
        )

        cursor.execute(
            """
            SELECT COALESCE(SUM(amount), 0) AS total
            FROM transactions
            WHERE user_id = %s
              AND transaction_type = 'expense'
              AND transaction_date = CURDATE()
            """,
            (user_id,)
        )
        today_expenses = float(cursor.fetchone()["total"])

        percentage_used = (
            (today_expenses / daily_limit_amount) * 100
            if daily_limit_amount > 0 else 0
        )

        return render_template(
            "daily_limit.html",
            daily_limit_amount=daily_limit_amount,
            today_expenses=today_expenses,
            percentage_used=percentage_used
        )

    except Exception as error:
        print("Daily spending limit error:", error)
        flash("Could not load the daily spending limit.", "error")
        return redirect(url_for("dashboard"))

    finally:
        if cursor is not None:
            cursor.close()
        connection.close()


# ---------------- DATABASE HEALTH CHECK ----------------
@app.route("/health")
def health():
    connection = None

    try:
        connection = get_db_connection()
        return {"status": "ok", "database": "connected"}

    except mysql.connector.Error:
        app.logger.exception("Database health check failed")
        return {"status": "error", "database": "disconnected"}, 500

    finally:
        if connection is not None and connection.is_connected():
            connection.close()


# ---------------- START APPLICATION ----------------

if __name__ == "__main__":
    try:
        initialize_database()
        print("Database connected and tables are ready.")
        app.run(debug=True)

    except mysql.connector.Error as error:
        print("Database connection failed:", error)
        print("Check your MySQL server and .env database settings.")
