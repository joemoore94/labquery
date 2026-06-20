"""Command-line interface for labquery."""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys

from dotenv import load_dotenv

from labquery.lims_client import LabioAllClient
from labquery.nl_layer import NLLayer
from labquery.plr_runner import PLRRunner


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="labquery",
        description="Natural language interface for LIMS and liquid handler automation",
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
        "--visualizer",
        action="store_true",
        help="Enable PLR deck visualizer (browser-based)",
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


async def run_serve(args, lims, plr) -> None:
    """Start the WebSocket chat server, optionally with PLR visualizer."""
    if args.visualizer:
        await plr.setup()

    from labquery.ws_server import ChatServer

    server = ChatServer(
        lims=lims,
        plr=plr,
        model=args.model,
        ws_port=args.ws_port,
        http_port=args.http_port,
    )
    try:
        await server.start()
    finally:
        if args.visualizer:
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

    lims = LabioAllClient(base_url=args.lims_url)
    plr = PLRRunner(
        use_simulator=True,
        enable_visualizer=args.visualizer,
    )

    if args.serve:
        try:
            asyncio.run(run_serve(args, lims, plr))
        except KeyboardInterrupt:
            print("\nShutting down.")
    elif args.query:
        nl = NLLayer(lims=lims, plr=plr, model=args.model)
        response = nl.query(args.query)
        print(response)
    else:
        nl = NLLayer(lims=lims, plr=plr, model=args.model)
        run_interactive(nl)


if __name__ == "__main__":
    main()
