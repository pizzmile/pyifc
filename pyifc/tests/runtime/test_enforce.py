import pytest

from pyifc.runtime.enforce import CapabilityError, restrict


class FakeBucket:
    def __init__(self):
        self.objects = {}

    def put(self, key, body, content_type):
        self.objects[key] = (body, content_type)

    def get(self, key):
        return self.objects[key]

    def list(self, prefix=""):
        return sorted(k for k in self.objects if k.startswith(prefix))

    def internal(self):
        return "secret"


def test_disallowed_method_raises_capability_error_naming_component_and_capability():
    bucket = restrict(FakeBucket(), "object_store", frozenset({"read"}), component_id="query")

    with pytest.raises(CapabilityError) as raised:
        bucket.put("k", b"v", "application/json")

    assert "query" in str(raised.value)
    assert "write" in str(raised.value)
    assert isinstance(raised.value, PermissionError)


def test_allowed_method_is_forwarded():
    inner = FakeBucket()
    inner.objects["k"] = (b"v", "text/plain")
    bucket = restrict(inner, "object_store", frozenset({"read"}), component_id="query")

    assert bucket.get("k") == (b"v", "text/plain")


def test_disallowed_call_does_not_reach_the_adapter():
    inner = FakeBucket()
    bucket = restrict(inner, "object_store", frozenset({"read"}), component_id="query")

    with pytest.raises(CapabilityError):
        bucket.put("k", b"v", "application/json")

    assert inner.objects == {}


def test_attribute_that_is_not_a_resource_method_is_hidden():
    bucket = restrict(FakeBucket(), "object_store", frozenset({"read", "write"}), component_id="c")

    with pytest.raises(AttributeError):
        bucket.internal
    with pytest.raises(AttributeError):
        bucket.objects


def test_method_of_another_kind_is_not_exposed():
    bucket = restrict(FakeBucket(), "object_store", frozenset({"publish"}), component_id="c")

    with pytest.raises(AttributeError):
        bucket.publish


def test_each_capability_gates_its_own_method():
    inner = FakeBucket()
    bucket = restrict(inner, "object_store", frozenset({"write", "list"}), component_id="c")

    bucket.put("a", b"1", "x")
    assert bucket.list() == ["a"]
    with pytest.raises(CapabilityError, match="read"):
        bucket.get("a")


@pytest.mark.parametrize("name", [
    "_adapter", "_allowed", "_kind", "_component_id", "__dict__", "__class__", "__wrapped__",
    "adapter", "allowed",
])
def test_proxy_internals_are_not_reachable(name):
    bucket = restrict(FakeBucket(), "object_store", frozenset({"read"}), component_id="c")

    with pytest.raises(AttributeError):
        getattr(bucket, name)


def test_forwarded_and_denied_callables_do_not_lead_to_the_adapter():
    bucket = restrict(FakeBucket(), "object_store", frozenset({"read"}), component_id="c")

    for method in (bucket.get, bucket.put):
        with pytest.raises(AttributeError):
            method.__self__
        assert not hasattr(method, "__wrapped__")


def test_attributes_cannot_be_set_on_the_proxy():
    bucket = restrict(FakeBucket(), "object_store", frozenset({"read"}), component_id="c")

    with pytest.raises(AttributeError):
        bucket.get = lambda key: None
    with pytest.raises(AttributeError):
        bucket._adapter = None


class FakeMessages:
    def __init__(self):
        self.sent = []

    def publish(self, message):
        self.sent.append(message)

    def receive(self):
        return b"m"


@pytest.mark.parametrize("kind", ["topic", "queue"])
def test_publish_allowed_receive_denied(kind):
    inner = FakeMessages()
    proxy = restrict(inner, kind, frozenset({"publish"}), component_id="sender")

    proxy.publish(b"x")
    assert inner.sent == [b"x"]
    with pytest.raises(CapabilityError) as raised:
        proxy.receive()
    assert "sender" in str(raised.value)
    assert "subscribe" in str(raised.value)


@pytest.mark.parametrize("kind", ["topic", "queue"])
def test_receive_allowed_publish_denied(kind):
    inner = FakeMessages()
    proxy = restrict(inner, kind, frozenset({"subscribe"}), component_id="listener")

    assert proxy.receive() == b"m"
    with pytest.raises(CapabilityError) as raised:
        proxy.publish(b"x")
    assert "listener" in str(raised.value)
    assert "publish" in str(raised.value)
    assert inner.sent == []
