import json
import logging
import uuid
from typing import Callable, Protocol

from .http_types import HttpResponse
from .schemas import InvalidSchemaValue, UnsupportedReadingKind, validate_job, validate_upload


MAX_BODY_BYTES = 4096
logger = logging.getLogger(__name__)


class StagingWriter(Protocol):
    def put(self, key: str, body: bytes, content_type: str) -> None: ...


class ReadingPublisher(Protocol):
    def publish(self, kind: str, job: dict) -> None: ...


class RequestError(Exception):
    status_code = 400
    code = "invalid_request"
    message = "The request body is not valid JSON."


class UnsupportedMediaType(RequestError):
    status_code = 415
    code = "unsupported_media_type"
    message = "Content-Type must be application/json."


class PayloadTooLarge(RequestError):
    status_code = 413
    code = "payload_too_large"
    message = "The request body exceeds 4096 bytes."


class InvalidReading(RequestError):
    status_code = 422
    code = "invalid_reading"
    message = "The reading does not match the supported schema."


class StagingUnavailable(RequestError):
    status_code = 503
    code = "staging_unavailable"
    message = "The reading could not be staged."


class PublishingUnavailable(RequestError):
    status_code = 503
    code = "publishing_unavailable"
    message = "The reading was staged but could not be queued."


def _reject_json_constant(value):
    raise ValueError(f"Non-standard JSON constant: {value}")


def _parse_and_validate(body: bytes, content_type: str | None) -> str:
    media_type = (content_type or "").split(";", maxsplit=1)[0].strip().lower()
    if media_type != "application/json":
        raise UnsupportedMediaType
    if len(body) > MAX_BODY_BYTES:
        raise PayloadTooLarge

    try:
        text = body.decode("utf-8")
        reading = json.loads(text, parse_constant=_reject_json_constant)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
        raise RequestError from None

    if not isinstance(reading, dict):
        raise RequestError

    try:
        return validate_upload(reading)
    except (UnsupportedReadingKind, InvalidSchemaValue, TypeError):
        raise InvalidReading from None


def prepare_reading(
    body: bytes,
    content_type: str | None,
    *,
    id_factory: Callable[[], object] = uuid.uuid4,
) -> tuple[str, str, dict]:
    kind = _parse_and_validate(body, content_type)
    reading_id = str(id_factory())
    staging_key = f"raw/{reading_id}.json"
    job = {"id": reading_id, "staging_key": staging_key}

    try:
        validate_job(job)
    except InvalidSchemaValue:
        raise RuntimeError("ID factory produced an invalid reading UUID") from None

    return kind, staging_key, job


def ingest_reading(
    body: bytes,
    content_type: str | None,
    staging: StagingWriter,
    publisher: ReadingPublisher,
    *,
    id_factory: Callable[[], object] = uuid.uuid4,
) -> str:
    kind, staging_key, job = prepare_reading(body, content_type, id_factory=id_factory)
    reading_id = job["id"]

    try:
        staging.put(staging_key, body, "application/json")
    except Exception:
        logger.exception("Staging write failed for reading_id=%s", reading_id)
        raise StagingUnavailable from None

    try:
        publisher.publish(kind, job)
    except Exception:
        logger.exception(
            "Queue publish failed for reading_id=%s staging_key=%s",
            reading_id,
            staging_key,
        )
        raise PublishingUnavailable from None

    return reading_id


def handle_post_readings(
    body: bytes,
    content_type: str | None,
    staging: StagingWriter,
    publisher: ReadingPublisher,
    *,
    id_factory: Callable[[], object] = uuid.uuid4,
) -> HttpResponse:
    try:
        reading_id = ingest_reading(
            body,
            content_type,
            staging,
            publisher,
            id_factory=id_factory,
        )
    except RequestError as error:
        return HttpResponse(
            error.status_code,
            {"error": {"code": error.code, "message": error.message}},
        )

    return HttpResponse(202, {"id": reading_id})