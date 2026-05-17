"""
Minimal CLI entrypoint to run this file
"""

import argparse

from orchestrator import run  # noqa: E402


def _parse_argvs(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="coder", description="Run a workflow iteration")
    parser.add_argument("--ticket_id", required=True, help="Ticket ID to be picked up for coding")
    parser.add_argument("--resume", action="store_true", help="Resume a previous run")
    return parser.parse_args(argv)


def main():
    args = _parse_argvs()
    try:
        run(args.ticket_id, args.resume)
    except Exception as e:
        print(f"run failed: {e}")


if __name__ == "__main__":
    main()
