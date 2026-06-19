"""Command-line interface for labquery."""

from __future__ import annotations

import argparse
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


def main() -> None:
    load_dotenv()

    parser = build_parser()
    args = parser.parse_args()

    lims = LabioAllClient(base_url=args.lims_url)
    plr = PLRRunner(use_simulator=True)
    nl = NLLayer(lims=lims, plr=plr, model=args.model)

    if args.query:
        response = nl.query(args.query)
        print(response)
    else:
        run_interactive(nl)


if __name__ == "__main__":
    main()
