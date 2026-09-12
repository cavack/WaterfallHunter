import { AlertTriangle, GitBranch, ShieldCheck, Zap } from "lucide-react";

type TriState = {
  passed?: number;
  failed?: number;
  unavailable?: number;
  evaluated?: number;
  pass_rate?: number | null;
};

export type SignalFunnelData = {
  observational_only?: boolean;
  hard_gating_allowed?: boolean;
  candidate_count?: number;
  lifecycle?: Record<string, number>;
  stage_lifecycle?: {
    version?: string;
    observational_only?: boolean;
    hard_gating_allowed?: boolean;
    availability?: TriState;
    stages?: Record<string, TriState>;
    members?: Record<string, string[]>;
  };
  quality_gates?: Record<string, TriState>;
  breakdown_evidence?: Record<string, TriState>;
  attention?: {
    required?: boolean;
    cross_exchange_systemic_zero?: boolean;
    reason?: string | null;
  };
};

const states = ["WATCH", "FUEL-RICH", "PRE-TRIGGER", "ARMED", "TRIGGERED"];
const lifecycleStages = [
  ["hype", "Hype"],
  ["damage", "Damage"],
  ["setup", "Setup"],
  ["trigger", "Current trigger"],
  ["passed", "Lifecycle passed"],
] as const;
const gates = [
  ["channel_stage_chain", "Snapshot stage gate"],
  ["microstructure_approved", "Microstructure"],
  ["complete_fresh_derivatives_packet", "Derivatives"],
  ["taker_sell_dominance", "Sell dominance"],
] as const;

const breakdownChecks = [
  ["primary_breakdown_confirmed", "Primary breakdown"],
  ["confirmation_exchange_15m", "Confirmation exchange 15m"],
  ["composite_breakdown_confirmed", "Composite breakdown"],
] as const;

function count(value: unknown): string {
  return typeof value === "number" && Number.isFinite(value)
    ? value.toLocaleString("en-US", { maximumFractionDigits: 0 })
    : "—";
}

