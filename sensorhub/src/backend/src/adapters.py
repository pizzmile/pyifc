from collections import deque


SUPPORTED_KINDS = ("speed", "line_voltage")


class InMemoryObjectStore:
    def __init__(self):
        self._objects = {}

    def put(self, key: str, body: bytes, content_type: str) -> None:
        self._objects[key] = (body, content_type)

    def get(self, key: str) -> tuple[bytes, str]:
        return self._objects[key]

    def list_keys(self) -> list[str]:
        return sorted(self._objects)

    def list(self, prefix: str = "") -> list[str]:
        return [key for key in self.list_keys() if key.startswith(prefix)]


class InMemoryQueues:
    def __init__(self):
        self._queues = {kind: deque() for kind in SUPPORTED_KINDS}

    def publish(self, kind: str, job: dict) -> None:
        self._queue_for(kind).append(job)

    def consume(self, kind: str) -> dict:
        return self._queue_for(kind).popleft()

    def _queue_for(self, kind: str):
        try:
            return self._queues[kind]
        except KeyError:
            raise ValueError(f"Unsupported queue kind: {kind}") from None