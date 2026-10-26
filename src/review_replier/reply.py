"""Draft a reply to one review in a business's voice. Serious reviews are escalated to a human instead;
every draft is checked by rules, gets one retry with the exact problem, and is only "ready" if it passes."""
import json
import logging
import os
import re
from dataclasses import dataclass, field
from functools import cache
from importlib.resources import files

from pydantic import BaseModel, Field

log = logging.getLogger(__name__)
usage = {"in": 0, "out": 0}  # running token totals, read by evals/run_eval.py
MODEL = os.getenv("REVIEW_REPLIER_MODEL", "claude-opus-5-5")
MAX_CHARS = 900  # our limit for a readable reply; Google allows far more


class Offer(BaseModel):
    text: str
    keywords: list[str]  # words that show a reply is making exactly this offer


class Example(BaseModel):
    review: str
    reply: str


class Business(BaseModel):
    name: str
    kind: str
    lang: str = "de"
    tone: str
    sign_off: str
    contact: str = ""
    allowed_offers: list[Offer] = []
    facts: list[str] = []
    confidential: bool = False  # health care: never confirm someone is a patient or mention treatment
    examples: list[Example] = []


class Review(BaseModel):
    author: str = ""
    rating: int = Field(ge=1, le=5)
    text: str


@dataclass
class Draft:
    review: Review
    status: str  # "ready", "needs review", "escalate" or "template"
    reply: str = ""
    reason: str = ""  # why it was escalated
    issues: list[str] = field(default_factory=list)  # guardrail problems left after the retry
    first_issues: list[str] = field(default_factory=list)  # problems in the first attempt, fixed by the retry
    source: str = "llm"  # "llm", "template" or "template (API error)"
    error: str = ""


@cache
def load_profiles() -> dict[str, Business]:
    raw = json.loads(files("review_replier").joinpath("profiles.json").read_text(encoding="utf-8"))
    return {key: Business(**b) for key, b in raw.items()}


GERMAN = {"der", "die", "das", "und", "ist", "nicht", "wir", "sie", "ich", "war", "sehr", "mit", "für", "auf",
          "ein", "eine", "uns", "danke", "vielen", "dein", "ihr", "ihre", "freuen", "leider", "aber", "es",
          # short reviews often have no function words at all
          "gerne", "gern", "wieder", "lecker", "toll", "gut", "schön", "freundlich", "nett", "essen", "kuchen",
          "kaffee", "bedienung", "preis", "zu", "hier", "immer", "empfehlen", "klasse", "mega", "wartezeit"}
ENGLISH = {"the", "and", "is", "not", "we", "you", "i", "was", "very", "with", "for", "on", "a", "an", "us",
           "thank", "thanks", "your", "our", "but", "it", "so", "great", "they", "to", "lovely", "amazing",
           "nice", "good", "staff", "food", "place", "will", "back", "best", "recommend", "love", "friendly"}


def detect_lang(text: str, default: str = "en") -> str:
    """English or German by common words (umlauts count for German); `default` when there is no signal."""
    lower = text.lower()
    words = re.findall(r"[a-zäöüß]+", lower)
    de = sum(w in GERMAN for w in words) + len(re.findall(r"[äöüß]", lower))
    en = sum(w in ENGLISH for w in words)
    return "de" if de > en else "en" if en > de else default


# Reviews a human must answer: health, safety, discrimination, legal threats. Deliberately broad.
ESCALATE = re.compile(
    r"poison\w*|vergift\w*|food ?borne|sick after|krank (nach|geworden)|(threw|throw) up|erbroch\w*|übergeben"
    r"|allergic reaction|allergische reaktion|anaphyla\w*|hospital|krankenhaus|notaufnahme|emergency room"
    r"|discriminat\w*|diskrimin\w*|rassis\w*|racis\w*|homophob\w*|sexis\w*|harass\w*|belästig\w*"
    r"|lawyer|anwalt|anwältin|sue\b|verklagen|klage einreichen|police|polizei|anzeige erstatt\w*"
    r"|cockroach\w*|kakerlake\w*|\bmice\b|\bmäuse\b|schimmel|mould|mold\b", re.IGNORECASE)
PROMISE = re.compile(
    r"refund\w*|money back|discount\w*|voucher\w*|coupon\w*|complimentary|on the house|compensat\w*|"
    r"free of charge|for free|\ba free\b|erstatt\w*|geld zurück|rabatt\w*|gutschein\w*|gratis|kostenlos\w*|"
    r"umsonst|entschädig\w*|aufs haus", re.IGNORECASE)
