"""Local web demo, started with `review-replier demo`."""
import json
import os
from pathlib import Path

import streamlit as st

from review_replier.reply import MODEL, Review, load_profiles, reply

SAMPLES = Path(__file__).resolve().parents[2] / "evals" / "reviews.json"  # present when run from a clone
BADGE = {"ready": "✅ Ready to post", "needs review": "⚠️ Check before posting", "escalate": "🚨 Answer personally",
         "template": "📝 Template (edit before posting)"}

st.set_page_config(page_title="Review Replier demo", page_icon="💬", layout="wide")
st.title("💬 Review Replier")
st.caption("Drafts replies to Google reviews in the business's own voice. Serious reviews are flagged for a human, "
           "and every draft is checked for promises, personal data and the right language before it is marked ready.")

profiles = load_profiles()
with st.sidebar:
    key_name = st.selectbox("Business", list(profiles), format_func=lambda k: profiles[k].name)
    biz = profiles[key_name]
    with st.expander("Profile", expanded=False):
        st.markdown(f"**{biz.kind}** · tone: {biz.tone}\n\nSign-off: *{biz.sign_off}*\n\n"
                    f"Allowed offers: {', '.join(o.text for o in biz.allowed_offers) or 'none'}\n\n"
                    f"Confidential (health care): {'yes' if biz.confidential else 'no'}")
        for e in biz.examples:
            st.caption(f"Review: {e.review}\n\nReply: {e.reply}")
    use_examples = st.toggle("Use the business's earlier replies (few-shot)", value=True,
                             help="Off shows how replies sound without examples of the business's voice.")
    api_key = st.text_input("Anthropic API key (optional)", type="password",
                            help="Used only for your requests in this local session, never stored.")
    if api_key or os.getenv("ANTHROPIC_API_KEY"):
        where = "sidebar" if api_key else "ANTHROPIC_API_KEY environment variable"
        st.success(f"🔑 Key set ({where}): Claude ({MODEL}) drafts the replies.")
    else:
        st.info("No key: rating-based templates, and only the keyword rule decides what to escalate. "
                "Paste a key and press Enter to let Claude write.")

samples = [r for r in json.loads(SAMPLES.read_text(encoding="utf-8")) if r["business"] == key_name] \
    if SAMPLES.is_file() else []
default = "\n".join(f"{r['rating']} | {r['author']} | {r['text']}" for r in samples[:6])
text = st.text_area("Reviews, one per line: stars | name | text", default, height=200, key=f"reviews-{key_name}")

reviews, bad = [], []
for line in filter(str.strip, text.splitlines()):
    parts = [p.strip() for p in line.split("|", 2)]
    try:
        reviews.append(Review(rating=int(parts[0]), author=parts[1], text=parts[2]))
    except (ValueError, IndexError):
        bad.append(line)
if bad:
    st.warning("Skipped lines that are not `stars | name | text`: " + "; ".join(b[:40] for b in bad))


@st.cache_data(show_spinner="Drafting a reply…")
def cached_reply(review_json: str, business: str, examples: bool, key: str):
    # cached so reruns (clicking, scrolling) never call or pay for the API twice
    return reply(Review.model_validate_json(review_json), load_profiles()[business], key or None, examples)


if st.button(f"Draft {len(reviews)} replies", type="primary", disabled=not reviews):
    st.session_state.drafts = [cached_reply(r.model_dump_json(), key_name, use_examples, api_key) for r in reviews]
    st.session_state.drafts_for = (text, key_name, use_examples, api_key)

drafts = st.session_state.get("drafts")
if not drafts:
    st.info("👆 Edit or paste reviews, then click **Draft replies**.")
    st.stop()
if st.session_state.get("drafts_for") != (text, key_name, use_examples, api_key):
    st.warning("Reviews, business, toggle or key changed since these drafts. Click **Draft replies** again.")

cols = st.columns(4)
for col, status in zip(cols, BADGE):
    col.metric(BADGE[status], sum(d.status == status for d in drafts))

for d in drafts:
    with st.container(border=True):
        st.markdown(f"{'★' * d.review.rating}{'☆' * (5 - d.review.rating)} **{d.review.author or 'anonymous'}**: "
                    f"{d.review.text}")
        st.markdown(f"**{BADGE[d.status]}**")
        if d.status == "escalate":
            st.error(f"No draft. A person should answer this one: {d.reason}")
            continue
        st.code(d.reply, language=None, wrap_lines=True)
        for issue in d.issues:
            st.warning(f"Guardrail: {issue}")
        if d.first_issues and not d.issues:
            st.caption("Fixed in a second attempt: " + "; ".join(d.first_issues))
        if d.error:
            st.caption(f"{d.source}: {d.error}")
