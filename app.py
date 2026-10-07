from flask import Flask, render_template, request, redirect, url_for, flash, session
from werkzeug.security import generate_password_hash, check_password_hash
from db import get_db_connection
from mail import send_verification_email, send_password_reset_email
from dotenv import load_dotenv
import os
import re
import secrets
from datetime import datetime, timedelta, time
from urllib.parse import quote


# Load environment variables
load_dotenv()

app = Flask(__name__)

app.config["SECRET_KEY"] = os.getenv("FLASK_SECRET_KEY")

# =========================================================
# POLICIES & SAFETY
# =========================================================

@app.route("/terms")
def terms():
    return render_template("Policies & safety/terms.html")

@app.route("/privacy")
def privacy():
    return render_template("Policies & safety/privacy.html")

@app.route("/community-guidelines")
def community_guidelines():
    return render_template("Policies & safety/community_guidelines.html")

@app.route("/safety-tips")
def safety_tips():
    return render_template("Policies & safety/safety_tips.html")

@app.route("/cookie-policy")
def cookie_policy():
    return render_template("Policies & safety/cookie_policy.html")

@app.route("/accessibility")
def accessibility():
    return render_template("Policies & safety/accessibility.html")



def normalize_mysql_time(value):
    """
    Convert a MySQL TIME value returned by PyMySQL
    into a Python datetime.time object.
    """

    if isinstance(value, time):
        return value

    if isinstance(value, timedelta):
        total_seconds = int(value.total_seconds())

        hours, remainder = divmod(total_seconds, 3600)
        minutes, seconds = divmod(remainder, 60)

        return time(hours, minutes, seconds)

    return value


def auto_complete_past_sessions(cursor):
    """
    Automatically marks scheduled sessions as 'Completed'
    if they are not cancelled and their scheduled date and end time are in the past.
    """
    now = datetime.now()
    cursor.execute(
        """
        UPDATE learning_sessions
        SET status = 'Completed'
        WHERE status = 'Scheduled'
          AND (
            session_date < %s
            OR (session_date = %s AND end_time <= %s)
          )
        """,
        (now.date(), now.date(), now.time())
    )

# =========================================================
# HOME
# =========================================================

@app.route("/", endpoint="home")
@app.route("/index", endpoint="index")
def home():
    return render_template("index.html")


# =========================================================
# REGISTER
# =========================================================

@app.route("/register", methods=["GET", "POST"])
def register():

    if request.method == "POST":

        # Get form data
        full_name = request.form.get("full_name", "").strip()
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        confirm_password = request.form.get("confirm_password", "")
        terms = request.form.get("terms")

        # -------------------------------------------------
        # Required fields
        # -------------------------------------------------

        if not full_name or not email or not password or not confirm_password:
            flash("All fields are required.", "error")
            return redirect(url_for("register"))

        # -------------------------------------------------
        # Name validation
        # -------------------------------------------------

        if len(full_name) < 2 or len(full_name) > 100:
            flash(
                "Full name must be between 2 and 100 characters.",
                "error"
            )
            return redirect(url_for("register"))

        if not re.fullmatch(r"[A-Za-z ]+", full_name):
            flash(
                "Full name can contain only letters and spaces.",
                "error"
            )
            return redirect(url_for("register"))

        # -------------------------------------------------
        # Email validation
        # -------------------------------------------------

        email_pattern = r"^[^@\s]+@[^@\s]+\.[^@\s]+$"

        if not re.fullmatch(email_pattern, email):
            flash("Please enter a valid email address.", "error")
            return redirect(url_for("register"))

        # -------------------------------------------------
        # Password validation
        # -------------------------------------------------

        if len(password) < 8 or len(password) > 15:
            flash(
                "Password must be between 8 and 15 characters.",
                "error"
            )
            return redirect(url_for("register"))

        if not re.search(r"[A-Za-z]", password):
            flash(
                "Password must contain at least one letter.",
                "error"
            )
            return redirect(url_for("register"))

        if not re.search(r"[0-9]", password):
            flash(
                "Password must contain at least one number.",
                "error"
            )
            return redirect(url_for("register"))

        if not re.search(r"[^A-Za-z0-9]", password):
            flash(
                "Password must contain at least one symbol.",
                "error"
            )
            return redirect(url_for("register"))

        # -------------------------------------------------
        # Confirm password
        # -------------------------------------------------

        if password != confirm_password:
            flash("Passwords do not match.", "error")
            return redirect(url_for("register"))

        # -------------------------------------------------
        # Terms and Privacy Policy
        # -------------------------------------------------

        if not terms:
            flash(
                "Please accept the Terms and Privacy Policy.",
                "error"
            )
            return redirect(url_for("register"))

        connection = None

        try:

            connection = get_db_connection()

            with connection.cursor() as cursor:

                # -------------------------------------------------
                # Check whether email already exists
                # -------------------------------------------------

                cursor.execute(
                    """
                    SELECT id, email_verified
                    FROM users
                    WHERE email = %s
                    """,
                    (email,)
                )

                existing_user = cursor.fetchone()

                if existing_user:

                    # Email already belongs to a verified account
                    if existing_user["email_verified"]:
                        flash(
                            "An account with this email already exists.",
                            "error"
                        )
                        return redirect(url_for("register"))

                    # Account exists but email is not verified
                    user_id = existing_user["id"]

                else:

                    # -------------------------------------------------
                    # Create new user
                    # -------------------------------------------------

                    password_hash = generate_password_hash(password)

                    cursor.execute(
                        """
                        INSERT INTO users
                        (full_name, email, password_hash)
                        VALUES (%s, %s, %s)
                        """,
                        (
                            full_name,
                            email,
                            password_hash
                        )
                    )

                    user_id = cursor.lastrowid

                # -------------------------------------------------
                # Generate 6-digit OTP
                # -------------------------------------------------

                otp = f"{secrets.randbelow(1000000):06d}"

                # Hash OTP before storing it
                otp_hash = generate_password_hash(otp)

                # OTP expires after 10 minutes
                expiry_time = datetime.now() + timedelta(minutes=10)

                # Store OTP and reset attempts
                cursor.execute(
                    """
                    UPDATE users
                    SET verification_code_hash = %s,
                        verification_code_expires_at = %s,
                        verification_attempts = 0
                    WHERE id = %s
                    """,
                    (
                        otp_hash,
                        expiry_time,
                        user_id
                    )
                )

            # Save database changes
            connection.commit()

            # -------------------------------------------------
            # Send OTP email
            # -------------------------------------------------

            send_verification_email(email, otp)

        except Exception as error:

            if connection:
                connection.rollback()

            print("Registration error:", error)

            flash(
                "Something went wrong while creating your account. "
                "Please try again.",
                "error"
            )

            return redirect(url_for("register"))

        finally:

            if connection:
                connection.close()

        # -------------------------------------------------
        # Remember email for verification
        # -------------------------------------------------

        session["verification_email"] = email

        return redirect(url_for("verify_email"))

    return render_template("user/register.html")


# =========================================================
# VERIFY EMAIL
# =========================================================

@app.route("/verify-email", methods=["GET", "POST"])
def verify_email():

    # Get email stored during registration
    email = session.get("verification_email")

    if not email:
        flash("Please register first.", "error")
        return redirect(url_for("register"))

    if request.method == "POST":

        # Get OTP
        otp = request.form.get("otp", "").strip()

        # -------------------------------------------------
        # OTP validation
        # -------------------------------------------------

        if not otp:
            flash(
                "Please enter the verification code.",
                "error"
            )
            return redirect(url_for("verify_email"))

        if not re.fullmatch(r"\d{6}", otp):
            flash(
                "Verification code must be 6 digits.",
                "error"
            )
            return redirect(url_for("verify_email"))

        connection = None

        try:

            connection = get_db_connection()

            with connection.cursor() as cursor:

                # -------------------------------------------------
                # Get user verification information
                # -------------------------------------------------

                cursor.execute(
                    """
                    SELECT
                        id,
                        verification_code_hash,
                        verification_code_expires_at,
                        verification_attempts
                    FROM users
                    WHERE email = %s
                    """,
                    (email,)
                )

                user = cursor.fetchone()

                # -------------------------------------------------
                # Check account
                # -------------------------------------------------

                if not user:
                    flash(
                        "Account not found.",
                        "error"
                    )
                    return redirect(url_for("register"))

                # -------------------------------------------------
                # Check whether OTP exists
                # -------------------------------------------------

                if not user["verification_code_hash"]:
                    flash(
                        "No verification code found. "
                        "Please request a new code.",
                        "error"
                    )
                    return redirect(url_for("verify_email"))

                # -------------------------------------------------
                # Maximum 5 incorrect attempts
                # -------------------------------------------------

                if user["verification_attempts"] >= 5:
                    flash(
                        "Too many incorrect attempts. "
                        "Please request a new code.",
                        "error"
                    )
                    return redirect(url_for("verify_email"))

                # -------------------------------------------------
                # Check OTP expiry
                # -------------------------------------------------

                if datetime.now() > user["verification_code_expires_at"]:
                    flash(
                        "Verification code has expired. "
                        "Please request a new code.",
                        "error"
                    )
                    return redirect(url_for("verify_email"))

                # -------------------------------------------------
                # Check OTP
                # -------------------------------------------------

                if not check_password_hash(
                    user["verification_code_hash"],
                    otp
                ):

                    # Increase failed attempt count
                    cursor.execute(
                        """
                        UPDATE users
                        SET verification_attempts =
                            verification_attempts + 1
                        WHERE id = %s
                        """,
                        (user["id"],)
                    )

                    connection.commit()

                    # Current attempts before increment
                    attempts_used = user["verification_attempts"] + 1

                    attempts_left = 5 - attempts_used

                    if attempts_left > 0:

                        flash(
                            f"Invalid verification code. "
                            f"{attempts_left} attempts remaining.",
                            "error"
                        )

                    else:

                        flash(
                            "Too many incorrect attempts. "
                            "Please request a new code.",
                            "error"
                        )

                    return redirect(url_for("verify_email"))

                # -------------------------------------------------
                # OTP is correct
                # -------------------------------------------------

                cursor.execute(
                    """
                    UPDATE users
                    SET email_verified = TRUE,
                        verification_code_hash = NULL,
                        verification_code_expires_at = NULL,
                        verification_attempts = 0
                    WHERE id = %s
                    """,
                    (user["id"],)
                )

            # Save verification
            connection.commit()

        except Exception as error:

            if connection:
                connection.rollback()

            print("Verification error:", error)

            flash(
                "Something went wrong while verifying your email.",
                "error"
            )

            return redirect(url_for("verify_email"))

        finally:

            if connection:
                connection.close()

        # -------------------------------------------------
        # Verification successful
        # -------------------------------------------------

        session.pop("verification_email", None)

        flash(
            "Your email has been verified successfully.",
            "success"
        )

        # Temporary response until login is created
        return "Email verified successfully!"

    return render_template("user/verify_email.html")

# =========================================================
# FORGOT PASSWORD
# =========================================================

