import argparse
import csv
import subprocess
import sys
from pathlib import Path

from .reply import Review, load_profiles, reply

DEMO_APP = Path(__file__).with_name("demo_app.py")
ICON = {"ready": "[READY]", "needs review": "[CHECK]", "escalate": "[HUMAN]", "template": "[TEMPLATE]"}


def demo() -> None:
    """Start the local web demo on http://localhost:8501 and open the browser."""
    try:
        import streamlit  # noqa: F401
    except ImportError:
        raise SystemExit('The demo needs Streamlit: pip install -e ".[demo]"')
    subprocess.run([sys.executable, "-m", "streamlit", "run", str(DEMO_APP), "--server.address", "localhost",
                    "--server.port", "8501", "--browser.gatherUsageStats", "false",
                    "--client.toolbarMode", "minimal",  # hides Streamlit's "Deploy" button
                    "--server.fileWatcherType", "none"])  # no code reloading needed, and no "install Watchdog" hint


def main(argv: list[str] | None = None) -> None:
    argv = sys.argv[1:] if argv is None else argv
    if argv == ["demo"]:
        return demo()
    profiles = load_profiles()
    p = argparse.ArgumentParser(prog="review-replier", description="Draft replies to Google reviews in a business's "
                                "voice. Run `review-replier demo` for the web demo.")
    p.add_argument("reviews", type=Path, help="CSV with the columns author, rating, text")
    p.add_argument("--business", choices=profiles, default="cafe-linde", help="business profile to write as")
    p.add_argument("--no-examples", action="store_true", help="do not show the business's earlier replies to Claude")
    args = p.parse_args(argv)

    biz = profiles[args.business]
    with open(args.reviews, newline="", encoding="utf-8-sig") as f:
        reviews = [Review(author=r.get("author", ""), rating=int(r["rating"]), text=r["text"]) for r in csv.DictReader(f)]
    for r in reviews:
        d = reply(r, biz, use_examples=not args.no_examples)
        print(f"\n{ICON[d.status]} {r.rating}* {r.author or 'anonymous'}: {r.text[:70]}")
        if d.status == "escalate":
            print(f"  Answer personally: {d.reason}")
        else:
            print(f"  {d.reply}")
        for issue in d.issues:
            print(f"  ! {issue}")
        if d.error:
            print(f"  ({d.source}: {d.error})")
