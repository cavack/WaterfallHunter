"use client";

import { ChangeEvent, useMemo, useRef, useState } from "react";
import { Database, Download, FileUp, FlaskConical, Play, ShieldAlert } from "lucide-react";

type ReplayEvent = {
  event_id?: string;
  occurred_at?: number;
  event_type?: string;
  position_id?: string;
  status?: string;
  reason?: string | null;
  cash_equity?: number;
  marked_equity?: number;
  open_positions?: number;
};

type PortfolioReport = {
  replay_sha256?: string;
  initial_equity?: number;
  final_cash_equity?: number;
  final_marked_equity?: number;
  maximum_drawdown_rate?: number;
  capacity_reject_count?: number;
  event_log?: ReplayEvent[];
  skipped_signals?: Array<{ signal_id?: string | null; position_id?: string; reason?: string }>;
  closed_positions?: unknown[];
  open_positions?: unknown[];
  cost_attribution?: {
    entry_cost?: number;
    exit_cost?: number;
    modeled_trading_cost?: number;
    net_funding?: number;
  };
};

type BacktestResponse = {
  contract_version: string;
  execution_mode: "SIGNAL_ONLY";
  strategy_equivalent: false;
  claims_allowed: false;
  promotion_allowed: false;
  portfolio_report: PortfolioReport;
  signal_level_report: { row_count?: number; portfolio_realizability_applied?: boolean };
  limitations: string[];
};

type ProductionBundleResponse = {
  contract_version: "backtest_production_bundle_v1";
  execution_mode: "SIGNAL_ONLY";
  strategy_equivalent: false;
  portfolio_events_available: false;
  row_count: number;
  bundle: {
    artifact_key_id: "wfh-backtest-hmac-v1";
    artifact_hmac_sha256: string;
    dataset_manifest_hash: string;
    initial_equity: number;
    events: unknown[];
    signal_rows: unknown[];
  };
};

function number(value: unknown, digits = 2): string {
  return typeof value === "number" && Number.isFinite(value)
    ? value.toLocaleString("en-US", { maximumFractionDigits: digits })
    : "—";
}

function percent(value: unknown): string {
  return typeof value === "number" && Number.isFinite(value)
    ? `${(value * 100).toFixed(2)}%`
    : "—";
}

function EquityCurve({ events }: Readonly<{ events: ReplayEvent[] }>) {
  const points = events
    .map((event) => event.marked_equity)
    .filter((value): value is number => typeof value === "number" && Number.isFinite(value));
  if (points.length < 2) {
    return <p className="py-10 text-center text-sm text-slate-500">At least two processed events are required for an equity curve.</p>;
  }
  const width = 760;
  const height = 180;
  const minimum = Math.min(...points);
  const maximum = Math.max(...points);
  const span = Math.max(maximum - minimum, Math.abs(maximum) * 0.001, 1);
  const path = points.map((value, index) => {
    const x = (index / (points.length - 1)) * width;
    const y = height - ((value - minimum) / span) * height;
    return `${x.toFixed(2)},${y.toFixed(2)}`;
  }).join(" ");
  return (
    <div>
      <svg viewBox={`0 0 ${width} ${height}`} role="img" aria-label="Marked equity across replay events" className="h-48 w-full overflow-visible">
        <defs>
          <linearGradient id="equity-area" x1="0" x2="0" y1="0" y2="1">
            <stop offset="0%" stopColor="#34d399" stopOpacity="0.28" />
            <stop offset="100%" stopColor="#34d399" stopOpacity="0" />
          </linearGradient>
        </defs>
        <polyline points={`0,${height} ${path} ${width},${height}`} fill="url(#equity-area)" stroke="none" />
        <polyline points={path} fill="none" stroke="#34d399" strokeWidth="3" vectorEffect="non-scaling-stroke" />
      </svg>
      <div className="flex justify-between text-xs text-slate-500"><span>Min {number(minimum)}</span><span>Max {number(maximum)}</span></div>
    </div>
  );
}