@app.route("/forgot-password", methods=["GET", "POST"])
def forgot_password():

    if request.method == "POST":

        email = request.form.get("email", "").strip().lower()

        # -------------------------------------------------
        # Validate email
        # -------------------------------------------------

        if not email:
            flash("Please enter your email address.", "error")
            return redirect(url_for("forgot_password"))

        email_pattern = r"^[^@\s]+@[^@\s]+\.[^@\s]+$"

        if not re.fullmatch(email_pattern, email):
            flash("Please enter a valid email address.", "error")
            return redirect(url_for("forgot_password"))

        connection = None

        try:

            connection = get_db_connection()

            with connection.cursor() as cursor:

                # -------------------------------------------------
                # Find account
                # -------------------------------------------------

                cursor.execute(
                    """
                    SELECT id, email_verified
                    FROM users
                    WHERE email = %s
                    """,
                    (email,)
                )

                user = cursor.fetchone()

                # -------------------------------------------------
                # Account does not exist
                # -------------------------------------------------

                if not user:
                    flash(
                        "No account found with this email address.",
                        "error"
                    )
                    return redirect(url_for("forgot_password"))

                # -------------------------------------------------
                # Email must be verified
                # -------------------------------------------------

                if not user["email_verified"]:
                    flash(
                        "Please verify your email before resetting "
                        "your password.",
                        "error"
                    )
                    return redirect(url_for("forgot_password"))

                # -------------------------------------------------
                # Generate 6-digit OTP
                # -------------------------------------------------

                otp = f"{secrets.randbelow(1000000):06d}"

                # Hash OTP before storing
                otp_hash = generate_password_hash(otp)

                # OTP expires after 10 minutes
                expiry_time = datetime.now() + timedelta(minutes=10)

                # Reset failed attempts
                cursor.execute(
                    """
                    UPDATE users
                    SET reset_code_hash = %s,
                        reset_code_expires_at = %s,
                        reset_attempts = 0
                    WHERE id = %s
                    """,
                    (
                        otp_hash,
                        expiry_time,
                        user["id"]
                    )
                )

            connection.commit()

            # -------------------------------------------------
            # Send reset OTP
            # -------------------------------------------------

            send_verification_email(email, otp)

            # Remember email for password reset
            session["reset_email"] = email

            flash(
                "A verification code has been sent to your email.",
                "success"
            )

            return redirect(url_for("reset_password"))

        except Exception as error:

            if connection:
                connection.rollback()

            print("Forgot password error:", error)

            flash(
                "Something went wrong. Please try again.",
                "error"
            )

            return redirect(url_for("forgot_password"))

        finally:

            if connection:
                connection.close()

    return render_template("user/forgot_password.html")

# =========================================================
# RESET PASSWORD - OTP VERIFICATION
# =========================================================

@app.route("/reset-password", methods=["GET", "POST"])
def reset_password():

    # Get email stored during forgot-password process
    email = session.get("reset_email")

    if not email:
        flash(
            "Please start the password reset process first.",
            "error"
        )
        return redirect(url_for("forgot_password"))

    if request.method == "POST":

        otp = request.form.get("otp", "").strip()

        # -------------------------------------------------
        # OTP validation
        # -------------------------------------------------

        if not otp:
            flash(
                "Please enter the verification code.",
                "error"
            )
            return redirect(url_for("reset_password"))

        if not re.fullmatch(r"\d{6}", otp):
            flash(
                "Verification code must be 6 digits.",
                "error"
            )
            return redirect(url_for("reset_password"))

        connection = None

        try:

            connection = get_db_connection()

            with connection.cursor() as cursor:

                # -------------------------------------------------
                # Get reset information
                # -------------------------------------------------

                cursor.execute(
                    """
                    SELECT
                        id,
                        reset_code_hash,
                        reset_code_expires_at,
                        reset_attempts
                    FROM users
                    WHERE email = %s
                    """,
                    (email,)
                )

                user = cursor.fetchone()

                # -------------------------------------------------
                # Check account
                # -------------------------------------------------

                if not user:
                    flash(
                        "Account not found.",
                        "error"
                    )
                    return redirect(url_for("forgot_password"))

                # -------------------------------------------------
                # Check whether reset OTP exists
                # -------------------------------------------------

                if not user["reset_code_hash"]:
                    flash(
                        "No verification code found. "
                        "Please start again.",
                        "error"
                    )
                    return redirect(url_for("forgot_password"))

                # -------------------------------------------------
                # Maximum 5 incorrect attempts
                # -------------------------------------------------

                if user["reset_attempts"] >= 5:
                    flash(
                        "Too many incorrect attempts. "
                        "Please request a new code.",
                        "error"
                    )
                    return redirect(url_for("reset_password"))

                # -------------------------------------------------
                # Check OTP expiry
                # -------------------------------------------------

                if datetime.now() > user["reset_code_expires_at"]:
                    flash(
                        "Verification code has expired. "
                        "Please request a new code.",
                        "error"
                    )
                    return redirect(url_for("forgot_password"))

                # -------------------------------------------------
                # Check OTP
                # -------------------------------------------------

                if not check_password_hash(
                    user["reset_code_hash"],
                    otp
                ):

                    cursor.execute(
                        """
                        UPDATE users
                        SET reset_attempts =
                            reset_attempts + 1
                        WHERE id = %s
                        """,
                        (user["id"],)
                    )

                    connection.commit()

                    attempts_used = user["reset_attempts"] + 1
                    attempts_left = 5 - attempts_used

                    if attempts_left > 0:

                        flash(
                            f"Invalid verification code. "
                            f"{attempts_left} attempts remaining.",
                            "error"
                        )

                    else:

                        flash(
                            "Too many incorrect attempts. "
                            "Please request a new code.",
                            "error"
                        )

                    return redirect(url_for("reset_password"))

                # -------------------------------------------------
                # OTP is correct
                # -------------------------------------------------

                session["reset_verified"] = True

                flash(
                    "Verification successful. "
                    "You can now create a new password.",
                    "success"
                )

                return redirect(url_for("new_password"))

        except Exception as error:

            if connection:
                connection.rollback()

            print("Reset password verification error:", error)

            flash(
                "Something went wrong. Please try again.",
                "error"
            )

            return redirect(url_for("reset_password"))

        finally:

            if connection:
                connection.close()

    return render_template("user/reset_password.html")


# =========================================================
# NEW PASSWORD
# =========================================================

@app.route("/new-password", methods=["GET", "POST"])
def new_password():

    email = session.get("reset_email")
    reset_verified = session.get("reset_verified")

    # User must complete OTP verification first
    if not email or not reset_verified:
        flash(
            "Please verify your email first.",
            "error"
        )
        return redirect(url_for("forgot_password"))

    if request.method == "POST":

        password = request.form.get("password", "")
        confirm_password = request.form.get("confirm_password", "")

        # -------------------------------------------------
        # Password validation
        # -------------------------------------------------

        if not password or not confirm_password:
            flash(
                "All fields are required.",
                "error"
            )
            return redirect(url_for("new_password"))

        if len(password) < 8 or len(password) > 15:
            flash(
                "Password must be between 8 and 15 characters.",
                "error"
            )
            return redirect(url_for("new_password"))

        if not re.search(r"[A-Za-z]", password):
            flash(
                "Password must contain at least one letter.",
                "error"
            )
            return redirect(url_for("new_password"))

        if not re.search(r"[0-9]", password):
            flash(
                "Password must contain at least one number.",
                "error"
            )
            return redirect(url_for("new_password"))

        if not re.search(r"[^A-Za-z0-9]", password):
            flash(
                "Password must contain at least one symbol.",
                "error"
            )
            return redirect(url_for("new_password"))

        if password != confirm_password:
            flash(
                "Passwords do not match.",
                "error"
            )
            return redirect(url_for("new_password"))

        connection = None

        try:

            connection = get_db_connection()

            with connection.cursor() as cursor:

                # -------------------------------------------------
                # Find user
                # -------------------------------------------------

                cursor.execute(
                    """
                    SELECT id
                    FROM users
                    WHERE email = %s
                    """,
                    (email,)
                )

                user = cursor.fetchone()

                if not user:
                    flash(
                        "Account not found.",
                        "error"
                    )
                    return redirect(url_for("forgot_password"))

                # -------------------------------------------------
                # Hash new password
                # -------------------------------------------------

                password_hash = generate_password_hash(password)

                # -------------------------------------------------
                # Update password and clear reset data
                # -------------------------------------------------

                cursor.execute(
                    """
                    UPDATE users
                    SET password_hash = %s,
                        reset_code_hash = NULL,
                        reset_code_expires_at = NULL,
                        reset_attempts = 0
                    WHERE id = %s
                    """,
                    (
                        password_hash,
                        user["id"]
                    )
                )

            connection.commit()

        except Exception as error:

            if connection:
                connection.rollback()

            print("New password error:", error)

            flash(
                "Something went wrong. Please try again.",
                "error"
            )

            return redirect(url_for("new_password"))

        finally:

            if connection:
                connection.close()

        # -------------------------------------------------
        # Clear password reset session
        # -------------------------------------------------

        session.pop("reset_email", None)
        session.pop("reset_verified", None)

        flash(
            "Your password has been updated successfully. "
            "You can now log in.",
            "success"
        )

        return redirect(url_for("login"))

    return render_template("user/new_password.html")


# =========================================================
# LOGIN
# =========================================================

@app.route("/login", methods=["GET", "POST"])
def login():

    if request.method == "POST":

        # Get form data
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")

        # -------------------------------------------------
        # Required fields
        # -------------------------------------------------

        if not email or not password:
            flash(
                "Email and password are required.",
                "error"
            )
            return redirect(url_for("login"))

        connection = None

        try:

            connection = get_db_connection()

            with connection.cursor() as cursor:

                # -------------------------------------------------
                # Find user
                # -------------------------------------------------

                cursor.execute(
                    """
                    SELECT
                        id,
                        full_name,
                        email,
                        password_hash,
                        email_verified
                    FROM users
                    WHERE email = %s
                    """,
                    (email,)
                )

                user = cursor.fetchone()

                # -------------------------------------------------
                # Account does not exist
                # -------------------------------------------------

                if not user:
                    flash(
                        "Invalid email or password.",
                        "error"
                    )
                    return redirect(url_for("login"))

                # -------------------------------------------------
                # Email verification check
                # -------------------------------------------------

                if not user["email_verified"]:

                    session["verification_email"] = email

                    flash(
                        "Please verify your email before logging in.",
                        "error"
                    )

                    return redirect(url_for("verify_email"))

                # -------------------------------------------------
                # Check password
                # -------------------------------------------------

                if not check_password_hash(
                    user["password_hash"],
                    password
                ):
                    flash(
                        "Invalid email or password.",
                        "error"
                    )
                    return redirect(url_for("login"))

                # -------------------------------------------------
                # Create login session
                # -------------------------------------------------

                session.clear()

                session["user_id"] = user["id"]
                session["user_name"] = user["full_name"]
                session["user_email"] = user["email"]
                session["logged_in"] = True

            connection.commit()

        except Exception as error:

            if connection:
                connection.rollback()

            print("Login error:", error)

            flash(
                "Something went wrong. Please try again.",
                "error"
            )

            return redirect(url_for("login"))

        finally:

            if connection:
                connection.close()

        # -------------------------------------------------
        # Login successful
        # -------------------------------------------------

        flash(
            "Login successful!",
            "success"
        )

        return redirect(url_for("dashboard"))

    return render_template("user/login.html")

# =========================================================
# LOGOUT
# =========================================================

@app.route("/logout")
def logout():

    # Remove all session data
    session.clear()

    flash(
        "You have been logged out successfully.",
        "success"
    )

    return redirect(url_for("login"))

# =========================================================
# PROFILE
# =========================================================

