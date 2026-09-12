import { CheckCircle2, CircleAlert, Gauge, MinusCircle, Sparkles, XCircle } from "lucide-react";
import { rawLeveragePresentation } from "../lib/decision-terminal-ui";

export type Candidate = Record<string, unknown>;
type RecordValue = Record<string, unknown>;

export function asRecord(value: unknown): RecordValue | undefined {
  return value !== null && typeof value === "object" && !Array.isArray(value) ? value as RecordValue : undefined;
}

export function isLiveCandidate(candidate: Candidate, hasFreshSnapshot: boolean): boolean {
  return hasFreshSnapshot && candidate.data_status === "live";
}

function finiteNumber(value: unknown): number | undefined {
  return typeof value === "number" && Number.isFinite(value) ? value : undefined;
}

function numberText(value: unknown, digits = 6): string {
  const number = finiteNumber(value);
  return number === undefined ? "—" : number.toFixed(digits);
}

function rawNumberText(value: unknown): string {
  const number = finiteNumber(value);
  return number === undefined ? "—" : String(number);
}

function compactNumberText(value: unknown, digits = 2): string {
  const number = finiteNumber(value);
  return number === undefined ? "—" : number.toLocaleString("en-US", { maximumFractionDigits: digits });
}

/** Price formatting that adapts magnitude: $0.00000412 vs $41,208.5 */
function priceText(value: unknown): string {
  const number = finiteNumber(value);
  if (number === undefined) return "—";
  if (number >= 1000) return number.toLocaleString("en-US", { maximumFractionDigits: 2 });
  if (number >= 1) return number.toLocaleString("en-US", { maximumFractionDigits: 4 });
  const precisioned = number.toPrecision(4);
  // A value like 0.99995 rounds up to "1.000" at four significant digits;
  // keep that magnitude instead of trimming into a wrong one. Zero keeps
  // its decimal-free form ("0"), never the trimmed artifact "0.".
  if (Number(precisioned) >= 1 || !precisioned.includes(".")) return precisioned.replace(/\.?0+$/, "");
  return precisioned.replace(/0+$/, "").replace(/\.$/, "");
}

function stateClass(state: string): string {
  const classes: Record<string, string> = {
    TRIGGERED: "border-rose-400/30 bg-rose-500/15 text-rose-200",
    ARMED: "border-amber-400/30 bg-amber-500/15 text-amber-200",
    REJECTED: "border-slate-600 bg-slate-800 text-slate-300",
    WATCH: "border-sky-400/30 bg-sky-500/15 text-sky-200",
  };
  return classes[state] ?? "border-slate-600 bg-slate-800 text-slate-300";
}

function advisoryText(advisory: RecordValue | undefined): string {
  const advice = advisory?.ai_advice;
  return typeof advice === "string" && advice ? advice : "Advisory unavailable";
}

function candidateState(candidate: Candidate, live: boolean): string {
  if (!live) return "UNAVAILABLE";
  return typeof candidate.status === "string" ? candidate.status : "UNAVAILABLE";
}

function analysisReason(metrics: RecordValue | undefined): string {
  if (typeof metrics?.analysis_reason === "string" && metrics.analysis_reason) return metrics.analysis_reason;
  if (typeof metrics?.error === "string" && metrics.error) return metrics.error;
  return "No current Score V2 analysis was supplied by the backend.";
}

function hasStrictScoreEvidence(live: boolean, metrics: RecordValue | undefined): boolean {
  const components = asRecord(metrics?.score_components);
  return Boolean(
    live
    && metrics?.score_version === "score_v2"
    && metrics?.trade_eligible === true
    && finiteNumber(metrics?.score) !== undefined
    && Object.values(components ?? {}).some((value) => finiteNumber(value) !== undefined)
  );
}

function advisoryReasoning(advisory: RecordValue | undefined): string {
  if (typeof advisory?.ai_reasoning === "string" && advisory.ai_reasoning) return advisory.ai_reasoning;
  return "No advisory reasoning supplied.";
}

function StrictScoreUnavailable({ reason }: { reason: string }) {
  return (
    <div className="mt-4 flex gap-2 rounded-lg border border-amber-500/20 bg-amber-500/10 px-3 py-2.5 text-sm text-amber-100">
      <CircleAlert className="mt-0.5 shrink-0" size={16} />
      <div><span className="font-medium">Strict Score V2 unavailable</span><p className="mt-0.5 break-words text-xs text-amber-100/75">{reason}</p></div>
    </div>
  );
}

