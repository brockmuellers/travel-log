from embedding_service.main import handle_embed, encode


def test_handle_embed_valid() -> None:
    status, resp = handle_embed(b'{"inputs": "hello world"}')
    assert status == 200
    assert isinstance(resp, list) and len(resp) == 1
    assert len(resp[0]) == 384


def test_handle_embed_missing_field() -> None:
    status, resp = handle_embed(b'{"text": "hello"}')
    assert status == 400
    assert resp == {"error": "missing field: inputs"}


def test_handle_embed_invalid_json() -> None:
    status, resp = handle_embed(b"not json")
    assert status == 400
    assert resp == {"error": "invalid JSON"}


def test_handle_embed_empty_inputs() -> None:
    status, resp = handle_embed(b'{"inputs": ""}')
    assert status == 200
    assert resp == [[0.0] * 384]


def test_encode_dimension() -> None:
    assert len(encode("some travel query")) == 384