LIABILITY = re.compile(r"\bliab(le|ility)\b|negligen\w*|\bhaft(en|et|ung|bar)\b|fahrlässig\w*|our fault|unsere schuld",
                       re.IGNORECASE)
CONFIDENTIAL = re.compile(
    r"ihre[rnm]? (behandlung|termin\w*|zähne|zahn|füllung|krone|wurzel\w*|befund\w*|diagnose)|"
    r"your (treatment|appointment|tooth|teeth|filling|crown|root canal|diagnosis|visit on)|"
    r"\b(als|unsere?r?) patient(in)?\b|as (our|a) patient|patient records|patientenakte", re.IGNORECASE)
CONTACT = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+|\+?\d[\d /-]{6,}\d")  # an email never ends in "."


def escalation_reason(text: str) -> str:
    m = ESCALATE.search(text)
    return f"mentions '{m.group(0)}'" if m else ""


def check(reply: str, review: Review, biz: Business) -> list[str]:
    """Rule-based guardrails on a drafted reply; returns readable problems."""
    problems = []
    if (m := PROMISE.search(reply)) and not any(k in reply.lower() for o in biz.allowed_offers for k in o.keywords):
        problems.append(f"offers something the business does not allow ('{m.group(0)}')")
    if m := LIABILITY.search(reply):
        problems.append(f"uses liability wording ('{m.group(0)}')")
    if biz.confidential and (m := CONFIDENTIAL.search(reply)):
        problems.append(f"may confirm a patient relationship or treatment ('{m.group(0)}')")
    for m in CONTACT.finditer(reply):
        if m.group(0).strip() not in biz.contact:
            problems.append(f"contains contact data that is not the business's ('{m.group(0).strip()}')")
    surname = review.author.split()[-1] if len(review.author.split()) > 1 else ""
    if surname and re.search(rf"\b{re.escape(surname)}\b", reply):
        problems.append(f"uses the reviewer's surname ('{surname}')")
    wanted = detect_lang(review.text, biz.lang)
    if detect_lang(reply, wanted) != wanted:
        problems.append(f"not in the reviewer's language ({wanted})")
    if len(reply) > MAX_CHARS:
        problems.append(f"too long ({len(reply)} characters, max {MAX_CHARS})")
    return problems


def reply(review: Review, biz: Business, api_key: str | None = None, use_examples: bool = True) -> Draft:
    rule = escalation_reason(review.text)
    key = api_key or os.getenv("ANTHROPIC_API_KEY")
    if not key:
        return Draft(review, "escalate", reason=rule, source="template") if rule else template(review, biz, "template")
    try:
        out, messages = _llm(review, biz, key, use_examples)
        if out["escalate"] or rule:
            return Draft(review, "escalate", reason=out["escalation_reason"] or f"rule: {rule}")
        first = check(out["reply"], review, biz)
        if first:  # one retry, told exactly what was wrong
            out, _ = _llm(review, biz, key, use_examples, messages, first)
            if out["escalate"]:
                return Draft(review, "escalate", reason=out["escalation_reason"], first_issues=first)
    except Exception as e:  # API down, bad key, refusal: a safe template the owner can edit
        log.warning("LLM call failed (%s), using template", type(e).__name__)
        return (Draft(review, "escalate", reason=rule, source="template (API error)") if rule
                else template(review, biz, "template (API error)", f"{type(e).__name__}: {e}"[:300]))
    left = check(out["reply"], review, biz)
    return Draft(review, "needs review" if left else "ready", out["reply"], issues=left, first_issues=first)


