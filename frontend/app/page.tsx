"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { Activity, Wifi, WifiOff, Clock3, Zap, Target, ChevronDown, BarChart3, FlaskConical, DollarSign, TrendingUp, TrendingDown, Shield } from "lucide-react";
import { Candidate } from "@/components/score-card";
import { DecisionTerminal, CandidateTable } from "@/components/decision-terminal";
import { OutcomeEvidence } from "@/components/outcome-evidence";
import { RecentSignals } from "@/components/recent-signals";
import { HistoricalOutcomes } from "@/components/historical-outcomes";
import { ProductionEvidence } from "@/components/production-evidence";
import { FeatureReplay } from "@/components/feature-replay";
import { LifecycleShadow } from "@/components/lifecycle-shadow";
import { BacktestLab } from "@/components/backtest-lab";
import { SignalFunnel, SignalFunnelData } from "@/components/signal-funnel";
import { FinalRanking } from "@/components/final-ranking";
import type { DashboardSnapshot } from "@/generated/dashboard-contract";
import { dashboardSnapshot, dashboardStreamEvent } from "@/lib/dashboard-contract";
import { summarizeCandidateFreshness } from "@/lib/decision-terminal-ui";

type ConnectionMode = "stream" | "polling" | "reconnecting";

function boundedJitter(maximum: number): number {
  const sample = new Uint32Array(1);
  globalThis.crypto.getRandomValues(sample);
  return Math.floor((sample[0] / 0xffffffff) * maximum);
}

/* ─── Helpers ─── */
function getMetrics(c: Candidate): Record<string, unknown> | undefined {
  const m = c.metrics;
  return m !== null && typeof m === "object" && !Array.isArray(m) ? (m as Record<string, unknown>) : undefined;
}
function getED(c: Candidate): Record<string, unknown> | undefined {
  const m = getMetrics(c);
  const ed = m?.entry_decision;
  return ed !== null && typeof ed === "object" && !Array.isArray(ed) ? (ed as Record<string, unknown>) : undefined;
}
function getReadiness(c: Candidate): number {
  const r = getED(c)?.entry_readiness;
  return typeof r === "number" && Number.isFinite(r) ? r : 0;
}
function getDecision(c: Candidate): string {
  return (getED(c)?.decision as string) ?? "—";
}
function getTradePlan(c: Candidate): Record<string, unknown> | null {
  const tp = getED(c)?.trade_plan;
  return tp !== null && typeof tp === "object" && !Array.isArray(tp) ? (tp as Record<string, unknown>) : null;
}
function getReasons(c: Candidate): string[] {
  const r = getED(c)?.reason_codes;
  return Array.isArray(r) ? r.filter((x): x is string => typeof x === "string") : [];
}

/* Smart number formatter - enough precision for any price */
function fmt(v: number | undefined): string {
  if (v === undefined || v === null || !Number.isFinite(v)) return "—";
  if (v === 0) return "0";
  const abs = Math.abs(v);
  if (abs >= 1000) return v.toLocaleString(undefined, { maximumFractionDigits: 2 });
  if (abs >= 1) return v.toFixed(4);
  if (abs >= 0.01) return v.toFixed(5);
  if (abs >= 0.0001) return v.toFixed(7);
  return v.toFixed(10);
}

