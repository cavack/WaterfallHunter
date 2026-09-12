"use client";

import { useEffect, useState } from "react";
import { GitBranch, ShieldAlert } from "lucide-react";

type ShadowEvent = {
  event_id?: string;
  symbol?: string;
  v1_state?: string;
  v2_from_state?: string;
  v2_to_state?: string;
  reason_codes?: string[];
  observed_at?: number;
  diverged?: boolean;
};

type ShadowReport = {
  shadow_only?: boolean;
  promotion_allowed?: boolean;
  event_count?: number;
  divergence_count?: number;
  returned_event_count?: number;
  analysis?: {
    state_counts?: Record<string, number>;
    episode_count_in_returned_window?: number;
    triggered_episode_count_in_returned_window?: number;
    lead_time_seconds?: { available?: boolean; median?: number | null };
    promotion_decision?: string;
  };
  events?: ShadowEvent[];
};

function count(value: unknown): string {
  return typeof value === "number" && Number.isFinite(value) ? value.toLocaleString("en-US", { maximumFractionDigits: 0 }) : "—";
}

export function LifecycleShadow() {
  const [report, setReport] = useState<ShadowReport>();
  const [unavailable, setUnavailable] = useState(false);
  useEffect(() => {
    let active = true;
    let controller: AbortController | undefined;
    const load = async () => {
      controller?.abort();
      const requestController = new AbortController();
      controller = requestController;
      try {
        const [reportResponse, contractResponse] = await Promise.all([
          fetch("/dashboard/api/lifecycle-v2-shadow?limit=100", { cache: "no-store", signal: requestController.signal }),
          fetch("/dashboard/api/lifecycle-v2-contract", { cache: "no-store", signal: requestController.signal }),
        ]);
        const payload = await reportResponse.json() as ShadowReport;
        const contract = await contractResponse.json() as { shadow_only?: boolean; promotion_allowed?: boolean };
        if (!reportResponse.ok || !contractResponse.ok || payload.shadow_only !== true || payload.promotion_allowed !== false || contract.shadow_only !== true || contract.promotion_allowed !== false) throw new Error("unsafe lifecycle contract");
        if (active && !requestController.signal.aborted) { setReport(payload); setUnavailable(false); }
      } catch {
        if (active && !requestController.signal.aborted) { setReport(undefined); setUnavailable(true); }
      }
    };
    void load();
    const timer = window.setInterval(load, 60_000);
    return () => { active = false; controller?.abort(); window.clearInterval(timer); };
  }, []);

  const events = report?.events ?? [];
  const states = Object.entries(report?.analysis?.state_counts ?? {}).sort((left, right) => right[1] - left[1]);
  return (
    <section id="lifecycle-shadow" className="panel mx-auto mb-7 max-w-7xl scroll-mt-32 border-violet-500/20 p-5 sm:p-7" aria-label="Lifecycle V2 shadow">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div><p className="mb-2 text-xs font-semibold uppercase tracking-[0.2em] text-violet-300">Lifecycle experiment</p><h2 className="flex items-center gap-2 text-xl font-semibold"><GitBranch size={21} />V1 versus V2 shadow</h2><p className="mt-2 max-w-3xl text-sm leading-6 text-slate-400">Append-only comparison of the current lifecycle and evidence-scoped V2. V2 cannot change eligibility, notification, ranking or execution.</p></div>
        <span className="status-pill border border-rose-400/30 bg-rose-500/10 text-rose-100">DO NOT PROMOTE</span>
      </div>
      {unavailable && <p className="mt-5 rounded-xl border border-amber-500/25 bg-amber-500/10 px-4 py-3 text-sm text-amber-100">Lifecycle shadow evidence is unavailable. No healthy or promotion-ready state is inferred.</p>}
      {report && (
        <>
          <dl className="mt-6 grid grid-cols-2 gap-3 sm:grid-cols-5">
            <div className="stat"><dt>Shadow events</dt><dd>{count(report.event_count)}</dd></div>
            <div className="stat"><dt>Divergences</dt><dd className="text-violet-200">{count(report.divergence_count)}</dd></div>
            <div className="stat"><dt>Episodes sampled</dt><dd>{count(report.analysis?.episode_count_in_returned_window)}</dd></div>
            <div className="stat"><dt>Triggered episodes</dt><dd>{count(report.analysis?.triggered_episode_count_in_returned_window)}</dd></div>
            <div className="stat"><dt>Median lead time</dt><dd>{report.analysis?.lead_time_seconds?.available ? `${count(report.analysis.lead_time_seconds.median)}s` : "—"}</dd></div>
          </dl>
          <div className="mt-4 flex flex-wrap gap-2">{states.length > 0 ? states.map(([state, total]) => <span key={state} className="rounded-full border border-slate-700 bg-slate-950/50 px-3 py-1.5 text-xs text-slate-300">{state} <strong className="ml-1 text-slate-100">{total}</strong></span>) : <span className="text-sm text-slate-500">No state distribution is available.</span>}</div>
          {events.length > 0 && <div className="mt-5 overflow-hidden rounded-2xl border border-slate-800"><div className="max-h-72 overflow-auto"><table className="w-full min-w-[780px] text-left text-xs"><thead className="sticky top-0 bg-slate-950 text-slate-500"><tr><th className="px-4 py-3">Observed</th><th className="px-4 py-3">Symbol</th><th className="px-4 py-3">V1</th><th className="px-4 py-3">V2 transition</th><th className="px-4 py-3">Reasons</th></tr></thead><tbody className="divide-y divide-slate-800">{events.map((event, index) => <tr key={event.event_id ?? index} className={event.diverged ? "bg-violet-500/5" : ""}><td className="px-4 py-3 tabular-nums text-slate-400">{count(event.observed_at)}</td><td className="px-4 py-3 font-medium">{event.symbol ?? "—"}</td><td className="px-4 py-3">{event.v1_state ?? "—"}</td><td className="px-4 py-3">{event.v2_from_state ?? "—"} → {event.v2_to_state ?? "—"}</td><td className="px-4 py-3 text-slate-400">{event.reason_codes?.join(", ") || "—"}</td></tr>)}</tbody></table></div></div>}
        </>
      )}
      <p className="mt-5 flex items-start gap-2 border-t border-slate-800 pt-4 text-xs leading-5 text-slate-400"><ShieldAlert size={16} className="mt-0.5 shrink-0 text-violet-300" />Promotion remains blocked until the complete STRICT multi-week, walk-forward, purged and untouched holdout evidence gates pass with independent calibration.</p>
    </section>
  );
}