export function SignalFunnel({ funnel }: { funnel?: SignalFunnelData }) {
  const safe = funnel?.observational_only === true && funnel?.hard_gating_allowed === false;
  if (!safe) {
    return (
      <section className="panel mx-auto mb-7 max-w-7xl p-5">
        <p className="text-sm text-slate-400">Signal funnel unavailable; no diagnostic values are inferred.</p>
      </section>
    );
  }

  const systemicZero = funnel.attention?.cross_exchange_systemic_zero === true;
  const lifecycleSafe = funnel.stage_lifecycle?.observational_only === true
    && funnel.stage_lifecycle?.hard_gating_allowed === false;
  const currentTriggerCount = lifecycleSafe
    ? funnel.stage_lifecycle?.stages?.trigger?.passed
    : undefined;
  const currentTriggerSymbols = lifecycleSafe
    ? (funnel.stage_lifecycle?.members?.trigger ?? []).filter(
        (symbol): symbol is string => typeof symbol === "string" && symbol.length > 0,
      )
    : [];

  return (
    <section className="panel mx-auto mb-7 max-w-7xl p-5 sm:p-6" aria-label="Signal funnel diagnostics">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <h2 className="flex items-center gap-2 text-sm font-semibold text-slate-100"><GitBranch size={17} className="text-violet-300" />Signal funnel</h2>
          <p className="mt-1 text-sm text-slate-400">Persisted lifecycle progress and current-snapshot gate diagnostics. Missing evidence stays separate from a failed gate.</p>
        </div>
        <span className="status-pill border border-sky-400/25 bg-sky-500/10 text-sky-200">OBSERVATIONAL</span>
      </div>

      <dl className="mt-5 grid grid-cols-2 gap-3 sm:grid-cols-5">
        {states.map((state) => (
          <div key={state} className="stat">
            <dt>{state}</dt>
            <dd>{count(funnel.lifecycle?.[state])}</dd>
          </div>
        ))}
      </dl>

      <div className="mt-5 border-t border-slate-800 pt-4">
        <div className="flex flex-wrap items-baseline justify-between gap-2">
          <div>
            <h3 className="text-xs font-semibold text-slate-200">Persisted lifecycle chain</h3>
            <p className="mt-1 text-xs text-slate-500">Stages may complete in order across separate evaluations; trigger must still be current.</p>
          </div>
          <p className="text-[11px] text-slate-500">Available: {count(funnel.stage_lifecycle?.availability?.passed)} / {count(funnel.candidate_count)}</p>
        </div>
        <dl className="mt-3 grid grid-cols-2 gap-3 sm:grid-cols-5">
          {lifecycleStages.map(([key, label]) => (
            <div
              key={key}
              className={key === "trigger" ? "stat border-amber-400/25 bg-amber-500/5" : "stat"}
            >
              <dt>{label}</dt>
              <dd className={key === "trigger" ? "text-amber-200" : "text-violet-200"}>
                {lifecycleSafe ? count(funnel.stage_lifecycle?.stages?.[key]?.passed) : "—"}
              </dd>
            </div>
          ))}
        </dl>

        {typeof currentTriggerCount === "number" && currentTriggerCount > 0 ? (
          <div className="mt-4 rounded-xl border border-amber-400/25 bg-amber-500/10 p-3.5" aria-label="Current trigger symbols">
            <div className="flex items-start gap-2.5">
              <Zap size={16} className="mt-0.5 shrink-0 text-amber-300" aria-hidden="true" />
              <div>
                <h4 className="text-xs font-semibold text-amber-100">Current trigger symbols</h4>
                <p className="mt-1 text-xs leading-5 text-amber-100/70">
                  These are the symbols behind Current trigger = {count(currentTriggerCount)} in this snapshot.
                </p>
              </div>
            </div>
            {currentTriggerSymbols.length > 0 ? (
              <div className="mt-3 flex flex-wrap gap-2">
                {currentTriggerSymbols.map((symbol) => (
                  <span
                    key={symbol}
                    className="rounded-lg border border-amber-300/25 bg-slate-950/45 px-2.5 py-1.5 font-mono text-xs font-semibold text-amber-100"
                  >
                    {symbol}
                  </span>
                ))}
              </div>
            ) : (
              <p className="mt-3 text-xs text-amber-100/70">Symbol details are unavailable in this snapshot.</p>
            )}
          </div>
        ) : null}
      </div>

      <div className="mt-5 border-t border-slate-800 pt-4">
        <p className="mb-3 text-xs font-semibold text-slate-200">Current-snapshot gates</p>
        <div className="table-scroll">
          <table className="data-table">
          <thead><tr><th>Gate</th><th>Pass</th><th>Fail</th><th>Unavailable</th><th>Pass rate</th></tr></thead>
          <tbody>
            {gates.map(([key, label]) => {
              const gate = funnel.quality_gates?.[key];
              const rate = typeof gate?.pass_rate === "number" ? `${(gate.pass_rate * 100).toFixed(1)}%` : "—";
              return <tr key={key}><td className="text-slate-300">{label}</td><td className="font-mono text-emerald-300">{count(gate?.passed)}</td><td className="font-mono text-rose-300">{count(gate?.failed)}</td><td className="font-mono text-slate-400">{count(gate?.unavailable)}</td><td className="font-mono text-slate-300">{rate}</td></tr>;
            })}
          </tbody>
          </table>
        </div>
      </div>

      <dl className="mt-5 grid gap-3 sm:grid-cols-3">
        {breakdownChecks.map(([key, label]) => {
          const check = funnel.breakdown_evidence?.[key];
          const rate = typeof check?.pass_rate === "number" ? `${(check.pass_rate * 100).toFixed(1)}%` : "—";
          return <div key={key} className="stat"><dt>{label}</dt><dd className="text-sm font-normal text-slate-200"><span className="font-mono text-emerald-300">{count(check?.passed)}</span> pass · <span className="font-mono text-rose-300">{count(check?.failed)}</span> fail</dd><p className="mt-1 text-[11px] text-slate-500">{rate} pass · {count(check?.unavailable)} unavailable</p></div>;
        })}
      </dl>

      {systemicZero ? (
        <p className="mt-4 flex items-start gap-2 rounded-xl border border-amber-400/25 bg-amber-500/10 p-3 text-xs leading-5 text-amber-100">
          <AlertTriangle size={16} className="mt-0.5 shrink-0" />Cross-exchange confirmation has zero passes across the evaluated snapshot. This is an audit signal, not permission to bypass the gate.
        </p>
      ) : null}

      <p className="mt-4 flex items-start gap-2 border-t border-slate-800 pt-3 text-xs leading-5 text-slate-500"><ShieldCheck size={15} className="mt-0.5 shrink-0 text-emerald-400/80" />Read-only diagnostics. This panel cannot change lifecycle state, score, alerts, thresholds, or eligibility.</p>
    </section>
  );
}
