import builtins

import pytest
from streamlit.testing.v1 import AppTest

from review_replier import cli
from review_replier import reply as rr


@pytest.fixture
def app(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    return AppTest.from_file(str(cli.DEMO_APP), default_timeout=20)  # the file `review-replier demo` starts


def test_demo_prefills_samples_and_drafts_offline(app):
    at = app.run()
    assert not at.exception and at.text_area[0].value.count("\n") == 5  # six sample reviews
    at.button[0].click().run()
    drafts = at.session_state["drafts"]
    assert len(drafts) == 6 and {d.status for d in drafts} == {"template", "escalate"}  # rule catches the illness
    assert any("A person should answer" in e.value for e in at.error)


def test_bad_lines_are_skipped(app):
    at = app.run()
    at.text_area[0].input("5 | Anna | Toll!\nthis is not a review").run()
    assert any("Skipped lines" in w.value for w in at.warning) and at.button[0].label == "Draft 1 replies"


def test_claude_drafts_show_status_and_retry_note(app, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
    calls = []

    def llm(review, biz, key, use_examples, history=None, problems=None):
        calls.append(problems)
        text = ("Danke! Als Entschuldigung ein Gutschein. Dein Team vom Café Linde" if not problems else
                "Danke für dein Feedback, das nehmen wir uns zu Herzen! Dein Team vom Café Linde")
        return {"language": "de", "escalate": False, "escalation_reason": "", "reply": text,
                "points_addressed": []}, []

    monkeypatch.setattr(rr, "_llm", llm)
    at = app.run()
    at.text_area[0].input("2 | Jonas | Kaffee war kalt.").run()
    at.button[0].click().run()
    assert not at.exception and at.session_state["drafts"][0].status == "ready"
    assert any("Fixed in a second attempt" in c.value for c in at.caption)


def test_demo_without_streamlit_gives_install_hint(monkeypatch):
    real = builtins.__import__
    monkeypatch.setattr(builtins, "__import__",
                        lambda n, *a, **k: (_ for _ in ()).throw(ImportError()) if n == "streamlit" else real(n, *a, **k))
    with pytest.raises(SystemExit, match="pip install"):
        cli.demo()
