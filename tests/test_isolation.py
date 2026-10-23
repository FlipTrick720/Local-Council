"""Checks that accounts are isolated from each other through the API, that admin routes are closed to normal users,
that sessions end with a password reset or account deletion, the login lockout, upload access and storage limits.

Runs without Ollama, in a temporary data folder (the real data/ and config.json are not touched):
    .venv/bin/python tests/test_isolation.py      (or: pytest tests/)
"""
import asyncio
import io
import json
import os
import shutil
import sys
import tempfile
import warnings
import zipfile
from pathlib import Path

warnings.filterwarnings("ignore")
_tmp = Path(tempfile.mkdtemp(prefix="lc-test-"))
os.environ["COUNCIL_CONFIG"] = str(_tmp / "config.json")
os.environ["COUNCIL_DATA"] = str(_tmp / "data")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient  # noqa: E402

from app import auth, config, judge, main, memory, rag, storage, tools, users  # noqa: E402

PASSWORDS = {"admin": "admin-pw", "alice": "alice-pw", "bob": "bob-pw"}
SECRET_WORD = "Zebrastreifenkoordinate"


class _NoSleep:
    """Replaces asyncio in app.main, so the one-second delay after a wrong password does not slow the test."""
    @staticmethod
    async def sleep(_):
        return None


main.asyncio = _NoSleep
config.update({"ollama": {"url": "http://127.0.0.1:9"}})  # nothing listens there: no test reaches a real model


def client(ip="10.0.0.1") -> TestClient:
    return TestClient(main.app, base_url="http://localhost", client=(ip, 50000))


def login(name, password=None, ip="10.0.0.1") -> TestClient:
    c = client(ip)
    r = c.post("/api/login", json={"username": name, "password": password or PASSWORDS[name]})
    assert r.status_code == 200, r.text
    return c


def setup_accounts():
    users.list_all()  # creates the first account "admin" (without password)
    users.update("admin", password=PASSWORDS["admin"])
    admin = login("admin")
    for name in ("alice", "bob"):
        assert admin.post("/api/users", json={"name": name, "password": PASSWORDS[name]}).status_code == 200
    return admin


admin = setup_accounts()
alice, bob = login("alice"), login("bob")


def alice_data():
    """A chat with an attachment, a knowledge base with a document, and private settings, all of alice."""
    up = alice.post("/api/uploads?name=geheim.txt", content=f"Notiz: {SECRET_WORD}".encode(),
                    headers={"Content-Type": "text/plain"}).json()
    chat = {"title": f"Alices {SECRET_WORD}", "model": "m", "messages": [
        {"role": "user", "content": f"Was ist {SECRET_WORD}?", "attachments": [up]}]}
    assert alice.put("/api/chats/alice-chat-1", json=chat).status_code == 200
    kb = alice.post("/api/rag", json={"name": "Alices Wissen"}).json()
    folder = rag.DATA_DIR / "alice" / kb["id"]  # a document without Ollama: write the index files directly
    collection = {**kb, "docs": [{"id": up["id"], "name": "geheim.txt", "chunks": 1, "pages": None}]}
    (folder / "collection.json").write_text(json.dumps(collection))
    (folder / f"{up['id']}.json").write_text(json.dumps([{"text": SECRET_WORD, "page": None}]))
    alice.put("/api/config", json={"chat": {"system": f"ALICE {SECRET_WORD}"}, "council": {"question": SECRET_WORD}})
    fact = alice.post("/api/memory", json={"text": f"Alice mag {SECRET_WORD}"}).json()
    judge.record("alice", {"chat": "alice-chat-1", "question": SECRET_WORD, "scores": {}})
    return up["id"], kb["id"], fact["id"]


UPLOAD, KB, FACT = alice_data()


def no_secret(response) -> bool:
    return SECRET_WORD not in response.content.decode("utf-8", "replace")


# ---------- Isolation between users ----------