def template(review: Review, biz: Business, source: str, error: str = "") -> Draft:
    """Rating-based fallback without a model: polite, generic, safe; the owner should personalise it."""
    de = detect_lang(review.text, biz.lang) == "de"
    formal = "'sie'" in biz.tone.lower()  # German "Sie" instead of "du"
    name = review.author.split()[0] if review.author and not formal else ""
    hi = (f"Hallo {name}, " if de else f"Hi {name}, ") if name else ""
    de_text = {
        "good": ("vielen Dank für Ihre freundliche Bewertung. Wir freuen uns sehr darüber.", "vielen Dank für deine "
                 "tolle Bewertung! Wir freuen uns auf deinen nächsten Besuch."),
        "mid": ("vielen Dank für Ihre Rückmeldung. Wir nehmen Ihre Hinweise ernst.", "danke für dein Feedback. Wir "
                "nehmen deine Hinweise mit und arbeiten daran."),
        "bad": (f"es tut uns leid, dass Sie nicht zufrieden waren. Bitte schreiben Sie uns an {biz.contact}, damit "
                "wir Ihr Anliegen klären können.", f"es tut uns leid, dass du nicht zufrieden warst. Schreib uns gern "
                f"an {biz.contact}, damit wir das klären können."),
    }
    en_text = {"good": "thank you so much for your lovely review! We look forward to seeing you again.",
               "mid": "thanks for your feedback. We take your points on board and will work on them.",
               "bad": f"we're sorry your visit didn't meet expectations. Please write to us at {biz.contact} so we "
                      "can make it right."}
    level = "good" if review.rating >= 4 else "mid" if review.rating == 3 else "bad"
    body = de_text[level][0 if formal else 1] if de else en_text[level]
    text = f"{hi}{body if hi else body[0].upper() + body[1:]} {biz.sign_off}"
    return Draft(review, "template", text, source=source, error=error)


SYSTEM = """You draft replies to Google reviews on behalf of a local business, in its own voice.
Rules:
- Reply in the reviewer's language, whatever the business's usual language is.
- Address the specific points of the review; never be defensive or argue.
- Only use facts from the business profile. Never invent opening hours, menu items, prices or policies.
- Offer nothing (refunds, discounts, vouchers, freebies) except the business's allowed offers, word for word.
- Never admit legal liability. Never repeat personal data from the review (surnames, phone numbers, emails).
- For confidential businesses (health care): never confirm that the reviewer is a patient or mention any
  treatment, appointment or medical detail; invite them to get in touch instead.
- Escalate instead of replying (escalate=true, reply="") when the review reports illness, injury, an allergic
  reaction, a hygiene problem such as pests, discrimination or harassment, or threatens legal action or police.
- End with the business's sign-off. Keep it short: 2 to 4 sentences."""

SCHEMA = {
    "type": "object",
    "properties": {"language": {"type": "string"}, "escalate": {"type": "boolean"},
                   "escalation_reason": {"type": "string"}, "reply": {"type": "string"},
                   "points_addressed": {"type": "array", "items": {"type": "string"}}},
    "required": ["language", "escalate", "escalation_reason", "reply", "points_addressed"],
    "additionalProperties": False,
}


def _prompt(review: Review, biz: Business, use_examples: bool) -> str:
    offers = "; ".join(o.text for o in biz.allowed_offers) or "none"
    parts = [f"Business: {biz.name} ({biz.kind}). Tone: {biz.tone}. Sign-off: {biz.sign_off}. "
             f"Contact: {biz.contact or 'none'}. Allowed offers: {offers}. Confidential: {biz.confidential}.",
             "Facts: " + ("; ".join(biz.facts) or "none")]
    if use_examples and biz.examples:
        parts.append("Earlier replies by the business, match their voice:\n"
                     + "\n".join(f"Review: {e.review}\nReply: {e.reply}" for e in biz.examples))
    parts.append(f"Review to answer ({review.rating} stars, by {review.author or 'anonymous'}):\n{review.text}")
    return "\n\n".join(parts)


def _llm(review: Review, biz: Business, api_key: str, use_examples: bool,
         history: list | None = None, problems: list[str] | None = None) -> tuple[dict, list]:
    import anthropic

    messages = history or [{"role": "user", "content": _prompt(review, biz, use_examples)}]
    if problems:
        messages = messages + [{"role": "user", "content": "Your reply has these problems: " + "; ".join(problems)
                                + ". Rewrite it so none of them remain; keep everything else."}]
    resp = anthropic.Anthropic(api_key=api_key).beta.messages.create(
        model=MODEL,
        max_tokens=16000,
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",  # if a safety classifier declines, the API retries on a fallback model
        system=SYSTEM,
        output_config={"effort": "medium", "format": {"type": "json_schema", "schema": SCHEMA}},
        messages=messages,
    )
    usage["in"] += resp.usage.input_tokens
    usage["out"] += resp.usage.output_tokens
    if resp.stop_reason == "refusal":
        raise RuntimeError("the model declined the request")
    out = json.loads(next(b.text for b in resp.content if b.type == "text"))
    return out, messages + [{"role": "assistant", "content": resp.content}]  # thinking blocks passed back unchanged
