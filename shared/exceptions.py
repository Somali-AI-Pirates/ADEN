from collections.abc import Mapping


class ServiceError(Exception):
    def __init__(
        self,
        message: str,
        status_code: int,
        *,
        response_headers: Mapping[str, str] | None = None,
    ):
        super().__init__(message)
        self.status_code = status_code
        self.response_headers = dict(response_headers or {})
