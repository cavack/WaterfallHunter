import { BarChart3, Database, Waves } from "lucide-react";
import { asRecord, Candidate, isLiveCandidate } from "@/components/score-card";

function finiteNumber(value: unknown): number | undefined {
  return typeof value === "number" && Number.isFinite(value) ? value : undefined;
}

function valueText(value: unknown, digits = 2, prefix = ""): string {
  const number = finiteNumber(value);
  return number === undefined ? "—" : `${prefix}${number.toFixed(digits)}`;
}

function percentageText(value: unknown, digits: number): string {
  const number = finiteNumber(value);
  return number === undefined ? "—" : `${(number * 100).toFixed(digits)}%`;
}

function stringText(value: unknown): string {
  return typeof value === "string" ? value : "—";
}

function approvalText(value: unknown): string {
  if (value === true) return "yes";
  if (value === false) return "no";
  return "—";
}

function Metric({ label, value }: { label: string; value: string }) {
  return <div className="flex items-center justify-between gap-3 border-b border-slate-800/80 py-2 last:border-0"><dt className="text-slate-400">{label}</dt><dd className="shrink-0 font-mono text-slate-200">{value}</dd></div>;
}

export function MarketContext({ candidate, hasFreshSnapshot }: { candidate: Candidate; hasFreshSnapshot: boolean }) {
  const live = isLiveCandidate(candidate, hasFreshSnapshot);
  const metrics = live ? asRecord(candidate.metrics) : undefined;
  const microstructure = asRecord(metrics?.microstructure);
  const derivatives = asRecord(metrics?.derivatives);
  const derivativesAvailable = derivatives?.available === true;
  const volume = live ? candidate.quote_volume : undefined;

  return <div className="grid gap-3 border-t border-slate-800 bg-slate-950/35 p-5 text-sm sm:p-6 lg:grid-cols-3">
    <ExecutionMetrics volume={volume} microstructure={microstructure} />
    <DerivativeMetrics derivatives={derivatives} available={derivativesAvailable} />
    <ProvenanceMetrics metrics={metrics} derivatives={derivatives} microstructure={microstructure} />
  </div>;
}

function ExecutionMetrics({ volume, microstructure }: { volume: unknown; microstructure: Record<string, unknown> | undefined }) {
  return <section className="metric-card"><p className="mb-2 flex items-center gap-2 text-xs font-medium uppercase tracking-wide text-slate-400"><Waves size={14} /> Execution</p><dl>
    <Metric label="24h futures volume" value={valueText(volume, 0, "$")} />
    <Metric label="Spread" value={valueText(microstructure?.spread_pct, 3)} />
    <Metric label="Slippage" value={valueText(microstructure?.slippage_pct, 3)} />
    <Metric label="Sell flow" value={valueText(microstructure?.sell_flow_usdt, 0, "$")} />
  </dl></section>;
}

function DerivativeMetrics({ derivatives, available }: { derivatives: Record<string, unknown> | undefined; available: boolean }) {
  return <section className="metric-card"><p className="mb-2 flex items-center gap-2 text-xs font-medium uppercase tracking-wide text-slate-400"><BarChart3 size={14} /> Derivatives</p>
    {available ? <DerivativePacket derivatives={derivatives} /> : <DerivativeUnavailable derivatives={derivatives} />}
  </section>;
}

function DerivativePacket({ derivatives }: { derivatives: Record<string, unknown> | undefined }) {
  return <dl>
    <Metric label="Source" value={stringText(derivatives?.source_exchange)} />
    <Metric label="Funding" value={percentageText(derivatives?.funding_rate, 4)} />
    <Metric label="Funding percentile" value={percentageText(derivatives?.funding_percentile, 2)} />
    <Metric label="OI change (1h)" value={valueText(derivatives?.oi_change_1h_pct, 3)} />
    <Metric label="Taker buy/sell" value={valueText(derivatives?.taker_buy_sell_ratio, 4)} />
    <Metric label="Taker ratio Δ (1h)" value={valueText(derivatives?.taker_ratio_change_1h, 4)} />
    <Metric label="Top trader L/S" value={valueText(derivatives?.top_trader_long_short_ratio, 4)} />
  </dl>;
}

function DerivativeUnavailable({ derivatives }: { derivatives: Record<string, unknown> | undefined }) {
  const reason = typeof derivatives?.reason === "string" && derivatives.reason ? derivatives.reason : "Derivative packet unavailable.";
  return <p className="rounded-lg border border-slate-800 bg-slate-900/70 p-3 text-xs leading-5 text-slate-400">{reason}</p>;
}

function ProvenanceMetrics({ metrics, derivatives, microstructure }: { metrics: Record<string, unknown> | undefined; derivatives: Record<string, unknown> | undefined; microstructure: Record<string, unknown> | undefined }) {
  return <section className="metric-card"><p className="mb-2 flex items-center gap-2 text-xs font-medium uppercase tracking-wide text-slate-400"><Database size={14} /> Data provenance</p><dl>
    <Metric label="Selected exchange" value={stringText(metrics?.exchange)} />
    <Metric label="Contract" value={stringText(metrics?.mapped_symbol)} />
    <Metric label="Derivative market ID" value={stringText(derivatives?.market_id)} />
    <Metric label="Orderbook approved" value={approvalText(microstructure?.approved)} />
  </dl></section>;
}
