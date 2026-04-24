import datetime
from sqlalchemy import Column, Integer, String, Boolean, ForeignKey, Text, DateTime, LargeBinary
from sqlalchemy.orm import relationship
from database import Base

class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    email = Column(String, unique=True, index=True)
    hashed_password = Column(String)
    is_active = Column(Boolean, default=True)
    reset_token = Column(String, index=True, nullable=True)
    reset_token_expiry = Column(DateTime, nullable=True)
    
    blogs = relationship("Blog", back_populates="user")

class Blog(Base):
    __tablename__ = "blogs"

    id = Column(Integer, primary_key=True, index=True)
    title = Column(String, index=True)
    filename = Column(String, unique=True, index=True)
    content = Column(Text)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    user_id = Column(Integer, ForeignKey("users.id"))

    user = relationship("User", back_populates="blogs")
    images = relationship("BlogImage", back_populates="blog", cascade="all, delete-orphan")

class BlogImage(Base):
    __tablename__ = "blog_images"

    id = Column(Integer, primary_key=True, index=True)
    filename = Column(String, index=True)
    content = Column(LargeBinary)
    blog_id = Column(Integer, ForeignKey("blogs.id"))

    blog = relationship("Blog", back_populates="images")
