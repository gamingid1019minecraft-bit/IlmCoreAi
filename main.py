import os
import sqlite3
import secrets
import bcrypt

from flask import Flask, request, jsonify
from flask_cors import CORS
from dotenv import load_dotenv
from groq import Groq

from google.oauth2 import id_token
from google.auth.transport import requests

# ==========================
# Load Environment
# ==========================

load_dotenv()

GROQ_API_KEY = os.getenv("GROQ_API_KEY")
GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID")

if not GROQ_API_KEY:
    raise Exception("GROQ_API_KEY not found in .env")

client = Groq(api_key=GROQ_API_KEY)

# ==========================
# Flask
# ==========================

app = Flask(__name__)
CORS(app)

print("🤖 IlmCoreAI Started!")

# ==========================
# Database
# ==========================

DATABASE = "ilmcore.db"

SYSTEM_PROMPT = """
You are IlmCoreAI.

Your owner and creator is Taha Bilal.

If someone asks who created you,
reply:

"My owner and creator is Taha Bilal."

Be friendly, professional and intelligent.
"""

messages = [
    {
        "role": "system",
        "content": SYSTEM_PROMPT
    }
]


def get_db():
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():

    conn = get_db()

    conn.execute("""
    CREATE TABLE IF NOT EXISTS users(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        email TEXT UNIQUE NOT NULL,
        password TEXT,
        token TEXT,
        google INTEGER DEFAULT 0
    )
    """)

    conn.commit()
    conn.close()
    # ==========================
# User Functions
# ==========================

def generate_token():
    return secrets.token_hex(32)


def create_user(name, email, password):

    conn = get_db()

    existing = conn.execute(
        "SELECT * FROM users WHERE email=?",
        (email,)
    ).fetchone()

    if existing:
        conn.close()
        return None, "Email already exists."

    hashed = bcrypt.hashpw(
        password.encode(),
        bcrypt.gensalt()
    ).decode()

    token = generate_token()

    cursor = conn.execute(
        """
        INSERT INTO users(name,email,password,token,google)
        VALUES(?,?,?,?,0)
        """,
        (
            name,
            email,
            hashed,
            token
        )
    )

    conn.commit()

    user = {
        "id": cursor.lastrowid,
        "name": name,
        "email": email
    }

    conn.close()

    return {
        "token": token,
        "user": user
    }, None


def login_user(email, password):

    conn = get_db()

    user = conn.execute(
        "SELECT * FROM users WHERE email=?",
        (email,)
    ).fetchone()

    if not user:
        conn.close()
        return None

    if user["google"] == 1:
        conn.close()
        return None

    if not bcrypt.checkpw(
        password.encode(),
        user["password"].encode()
    ):
        conn.close()
        return None

    token = generate_token()

    conn.execute(
        "UPDATE users SET token=? WHERE id=?",
        (
            token,
            user["id"]
        )
    )

    conn.commit()

    result = {
        "token": token,
        "user": {
            "id": user["id"],
            "name": user["name"],
            "email": user["email"]
        }
    }

    conn.close()

    return result


def google_login(idinfo):

    email = idinfo["email"]

    name = idinfo.get("name", email.split("@")[0])

    conn = get_db()

    user = conn.execute(
        "SELECT * FROM users WHERE email=?",
        (email,)
    ).fetchone()

    token = generate_token()

    if user:

        conn.execute(
            "UPDATE users SET token=? WHERE id=?",
            (
                token,
                user["id"]
            )
        )

        conn.commit()

        result = {
            "token": token,
            "user": {
                "id": user["id"],
                "name": user["name"],
                "email": user["email"]
            }
        }

        conn.close()

        return result

    cursor = conn.execute(
        """
        INSERT INTO users(name,email,password,token,google)
        VALUES(?,?,?,?,1)
        """,
        (
            name,
            email,
            "",
            token
        )
    )

    conn.commit()

    result = {
        "token": token,
        "user": {
            "id": cursor.lastrowid,
            "name": name,
            "email": email
        }
    }

    conn.close()

    return result
    # ==========================
# Routes
# ==========================

@app.get("/")
def home():
    return "🤖 IlmCoreAI Backend Running"


@app.get("/health")
def health():
    return jsonify({
        "status": "ok",
        "message": "IlmCoreAI Backend Online"
    })


# --------------------------
# Register
# --------------------------

@app.post("/auth/register")
def register():

    data = request.get_json() or {}

    name = data.get("name", "").strip()
    email = data.get("email", "").strip().lower()
    password = data.get("password", "")

    if not name or not email or not password:
        return jsonify({
            "error": "Please fill all fields."
        }), 400

    result, error = create_user(
        name,
        email,
        password
    )

    if error:
        return jsonify({
            "error": error
        }), 400

    return jsonify(result)


# --------------------------
# Login
# --------------------------

@app.post("/auth/login")
def login():

    data = request.get_json() or {}

    email = data.get("email", "").strip().lower()
    password = data.get("password", "")

    result = login_user(
        email,
        password
    )

    if not result:
        return jsonify({
            "error": "Invalid email or password."
        }), 401

    return jsonify(result)


# --------------------------
# Google Login
# --------------------------

@app.post("/auth/google")
def google():

    data = request.get_json() or {}

    token = data.get("id_token")

    if not token:
        return jsonify({
            "error": "Missing Google token."
        }), 400

    try:

        idinfo = id_token.verify_oauth2_token(
            token,
            requests.Request(),
            GOOGLE_CLIENT_ID
        )

        result = google_login(idinfo)

        return jsonify(result)

    except Exception as e:

        return jsonify({
            "error": "Invalid Google token.",
            "details": str(e)
        }), 401


# --------------------------
# Chat
# --------------------------

@app.post("/chat")
def chat():

    data = request.get_json() or {}

    message = data.get("message", "").strip()

    if message == "":
        return jsonify({
            "reply": "Please type a message."
        })

    lower = message.lower()

    if "who made you" in lower or \
       "who created you" in lower or \
       "who owns you" in lower or \
       "owner" in lower:

        return jsonify({
            "reply": "My owner and creator is Taha Bilal."
        })

    if "who are you" in lower or \
       "your name" in lower:

        return jsonify({
            "reply": "I am IlmCoreAI, your intelligent AI assistant."
        })

    messages.append({
        "role": "user",
        "content": message
    })

    response = client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=messages,
        temperature=0.7,
        max_tokens=1024
    )

    reply = response.choices[0].message.content

    messages.append({
        "role": "assistant",
        "content": reply
    })

    return jsonify({
        "reply": reply
    })


# ==========================
# Start Server
# ==========================

if __name__ == "__main__":

    init_db()

    app.run(
        host="127.0.0.1",
        port=5000,
        debug=True
    )