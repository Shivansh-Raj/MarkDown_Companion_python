"""The company AI gateway: token then chat, retry on an expired token, clear errors, and the Settings toggle."""
from __future__ import annotations

import threading

import pytest

from app.ai import gateway as gw
from app.ai.groq import Cancelled, GroqError, stream_chat
from app.utils import dotenv

CFG = gw.GatewayConfig("https://auth.example/api/v1/auth", "https://models.example/maas", "gpt-5.1", "cid", "csecret")


class Fake:
    def __init__(self, *replies):
        self.replies, self.calls = list(replies), []

    def __call__(self, url, body, headers, verify):
        self.calls.append((url, body, headers, verify))
        return self.replies.pop(0)


@pytest.fixture(autouse=True)
def _fresh_token():
    gw.forget_token()


def ok_chat(text="Hi"):
    return 200, {"choices": [{"message": {"content": text}}]}


def test_gets_a_token_then_chats_with_it():
    http = Fake((200, {"token": "T1"}), ok_chat("Hello!"))
    assert gw.chat(CFG, "sys", "usr", http) == "Hello!"
    (auth_url, auth_body, _, _), (chat_url, chat_body, headers, _) = http.calls
    assert auth_url == "https://auth.example/api/v1/auth/generate-token" and auth_body == {"client_id": "cid", "client_secret": "csecret"}
    assert chat_url == "https://models.example/maas/chat/completions" and headers == {"Authorization": "Bearer T1"}
    assert chat_body == {"model": "gpt-5.1", "messages": [{"role": "system", "content": "sys"}, {"role": "user", "content": "usr"}]}
    assert "stream" not in chat_body and "temperature" not in chat_body


def test_the_token_is_reused_and_replaced_once_when_it_expires():
    http = Fake((200, {"token": "T1"}), ok_chat("a"), ok_chat("b"))
    gw.chat(CFG, "s", "u", http)
    gw.chat(CFG, "s", "u", http)
    assert [c[0].endswith("generate-token") for c in http.calls] == [True, False, False], "one token for two requests"
    gw.forget_token()
    http = Fake((200, {"token": "T2"}), (401, {}), (200, {"token": "T3"}), ok_chat("c"))
    assert gw.chat(CFG, "s", "u", http) == "c"
    assert http.calls[-1][2] == {"Authorization": "Bearer T3"}


def test_clear_errors_that_never_contain_the_secret():
    with pytest.raises(gw.GatewayError, match="rejected the client id"):
        gw.chat(CFG, "s", "u", Fake((401, {})))
    gw.forget_token()
    with pytest.raises(gw.GatewayError, match="did not give a token"):
        gw.chat(CFG, "s", "u", Fake((500, {})))
    gw.forget_token()
    with pytest.raises(gw.GatewayError, match="unexpected format") as e:
        gw.chat(CFG, "s", "u", Fake((200, {"token": "T"}), (200, {"nope": 1})))
    assert "csecret" not in str(e.value)
    gw.forget_token()
    with pytest.raises(gw.GatewayError, match=r"failed \(400\)\. bad model"):
        gw.chat(CFG, "s", "u", Fake((200, {"token": "T"}), (400, {"error": {"message": "bad model"}})))
    gw.forget_token()
    with pytest.raises(gw.GatewayError, match="busy or the request was too large"):
        gw.chat(CFG, "s", "u", Fake((200, {"token": "T"}), (429, {})))


def test_a_missing_setup_says_what_is_missing():
    empty = gw.GatewayConfig("", "", "gpt-5.1", "", "")
    with pytest.raises(gw.GatewayError) as e:
        gw.chat(empty, "s", "u", Fake())
    assert "CLIENT_ID and CLIENT_SECRET" in str(e.value) and "authentication URL" in str(e.value) and "model URL" in str(e.value)


def test_certificate_checks_are_on_unless_the_user_turns_them_off():
    http = Fake((200, {"token": "T"}), ok_chat())
    gw.chat(CFG, "s", "u", http)
    assert all(c[3] is True for c in http.calls)
    off = gw.GatewayConfig(CFG.auth_url, CFG.model_url, CFG.model, "cid", "csecret", False)
    gw.forget_token()
    http = Fake((200, {"token": "T"}), ok_chat())
    gw.chat(off, "s", "u", http)
    assert all(c[3] is False for c in http.calls)


def test_streaming_shaped_call_delivers_the_text_once_and_honours_cancel(monkeypatch):
    monkeypatch.setattr(gw, "chat", lambda cfg, s, u, http=None: "full reply")
    gw.configure(lambda: CFG)
    seen = []
    assert gw.stream_chat("s", "u", seen.append) == "full reply" and seen == ["full reply"]
    ev = threading.Event()
    ev.set()
    with pytest.raises(Cancelled):
        gw.stream_chat("s", "u", seen.append, ev)


def test_groq_stream_chat_hands_over_to_the_gateway_for_its_stand_in_key(monkeypatch):
    monkeypatch.setattr(gw, "stream_chat", lambda system, user, on_text, cancel=None: (on_text("via gateway"), "via gateway")[1])
    got = []
    assert stream_chat(gw.GATEWAY_KEY, "s", "u", got.append) == "via gateway" and got == ["via gateway"]


def test_env_file_parsing_and_precedence(tmp_path, monkeypatch):
    assert dotenv.parse('# c\nCLIENT_ID=abc\nclient_secret: "x y"\nexport DAIA_MODEL=gpt-5.1  # note\n\nbad line\n') == {
        "CLIENT_ID": "abc", "CLIENT_SECRET": "x y", "DAIA_MODEL": "gpt-5.1"}
    (tmp_path / ".env").write_text('client_id: "from-file"\nclient_secret: "s"\n', encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MDC_V2_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.delenv("CLIENT_ID", raising=False)
    assert dotenv.load()["CLIENT_ID"] == "from-file"
    monkeypatch.setenv("CLIENT_ID", "from-env")
    assert dotenv.load()["CLIENT_ID"] == "from-env", "a real environment variable wins over the file"


def test_the_settings_toggle_chooses_the_chat_key(qapp, tmp_path):
    from app.ai.services import AiServices
    from app.core.settings_manager import SettingsManager
    from app.services.persistence import Database
    s = SettingsManager(Database(tmp_path / "db.sqlite"))
    ai = AiServices(s)
    assert ai.provider() == "groq" and ai.chat_key() == ai.api_key()
    s.set("ai_provider", "gateway")
    assert ai.chat_key() == gw.GATEWAY_KEY and ai.api_key() != gw.GATEWAY_KEY, "speech-to-text keeps using the Groq key"
    with pytest.raises(ValueError):
        s.set("ai_provider", "other")
    assert gw.load_config(s).model == "gpt-5.1" and gw.load_config(s).verify_tls is True


def test_settings_window_shows_the_company_fields_only_for_that_provider(make_app):
    app = make_app()
    app.show_settings()
    w = app._settings_win
    w.show()
    assert w.gw_auth.isHidden() and w.gw_test.isHidden()
    w.provider.setCurrentIndex(w.provider.findData("gateway"))
    assert not w.gw_auth.isHidden() and not w.gw_test.isHidden() and "Groq" in w.key_msg.text()
    w.gw_model.setText("gpt-5.1-mini")
    w.apply()
    assert app.settings.get("ai_provider") == "gateway" and app.settings.get("gateway_model") == "gpt-5.1-mini"
    assert "client_secret" not in str(app.db._conn.execute("SELECT * FROM settings").fetchall()).lower()
