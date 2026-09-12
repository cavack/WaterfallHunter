"use client";

import { useEffect, useState } from "react";
import { Database, ShieldCheck } from "lucide-react";

type Report = {
  operational?: boolean;
  observational_only?: boolean;
  hard_gating_allowed?: boolean;
  snapshot_count_24h?: number;
  symbol_count_24h?: number;
  latest_age_seconds?: number | null;
  coverage?: { decision_packet_complete_rate?: number | null; orderbook_rate?: number | null; confirmation_source_rate?: number | null };
  replay?: { decision_packet_replay?: boolean; source_replay_ready?: boolean; source_replay_ready_rate?: number | null; raw_ohlcv_capture_rate?: number | null; raw_trades_capture_rate?: number | null };
};

function count(value: unknown): string {
  return typeof value === "number" && Number.isFinite(value) ? value.toLocaleString("en-US", { maximumFractionDigits: 0 }) : "—";
}

function percent(value: unknown): string {
  return typeof value === "number" && Number.isFinite(value) ? `${(value * 100).toFixed(1)}%` : "—";
}

export function ProductionEvidence() {
  const [report, setReport] = useState<Report>();
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    let active = true;
    const load = async () => {
      try {
        const response = await fetch("/dashboard/api/production-evidence", { cache: "no-store" });
        const payload = await response.json() as Report;
        if (!response.ok || payload.operational !== true || payload.observational_only !== true || payload.hard_gating_allowed !== false) throw new Error("unsafe contract");
        if (active) { setReport(payload); setFailed(false); }
      } catch { if (active) setFailed(true); }
    };
    void load();
    const timer = window.setInterval(load, 60_000);
    return () => { active = false; window.clearInterval(timer); };
  }, []);

  if (!report) return <section className="panel mx-auto mb-7 max-w-7xl p-5 sm:p-6"><p className="text-sm text-slate-400">{failed ? "Production evidence unavailable; no values are inferred." : "Loading production evidence…"}</p></section>;
  return (
    <section className="panel mx-auto mb-7 max-w-7xl p-5 sm:p-6" aria-label="Production evidence recorder">
      <div className="flex flex-wrap items-start justify-between gap-4"><div><h2 className="flex items-center gap-2 text-sm font-semibold"><Database size={17} className="text-emerald-300" />Production evidence recorder</h2><p className="mt-1 text-sm text-slate-400">Real decision packets captured from the running evaluator every five minutes.</p></div><span className="status-pill border border-emerald-400/25 bg-emerald-500/10 text-emerald-200">RECORDING</span></div>
      {failed ? <p className="mt-4 rounded-xl border border-amber-400/25 bg-amber-500/10 p-3 text-xs text-amber-100">Refresh unavailable. The last verified report remains displayed.</p> : null}
      <dl className="mt-5 grid grid-cols-2 gap-3 sm:grid-cols-4">
        <div className="stat"><dt>Snapshots 24h</dt><dd>{count(report.snapshot_count_24h)}</dd></div>
        <div className="stat"><dt>Symbols 24h</dt><dd>{count(report.symbol_count_24h)}</dd></div>
        <div className="stat"><dt>Complete packets</dt><dd>{percent(report.coverage?.decision_packet_complete_rate)}</dd></div>
        <div className="stat"><dt>Latest age</dt><dd>{count(report.latest_age_seconds)}s</dd></div>
      </dl>
      <p className="mt-3 text-xs text-slate-500">Orderbook coverage: {percent(report.coverage?.orderbook_rate)} · confirmation-source coverage: {percent(report.coverage?.confirmation_source_rate)} · decision replay: {report.replay?.decision_packet_replay === true ? "ready" : "not ready"}.</p>
      <p className="mt-2 text-xs text-slate-500">Raw OHLCV: {percent(report.replay?.raw_ohlcv_capture_rate)} · raw trades: {percent(report.replay?.raw_trades_capture_rate)} · source replay ready: {percent(report.replay?.source_replay_ready_rate)}.</p>
      <p className="mt-4 flex items-start gap-2 border-t border-slate-800 pt-3 text-xs leading-5 text-slate-500"><ShieldCheck size={15} className="mt-0.5 shrink-0 text-emerald-400/80" />Source replay is claimed only for packets containing both validated closed OHLCV and the fresh trades used by microstructure. Recorder failures remain fail-open.</p>
    </section>
  );
}