/* ─── Signal Card - compact, no wasted space ─── */
function SignalCard({ symbol, candidate }: Readonly<{ symbol: string; candidate: Candidate }>) {
  const ed = getED(candidate);
  const tp = getTradePlan(candidate);
  if (!ed || !tp) return null;

  const shortName = symbol.replace("/USDT:USDT", "").replace("/USDT", "");
  const ep = tp.entry_price as number | undefined;
  const sl = tp.stop_loss as number | undefined;
  const tp1 = tp.take_profit_1 as number | undefined;
  const tp2 = tp.take_profit_2 as number | undefined;
  const r2r = tp.reward_to_risk as number | undefined;
  const readiness = (ed.entry_readiness as number) ?? 0;
  const es = ed.evidence_summary as Record<string, unknown> | undefined;
  const cascade = es?.cascade as Record<string, unknown> | undefined;
  const cross = es?.cross_exchange_confirmed as boolean | undefined;
  const reasons = getReasons(candidate);
  const ai = getMetrics(candidate)?.ai_advisory as Record<string, unknown> | undefined;
  const aiAdvice = (ai?.ai_advice as string) ?? "";
  const aiProvider = (ai?.ai_provider as string) ?? "";

  // Only show AI if it actually has advice
  const hasAI = aiAdvice && aiAdvice !== "—" && aiAdvice !== "UNAVAILABLE" && aiAdvice !== "PENDING" && aiAdvice !== "ERROR";

  const r2rStr = r2r !== undefined ? `1:${r2r.toFixed(1)}` : "";
  const riskPct = ep && sl && ep > 0 ? Math.abs((ep - sl) / ep * 100).toFixed(1) : "—";

  const decColor = readiness >= 55 ? "text-emerald-400" : readiness >= 40 ? "text-sky-400" : "text-amber-400";
  const decBg = readiness >= 55 ? "bg-emerald-500/10 border-emerald-500/20" : readiness >= 40 ? "bg-sky-500/10 border-sky-500/20" : "bg-amber-500/10 border-amber-500/20";

  return (
    <div className={`rounded-xl border ${decBg} p-4 transition-all hover:scale-[1.01] hover:shadow-lg hover:shadow-black/20`}>
      {/* Header: symbol + readiness badge */}
      <div className="flex items-center justify-between mb-3">
        <div className="flex items-center gap-2">
          <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-slate-800/60 text-xs font-bold text-white">
            {shortName.slice(0, 3)}
          </div>
          <div>
            <span className="text-base font-bold text-white">{shortName}</span>
            <span className="ml-1.5 text-[10px] text-slate-500">{String(candidate.status ?? "—")}</span>
          </div>
        </div>
        <div className={`rounded-lg px-2.5 py-1 text-center ${decColor} bg-slate-900/50`}>
          <span className="font-mono text-lg font-bold">{readiness.toFixed(0)}</span>
          <span className={`text-[9px] block leading-none ${readiness >= 70 ? "text-emerald-400" : "text-sky-400"}`}>{readiness >= 70 ? "READY" : "FORMING"}</span>
        </div>
      </div>

      {/* Trade plan - compact inline, no gaps */}
      <div className="grid grid-cols-3 gap-1.5 mb-2">
        <div className="rounded-lg bg-sky-500/5 border border-sky-500/10 px-2.5 py-2">
          <div className="text-[9px] uppercase text-sky-400/60 mb-0.5">Entry</div>
          <div className="font-mono text-sm font-bold text-sky-300">{fmt(ep)}</div>
        </div>
        <div className="rounded-lg bg-rose-500/5 border border-rose-500/10 px-2.5 py-2">
          <div className="text-[9px] uppercase text-rose-400/60 mb-0.5">Stop</div>
          <div className="font-mono text-sm font-bold text-rose-300">{fmt(sl)}</div>
        </div>
        <div className="rounded-lg bg-emerald-500/5 border border-emerald-500/10 px-2.5 py-2">
          <div className="text-[9px] uppercase text-emerald-400/60 mb-0.5">Target</div>
          <div className="font-mono text-sm font-bold text-emerald-300">{fmt(tp1)}</div>
        </div>
      </div>

      {/* Badges - compact, full width */}
      <div className="flex flex-wrap gap-1.5 text-[10px]">
        {r2rStr && <span className="rounded bg-slate-800/60 px-2 py-0.5 text-slate-300 font-mono">{r2rStr}</span>}
        <span className="rounded bg-slate-800/60 px-2 py-0.5 text-slate-300 font-mono">Risk {riskPct}%</span>
        {tp2 !== undefined && tp2 !== null && <span className="rounded bg-slate-800/60 px-2 py-0.5 text-slate-300 font-mono">TP2 {fmt(tp2)}</span>}
        {cross !== undefined && (
          <span className={`rounded px-2 py-0.5 font-mono ${cross ? "bg-emerald-500/10 text-emerald-400" : "bg-slate-800/40 text-slate-500"}`}>
            Cross {cross ? "✓" : "—"}
          </span>
        )}
        {cascade && <span className={`rounded px-2 py-0.5 font-mono ${String(cascade.status) === "PASS" ? "bg-emerald-500/10 text-emerald-400" : "bg-rose-500/10 text-rose-400"}`}>{String(cascade.status ?? "?")}</span>}
        {hasAI && (
          <span className={`rounded px-2 py-0.5 font-mono ${
            aiAdvice === "LONG" || aiAdvice === "GO" ? "bg-emerald-500/10 text-emerald-400" :
            aiAdvice === "SHORT" || aiAdvice === "AVOID" ? "bg-rose-500/10 text-rose-400" :
            "bg-amber-500/10 text-amber-400"
          }`}>AI {aiAdvice}</span>
        )}
      </div>
    </div>
  );
}

