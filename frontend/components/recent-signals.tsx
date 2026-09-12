"use client";

import { useEffect, useState } from "react";
import { History, ShieldCheck } from "lucide-react";

type DecisionRow = {
  event_id?: number;
  symbol?: string;
  event_at?: number;
  decision?: string;
  previous_decision?: string | null;
  lifecycle_state?: string;
  entry_readiness?: number;
  evidence_coverage_pct?: number;
  trade_plan?: Record<string, unknown> | null;
  ai_advisory?: Record<string, unknown>;
};

type RecentSignalReport = {
  contract_version?: string;
  operational?: boolean;
  observational_only?: boolean;
  hard_gating_allowed?: boolean;
  count?: number;
  decisions?: DecisionRow[];
};

function n(value: unknown, digits = 4): string {
  return typeof value === "number" && Number.isFinite(value)
    ? value.toLocaleString("en-US", { maximumFractionDigits: digits })
    : "—";
}

function timeText(value: unknown): string {
  return typeof value === "number" && Number.isFinite(value)
    ? new Date(value * 1000).toLocaleString()
    : "—";
}

export function RecentSignals() {
  const [report, setReport] = useState<RecentSignalReport>();
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    let active = true;
    let controller: AbortController | undefined;
    const refresh = async () => {
      controller?.abort();
      const requestController = new AbortController();
      controller = requestController;
      try {
        const response = await fetch("/dashboard/api/recent-signals?limit=30", {
          cache: "no-store",
          signal: requestController.signal,
        });
        const payload = await response.json() as RecentSignalReport;
        if (!response.ok || payload.contract_version !== "canonical_decision_history_v1" || payload.operational !== true || payload.observational_only !== true || payload.hard_gating_allowed !== false) {
          throw new Error("unsafe canonical decision history contract");
        }
        if (active && controller === requestController) { setReport(payload); setFailed(false); }
      } catch {
        if (active && controller === requestController) setFailed(true);
      }
    };
    void refresh();
    const timer = window.setInterval(() => void refresh(), 15_000);
    return () => { active = false; controller?.abort(); window.clearInterval(timer); };
  }, []);

  const rows = Array.isArray(report?.decisions) ? report.decisions : [];
  return (
    <section className="panel mx-auto mb-7 max-w-7xl p-5 sm:p-6" aria-label="Recent canonical decision history">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <h2 className="flex items-center gap-2 text-sm font-semibold"><History size={17} className="text-cyan-300" />Recent canonical decisions</h2>
          <p className="mt-1 text-sm text-slate-400">Immutable decision transitions; current candidate state cannot erase earlier ENTRY READY, ACTIVE, LATE, INVALIDATED, or EXPIRED events.</p>
        </div>
        <span className="status-pill border border-cyan-400/25 bg-cyan-500/10 text-cyan-200">DECISION LEDGER</span>
      </div>
      {failed && report ? <p className="mt-4 rounded-xl border border-amber-400/25 bg-amber-500/10 p-3 text-xs text-amber-100">Refresh unavailable. The last verified rows remain displayed.</p> : null}
      {!report ? <p className="mt-5 text-sm text-slate-500">{failed ? "Recent decision history is unavailable; no rows are inferred." : "Loading immutable decision history…"}</p> : rows.length === 0 ? <p className="mt-5 text-sm text-slate-500">No persisted decisions are available.</p> : (
        <div className="table-scroll mt-5">
          <table className="data-table min-w-[980px]">
            <thead><tr><th>Changed</th><th>Symbol</th><th>Previous</th><th>Decision</th><th>Readiness</th><th>Data</th><th>Entry</th><th>SL</th><th>TP1</th><th>TP2</th><th>Expiry</th><th>AI</th></tr></thead>
            <tbody>{rows.map((row) => {
              const plan = row.trade_plan ?? {};
              const advisory = row.ai_advisory ?? {};
              return <tr key={row.event_id ?? `${row.symbol}-${row.event_at}`}>
              <td className="whitespace-nowrap text-slate-400">{timeText(row.event_at)}</td>
              <td className="font-mono text-sky-300">{row.symbol ?? "—"}</td>
              <td>{row.previous_decision ?? "—"}</td>
              <td className="font-medium text-cyan-100">{row.decision ?? "UNAVAILABLE"}</td>
              <td className="font-mono">{n(row.entry_readiness, 1)}%</td>
              <td className="font-mono">{n(row.evidence_coverage_pct, 1)}%</td>
              <td className="font-mono">{n(plan.entry_price, 8)}</td>
              <td className="font-mono">{n(plan.stop_loss, 8)}</td>
              <td className="font-mono">{n(plan.take_profit_1, 8)}</td>
              <td className="font-mono">{n(plan.take_profit_2, 8)}</td>
              <td className="whitespace-nowrap">{timeText(plan.expires_at)}</td>
              <td>{String(advisory.ai_advice ?? "UNAVAILABLE")}</td>
            </tr>})}</tbody>
          </table>
        </div>
      )}
      <p className="mt-4 flex items-start gap-2 border-t border-slate-800 pt-3 text-xs text-slate-500"><ShieldCheck size={14} className="mt-0.5 shrink-0" />Read-only historical evidence. It does not alter scores, states, alerts, or eligibility.</p>
    </section>
  );
}
