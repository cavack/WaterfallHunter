"use client";

import { useEffect, useState } from "react";
import { GitCompare, ShieldCheck } from "lucide-react";

type Report = {
  operational?: boolean;
  observational_only?: boolean;
  hard_gating_allowed?: boolean;
  replayed_count?: number;
  equivalent_count?: number;
  mismatch_count?: number;
  not_replayable_count?: number;
  equivalence_rate?: number | null;
  strategy_equivalent?: boolean;
  requirements?: { minimum_replays?: number; triggered_path_replay_required?: boolean };
};

function count(value: unknown): string {
  return typeof value === "number" && Number.isFinite(value) ? value.toLocaleString("en-US", { maximumFractionDigits: 0 }) : "—";
}

export function FeatureReplay() {
  const [report, setReport] = useState<Report>();
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    let active = true;
    const load = async () => {
      try {
        const response = await fetch("/dashboard/api/feature-replay", { cache: "no-store" });
        const payload = await response.json() as Report;
        if (!response.ok || payload.operational !== true || payload.observational_only !== true || payload.hard_gating_allowed !== false) throw new Error("unsafe contract");
        if (active) { setReport(payload); setFailed(false); }
      } catch { if (active) setFailed(true); }
    };
    void load();
    const timer = window.setInterval(load, 60_000);
    return () => { active = false; window.clearInterval(timer); };
  }, []);

  if (!report) return <section className="panel mx-auto mb-7 max-w-7xl p-5 sm:p-6"><p className="text-sm text-slate-400">{failed ? "Feature replay unavailable; no equivalence is inferred." : "Loading feature replay…"}</p></section>;
  const rate = typeof report.equivalence_rate === "number" ? `${(report.equivalence_rate * 100).toFixed(1)}%` : "—";
  return (
    <section className="panel mx-auto mb-7 max-w-7xl p-5 sm:p-6" aria-label="Feature equivalent replay">
      <div className="flex flex-wrap items-start justify-between gap-4"><div><h2 className="flex items-center gap-2 text-sm font-semibold"><GitCompare size={17} className="text-fuchsia-300" />Feature-equivalent replay</h2><p className="mt-1 text-sm text-slate-400">Raw production sources are replayed through the same candle, microstructure, stage, gate, and score code.</p></div><span className={`status-pill border ${report.strategy_equivalent === true ? "border-emerald-400/25 bg-emerald-500/10 text-emerald-200" : "border-amber-400/25 bg-amber-500/10 text-amber-200"}`}>{report.strategy_equivalent === true ? "EQUIVALENT" : "VALIDATING"}</span></div>
      {failed ? <p className="mt-4 rounded-xl border border-amber-400/25 bg-amber-500/10 p-3 text-xs text-amber-100">Refresh unavailable. The last verified report remains displayed.</p> : null}
      <dl className="mt-5 grid grid-cols-2 gap-3 sm:grid-cols-4">
        <div className="stat"><dt>Replayed</dt><dd>{count(report.replayed_count)}</dd></div>
        <div className="stat"><dt>Equivalent</dt><dd className="text-emerald-200">{count(report.equivalent_count)}</dd></div>
        <div className="stat"><dt>Mismatch</dt><dd className="text-rose-200">{count(report.mismatch_count)}</dd></div>
        <div className="stat"><dt>Equivalence rate</dt><dd>{rate}</dd></div>
      </dl>
      <p className="mt-3 text-xs text-slate-500">Global equivalence requires at least {count(report.requirements?.minimum_replays)} replays, zero mismatches, and coverage of the natural TRIGGERED path.</p>
      <p className="mt-4 flex items-start gap-2 border-t border-slate-800 pt-3 text-xs leading-5 text-slate-500"><ShieldCheck size={15} className="mt-0.5 shrink-0 text-emerald-400/80" />Replay results are immutable and observational. They cannot promote thresholds, ranking, lifecycle state, alerts, or orders.</p>
    </section>
  );
}