@app.route("/profile", methods=["GET", "POST"])
def profile():

    if not session.get("logged_in"):
        flash(
            "Please log in to continue.",
            "error"
        )
        return redirect(url_for("login"))

    user_id = session.get("user_id")

    connection = None

    # Allowed values for skill levels
    allowed_levels = [
        "Beginner",
        "Intermediate",
        "Advanced",
        "Expert"
    ]

    # Allowed skill categories
    allowed_categories = [
        "Technology",
        "Creative Skills",
        "Music",
        "Languages",
        "Academic Support",
        "Everyday Skills",
        "Sports & Fitness",
        "Business",
        "Career & Professional",
        "Other"
    ]

    try:

        connection = get_db_connection()

        with connection.cursor() as cursor:

            # =================================================
            # POST REQUEST
            # =================================================

            if request.method == "POST":

                action = request.form.get(
                    "action",
                    ""
                ).strip()

                # =================================================
                # SAVE BASIC PROFILE
                # =================================================

                if action == "save_profile":

                    bio = request.form.get(
                        "bio",
                        ""
                    ).strip()

                    location_area = request.form.get(
                        "location_area",
                        ""
                    ).strip()

                    learning_mode = request.form.get(
                        "learning_mode",
                        "Both"
                    ).strip()

                    availability = request.form.get(
                        "availability",
                        ""
                    ).strip()

                    whatsapp_number = request.form.get(
                        "whatsapp_number",
                        ""
                    ).strip()

                    # -------------------------------------------------
                    # Validate required fields
                    # -------------------------------------------------
                    if not bio:
                        flash(
                            "Please fill in 'About you' to tell others about yourself.",
                            "error"
                        )
                        return redirect(url_for("profile"))

                    if not location_area:
                        flash(
                            "Please provide your Location / Area.",
                            "error"
                        )
                        return redirect(url_for("profile"))

                    if not availability:
                        flash(
                            "Please provide your Availability details.",
                            "error"
                        )
                        return redirect(url_for("profile"))

                    if not whatsapp_number:
                        flash(
                            "Please provide your WhatsApp number with country code.",
                            "error"
                        )
                        return redirect(url_for("profile"))

                    # -------------------------------------------------
                    # Validate learning mode
                    # -------------------------------------------------

                    if learning_mode not in [
                        "Online",
                        "Offline",
                        "Both"
                    ]:

                        flash(
                            "Invalid learning mode.",
                            "error"
                        )

                        return redirect(url_for("profile"))

                    # -------------------------------------------------
                    # Validate WhatsApp number
                    # -------------------------------------------------

                    # Remove spaces, brackets, hyphens and dots
                    whatsapp_number = (
                        whatsapp_number
                        .replace(" ", "")
                        .replace("-", "")
                        .replace("(", "")
                        .replace(")", "")
                        .replace(".", "")
                    )

                    # Allow an optional + at the beginning
                    if whatsapp_number.startswith("+"):

                        whatsapp_number = (
                            "+"
                            + "".join(
                                character
                                for character in whatsapp_number[1:]
                                if character.isdigit()
                            )
                        )

                    else:

                        whatsapp_number = "".join(
                            character
                            for character in whatsapp_number
                            if character.isdigit()
                        )

                    # WhatsApp international numbers can contain
                    # a maximum of 15 digits.
                    number_without_plus = whatsapp_number.lstrip("+")

                    if (
                        not number_without_plus.isdigit()
                        or len(number_without_plus) < 7
                        or len(number_without_plus) > 15
                    ):

                        flash(
                            "Please enter a valid WhatsApp number with country code.",
                            "error"
                        )

                        return redirect(url_for("profile"))

                    # Store without + because wa.me uses
                    # the international number without +.
                    whatsapp_number = number_without_plus
                    city = location_area

                    # -------------------------------------------------
                    # Check whether profile already exists
                    # -------------------------------------------------

                    cursor.execute(
                        """
                        SELECT id
                        FROM user_profiles
                        WHERE user_id = %s
                        """,
                        (user_id,)
                    )

                    existing_profile = cursor.fetchone()

                    # -------------------------------------------------
                    # UPDATE EXISTING PROFILE
                    # -------------------------------------------------

                    if existing_profile:

                        cursor.execute(
                            """
                            UPDATE user_profiles
                            SET bio = %s,
                                location_area = %s,
                                city = %s,
                                learning_mode = %s,
                                availability = %s,
                                whatsapp_number = %s
                            WHERE user_id = %s
                            """,
                            (
                                bio,
                                location_area,
                                city,
                                learning_mode,
                                availability,
                                whatsapp_number,
                                user_id
                            )
                        )

                    # -------------------------------------------------
                    # CREATE NEW PROFILE
                    # -------------------------------------------------

                    else:

                        cursor.execute(
                            """
                            INSERT INTO user_profiles
                            (
                                user_id,
                                bio,
                                location_area,
                                city,
                                learning_mode,
                                availability,
                                whatsapp_number
                            )
                            VALUES
                            (%s, %s, %s, %s, %s, %s, %s)
                            """,
                            (
                                user_id,
                                bio,
                                location_area,
                                city,
                                learning_mode,
                                availability,
                                whatsapp_number
                            )
                        )

                    connection.commit()

                    flash(
                        "Profile saved successfully.",
                        "success"
                    )

                    return redirect(url_for("profile"))

                # =================================================
                # ADD NEW SKILL
                # =================================================

                elif action == "add_new_skill":

                    skill_name = request.form.get(
                        "new_skill_name",
                        ""
                    ).strip()

                    category = request.form.get(
                        "new_skill_category",
                        ""
                    ).strip()

                    description = request.form.get(
                        "new_skill_description",
                        ""
                    ).strip()

                    # -------------------------------
                    # Validate skill name
                    # -------------------------------

                    if not skill_name:

                        flash(
                            "Please enter a skill name.",
                            "error"
                        )

                        return redirect(url_for("profile"))

                    if len(skill_name) < 2:

                        flash(
                            "Skill name must contain at least 2 characters.",
                            "error"
                        )

                        return redirect(url_for("profile"))

                    if len(skill_name) > 100:

                        flash(
                            "Skill name cannot exceed 100 characters.",
                            "error"
                        )

                        return redirect(url_for("profile"))

                    # -------------------------------
                    # Validate category
                    # -------------------------------

                    if category not in allowed_categories:

                        flash(
                            "Please select a valid skill category.",
                            "error"
                        )

                        return redirect(url_for("profile"))

                    # -------------------------------
                    # Validate description
                    # -------------------------------

                    if not description:

                        flash(
                            "Please provide a description for the skill.",
                            "error"
                        )

                        return redirect(url_for("profile"))

                    if len(description) > 1000:

                        flash(
                            "Skill description cannot exceed 1000 characters.",
                            "error"
                        )

                        return redirect(url_for("profile"))

                    # -------------------------------
                    # Check duplicate skill
                    # -------------------------------

                    cursor.execute(
                        """
                        SELECT id
                        FROM skills
                        WHERE LOWER(skill_name) = LOWER(%s)
                        """,
                        (skill_name,)
                    )

                    existing_skill = cursor.fetchone()

                    if existing_skill:

                        flash(
                            "This skill already exists. Please select it from the skill list.",
                            "error"
                        )

                        return redirect(url_for("profile"))

                    # -------------------------------
                    # Insert new skill
                    # -------------------------------

                    try:

                        cursor.execute(
                            """
                            INSERT INTO skills
                            (
                                skill_name,
                                category,
                                description
                            )
                            VALUES
                            (%s, %s, %s)
                            """,
                            (
                                skill_name,
                                category,
                                description
                            )
                        )

                        connection.commit()

                        flash(
                            f'"{skill_name}" was added successfully. You can now select it from the skill list.',
                            "success"
                        )

                    except Exception as error:

                        connection.rollback()

                        print(
                            "Add skill error:",
                            error
                        )

                        flash(
                            "Unable to add the new skill. It may already exist.",
                            "error"
                        )

                    return redirect(url_for("profile"))

                # =================================================
                # ADD TEACHING SKILL
                # =================================================

                elif action == "add_teaching_skill":

                    # Check whether profile details are complete before teaching
                    cursor.execute(
                        """
                        SELECT bio, location_area, availability, whatsapp_number
                        FROM user_profiles
                        WHERE user_id = %s
                        LIMIT 1
                        """,
                        (user_id,)
                    )
                    user_prof = cursor.fetchone()
                    if not (
                        user_prof
                        and (user_prof.get("bio") or "").strip()
                        and (user_prof.get("location_area") or "").strip()
                        and (user_prof.get("availability") or "").strip()
                        and (user_prof.get("whatsapp_number") or "").strip()
                    ):
                        flash(
                            "To teach, you must first fill in all required profile details (About you, Location, Availability, and WhatsApp number).",
                            "error"
                        )
                        return redirect(url_for("profile"))

                    skill_id = request.form.get(
                        "teaching_skill",
                        ""
                    ).strip()

                    skill_level = request.form.get(
                        "teaching_level",
                        "Beginner"
                    ).strip()

                    if not skill_id:

                        flash(
                            "Please select a skill to teach.",
                            "error"
                        )

                        return redirect(url_for("profile"))

                    if skill_level not in allowed_levels:

                        flash(
                            "Invalid teaching level.",
                            "error"
                        )

                        return redirect(url_for("profile"))

                    # Check whether selected skill exists
                    cursor.execute(
                        """
                        SELECT
                            id,
                            skill_name
                        FROM skills
                        WHERE id = %s
                        """,
                        (skill_id,)
                    )

                    skill = cursor.fetchone()

                    if not skill:

                        flash(
                            "Selected skill does not exist.",
                            "error"
                        )

                        return redirect(url_for("profile"))

                    # Check duplicate user teaching skill
                    cursor.execute(
                        """
                        SELECT id
                        FROM user_teaching_skills
                        WHERE user_id = %s AND skill_id = %s
                        LIMIT 1
                        """,
                        (user_id, skill_id)
                    )

                    if cursor.fetchone():
                        flash(
                            "You have already added this teaching skill to your profile.",
                            "error"
                        )
                        return redirect(url_for("profile"))

                    try:

                        cursor.execute(
                            """
                            INSERT INTO user_teaching_skills
                            (
                                user_id,
                                skill_id,
                                skill_level
                            )
                            VALUES
                            (%s, %s, %s)
                            """,
                            (
                                user_id,
                                skill_id,
                                skill_level
                            )
                        )

                        connection.commit()

                        flash(
                            "Teaching skill added successfully.",
                            "success"
                        )

                    except Exception as error:

                        connection.rollback()

                        print(
                            "Teaching skill error:",
                            error
                        )

                        flash(
                            "You have already added this teaching skill.",
                            "error"
                        )

                    return redirect(url_for("profile"))

                # =================================================
                # ADD LEARNING SKILL
                # =================================================

                elif action == "add_learning_skill":

                    # Check whether profile details are complete before adding learning skill
                    cursor.execute(
                        """
                        SELECT bio, location_area, availability, whatsapp_number
                        FROM user_profiles
                        WHERE user_id = %s
                        LIMIT 1
                        """,
                        (user_id,)
                    )
                    user_prof = cursor.fetchone()
                    if not (
                        user_prof
                        and (user_prof.get("bio") or "").strip()
                        and (user_prof.get("location_area") or "").strip()
                        and (user_prof.get("availability") or "").strip()
                        and (user_prof.get("whatsapp_number") or "").strip()
                    ):
                        flash(
                            "To learn skills, you must first fill in all required profile details (About you, Location, Availability, and WhatsApp number).",
                            "error"
                        )
                        return redirect(url_for("profile"))

                    skill_id = request.form.get(
                        "learning_skill",
                        ""
                    ).strip()

                    skill_level = request.form.get(
                        "learning_level",
                        "Beginner"
                    ).strip()

                    if not skill_id:

                        flash(
                            "Please select a skill to learn.",
                            "error"
                        )

                        return redirect(url_for("profile"))

                    if skill_level not in allowed_levels:

                        flash(
                            "Invalid learning level.",
                            "error"
                        )

                        return redirect(url_for("profile"))

                    # Check whether selected skill exists
                    cursor.execute(
                        """
                        SELECT
                            id,
                            skill_name
                        FROM skills
                        WHERE id = %s
                        """,
                        (skill_id,)
                    )

                    skill = cursor.fetchone()

                    if not skill:

                        flash(
                            "Selected skill does not exist.",
                            "error"
                        )

                        return redirect(url_for("profile"))

                    # Check duplicate user learning skill
                    cursor.execute(
                        """
                        SELECT id
                        FROM user_learning_skills
                        WHERE user_id = %s AND skill_id = %s
                        LIMIT 1
                        """,
                        (user_id, skill_id)
                    )

                    if cursor.fetchone():
                        flash(
                            "You have already added this learning skill to your profile.",
                            "error"
                        )
                        return redirect(url_for("profile"))

                    try:

                        cursor.execute(
                            """
                            INSERT INTO user_learning_skills
                            (
                                user_id,
                                skill_id,
                                skill_level
                            )
                            VALUES
                            (%s, %s, %s)
                            """,
                            (
                                user_id,
                                skill_id,
                                skill_level
                            )
                        )

                        connection.commit()

                        flash(
                            "Learning skill added successfully.",
                            "success"
                        )

                    except Exception as error:

                        connection.rollback()

                        print(
                            "Learning skill error:",
                            error
                        )

                        flash(
                            "You have already added this learning skill.",
                            "error"
                        )

                    return redirect(url_for("profile"))

                # =================================================
                # DELETE TEACHING SKILL
                # =================================================

                elif action == "delete_teaching_skill":

                    skill_id = request.form.get(
                        "skill_id",
                        ""
                    ).strip()

                    if not skill_id.isdigit():
                        flash("Invalid teaching skill.", "error")
                        return redirect(url_for("profile"))

                    try:
                        cursor.execute(
                            """
                            DELETE FROM user_teaching_skills
                            WHERE id = %s AND user_id = %s
                            """,
                            (int(skill_id), user_id)
                        )
                        connection.commit()

                        if cursor.rowcount > 0:
                            flash("Teaching skill deleted successfully.", "success")
                        else:
                            flash("Skill not found or already deleted.", "error")

                    except Exception as error:
                        connection.rollback()
                        print("Delete teaching skill error:", error)
                        flash("Unable to delete teaching skill.", "error")

                    return redirect(url_for("profile"))

                # =================================================
                # DELETE LEARNING SKILL
                # =================================================

                elif action == "delete_learning_skill":

                    skill_id = request.form.get(
                        "skill_id",
                        ""
                    ).strip()

                    if not skill_id.isdigit():
                        flash("Invalid learning skill.", "error")
                        return redirect(url_for("profile"))

                    try:
                        cursor.execute(
                            """
                            DELETE FROM user_learning_skills
                            WHERE id = %s AND user_id = %s
                            """,
                            (int(skill_id), user_id)
                        )
                        connection.commit()

                        if cursor.rowcount > 0:
                            flash("Learning skill deleted successfully.", "success")
                        else:
                            flash("Skill not found or already deleted.", "error")

                    except Exception as error:
                        connection.rollback()
                        print("Delete learning skill error:", error)
                        flash("Unable to delete learning skill.", "error")

                    return redirect(url_for("profile"))

            # =================================================
            # GET DATA FOR PROFILE PAGE
            # =================================================

            # -------------------------------------------------
            # Get existing profile
            # -------------------------------------------------

            cursor.execute(
                """
                SELECT
                    id,
                    bio,
                    location_area,
                    learning_mode,
                    availability,
                    whatsapp_number,
                    profile_photo
                FROM user_profiles
                WHERE user_id = %s
                """,
                (user_id,)
            )

            profile_data = cursor.fetchone()

            # -------------------------------------------------
            # Get all available skills
            # -------------------------------------------------

            cursor.execute(
                """
                SELECT
                    id,
                    skill_name,
                    category,
                    description
                FROM skills
                ORDER BY category, skill_name
                """
            )

            skills = cursor.fetchall()

            # -------------------------------------------------
            # Get teaching skills
            # -------------------------------------------------

            cursor.execute(
                """
                SELECT
                    user_teaching_skills.id,
                    skills.skill_name,
                    skills.category,
                    skills.description,
                    user_teaching_skills.skill_level
                FROM user_teaching_skills

                INNER JOIN skills
                    ON user_teaching_skills.skill_id = skills.id

                WHERE user_teaching_skills.user_id = %s

                ORDER BY skills.skill_name
                """,
                (user_id,)
            )

            teaching_skills = cursor.fetchall()

            # -------------------------------------------------
            # Get learning skills
            # -------------------------------------------------

            cursor.execute(
                """
                SELECT
                    user_learning_skills.id,
                    skills.skill_name,
                    skills.category,
                    skills.description,
                    user_learning_skills.skill_level
                FROM user_learning_skills

                INNER JOIN skills
                    ON user_learning_skills.skill_id = skills.id

                WHERE user_learning_skills.user_id = %s

                ORDER BY skills.skill_name
                """,
                (user_id,)
            )

            learning_skills = cursor.fetchall()

    except Exception as error:

        if connection:
            connection.rollback()

        print(
            "Profile error:",
            error
        )

        flash(
            "Something went wrong. Please try again.",
            "error"
        )

        return redirect(url_for("profile"))

    finally:

        if connection:
            connection.close()

    profile_complete = bool(
        profile_data
        and (profile_data.get("bio") or "").strip()
        and (profile_data.get("location_area") or "").strip()
        and (profile_data.get("availability") or "").strip()
        and (profile_data.get("whatsapp_number") or "").strip()
    )

    return render_template(
        "user/profile.html",
        profile=profile_data,
        profile_complete=profile_complete,
        skills=skills,
        teaching_skills=teaching_skills,
        learning_skills=learning_skills,
        skill_categories=allowed_categories
    )