function executionStatusClass(status: string): string {
  const classes: Record<string, string> = {
    SUITABLE: "border-emerald-400/25 bg-emerald-500/10 text-emerald-200",
    MARGINAL: "border-amber-400/25 bg-amber-500/10 text-amber-200",
    POOR: "border-rose-400/25 bg-rose-500/10 text-rose-200",
    UNKNOWN: "border-slate-600 bg-slate-800 text-slate-300",
  };
  return classes[status] ?? classes.UNKNOWN;
}

function ExecutionSuitability({ packet }: { packet: RecordValue | undefined }) {
  const status = typeof packet?.status === "string" ? packet.status : "UNKNOWN";
  const reason = typeof packet?.reason === "string" && packet.reason
    ? packet.reason
    : "Historical execution evidence is unavailable.";
  const evidenceStatus = typeof packet?.evidence_status === "string" ? packet.evidence_status : "UNAVAILABLE";
  const samples = finiteNumber(packet?.observed_samples);
  const spanHours = finiteNumber(packet?.observation_span_hours);
  const failedChecks = Array.isArray(packet?.failed_checks)
    ? packet.failed_checks.filter((value): value is string => typeof value === "string")
    : [];

  return (
    <section className="mt-4 rounded-xl border border-slate-800 bg-slate-950/50 p-4" aria-label="Execution suitability">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <p className="flex items-center gap-2 text-xs font-semibold uppercase tracking-[0.14em] text-slate-400">
          <Gauge size={15} /> Execution suitability
        </p>
        <span className={`status-pill border ${executionStatusClass(status)}`}>{status}</span>
      </div>
      <p className="mt-2 text-sm leading-5 text-slate-200">{reason}</p>
      <p className="mt-1 text-xs text-slate-500">
        Evidence {evidenceStatus.toLowerCase()} · {samples === undefined ? "—" : compactNumberText(samples, 0)} samples · {spanHours === undefined ? "—" : `${compactNumberText(spanHours, 1)}h`} span
      </p>
      <dl className="mt-3 grid grid-cols-3 gap-2 text-xs">
        <div className="metric-card"><dt className="text-slate-500">Cost $100 p90</dt><dd className="mt-1 font-mono text-slate-100">{finiteNumber(packet?.cost_100_p90_pct) === undefined ? "—" : `${compactNumberText(packet?.cost_100_p90_pct, 4)}%`}</dd></div>
        <div className="metric-card"><dt className="text-slate-500">Spread p90</dt><dd className="mt-1 font-mono text-slate-100">{finiteNumber(packet?.spread_p90_pct) === undefined ? "—" : `${compactNumberText(packet?.spread_p90_pct, 4)}%`}</dd></div>
        <div className="metric-card"><dt className="text-slate-500">Depth ±25bps p50</dt><dd className="mt-1 font-mono text-slate-100">{finiteNumber(packet?.depth_25bps_p50_usdt) === undefined ? "—" : `$${compactNumberText(packet?.depth_25bps_p50_usdt, 0)}`}</dd></div>
      </dl>
      {failedChecks.length > 0 ? <p className="mt-2 break-words text-xs text-rose-200/80">Failed checks: {failedChecks.join(", ").replaceAll("_", " ")}</p> : null}
      <p className="mt-3 border-t border-slate-800 pt-2 text-xs text-slate-500">Observational only · does not change score, state, or trade eligibility.</p>
    </section>
  );
}

function CandidateHeader({ symbol, candidate, live, state }: {
  symbol: string;
  candidate: Candidate;
  live: boolean;
  state: string;
}) {
  return (
    <div className="flex flex-wrap items-start justify-between gap-x-4 gap-y-3">
      <div className="min-w-0">
        {/* Break the contract suffix onto its own line on phones so long
            symbols never overflow the card. */}
        <p className="break-all font-mono text-base font-semibold text-sky-300 sm:text-lg">
          {symbol.split(":")[0]}
          {symbol.includes(":") ? <span className="text-slate-500">:{symbol.split(":").slice(1).join(":")}</span> : null}
        </p>
        <p className="mt-1 text-2xl font-semibold tabular-nums sm:text-3xl">${priceText(live ? candidate.last_price : undefined)}</p>
        <p className="mt-1 text-xs text-slate-500">{live ? `ref ${numberText(candidate.reference_age_seconds, 1)}s · analysis ${numberText(candidate.analysis_age_seconds, 1)}s` : "live reference unavailable"}</p>
      </div>
      <span className={`status-pill ${stateClass(state)}`}>{state}</span>
    </div>
  );
}

