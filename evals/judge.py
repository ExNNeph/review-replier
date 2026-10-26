"""LLM-as-judge: Claude scores a reply 1-5 on specificity, the business's voice and tone, against a fixed rubric.
calibrate() first checks the judge on hand-written good / generic / defensive replies with known expectations."""
import json
from pathlib import Path

from review_replier import reply as rr

HERE = Path(__file__).parent
RUBRIC = """You grade a business's reply to a Google review. Score each criterion from 1 (bad) to 5 (excellent):
- specific: addresses the reviewer's concrete points instead of generic thanks or apologies
- voice: matches the business's tone and its earlier replies (formality, length, warmth, sign-off)
- tone: polite, empathetic and never defensive, sarcastic or blaming the reviewer
- overall: how ready the reply is to be posted as it is
Be strict: a generic reply that could answer any review gets at most 2 for specific."""
SCHEMA = {"type": "object", "additionalProperties": False,
          "required": ["specific", "voice", "tone", "overall", "reason"],
          "properties": {**{k: {"type": "integer"} for k in ("specific", "voice", "tone", "overall")},
                         "reason": {"type": "string"}}}


def judge(review_text: str, rating: int, reply_text: str, biz: rr.Business) -> dict:
    import anthropic

    examples = "\n".join(f"Review: {e.review}\nReply: {e.reply}" for e in biz.examples)
    resp = anthropic.Anthropic().beta.messages.create(
        model=rr.MODEL,
        max_tokens=16000,
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
        system=RUBRIC,
        output_config={"effort": "low", "format": {"type": "json_schema", "schema": SCHEMA}},
        messages=[{"role": "user", "content": f"Business: {biz.name} ({biz.kind}). Tone: {biz.tone}.\n"
                   f"Earlier replies by the business:\n{examples}\n\nReview ({rating} stars):\n{review_text}\n\n"
                   f"Reply to grade:\n{reply_text}"}],
    )
    rr.usage["in"] += resp.usage.input_tokens
    rr.usage["out"] += resp.usage.output_tokens
    return json.loads(next(b.text for b in resp.content if b.type == "text"))


def meets(score: int, rule: str) -> bool:
    return score >= int(rule[2:]) if rule.startswith(">=") else score <= int(rule[2:])


def calibrate() -> list[tuple[str, str, dict, bool]]:
    """(business, kind, scores, passed) for every hand-written calibration reply."""
    profiles, out = rr.load_profiles(), []
    for case in json.loads((HERE / "calibration.json").read_text(encoding="utf-8")):
        biz = profiles[case["business"]]
        for r in case["replies"]:
            scores = judge(case["review"], case["rating"], r["text"], biz)
            out.append((case["business"], r["kind"], scores, all(meets(scores[k], v) for k, v in r["expect"].items())))
    return out