def test_chats_isolated():
    assert alice.get("/api/chats").json()[0]["id"] == "alice-chat-1"
    assert bob.get("/api/chats").json() == []
    for method in ("get", "delete"):
        assert getattr(bob, method)("/api/chats/alice-chat-1").status_code == 404
    assert bob.patch("/api/chats/alice-chat-1", json={"title": "x"}).status_code == 404
    # The same id stored by bob is bob's own chat; alice's stays as it was.
    assert bob.put("/api/chats/alice-chat-1", json={"title": "bobs", "messages": []}).status_code == 200
    assert alice.get("/api/chats/alice-chat-1").json()["title"].startswith("Alices")
    bob.delete("/api/chats/alice-chat-1")
    assert alice.get("/api/chats/alice-chat-1").status_code == 200


def test_search_and_exports_isolated():
    assert alice.get(f"/api/chats/search?q={SECRET_WORD}").json()
    assert bob.get(f"/api/chats/search?q={SECRET_WORD}").json() == []
    for fmt in ("md", "json"):
        assert bob.get(f"/api/chats/alice-chat-1/export?format={fmt}").status_code == 404
    z = zipfile.ZipFile(io.BytesIO(bob.get("/api/chats/export").content))
    assert all(SECRET_WORD not in z.read(n).decode() for n in z.namelist())
    z = zipfile.ZipFile(io.BytesIO(alice.get("/api/chats/export").content))
    assert any(SECRET_WORD in z.read(n).decode() for n in z.namelist())


def test_knowledge_bases_isolated():
    assert [k["id"] for k in alice.get("/api/rag").json()] == [KB]
    assert bob.get("/api/rag").json() == []
    assert bob.delete(f"/api/rag/{KB}").status_code == 404
    assert bob.delete(f"/api/rag/{KB}/documents/{UPLOAD}").status_code == 404
    assert bob.post(f"/api/rag/{KB}/documents", json={"upload_id": UPLOAD}).status_code == 404
    # Asking with alice's knowledge base fails before any model is called.
    r = bob.post("/api/chat", json={"messages": [{"role": "user", "content": "hi"}], "model": "m", "knowledge": KB})
    assert "nicht gefunden" in r.text and no_secret(r)
    assert alice.get(f"/api/rag").json()[0]["docs"]


def test_settings_isolated():
    assert SECRET_WORD in alice.get("/api/config").text
    assert no_secret(bob.get("/api/config"))
    bob.put("/api/config", json={"chat": {"system": "bob"}})
    assert SECRET_WORD in alice.get("/api/config").json()["chat"]["system"]
    assert "auth" not in bob.get("/api/config").json()


def test_memory_and_scores_isolated():
    assert SECRET_WORD in alice.get("/api/memory").text
    assert no_secret(bob.get("/api/memory"))
    assert bob.patch(f"/api/memory/{FACT}", json={"text": "von bob"}).status_code == 400
    bob.delete(f"/api/memory/{FACT}")
    assert SECRET_WORD in alice.get("/api/memory").text
    assert SECRET_WORD in alice.get("/api/judge").text and bob.get("/api/judge").json() == []
    # The memory goes only into alice's prompt.
    assert SECRET_WORD in memory.system_block("alice") and SECRET_WORD not in memory.system_block("bob")


def test_memory_only_proposed_never_stored():
    async def answer():
        yield {"type": "done", "content": "Gern.\n<<remember: Nutzt Arch Linux>>", "stats": {}}
    before = memory.load("bob")["items"]
    events = asyncio.run(_collect(memory.with_proposals(answer())))
    assert events[0]["content"] == "Gern." and events[1] == {"type": "memory_proposals", "items": ["Nutzt Arch Linux"]}
    assert memory.load("bob")["items"] == before


async def _collect(stream):
    return [e async for e in stream]