# =========================================================
# DASHBOARD
# =========================================================

@app.route("/dashboard")
def dashboard():

    if not session.get("logged_in"):
        flash(
            "Please log in to continue.",
            "error"
        )
        return redirect(url_for("login"))

    user_id = session.get("user_id")

    connection = None

    try:

        connection = get_db_connection()

        with connection.cursor() as cursor:

            # -------------------------------------------------
            # Get profile
            # -------------------------------------------------

            cursor.execute(
                """
                SELECT
                    bio,
                    location_area,
                    learning_mode,
                    availability
                FROM user_profiles
                WHERE user_id = %s
                """,
                (user_id,)
            )

            profile = cursor.fetchone()

            # -------------------------------------------------
            # Get teaching skills
            # -------------------------------------------------

            cursor.execute(
                """
                SELECT
                    skills.id,
                    skills.skill_name,
                    skills.category,
                    user_teaching_skills.skill_level
                FROM user_teaching_skills
                INNER JOIN skills
                    ON user_teaching_skills.skill_id = skills.id
                WHERE user_teaching_skills.user_id = %s
                ORDER BY skills.skill_name
                """,
                (user_id,)
            )

            teaching_skills = cursor.fetchall()

            # -------------------------------------------------
            # Get learning skills
            # -------------------------------------------------

            cursor.execute(
                """
                SELECT
                    skills.id,
                    skills.skill_name,
                    skills.category,
                    user_learning_skills.skill_level
                FROM user_learning_skills
                INNER JOIN skills
                    ON user_learning_skills.skill_id = skills.id
                WHERE user_learning_skills.user_id = %s
                ORDER BY skills.skill_name
                """,
                (user_id,)
            )

            learning_skills = cursor.fetchall()

            # -------------------------------------------------
            # Calculate profile completion
            # -------------------------------------------------

            profile_complete = bool(
                profile
                and (profile.get("bio") or "").strip()
                and (profile.get("location_area") or "").strip()
                and (profile.get("availability") or "").strip()
                and (profile.get("whatsapp_number") or "").strip()
            )

            completed_items = 0
            total_items = 6

            if profile:
                if (profile.get("bio") or "").strip():
                    completed_items += 1

                if (profile.get("location_area") or "").strip():
                    completed_items += 1

                if (profile.get("availability") or "").strip():
                    completed_items += 1

                if (profile.get("whatsapp_number") or "").strip():
                    completed_items += 1

            if teaching_skills:
                completed_items += 1

            if learning_skills:
                completed_items += 1

            profile_completion = int(
                (completed_items / total_items) * 100
            )

    except Exception as error:

        if connection:
            connection.rollback()

        print("Dashboard error:", error)

        flash(
            "Something went wrong while loading your dashboard.",
            "error"
        )

        return redirect(url_for("login"))

    finally:

        if connection:
            connection.close()

    return render_template(
        "user/dashboard.html",
        profile=profile,
        profile_complete=profile_complete,
        teaching_skills=teaching_skills,
        learning_skills=learning_skills,
        profile_completion=profile_completion
    )

# =========================================================
# COMMUNITY / DISCOVER
# =========================================================

# =========================================================
# COMMUNITY / DISCOVER
# =========================================================

