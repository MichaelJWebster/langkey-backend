from httpx import AsyncClient

from tests.conftest import login


async def test_health(client: AsyncClient) -> None:
    resp = await client.get("/api/v1/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok", "database": "ready"}


async def test_login_success(client: AsyncClient) -> None:
    headers = await login(client)
    resp = await client.get("/api/v1/auth/me", headers=headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["email"] == "dev@langkey.dev"
    assert body["display_name"] == "Dev Learner"


async def test_login_wrong_password(client: AsyncClient) -> None:
    resp = await client.post(
        "/api/v1/auth/login",
        json={"email": "dev@langkey.dev", "password": "wrong"},
    )
    assert resp.status_code == 401


async def test_hello_requires_auth(client: AsyncClient) -> None:
    resp = await client.get("/api/v1/hello")
    assert resp.status_code == 401


async def test_hello_reads_greeting_from_db(client: AsyncClient) -> None:
    headers = await login(client)
    resp = await client.get("/api/v1/hello", headers=headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["greeting"] == "Hei! Velkommen til LangKey 🇳🇴"
    assert body["display_name"] == "Dev Learner"
