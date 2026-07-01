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


def cmd_dashboard(cfg, args) -> int:
    """Bundle the latest audit into webui/data.js so the static dashboard renders
    with no backend (works over file:// and any static host)."""
    from pathlib import Path

    audit_p = cfg.out_dir / "audit.json"
    if not audit_p.exists():
        Pipeline(cfg).run(quiet=True)
    audit = json.loads(audit_p.read_text(encoding="utf-8"))
    exq_p = cfg.out_dir / "exception_queue.json"
    exq = json.loads(exq_p.read_text(encoding="utf-8")) if exq_p.exists() else {}
    data_js = (
        "window.AUDIT=" + json.dumps(audit) + ";\n"
        "window.EXCEPTIONS=" + json.dumps(exq) + ";\n"
    )
    # docs/ is the published dashboard (GitHub Pages serves it as the live URL)
    docs = Path("docs")
    docs.mkdir(exist_ok=True)
    (docs / "data.js").write_text(data_js, encoding="utf-8")
    (docs / ".nojekyll").write_text("", encoding="utf-8")
    print(f"wrote docs/data.js ({len(audit['records'])} records). Open docs/index.html.")
    return 0


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
    sub.add_parser("dashboard")

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
    if args.cmd == "dashboard":
        return cmd_dashboard(cfg, args)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