@app.route("/community")
def community():

    if not session.get("logged_in"):
        flash(
            "Please log in to continue.",
            "error"
        )
        return redirect(url_for("login"))

    user_id = session.get("user_id")

    search = request.args.get(
        "search",
        ""
    ).strip()

    category = request.args.get(
        "category",
        ""
    ).strip()

    learning_mode = request.args.get(
        "learning_mode",
        ""
    ).strip()

    # =====================================================
    # LOCATION FILTER
    #
    # Default = My Community
    # Options = My Community / All Users
    # =====================================================

    location_scope = request.args.get(
        "location_scope",
        "community"
    ).strip()

    if location_scope not in ["community", "all"]:
        location_scope = "community"

    connection = None

    try:

        connection = get_db_connection()

        with connection.cursor() as cursor:

            # =================================================
            # GET CURRENT USER'S CITY
            # =================================================

            cursor.execute(
                """
                SELECT city
                FROM user_profiles
                WHERE user_id = %s
                LIMIT 1
                """,
                (user_id,)
            )

            current_profile = cursor.fetchone()

            current_city = None

            if current_profile:
                current_city = (
                    current_profile.get("city") or ""
                ).strip()


            # =================================================
            # GET SKILL CATEGORIES
            # =================================================

            cursor.execute(
                """
                SELECT DISTINCT category
                FROM skills
                WHERE category IS NOT NULL
                  AND category != ''
                ORDER BY category
                """
            )

            categories = cursor.fetchall()


            # =================================================
            # GET PEOPLE
            #
            # One person appears only once.
            # =================================================

            people_query = """
                SELECT DISTINCT
                    users.id AS user_id,
                    users.full_name,

                    user_profiles.bio,
                    user_profiles.location_area,
                    user_profiles.city,
                    user_profiles.learning_mode,
                    user_profiles.availability

                FROM users

                INNER JOIN user_teaching_skills
                    ON user_teaching_skills.user_id = users.id

                INNER JOIN skills
                    ON user_teaching_skills.skill_id = skills.id

                LEFT JOIN user_profiles
                    ON user_profiles.user_id = users.id

                WHERE users.email_verified = TRUE
                  AND users.id != %s
            """

            people_parameters = [user_id]


            # =================================================
            # LOCATION FILTER
            #
            # My Community:
            # Show users from the same city.
            #
            # All Users:
            # No city restriction.
            # =================================================

            if location_scope == "community":

                if current_city:

                    people_query += """
                        AND LOWER(
                            TRIM(user_profiles.city)
                        ) = LOWER(
                            TRIM(%s)
                        )
                    """

                    people_parameters.append(
                        current_city
                    )

                else:

                    # User has not added a city.
                    # No people are shown under My Community.

                    people_query += """
                        AND 1 = 0
                    """


            # =================================================
            # SEARCH FILTER
            # =================================================

            if search:

                people_query += """
                    AND (
                        LOWER(users.full_name) LIKE LOWER(%s)
                        OR LOWER(skills.skill_name) LIKE LOWER(%s)
                        OR LOWER(skills.category) LIKE LOWER(%s)
                    )
                """

                search_value = f"%{search}%"

                people_parameters.extend([
                    search_value,
                    search_value,
                    search_value
                ])


            # =================================================
            # CATEGORY FILTER
            # =================================================

            if category:

                people_query += """
                    AND skills.category = %s
                """

                people_parameters.append(category)


            # =================================================
            # LEARNING MODE FILTER
            # =================================================

            if learning_mode == "Online":

                people_query += """
                    AND (
                        user_profiles.learning_mode = 'Online'
                        OR user_profiles.learning_mode = 'Both'
                    )
                """

            elif learning_mode == "Offline":

                people_query += """
                    AND (
                        user_profiles.learning_mode = 'Offline'
                        OR user_profiles.learning_mode = 'Both'
                    )
                """

            elif learning_mode == "Both":

                people_query += """
                    AND user_profiles.learning_mode = 'Both'
                """


            people_query += """
                ORDER BY users.full_name ASC
            """


            cursor.execute(
                people_query,
                tuple(people_parameters)
            )

            people = cursor.fetchall()


            # =================================================
            # GET TEACHING SKILLS
            #
            # Only matching skills are loaded when search or
            # category filters are being used.
            # =================================================

            skills_query = """
                SELECT
                    user_teaching_skills.user_id,

                    skills.id AS skill_id,
                    skills.skill_name,
                    skills.category,
                    skills.description,

                    user_teaching_skills.skill_level

                FROM user_teaching_skills

                INNER JOIN skills
                    ON user_teaching_skills.skill_id = skills.id

                INNER JOIN users
                    ON user_teaching_skills.user_id = users.id

                LEFT JOIN user_profiles
                    ON user_profiles.user_id = users.id

                WHERE users.email_verified = TRUE
                  AND users.id != %s
            """

            skill_parameters = [user_id]


            # =================================================
            # LOCATION FILTER FOR TEACHING SKILLS
            # =================================================

            if location_scope == "community":

                if current_city:

                    skills_query += """
                        AND LOWER(
                            TRIM(user_profiles.city)
                        ) = LOWER(
                            TRIM(%s)
                        )
                    """

                    skill_parameters.append(
                        current_city
                    )

                else:

                    skills_query += """
                        AND 1 = 0
                    """


            # =================================================
            # SEARCH SKILLS
            # =================================================

            if search:

                skills_query += """
                    AND (
                        LOWER(skills.skill_name) LIKE LOWER(%s)
                        OR LOWER(skills.category) LIKE LOWER(%s)
                    )
                """

                search_value = f"%{search}%"

                skill_parameters.extend([
                    search_value,
                    search_value
                ])


            # =================================================
            # CATEGORY
            # =================================================

            if category:

                skills_query += """
                    AND skills.category = %s
                """

                skill_parameters.append(category)


            skills_query += """
                ORDER BY
                    skills.skill_name ASC
            """


            cursor.execute(
                skills_query,
                tuple(skill_parameters)
            )

            all_teaching_skills = cursor.fetchall()


            # =================================================
            # GROUP SKILLS BY USER
            # =================================================

            skills_by_user = {}

            for skill in all_teaching_skills:

                teacher_id = skill["user_id"]

                if teacher_id not in skills_by_user:

                    skills_by_user[teacher_id] = []

                skills_by_user[teacher_id].append(skill)


            # =================================================
            # ADD SKILL COUNT TO EACH PERSON
            # =================================================

            filtered_people = []

            for person in people:

                person_skills = skills_by_user.get(
                    person["user_id"],
                    []
                )

                person["teaching_skill_count"] = len(
                    person_skills
                )

                # Only keep people who have matching skills.
                if person["teaching_skill_count"] > 0:

                    filtered_people.append(person)


            people = filtered_people


            # =================================================
            # AVAILABLE SKILLS
            # =================================================

            available_skills_query = """
                SELECT
                    skills.id,
                    skills.skill_name,
                    skills.category,
                    skills.description,

                    COUNT(
                        DISTINCT user_teaching_skills.user_id
                    ) AS teacher_count

                FROM skills

                INNER JOIN user_teaching_skills
                    ON skills.id = user_teaching_skills.skill_id

                INNER JOIN users
                    ON user_teaching_skills.user_id = users.id

                LEFT JOIN user_profiles
                    ON user_profiles.user_id = users.id

                WHERE users.email_verified = TRUE
                  AND users.id != %s
            """

            available_skill_parameters = [user_id]


            # =================================================
            # LOCATION FILTER FOR AVAILABLE SKILLS
            # =================================================

            if location_scope == "community":

                if current_city:

                    available_skills_query += """
                        AND LOWER(
                            TRIM(user_profiles.city)
                        ) = LOWER(
                            TRIM(%s)
                        )
                    """

                    available_skill_parameters.append(
                        current_city
                    )

                else:

                    available_skills_query += """
                        AND 1 = 0
                    """


            # =================================================
            # SEARCH AVAILABLE SKILLS
            # =================================================

            if search:

                available_skills_query += """
                    AND (
                        LOWER(skills.skill_name) LIKE LOWER(%s)
                        OR LOWER(skills.category) LIKE LOWER(%s)
                        OR LOWER(skills.description) LIKE LOWER(%s)
                    )
                """

                search_value = f"%{search}%"

                available_skill_parameters.extend([
                    search_value,
                    search_value,
                    search_value
                ])


            # =================================================
            # CATEGORY FILTER
            # =================================================

            if category:

                available_skills_query += """
                    AND skills.category = %s
                """

                available_skill_parameters.append(category)


            available_skills_query += """
                GROUP BY
                    skills.id,
                    skills.skill_name,
                    skills.category,
                    skills.description

                ORDER BY
                    teacher_count DESC,
                    skills.skill_name ASC
            """


            cursor.execute(
                available_skills_query,
                tuple(available_skill_parameters)
            )

            available_skills = cursor.fetchall()


    except Exception as error:

        if connection:
            connection.rollback()

        print(
            "Community error:",
            repr(error)
        )

        flash(
            "Something went wrong while loading the community.",
            "error"
        )

        return redirect(
            url_for("dashboard")
        )

    finally:

        if connection:
            connection.close()


    return render_template(
        "user/community.html",

        people=people,

        available_skills=available_skills,

        categories=categories,

        search=search,

        selected_category=category,

        selected_learning_mode=learning_mode,

        selected_location_scope=location_scope,

        current_city=current_city
    )

# =========================================================
# PUBLIC USER PROFILE
# =========================================================

@app.route("/user/<int:user_id>")
def public_profile(user_id):

    if not session.get("logged_in"):
        flash(
            "Please log in to continue.",
            "error"
        )
        return redirect(url_for("login"))

    current_user_id = session.get("user_id")

    # Prevent viewing your own profile through the public profile route
    if current_user_id == user_id:
        return redirect(url_for("profile"))

    connection = None

    try:

        connection = get_db_connection()

        with connection.cursor() as cursor:

            # =================================================
            # GET USER + PROFILE
            # =================================================

            cursor.execute(
                """
                SELECT
                    users.id,
                    users.full_name,
                    user_profiles.bio,
                    user_profiles.location_area,
                    user_profiles.learning_mode,
                    user_profiles.availability
                FROM users

                LEFT JOIN user_profiles
                    ON users.id = user_profiles.user_id

                WHERE users.id = %s
                  AND users.email_verified = TRUE
                """,
                (user_id,)
            )

            user = cursor.fetchone()

            if not user:

                flash(
                    "This user profile could not be found.",
                    "error"
                )

                return redirect(url_for("community"))

            # =================================================
            # GET TEACHING SKILLS
            # =================================================

            cursor.execute(
                """
                SELECT
                    skills.id,
                    skills.skill_name,
                    skills.category,
                    skills.description,
                    user_teaching_skills.skill_level,
                    (
                        SELECT status
                        FROM learning_requests
                        WHERE learning_requests.learner_id = %s
                          AND learning_requests.teacher_id = user_teaching_skills.user_id
                          AND learning_requests.skill_id = skills.id
                          AND learning_requests.status IN ('Pending', 'Accepted')
                        LIMIT 1
                    ) AS existing_request_status
                FROM user_teaching_skills

                INNER JOIN skills
                    ON user_teaching_skills.skill_id = skills.id

                WHERE user_teaching_skills.user_id = %s

                ORDER BY skills.skill_name
                """,
                (current_user_id, user_id)
            )

            teaching_skills = cursor.fetchall()

            # =================================================
            # GET LEARNING SKILLS
            # =================================================

            cursor.execute(
                """
                SELECT
                    skills.id,
                    skills.skill_name,
                    skills.category,
                    skills.description,
                    user_learning_skills.skill_level
                FROM user_learning_skills

                INNER JOIN skills
                    ON user_learning_skills.skill_id = skills.id

                WHERE user_learning_skills.user_id = %s

                ORDER BY skills.skill_name
                """,
                (user_id,)
            )

            learning_skills = cursor.fetchall()

    except Exception as error:

        if connection:
            connection.rollback()

        print(
            "Public profile error:",
            error
        )

        flash(
            "Something went wrong while loading this profile.",
            "error"
        )

        return redirect(url_for("community"))

    finally:

        if connection:
            connection.close()

    return render_template(
        "user/public_profile.html",
        user=user,
        teaching_skills=teaching_skills,
        learning_skills=learning_skills
    )

# =========================================================
# REQUEST TO LEARN
# =========================================================