/* ─── Market Overview - compact ─── */
function MarketOverview({ candidates }: Readonly<{ candidates: Record<string, Candidate> }>) {
  const btc = candidates["BTC/USDT:USDT"];
  const eth = candidates["ETH/USDT:USDT"];
  const btcPrice = typeof btc?.last_price === "number" ? btc.last_price : undefined;
  const ethPrice = typeof eth?.last_price === "number" ? eth.last_price : undefined;
  const all = Object.values(candidates);
  const fuelRich = all.filter((c) => c.status === "FUEL-RICH").length;
  const watch = all.filter((c) => c.status === "WATCH").length;
  const bearish = fuelRich > watch;
  const entryReady = all.filter((c) => getDecision(c) === "ENTRY_READY" || getDecision(c) === "ACTIVE").length;
  const forming = all.filter((c) => getDecision(c) === "FORMING").length;

  const fmtPrice = (v: number | undefined) => {
    if (v === undefined) return "—";
    if (v >= 1000) return `$${v.toLocaleString(undefined, { maximumFractionDigits: 0 })}`;
    if (v >= 1) return `$${v.toFixed(2)}`;
    return `$${v.toFixed(4)}`;
  };

  return (
    <div className="grid grid-cols-3 gap-2 sm:grid-cols-6">
      <div className="rounded-lg border border-amber-500/15 bg-amber-500/5 p-2.5">
        <div className="text-[10px] text-amber-400/60">BTC</div>
        <div className="mt-0.5 font-mono text-sm font-bold text-amber-200">{fmtPrice(btcPrice)}</div>
      </div>
      <div className="rounded-lg border border-indigo-500/15 bg-indigo-500/5 p-2.5">
        <div className="text-[10px] text-indigo-400/60">ETH</div>
        <div className="mt-0.5 font-mono text-sm font-bold text-indigo-200">{fmtPrice(ethPrice)}</div>
      </div>
      <div className="rounded-lg border border-slate-700/30 bg-slate-800/30 p-2.5">
        <div className="text-[10px] text-slate-400">Market</div>
        <div className={`mt-0.5 text-sm font-bold ${bearish ? "text-rose-400" : "text-amber-400"}`}>
          {bearish ? "Bear" : "Neutral"}
        </div>
      </div>
      <div className="rounded-lg border border-slate-700/30 bg-slate-800/30 p-2.5">
        <div className="text-[10px] text-slate-400">Tracked</div>
        <div className="mt-0.5 font-mono text-sm font-bold text-slate-200">{all.length}</div>
      </div>
      <div className="rounded-lg border border-emerald-500/15 bg-emerald-500/5 p-2.5">
        <div className="text-[10px] text-emerald-400/60">Ready</div>
        <div className="mt-0.5 font-mono text-sm font-bold text-emerald-300">{entryReady}</div>
      </div>
      <div className="rounded-lg border border-sky-500/15 bg-sky-500/5 p-2.5">
        <div className="text-[10px] text-sky-400/60">Forming</div>
        <div className="mt-0.5 font-mono text-sm font-bold text-sky-300">{forming}</div>
      </div>
    </div>
  );
}

