from pydantic import BaseModel
from datetime import datetime


class ActivityTypeOut(BaseModel):
    id: int
    name: str

    class Config:
        from_attributes = True


class LogEntryCreate(BaseModel):
    activity_type_id: int
    notes: str | None = None


class LogEntryOut(BaseModel):
    id: int
    activity_type_id: int
    timestamp: datetime
    notes: str | None

    class Config:
        from_attributes = True