@app.route(
    "/request-to-learn/<int:teacher_id>/<int:skill_id>",
    methods=["GET", "POST"]
)
def request_to_learn(teacher_id, skill_id):

    # ---------------------------------------------------------
    # LOGIN CHECK
    # ---------------------------------------------------------

    if not session.get("logged_in"):

        flash(
            "Please log in to continue.",
            "error"
        )

        return redirect(url_for("login"))

    learner_id = session.get("user_id")

    # ---------------------------------------------------------
    # PREVENT SELF REQUEST
    # ---------------------------------------------------------

    if learner_id == teacher_id:

        flash(
            "You cannot send a learning request to yourself.",
            "error"
        )

        return redirect(url_for("profile"))

    connection = None

    try:

        connection = get_db_connection()

        with connection.cursor() as cursor:

            # Check learner profile completion
            cursor.execute(
                """
                SELECT bio, location_area, availability, whatsapp_number
                FROM user_profiles
                WHERE user_id = %s
                LIMIT 1
                """,
                (learner_id,)
            )
            learner_prof = cursor.fetchone()
            if not (
                learner_prof
                and (learner_prof.get("bio") or "").strip()
                and (learner_prof.get("location_area") or "").strip()
                and (learner_prof.get("availability") or "").strip()
                and (learner_prof.get("whatsapp_number") or "").strip()
            ):
                flash(
                    "Please complete all required profile details (About you, Location, Availability, and WhatsApp number) before sending a learning request.",
                    "error"
                )
                return redirect(url_for("profile"))

            # =================================================
            # GET TEACHER + TEACHING SKILL
            # =================================================

            cursor.execute(
                """
                SELECT
                    users.id AS teacher_id,
                    users.full_name AS teacher_name,

                    skills.id AS skill_id,
                    skills.skill_name,
                    skills.category,
                    skills.description,

                    user_teaching_skills.skill_level

                FROM user_teaching_skills

                INNER JOIN users
                    ON user_teaching_skills.user_id = users.id

                INNER JOIN skills
                    ON user_teaching_skills.skill_id = skills.id

                WHERE users.id = %s
                  AND skills.id = %s
                  AND users.email_verified = TRUE

                LIMIT 1
                """,
                (
                    teacher_id,
                    skill_id
                )
            )

            teacher_skill = cursor.fetchone()

            # -------------------------------------------------
            # TEACHER/SKILL NOT FOUND
            # -------------------------------------------------

            if not teacher_skill:

                flash(
                    "This teaching skill is no longer available.",
                    "error"
                )

                return redirect(
                    url_for(
                        "community"
                    )
                )

            # =================================================
            # CHECK EXISTING ACTIVE REQUEST (PENDING OR ACCEPTED)
            # =================================================

            cursor.execute(
                """
                SELECT
                    id,
                    status
                FROM learning_requests

                WHERE learner_id = %s
                  AND teacher_id = %s
                  AND skill_id = %s
                  AND status IN ('Pending', 'Accepted')

                LIMIT 1
                """,
                (
                    learner_id,
                    teacher_id,
                    skill_id
                )
            )

            existing_request = cursor.fetchone()

            if existing_request:

                if existing_request["status"] == "Pending":
                    flash(
                        "You already have a pending request for this skill.",
                        "error"
                    )
                else:
                    flash(
                        "Your learning request for this skill has already been accepted.",
                        "error"
                    )

                return redirect(
                    url_for(
                        "public_profile",
                        user_id=teacher_id
                    )
                )

            # =================================================
            # POST - SEND REQUEST
            # =================================================

            if request.method == "POST":

                message = request.form.get(
                    "message",
                    ""
                ).strip()

                preferred_mode = request.form.get(
                    "preferred_mode",
                    "Both"
                ).strip()

                # -------------------------------------------------
                # VALIDATE LEARNING MODE
                # -------------------------------------------------

                allowed_modes = [
                    "Online",
                    "Offline",
                    "Both"
                ]

                if preferred_mode not in allowed_modes:

                    flash(
                        "Please select a valid learning mode.",
                        "error"
                    )

                    return redirect(
                        url_for(
                            "request_to_learn",
                            teacher_id=teacher_id,
                            skill_id=skill_id
                        )
                    )

                # -------------------------------------------------
                # VALIDATE MESSAGE
                # -------------------------------------------------

                if len(message) > 1000:

                    flash(
                        "Message cannot exceed 1000 characters.",
                        "error"
                    )

                    return redirect(
                        url_for(
                            "request_to_learn",
                            teacher_id=teacher_id,
                            skill_id=skill_id
                        )
                    )

                # =================================================
                # CREATE REQUEST
                # =================================================

                cursor.execute(
                    """
                    INSERT INTO learning_requests
                    (
                        learner_id,
                        teacher_id,
                        skill_id,
                        message,
                        preferred_mode,
                        status
                    )

                    VALUES
                    (
                        %s,
                        %s,
                        %s,
                        %s,
                        %s,
                        'Pending'
                    )
                    """,
                    (
                        learner_id,
                        teacher_id,
                        skill_id,
                        message if message else None,
                        preferred_mode
                    )
                )

                connection.commit()

                flash(
                    "Learning request sent successfully.",
                    "success"
                )

                return redirect(
                    url_for(
                        "learning_requests"
                    )
                )

    except Exception as error:

        if connection:
            connection.rollback()

        print(
            "Request to learn error:",
            error
        )

        flash(
            "Something went wrong while sending the learning request.",
            "error"
        )

        return redirect(
            url_for(
                "community"
            )
        )

    finally:

        if connection:
            connection.close()

    # =========================================================
    # SHOW REQUEST FORM
    # =========================================================

    return render_template(
        "user/request_to_learn.html",
        teacher=teacher_skill
    )

# =========================================================
# LEARNING REQUESTS
# =========================================================

@app.route("/learning-requests", methods=["GET", "POST"])
def learning_requests():

    if not session.get("logged_in"):
        flash("Please log in to continue.", "error")
        return redirect(url_for("login"))

    user_id = session.get("user_id")
    connection = None

    try:
        connection = get_db_connection()

        with connection.cursor() as cursor:

            # Auto-complete past scheduled sessions
            auto_complete_past_sessions(cursor)
            connection.commit()

            # =================================================
            # HANDLE REQUEST ACTIONS
            # =================================================

            if request.method == "POST":

                action = request.form.get("action", "").strip()
                request_id = request.form.get("request_id", "").strip()

                if not request_id.isdigit():
                    flash("Invalid learning request.", "error")
                    return redirect(url_for("learning_requests"))

                request_id = int(request_id)

                # =================================================
                # GET REQUEST
                # =================================================

                cursor.execute(
                    """
                    SELECT
                        id,
                        learner_id,
                        teacher_id,
                        status
                    FROM learning_requests
                    WHERE id = %s
                    """,
                    (request_id,)
                )

                learning_request = cursor.fetchone()

                if not learning_request:
                    flash("Learning request not found.", "error")
                    return redirect(url_for("learning_requests"))

                # =================================================
                # ACCEPT
                # =================================================

                if action == "accept":

                    if learning_request["teacher_id"] != user_id:
                        flash(
                            "You are not authorized to accept this request.",
                            "error"
                        )
                        return redirect(url_for("learning_requests"))

                    if learning_request["status"] != "Pending":
                        flash(
                            "This request has already been processed.",
                            "error"
                        )
                        return redirect(url_for("learning_requests"))

                    cursor.execute(
                        """
                        UPDATE learning_requests
                        SET status = 'Accepted',
                            responded_at = CURRENT_TIMESTAMP
                        WHERE id = %s
                          AND teacher_id = %s
                          AND status = 'Pending'
                        """,
                        (request_id, user_id)
                    )

                    connection.commit()

                    flash(
                        "Learning request accepted.",
                        "success"
                    )

                    return redirect(url_for("learning_requests"))

                # =================================================
                # REJECT
                # =================================================

                elif action == "reject":

                    if learning_request["teacher_id"] != user_id:
                        flash(
                            "You are not authorized to reject this request.",
                            "error"
                        )
                        return redirect(url_for("learning_requests"))

                    if learning_request["status"] != "Pending":
                        flash(
                            "This request has already been processed.",
                            "error"
                        )
                        return redirect(url_for("learning_requests"))

                    cursor.execute(
                        """
                        UPDATE learning_requests
                        SET status = 'Rejected',
                            responded_at = CURRENT_TIMESTAMP
                        WHERE id = %s
                          AND teacher_id = %s
                          AND status = 'Pending'
                        """,
                        (request_id, user_id)
                    )

                    connection.commit()

                    flash(
                        "Learning request rejected.",
                        "success"
                    )

                    return redirect(url_for("learning_requests"))

                # =================================================
                # CANCEL
                # =================================================

                elif action == "cancel":

                    if learning_request["learner_id"] != user_id:
                        flash(
                            "You are not authorized to cancel this request.",
                            "error"
                        )
                        return redirect(url_for("learning_requests"))

                    if learning_request["status"] != "Pending":
                        flash(
                            "Only pending requests can be cancelled.",
                            "error"
                        )
                        return redirect(url_for("learning_requests"))

                    cursor.execute(
                        """
                        UPDATE learning_requests
                        SET status = 'Cancelled',
                            responded_at = CURRENT_TIMESTAMP
                        WHERE id = %s
                          AND learner_id = %s
                          AND status = 'Pending'
                        """,
                        (request_id, user_id)
                    )

                    connection.commit()

                    flash(
                        "Learning request cancelled.",
                        "success"
                    )

                    return redirect(url_for("learning_requests"))

                else:
                    flash("Invalid request action.", "error")
                    return redirect(url_for("learning_requests"))

            # =================================================
            # INCOMING REQUESTS
            # =================================================

            cursor.execute(
                """
                SELECT
                    learning_requests.id,
                    learning_requests.message,
                    learning_requests.preferred_mode,
                    learning_requests.status,
                    learning_requests.created_at,
                    learning_requests.responded_at,

                    users.id AS learner_id,
                    users.full_name AS learner_name,

                    skills.id AS skill_id,
                    skills.skill_name,
                    skills.category,

                    CASE
                        WHEN user_profiles.whatsapp_number IS NOT NULL
                         AND user_profiles.whatsapp_number != ''
                        THEN 1
                        ELSE 0
                    END AS whatsapp_available,

                    (
                        SELECT id
                        FROM learning_sessions
                        WHERE learning_sessions.request_id = learning_requests.id
                          AND learning_sessions.status = 'Scheduled'
                        LIMIT 1
                    ) AS scheduled_session_id

                FROM learning_requests

                INNER JOIN users
                    ON learning_requests.learner_id = users.id

                INNER JOIN skills
                    ON learning_requests.skill_id = skills.id

                LEFT JOIN user_profiles
                    ON learning_requests.learner_id = user_profiles.user_id

                WHERE learning_requests.teacher_id = %s

                ORDER BY learning_requests.created_at DESC
                """,
                (user_id,)
            )

            incoming_requests = cursor.fetchall()

            # =================================================
            # SENT REQUESTS
            # =================================================

            cursor.execute(
                """
                SELECT
                    learning_requests.id,
                    learning_requests.message,
                    learning_requests.preferred_mode,
                    learning_requests.status,
                    learning_requests.created_at,
                    learning_requests.responded_at,

                    users.id AS teacher_id,
                    users.full_name AS teacher_name,

                    skills.id AS skill_id,
                    skills.skill_name,
                    skills.category,

                    CASE
                        WHEN user_profiles.whatsapp_number IS NOT NULL
                         AND user_profiles.whatsapp_number != ''
                        THEN 1
                        ELSE 0
                    END AS whatsapp_available,

                    (
                        SELECT id
                        FROM learning_sessions
                        WHERE learning_sessions.request_id = learning_requests.id
                          AND learning_sessions.status = 'Scheduled'
                        LIMIT 1
                    ) AS scheduled_session_id

                FROM learning_requests

                INNER JOIN users
                    ON learning_requests.teacher_id = users.id

                INNER JOIN skills
                    ON learning_requests.skill_id = skills.id

                LEFT JOIN user_profiles
                    ON learning_requests.teacher_id = user_profiles.user_id

                WHERE learning_requests.learner_id = %s

                ORDER BY learning_requests.created_at DESC
                """,
                (user_id,)
            )

            sent_requests = cursor.fetchall()

            # =================================================
            # SEPARATE REQUESTS BY STATUS
            # =================================================

            pending_incoming = [
                item for item in incoming_requests
                if item["status"] == "Pending"
            ]

            pending_sent = [
                item for item in sent_requests
                if item["status"] == "Pending"
            ]

            accepted_incoming = [
                item for item in incoming_requests
                if item["status"] == "Accepted"
            ]

            accepted_sent = [
                item for item in sent_requests
                if item["status"] == "Accepted"
            ]

            rejected_incoming = [
                item for item in incoming_requests
                if item["status"] == "Rejected"
            ]

            rejected_sent = [
                item for item in sent_requests
                if item["status"] == "Rejected"
            ]

            cancelled_sent = [
                item for item in sent_requests
                if item["status"] == "Cancelled"
            ]

            # =================================================
            # COUNTS
            # =================================================

            incoming_pending_count = len(pending_incoming)
            sent_pending_count = len(pending_sent)

            accepted_count = (
                len(accepted_incoming) +
                len(accepted_sent)
            )

            rejected_count = (
                len(rejected_incoming) +
                len(rejected_sent)
            )

            cancelled_count = len(cancelled_sent)

    except Exception as error:

        if connection:
            connection.rollback()

        print("Learning requests error:", error)

        flash(
            "Something went wrong while loading learning requests.",
            "error"
        )

        return redirect(url_for("dashboard"))

    finally:

        if connection:
            connection.close()

    return render_template(
        "user/learning_requests.html",
        pending_incoming=pending_incoming,
        pending_sent=pending_sent,
        accepted_incoming=accepted_incoming,
        accepted_sent=accepted_sent,
        rejected_incoming=rejected_incoming,
        rejected_sent=rejected_sent,
        cancelled_sent=cancelled_sent,
        incoming_pending_count=incoming_pending_count,
        sent_pending_count=sent_pending_count,
        accepted_count=accepted_count,
        rejected_count=rejected_count,
        cancelled_count=cancelled_count
    )


