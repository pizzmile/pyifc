import pytest

from pyifc import Bucket, Queue
from pyifc.core.binding import bind


class RecordingBucket:
    def __init__(self):
        self.objects = {}

    def put(self, key, body, content_type):
        self.objects[key] = (body, content_type)

    def get(self, key):
        return self.objects[key]


class RecordingQueue:
    def __init__(self):
        self.messages = []

    def publish(self, message):
        self.messages.append(message)


def test_binding_dispatches_and_restores_unbound_state():
    bucket = Bucket("staging")
    queue = Queue("jobs")
    store = RecordingBucket()
    messages = RecordingQueue()

    with bind({"staging": store, "jobs": messages}):
        bucket.put("raw/1.json", b"{}")
        assert bucket.get("raw/1.json") == (b"{}", "application/json")
        queue.publish(b"job")

    assert store.objects == {"raw/1.json": (b"{}", "application/json")}
    assert messages.messages == [b"job"]
    with pytest.raises(RuntimeError, match="No runtime adapter bound for staging"):
        bucket.put("raw/2.json", b"{}")