import uuid
from datetime import datetime
from pydantic import BaseModel, ConfigDict


class SubmissionMetadataOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    version: int
    original_filename: str
    mime_type: str
    size_bytes: int
    status: str
    submitted_at: datetime
    version_count: int