function parseArray(text: string, name: string): unknown[] {
  const parsed: unknown = JSON.parse(text);
  if (!Array.isArray(parsed)) throw new Error(`${name} must be a JSON array.`);
  if (parsed.length > 5_000) throw new Error(`${name} exceeds the 5,000-row limit.`);
  return parsed;
}

export function BacktestLab() {
  const [manifestHash, setManifestHash] = useState("");
  const [initialEquity, setInitialEquity] = useState("1000");
  const [eventsText, setEventsText] = useState("[]");
  const [signalsText, setSignalsText] = useState("[]");
  const [artifactHmac, setArtifactHmac] = useState("");
  const [result, setResult] = useState<BacktestResponse>();
  const [error, setError] = useState<string>();
  const [running, setRunning] = useState(false);
  const [loadingProduction, setLoadingProduction] = useState(false);
  const inputRevision = useRef(0);

  const validHash = /^[0-9a-f]{64}$/.test(manifestHash);
  const validAttestation = /^[0-9a-f]{64}$/.test(artifactHmac);
  const events = result?.portfolio_report.event_log ?? [];
  const skipped = result?.portfolio_report.skipped_signals ?? [];
  const report = result?.portfolio_report;
  const canRun = validHash && validAttestation && !running && Number(initialEquity) > 0;
  const replayLabel = useMemo(
    () => report?.replay_sha256 ? report.replay_sha256.slice(0, 12) : "not run",
    [report?.replay_sha256],
  );

  const invalidateResult = () => {
    inputRevision.current += 1;
    setResult(undefined);
    setError(undefined);
  };
  const invalidateAttestation = () => {
    invalidateResult();
    setArtifactHmac("");
  };

  const run = async () => {
    setRunning(true);
    setError(undefined);
    const runRevision = inputRevision.current;
    try {
      const request = {
        artifact_key_id: "wfh-backtest-hmac-v1",
        artifact_hmac_sha256: artifactHmac,
        dataset_manifest_hash: manifestHash,
        initial_equity: Number(initialEquity),
        events: parseArray(eventsText, "Events"),
        signal_rows: parseArray(signalsText, "Signal rows"),
      };
      const response = await fetch("/dashboard/api/backtest-lab/replay", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify(request),
      });
      const payload = await response.json() as BacktestResponse | { detail?: unknown };
      if (!response.ok) throw new Error(`Replay rejected: ${JSON.stringify((payload as { detail?: unknown }).detail ?? payload)}`);
      const safe = payload as BacktestResponse;
      if (safe.execution_mode !== "SIGNAL_ONLY" || safe.strategy_equivalent !== false || safe.claims_allowed !== false || safe.promotion_allowed !== false) {
        throw new Error("Unsafe or incompatible replay contract received.");
      }
      if (runRevision === inputRevision.current) setResult(safe);
    } catch (reason) {
      if (runRevision === inputRevision.current) {
        setResult(undefined);
        setError(reason instanceof Error ? reason.message : "Replay failed.");
      }
    } finally {
      setRunning(false);
    }
  };

  const loadProductionBundle = async () => {
    setLoadingProduction(true);
    setError(undefined);
    try {
      const equity = Number(initialEquity);
      if (!Number.isFinite(equity) || equity <= 0) throw new Error("Positive initial equity is required.");
      const response = await fetch(`/dashboard/api/backtest-lab/production-bundle?limit=500&initial_equity=${encodeURIComponent(String(equity))}`, { cache: "no-store" });
      const payload = await response.json() as ProductionBundleResponse | { detail?: unknown };
      if (!response.ok) throw new Error(`Production bundle unavailable: ${JSON.stringify((payload as { detail?: unknown }).detail ?? payload)}`);
      const safe = payload as ProductionBundleResponse;
      if (safe.execution_mode !== "SIGNAL_ONLY" || safe.strategy_equivalent !== false || safe.portfolio_events_available !== false) {
        throw new Error("Unsafe or incompatible production bundle received.");
      }
      const bundle = safe.bundle;
      if (!Array.isArray(bundle.events) || !Array.isArray(bundle.signal_rows)) throw new Error("Production bundle rows are invalid.");
      if (!/^[0-9a-f]{64}$/.test(bundle.dataset_manifest_hash) || !/^[0-9a-f]{64}$/.test(bundle.artifact_hmac_sha256)) {
        throw new Error("Production bundle attestation is invalid.");
      }
      invalidateResult();
      setEventsText(JSON.stringify(bundle.events, null, 2));
      setSignalsText(JSON.stringify(bundle.signal_rows, null, 2));
      setManifestHash(bundle.dataset_manifest_hash);
      setInitialEquity(String(bundle.initial_equity));
      setArtifactHmac(bundle.artifact_hmac_sha256);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Production bundle load failed.");
    } finally {
      setLoadingProduction(false);
    }
  };

  const importDataset = async (event: ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    if (!file) return;
    setError(undefined);
    try {
      if (file.size > 10_000_000) throw new Error("Dataset file exceeds the 10 MB limit.");
      const parsed = JSON.parse(await file.text()) as Record<string, unknown>;
      if (!Array.isArray(parsed.events)) throw new Error("Dataset file must contain an events array.");
      const signalRows = Array.isArray(parsed.signal_rows) ? parsed.signal_rows : [];
      if (parsed.events.length > 5_000) throw new Error("Events exceed the 5,000-row limit.");
      if (signalRows.length > 5_000) throw new Error("Signal rows exceed the 5,000-row limit.");
      if (typeof parsed.dataset_manifest_hash !== "string") throw new Error("Dataset manifest hash is required.");
      if (typeof parsed.initial_equity !== "number" || !Number.isFinite(parsed.initial_equity) || parsed.initial_equity <= 0) throw new Error("Positive initial equity is required.");
      if (parsed.artifact_key_id !== "wfh-backtest-hmac-v1" || typeof parsed.artifact_hmac_sha256 !== "string") throw new Error("Server-verifiable artifact attestation is required.");
      invalidateResult();
      setEventsText(JSON.stringify(parsed.events, null, 2));
      setSignalsText(JSON.stringify(signalRows, null, 2));
      setManifestHash(parsed.dataset_manifest_hash);
      setInitialEquity(String(parsed.initial_equity));
      setArtifactHmac(parsed.artifact_hmac_sha256);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Dataset import failed.");
    } finally {
      event.target.value = "";
    }
  };

  const download = () => {
    if (!result) return;
    const blob = new Blob([JSON.stringify(result, null, 2)], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = `wfh-simulated-replay-${report?.replay_sha256?.slice(0, 12) ?? "report"}.json`;
    anchor.click();
    URL.revokeObjectURL(url);
  };

  return (
    <section id="backtest-lab" className="panel mx-auto mb-7 max-w-7xl scroll-mt-32 border-cyan-500/20 bg-gradient-to-br from-slate-900 via-slate-900 to-cyan-950/20 p-5 sm:p-7" aria-label="Backtest Lab">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <p className="mb-2 text-xs font-semibold uppercase tracking-[0.2em] text-cyan-300">Research workspace</p>
          <h2 className="flex items-center gap-2 text-xl font-semibold"><FlaskConical size={21} />Backtest Lab</h2>
          <p className="mt-2 max-w-3xl text-sm leading-6 text-slate-400">Deterministic replay with the canonical risk policy, portfolio capacity, isolated liquidation and explicit cost attribution. Input data is never inferred or repaired.</p>
        </div>
        <div className="flex flex-wrap gap-2">

          <span className="status-pill border border-cyan-400/30 bg-cyan-500/10 text-cyan-100">SIGNAL ONLY</span>

          <span className="status-pill border border-rose-400/30 bg-rose-500/10 text-rose-100">STRATEGY-EQUIVALENT: FALSE</span>
        </div>
      </div>

      <div className="mt-6 rounded-2xl border border-slate-700/80 bg-slate-950/55 p-4 sm:p-5">
        <div className="grid gap-4 lg:grid-cols-[1fr_13rem]">
          <label className="text-xs font-medium text-slate-300"><span className="block">Dataset manifest SHA-256</span>
            <input value={manifestHash} onChange={(event) => { invalidateAttestation(); setManifestHash(event.target.value.trim()); }} placeholder="64 lowercase hexadecimal characters" spellCheck={false} className={`mt-2 w-full rounded-xl border bg-slate-950 px-3 py-2.5 font-mono text-xs outline-none ${manifestHash && !validHash ? "border-rose-500/60" : "border-slate-700 focus:border-cyan-500"}`} />
          </label>
          <label className="text-xs font-medium text-slate-300"><span className="block">Initial simulated equity</span>
            <input type="number" min="0.01" step="0.01" value={initialEquity} onChange={(event) => { invalidateAttestation(); setInitialEquity(event.target.value); }} className="mt-2 w-full rounded-xl border border-slate-700 bg-slate-950 px-3 py-2.5 text-sm outline-none focus:border-cyan-500" />
          </label>
        </div>
        <label className="mt-4 block text-xs font-medium text-slate-300"><span className="block">Trusted artifact HMAC-SHA256</span>
          <input value={artifactHmac} onChange={(event) => { invalidateResult(); setArtifactHmac(event.target.value.trim()); }} placeholder="Generated by the server-side bundle signer" spellCheck={false} className={`mt-2 w-full rounded-xl border bg-slate-950 px-3 py-2.5 font-mono text-xs outline-none ${artifactHmac && !validAttestation ? "border-rose-500/60" : "border-slate-700 focus:border-cyan-500"}`} />
        </label>
        <div className="mt-4 grid gap-4 lg:grid-cols-2">
          <label className="text-xs font-medium text-slate-300"><span className="block">Portfolio events JSON</span>
            <textarea value={eventsText} onChange={(event) => { invalidateAttestation(); setEventsText(event.target.value); }} spellCheck={false} className="mt-2 h-44 w-full resize-y rounded-xl border border-slate-700 bg-slate-950 p-3 font-mono text-xs leading-5 outline-none focus:border-cyan-500" />
          </label>
          <label className="text-xs font-medium text-slate-300"><span className="block">Signal-level research rows JSON</span>
            <textarea value={signalsText} onChange={(event) => { invalidateAttestation(); setSignalsText(event.target.value); }} spellCheck={false} className="mt-2 h-44 w-full resize-y rounded-xl border border-slate-700 bg-slate-950 p-3 font-mono text-xs leading-5 outline-none focus:border-cyan-500" />
          </label>
        </div>
        <div className="mt-4 flex flex-wrap items-center gap-3">
          <button type="button" disabled={!canRun} onClick={() => void run()} className="inline-flex items-center gap-2 rounded-xl bg-cyan-400 px-4 py-2.5 text-sm font-semibold text-slate-950 transition hover:bg-cyan-300 disabled:cursor-not-allowed disabled:opacity-40"><Play size={16} />{running ? "Running…" : "Run bounded replay"}</button>
          <button type="button" disabled={loadingProduction || running} onClick={() => void loadProductionBundle()} className="inline-flex items-center gap-2 rounded-xl border border-cyan-500/40 bg-cyan-500/10 px-4 py-2.5 text-sm text-cyan-100 transition hover:border-cyan-400 disabled:cursor-not-allowed disabled:opacity-40"><Database size={16} />{loadingProduction ? "Loading production…" : "Load production signals"}</button>
          <label className="inline-flex cursor-pointer items-center gap-2 rounded-xl border border-slate-700 px-4 py-2.5 text-sm text-slate-200 transition hover:border-slate-500"><FileUp size={16} />Import manifest bundle<input type="file" accept="application/json,.json" onChange={(event) => void importDataset(event)} className="sr-only" /></label>
          <button type="button" disabled={!result} onClick={download} className="inline-flex items-center gap-2 rounded-xl border border-slate-700 px-4 py-2.5 text-sm text-slate-200 transition hover:border-slate-500 disabled:cursor-not-allowed disabled:opacity-40"><Download size={16} />Export hash-bound result</button>
          <span className="ml-auto font-mono text-xs text-slate-500">replay {replayLabel}</span>
        </div>
        {error && <p role="alert" className="mt-4 rounded-xl border border-rose-500/30 bg-rose-500/10 px-3 py-2 text-sm text-rose-200">{error}</p>}
      </div>

      {result && report && (
        <div className="mt-6 space-y-5">
          <dl className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-6">
            <div className="stat"><dt>Final marked equity</dt><dd>{number(report.final_marked_equity)}</dd></div>
            <div className="stat"><dt>Maximum drawdown</dt><dd className="text-amber-200">{percent(report.maximum_drawdown_rate)}</dd></div>
            <div className="stat"><dt>Closed / open</dt><dd>{number(report.closed_positions?.length, 0)} / {number(report.open_positions?.length, 0)}</dd></div>
            <div className="stat"><dt>Skipped</dt><dd>{number(skipped.length, 0)}</dd></div>
            <div className="stat"><dt>Capacity rejects</dt><dd>{number(report.capacity_reject_count, 0)}</dd></div>
            <div className="stat"><dt>Signal rows</dt><dd>{number(result.signal_level_report.row_count, 0)}</dd></div>
          </dl>

          <div className="grid gap-4 lg:grid-cols-[1.6fr_1fr]">
            <div className="rounded-2xl border border-slate-800 bg-slate-950/50 p-4"><h3 className="text-sm font-semibold">Marked-equity path</h3><EquityCurve events={events} /></div>
            <div className="rounded-2xl border border-slate-800 bg-slate-950/50 p-4">
              <h3 className="text-sm font-semibold">Cost attribution</h3>
              <dl className="mt-4 space-y-3 text-sm">
                <div className="flex justify-between"><dt className="text-slate-500">Entry cost</dt><dd>{number(report.cost_attribution?.entry_cost, 6)}</dd></div>
                <div className="flex justify-between"><dt className="text-slate-500">Exit cost</dt><dd>{number(report.cost_attribution?.exit_cost, 6)}</dd></div>
                <div className="flex justify-between border-t border-slate-800 pt-3"><dt className="text-slate-400">Modeled trading cost</dt><dd className="font-semibold">{number(report.cost_attribution?.modeled_trading_cost, 6)}</dd></div>
                <div className="flex justify-between"><dt className="text-slate-500">Net funding</dt><dd>{number(report.cost_attribution?.net_funding, 6)}</dd></div>
              </dl>
            </div>
          </div>

          <div className="overflow-hidden rounded-2xl border border-slate-800">
            <div className="flex items-center justify-between border-b border-slate-800 bg-slate-950/60 px-4 py-3"><h3 className="text-sm font-semibold">Event drilldown</h3><span className="text-xs text-slate-500">{events.length.toLocaleString()} processed</span></div>
            <div className="max-h-80 overflow-auto">
              <table className="w-full min-w-[760px] text-left text-xs"><thead className="sticky top-0 bg-slate-900 text-slate-500"><tr><th className="px-4 py-3">Time</th><th className="px-4 py-3">Event</th><th className="px-4 py-3">Position</th><th className="px-4 py-3">Status</th><th className="px-4 py-3">Reason</th><th className="px-4 py-3 text-right">Marked equity</th></tr></thead><tbody className="divide-y divide-slate-800">{events.map((event, index) => <tr key={`${event.event_id ?? "event"}-${index}`}><td className="px-4 py-3 tabular-nums text-slate-400">{number(event.occurred_at, 0)}</td><td className="px-4 py-3 font-medium">{event.event_type ?? "—"}</td><td className="px-4 py-3 font-mono text-slate-400">{event.position_id ?? "—"}</td><td className="px-4 py-3">{event.status ?? "—"}</td><td className="px-4 py-3 text-slate-400">{event.reason ?? "—"}</td><td className="px-4 py-3 text-right tabular-nums">{number(event.marked_equity)}</td></tr>)}</tbody></table>
            </div>
          </div>
        </div>
      )}

      <p className="mt-6 flex items-start gap-2 border-t border-slate-800 pt-4 text-xs leading-5 text-slate-400"><ShieldAlert size={16} className="mt-0.5 shrink-0 text-rose-300" />A successful replay proves deterministic computation for the supplied manifest only. It does not prove dataset provenance, strategy equivalence, out-of-sample validity, profitability, or permission to promote or trade.</p>
    </section>
  );
}
