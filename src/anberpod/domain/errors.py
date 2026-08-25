"""Errors safe for translation into UI messages and structured logs."""


class AnberPodError(Exception):
    code = "anberpod_error"


class DataSafetyError(AnberPodError):
    code = "data_safety"


class MigrationError(AnberPodError):
    code = "migration_failed"


class ImportValidationError(AnberPodError):
    code = "import_validation"


class HttpPolicyError(AnberPodError):
    def __init__(self, message: str, *, code: str = "http_policy") -> None:
        super().__init__(message)
        self.code = code


class FeedParseError(AnberPodError):
    code = "invalid_feed"


class FeedHttpError(AnberPodError):
    def __init__(self, message: str, *, code: str = "feed_http") -> None:
        super().__init__(message)
        self.code = code


class CatalogError(AnberPodError):
    code = "catalog_error"


class MissingCatalogCredentialsError(CatalogError):
    code = "catalog_credentials_missing"


class CatalogAuthenticationError(CatalogError):
    code = "catalog_authentication"


class CatalogRateLimitError(CatalogError):
    code = "catalog_rate_limit"

    def __init__(self, retry_after_seconds: int | None = None) -> None:
        super().__init__("Podcast Index rate limit reached")
        self.retry_after_seconds = retry_after_seconds


class CatalogUnavailableError(CatalogError):
    code = "catalog_unavailable"


class CatalogDataError(CatalogError):
    code = "catalog_bad_data"


class CatalogClockError(CatalogError):
    code = "catalog_clock"


class DownloadError(AnberPodError):
    def __init__(self, message: str, *, code: str = "download_failed") -> None:
        super().__init__(message)
        self.code = code


class PlaybackError(AnberPodError):
    """A playback failure safe to expose in the UI and structured logs."""

    def __init__(self, message: str, *, code: str = "playback_failed") -> None:
        super().__init__(message)
        self.code = code
