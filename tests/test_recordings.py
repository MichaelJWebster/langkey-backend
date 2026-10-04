"""Learner voice recordings: upload, list, stream, delete.

S3 is stubbed with an in-memory bucket — these cover ownership, validation and
the round trip, not object-store behaviour.
"""

import pytest
from botocore.exceptions import ClientError

import app.routes.recordings as recordings
from tests.conftest import login

AUDIO = b"\x1aE\xdf\xa3fake-webm-bytes" * 8


class _FakeS3:
    """In-memory stand-in for the bucket, shared across one test."""

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    def put_object(self, Bucket, Key, Body, ContentType=None):
        self.objects[Key] = Body
        return {}

    def get_object(self, Bucket, Key):
        if Key not in self.objects:
            raise ClientError({"Error": {"Code": "NoSuchKey"}}, "GetObject")
        data = self.objects[Key]

        class _Body:
            def read(self_inner):
                return data

        return {"Body": _Body(), "ContentLength": len(data)}

    def delete_object(self, Bucket, Key):
        self.objects.pop(Key, None)
        return {}


@pytest.fixture
def bucket(monkeypatch):
    fake = _FakeS3()
    monkeypatch.setattr(recordings, "_s3_client", lambda **kw: fake)
    return fake


async def _first_item_id(client, headers) -> int:
    queue = (await client.get("/api/v1/reviews/queue", headers=headers)).json()
    return queue["cards"][0]["item"]["id"]


async def _upload(client, headers, item_id, data=AUDIO, content_type="audio/webm;codecs=opus"):
    return await client.post(
        "/api/v1/recordings",
        headers=headers,
        data={"item_id": str(item_id), "duration_ms": "1500"},
        files={"audio": ("take.webm", data, content_type)},
    )


async def test_upload_then_list_and_stream(client, bucket):
    headers = await login(client)
    item_id = await _first_item_id(client, headers)

    resp = await _upload(client, headers, item_id)
    assert resp.status_code == 201, resp.text
    created = resp.json()
    assert created["item_id"] == item_id
    assert created["example_id"] is None
    assert created["size_bytes"] == len(AUDIO)
    assert created["duration_ms"] == 1500
    # Codec parameters are stripped, and the object is scoped to the user.
    assert created["content_type"] == "audio/webm"
    assert len(bucket.objects) == 1
    assert next(iter(bucket.objects)).startswith("recordings/")

    listed = await client.get(f"/api/v1/recordings?item_id={item_id}", headers=headers)
    assert [r["id"] for r in listed.json()] == [created["id"]]

    audio = await client.get(f"/api/v1/recordings/{created['id']}/audio", headers=headers)
    assert audio.status_code == 200
    assert audio.content == AUDIO
    assert audio.headers["content-type"].startswith("audio/webm")


async def test_takes_accumulate_newest_first(client, bucket):
    headers = await login(client)
    item_id = await _first_item_id(client, headers)
    first = (await _upload(client, headers, item_id)).json()
    second = (await _upload(client, headers, item_id)).json()

    listed = (await client.get(f"/api/v1/recordings?item_id={item_id}", headers=headers)).json()
    ids = [r["id"] for r in listed]
    assert set(ids) == {first["id"], second["id"]}, "takes are kept, not overwritten"
    assert len(bucket.objects) == 2


async def test_rejects_unsupported_type_and_empty_body(client, bucket):
    headers = await login(client)
    item_id = await _first_item_id(client, headers)

    bad_type = await _upload(client, headers, item_id, content_type="application/pdf")
    assert bad_type.status_code == 415

    empty = await _upload(client, headers, item_id, data=b"")
    assert empty.status_code == 400
    assert bucket.objects == {}, "nothing is stored when validation fails"


async def test_rejects_unknown_item(client, bucket):
    headers = await login(client)
    resp = await _upload(client, headers, 10_000_000)
    assert resp.status_code == 404
    assert bucket.objects == {}


async def test_delete_removes_row_and_object(client, bucket):
    headers = await login(client)
    item_id = await _first_item_id(client, headers)
    created = (await _upload(client, headers, item_id)).json()

    resp = await client.delete(f"/api/v1/recordings/{created['id']}", headers=headers)
    assert resp.status_code == 204
    assert bucket.objects == {}

    listed = (await client.get(f"/api/v1/recordings?item_id={item_id}", headers=headers)).json()
    assert listed == []


async def test_requires_authentication(client, bucket):
    headers = await login(client)
    item_id = await _first_item_id(client, headers)
    created = (await _upload(client, headers, item_id)).json()

    for resp in (
        await client.get(f"/api/v1/recordings?item_id={item_id}"),
        await client.get(f"/api/v1/recordings/{created['id']}/audio"),
    ):
        assert resp.status_code == 401
