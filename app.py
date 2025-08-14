from flask import Flask, render_template, request, jsonify, redirect, url_for, session, make_response
from flask_sqlalchemy import SQLAlchemy
from google import genai
import os
from dotenv import load_dotenv
from xhtml2pdf import pisa
import io

# Load environment variables
load_dotenv()
API_KEY = os.getenv("GEMINI_API_KEY")

# -----------------------
# Flask setup
# -----------------------
app = Flask(__name__)
app.secret_key = "secret123"
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///health_assistant.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)
client = genai.Client(api_key=API_KEY)

# -----------------------
# Models
# -----------------------
class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(100), unique=True, nullable=False)
    password = db.Column(db.String(100), nullable=False)

class ChatHistory(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    category = db.Column(db.String(100))
    user_message = db.Column(db.Text)
    ai_response = db.Column(db.Text)

# -----------------------
# Routes
# -----------------------
@app.route("/")
def home():
    if "user_id" in session:
        chats = ChatHistory.query.filter_by(user_id=session["user_id"]).all()
        return render_template("index.html", chats=chats)
    return redirect(url_for("login"))

@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        username = request.form["username"]
        password = request.form["password"]
        if User.query.filter_by(username=username).first():
            return "Username already exists."
        new_user = User(username=username, password=password)
        db.session.add(new_user)
        db.session.commit()
        return redirect(url_for("login"))
    return render_template("register.html")

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form["username"]
        password = request.form["password"]
        user = User.query.filter_by(username=username, password=password).first()
        if user:
            session["user_id"] = user.id
            return redirect(url_for("home"))
        return "Invalid credentials."
    return render_template("login.html")

@app.route("/logout")
def logout():
    session.pop("user_id", None)
    session.pop("admin", None)
    return redirect(url_for("login"))

@app.route("/ask", methods=["POST"])
def ask_ai():
    if "user_id" not in session:
        return jsonify({"error": "Not logged in"}), 401
    data = request.json
    category = data.get("category")
    user_message = data.get("message")
    if not user_message:
        return jsonify({"error": "No message provided"}), 400
    prompt = f"You are a helpful AI health assistant. Category: {category}. User says: {user_message}."
    try:
        response = client.models.generate_content(model="gemini-2.5-flash", contents=prompt)
        ai_reply = response.text
        chat_entry = ChatHistory(user_id=session["user_id"], category=category,
                                 user_message=user_message, ai_response=ai_reply)
        db.session.add(chat_entry)
        db.session.commit()
        return jsonify({"reply": ai_reply})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/symptoms")
def symptoms():
    if "user_id" not in session:
        return redirect(url_for("login"))
    return render_template("symptoms.html")

@app.route("/check_symptoms", methods=["POST"])
def check_symptoms():
    if "user_id" not in session:
        return jsonify({"error": "Not logged in"}), 401
    symptoms = request.json.get("symptoms", "")
    prompt = f"You are a medical assistant. A user reports the following symptoms: {symptoms}. " \
             f"Suggest possible conditions (non-diagnostic), precautions, and next steps."
    try:
        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=prompt
        )
        return jsonify({"reply": response.text})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/export_pdf")
def export_pdf():
    if "user_id" not in session:
        return redirect(url_for("login"))
    chats = ChatHistory.query.filter_by(user_id=session["user_id"]).all()
    rendered = render_template("pdf_template.html", chats=chats)
    pdf_buffer = io.BytesIO()
    pisa_status = pisa.CreatePDF(rendered, dest=pdf_buffer)
    if pisa_status.err:
        return "Error generating PDF"
    pdf_buffer.seek(0)
    response = make_response(pdf_buffer.read())
    response.headers['Content-Type'] = 'application/pdf'
    response.headers['Content-Disposition'] = 'attachment; filename=chat_history.pdf'
    return response

# -----------------------
# Admin
# -----------------------
ADMIN_USERNAME = "admin"
ADMIN_PASSWORD = "admin123"

@app.route("/admin", methods=["GET", "POST"])
def admin_login():
    if request.method == "POST":
        username = request.form["username"]
        password = request.form["password"]
        if username == ADMIN_USERNAME and password == ADMIN_PASSWORD:
            session["admin"] = True
            return redirect(url_for("admin_dashboard"))
        return "Invalid admin credentials."
    return render_template("login.html", admin=True)

@app.route("/admin/dashboard")
def admin_dashboard():
    if not session.get("admin"):
        return redirect(url_for("admin_login"))
    total_users = User.query.count()
    total_chats = ChatHistory.query.count()
    recent_chats = ChatHistory.query.order_by(ChatHistory.id.desc()).limit(10).all()
    categories = db.session.query(ChatHistory.category, db.func.count(ChatHistory.category)) \
                           .group_by(ChatHistory.category).all()
    categories_dict = {c[0]: c[1] for c in categories}
    return render_template("admin.html", total_users=total_users,
                           total_chats=total_chats,
                           recent_chats=recent_chats,
                           categories_dict=categories_dict)

@app.route("/get_suggestions", methods=["POST"])
def get_suggestions():
    if "user_id" not in session:
        return jsonify({"error": "Not logged in"}), 401
    conversation = request.json.get("conversation", "")
    prompt = f"You are an AI assistant. Based on this user conversation: \"{conversation}\", " \
             f"suggest 3-5 relevant follow-up questions or tips in short phrases. Respond only with a JSON list."
    try:
        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=prompt
        )
        import json
        try:
            suggestions = json.loads(response.text)
        except:
            suggestions = [s.strip() for s in response.text.split("\n") if s.strip()]
        return jsonify({"suggestions": suggestions[:5]})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/analytics")
def analytics():
    # Example data - replace with real DB queries
    total_chats = 42
    categories = ["General Health", "Symptoms", "Nutrition", "Mental Health", "Other"]
    counts = [15, 12, 5, 6, 4]
    recent_chats = [
        {"user_message": "I have a headache and fever", "ai_response": "It could be the flu or a cold..."},
        {"user_message": "Best foods for heart health?", "ai_response": "Include leafy greens, berries, and nuts..."},
    ]

    return render_template(
        "analytics.html",
        total_chats=total_chats,
        categories=categories,
        counts=counts,
        recent_chats=recent_chats
    )


# -----------------------
# Run App
# -----------------------
if __name__ == "__main__":
    with app.app_context():
        db.create_all()
    app.run(debug=True)
