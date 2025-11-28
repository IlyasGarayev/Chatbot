import os
import json
from flask import Flask, jsonify, session, request, redirect, url_for, send_from_directory, Response
from flask_migrate import Migrate
from flask_cors import CORS
from dotenv import load_dotenv
from authlib.integrations.flask_client import OAuth
from models import db, User, Message, ChatSession  # Import new ChatSession
from agent_chatbot import stream_chat_response  # Import new stream function

# Load environment variables
load_dotenv()

# App Initialization
app = Flask(__name__, static_folder='static')
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY')
app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get('DATABASE_URL')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

# Database Setup
db.init_app(app)
migrate = Migrate(app, db)

# Enable CORS for frontend
CORS(app, supports_credentials=True, resources={r"/api/*": {"origins": "http://127.0.0.1:5000"}})

# OAuth Setup
oauth = OAuth(app)
google = oauth.register(
    name='google',
    client_id=os.environ.get('GOOGLE_CLIENT_ID'),
    client_secret=os.environ.get('GOOGLE_CLIENT_SECRET'),
    client_kwargs={'scope': 'openid email profile'},
    server_metadata_url='https://accounts.google.com/.well-known/openid-configuration'
)


# --- Frontend Serving ---

@app.route('/')
def serve_index():
    return send_from_directory(app.static_folder, 'index.html')


@app.route('/<path:path>')
def serve_static(path):
    return send_from_directory(app.static_folder, path)


# --- Auth Routes ---

@app.route('/login')
def login():
    redirect_uri = url_for('auth', _external=True)
    return google.authorize_redirect(redirect_uri, response_type='code')


@app.route('/auth')
def auth():
    try:
        token = google.authorize_access_token()
    except Exception as e:
        print(f"Authlib authorization error: {e}")
        return "Authentication failed. Please try logging in again.", 400

    user_info = token.get('userinfo')
    if not user_info:
        return "Failed to retrieve user information from token.", 400

    user = db.session.scalar(db.select(User).where(User.google_id == user_info['sub']))

    if not user:
        user = User(
            google_id=user_info['sub'],
            email=user_info.get('email'),
            name=user_info.get('name'),
            picture=user_info.get('picture')
        )
        db.session.add(user)
        db.session.commit()

    session['user_id'] = user.id
    return redirect('/')


@app.route('/logout')
def logout():
    session.pop('user_id', None)
    return redirect('/')


# --- API Routes ---

def get_current_user():
    """Helper function to get the current user from session."""
    user_id = session.get('user_id')
    if not user_id:
        return None
    return db.session.get(User, user_id)


@app.route('/api/me')
def me():
    """Get the current logged-in user."""
    user = get_current_user()
    if not user:
        return jsonify({"error": "Not authenticated"}), 401

    return jsonify({
        "id": user.id,
        "name": user.name,
        "email": user.email,
        "picture": user.picture
    })


@app.route('/api/sessions', methods=['GET'])
def get_sessions():
    """Get all chat sessions for the user."""
    user = get_current_user()
    if not user:
        return jsonify({"error": "Not authenticated"}), 401

    sessions = db.session.scalars(
        db.select(ChatSession)
        .where(ChatSession.user_id == user.id)
        .order_by(ChatSession.created_at.desc())
    ).all()

    return jsonify([
        {"id": s.id, "title": s.title, "created_at": s.created_at}
        for s in sessions
    ])


@app.route('/api/session/<int:session_id>/messages', methods=['GET'])
def get_session_messages(session_id):
    """Get all messages for a specific chat session."""
    user = get_current_user()
    if not user:
        return jsonify({"error": "Not authenticated"}), 401

    # Check if this session belongs to the user
    chat_session = db.session.get(ChatSession, session_id)
    if not chat_session or chat_session.user_id != user.id:
        return jsonify({"error": "Session not found or access denied"}), 404

    messages = db.session.scalars(
        db.select(Message)
        .where(Message.session_id == session_id)
        .order_by(Message.created_at.asc())
    ).all()

    return jsonify([
        {"role": msg.role, "content": msg.content, "created_at": msg.created_at}
        for msg in messages
    ])


@app.route('/api/chat/stream', methods=['POST'])
def chat_stream():
    """Send a message and get a streaming response."""
    user = get_current_user()
    if not user:
        return jsonify({"error": "Not authenticated"}), 401

    data = request.json
    if not data or 'message' not in data:
        return jsonify({"error": "No message provided"}), 400

    user_message = data['message']
    session_id = data.get('sessionId')  # Can be null

    def generate_stream():
        # Wrap the entire generator logic in an app_context
        # This solves the "Working outside of application context" error
        with app.app_context():
            nonlocal session_id  # Allow modification of session_id

            try:
                chat_session = None

                # --- FIX 1: Check for existing session ---
                if session_id:
                    chat_session = db.session.get(ChatSession, session_id)
                    # Ensure session exists and belongs to the user
                    if not chat_session or chat_session.user_id != user.id:
                        chat_session = None
                        session_id = None  # Force creation of new session

                # If no valid session, create a new one
                if not chat_session:
                    title = (user_message[:50] + '...') if len(user_message) > 50 else user_message
                    new_session = ChatSession(user_id=user.id, title=title)
                    db.session.add(new_session)
                    db.session.commit()  # Commit to get the ID
                    session_id = new_session.id

                    # Send a control message to client
                    control_message = json.dumps({
                        "type": "session_created",
                        "sessionId": session_id,
                        "title": title,
                        "created_at": new_session.created_at.isoformat()
                    })
                    yield f"data: {control_message}\n\n"
                # --- End of FIX 1 ---

                # 2. Save User Message
                user_msg = Message(session_id=session_id, role="user", content=user_message)
                db.session.add(user_msg)
                db.session.commit()

                # 3. Stream AI Response
                full_reply = ""
                for chunk in stream_chat_response(session_id, user_message):
                    full_reply += chunk
                    chunk_data = json.dumps({"type": "content", "chunk": chunk})
                    yield f"data: {chunk_data}\n\n"

                # 4. Save Full AI Reply (AFTER the loop)
                # --- FIX 2: Save AI reply *after* stream is complete ---
                if full_reply:
                    ai_msg = Message(session_id=session_id, role="ai", content=full_reply)
                    db.session.add(ai_msg)
                    db.session.commit()
                # --- End of FIX 2 ---

            except Exception as e:
                db.session.rollback()
                print(f"Error in stream generator: {e}")
                error_message = json.dumps({"type": "error", "message": "An internal error occurred."})
                yield f"data: {error_message}\n\n"

    # Return the streaming response
    return Response(generate_stream(), mimetype='text/event-stream')


# --- Main Execution ---

if __name__ == '__main__':
    app.run(debug=True, port=5000)