/* ─── Top Candidates - compact list ─── */
function TopCandidates({ rows, excludeSymbols }: Readonly<{ rows: [string, Candidate][]; excludeSymbols: Set<string> }>) {
  // Only show candidates with meaningful readiness, sorted, limited to 5
  const top5 = rows
    .filter(([sym]) => !excludeSymbols.has(sym))
    .filter(([, c]) => getReadiness(c) >= 40)
    .sort(([, a], [, b]) => getReadiness(b) - getReadiness(a))
    .slice(0, 5);

  if (top5.length === 0) return null;

  const decColor = (dec: string) =>
    dec === "ENTRY_READY" || dec === "ACTIVE" ? "text-emerald-400" : dec === "FORMING" ? "text-sky-400" : dec === "LATE" ? "text-amber-400" : "text-slate-400";

  return (
    <section>
      <h2 className="mb-3 text-xs font-semibold uppercase tracking-wide text-slate-500">
        <BarChart3 size={13} className="mr-1.5 inline text-sky-400" /> Top Candidates
      </h2>
      <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-5">
        {top5.map(([symbol, candidate]) => {
          const r = getReadiness(candidate);
          const dec = getDecision(candidate);
          const tp = getTradePlan(candidate);
          const shortName = symbol.replace("/USDT:USDT", "").replace("/USDT", "");

          return (
            <div key={symbol} className="rounded-lg border border-slate-800/60 bg-slate-900/50 p-3">
              <div className="flex items-center justify-between mb-1.5">
                <span className="font-bold text-sm text-white">{shortName}</span>
                <span className={`text-[10px] font-semibold ${decColor(dec)}`}>{dec}</span>
              </div>
              <div className="flex items-baseline justify-between mb-2">
                <span className="font-mono text-lg font-bold text-emerald-400">{r.toFixed(0)}</span>
                <span className="text-[10px] text-slate-500">{String(candidate.status ?? "—")}</span>
              </div>
              {tp && (
                <div className="grid grid-cols-3 gap-1 text-[10px]">
                  <div><span className="text-slate-500">EP</span> <span className="font-mono text-sky-300">{fmt(tp.entry_price as number)}</span></div>
                  <div><span className="text-slate-500">SL</span> <span className="font-mono text-rose-300">{fmt(tp.stop_loss as number)}</span></div>
                  <div><span className="text-slate-500">TP</span> <span className="font-mono text-emerald-300">{fmt(tp.take_profit_1 as number)}</span></div>
                </div>
              )}
            </div>
          );
        })}
      </div>
    </section>
  );
}

/* ─── Backtester Results ─── */
function BacktesterResults() {
  const [data, setData] = useState<Record<string, unknown> | null>(null);

  useEffect(() => {
    const load = async () => {
      try {
        const r = await fetch(`/api/backtest/results`);
        if (r.ok) setData(await r.json());
      } catch { /* ignore */ }
    };
    load();
    const t = setInterval(load, 60000);
    return () => clearInterval(t);
  }, []);

  if (!data) return null;
  const stats = (data.stats ?? {}) as Record<string, unknown>;
  if (Object.keys(stats).length === 0) return null;

  return (
    <section>
      <h2 className="mb-3 text-xs font-semibold uppercase tracking-wide text-slate-500">
        <DollarSign size={13} className="mr-1.5 inline text-amber-400" /> Backtester · $100 Capital · 4-14x Leverage
      </h2>
      <div className="grid grid-cols-3 gap-2 sm:grid-cols-6">
        <div className="rounded-lg border border-slate-800/60 bg-slate-900/50 p-2.5">
          <div className="text-[10px] text-slate-500">Capital</div>
          <div className="mt-0.5 font-mono text-sm font-bold text-amber-300">${Number(stats.current_capital ?? 200).toFixed(2)}</div>
        </div>
        <div className="rounded-lg border border-slate-800/60 bg-slate-900/50 p-2.5">
          <div className="text-[10px] text-slate-500">Trades</div>
          <div className="mt-0.5 font-mono text-sm font-bold text-slate-200">{String(stats.total_trades ?? 0)}</div>
        </div>
        <div className="rounded-lg border border-slate-800/60 bg-slate-900/50 p-2.5">
          <div className="text-[10px] text-slate-500">Win Rate</div>
          <div className="mt-0.5 font-mono text-sm font-bold text-emerald-400">{Number(stats.win_rate ?? 0).toFixed(0)}%</div>
        </div>
        <div className="rounded-lg border border-slate-800/60 bg-slate-900/50 p-2.5">
          <div className="text-[10px] text-slate-500">Max DD</div>
          <div className="mt-0.5 font-mono text-sm font-bold text-rose-400">{Number(stats.max_drawdown_pct ?? 0).toFixed(1)}%</div>
        </div>
        <div className="rounded-lg border border-slate-800/60 bg-slate-900/50 p-2.5">
          <div className="text-[10px] text-slate-500">PF</div>
          <div className="mt-0.5 font-mono text-sm font-bold text-sky-300">{Number(stats.profit_factor ?? 0).toFixed(2)}</div>
        </div>
        <div className="rounded-lg border border-slate-800/60 bg-slate-900/50 p-2.5">
          <div className="text-[10px] text-slate-500">Sharpe</div>
          <div className="mt-0.5 font-mono text-sm font-bold text-violet-300">{Number(stats.sharpe_ratio ?? 0).toFixed(2)}</div>
        </div>
      </div>
    </section>
  );
}

