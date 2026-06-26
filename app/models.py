import enum
import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, Enum as SAEnum, String
from sqlalchemy.orm import Mapped, mapped_column

from .database import Base


class PrintJobStatus(str, enum.Enum):
    """Status values for a PrintJob, progressing from PENDING to a terminal state."""

    PENDING = "PENDING"
    PROCESSING = "PROCESSING"
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"


class PrintJob(Base):
    """ORM model representing a photo submitted for printing.

    Tracks lifecycle from upload (PENDING) through printer claim (PROCESSING)
    to final outcome (SUCCESS or FAILED).
    """

    __tablename__ = "print_jobs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    filename: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    original_name: Mapped[str] = mapped_column(String, nullable=False)
    device_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    status: Mapped[PrintJobStatus] = mapped_column(
        SAEnum(PrintJobStatus), default=PrintJobStatus.PENDING, nullable=False, index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc), nullable=False, index=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
