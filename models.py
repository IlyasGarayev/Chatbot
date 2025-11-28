from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import Integer, String, Text, ForeignKey, DateTime
from sqlalchemy.orm import DeclarativeBase, mapped_column, relationship
from sqlalchemy.sql import func
import datetime


class Base(DeclarativeBase):
    pass


db = SQLAlchemy(model_class=Base)


class User(db.Model):
    __tablename__ = 'users'

    id = mapped_column(Integer, primary_key=True)
    google_id = mapped_column(String(255), unique=True, nullable=False)
    email = mapped_column(String(255), unique=True, nullable=False)
    name = mapped_column(String(255))
    picture = mapped_column(String(255))
    created_at = mapped_column(DateTime(timezone=True), server_default=func.now())

    # Relationship to ChatSessions
    chat_sessions = relationship('ChatSession', back_populates='user', lazy=True, cascade="all, delete-orphan")

    def __repr__(self):
        return f'<User {self.email}>'


class ChatSession(db.Model):
    __tablename__ = 'chat_sessions'

    id = mapped_column(Integer, primary_key=True)
    user_id = mapped_column(Integer, ForeignKey('users.id'), nullable=False)
    title = mapped_column(String(255), nullable=False, default='New Chat')
    created_at = mapped_column(DateTime(timezone=True), server_default=func.now())

    # Relationship to user
    user = relationship('User', back_populates='chat_sessions')

    # Relationship to messages
    messages = relationship('Message', back_populates='chat_session', lazy=True, cascade="all, delete-orphan")

    def __repr__(self):
        return f'<ChatSession {self.id} by User {self.user_id}>'


class Message(db.Model):
    __tablename__ = 'messages'

    id = mapped_column(Integer, primary_key=True)
    session_id = mapped_column(Integer, ForeignKey('chat_sessions.id'), nullable=False)
    role = mapped_column(String(50), nullable=False)  # 'user' or 'ai'
    content = mapped_column(Text, nullable=False)
    created_at = mapped_column(DateTime(timezone=True), server_default=func.now())

    # Relationship to chat session
    chat_session = relationship('ChatSession', back_populates='messages')

    def __repr__(self):
        return f'<Message {self.id} in Session {self.session_id}>'
