"""Aggregation and reporting for the synthetic LLM evaluation (`flowpilot-demo eval`).

Pure functions over `llm_runs` rows: success rate, fallback reasons, latency and guard failures
per task. Reports carry counts, names and timings only, never lead emails, lead messages, raw
model output or secrets.
"""

from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass, field

TASKS = ("qualification", "draft")


@dataclass(frozen=True)
class RunRecord:
    task: str
    outcome: str  # "ok" or "fallback"
    fallback_reason: str | None
    error: str | None
    latency_ms: int | None
    warmup: bool = False  # the first model call loads the model; kept out of latency figures


@dataclass(frozen=True)
class Latency:
    count: int
    p50: int
    p95: int
    max: int


@dataclass(frozen=True)
class TaskSummary:
    task: str
    calls: int
    ok: int
    fallbacks: dict[str, int]
    guard_failures: dict[str, int]
    latency_ok: Latency | None
    latency_all: Latency | None
    layout_repairs: int = 0

    @property
    def success_rate(self) -> float:
        return self.ok / self.calls if self.calls else 0.0


@dataclass(frozen=True)
class Report:
    meta: dict[str, object]
    tasks: tuple[TaskSummary, ...]
    warmup_ms: int | None = None
    notes: tuple[str, ...] = field(default_factory=tuple)


def percentile(values: list[int], q: float) -> int:
    """Nearest-rank percentile of a non-empty list."""
    ordered = sorted(values)
    rank = max(1, math.ceil(q * len(ordered)))
    return ordered[rank - 1]


def _latency(values: list[int]) -> Latency | None:
    if not values:
        return None
    return Latency(len(values), percentile(values, 0.5), percentile(values, 0.95), max(values))


def guard_names(error: str | None) -> list[str]:
    """Guard violation names; `assist` stores them comma-separated in `llm_runs.error`."""
    return [name.strip() for name in (error or "").split(",") if name.strip()]


def summarize(records: list[RunRecord], layout_repairs: int = 0) -> tuple[TaskSummary, ...]:
    summaries = []
    for task in TASKS:
        runs = [r for r in records if r.task == task]
        fallbacks = Counter(r.fallback_reason for r in runs if r.outcome != "ok")
        guards: Counter[str] = Counter()
        for r in runs:
            if r.fallback_reason == "guard_rejected":
                guards.update(guard_names(r.error))
        timed = [r for r in runs if r.latency_ms is not None and not r.warmup]
        summaries.append(
            TaskSummary(
                task=task,
                calls=len(runs),
                ok=sum(r.outcome == "ok" for r in runs),
                fallbacks=dict(sorted(fallbacks.items(), key=lambda kv: (-kv[1], str(kv[0])))),
                guard_failures=dict(sorted(guards.items(), key=lambda kv: (-kv[1], kv[0]))),
                latency_ok=_latency([r.latency_ms for r in timed if r.outcome == "ok"]),
                latency_all=_latency([r.latency_ms for r in timed]),
                layout_repairs=layout_repairs if task == "draft" else 0,
            )
        )
    return tuple(summaries)


def _fmt_latency(latency: Latency | None) -> str:
    if latency is None:
        return "n/a"
    return f"p50 {latency.p50} ms, p95 {latency.p95} ms, max {latency.max} ms (n={latency.count})"


def format_report(report: Report) -> str:
    meta = ", ".join(f"{k}={v}" for k, v in report.meta.items())
    lines = [f"FlowPilot synthetic LLM evaluation ({meta})"]
    if report.warmup_ms is not None:
        lines.append(f"warm-up call (model load, excluded from latency): {report.warmup_ms} ms")
    for t in report.tasks:
        lines.append(
            f"\n{t.task}: {t.ok}/{t.calls} from the model ({t.success_rate:.0%}), "
            f"{t.calls - t.ok} fallback"
        )
        lines.append(f"  latency, model successes: {_fmt_latency(t.latency_ok)}")
        lines.append(f"  latency, all calls:       {_fmt_latency(t.latency_all)}")
        if t.fallbacks:
            reasons = ", ".join(f"{k}={v}" for k, v in t.fallbacks.items())
            lines.append(f"  fallback reasons: {reasons}")
        if t.guard_failures:
            guards = ", ".join(f"{k}={v}" for k, v in t.guard_failures.items())
            lines.append(f"  guard failures:   {guards}")
        if t.task == "draft":
            lines.append(f"  layout repairs (whitespace only): {t.layout_repairs}")
    lines.extend(f"note: {n}" for n in report.notes)
    return "\n".join(lines)


def report_dict(report: Report) -> dict:
    """JSON-safe report: counts, names and timings only."""

    def latency(value: Latency | None) -> dict | None:
        return None if value is None else value.__dict__.copy()

    return {
        "meta": dict(report.meta),
        "warmup_ms": report.warmup_ms,
        "tasks": [
            {
                "task": t.task,
                "calls": t.calls,
                "model_ok": t.ok,
                "success_rate": round(t.success_rate, 4),
                "fallbacks": t.fallbacks,
                "guard_failures": t.guard_failures,
                "latency_ok": latency(t.latency_ok),
                "latency_all": latency(t.latency_all),
                "layout_repairs": t.layout_repairs,
            }
            for t in report.tasks
        ],
        "notes": list(report.notes),
    }