function ScoreSummary({ ready, watchScore, coverage, derivativePressure, takerRatio, score, leverageText }: {
  ready: boolean;
  watchScore: number | undefined;
  coverage: number | undefined;
  derivativePressure: number | undefined;
  takerRatio: number | undefined;
  score: number | undefined;
  leverageText: string;
}) {
  const partial = !ready && watchScore !== undefined;
  const displayedScore = ready ? score : watchScore;
  return (
    <section className="score-summary mt-5">
      <div className="min-w-0">
        <p className="text-xs font-semibold uppercase tracking-[0.16em] text-slate-400">{ready ? "Score V2" : partial ? "Watch score · partial" : "Score V2"}</p>
        <p className="mt-1 text-4xl font-semibold tabular-nums text-slate-50">{displayedScore === undefined ? "—" : rawNumberText(displayedScore)}<span className="ml-1 text-base font-medium text-slate-500">/100</span></p>
        <p className="mt-1 text-xs text-slate-400">{ready ? "Complete live evidence" : partial ? `${coverage === undefined ? "Partial" : `${rawNumberText(coverage)}%`} evidence coverage · not trade eligible` : "Awaiting complete live evidence"}</p>
        {partial && derivativePressure !== undefined ? <p className="mt-2 text-xs text-slate-300">Derivative short pressure <span className="font-mono">{rawNumberText(derivativePressure)}/15</span>{takerRatio === undefined ? "" : takerRatio < 1 ? " · sell dominance confirmed" : " · buyers still active"}</p> : null}
      </div>
      <div className="shrink-0 text-right">
        <p className="text-xs font-semibold uppercase tracking-[0.12em] text-slate-500">Leverage</p>
        <p className="mt-1 font-mono text-lg font-medium text-slate-100">{leverageText}</p>
      </div>
    </section>
  );
}

function Advisory({ advisory, confidence }: { advisory: RecordValue | undefined; confidence: number | undefined }) {
  if (!advisory) {
    return <p className="mt-4 border-t border-slate-800 pt-3 text-xs text-slate-500"><Sparkles className="mr-1 inline-block" size={13} />Advisory is requested only after an eligible live setup.</p>;
  }
  return (
    <div className="mt-4 rounded-xl border border-slate-800 bg-slate-950/50 p-3">
      <p className="flex items-center gap-2 text-xs font-medium uppercase tracking-wide text-slate-400"><Sparkles size={14} /> Advisory</p>
      <p className="mt-2 text-sm font-medium text-slate-100">{advisoryText(advisory)}{confidence === undefined ? null : <span className="ml-2 text-slate-400">{rawNumberText(confidence)}%</span>}</p>
      <p className="mt-1 break-words text-xs leading-5 text-slate-400">{advisoryReasoning(advisory)}</p>
    </div>
  );
}

function gateLabel(name: string): string {
  const labels: Record<string, string> = {
    confirmation_exchange_15m: "confirmation exchange 15m",
    composite_breakdown_confirmed: "composite breakdown",
    cross_exchange_confirmed: "composite breakdown gate",
  };
  return labels[name] ?? name.replaceAll("_", " ");
}

