import os
import sqlite3
import uuid
from functools import wraps
from io import BytesIO

from flask import (
    Flask,
    render_template,
    request,
    redirect,
    url_for,
    flash,
    session,
    send_from_directory,
)
from PIL import Image
from rembg import remove
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename

app = Flask(__name__)
app.secret_key = "super_secret_key_change_this"

# =========================
# CONFIG
# =========================
UPLOAD_FOLDER = "static/uploads"
OUTPUT_FOLDER = "static/outputs"
TEMPLATE_BG_FOLDER = "static/templates_bg"
DB_NAME = "users.db"

ALLOWED_EXTENSIONS = {"png", "jpg", "jpeg", "webp"}

app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER
app.config["OUTPUT_FOLDER"] = OUTPUT_FOLDER
app.config["TEMPLATE_BG_FOLDER"] = TEMPLATE_BG_FOLDER

# Ensure folders exist
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(OUTPUT_FOLDER, exist_ok=True)
os.makedirs(TEMPLATE_BG_FOLDER, exist_ok=True)


# =========================
# DATABASE
# =========================
def init_db():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL UNIQUE,
            email TEXT NOT NULL UNIQUE,
            password TEXT NOT NULL
        )
    """)

    conn.commit()
    conn.close()


init_db()


# =========================
# HELPERS
# =========================
def allowed_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


def safe_web_path(path):
    return "/" + path.replace("\\", "/")


def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if "user_id" not in session:
            flash("Please login first.", "warning")
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return decorated_function


def get_user_folder(base_folder, username):
    user_folder = os.path.join(base_folder, secure_filename(username))
    os.makedirs(user_folder, exist_ok=True)
    return user_folder


def resize_background_to_fit(bg_image, fg_size):
    """
    Resize background to exactly fit foreground size.
    """
    return bg_image.resize(fg_size, Image.LANCZOS)


def remove_background_from_image(input_path):
    with open(input_path, "rb") as f:
        input_data = f.read()

    output_data = remove(input_data)
    output_image = Image.open(BytesIO(output_data)).convert("RGBA")
    return output_image


def apply_solid_color_background(fg_rgba, hex_color):
    bg = Image.new("RGBA", fg_rgba.size, hex_color)
    composed = Image.alpha_composite(bg, fg_rgba)
    return composed.convert("RGB")


def apply_template_background(fg_rgba, template_path):
    bg = Image.open(template_path).convert("RGBA")
    bg = resize_background_to_fit(bg, fg_rgba.size)
    composed = Image.alpha_composite(bg, fg_rgba)
    return composed.convert("RGB")


def apply_custom_background(fg_rgba, custom_bg_path):
    bg = Image.open(custom_bg_path).convert("RGBA")
    bg = resize_background_to_fit(bg, fg_rgba.size)
    composed = Image.alpha_composite(bg, fg_rgba)
    return composed.convert("RGB")


# =========================
# AUTH ROUTES
# =========================
@app.route("/signup", methods=["GET", "POST"])
def signup():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        email = request.form.get("email", "").strip()
        password = request.form.get("password", "").strip()

        if not username or not email or not password:
            flash("All fields are required.", "danger")
            return redirect(url_for("signup"))

        hashed_password = generate_password_hash(password)

        try:
            conn = sqlite3.connect(DB_NAME)
            cursor = conn.cursor()
            cursor.execute(
                "INSERT INTO users (username, email, password) VALUES (?, ?, ?)",
                (username, email, hashed_password),
            )
            conn.commit()
            conn.close()

            flash("Signup successful! Please login.", "success")
            return redirect(url_for("login"))

        except sqlite3.IntegrityError:
            flash("Username or email already exists.", "danger")
            return redirect(url_for("signup"))

    return render_template("signup.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form.get("email", "").strip()
        password = request.form.get("password", "").strip()

        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        cursor.execute("SELECT id, username, email, password FROM users WHERE email = ?", (email,))
        user = cursor.fetchone()
        conn.close()

        if user and check_password_hash(user[3], password):
            session["user_id"] = user[0]
            session["username"] = user[1]
            session["email"] = user[2]
            flash("Login successful!", "success")
            return redirect(url_for("index"))
        else:
            flash("Invalid email or password.", "danger")
            return redirect(url_for("login"))

    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    flash("Logged out successfully.", "info")
    return redirect(url_for("login"))


# =========================
# MAIN APP ROUTES
# =========================
@app.route("/", methods=["GET", "POST"])
@login_required
def index():
    result = None

    if request.method == "POST":
        main_image = request.files.get("main_image")
        bg_mode = request.form.get("bg_mode", "transparent")
        bg_color = request.form.get("bg_color", "#ffffff")
        template_name = request.form.get("template_name", "")
        custom_bg = request.files.get("custom_bg")

        if not main_image or main_image.filename == "":
            flash("Please upload a main image.", "danger")
            return redirect(url_for("index"))

        if not allowed_file(main_image.filename):
            flash("Invalid main image format. Use PNG/JPG/JPEG/WEBP.", "danger")
            return redirect(url_for("index"))

        username = session.get("username", "guest")
        user_upload_folder = get_user_folder(app.config["UPLOAD_FOLDER"], username)
        user_output_folder = get_user_folder(app.config["OUTPUT_FOLDER"], username)

        unique_id = str(uuid.uuid4())[:8]

        # Save main image
        main_filename = f"input_{unique_id}_{secure_filename(main_image.filename)}"
        main_input_path = os.path.join(user_upload_folder, main_filename)
        main_image.save(main_input_path)

        try:
            # Remove background
            fg_rgba = remove_background_from_image(main_input_path)

            output_filename = ""
            output_path = ""

            if bg_mode == "transparent":
                output_filename = f"output_transparent_{unique_id}.png"
                output_path = os.path.join(user_output_folder, output_filename)
                fg_rgba.save(output_path, "PNG")

            elif bg_mode == "solid":
                final_img = apply_solid_color_background(fg_rgba, bg_color)
                output_filename = f"output_solid_{unique_id}.jpg"
                output_path = os.path.join(user_output_folder, output_filename)
                final_img.save(output_path, "JPEG", quality=95)

            elif bg_mode == "template":
                if not template_name:
                    flash("Please select a template background.", "danger")
                    return redirect(url_for("index"))

                template_path = os.path.join(app.config["TEMPLATE_BG_FOLDER"], template_name)

                if not os.path.exists(template_path):
                    flash("Selected template background not found.", "danger")
                    return redirect(url_for("index"))

                final_img = apply_template_background(fg_rgba, template_path)
                output_filename = f"output_template_{unique_id}.jpg"
                output_path = os.path.join(user_output_folder, output_filename)
                final_img.save(output_path, "JPEG", quality=95)

            elif bg_mode == "custom":
                if not custom_bg or custom_bg.filename == "":
                    flash("Please upload a custom background image.", "danger")
                    return redirect(url_for("index"))

                if not allowed_file(custom_bg.filename):
                    flash("Invalid custom background format.", "danger")
                    return redirect(url_for("index"))

                custom_bg_filename = f"custombg_{unique_id}_{secure_filename(custom_bg.filename)}"
                custom_bg_path = os.path.join(user_upload_folder, custom_bg_filename)
                custom_bg.save(custom_bg_path)

                final_img = apply_custom_background(fg_rgba, custom_bg_path)
                output_filename = f"output_custom_{unique_id}.jpg"
                output_path = os.path.join(user_output_folder, output_filename)
                final_img.save(output_path, "JPEG", quality=95)

            else:
                flash("Invalid background mode selected.", "danger")
                return redirect(url_for("index"))

            # SAFE PATHS (No f-string backslash bug)
            original_image_web = safe_web_path(main_input_path)
            output_image_web = safe_web_path(output_path)

            result = {
                "original_image": original_image_web,
                "output_image": output_image_web,
                "download_file": output_filename,
                "username": username,
            }

            flash("Image processed successfully!", "success")

        except Exception as e:
            flash(f"Processing error: {str(e)}", "danger")
            return redirect(url_for("index"))

    templates = []
    if os.path.exists(app.config["TEMPLATE_BG_FOLDER"]):
        templates = [
            f for f in os.listdir(app.config["TEMPLATE_BG_FOLDER"])
            if allowed_file(f)
        ]

    return render_template("index.html", result=result, templates=templates)


@app.route("/download/<filename>")
@login_required
def download_file(filename):
    username = session.get("username", "guest")
    user_output_folder = get_user_folder(app.config["OUTPUT_FOLDER"], username)
    return send_from_directory(user_output_folder, filename, as_attachment=True)


# =========================
# RUN
# =========================
if __name__ == "__main__":
    app.run(debug=True)