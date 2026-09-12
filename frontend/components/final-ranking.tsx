import { AlertTriangle, Medal, ShieldCheck } from "lucide-react";
import { asRecord } from "@/components/score-card";

type Ranking = {
  version?: string;
  observational_only?: boolean;
  top?: unknown[];
};

function finite(value: unknown): number | undefined {
  return typeof value === "number" && Number.isFinite(value) ? value : undefined;
}

function percent(value: unknown): string {
  const number = finite(value);
  return number === undefined ? "—" : `${(number * 100).toFixed(0)}%`;
}

function label(value: string): string {
  return value.replaceAll("_", " ");
}

export function FinalRanking({ ranking }: { ranking: Ranking | undefined }) {
  const top = Array.isArray(ranking?.top) ? ranking.top.map(asRecord).filter(Boolean) : [];
  if (ranking?.observational_only !== true || top.length === 0) {
    return (
      <section className="panel mx-auto mb-7 max-w-7xl p-5">
        <p className="flex items-center gap-2 text-sm font-medium text-slate-200"><Medal size={17} />Final ranking unavailable</p>
        <p className="mt-1 text-xs text-slate-500">No ranking is inferred until the backend supplies a safe observational packet.</p>
      </section>
    );
  }

  return (
    <section className="mx-auto mb-7 max-w-7xl" aria-label="Observational top three">
      <div className="mb-3 flex flex-wrap items-end justify-between gap-3">
        <div>
          <h2 className="flex items-center gap-2 text-lg font-semibold"><Medal className="text-amber-300" size={20} />Top 3 watchlist</h2>
          <p className="mt-1 text-xs text-slate-500">Readiness × signal × execution × relative weakness × evidence freshness.</p>
        </div>
        <span className="status-pill border border-sky-400/25 bg-sky-500/10 text-sky-200">OBSERVATIONAL</span>
      </div>
      <div className="grid gap-4 lg:grid-cols-3">
        {top.map((packet) => {
          const symbol = typeof packet?.symbol === "string" ? packet.symbol : "Unknown";
          const missing = Array.isArray(packet?.missing_components)
            ? packet.missing_components.filter((value): value is string => typeof value === "string")
            : [];
          const components = asRecord(packet?.components);
          const antiChase = asRecord(packet?.anti_chase);
          return (
            <article key={symbol} className="panel p-5">
              <div className="flex items-start justify-between gap-3">
                <div><p className="text-xs font-semibold uppercase tracking-[0.18em] text-amber-300">Rank #{finite(packet?.rank) ?? "—"}</p><p className="mt-1 break-all font-mono text-lg font-semibold text-sky-200">{symbol}</p></div>
                <div className="text-right"><p className="text-2xl font-semibold tabular-nums">{finite(packet?.score)?.toFixed(1) ?? "—"}</p><p className="text-xs text-slate-500">ranking score</p></div>
              </div>
              <div className="mt-4 flex items-center justify-between rounded-xl border border-slate-800 bg-slate-950/50 px-3 py-2 text-xs"><span className="text-slate-400">Evidence confidence</span><span className="font-mono text-slate-100">{percent(packet?.confidence)}</span></div>
              <dl className="mt-3 space-y-1.5 text-xs">
                {Object.entries(components ?? {}).map(([name, raw]) => {
                  const component = asRecord(raw);
                  return <div key={name} className="flex justify-between gap-3"><dt className="text-slate-500">{label(name)}</dt><dd className="font-mono text-slate-200">{component?.available === true ? finite(component.points)?.toFixed(1) ?? "—" : "unavailable"}</dd></div>;
                })}
              </dl>
              {missing.length > 0 ? <p className="mt-3 flex items-start gap-1.5 text-xs leading-5 text-amber-200/80"><AlertTriangle className="mt-0.5 shrink-0" size={13} />Missing: {missing.map(label).join(", ")}</p> : null}
              <p className="mt-3 flex items-start gap-1.5 border-t border-slate-800 pt-3 text-xs leading-5 text-slate-500"><ShieldCheck className="mt-0.5 shrink-0" size={13} />Anti-chase: {typeof antiChase?.status === "string" ? label(antiChase.status) : "not evaluated"}. No trade eligibility is implied.</p>
            </article>
          );
        })}
      </div>
    </section>
  );
}