def test_uploads_need_ownership():
    assert alice.get(f"/api/uploads/{UPLOAD}").status_code == 200
    assert bob.get(f"/api/uploads/{UPLOAD}").status_code == 404
    assert not storage.can_read("bob", UPLOAD)
    # Bob may not put alice's file into his own knowledge base or chat either.
    bob_kb = bob.post("/api/rag", json={"name": "Bobs"}).json()["id"]
    assert bob.post(f"/api/rag/{bob_kb}/documents", json={"upload_id": UPLOAD}).status_code == 404
    bob.put("/api/chats/bob-chat-1", json={"title": "b", "messages": [{"role": "user", "content": "x",
                                                                       "attachments": [{"id": UPLOAD}]}]})
    assert bob.get(f"/api/uploads/{UPLOAD}").status_code == 404
    # Whoever has the same file uploads it and then owns it too; the answer does not reveal the other owner.
    meta = bob.post("/api/uploads?name=kopie.txt", content=f"Notiz: {SECRET_WORD}".encode()).json()
    assert meta["id"] == UPLOAD and "owners" not in meta
    assert bob.get(f"/api/uploads/{UPLOAD}").status_code == 200


def test_fetch_url_only_allowed_urls():
    result, found = asyncio.run(tools.run("fetch_url", {"url": "https://evil.example/?q=secret"}, {}, set()))
    assert result.startswith("Error") and not found
    assert tools.normalize_url("HTTPS://Example.org/a/#x") in tools.urls_in("siehe http://example.org/a/")


# ---------- Admin routes ----------

ADMIN_ROUTES = [
    ("put", "/api/config", {"ollama": {"url": "http://evil"}}),
    ("put", "/api/config", {"web": {"searxng_url": "http://evil"}}),
    ("put", "/api/config", {"limits": {"user_mb": 0}}),
    ("put", "/api/config", {"speedtest": {"prompt": "x"}}),
    ("get", "/api/users", None),
    ("post", "/api/users", {"name": "mallory", "password": "x", "admin": True}),
    ("patch", "/api/users/bob", {"admin": True}),
    ("delete", "/api/users/alice", None),
    ("post", "/api/speedtest", {"prompt": "x", "models": ["m"]}),
    ("get", "/api/speedtest/history", None),
    ("post", "/api/speedtest/history/prune", {"keep": 0}),
    ("post", "/api/models/unload", {"model": "m"}),
    ("post", "/api/web/test", None),
    ("put", "/api/config", {"code": {"allow_users": True}}),
    ("put", "/api/config", {"queue": {"parallel": 8}}),
    ("post", "/api/code/run", {"code": "print(1)"}),
    ("post", "/api/rag/kb-00000000/folder", {"path": "/etc"}),
    ("get", "/api/benchmark/questions", None),
    ("put", "/api/benchmark/questions", [{"question": "x"}]),
    ("get", "/api/benchmark/runs", None),
    ("post", "/api/benchmark/runs", {"baseline": "m", "members": [{"model": "m"}, {"model": "m"}], "synthesizer": "m", "judge": "m"}),
    ("get", "/api/benchmark/runs/x", None),
    ("post", "/api/benchmark/runs/x/start", None),
    ("post", "/api/benchmark/runs/x/stop", None),
    ("post", "/api/benchmark/runs/x/vote", {"qid": "q", "label": "A"}),
    ("delete", "/api/benchmark/runs/x", None),
]


def test_admin_routes_closed_to_users():
    for method, path, body in ADMIN_ROUTES:
        kwargs = {"json": body} if body is not None else {}
        r = getattr(bob, method)(path, **kwargs)
        assert r.status_code == 403, (method, path, r.status_code)
    assert not users.get("bob")["admin"] and users.get("mallory") is None
    assert config.load()["ollama"]["url"] != "http://evil" and not config.load()["code"]["allow_users"]
    assert admin.get("/api/users").status_code == 200
    admin.put("/api/config", json={"code": {"roots": ["/srv/geheim"]}})
    assert "/srv/geheim" in admin.get("/api/config").text and "/srv/geheim" not in bob.get("/api/config").text


def test_anonymous_gets_nothing():
    anon = client()
    for path in ("/api/chats", "/api/config", "/api/rag", f"/api/uploads/{UPLOAD}", "/api/users", "/api/storage",
                 "/api/memory", "/api/judge", "/api/queue"):
        assert anon.get(path).status_code == 401, path
    forged = client()
    forged.cookies.set(auth.COOKIE, "admin.9999999999.deadbeef")
    assert forged.get("/api/chats").status_code == 401


