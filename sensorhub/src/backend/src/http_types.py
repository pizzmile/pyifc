from dataclasses import dataclass


@dataclass(frozen=True)
class HttpResponse:
    status_code: int
    body: dict