function ScoreEvidence({ metrics, strict }: { metrics: RecordValue; strict: boolean }) {
  const watchScore = asRecord(metrics.watch_score);
  const components = strict ? asRecord(metrics.score_components) : asRecord(watchScore?.components);
  const gates = asRecord(metrics.quality_gates);
  const breakdown = asRecord(metrics.breakdown_confirmation);
  const componentRows = Object.entries(components ?? {}).filter(([, value]) => finiteNumber(value) !== undefined);
  const evidenceState = (value: unknown): "PASS" | "FAIL" | "UNAVAILABLE" =>
    value === true ? "PASS" : value === false ? "FAIL" : "UNAVAILABLE";
  const gateRows = Object.entries(gates ?? {})
    .filter(([name]) => name !== "cross_exchange_confirmed")
    .map(([name, value]) => [name, evidenceState(value)] as const);
  const breakdownRows = [
    ["confirmation_exchange_15m", evidenceState(breakdown?.confirmation_exchange_15m)],
    ["composite_breakdown_confirmed", evidenceState(breakdown?.composite_breakdown_confirmed)],
  ] as const;
  const unavailable = !strict && Array.isArray(watchScore?.unavailable_components)
    ? watchScore.unavailable_components.filter((value): value is string => typeof value === "string")
    : [];

  return (
    <div className="mt-4 grid gap-4 lg:grid-cols-2">
      <div>
        <p className="mb-2 text-xs font-medium uppercase tracking-wide text-slate-400">{strict ? "Score components" : "Watch score components"}</p>
        <dl className="space-y-1.5 text-sm">
          {componentRows.map(([name, value]) => <div key={name} className="flex justify-between gap-4"><dt className="text-slate-400">{name.replaceAll("_", " ")}</dt><dd className="font-mono text-slate-100">{rawNumberText(value)}</dd></div>)}
        </dl>
        {unavailable.length > 0 ? <p className="mt-2 text-xs text-slate-500">Unavailable: {unavailable.join(", ").replaceAll("_", " ")}</p> : null}
      </div>
      <div>
        <p className="mb-2 text-xs font-medium uppercase tracking-wide text-slate-400">Quality evidence</p>
        <div className="flex flex-wrap gap-2">
          {[...breakdownRows, ...gateRows].map(([name, state]) => {
            const tone = state === "PASS"
              ? "border-emerald-400/25 bg-emerald-500/10 text-emerald-200"
              : state === "FAIL"
                ? "border-rose-400/25 bg-rose-500/10 text-rose-200"
                : "border-slate-600/40 bg-slate-800/50 text-slate-400";
            let Icon = MinusCircle;
            if (state === "PASS") Icon = CheckCircle2;
            else if (state === "FAIL") Icon = XCircle;
            return <span key={name} className={`inline-flex items-center gap-1 rounded-full border px-2 py-1 text-xs ${tone}`}><Icon size={12} />{state} · {gateLabel(name)}</span>;
          })}
        </div>
      </div>
    </div>
  );
}

export function ScoreCard({ symbol, candidate, hasFreshSnapshot }: { symbol: string; candidate: Candidate; hasFreshSnapshot: boolean }) {
  const live = isLiveCandidate(candidate, hasFreshSnapshot);
  const metrics = live ? asRecord(candidate.metrics) : undefined;
  const advisory = asRecord(metrics?.ai_advisory);
  const strictScore = finiteNumber(metrics?.score);
  const watchScore = asRecord(metrics?.watch_score);
  const partialWatchScore = watchScore?.score_version === "score_v2_watch_v1" ? finiteNumber(watchScore.score) : undefined;
  const coverage = finiteNumber(watchScore?.coverage_pct);
  const derivativePressure = finiteNumber(asRecord(watchScore?.components)?.derivatives_confirmation);
  const takerRatio = finiteNumber(asRecord(metrics?.derivatives)?.taker_buy_sell_ratio);
  const state = candidateState(candidate, live);
  const unavailableReason = analysisReason(metrics);
  const confidence = finiteNumber(advisory?.ai_confidence);
  const leverageText = rawLeveragePresentation(metrics);
  const executionSuitability = asRecord(candidate.execution_suitability);
  const ready = hasStrictScoreEvidence(live, metrics);
  const hasWatchEvidence = partialWatchScore !== undefined && asRecord(watchScore?.components) !== undefined;

  return <article className="p-4 sm:p-6">
    <CandidateHeader symbol={symbol} candidate={candidate} live={live} state={state} />
    <ScoreSummary ready={ready} watchScore={partialWatchScore} coverage={coverage} derivativePressure={derivativePressure} takerRatio={takerRatio} score={strictScore} leverageText={leverageText} />
    <ExecutionSuitability packet={executionSuitability} />
    {ready && metrics ? <ScoreEvidence metrics={metrics} strict /> : null}
    {!ready && hasWatchEvidence && metrics ? <ScoreEvidence metrics={metrics} strict={false} /> : null}
    {!ready ? <StrictScoreUnavailable reason={unavailableReason} /> : null}
    <Advisory advisory={advisory} confidence={confidence} />
  </article>;
}
