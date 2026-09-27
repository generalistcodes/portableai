import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from ollama_client import OllamaClient, OllamaError


def _mock_response(status_code=200, json_data=None, text=""):
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_data or {}
    resp.text = text
    return resp


@patch("ollama_client.requests.get")
def test_is_available_true_on_200(mock_get):
    mock_get.return_value = _mock_response(200)
    client = OllamaClient()
    assert client.is_available() is True


@patch("ollama_client.requests.get")
def test_is_available_false_on_connection_error(mock_get):
    import requests

    mock_get.side_effect = requests.RequestException("connection refused")
    client = OllamaClient()
    assert client.is_available() is False


@patch("ollama_client.requests.get")
def test_list_models_parses_models_key(mock_get):
    mock_get.return_value = _mock_response(200, {"models": [{"name": "llama3.2:3b"}]})
    client = OllamaClient()
    models = client.list_models()
    assert models == [{"name": "llama3.2:3b"}]


@patch("ollama_client.requests.get")
def test_list_models_raises_on_error_status(mock_get):
    mock_get.return_value = _mock_response(500, text="boom")
    client = OllamaClient()
    with pytest.raises(OllamaError, match="500"):
        client.list_models()


@patch("ollama_client.requests.post")
def test_create_model_posts_expected_body(mock_post):
    mock_post.return_value = _mock_response(200)
    client = OllamaClient()
    payload = {"model": "mentor", "from": "llama3.2:3b", "system": "Be terse."}
    client.create_model(payload)

    args, kwargs = mock_post.call_args
    assert kwargs["json"]["model"] == "mentor"
    assert kwargs["json"]["stream"] is False  # client adds this default


@patch("ollama_client.requests.post")
def test_create_model_raises_on_error_status(mock_post):
    mock_post.return_value = _mock_response(400, text="invalid modelfile")
    client = OllamaClient()
    with pytest.raises(OllamaError, match="400"):
        client.create_model({"model": "x", "from": "llama3.2:3b"})


def _stream_response(lines, status_code=200, text=""):
    resp = MagicMock()
    resp.status_code = status_code
    resp.text = text
    resp.iter_lines.return_value = lines
    return resp


@patch("ollama_client.requests.post")
def test_chat_extracts_message_content(mock_post):
    mock_post.return_value = _stream_response([
        '{"message":{"role":"assistant","content":"hello "},"done":false}',
        '{"message":{"role":"assistant","content":"there"},"done":true}',
    ])
    client = OllamaClient()
    reply = client.chat("mentor", [{"role": "user", "content": "hi"}])
    assert reply == "hello there"
    _, kwargs = mock_post.call_args
    assert kwargs["json"]["stream"] is True
    assert kwargs["json"]["keep_alive"] == "30m"
    assert kwargs["stream"] is True


@patch("ollama_client.requests.post")
def test_iter_chat_yields_each_token(mock_post):
    mock_post.return_value = _stream_response([
        '{"message":{"content":"hello "},"done":false}',
        '{"message":{"content":"there"},"done":true}',
    ])
    client = OllamaClient()
    assert list(client.iter_chat("mentor", [{"role": "user", "content": "hi"}])) == [
        "hello ",
        "there",
    ]


@patch("ollama_client.requests.post")
def test_chat_passes_options_through(mock_post):
    mock_post.return_value = _stream_response([
        '{"message":{"content":"ok"},"done":true}',
    ])
    client = OllamaClient()
    client.chat("mentor", [{"role": "user", "content": "hi"}], options={"temperature": 0})

    _, kwargs = mock_post.call_args
    assert kwargs["json"]["options"] == {"temperature": 0}
    assert kwargs["json"]["stream"] is True
    assert kwargs["json"]["keep_alive"] == "30m"


@patch("ollama_client.requests.post")
def test_chat_raises_on_unexpected_shape(mock_post):
    mock_post.return_value = _stream_response(['{"unexpected": "shape"}'])
    client = OllamaClient()
    with pytest.raises(OllamaError, match="unexpected"):
        client.chat("mentor", [{"role": "user", "content": "hi"}])


