import pytest

from review_replier import cli
from review_replier import reply as rr
from review_replier.reply import Review

CAFE, SALON, PRAXIS = (rr.load_profiles()[k] for k in ("cafe-linde", "studio-mira", "praxis-berger"))
SLOW = Review(author="Jonas Weber", rating=2, text="Das Essen war kalt und wir haben ewig gewartet.")


@pytest.mark.parametrize("text,expected", [
    ("Nach dem Brunch bin ich krank geworden und musste mich übergeben.", True),
    ("I had an allergic reaction to the cake even though I asked about nuts.", True),
    ("The waiter made racist comments to my friend.", True),
    ("Mein Anwalt wird sich melden.", True),
    ("Saw a cockroach under the table.", True),
    ("Leckerer Kuchen, aber etwas teuer.", False),
    ("Great haircut, will come back!", False),
])
def test_escalation_rule(text, expected):
    assert bool(rr.escalation_reason(text)) == expected


def test_check_flags_each_guardrail():
    review = Review(author="Jonas Weber", rating=2, text=SLOW.text)
    assert rr.check("Sorry for the wait, Jonas! Your team at Café Linde", review, CAFE) == [
        "not in the reviewer's language (de)"]
    bad ="Das tut uns leid, Herr Weber. Als Entschuldigung gibt es einen Gutschein, ruf uns an: 030 1234567."
    problems = rr.check(bad, review, CAFE)
    assert any("offers something" in p for p in problems)
    assert any("contact data" in p and "030 1234567" in p for p in problems)
    assert any("surname" in p for p in problems)
    assert rr.check("Wir haften selbstverständlich für den Schaden.", review, CAFE)[0].startswith("uses liability")


def test_allowed_offer_passes_and_confidential_breach_is_caught():
    cut = Review(author="Sam", rating=3, text="The cut is a bit uneven on the left side.")
    ok = "So sorry Sam! Pop back in within 14 days and we'll fix it for free. Mira & team"
    assert rr.check(ok, cut, SALON) == []
    pain = Review(rating=1, text="Die Behandlung war schmerzhaft und niemand hat mir zugehört.")
    leak = "Es tut uns leid, dass Ihre Behandlung schmerzhaft war. Ihr Praxisteam Dr. Berger"
    assert any("patient relationship" in p for p in rr.check(leak, pain, PRAXIS))
    safe = ("Vielen Dank für Ihre Rückmeldung. Bitte melden Sie sich bei uns unter praxis@dr-berger.example, "
            "damit wir Ihr Anliegen persönlich besprechen können. Ihr Praxisteam Dr. Berger")
    assert rr.check(safe, pain, PRAXIS) == []


@pytest.mark.parametrize("text,default,expected", [
    ("Super lecker, gerne wieder!", "en", "de"),  # short German review without function words
    ("Mega Brötchen", "en", "de"),  # umlaut
    ("Lovely staff, will be back", "de", "en"),
    ("Top! 👍", "de", "de"), ("Top! 👍", "en", "en"),  # no signal: business language
])
def test_detect_lang(text, default, expected):
    assert rr.detect_lang(text, default) == expected


def test_template_respects_formality_and_language():
    assert "deine" in rr.template(Review(author="Jonas", rating=5, text="Super lecker, gerne wieder!"), CAFE, "t").reply
    formal = rr.template(Review(author="Petra Klein", rating=2, text="Die Wartezeit war sehr lang."), PRAXIS, "t").reply
    assert "Sie" in formal and "Petra" not in formal and formal.endswith("Ihr Praxisteam Dr. Berger")
    assert rr.template(Review(rating=5, text="Great place, lovely staff"), SALON, "t").reply.startswith("Thank you")


def fake(monkeypatch, *outs):
    """Simulated Claude: returns the given outputs in order (first attempt, retry)."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
    calls = []

    def llm(review, biz, key, use_examples, history=None, problems=None):
        calls.append(problems)
        return outs[len(calls) - 1], ["history"]

    monkeypatch.setattr(rr, "_llm", llm)
    return calls


def out(text="", escalate=False, reason=""):
    return {"language": "de", "escalate": escalate, "escalation_reason": reason, "reply": text, "points_addressed": []}


GOOD = "Danke für dein ehrliches Feedback! Kaltes Essen und lange Wartezeit, das tut uns leid. Dein Team vom Café Linde"


def test_ready_on_first_try(monkeypatch):
    calls = fake(monkeypatch, out(GOOD))
    d = rr.reply(SLOW, CAFE)
    assert (d.status, d.reply, d.first_issues) == ("ready", GOOD, []) and calls == [None]


def test_retry_fixes_a_promise(monkeypatch):
    calls = fake(monkeypatch, out("Das tut uns leid, wir schenken dir einen Gutschein! Dein Team vom Café Linde"), out(GOOD))
    d = rr.reply(SLOW, CAFE)
    assert d.status == "ready" and d.first_issues and "Gutschein" in calls[1][0]


def test_still_broken_after_retry_needs_review(monkeypatch):
    bad = "Das tut uns leid, wir erstatten dir den vollen Betrag. Dein Team vom Café Linde"
    fake(monkeypatch, out(bad), out(bad))
    d = rr.reply(SLOW, CAFE)
    assert d.status == "needs review" and any("offers something" in i for i in d.issues)


def test_model_escalation_and_rule_backup(monkeypatch):
    fake(monkeypatch, out(escalate=True, reason="reports a hygiene problem"))
    assert rr.reply(SLOW, CAFE).status == "escalate"
    sick = Review(rating=1, text="Nach dem Brunch bin ich krank geworden.")
    fake(monkeypatch, out(GOOD))  # model misses it, the rule does not
    d = rr.reply(sick, CAFE)
    assert d.status == "escalate" and d.reason.startswith("rule: mentions 'krank geworden'")


def test_api_error_and_offline(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")

    def boom(*a, **k):
        raise RuntimeError("overloaded")

    monkeypatch.setattr(rr, "_llm", boom)
    d = rr.reply(SLOW, CAFE)
    assert (d.status, d.source, d.error) == ("template", "template (API error)", "RuntimeError: overloaded")
    monkeypatch.delenv("ANTHROPIC_API_KEY")
    assert rr.reply(Review(rating=1, text="Saw mice in the kitchen!"), CAFE).status == "escalate"


def test_cli(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    f = tmp_path / "r.csv"
    f.write_text("author,rating,text\nAnna,5,Bester Kuchen!\nTom,1,I got food poisoning after lunch.\n")
    cli.main([str(f), "--business", "cafe-linde"])
    out = capsys.readouterr().out
    assert "[TEMPLATE] 5* Anna" in out and "[HUMAN] 1* Tom" in out and "Answer personally: mentions 'poisoning'" in out
