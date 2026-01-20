from sqlalchemy import Column, Integer, String, Text, ForeignKey, DateTime
from sqlalchemy.sql import func
from database import Base

class ActivityType(Base):
    __tablename__ = "activity_types"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), unique=True, nullable=False)
    parent_id = Column(Integer, ForeignKey("parent_activities.id"), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

class LogEntry(Base):
    __tablename__ = "log_entry"

    id = Column(Integer, primary_key=True, index=True)
    activity_type_id = Column(Integer, ForeignKey("activity_types.id"), nullable=True)
    custom_activity = Column(String(255), nullable=True)   # <-- THIS MUST EXIST
    notes = Column(Text, nullable=True)
    duration_minutes = Column(Integer, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class ParentActivity(Base):
    __tablename__ = "parent_activities"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), unique=True, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
