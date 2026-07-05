import os
import sqlite3
import secrets
import bcrypt
from datetime import datetime
import uuid
from functools import wraps
 
from flask import Flask, request, jsonify, g
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
 
def get_db():
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    return conn
 
def init_db():
    conn = get_db()
 
    # Users table
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
 
    # Chat history table
    conn.execute("""
    CREATE TABLE IF NOT EXISTS chat_history(
        id TEXT PRIMARY KEY,
        user_id INTEGER NOT NULL,
        session_id TEXT NOT NULL,
        role TEXT NOT NULL,
        content TEXT NOT NULL,
        timestamp TEXT NOT NULL,
        FOREIGN KEY (user_id) REFERENCES users(id)
    )
    """)
 
    # Sessions table for managing multiple conversations
    conn.execute("""
    CREATE TABLE IF NOT EXISTS sessions(
        id TEXT PRIMARY KEY,
        user_id INTEGER NOT NULL,
        title TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        FOREIGN KEY (user_id) REFERENCES users(id)
    )
    """)
 
    conn.commit()
    conn.close()
 
 
# Run at IMPORT time, not just under `if __name__ == "__main__"`. PythonAnywhere's
# WSGI server imports `app` from this file — it never runs the file as a script —
# so anything only inside the __main__ guard (like init_db()) was silently never
# executing, meaning these tables never got created on the actual server.
init_db()
 
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
        (name, email, hashed, token)
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
        (token, user["id"])
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
            (token, user["id"])
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
        (name, email, "", token)
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
# Chat History Functions
# ==========================
 
def get_user_from_token(token):
    """Validate token and return user"""
    if not token:
        return None
 
    conn = get_db()
    user = conn.execute(
        "SELECT * FROM users WHERE token=?",
        (token,)
    ).fetchone()
    conn.close()
 
    return user
 
def create_session(user_id, title="New conversation"):
    """Create a new chat session"""
    session_id = str(uuid.uuid4())
    now = datetime.utcnow().isoformat()
 
    conn = get_db()
    conn.execute(
        "INSERT INTO sessions (id, user_id, title, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
        (session_id, user_id, title, now, now)
    )
    conn.commit()
    conn.close()
 
    return session_id
 
def get_sessions(user_id):
    """Get all sessions for a user"""
    conn = get_db()
    sessions = conn.execute(
        "SELECT * FROM sessions WHERE user_id=? ORDER BY updated_at DESC",
        (user_id,)
    ).fetchall()
    conn.close()
 
    return [dict(s) for s in sessions]
 
def save_message(user_id, session_id, role, content):
    """Save a message to chat history"""
    msg_id = str(uuid.uuid4())
    timestamp = datetime.utcnow().isoformat()
 
    conn = get_db()
    conn.execute(
        "INSERT INTO chat_history (id, user_id, session_id, role, content, timestamp) VALUES (?, ?, ?, ?, ?, ?)",
        (msg_id, user_id, session_id, role, content, timestamp)
    )
 
    # Update session timestamp
    conn.execute(
        "UPDATE sessions SET updated_at=? WHERE id=?",
        (timestamp, session_id)
    )
 
    conn.commit()
    conn.close()
 
def get_chat_history(user_id, session_id, limit=20):
    """Get recent messages for a session (oldest to newest) — used to
    build context for the Groq API call. Only role/content, since Groq's
    API rejects unexpected extra keys in a message object."""
    conn = get_db()
    messages = conn.execute(
        """
        SELECT role, content FROM chat_history
        WHERE user_id=? AND session_id=?
        ORDER BY timestamp DESC LIMIT ?
        """,
        (user_id, session_id, limit)
    ).fetchall()
    conn.close()
 
    # Reverse to get oldest first
    return [dict(m) for m in messages[::-1]]
 
def get_chat_history_for_display(user_id, session_id, limit=200):
    """Same as get_chat_history, but includes the timestamp — used for
    the /sessions/<id>/messages endpoint the frontend renders in the
    chat window (it needs a timestamp per message, Groq does not)."""
    conn = get_db()
    messages = conn.execute(
        """
        SELECT role, content, timestamp FROM chat_history
        WHERE user_id=? AND session_id=?
        ORDER BY timestamp ASC LIMIT ?
        """,
        (user_id, session_id, limit)
    ).fetchall()
    conn.close()
 
    return [dict(m) for m in messages]
 
def delete_session(user_id, session_id):
    """Delete a session and its messages"""
    conn = get_db()
    conn.execute(
        "DELETE FROM chat_history WHERE user_id=? AND session_id=?",
        (user_id, session_id)
    )
    conn.execute(
        "DELETE FROM sessions WHERE user_id=? AND id=?",
        (user_id, session_id)
    )
    conn.commit()
    conn.close()
 