# ---------- Sessions and login lockout ----------

def test_password_reset_ends_sessions():
    carol_pw = "carol-pw"
    admin.post("/api/users", json={"name": "carol", "password": carol_pw})
    carol = login("carol", carol_pw)
    assert carol.get("/api/chats").status_code == 200
    admin.patch("/api/users/carol", json={"password": "new-pw"})
    assert carol.get("/api/chats").status_code == 401
    carol = login("carol", "new-pw")
    # Changing one's own password keeps this device logged in (new cookie), but ends the others.
    other = login("carol", "new-pw", ip="10.0.0.9")
    assert carol.put("/api/auth/password", json={"current": "new-pw", "password": "newer"}).status_code == 200
    assert carol.get("/api/chats").status_code == 200 and other.get("/api/chats").status_code == 401


def test_deleting_user_ends_sessions():
    admin.post("/api/users", json={"name": "dave", "password": "dave-pw"})
    dave = login("dave", "dave-pw")
    dave.put("/api/chats/dave-chat-1", json={"title": "d", "messages": []})
    assert admin.delete("/api/users/dave").status_code == 200
    assert dave.get("/api/chats").status_code == 401
    # A new account with the same name and password does not revive old cookies or data.
    admin.post("/api/users", json={"name": "dave", "password": "dave-pw"})
    assert dave.get("/api/chats").status_code == 401
    assert login("dave", "dave-pw").get("/api/chats").json() == []


def test_login_lockout_per_ip_and_per_user():
    auth.MAX_FAILURES = 3
    try:
        admin.post("/api/users", json={"name": "erin", "password": "erin-pw"})
        admin.post("/api/users", json={"name": "frank", "password": "frank-pw"})
        attacker = client("10.6.6.6")
        for _ in range(3):
            assert attacker.post("/api/login", json={"username": "erin", "password": "x"}).status_code == 401
        # Per account: even the right password from another address is refused for a while …
        assert client("10.1.1.1").post("/api/login", json={"username": "erin", "password": "erin-pw"}).status_code == 429
        # … per address: this address may not try other accounts either …
        assert attacker.post("/api/login", json={"username": "frank", "password": "frank-pw"}).status_code == 429
        # … but other people elsewhere are not affected.
        assert client("10.2.2.2").post("/api/login", json={"username": "frank", "password": "frank-pw"}).status_code == 200
    finally:
        auth.MAX_FAILURES = 10
        auth._failures.clear()


# ---------- Storage limits ----------

def test_storage_limits():
    admin.put("/api/config", json={"limits": {"user_mb": 1, "total_mb": 0}})
    try:
        big = os.urandom(600 * 1024).hex()[:600 * 1024].encode()
        assert bob.post("/api/uploads?name=a.txt", content=big).status_code == 200
        r = bob.post("/api/uploads?name=b.txt", content=big[::-1])
        assert r.status_code == 413 and "Speicherplatz" in r.json()["detail"]
        assert alice.post("/api/uploads?name=c.txt", content=big[::-1]).status_code == 200  # alice has her own limit
        used = bob.get("/api/storage").json()
        assert used["limit"] == 2**20 and used["used"] >= 600 * 1024
        admin.put("/api/config", json={"limits": {"user_mb": 0, "total_mb": 1}})
        r = admin.post("/api/uploads?name=d.txt", content=os.urandom(300 * 1024).hex().encode())
        assert r.status_code == 413 and "Speicher der App" in r.json()["detail"]
    finally:
        admin.put("/api/config", json={"limits": {"user_mb": 1024, "total_mb": 20480}})


if __name__ == "__main__":
    failed = 0
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"ok    {name}")
            except Exception as e:  # noqa: BLE001
                failed += 1
                print(f"FAIL  {name}: {type(e).__name__} {e}")
    if failed:
        print(f"\n{failed} fehlgeschlagen (Testdaten bleiben zum Nachsehen in {_tmp})")
        sys.exit(1)
    shutil.rmtree(_tmp)
    print("\nAlles bestanden")