/* ─── Main Dashboard ─── */
export default function Dashboard() {
  const [data, setData] = useState<DashboardSnapshot | null>(null);
  const [mode, setMode] = useState<ConnectionMode>("reconnecting");
  const [generatedAt, setGeneratedAt] = useState<number | null>(null);
  const [freshnessNow, setFreshnessNow] = useState<number | undefined>(undefined);
  const [researchOpen, setResearchOpen] = useState(false);
  const latestVersion = useRef(0);
  const lastStreamEventAt = useRef(0);

  useEffect(() => {
    const t = setInterval(() => setFreshnessNow(Date.now() / 1000), 5000);
    return () => clearInterval(t);
  }, []);

  useEffect(() => {
    let active = true;
    let pollTimer: ReturnType<typeof setTimeout> | undefined;
    let pollAttempt = 0;
    let streaming = false;
    let hasSnapshot = false;
    let watchdog: ReturnType<typeof setInterval> | undefined;
    const stream = new EventSource("/dashboard/api/stream");

    const accept = (snapshot: DashboardSnapshot) => {
      if (!active || snapshot.snapshot_version <= latestVersion.current) return;
      latestVersion.current = snapshot.snapshot_version;
      hasSnapshot = true;
      setData(snapshot);
      setGeneratedAt(snapshot.generated_at * 1000);
    };

    const schedulePoll = (delay: number) => {
      if (!active || pollTimer !== undefined) return;
      pollTimer = setTimeout(() => {
        pollTimer = undefined;
        if (!active) return;
        void (async () => {
          try {
            const resp = await fetch("/dashboard/api/candidates", { cache: "no-store" });
            const snap = resp.ok ? dashboardSnapshot(await resp.json()) : undefined;
            if (!snap) throw new Error("bad snapshot");
            accept(snap);
            pollAttempt = 0;
            if (streaming) return;
            setMode("polling");
            schedulePoll(5000);
          } catch {
            pollAttempt++;
            if (!streaming) setMode("reconnecting");
            if (!streaming || !hasSnapshot) schedulePoll(Math.min(30000, 1000 * 2 ** Math.min(pollAttempt, 5)));
          }
        })();
      }, delay + (delay > 0 ? boundedJitter(1500) : 0));
    };

    const onMsg = (ev: MessageEvent<string>) => {
      try {
        const pkt = dashboardStreamEvent(JSON.parse(ev.data));
        if (!pkt) throw new Error("bad");
        if (pkt.payload) {
          accept(pkt.payload);
          if (hasSnapshot && pollTimer) {
            clearTimeout(pollTimer);
            pollTimer = undefined;
          }
        }
        lastStreamEventAt.current = Date.now();
        streaming = true;
        setMode("stream");
      } catch {
        streaming = false;
        setMode("reconnecting");
        schedulePoll(1000);
      }
    };

    stream.onopen = () => {
      pollAttempt = 0;
      if (!hasSnapshot) setMode("reconnecting");
    };
    stream.onerror = () => {
      streaming = false;
      setMode("reconnecting");
      schedulePoll(1000);
    };
    stream.onmessage = onMsg;
    stream.addEventListener("snapshot", (e) => e instanceof MessageEvent && onMsg(e as MessageEvent<string>));
    stream.addEventListener("heartbeat", (e) => e instanceof MessageEvent && onMsg(e as MessageEvent<string>));
    schedulePoll(0);
    watchdog = setInterval(() => {
      if (!active || !streaming || lastStreamEventAt.current <= 0) return;
      if (Date.now() - lastStreamEventAt.current > 45000) {
        streaming = false;
        setMode("reconnecting");
        schedulePoll(0);
      }
    }, 5000);

    return () => {
      active = false;
      stream.close();
      if (pollTimer) clearTimeout(pollTimer);
      if (watchdog) clearInterval(watchdog);
    };
  }, []);

  const candidates = (data?.candidates ?? {}) as Record<string, Candidate>;
  const nowSeconds = freshnessNow;

  const rows = useMemo(
    () =>
      Object.entries(candidates).sort(([, a], [, b]) => {
        const ra = typeof a.score === "number" ? a.score : -1;
        const rb = typeof b.score === "number" ? b.score : -1;
        return rb - ra;
      }),
    [candidates],
  );

  const freshnessSummary = useMemo(
    () => summarizeCandidateFreshness(candidates as Record<string, unknown>, freshnessNow),
    [candidates, freshnessNow],
  );

  // Only show high-quality signals: ENTRY_READY + FORMING with readiness >= 45
  const signals = useMemo(
    () =>
      rows.filter(([, c]) => {
        const d = getDecision(c);
        const r = getReadiness(c);
        const es = getED(c)?.evidence_summary as Record<string, unknown> | undefined;
        const casc = es?.cascade as Record<string, unknown> | undefined;
        const cascStatus = (casc?.status as string) ?? "FAIL";
        return (d === "ENTRY_READY" || d === "ACTIVE") || (d === "FORMING" && r >= 45 && cascStatus === "PASS");
      }),
    [rows],
  );

  const signalSymbols = useMemo(() => new Set(signals.map(([s]) => s)), [signals]);

  return (
    <main className="min-h-dvh bg-gradient-to-br from-slate-950 via-slate-900 to-slate-950 pb-20 text-slate-100">
      {/* Subtle texture overlay */}
      <div className="pointer-events-none fixed inset-0 bg-[radial-gradient(circle_at_50%_0%,rgba(16,185,129,0.03),transparent_50%)]" />

      {/* ─── Header ─── */}
      <header className="sticky top-0 z-40 border-b border-slate-800/40 bg-slate-950/80 backdrop-blur-xl">
        <div className="mx-auto flex h-14 max-w-7xl items-center gap-3 px-4 sm:px-6">
          <div className="flex h-7 w-7 items-center justify-center rounded-lg bg-emerald-500/15">
            <Activity size={16} className="text-emerald-400" />
          </div>
          <div className="min-w-0">
            <h1 className="text-base font-bold tracking-tight text-white truncate">WaterfallHunter</h1>
            <p className="text-[10px] text-slate-500 hidden sm:block">Signal Terminal</p>
          </div>
          <div className="ml-auto flex items-center gap-2">
            {generatedAt !== null && (
              <time className="hidden font-mono text-[10px] text-slate-600 md:inline">
                {new Date(generatedAt).toLocaleTimeString()}
              </time>
            )}
            <span className={`flex items-center gap-1 rounded-full px-2 py-0.5 text-[10px] font-medium ${freshnessSummary.state === "fresh" ? "bg-emerald-500/10 text-emerald-300" : "bg-amber-500/10 text-amber-300"}`}>
              <Clock3 size={10} />
              {freshnessSummary.fresh}/{freshnessSummary.total}
            </span>
            <span className={`flex items-center gap-1 rounded-full px-2 py-0.5 text-[10px] font-medium ${mode === "stream" ? "bg-emerald-500/10 text-emerald-300" : mode === "polling" ? "bg-sky-500/10 text-sky-300" : "bg-amber-500/10 text-amber-300"}`}>
              {mode === "stream" ? <Wifi size={10} /> : <WifiOff size={10} />}
              {mode === "stream" ? "Live" : mode === "polling" ? "Poll" : "Recon"}
            </span>
          </div>
        </div>
      </header>

      <div className="mx-auto max-w-7xl space-y-6 px-4 py-5 sm:px-6">
        {data === null ? (
          <div className="flex min-h-[300px] flex-col items-center justify-center text-center">
            <div className="mb-3 h-3 w-3 animate-pulse rounded-full bg-emerald-400" />
            <p className="text-base font-medium text-slate-300">Loading…</p>
            <p className="mt-1 text-xs text-slate-600">Waiting for stream</p>
          </div>
        ) : (
          <>
            {/* ─── 1. Active Signals ─── */}
            {signals.length > 0 && (
              <section>
                <div className="flex items-center gap-2 mb-3">
                  <Zap size={14} className="text-emerald-400" />
                  <h2 className="text-xs font-semibold uppercase tracking-wide text-emerald-400">
                    Signals ({signals.length})
                  </h2>
                </div>
                <div className="grid gap-2.5 sm:grid-cols-2 lg:grid-cols-3">
                  {signals.map(([symbol, candidate]) => (
                    <SignalCard key={symbol} symbol={symbol} candidate={candidate} />
                  ))}
                </div>
              </section>
            )}

            {/* ─── 2. Market Overview ─── */}
            <section>
              <div className="flex items-center gap-2 mb-3">
                <Activity size={14} className="text-amber-400" />
                <h2 className="text-xs font-semibold uppercase tracking-wide text-slate-500">Market</h2>
              </div>
              <MarketOverview candidates={candidates} />
            </section>

            {/* ─── 3. Top Candidates ─── */}
            <TopCandidates rows={rows} excludeSymbols={signalSymbols} />

            {/* ─── 4. Backtester ─── */}
            <BacktesterResults />

            {/* ─── 5. Decision Terminal ─── */}
            <section>
              <div className="flex items-center gap-2 mb-3">
                <Target size={14} className="text-sky-400" />
                <h2 className="text-xs font-semibold uppercase tracking-wide text-slate-500">Decision Terminal</h2>
              </div>
              <CandidateTable candidates={candidates} nowSeconds={nowSeconds} />
              <div className="mt-4">
                <DecisionTerminal terminal={data.decision_terminal} candidates={candidates} nowSeconds={nowSeconds} />
              </div>
            </section>

            {/* ─── 6. Research (collapsed) ─── */}
            <details
              className="rounded-xl border border-slate-800/40 bg-slate-950/40 overflow-hidden"
              open={researchOpen}
              onToggle={(e) => setResearchOpen(e.currentTarget.open)}
            >
              <summary className="flex cursor-pointer list-none items-center justify-between px-4 py-3 text-xs font-semibold text-slate-500">
                <span className="flex items-center gap-2">
                  <FlaskConical size={13} /> Research & Diagnostics
                </span>
                <ChevronDown size={14} className={`transition-transform ${researchOpen ? "rotate-180" : ""}`} />
              </summary>
              {researchOpen && (
                <div className="border-t border-slate-800/40 px-4 py-4 space-y-5">
                  <OutcomeEvidence />
                  <RecentSignals />
                  <HistoricalOutcomes />
                  <ProductionEvidence />
                  <FeatureReplay />
                  <LifecycleShadow />
                  <BacktestLab />
                  <SignalFunnel funnel={data?.signal_funnel as SignalFunnelData | undefined} />
                  <FinalRanking ranking={data?.final_ranking} />
                </div>
              )}
            </details>
          </>
        )}
      </div>
    </main>
  );
}
