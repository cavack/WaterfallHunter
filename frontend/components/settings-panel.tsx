"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { SlidersHorizontal } from "lucide-react";

type FieldKind = "number" | "toggle";

type SettingField = {
  key: string;
  kind: FieldKind;
  label: string;
  help: string;
  unit: string;
  minimum: number | null;
  maximum: number | null;
  step: number | null;
  default: number | boolean;
  value: number | boolean;
  modified: boolean;
};

type SettingGroup = { key: string; label: string; fields: SettingField[] };
type SettingsDoc = { contract_version: string; generated_at: number; groups: SettingGroup[] };

type HistoryEntry = {
  changed_at: number;
  changed_by: string;
  setting_key: string;
  previous_value: unknown;
  new_value: unknown;
  note: string;
};

const TOKEN_STORAGE_KEY = "wfh.operatorToken";

function fmtValue(v: unknown): string {
  if (typeof v === "boolean") return v ? "on" : "off";
  if (typeof v === "number") return String(v);
  if (v === null || v === undefined) return "—";
  return String(v);
}

function fmtTime(seconds: number): string {
  if (!Number.isFinite(seconds)) return "—";
  return new Date(seconds * 1000).toLocaleString();
}

export function SettingsPanel() {
  const [doc, setDoc] = useState<SettingsDoc | null>(null);
  const [draft, setDraft] = useState<Record<string, number | boolean>>({});
  const [history, setHistory] = useState<HistoryEntry[]>([]);
  const [token, setToken] = useState("");
  const [note, setNote] = useState("");
  const [status, setStatus] = useState<{ kind: "idle" | "ok" | "error"; text: string }>({
    kind: "idle",
    text: "",
  });
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    try {
      setToken(window.localStorage.getItem(TOKEN_STORAGE_KEY) ?? "");
    } catch {
      /* storage unavailable; the field simply starts empty */
    }
  }, []);

  const load = useCallback(async () => {
    try {
      const [sResp, hResp] = await Promise.all([
        fetch("/dashboard/api/settings", { cache: "no-store" }),
        fetch("/dashboard/api/settings/history?limit=25", { cache: "no-store" }),
      ]);
      if (sResp.ok) {
        setDoc((await sResp.json()) as SettingsDoc);
        setDraft({});
      }
      if (hResp.ok) {
        const data = (await hResp.json()) as { entries?: HistoryEntry[] };
        setHistory(Array.isArray(data.entries) ? data.entries : []);
      }
    } catch {
      setStatus({ kind: "error", text: "Could not load settings." });
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const fields = useMemo(() => (doc?.groups ?? []).flatMap((g) => g.fields), [doc]);

  const pending = useMemo(() => {
    const out: Record<string, number | boolean> = {};
    for (const f of fields) {
      if (f.key in draft && draft[f.key] !== f.value) out[f.key] = draft[f.key];
    }
    return out;
  }, [draft, fields]);

  const pendingCount = Object.keys(pending).length;

  const save = async () => {
    if (pendingCount === 0 || busy) return;
    setBusy(true);
    setStatus({ kind: "idle", text: "" });
    try {
      const resp = await fetch("/dashboard/api/settings", {
        method: "PUT",
        headers: { "Content-Type": "application/json", "X-Operator-Token": token },
        body: JSON.stringify({ changes: pending, note }),
      });
      if (!resp.ok) {
        const body = (await resp.json().catch(() => ({}))) as { detail?: string };
        setStatus({ kind: "error", text: body.detail ?? `Rejected (${resp.status}).` });
        return;
      }
      try {
        window.localStorage.setItem(TOKEN_STORAGE_KEY, token);
      } catch {
        /* non-fatal */
      }
      setNote("");
      setStatus({
        kind: "ok",
        text: `Saved ${pendingCount} change${pendingCount === 1 ? "" : "s"} — applies to new signals only.`,
      });
      await load();
    } catch {
      setStatus({ kind: "error", text: "Network error while saving." });
    } finally {
      setBusy(false);
    }
  };

  const reset = async () => {
    if (busy) return;
    if (!window.confirm("Restore every setting to its shipped default?")) return;
    setBusy(true);
    try {
      const resp = await fetch("/dashboard/api/settings/reset", {
        method: "POST",
        headers: { "X-Operator-Token": token },
      });
      if (!resp.ok) {
        const body = (await resp.json().catch(() => ({}))) as { detail?: string };
        setStatus({ kind: "error", text: body.detail ?? `Rejected (${resp.status}).` });
        return;
      }
      setStatus({ kind: "ok", text: "Restored shipped defaults." });
      await load();
    } catch {
      setStatus({ kind: "error", text: "Network error while resetting." });
    } finally {
      setBusy(false);
    }
  };

  if (!doc) {
    return <p className="text-[11px] text-slate-500">Loading settings…</p>;
  }

  const modifiedCount = fields.filter((f) => f.modified).length;
  const inputCls =
    "w-full rounded border border-slate-700/60 bg-slate-900/60 px-2 py-1 text-[11px] text-slate-200 outline-none focus:border-sky-500/60";

  return (
    <section id="settings-panel" className="space-y-4">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div>
          <div className="flex items-center gap-2">
            <SlidersHorizontal size={13} className="text-sky-400" />
            <h2 className="text-xs font-semibold uppercase tracking-wide text-slate-400">
              Decision settings
            </h2>
          </div>
          <p className="mt-1 max-w-2xl text-[11px] leading-relaxed text-slate-500">
            Changes apply to <strong className="text-slate-300">new signals only</strong>; decisions
            already recorded are never rewritten. Every change is logged with its previous value.
          </p>
        </div>
        <div className="flex items-center gap-2">
          {modifiedCount > 0 && (
            <span className="rounded bg-amber-500/15 px-2 py-0.5 text-[10px] font-semibold text-amber-300">
              {modifiedCount} off default
            </span>
          )}
          {pendingCount > 0 && (
            <span className="rounded bg-sky-500/15 px-2 py-0.5 text-[10px] font-semibold text-sky-300">
              {pendingCount} unsaved
            </span>
          )}
        </div>
      </div>

      <div className="grid gap-3 sm:grid-cols-2">
        <label className="text-[10px] uppercase tracking-wide text-slate-500">
          Operator token
          <input
            type="password"
            className={`mt-1 ${inputCls}`}
            value={token}
            onChange={(e) => setToken(e.target.value)}
            placeholder="required to save"
            autoComplete="off"
          />
        </label>
        <label className="text-[10px] uppercase tracking-wide text-slate-500">
          Note (optional)
          <input
            type="text"
            className={`mt-1 ${inputCls}`}
            value={note}
            onChange={(e) => setNote(e.target.value)}
            placeholder="why this change"
          />
        </label>
      </div>

      {doc.groups.map((group) => (
        <div key={group.key}>
          <h3 className="mb-2 text-[10px] font-semibold uppercase tracking-wide text-slate-500">
            {group.label}
          </h3>
          <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
            {group.fields.map((f) => {
              const value = f.key in draft ? draft[f.key] : f.value;
              const dirty = f.key in draft && draft[f.key] !== f.value;
              return (
                <div
                  key={f.key}
                  className={`rounded-lg border p-2.5 ${
                    dirty
                      ? "border-sky-500/50 bg-sky-500/5"
                      : "border-slate-800/50 bg-slate-950/40"
                  }`}
                >
                  <div className="flex items-center justify-between gap-2">
                    <span className="text-[11px] font-medium text-slate-300">{f.label}</span>
                    {f.modified && !dirty && (
                      <span
                        title="differs from shipped default"
                        className="text-[10px] text-amber-400"
                      >
                        ●
                      </span>
                    )}
                  </div>

                  <div className="mt-1.5">
                    {f.kind === "toggle" ? (
                      <button
                        type="button"
                        aria-pressed={value === true}
                        onClick={() => setDraft((d) => ({ ...d, [f.key]: !(value === true) }))}
                        className={`rounded px-2.5 py-1 text-[10px] font-semibold transition-colors ${
                          value === true
                            ? "bg-emerald-500/20 text-emerald-300"
                            : "bg-slate-800/60 text-slate-500"
                        }`}
                      >
                        {value === true ? "ON" : "OFF"}
                      </button>
                    ) : (
                      <div className="flex items-center gap-1.5">
                        <input
                          type="number"
                          className={inputCls}
                          value={typeof value === "number" ? value : 0}
                          min={f.minimum ?? undefined}
                          max={f.maximum ?? undefined}
                          step={f.step ?? "any"}
                          onChange={(e) => {
                            const next = Number(e.target.value);
                            if (Number.isFinite(next)) {
                              setDraft((d) => ({ ...d, [f.key]: next }));
                            }
                          }}
                        />
                        {f.unit && (
                          <span className="shrink-0 text-[10px] text-slate-500">{f.unit}</span>
                        )}
                      </div>
                    )}
                  </div>

                  <p className="mt-1.5 text-[10px] leading-snug text-slate-500">{f.help}</p>
                  <p className="mt-1 text-[10px] text-slate-600">
                    default {fmtValue(f.default)}
                    {f.kind === "number" && f.minimum !== null && f.maximum !== null
                      ? ` · allowed ${f.minimum}–${f.maximum}`
                      : ""}
                  </p>
                </div>
              );
            })}
          </div>
        </div>
      ))}

      {status.text && (
        <p
          className={`text-[11px] ${
            status.kind === "error" ? "text-rose-400" : "text-emerald-400"
          }`}
        >
          {status.text}
        </p>
      )}

      <div className="flex flex-wrap gap-2">
        <button
          type="button"
          onClick={save}
          disabled={pendingCount === 0 || busy}
          className="rounded bg-sky-500/20 px-3 py-1.5 text-[11px] font-semibold text-sky-300 disabled:opacity-40"
        >
          {busy ? "Saving…" : `Save${pendingCount ? ` ${pendingCount}` : ""} change${pendingCount === 1 ? "" : "s"}`}
        </button>
        <button
          type="button"
          onClick={() => setDraft({})}
          disabled={pendingCount === 0 || busy}
          className="rounded bg-slate-800/60 px-3 py-1.5 text-[11px] font-semibold text-slate-400 disabled:opacity-40"
        >
          Discard
        </button>
        <button
          type="button"
          onClick={reset}
          disabled={busy}
          className="rounded bg-rose-500/15 px-3 py-1.5 text-[11px] font-semibold text-rose-300 disabled:opacity-40"
        >
          Restore defaults
        </button>
      </div>

      <details className="rounded-lg border border-slate-800/50 bg-slate-950/40">
        <summary className="cursor-pointer list-none px-3 py-2 text-[11px] font-semibold text-slate-400">
          Change history ({history.length})
        </summary>
        <div className="border-t border-slate-800/50 px-3 py-2">
          {history.length === 0 ? (
            <p className="text-[11px] text-slate-500">No changes recorded.</p>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-left text-[10px]">
                <thead className="text-slate-500">
                  <tr>
                    <th className="py-1 pr-3 font-medium">When</th>
                    <th className="py-1 pr-3 font-medium">Setting</th>
                    <th className="py-1 pr-3 font-medium">From</th>
                    <th className="py-1 pr-3 font-medium">To</th>
                    <th className="py-1 pr-3 font-medium">By</th>
                    <th className="py-1 font-medium">Note</th>
                  </tr>
                </thead>
                <tbody className="text-slate-400">
                  {history.map((h, i) => (
                    <tr
                      key={`${h.changed_at}-${h.setting_key}-${i}`}
                      className="border-t border-slate-900/60"
                    >
                      <td className="py-1 pr-3 whitespace-nowrap">{fmtTime(h.changed_at)}</td>
                      <td className="py-1 pr-3 font-mono">{h.setting_key}</td>
                      <td className="py-1 pr-3 text-slate-600">{fmtValue(h.previous_value)}</td>
                      <td className="py-1 pr-3 text-sky-300">{fmtValue(h.new_value)}</td>
                      <td className="py-1 pr-3">{h.changed_by}</td>
                      <td className="py-1">{h.note || "—"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      </details>
    </section>
  );
}

export default SettingsPanel;
