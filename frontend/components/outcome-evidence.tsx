"use client";

import { useEffect, useMemo, useState } from "react";
import { Beaker, CircleAlert, ShieldCheck } from "lucide-react";

type StatusSummary = {
  decisive_outcome_count?: number;
};

type OutcomeReport = {
  observational_only?: boolean;
  threshold_calibration_allowed?: boolean;
  hard_gating_allowed?: boolean;
  settlement?: {
    signal_count?: number;
    settled_outcome_count?: number;
    mature_settlement_coverage_rate?: number | null;
  };
  evidence?: {
    status?: string;
    ready?: boolean;
    decisive_outcome_count?: number;
    observation_span_days?: number;
    requirements?: {
      minimum_decisive_outcomes?: number;
      minimum_outcomes_per_status?: number;
      minimum_observation_span_days?: number;
    };
  };
  by_execution_status?: Record<string, StatusSummary>;
};

function finite(value: unknown): number | undefined {
  return typeof value === "number" && Number.isFinite(value) ? value : undefined;
}

function count(value: unknown): string {
  const number = finite(value);
  return number === undefined ? "—" : number.toLocaleString("en-US", { maximumFractionDigits: 0 });
}

function progress(value: number | undefined, target: number | undefined): number {
  if (value === undefined || target === undefined || target <= 0) return 0;
  return Math.max(0, Math.min(100, (value / target) * 100));
}

function EvidenceProgress({ label, value, target, suffix = "" }: {
  label: string;
  value: number | undefined;
  target: number | undefined;
  suffix?: string;
}) {
  return (
    <div>
      <div className="flex items-center justify-between gap-4 text-xs">
        <span className="text-slate-400">{label}</span>
        <span className="font-mono text-slate-200">{count(value)} / {count(target)}{suffix}</span>
      </div>
      <div className="mt-1.5 h-1.5 overflow-hidden rounded-full bg-slate-800">
        <div className="h-full rounded-full bg-amber-400/80 transition-[width] duration-500" style={{ width: `${progress(value, target)}%` }} />
      </div>
    </div>
  );
}

export function OutcomeEvidence() {
  const [report, setReport] = useState<OutcomeReport>();
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    let active = true;
    const load = async () => {
      try {
        const response = await fetch("/dashboard/api/execution-outcome-validation", { cache: "no-store" });
        if (!response.ok) throw new Error("outcome evidence unavailable");
        const payload = await response.json() as OutcomeReport;
        if (payload.observational_only !== true || payload.hard_gating_allowed !== false) {
          throw new Error("unsafe outcome evidence contract");
        }
        if (active) {
          setReport(payload);
          setFailed(false);
        }
      } catch {
        if (active) {
          setReport(undefined);
          setFailed(true);
        }
      }
    };

    void load();
    const interval = window.setInterval(load, 60_000);
    return () => {
      active = false;
      window.clearInterval(interval);
    };
  }, []);

  const minimumClassOutcomes = useMemo(() => {
    if (!report?.by_execution_status) return undefined;
    const values = ["SUITABLE", "MARGINAL", "POOR"].map(
      (status) => finite(report.by_execution_status?.[status]?.decisive_outcome_count) ?? 0,
    );
    return Math.min(...values);
  }, [report]);

  if (!report) {
    return (
      <section className="panel mx-auto mb-7 max-w-7xl p-5" aria-label="Execution outcome evidence">
        <div className="flex items-center gap-2 text-sm font-medium text-slate-200">
          {failed ? <CircleAlert size={17} className="text-amber-400" /> : <Beaker size={17} className="text-slate-400" />}
          {failed ? "Outcome evidence unavailable" : "Loading outcome evidence…"}
        </div>
        <p className="mt-1 text-xs text-slate-500">No values are inferred while the read-only evidence report is unavailable.</p>
      </section>
    );
  }

  const ready = report.evidence?.ready === true;
  const requirements = report.evidence?.requirements;
  const decisive = finite(report.evidence?.decisive_outcome_count);
  const spanDays = finite(report.evidence?.observation_span_days);
  const minimumDecisive = finite(requirements?.minimum_decisive_outcomes);
  const minimumPerStatus = finite(requirements?.minimum_outcomes_per_status);
  const minimumSpan = finite(requirements?.minimum_observation_span_days);

  return (
    <section className="panel mx-auto mb-7 max-w-7xl p-5 sm:p-6" aria-label="Execution outcome evidence">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <h2 className="flex items-center gap-2 text-sm font-semibold text-slate-100"><Beaker size={17} className="text-sky-300" />Execution outcome evidence</h2>
          <p className="mt-1 text-sm text-slate-400">Natural signals are observed for 24 hours before execution suitability can be compared with outcomes.</p>
        </div>
        <span className={`status-pill border ${ready ? "border-emerald-400/25 bg-emerald-500/10 text-emerald-200" : "border-amber-400/25 bg-amber-500/10 text-amber-200"}`}>
          {ready ? "COMPARISON READY" : "COLLECTING EVIDENCE"}
        </span>
      </div>

      <dl className="mt-5 grid grid-cols-2 gap-3 sm:grid-cols-4">
        <div className="stat"><dt>Natural signals</dt><dd>{count(report.settlement?.signal_count)}</dd></div>
        <div className="stat"><dt>Settled 24h</dt><dd>{count(report.settlement?.settled_outcome_count)}</dd></div>
        <div className="stat"><dt>Decisive outcomes</dt><dd>{count(decisive)}</dd></div>
        <div className="stat"><dt>Observation span</dt><dd>{spanDays === undefined ? "—" : `${spanDays.toFixed(1)}d`}</dd></div>
      </dl>

      {!ready ? (
        <div className="mt-5 grid gap-3 lg:grid-cols-3">
          <EvidenceProgress label="Decisive outcomes" value={decisive} target={minimumDecisive} />
          <EvidenceProgress label="Each suitability class" value={minimumClassOutcomes} target={minimumPerStatus} />
          <EvidenceProgress label="Observation span" value={spanDays} target={minimumSpan} suffix="d" />
        </div>
      ) : null}

      <p className="mt-5 flex items-start gap-2 border-t border-slate-800 pt-3 text-xs leading-5 text-slate-500">
        <ShieldCheck size={15} className="mt-0.5 shrink-0 text-emerald-400/80" />
        Read-only research evidence. It cannot change scores, alerts, thresholds, or trade eligibility.
      </p>
    </section>
  );
}
