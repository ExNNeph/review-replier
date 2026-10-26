"""Compare three ways of replying on 24 labeled reviews. Usage: python evals/run_eval.py  (needs ANTHROPIC_API_KEY)

Strategies: template (no model), zero-shot (Claude without the business's earlier replies) and few-shot (with them).
1. The judge is calibrated first; if it fails its calibration, the judge scores are printed but marked untrusted.
2. Per strategy: escalation precision/recall, language, point coverage, guardrail problems (first attempt / left
   after the retry), and the judge's average scores on the replies that were not escalated.
"""
import json
import os
import sys
from collections import Counter, defaultdict
from pathlib import Path

from judge import calibrate, judge
from review_replier import reply as rr

HERE = Path(__file__).parent
REVIEWS = json.loads((HERE / "reviews.json").read_text(encoding="utf-8"))
STRATEGIES = ["template", "zero-shot", "few-shot"]


def covers(text: str, point: str) -> bool:
    return any(alt in text.lower() for alt in point.split("|"))


def run(strategy: str, case: dict) -> rr.Draft:
    biz, review = rr.load_profiles()[case["business"]], rr.Review(**{k: case[k] for k in ("author", "rating", "text")})
    if strategy == "template":
        rule = rr.escalation_reason(review.text)
        return rr.Draft(review, "escalate", reason=rule, source="template") if rule else rr.template(review, biz, "template")
    return rr.reply(review, biz, use_examples=strategy == "few-shot")


def main():
    if not os.getenv("ANTHROPIC_API_KEY"):
        raise SystemExit("Set ANTHROPIC_API_KEY to run the eval (the sanity checks run with pytest).")
    print("Judge calibration (hand-written replies with known expectations):")
    cal = calibrate()
    for biz, kind, s, ok in cal:
        print(f"  {'ok  ' if ok else 'FAIL'} {biz:14} {kind:10} specific {s['specific']} voice {s['voice']} "
              f"tone {s['tone']} overall {s['overall']}")
    trusted = all(ok for *_, ok in cal)
    print(f"  judge {'passed' if trusted else 'FAILED'} calibration ({sum(ok for *_, ok in cal)}/{len(cal)})\n")

    stats = {s: Counter() for s in STRATEGIES}
    scores = {s: defaultdict(list) for s in STRATEGIES}
    for case in REVIEWS:
        biz = rr.load_profiles()[case["business"]]
        for strategy in STRATEGIES:
            print(f"  {case['business']:14} {case['author'] or 'anonymous':12} {strategy} …", file=sys.stderr, flush=True)
            d = run(strategy, case)
            c = stats[strategy]
            escalated = d.status == "escalate"
            c["tp"] += escalated and case["escalate"]
            c["fp"] += escalated and not case["escalate"]
            c["fn"] += (not escalated) and case["escalate"]
            if escalated or case["escalate"]:
                if escalated != case["escalate"]:
                    print(f"      escalation {'missed' if case['escalate'] else 'wrong'}: {case['text'][:60]}"
                          + (f" ({d.reason})" if d.reason else ""), file=sys.stderr)
                continue
            c["replies"] += 1
            c["lang_ok"] += rr.detect_lang(d.reply, case["lang"]) == case["lang"]
            c["points"] += sum(covers(d.reply, p) for p in case["points"])
            c["points_total"] += len(case["points"])
            c["first_issues"] += bool(d.first_issues)
            c["left_issues"] += bool(d.issues)
            for issue in d.first_issues + d.issues:
                print(f"      {strategy} guardrail: {issue}", file=sys.stderr)
            s = judge(case["text"], case["rating"], d.reply, biz)
            for k in ("specific", "voice", "tone", "overall"):
                scores[strategy][k].append(s[k])

    print("| strategy | escalation recall | escalation precision | language | points addressed | guardrail hits "
          "(first → after retry) | judge specific | judge voice | judge tone | judge overall |")
    print("|---|---|---|---|---|---|---|---|---|---|")
    for strategy in STRATEGIES:
        c, sc = stats[strategy], scores[strategy]
        avg = {k: sum(v) / len(v) for k, v in sc.items()}
        rec = c["tp"] / (c["tp"] + c["fn"]) if c["tp"] + c["fn"] else 0
        prec = c["tp"] / (c["tp"] + c["fp"]) if c["tp"] + c["fp"] else 0
        print(f"| {strategy} | {rec:.0%} | {prec:.0%} | {c['lang_ok']}/{c['replies']} | {c['points']}/{c['points_total']} | "
              f"{c['first_issues']} → {c['left_issues']} | {avg['specific']:.1f} | {avg['voice']:.1f} | "
              f"{avg['tone']:.1f} | {avg['overall']:.1f} |")
    if not trusted:
        print("\nJudge failed its calibration: treat the judge columns as untrusted.")
    print(f"\nLLM tokens (replies, retries and judge): {rr.usage['in']} in / {rr.usage['out']} out")


if __name__ == "__main__":
    main()
