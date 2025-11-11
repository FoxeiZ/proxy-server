from enum import Enum


class DownloadStatus(Enum):
    PENDING = "pending"  # queued for download
    DOWNLOADING = "downloading"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    FAILED = "failed"
    MISSING = "missing"  # some files are missing


class FileStatus(Enum):
    CONVERTED = "converted"
    COMPLETED = "completed"
    MISSING = "missing"  # has file entry but file is missing
    NOT_FOUND = "not_found"  # file not found in manga directory
    IN_DIFF_LANG = "in_diff_lang"  # found but in different language
    AVAILABLE = "available"  # has title match but maybe id different
    MAYBE_AVALIABLE = (
        "fuzzy"  # has similar title but different id or maybe different chapter
    )
    PLACEHOLDER = "placeholder"  # debug
