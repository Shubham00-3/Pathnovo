"""Typed, visible failures for the delta-chat pipeline."""

from __future__ import annotations


class DeltaChatError(Exception):
    """Base error with a stable code for traces and UI."""

    code = "delta_chat_error"

    def __init__(self, message: str, *, details: dict | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = details or {}

    def to_dict(self) -> dict:
        return {
            "error_type": self.__class__.__name__,
            "code": self.code,
            "message": self.message,
            "details": self.details,
        }


class PidNotFoundError(DeltaChatError):
    code = "pid_not_found"


class UnsupportedFormatError(DeltaChatError):
    code = "unsupported_format"


class CorruptDocumentError(DeltaChatError):
    code = "corrupt_document"


class ResourceLimitError(DeltaChatError):
    code = "resource_limit"


class OcrFailure(DeltaChatError):
    code = "ocr_failure"


class PairMismatchError(DeltaChatError):
    code = "pair_mismatch"


class RegistrationFailure(DeltaChatError):
    code = "registration_failure"


class LLMTimeoutError(DeltaChatError):
    code = "llm_timeout"


class CitationValidationError(DeltaChatError):
    code = "citation_validation"


class ConfigError(DeltaChatError):
    code = "config_error"


class PathEscapeError(DeltaChatError):
    code = "path_escape"
