"""Single entrypoint behind every Makefile target.

    python -m cedx.cli <command> [args]

Commands map 1:1 to the uniform probe interface the graders invoke.
"""
from __future__ import annotations

import argparse
import json
import sys

from .amendment import compute_amendment
from .config import load_config
from .pipeline import Pipeline


def _print_amendment(cfg) -> None:
    amd = compute_amendment(cfg.case_id)
    print(f"AMENDMENT: role={amd.role} threshold={amd.threshold}", flush=True)


def cmd_demo(cfg, args) -> int:
    print(f"[cedx] case_id={cfg.case_id}  replay={cfg.replay_llm}  seed={cfg.seed_dir}")
    _print_amendment(cfg)
    Pipeline(cfg).run()
    return 0


def cmd_trace(cfg, args) -> int:
    from .probes import trace_record

    return trace_record(cfg, args.id)


def cmd_replay(cfg, args) -> int:
    from .probes import replay_lineage

    return replay_lineage(cfg, args.id)


def cmd_eval(cfg, args) -> int:
    from .eval_harness import run_eval

    return run_eval(cfg)


def cmd_probe(cfg, args) -> int:
    from . import probes

    fn = getattr(probes, f"probe_{args.name.replace('-', '_')}")
    return fn(cfg)


def main(argv=None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    cfg = load_config()
    ap = argparse.ArgumentParser(prog="cedx")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("demo")
    p_trace = sub.add_parser("trace"); p_trace.add_argument("--id", required=True)
    p_replay = sub.add_parser("replay"); p_replay.add_argument("--id", required=True)
    sub.add_parser("eval")
    p_probe = sub.add_parser("probe"); p_probe.add_argument("name")
    sub.add_parser("amendment")

    args = ap.parse_args(argv)
    if args.cmd == "demo":
        return cmd_demo(cfg, args)
    if args.cmd == "trace":
        return cmd_trace(cfg, args)
    if args.cmd == "replay":
        return cmd_replay(cfg, args)
    if args.cmd == "eval":
        return cmd_eval(cfg, args)
    if args.cmd == "amendment":
        _print_amendment(cfg)
        return 0
    if args.cmd == "probe":
        return cmd_probe(cfg, args)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
