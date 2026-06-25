"""Command-line interface for labquery."""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys

from dotenv import load_dotenv

from labquery.lims_client import LabioAllClient
from labquery.nl_layer import NLLayer
from labquery.notify import SlackNotifier
from labquery.plr_runner import PLRRunner


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="labquery",
        description="Natural language interface for LIMS and liquid handler automation",
    )
    parser.add_argument(
        "--lims",
        choices=["labio", "local", "benchling", "elabjournal"],
        default="local",
        help="LIMS backend: local (default, persistent SQLite), labio, benchling, or elabjournal",
    )
    parser.add_argument(
        "--lims-url",
        default=None,
        help="Base URL for the LIMS API (default: $LABIO_URL or http://127.0.0.1:5001)",
    )
    parser.add_argument(
        "--model",
        default="claude-haiku-4-5-20251001",
        help="Claude model to use (default: claude-haiku-4-5-20251001)",
    )
    parser.add_argument(
        "--backend",
        choices=["opentrons", "tecan", "hamilton"],
        default="opentrons",
        help="Liquid handler backend (default: opentrons)",
    )
    parser.add_argument(
        "--serve",
        action="store_true",
        help="Start WebSocket chat server instead of CLI",
    )
    parser.add_argument(
        "--ws-port",
        type=int,
        default=8765,
        help="WebSocket server port (default: 8765)",
    )
    parser.add_argument(
        "--http-port",
        type=int,
        default=8080,
        help="HTTP server port for chat UI (default: 8080)",
    )
    parser.add_argument(
        "--simulator",
        action="store_true",
        help="Use PLR simulator backend instead of hardware (required until hardware drivers are tested)",
    )
    parser.add_argument(
        "--visualizer",
        action="store_true",
        help="Enable PLR deck visualizer (browser-based, requires --simulator)",
    )
    parser.add_argument(
        "--slack-webhook",
        default=None,
        help="Slack incoming webhook URL for notifications (or set SLACK_WEBHOOK_URL env var)",
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Enable verbose logging",
    )
    parser.add_argument(
        "query",
        nargs="?",
        help="Single query to run (omit for interactive mode)",
    )
    return parser


def run_interactive(nl: NLLayer) -> None:
    """Run the interactive REPL loop."""
    print("labquery — Natural Language LIMS Interface")
    print("Type your query, or 'quit' to exit.\n")

    while True:
        try:
            user_input = input("labquery> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nGoodbye.")
            break

        if not user_input:
            continue
        if user_input.lower() in ("quit", "exit", "q"):
            print("Goodbye.")
            break

        try:
            response = nl.query(user_input)
            print(f"\n{response}\n")
        except Exception as e:
            print(f"\nError: {e}\n", file=sys.stderr)


async def run_serve(args, lims, plr, notifier=None) -> None:
    """Start the WebSocket chat server."""
    await plr.setup()

    from labquery.ws_server import ChatServer

    server = ChatServer(
        lims=lims,
        plr=plr,
        model=args.model,
        ws_port=args.ws_port,
        http_port=args.http_port,
        notifier=notifier,
    )
    try:
        await server.start()
    finally:
        if plr.bridge_ready:
            await plr.teardown()


def main() -> None:
    load_dotenv()

    parser = build_parser()
    args = parser.parse_args()

    log_level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=[
            logging.FileHandler("labquery.log"),
            *([] if not args.verbose else [logging.StreamHandler()]),
        ],
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("websockets").setLevel(logging.WARNING)

    if args.visualizer and not args.simulator:
        print(
            "Error: --visualizer requires --simulator.\n"
            "Hardware visualizer support is not yet implemented."
        )
        sys.exit(1)

    lims_backend = args.lims

    labio_proc = None
    if lims_backend == "labio":
        from labquery.labio_server import start_labio_server
        labio_proc = start_labio_server()
        lims = LabioAllClient(base_url=args.lims_url)
    elif lims_backend == "local":
        from labquery.lims_server import start_local_lims
        start_local_lims()
        lims = LabioAllClient(base_url=args.lims_url or "http://127.0.0.1:5001")
    elif lims_backend == "benchling":
        from labquery.lims_client import BenchlingClient
        lims = BenchlingClient(url=args.lims_url)
    elif lims_backend == "elabjournal":
        from labquery.lims_client import ELabJournalClient
        lims = ELabJournalClient(url=args.lims_url)
    plr = PLRRunner(
        backend=args.backend,
        simulate=args.simulator,
        enable_visualizer=args.visualizer,
    )

    import os
    webhook_url = args.slack_webhook or os.environ.get("SLACK_WEBHOOK_URL")
    notifier = SlackNotifier(webhook_url=webhook_url)
    if notifier.enabled:
        logging.getLogger("labquery").info("Slack notifications enabled")

    try:
        if args.serve:
            try:
                asyncio.run(run_serve(args, lims, plr, notifier))
            except KeyboardInterrupt:
                print("\nShutting down.")
        else:
            asyncio.run(plr.setup())
            nl = NLLayer(lims=lims, plr=plr, model=args.model, notifier=notifier)
            if args.query:
                response = nl.query(args.query)
                print(response)
            else:
                run_interactive(nl)
    except NotImplementedError as e:
        print(
            f"Error: {e}\n"
            "Use --simulator to run with a simulated liquid handler."
        )
        sys.exit(1)


if __name__ == "__main__":
    main()
