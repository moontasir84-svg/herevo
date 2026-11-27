"""Accounts (name + PIN, no phone or email) and chat history."""
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def login(name="রিনা চরপাড়া", pin="1234", create=False):
    return client.post("/v1/account", json={"name": name, "pin": pin, "create": create})


def auth(token):
    return {"Authorization": "Bearer " + token}


def test_create_login_and_wrong_pin():
    r = login(create=True)
    assert r.status_code == 200 and r.json()["name"] == "রিনা চরপাড়া" and r.json()["sid"].startswith("acct-")
    assert login(create=True).status_code == 409                     # name taken
    assert login(name="  রিনা   চরপাড়া ").status_code == 200        # spaces and case don't matter
    assert login(pin="9999").status_code == 401
    assert login(pin="12").status_code == 422                        # a PIN is 4-6 digits
    me = client.get("/v1/account", headers=auth(login().json()["token"])).json()
    assert me["name"] == "রিনা চরপাড়া"


def test_login_tries_are_limited():
    login(name="Amina K", create=True)
    codes = [login(name="Amina K", pin="0000").status_code for _ in range(9)]
    assert codes[-1] == 429


def test_chat_history_is_private_and_deletable():
    a = login(name="Salma", create=True).json()["token"]
    b = login(name="Nasrin", create=True).json()["token"]
    chat = "chat-0001-abcdef"
    msgs = {"messages": [{"role": "her", "text": "আমার জমির ভাগ কত?"}, {"role": "herevo", "text": "আপনার ভাগ ১/৮।", "extra": {"steps": ["নামজারি"]}}]}
    assert client.post(f"/v1/chats/{chat}", json=msgs, headers=auth(a)).json() == {"ok": True}
    client.post(f"/v1/chats/{chat}", json={"messages": [{"role": "her", "text": "ধন্যবাদ"}]}, headers=auth(a))
    lst = client.get("/v1/chats", headers=auth(a)).json()["chats"]
    assert [c["title"] for c in lst] == ["আমার জমির ভাগ কত?"]
    c = client.get(f"/v1/chats/{chat}", headers=auth(a)).json()
    assert [m["role"] for m in c["messages"]] == ["her", "herevo", "her"] and c["messages"][1]["extra"] == {"steps": ["নামজারি"]}
    assert client.get(f"/v1/chats/{chat}", headers=auth(b)).status_code == 404            # someone else's chat
    assert client.post(f"/v1/chats/{chat}", json=msgs, headers=auth(b)).status_code == 404
    assert client.get("/v1/chats").status_code == 401
    client.delete(f"/v1/chats/{chat}", headers=auth(a))
    assert client.get("/v1/chats", headers=auth(a)).json()["chats"] == []


def test_logout_and_delete_account():
    t = login(name="Lucia", create=True).json()["token"]
    client.post("/v1/account/logout", headers=auth(t))
    assert client.get("/v1/account", headers=auth(t)).status_code == 401
    t = login(name="Lucia").json()["token"]
    client.post("/v1/chats/chat-0002-abcdef", json={"messages": [{"role": "her", "text": "hola"}]}, headers=auth(t))
    assert client.delete("/v1/account", headers=auth(t)).json() == {"ok": True}
    assert login(name="Lucia").status_code == 401