# =========================================================
# WHATSAPP COMMUNICATION
# =========================================================

@app.route(
    "/learning-request/<int:request_id>/whatsapp"
)
def learning_request_whatsapp(request_id):

    # ---------------------------------------------------------
    # LOGIN CHECK
    # ---------------------------------------------------------

    if not session.get("logged_in"):

        flash(
            "Please log in to continue.",
            "error"
        )

        return redirect(url_for("login"))

    user_id = session.get("user_id")

    connection = None

    try:

        connection = get_db_connection()

        with connection.cursor() as cursor:

            # =================================================
            # GET ACCEPTED REQUEST + OTHER USER'S WHATSAPP
            # =================================================

            cursor.execute(
                """
                SELECT
                    learning_requests.id,
                    learning_requests.learner_id,
                    learning_requests.teacher_id,
                    learning_requests.status,
                    skills.skill_name,

                    CASE
                        WHEN learning_requests.learner_id = %s
                        THEN teacher.full_name
                        ELSE learner.full_name
                    END AS other_user_name,

                    CASE
                        WHEN learning_requests.learner_id = %s
                        THEN teacher_profile.whatsapp_number
                        ELSE learner_profile.whatsapp_number
                    END AS whatsapp_number

                FROM learning_requests

                INNER JOIN skills
                    ON learning_requests.skill_id = skills.id

                INNER JOIN users AS learner
                    ON learning_requests.learner_id = learner.id

                INNER JOIN users AS teacher
                    ON learning_requests.teacher_id = teacher.id

                LEFT JOIN user_profiles AS learner_profile
                    ON learning_requests.learner_id = learner_profile.user_id

                LEFT JOIN user_profiles AS teacher_profile
                    ON learning_requests.teacher_id = teacher_profile.user_id

                WHERE learning_requests.id = %s
                  AND (
                        learning_requests.learner_id = %s
                        OR learning_requests.teacher_id = %s
                  )

                LIMIT 1
                """,
                (
                    user_id,
                    user_id,
                    request_id,
                    user_id,
                    user_id
                )
            )

            learning_request = cursor.fetchone()

            # -------------------------------------------------
            # REQUEST NOT FOUND / NOT AUTHORIZED
            # -------------------------------------------------

            if not learning_request:

                flash(
                    "Learning relationship not found.",
                    "error"
                )

                return redirect(
                    url_for("learning_requests")
                )

            # -------------------------------------------------
            # ONLY ACCEPTED REQUESTS CAN USE WHATSAPP
            # -------------------------------------------------

            if learning_request["status"] != "Accepted":

                flash(
                    "WhatsApp communication is available only after the request is accepted.",
                    "error"
                )

                return redirect(
                    url_for("learning_requests")
                )

            # -------------------------------------------------
            # CHECK WHATSAPP NUMBER
            # -------------------------------------------------

            whatsapp_number = learning_request[
                "whatsapp_number"
            ]

            if not whatsapp_number:

                flash(
                    f'{learning_request["other_user_name"]} has not added a WhatsApp number.',
                    "error"
                )

                return redirect(
                    url_for("learning_requests")
                )

            # -------------------------------------------------
            # CREATE SAFE WHATSAPP MESSAGE
            # -------------------------------------------------

            message = (
            "Hi "
            + learning_request["other_user_name"]
            + ", I’m connecting with you through SkillCircle. "
            + "I’m interested in learning "
            + learning_request["skill_name"]
            + " from you. "
            + "Please let me know when you’re available to start. "
            + "Thank you!"
        )

            encoded_message = quote(
                message
            )

            # -------------------------------------------------
            # REDIRECT TO WHATSAPP
            # -------------------------------------------------

            whatsapp_url = (
                "https://wa.me/"
                + whatsapp_number
                + "?text="
                + encoded_message
            )

            return redirect(
                whatsapp_url
            )

    except Exception as error:

        if connection:
            connection.rollback()

        print(
            "WhatsApp communication error:",
            error
        )

        flash(
            "Unable to open WhatsApp right now.",
            "error"
        )

        return redirect(
            url_for("learning_requests")
        )

    finally:

        if connection:
            connection.close()

# =========================================================
# SCHEDULE LEARNING SESSION
# =========================================================
@app.route(
    "/schedule-session/<int:request_id>",
    methods=["GET", "POST"]
)
def schedule_session(request_id):

    if not session.get("logged_in"):
        flash("Please log in to continue.", "error")
        return redirect(url_for("login"))

    user_id = session.get("user_id")

    connection = None

    try:

        connection = get_db_connection()

        with connection.cursor() as cursor:

            # Auto-complete past scheduled sessions
            auto_complete_past_sessions(cursor)
            connection.commit()

            # -------------------------------------------------
            # GET LEARNING REQUEST
            # -------------------------------------------------

            cursor.execute(
                """
                SELECT
                    learning_requests.id,
                    learning_requests.learner_id,
                    learning_requests.teacher_id,
                    learning_requests.skill_id,
                    learning_requests.preferred_mode,
                    learning_requests.status,

                    learner.full_name AS learner_name,
                    teacher.full_name AS teacher_name,

                    skills.skill_name

                FROM learning_requests

                INNER JOIN users AS learner
                    ON learning_requests.learner_id = learner.id

                INNER JOIN users AS teacher
                    ON learning_requests.teacher_id = teacher.id

                INNER JOIN skills
                    ON learning_requests.skill_id = skills.id

                WHERE learning_requests.id = %s

                LIMIT 1
                """,
                (request_id,)
            )

            learning_request = cursor.fetchone()

            if not learning_request:
                flash(
                    "Learning request not found.",
                    "error"
                )
                return redirect(
                    url_for("learning_requests")
                )

            # -------------------------------------------------
            # CHECK USER PARTICIPATION
            # -------------------------------------------------

            if (
                learning_request["learner_id"] != user_id
                and
                learning_request["teacher_id"] != user_id
            ):
                flash(
                    "You are not part of this learning relationship.",
                    "error"
                )

                return redirect(
                    url_for("learning_requests")
                )

            # -------------------------------------------------
            # ONLY ACCEPTED REQUEST CAN BE SCHEDULED
            # -------------------------------------------------

            if learning_request["status"] != "Accepted":
                flash(
                    "A session can only be scheduled after the learning request is accepted.",
                    "error"
                )

                return redirect(
                    url_for("learning_requests")
                )

            # -------------------------------------------------
            # CHECK EXISTING SCHEDULED SESSION
            # -------------------------------------------------

            cursor.execute(
                """
                SELECT id
                FROM learning_sessions

                WHERE request_id = %s
                  AND status = 'Scheduled'

                LIMIT 1
                """,
                (request_id,)
            )

            existing_session = cursor.fetchone()

            if existing_session:
                flash(
                    "A session has already been scheduled for this learning request.",
                    "error"
                )

                return redirect(
                    url_for("learning_sessions")
                )

            # -------------------------------------------------
            # COMMON TEMPLATE DATA
            # -------------------------------------------------

            other_user_name = (
                learning_request["teacher_name"]
                if learning_request["learner_id"] == user_id
                else learning_request["learner_name"]
            )

            template_data = {
                "other_user_name": other_user_name,
                "skill_name": learning_request["skill_name"],
                "preferred_mode": learning_request["preferred_mode"],
                "today": datetime.now().date().isoformat()
            }

            # -------------------------------------------------
            # POST
            # -------------------------------------------------

            if request.method == "POST":

                session_date_value = request.form.get(
                    "session_date",
                    ""
                ).strip()

                start_time_value = request.form.get(
                    "start_time",
                    ""
                ).strip()

                end_time_value = request.form.get(
                    "end_time",
                    ""
                ).strip()

                learning_mode = request.form.get(
                    "learning_mode",
                    ""
                ).strip()

                meeting_details = request.form.get(
                    "meeting_details",
                    ""
                ).strip()

                # ---------------------------------------------
                # DATE VALIDATION
                # ---------------------------------------------

                if not session_date_value:
                    flash(
                        "Please select a session date.",
                        "error"
                    )

                    return render_template(
                        "user/schedule_session.html",
                        **template_data
                    )

                try:

                    session_date = datetime.strptime(
                        session_date_value,
                        "%Y-%m-%d"
                    ).date()

                except ValueError:

                    flash(
                        "Invalid session date.",
                        "error"
                    )

                    return render_template(
                        "user/schedule_session.html",
                        **template_data
                    )

                today = datetime.now().date()

                if session_date < today:
                    flash(
                        "Session date cannot be in the past.",
                        "error"
                    )

                    return render_template(
                        "user/schedule_session.html",
                        **template_data
                    )

                # ---------------------------------------------
                # TIME VALIDATION
                # ---------------------------------------------

                if not start_time_value or not end_time_value:
                    flash(
                        "Please select both start and end time.",
                        "error"
                    )

                    return render_template(
                        "user/schedule_session.html",
                        **template_data
                    )

                try:

                    start_time = datetime.strptime(
                        start_time_value,
                        "%H:%M"
                    ).time()

                    end_time = datetime.strptime(
                        end_time_value,
                        "%H:%M"
                    ).time()

                except ValueError:

                    flash(
                        "Invalid session time.",
                        "error"
                    )

                    return render_template(
                        "user/schedule_session.html",
                        **template_data
                    )

                if start_time >= end_time:
                    flash(
                        "End time must be later than start time.",
                        "error"
                    )

                    return render_template(
                        "user/schedule_session.html",
                        **template_data
                    )

                # ---------------------------------------------
                # PREVENT PAST TIME FOR TODAY
                # ---------------------------------------------

                if session_date == today:

                    current_time = datetime.now().time()

                    if start_time <= current_time:
                        flash(
                            "Session start time must be in the future.",
                            "error"
                        )

                        return render_template(
                            "user/schedule_session.html",
                            **template_data
                        )

                # ---------------------------------------------
                # LEARNING MODE VALIDATION
                # ---------------------------------------------

                if learning_mode not in [
                    "Online",
                    "Offline"
                ]:
                    flash(
                        "Please select a valid learning mode.",
                        "error"
                    )

                    return render_template(
                        "user/schedule_session.html",
                        **template_data
                    )

                preferred_mode = learning_request[
                    "preferred_mode"
                ]

                if (
                    preferred_mode != "Both"
                    and
                    learning_mode != preferred_mode
                ):
                    flash(
                        "The selected session mode does not match the preferred mode of the learning request.",
                        "error"
                    )

                    return render_template(
                        "user/schedule_session.html",
                        **template_data
                    )

                # ---------------------------------------------
                # MEETING DETAILS VALIDATION
                # ---------------------------------------------

                if len(meeting_details) > 500:
                    flash(
                        "Meeting details cannot exceed 500 characters.",
                        "error"
                    )

                    return render_template(
                        "user/schedule_session.html",
                        **template_data
                    )

                # ---------------------------------------------
                # CHECK OVERLAPPING SESSIONS
                # ---------------------------------------------

                cursor.execute(
                    """
                    SELECT id

                    FROM learning_sessions

                    WHERE session_date = %s
                      AND status = 'Scheduled'

                      AND (
                            teacher_id = %s
                            OR teacher_id = %s
                            OR learner_id = %s
                            OR learner_id = %s
                          )

                      AND start_time < %s
                      AND end_time > %s

                    LIMIT 1
                    """,
                    (
                        session_date,

                        learning_request["teacher_id"],
                        learning_request["learner_id"],

                        learning_request["teacher_id"],
                        learning_request["learner_id"],

                        end_time,
                        start_time
                    )
                )

                overlapping_session = cursor.fetchone()

                if overlapping_session:

                    flash(
                        "This time overlaps with another scheduled learning session.",
                        "error"
                    )

                    return render_template(
                        "user/schedule_session.html",
                        **template_data
                    )

                # ---------------------------------------------
                # INSERT SESSION
                # ---------------------------------------------

                cursor.execute(
                    """
                    INSERT INTO learning_sessions (
                        request_id,
                        teacher_id,
                        learner_id,
                        skill_id,
                        session_date,
                        start_time,
                        end_time,
                        learning_mode,
                        meeting_details,
                        status
                    )

                    VALUES (
                        %s,
                        %s,
                        %s,
                        %s,
                        %s,
                        %s,
                        %s,
                        %s,
                        %s,
                        'Scheduled'
                    )
                    """,
                    (
                        request_id,
                        learning_request["teacher_id"],
                        learning_request["learner_id"],
                        learning_request["skill_id"],
                        session_date,
                        start_time,
                        end_time,
                        learning_mode,
                        meeting_details
                        if meeting_details
                        else None
                    )
                )

                connection.commit()

                flash(
                    "Learning session scheduled successfully.",
                    "success"
                )

                return redirect(
                    url_for("learning_sessions")
                )

            # -------------------------------------------------
            # GET
            # -------------------------------------------------

            return render_template(
                "user/schedule_session.html",
                **template_data
            )

    except Exception as error:

        if connection:
            connection.rollback()

        print(
            "Schedule session error:",
            repr(error)
        )

        flash(
            "Unable to schedule the session right now.",
            "error"
        )

        return redirect(
            url_for("learning_requests")
        )

    finally:

        if connection:
            connection.close()

