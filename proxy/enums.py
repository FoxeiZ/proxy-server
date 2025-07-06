from enum import Enum


class DownloadStatus(Enum):
    PENDING = "pending"
    DOWNLOADING = "downloading"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    FAILED = "failed"
    MISSING = "missing"


class FileStatus(Enum):
    CONVERTED = "converted"
    COMPLETED = "completed"
    MISSING = "missing"
    NOT_FOUND = "not_found"
