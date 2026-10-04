"""Byte-range support on the audio endpoint.

Safari and iOS refuse to play an <audio> source that does not advertise
Accept-Ranges and answer 206, so these cover the range contract rather than
the clip contents. S3 is stubbed: the route is meant to forward the range and
mirror the reply, not slice bytes itself.
"""

import re

import pytest
from botocore.exceptions import ClientError

import app.audio as audio

KEY = "audio/nb/abc123.wav"
DATA = bytes(range(256)) * 4  # 1024 bytes, every value distinguishable


def _error(code: str) -> ClientError:
    return ClientError({"Error": {"Code": code}}, "GetObject")


class _Body:
    def __init__(self, data: bytes) -> None:
        self.data = data

    def iter_chunks(self):
        for i in range(0, len(self.data), 64):
            yield self.data[i : i + 64]


class _FakeS3:
    """Minimal S3 with the range semantics boto3 gives us."""

    def head_object(self, Bucket: str, Key: str):
        if Key != KEY:
            raise _error("NoSuchKey")
        return {"ContentLength": len(DATA)}

    def get_object(self, Bucket: str, Key: str, Range: str | None = None):
        if Key != KEY:
            raise _error("NoSuchKey")
        if Range is None:
            return {"ContentLength": len(DATA), "Body": _Body(DATA)}
        start_s, end_s = re.match(r"bytes=(\d*)-(\d*)", Range).groups()
        n = len(DATA)
        if not start_s:  # suffix range: the last N bytes
            suffix = int(end_s)
            if suffix == 0:
                raise _error("InvalidRange")
            start, end = max(0, n - suffix), n - 1
        else:
            start = int(start_s)
            if start >= n:
                raise _error("InvalidRange")
            end = min(int(end_s), n - 1) if end_s else n - 1
        chunk = DATA[start : end + 1]
        return {
            "ContentLength": len(chunk),
            "ContentRange": f"bytes {start}-{end}/{n}",
            "Body": _Body(chunk),
        }


@pytest.fixture(autouse=True)
def _stub_s3(monkeypatch):
    # app.audio imported the symbol directly, so patch it there.
    monkeypatch.setattr(audio, "_s3_client", lambda **kw: _FakeS3())


async def test_full_request_advertises_ranges_and_length(client):
    resp = await client.get("/api/v1/audio/nb/abc123.wav")
    assert resp.status_code == 200
    assert resp.headers["accept-ranges"] == "bytes"
    assert resp.headers["content-length"] == str(len(DATA))
    assert resp.content == DATA


async def test_range_returns_206_with_content_range(client):
    resp = await client.get("/api/v1/audio/nb/abc123.wav", headers={"Range": "bytes=0-99"})
    assert resp.status_code == 206
    assert resp.headers["content-range"] == f"bytes 0-99/{len(DATA)}"
    assert resp.headers["content-length"] == "100"
    assert resp.content == DATA[:100]


async def test_open_ended_range_runs_to_the_end(client):
    resp = await client.get("/api/v1/audio/nb/abc123.wav", headers={"Range": "bytes=1000-"})
    assert resp.status_code == 206
    assert resp.headers["content-range"] == f"bytes 1000-1023/{len(DATA)}"
    assert resp.content == DATA[1000:]


async def test_suffix_range_returns_the_tail(client):
    resp = await client.get("/api/v1/audio/nb/abc123.wav", headers={"Range": "bytes=-50"})
    assert resp.status_code == 206
    assert resp.content == DATA[-50:]


async def test_unsatisfiable_range_is_416_with_the_real_length(client):
    resp = await client.get("/api/v1/audio/nb/abc123.wav", headers={"Range": "bytes=99999-"})
    assert resp.status_code == 416
    assert resp.headers["content-range"] == f"bytes */{len(DATA)}"


async def test_malformed_range_is_ignored_and_serves_the_whole_clip(client):
    # A server may always ignore Range; better than failing the playback.
    for bad in ("bytes=abc", "items=0-10", "bytes=0-10, 20-30", "bytes=-"):
        resp = await client.get("/api/v1/audio/nb/abc123.wav", headers={"Range": bad})
        assert resp.status_code == 200, bad
        assert resp.content == DATA


async def test_missing_clip_is_404(client):
    resp = await client.get("/api/v1/audio/nb/doesnotexist.wav")
    assert resp.status_code == 404


async def test_unreachable_store_is_503_not_404(client, monkeypatch):
    """A dead object store must not look like missing content.

    Collapsing both into 404 is what let a stopped MinIO masquerade as
    ungenerated clips, with the UI quietly substituting a browser voice.
    """
    from botocore.exceptions import EndpointConnectionError

    class _Unreachable:
        def get_object(self, **kw):
            raise EndpointConnectionError(endpoint_url="http://localhost:9000")

    monkeypatch.setattr(audio, "_s3_client", lambda **kw: _Unreachable())
    resp = await client.get("/api/v1/audio/nb/abc123.wav")
    assert resp.status_code == 503
    assert "storage" in resp.json()["detail"].lower()


async def test_store_error_other_than_missing_is_503(client, monkeypatch):
    class _Broken:
        def get_object(self, **kw):
            raise _error("InternalError")

    monkeypatch.setattr(audio, "_s3_client", lambda **kw: _Broken())
    resp = await client.get("/api/v1/audio/nb/abc123.wav")
    assert resp.status_code == 503
