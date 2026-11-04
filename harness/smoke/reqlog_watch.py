#!/usr/bin/env python3
"""Capture goose's per-request LLM logs while a run is in progress.

goose writes each provider request to ``<state>/logs/llm_request.<uuid>.jsonl``
and, when the request finishes, renames it to ``llm_request.0.jsonl``, shifting
older ones up to ``.9`` (crates/goose/src/providers/utils.rs, LOGS_TO_KEEP=10).
A recipe run makes far more than ten requests, so this polls the directory and
keeps every finished request, keyed by inode. The file's birth time is when the
request started (``start_log``), and its mtime is when the usage line was
written, so ``end - start - model_load_ms`` is the generation time, prefill
included.

    reqlog_watch.py watch LOGS_DIR OUT.jsonl     # until SIGTERM/SIGINT
    reqlog_watch.py summary OUT.jsonl [...]      # print a JSON summary

stdlib only; run with ``python3 -I``.
"""

from __future__ import annotations

import json
import os
import re
import signal
import statistics
import sys
import time
from pathlib import Path

FINAL = re.compile(r"^llm_request\.\d+\.jsonl$")
TOOL_CALL = re.compile(r"<function=([A-Za-z0-9_.:-]+)>|\"name\":\s*\"([A-Za-z0-9_.:-]+)\"")


def _parse(path: Path) -> dict:
    rec: dict = {"tools_offered": 0, "tool_calls": []}
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return rec
    for i, line in enumerate(lines):
        try:
            obj = json.loads(line)
        except ValueError:
            continue
        if i == 0 and isinstance(obj, dict):
            inp = obj.get("input") or {}
            rec["tools_offered"] = len(inp.get("tools") or [])
            rec["system_head"] = str(inp.get("system") or "")[:60]
            continue
        data = obj.get("data", obj) if isinstance(obj, dict) else {}
        if not isinstance(data, dict):
            continue
        if "model_load_ms" in data:
            rec["model_load_ms"] = data["model_load_ms"]
        for key in ("prompt_tokens", "output_tokens", "path"):
            if key in data:
                rec[key] = data[key]
        text = data.get("generated_text")
        if isinstance(text, str):
            thinking, _, answer = text.rpartition("</think>")
            rec["tool_calls"] = [a or b for a, b in TOOL_CALL.findall(answer)]
            rec["thinking_chars"] = len(thinking)
        usage = obj.get("usage") if isinstance(obj, dict) else None
        if isinstance(usage, dict):
            rec.setdefault("prompt_tokens", usage.get("input_tokens"))
            rec.setdefault("output_tokens", usage.get("output_tokens"))
        if isinstance(obj, dict) and "error" in obj:
            rec["error"] = str(obj.get("error"))[:300]
    return rec


def watch(logs_dir: Path, out: Path) -> None:
    seen: set[tuple[int, float]] = set()
    stop = False

    def _stop(*_: object) -> None:
        nonlocal stop
        stop = True

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    # Requests finished before the watch started are not ours.
    if logs_dir.is_dir():
        for p in logs_dir.iterdir():
            if FINAL.match(p.name):
                st = p.stat()
                seen.add((st.st_ino, getattr(st, "st_birthtime", st.st_ctime)))
    with out.open("a", encoding="utf-8") as fh:
        while True:
            if logs_dir.is_dir():
                for p in sorted(logs_dir.iterdir()):
                    if not FINAL.match(p.name):
                        continue
                    try:
                        st = p.stat()
                    except FileNotFoundError:
                        continue
                    start = getattr(st, "st_birthtime", st.st_ctime)
                    key = (st.st_ino, start)
                    if key in seen:
                        continue
                    seen.add(key)
                    rec = _parse(p)
                    rec.update(start=start, end=st.st_mtime, wall_s=round(st.st_mtime - start, 3))
                    load = (rec.get("model_load_ms") or 0) / 1000.0
                    rec["gen_s"] = round(max(rec["wall_s"] - load, 0.0), 3)
                    fh.write(json.dumps(rec) + "\n")
                    fh.flush()
            if stop:
                return
            time.sleep(0.25)


