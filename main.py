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

load_dotenv()

GROQ_API_KEY = os.getenv("GROQ_API_KEY")
GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID")

if not GROQ_API_KEY:
    raise Exception("GROQ_API_KEY not found")

client = Groq(api_key=GROQ_API_KEY)

app = Flask(__name__)
CORS(app)

DATABASE = "ilmcore.db"

SYSTEM_PROMPT = """
You are IlmCoreAI.

Your owner and creator is Taha Bilal.

Be friendly, professional and intelligent.
"""


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

    conn.execute("""
    CREATE TABLE IF NOT EXISTS sessions(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        title TEXT DEFAULT 'New Chat',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """)

    conn.execute("""
    CREATE TABLE IF NOT EXISTS messages(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        session_id INTEGER NOT NULL,
        role TEXT NOT NULL,
        content TEXT NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """)

    conn.commit()
    conn.close()


def generate_token():
    return secrets.token_hex(32)

# ==========================
# USER FUNCTIONS
# ==========================

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
# AUTH HELPERS
# ==========================

def get_user_from_token(token):

    if not token:
        return None

    conn = get_db()

    user = conn.execute(
        "SELECT * FROM users WHERE token=?",
        (token,)
    ).fetchone()

    conn.close()

    return user


from functools import wraps

def require_auth(func):

    @wraps(func)
    def wrapper(*args, **kwargs):

        token = request.headers.get(
            "Authorization",
            ""
        ).replace("Bearer ", "")

        user = get_user_from_token(token)

        if not user:
            return jsonify({
                "error": "Unauthorized"
            }), 401

        request.user = user

        return func(*args, **kwargs)

    return wrapper

# ==========================
# CHAT HISTORY FUNCTIONS
# ==========================

def create_session(user_id, title="New Chat"):

    conn = get_db()

    cursor = conn.execute(
        """
        INSERT INTO sessions(user_id,title)
        VALUES(?,?)
        """,
        (
            user_id,
            title
        )
    )

    conn.commit()

    session_id = cursor.lastrowid

    conn.close()

    return session_id


def save_message(session_id, role, content):

    conn = get_db()

    conn.execute(
        """
        INSERT INTO messages(
            session_id,
            role,
            content
        )
        VALUES(?,?,?)
        """,
        (
            session_id,
            role,
            content
        )
    )

    conn.commit()

    conn.close()


def load_messages(session_id):

    conn = get_db()

    rows = conn.execute(
        """
        SELECT role,content
        FROM messages
        WHERE session_id=?
        ORDER BY id ASC
        """,
        (session_id,)
    ).fetchall()

    conn.close()

    history = []

    history.append({
        "role":"system",
        "content":SYSTEM_PROMPT
    })

    for row in rows:

        history.append({
            "role":row["role"],
            "content":row["content"]
        })

    return history


# ==========================
# SESSION ROUTES
# ==========================

@app.get("/sessions")
@require_auth
def get_sessions_route():

    conn = get_db()

    rows = conn.execute(
        """
        SELECT *
        FROM sessions
        WHERE user_id=?
        ORDER BY id DESC
        """,
        (
            request.user["id"],
        )
    ).fetchall()

    conn.close()

    return jsonify(rows=[dict(x) for x in rows])


@app.post("/sessions")
@require_auth
def new_session():

    session_id = create_session(
        request.user["id"]
    )

    return jsonify({
        "session_id":session_id
    })


@app.get("/sessions/<int:session_id>/messages")
@require_auth
def get_messages(session_id):

    conn = get_db()

    session = conn.execute(
        """
        SELECT *
        FROM sessions
        WHERE id=?
        AND user_id=?
        """,
        (
            session_id,
            request.user["id"]
        )
    ).fetchone()

    if not session:

        conn.close()

        return jsonify({
            "error":"Not found"
        }),404

    rows = conn.execute(
        """
        SELECT role,content,created_at
        FROM messages
        WHERE session_id=?
        ORDER BY id
        """,
        (session_id,)
    ).fetchall()

    conn.close()

    return jsonify(
        messages=[
            dict(x)
            for x in rows
        ]
    )


@app.delete("/sessions/<int:session_id>")
@require_auth
def delete_chat(session_id):

    conn = get_db()

    conn.execute(
        "DELETE FROM messages WHERE session_id=?",
        (session_id,)
    )

    conn.execute(
        """
        DELETE FROM sessions
        WHERE id=?
        AND user_id=?
        """,
        (
            session_id,
            request.user["id"]
        )
    )

    conn.commit()

    conn.close()

    return jsonify({
        "success":True
    })

# ==========================================
# DELETE CHAT SESSION
# ==========================================

@app.delete("/sessions/<int:session_id>")
def delete_session(session_id):

    token = request.headers.get("Authorization", "").replace("Bearer ", "")

    if not token:
        return jsonify({"error": "Unauthorized"}), 401

    conn = get_db()

    user = conn.execute(
        "SELECT * FROM users WHERE token=?",
        (token,)
    ).fetchone()

    if not user:
        conn.close()
        return jsonify({"error": "Unauthorized"}), 401

    conn.execute(
        "DELETE FROM messages WHERE session_id=?",
        (session_id,)
    )

    conn.execute(
        "DELETE FROM sessions WHERE id=? AND user_id=?",
        (session_id, user["id"])
    )

    conn.commit()
    conn.close()

    return jsonify({"success": True})


# ==========================================
# START SERVER
# ==========================================

if __name__ == "__main__":

    init_db()

    app.run(
        host="0.0.0.0",
        port=5000,
        debug=True
    )
