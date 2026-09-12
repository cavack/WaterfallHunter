"use client";

import { useEffect, useState } from "react";
import { Archive, ShieldCheck } from "lucide-react";

type Report = {
  available?: boolean;
  operational?: boolean;
  observational_only?: boolean;
  hard_gating_allowed?: boolean;
  dataset?: {
    days?: number;
    source?: string;
    strategy_equivalent?: boolean;
  } | null;
  summary?: {
    event_count?: number;
    settled_count?: number;
    win_rate?: number | null;
    net_expectancy_r?: number | null;
  };
};

function number(value: unknown, digits = 0): string {
  return typeof value === "number" && Number.isFinite(value)
    ? value.toLocaleString("en-US", { maximumFractionDigits: digits, minimumFractionDigits: digits })
    : "—";
}

export function HistoricalOutcomes() {
  const [report, setReport] = useState<Report>();
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    let active = true;
    const load = async () => {
      try {
        const response = await fetch("/dashboard/api/historical-outcomes", { cache: "no-store" });
        const payload = await response.json() as Report;
        if (!response.ok || payload.operational !== true || payload.observational_only !== true || payload.hard_gating_allowed !== false) throw new Error("unsafe contract");
        if (active) { setReport(payload); setFailed(false); }
      } catch {
        if (active) setFailed(true);
      }
    };
    void load();
    const timer = window.setInterval(load, 60_000);
    return () => { active = false; window.clearInterval(timer); };
  }, []);

  if (!report) return <section className="panel mx-auto mb-7 max-w-7xl p-5 sm:p-6"><p className="text-sm text-slate-400">{failed ? "Historical outcomes unavailable; no values are inferred." : "Loading historical outcomes…"}</p></section>;
  const winRate = typeof report.summary?.win_rate === "number" ? report.summary.win_rate * 100 : undefined;

  return (
    <section className="panel mx-auto mb-7 max-w-7xl p-5 sm:p-6" aria-label="Operational historical outcomes">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div><h2 className="flex items-center gap-2 text-sm font-semibold"><Archive size={17} className="text-cyan-300" />Operational historical outcomes</h2><p className="mt-1 text-sm text-slate-400">Downloaded historical evidence is served from the production database with explicit provenance.</p></div>
        <span className={report.available === true ? "status-pill border border-cyan-400/25 bg-cyan-500/10 text-cyan-200" : "status-pill border border-amber-400/25 bg-amber-500/10 text-amber-200"}>{report.available === true ? "PRODUCTION DATA" : "NO DATA"}</span>
      </div>
      {failed ? <p className="mt-4 rounded-xl border border-amber-400/25 bg-amber-500/10 p-3 text-xs text-amber-100">Refresh unavailable. The last verified report remains displayed.</p> : null}
      <dl className="mt-5 grid grid-cols-2 gap-3 sm:grid-cols-4">
        <div className="stat"><dt>Window</dt><dd>{number(report.dataset?.days)}d</dd></div>
        <div className="stat"><dt>Historical events</dt><dd>{number(report.summary?.event_count)}</dd></div>
        <div className="stat"><dt>Settled</dt><dd>{number(report.summary?.settled_count)}</dd></div>
        <div className="stat"><dt>Modeled net EV</dt><dd>{number(report.summary?.net_expectancy_r, 4)}R</dd></div>
      </dl>
      <p className="mt-3 text-xs text-slate-500">Settled win rate: {number(winRate, 1)}%. Strategy-equivalent: {report.dataset?.strategy_equivalent === true ? "yes" : "no"}.</p>
      <p className="mt-4 flex items-start gap-2 border-t border-slate-800 pt-3 text-xs leading-5 text-slate-500"><ShieldCheck size={15} className="mt-0.5 shrink-0 text-emerald-400/80" />Operational and candidate-linked, but not ranking-eligible and never mixed with the natural live ledger.</p>
    </section>
  );
}