@patch("ollama_client.requests.post")
def test_chat_raises_on_truncated_json(mock_post):
    from ollama_client import INCOMPLETE_RESPONSE_MESSAGE

    mock_post.return_value = _stream_response(['{"message": {"content": "hel'])
    client = OllamaClient()
    with pytest.raises(OllamaError, match="incomplete or invalid") as exc_info:
        client.chat("mentor", [{"role": "user", "content": "hi"}])
    assert str(exc_info.value) == INCOMPLETE_RESPONSE_MESSAGE


@patch("ollama_client.requests.delete")
def test_delete_model_accepts_404_as_success(mock_delete):
    mock_delete.return_value = _mock_response(404)
    client = OllamaClient()
    client.delete_model("nonexistent")  # should not raise


@patch("ollama_client.requests.post")
def test_pull_model_success(mock_post):
    mock_post.return_value = _mock_response(200, {"status": "success"})
    client = OllamaClient()
    client.pull_model("qwen2.5:0.5b")  # should not raise

    _, kwargs = mock_post.call_args
    assert kwargs["json"] == {"name": "qwen2.5:0.5b", "stream": False}
    assert kwargs["timeout"] == 1800


@patch("ollama_client.requests.post")
def test_pull_model_on_progress_streams_ndjson(mock_post):
    resp = MagicMock()
    resp.status_code = 200
    resp.iter_lines.return_value = [
        b'{"status":"pulling","completed":50,"total":100}',
        b'{"status":"success","completed":100,"total":100}',
    ]
    mock_post.return_value = resp
    ticks = []
    client = OllamaClient()
    client.pull_model("llama3.2:3b", on_progress=lambda done, total: ticks.append((done, total)))
    _, kwargs = mock_post.call_args
    assert kwargs["json"] == {"name": "llama3.2:3b", "stream": True}
    assert kwargs["stream"] is True
    assert ticks == [(50, 100), (100, 100)]


@patch("ollama_client.requests.post")
def test_pull_model_raises_on_error_status(mock_post):
    mock_post.return_value = _mock_response(500, text="server error")
    client = OllamaClient()
    with pytest.raises(OllamaError, match="500"):
        client.pull_model("qwen2.5:0.5b")


@patch("ollama_client.requests.post")
def test_pull_model_raises_when_status_reports_error(mock_post):
    mock_post.return_value = _mock_response(200, {"status": "error: model not found"})
    client = OllamaClient()
    with pytest.raises(OllamaError, match="pull failed"):
        client.pull_model("does-not-exist:9000b")
    assert mock_post.call_count == 1  # non-transient: do not retry


@patch("ollama_client.requests.post")
def test_pull_model_respects_custom_timeout(mock_post):
    mock_post.return_value = _mock_response(200, {"status": "success"})
    client = OllamaClient()
    client.pull_model("qwen2.5:0.5b", timeout=60)
    _, kwargs = mock_post.call_args
    assert kwargs["timeout"] == 60


@patch("ollama_client.time.sleep")
@patch("ollama_client.requests.post")
def test_pull_model_retries_connection_error_then_succeeds(mock_post, mock_sleep):
    import requests

    mock_post.side_effect = [
        requests.ConnectionError("connection timed out"),
        _mock_response(200, {"status": "success"}),
    ]
    statuses = []
    client = OllamaClient()
    client.pull_model(
        "qwen2.5:0.5b",
        on_status=lambda msg, **_kw: statuses.append(msg),
    )

    assert mock_post.call_count == 2
    mock_sleep.assert_called_once_with(2)
    assert statuses == ["Connection issue, retrying (2/3)..."]


@patch("ollama_client.time.sleep")
@patch("ollama_client.requests.post")
def test_pull_model_gives_up_after_retries(mock_post, mock_sleep):
    import requests

    mock_post.side_effect = requests.ConnectionError("connection timed out")
    client = OllamaClient()
    with pytest.raises(OllamaError, match="connection"):
        client.pull_model("qwen2.5:0.5b")
    assert mock_post.call_count == 3
    assert mock_sleep.call_count == 2
    assert mock_sleep.call_args_list[0].args[0] == 2
    assert mock_sleep.call_args_list[1].args[0] == 5
