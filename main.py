"""
main.py - single entry point.

  python main.py                      run the polling agent
  python main.py --once               process the inbox once and exit
  python main.py --dry-run --once     decide only: no send, no labels, no DB writes
  python main.py feedback <sender> spam|not_spam
  python main.py export-token         print GMAIL_TOKEN_JSON_B64 for cloud deploys
  python main.py watch start|stop     manage Gmail push (webhook mode)
"""

import argparse
import signal
import sys
import threading

from src import config
from src.step7_logger import setup_logger

logger = setup_logger()
_stop = threading.Event()


def _handle_stop(signum, _frame):
    logger.info(f"Signal {signum} received - finishing the current email, then stopping.")
    _stop.set()


def run(once: bool, dry_run: bool):
    from src.orchestrator import run_cycle
    from src.step1_auth import get_gmail_service
    from src.step3_state import init_db

    init_db()
    service = get_gmail_service()
    mode = "DRY-RUN " if dry_run else ""
    logger.info(f"{mode}Email Reply Agent started | poll={config.POLL_INTERVAL_SECONDS}s | model={config.GROQ_MODEL}")

    while not _stop.is_set():
        try:
            summary = run_cycle(service, dry_run=dry_run, stop_event=_stop)
            if summary["fetched"]:
                logger.info(f"Cycle done | {summary}")
        except Exception as exc:  # the loop itself must never die
            logger.error(f"Cycle-level error (loop continues): {exc}")
        if once:
            break
        _stop.wait(config.POLL_INTERVAL_SECONDS)   # wakes immediately on SIGTERM
    logger.info("Agent stopped.")


def feedback(sender: str, label: str):
    from src.step3_state import init_db, record_sender_feedback

    init_db()
    spam, not_spam = record_sender_feedback(sender, is_spam=(label == "spam"))
    print(f"Feedback recorded for '{sender.lower()}': spam_votes={spam}, not_spam_votes={not_spam}")


def export_token():
    from src.step1_auth import export_token_b64

    print(export_token_b64())


def watch(command: str):
    from src.step1_auth import get_gmail_service
    from src.step8_watch import expiration_text, start_watch, stop_watch

    service = get_gmail_service()
    if command == "start":
        resp = start_watch(service)
        print(f"Watch active. historyId={resp.get('historyId')} expires={expiration_text(resp)}")
    else:
        stop_watch(service)
        print("Watch stopped.")


def main(argv=None):
    parser = argparse.ArgumentParser(description="Email Reply AI Agent")
    sub = parser.add_subparsers(dest="command")
    parser.add_argument("--once", action="store_true", help="run one cycle and exit")
    parser.add_argument("--dry-run", action="store_true", help="decide only; change nothing")
    fb = sub.add_parser("feedback", help="correct the spam filter for a sender")
    fb.add_argument("sender")
    fb.add_argument("label", choices=["spam", "not_spam"])
    sub.add_parser("export-token", help="print base64 token for GMAIL_TOKEN_JSON_B64")
    wt = sub.add_parser("watch", help="Gmail push notifications")
    wt.add_argument("action", choices=["start", "stop"])
    args = parser.parse_args(argv)

    from src.step1_auth import GmailAuthError

    try:
        if args.command == "feedback":
            feedback(args.sender, args.label)
        elif args.command == "export-token":
            export_token()
        elif args.command == "watch":
            watch(args.action)
        else:
            signal.signal(signal.SIGINT, _handle_stop)
            signal.signal(signal.SIGTERM, _handle_stop)
            run(once=args.once, dry_run=args.dry_run)
    except GmailAuthError as exc:
        logger.error(f"Gmail authentication problem: {exc}")
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