# =========================================================
# MY LEARNING SESSIONS
# =========================================================

@app.route(
    "/learning-sessions",
    methods=["GET", "POST"]
)
def learning_sessions():

    if not session.get("logged_in"):
        flash(
            "Please log in to continue.",
            "error"
        )

        return redirect(
            url_for("login")
        )

    user_id = session.get("user_id")

    connection = None

    try:

        connection = get_db_connection()

        with connection.cursor() as auto_cursor:
            auto_complete_past_sessions(auto_cursor)
            connection.commit()

        # =====================================================
        # POST - COMPLETE / CANCEL SESSION
        # =====================================================

        if request.method == "POST":

            session_id = request.form.get(
                "session_id",
                ""
            ).strip()

            action = request.form.get(
                "action",
                ""
            ).strip()

            # ---------------------------------------------
            # VALIDATE SESSION ID
            # ---------------------------------------------

            if not session_id.isdigit():

                flash(
                    "Invalid session.",
                    "error"
                )

                return redirect(
                    url_for("learning_sessions")
                )

            session_id = int(session_id)

            # ---------------------------------------------
            # VALIDATE ACTION
            # ---------------------------------------------

            if action not in [
                "complete",
                "cancel"
            ]:

                flash(
                    "Invalid session action.",
                    "error"
                )

                return redirect(
                    url_for("learning_sessions")
                )

            with connection.cursor() as cursor:

                # -----------------------------------------
                # GET SELECTED SESSION
                # -----------------------------------------

                cursor.execute(
                    """
                    SELECT
                        id,
                        teacher_id,
                        learner_id,
                        session_date,
                        start_time,
                        end_time,
                        status

                    FROM learning_sessions

                    WHERE id = %s

                      AND (
                            teacher_id = %s
                            OR learner_id = %s
                          )

                    LIMIT 1
                    """,
                    (
                        session_id,
                        user_id,
                        user_id
                    )
                )

                selected_session = cursor.fetchone()

                if not selected_session:

                    flash(
                        "Session not found or you do not have permission to manage it.",
                        "error"
                    )

                    return redirect(
                        url_for("learning_sessions")
                    )

                # -----------------------------------------
                # ONLY SCHEDULED SESSION CAN BE CHANGED
                # -----------------------------------------

                if selected_session["status"] != "Scheduled":

                    flash(
                        "This session has already been "
                        + selected_session["status"].lower()
                        + " and cannot be changed.",
                        "error"
                    )

                    return redirect(
                        url_for("learning_sessions")
                    )

                # =========================================
                # CANCEL SESSION
                # =========================================

                if action == "cancel":

                    cursor.execute(
                        """
                        UPDATE learning_sessions

                        SET status = 'Cancelled'

                        WHERE id = %s

                          AND status = 'Scheduled'

                          AND (
                                teacher_id = %s
                                OR learner_id = %s
                              )
                        """,
                        (
                            session_id,
                            user_id,
                            user_id
                        )
                    )

                    connection.commit()

                    if cursor.rowcount == 1:

                        flash(
                            "Session cancelled successfully.",
                            "success"
                        )

                    else:

                        flash(
                            "Unable to cancel the session.",
                            "error"
                        )

                    return redirect(
                        url_for("learning_sessions")
                    )

                # =========================================
                # COMPLETE SESSION
                # =========================================

                if action == "complete":

                    # -------------------------------------
                    # FIX MySQL TIME VALUE
                    # -------------------------------------

                    session_end_time = normalize_mysql_time(
                        selected_session["end_time"]
                    )

                    session_end_datetime = datetime.combine(
                        selected_session["session_date"],
                        session_end_time
                    )

                    current_datetime = datetime.now()

                    # -------------------------------------
                    # SESSION MUST BE FINISHED
                    # -------------------------------------

                    if current_datetime < session_end_datetime:

                        flash(
                            "A session can be marked as completed only after its scheduled end time.",
                            "error"
                        )

                        return redirect(
                            url_for("learning_sessions")
                        )

                    # -------------------------------------
                    # UPDATE STATUS
                    # -------------------------------------

                    cursor.execute(
                        """
                        UPDATE learning_sessions

                        SET status = 'Completed'

                        WHERE id = %s

                          AND status = 'Scheduled'

                          AND (
                                teacher_id = %s
                                OR learner_id = %s
                              )
                        """,
                        (
                            session_id,
                            user_id,
                            user_id
                        )
                    )

                    connection.commit()

                    if cursor.rowcount == 1:

                        flash(
                            "Session marked as completed.",
                            "success"
                        )

                    else:

                        flash(
                            "Unable to complete the session.",
                            "error"
                        )

                    return redirect(
                        url_for("learning_sessions")
                    )

        # =====================================================
        # GET - LOAD SESSIONS
        # =====================================================

        with connection.cursor() as cursor:

            cursor.execute(
                """
                SELECT
                    learning_sessions.id,
                    learning_sessions.request_id,
                    learning_sessions.teacher_id,
                    learning_sessions.learner_id,
                    learning_sessions.session_date,
                    learning_sessions.start_time,
                    learning_sessions.end_time,
                    learning_sessions.learning_mode,
                    learning_sessions.meeting_details,
                    learning_sessions.status,
                    learning_sessions.created_at,

                    skills.skill_name,

                    teacher.full_name AS teacher_name,
                    learner.full_name AS learner_name

                FROM learning_sessions

                INNER JOIN skills
                    ON learning_sessions.skill_id = skills.id

                INNER JOIN users AS teacher
                    ON learning_sessions.teacher_id = teacher.id

                INNER JOIN users AS learner
                    ON learning_sessions.learner_id = learner.id

                WHERE
                    learning_sessions.teacher_id = %s
                    OR
                    learning_sessions.learner_id = %s

                ORDER BY

                    CASE
                        WHEN learning_sessions.status = 'Scheduled'
                            THEN 1

                        WHEN learning_sessions.status = 'Completed'
                            THEN 2

                        ELSE 3
                    END,

                    learning_sessions.session_date ASC,
                    learning_sessions.start_time ASC
                """,
                (
                    user_id,
                    user_id
                )
            )

            session_rows = cursor.fetchall()

        # =====================================================
        # PREPARE SESSION DATA
        # =====================================================

        for item in session_rows:

            # ---------------------------------------------
            # NORMALIZE MYSQL TIME VALUES
            # ---------------------------------------------

            item["start_time"] = normalize_mysql_time(
                item["start_time"]
            )

            item["end_time"] = normalize_mysql_time(
                item["end_time"]
            )

            # ---------------------------------------------
            # DETERMINE USER ROLE
            # ---------------------------------------------

            if item["teacher_id"] == user_id:

                item["user_role"] = "Teaching"

                item["other_user_name"] = (
                    item["learner_name"]
                )

            else:

                item["user_role"] = "Learning"

                item["other_user_name"] = (
                    item["teacher_name"]
                )

        # =====================================================
        # SEPARATE UPCOMING / PREVIOUS
        # =====================================================

        current_datetime = datetime.now()

        upcoming_sessions = []
        previous_sessions = []

        for item in session_rows:

            session_end = datetime.combine(
                item["session_date"],
                item["end_time"]
            )

            if (
                item["status"] == "Scheduled"
                and
                session_end >= current_datetime
            ):

                upcoming_sessions.append(item)

            else:

                previous_sessions.append(item)

        # =====================================================
        # RENDER PAGE
        # =====================================================

        return render_template(
            "user/learning_sessions.html",
            sessions=session_rows,
            upcoming_sessions=upcoming_sessions,
            previous_sessions=previous_sessions
        )

    except Exception as error:

        if connection:
            connection.rollback()

        print(
            "Learning sessions error:",
            repr(error)
        )

        flash(
            "Unable to process learning sessions right now.",
            "error"
        )

        return redirect(
            url_for("dashboard")
        )

    finally:

        if connection:
            connection.close()


# =========================================================
# RUN APPLICATION
# =========================================================

if __name__ == "__main__":
    app.run(debug=True)