# ==========================
# Authentication Decorator
# ==========================
 
def require_auth(f):
    """Decorator to check authentication"""
    @wraps(f)
    def decorated(*args, **kwargs):
        token = request.headers.get('Authorization')
        if not token:
            return jsonify({"error": "Authentication required"}), 401
 
        # Remove 'Bearer ' prefix if present
        if token.startswith('Bearer '):
            token = token[7:]
 
        user = get_user_from_token(token)
        if not user:
            return jsonify({"error": "Invalid or expired token"}), 401
 
        # Add user to request context
        request.user = user
        return f(*args, **kwargs)
    return decorated
 
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
# Auth Routes (unchanged)
# --------------------------
 
@app.post("/auth/register")
def register():
    data = request.get_json() or {}
    name = data.get("name", "").strip()
    email = data.get("email", "").strip().lower()
    password = data.get("password", "")
 
    if not name or not email or not password:
        return jsonify({"error": "Please fill all fields."}), 400
 
    result, error = create_user(name, email, password)
    if error:
        return jsonify({"error": error}), 400
 
    return jsonify(result)
 
@app.post("/auth/login")
def login():
    data = request.get_json() or {}
    email = data.get("email", "").strip().lower()
    password = data.get("password", "")
 
    result = login_user(email, password)
    if not result:
        return jsonify({"error": "Invalid email or password."}), 401
 
    return jsonify(result)
 
@app.post("/auth/google")
def google():
    data = request.get_json() or {}
    token = data.get("id_token")
 
    if not token:
        return jsonify({"error": "Missing Google token."}), 400
 
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
# Sessions (renamed to match the frontend's actual fetch() calls —
# it calls "/sessions", not "/chat/sessions")
# --------------------------
 
@app.get("/sessions")
@require_auth
def list_sessions():
    """List all sessions for the user"""
    sessions = get_sessions(request.user["id"])
    return jsonify({"sessions": sessions})
 
@app.get("/sessions/<session_id>/messages")
@require_auth
def session_messages(session_id):
    """Full message history for one session — matches what the
    frontend calls when you click a conversation in the sidebar."""
    conn = get_db()
    session_row = conn.execute(
        "SELECT * FROM sessions WHERE id=? AND user_id=?",
        (session_id, request.user["id"])
    ).fetchone()
    conn.close()
 
    if not session_row:
        return jsonify({"error": "Conversation not found"}), 404
 
    messages = get_chat_history_for_display(request.user["id"], session_id)
    return jsonify({"messages": messages})
 
@app.delete("/sessions/<session_id>")
@require_auth
def delete_session_route(session_id):
    """Delete a session"""
    conn = get_db()
    session_row = conn.execute(
        "SELECT * FROM sessions WHERE id=? AND user_id=?",
        (session_id, request.user["id"])
    ).fetchone()
    conn.close()
 
    if not session_row:
        return jsonify({"error": "Conversation not found"}), 404
 
    delete_session(request.user["id"], session_id)
    return jsonify({"success": True})
 
# --------------------------
# Chat
# --------------------------
 
@app.post("/chat")
@require_auth
def chat():
    data = request.get_json() or {}
    message = data.get("message", "").strip()
    session_id = data.get("session_id", None)
 
    user_id = request.user["id"]
 
    if not message:
        return jsonify({"error": "Please type a message."}), 400
 
    # Create session if not provided — title it from the first message,
    # so the sidebar shows something meaningful instead of "New Chat"
    # for every conversation.
    if not session_id:
        title = message[:60] + ("…" if len(message) > 60 else "")
        session_id = create_session(user_id, title)
    else:
        # Check the session exists and belongs to this user.
        conn = get_db()
        session_row = conn.execute(
            "SELECT * FROM sessions WHERE id=? AND user_id=?",
            (session_id, user_id)
        ).fetchone()
        conn.close()
 
        if not session_row:
            return jsonify({"error": "Invalid session"}), 404
 
    # Save user message
    save_message(user_id, session_id, "user", message)
 
    # Get recent history (limit to 10 messages to save tokens)
    history = get_chat_history(user_id, session_id, limit=10)
 
    # Build messages for API
    chat_messages = [
        {"role": "system", "content": SYSTEM_PROMPT}
    ]
    chat_messages.extend(history)
 
    # Call Groq
    response = client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=chat_messages,
        temperature=0.7,
        max_tokens=1024
    )
 
    reply = response.choices[0].message.content
 
    # Save assistant reply
    save_message(user_id, session_id, "assistant", reply)
 
    return jsonify({
        "reply": reply,
        "session_id": session_id
    })
 
# ==========================
# Start Server
# ==========================
 
if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=int(os.environ.get("PORT", 5000)),
        debug=True
    )