def _count(items) -> dict:
    out: dict = {}
    for item in items:
        out[item] = out.get(item, 0) + 1
    return out


def summary(paths: list[Path]) -> dict:
    recs = []
    for path in paths:
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                recs.append(json.loads(line))
    done = [r for r in recs if r.get("output_tokens") is not None and r.get("gen_s", 0) > 0]
    out_tok = sum(r["output_tokens"] for r in done)
    gen_s = sum(r["gen_s"] for r in done)
    per = [r["output_tokens"] / r["gen_s"] for r in done if r["output_tokens"] >= 50]
    # The llama.cpp backend serves one request at a time, so overlapping
    # requests queue. Busy time is the union of [start + load, end].
    spans = sorted((r["start"] + (r.get("model_load_ms") or 0) / 1000.0, r["end"]) for r in done)
    busy = 0.0
    cur_s, cur_e = None, None
    for s_, e_ in spans:
        if cur_e is None or s_ > cur_e:
            if cur_e is not None:
                busy += cur_e - cur_s
            cur_s, cur_e = s_, e_
        else:
            cur_e = max(cur_e, e_)
    if cur_e is not None:
        busy += cur_e - cur_s
    # gen_s ~= prompt_tokens / prefill_rate + output_tokens / decode_rate,
    # fitted by least squares with no intercept. Needs varied requests.
    fit_prefill = fit_decode = None
    pts = [(r.get("prompt_tokens") or 0, r["output_tokens"], r["gen_s"]) for r in done]
    spp = sum(p * p for p, _, _ in pts)
    soo = sum(o * o for _, o, _ in pts)
    spo = sum(p * o for p, o, _ in pts)
    det = spp * soo - spo * spo
    if len(pts) >= 3 and det > 0:
        a = (sum(p * g for p, _, g in pts) * soo - sum(o * g for _, o, g in pts) * spo) / det
        b = (spp * sum(o * g for _, o, g in pts) - spo * sum(p * g for p, _, g in pts)) / det
        if a > 0 and b > 0:
            fit_prefill, fit_decode = round(1 / a), round(1 / b, 1)
    title = [r for r in recs if str(r.get("system_head", "")).startswith("Generate a short title")]
    return {
        "requests": len(recs),
        "requests_with_usage": len(done),
        "errors": sum(1 for r in recs if r.get("error")),
        "model_load_s": [round(r["model_load_ms"] / 1000, 2) for r in recs if r.get("model_load_ms")],
        "prompt_tokens_max": max((r.get("prompt_tokens") or 0 for r in done), default=0),
        "prompt_tokens_median": statistics.median([r.get("prompt_tokens") or 0 for r in done]) if done else 0,
        "output_tokens_total": out_tok,
        "generation_s_total": round(gen_s, 1),
        "model_busy_s": round(busy, 1),
        "output_tok_per_s_over_busy_time_incl_prefill": round(out_tok / busy, 1) if busy else None,
        "title_requests": len(title),
        "title_output_tokens": sum(r.get("output_tokens") or 0 for r in title),
        "tool_calls_emitted": dict(sorted(_count(c for r in recs for c in r.get("tool_calls") or []).items())),
        "fit_prefill_tok_per_s": fit_prefill,
        "fit_decode_tok_per_s": fit_decode,
        "output_tok_per_s_median_requests_ge50_out": round(statistics.median(per), 1) if per else None,
        "output_tok_per_s_max_request": round(max(per), 1) if per else None,
    }


def main(argv: list[str]) -> int:
    if len(argv) >= 3 and argv[0] == "watch":
        watch(Path(argv[1]), Path(argv[2]))
        return 0
    if len(argv) >= 2 and argv[0] == "summary":
        print(json.dumps(summary([Path(a) for a in argv[1:]]), indent=2))
        return 0
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
