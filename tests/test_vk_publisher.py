import pytest
from invest_blog.vk_publisher import VKConfig, VKPublisher, VKPublishError


class Response:
    def __init__(self, result):
        self.result = result
    def raise_for_status(self):
        pass
    def json(self):
        return self.result


class Session:
    def __init__(self, result):
        self.result = result
        self.payload = None
    def post(self, url, data, timeout):
        self.payload = (url, data, timeout)
        return Response(self.result)


def test_vk_post():
    s = Session({"response": {"post_id": 123}})
    url = VKPublisher(VKConfig(456, "secret"), s).publish("Привет", guid="mytest")
    assert url == "https://vk.com/wall-456_123"
    assert s.payload[1]["owner_id"] == -456
    assert s.payload[1]["from_group"] == 1
    assert s.payload[1]["guid"] == "mytest"


def test_vk_error_does_not_leak_secret():
    s = Session({"error": {"error_code": 15, "error_msg": "secret"}})
    with pytest.raises(VKPublishError) as e:
        VKPublisher(VKConfig(456, "secret"), s).publish("Тест", guid="test")
    assert "secret" not in str(e.value)


def test_vk_requires_message():
    with pytest.raises(ValueError):
        VKPublisher(VKConfig(456, "secret")).publish(" ", guid="test")


def test_vk_config(monkeypatch):
    monkeypatch.setenv("VK_GROUP_ID", "12345")
    monkeypatch.setenv("VK_ACCESS_TOKEN", "test")
    monkeypatch.setenv("VK_AUTO_PUBLISH", "false")
    assert VKConfig.from_env() == VKConfig(12345, "test", False)
