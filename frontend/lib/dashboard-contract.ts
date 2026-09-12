import type { DashboardSnapshot, DashboardStreamEvent, DecisionTerminal, JsonObject } from "@/generated/dashboard-contract";

function record(value: unknown): JsonObject | undefined {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    ? value as JsonObject
    : undefined;
}

function finite(value: unknown): value is number {
  return typeof value === "number" && Number.isFinite(value);
}

function positiveIntegerText(value: unknown): value is string {
  return typeof value === "string" && /^[1-9]\d*$/.test(value);
}

const DECISION_COUNT_KEYS = [
  "ENTRY_READY", "FORMING", "ACTIVE", "LATE",
  "INVALIDATED", "EXPIRED", "NO_TRADE", "UNAVAILABLE",
] as const;

function stringArray(value: unknown): value is string[] {
  return Array.isArray(value) && value.every((item) => typeof item === "string" && item.length > 0);
}

function diagnosticReasonArray(value: unknown): boolean {
  return Array.isArray(value) && value.every((item) => {
    const row = record(item);
    return row !== undefined
      && typeof row.reason === "string"
      && row.reason.length > 0
      && Number.isInteger(row.count)
      && (row.count as number) >= 0
      && finite(row.share_pct)
      && (row.share_pct as number) >= 0
      && (row.share_pct as number) <= 100;
  });
}

export function decisionTerminal(value: unknown): DecisionTerminal | undefined {
  const packet = record(value);
  if (!packet || packet.contract_version !== "decision_terminal_v1") return undefined;
  const counts = record(packet.counts);
  const diagnostics = record(packet.zero_entry_ready_diagnostics);
  if (!counts || !diagnostics) return undefined;
  for (const key of DECISION_COUNT_KEYS) {
    const count = counts[key];
    if (!Number.isInteger(count) || (count as number) < 0) return undefined;
  }
  if (!stringArray(packet.entry_ready)
    || !stringArray(packet.forming)
    || !stringArray(packet.active)
    || !stringArray(packet.late)
    || !Array.isArray(packet.recent_changes)
    || packet.recent_changes.length > 10
    || !packet.recent_changes.every((item) => record(item) !== undefined)
    || typeof diagnostics.entry_ready_zero !== "boolean"
    || !Number.isInteger(diagnostics.evaluated_candidates)
    || (diagnostics.evaluated_candidates as number) < 0
    || !diagnosticReasonArray(diagnostics.top_reasons)) return undefined;
  const hasPipelineHealth = diagnostics.pipeline_degraded !== undefined
    || diagnostics.systemic_unavailable_reasons !== undefined;
  if (hasPipelineHealth) {
    if (typeof diagnostics.pipeline_degraded !== "boolean"
      || !diagnosticReasonArray(diagnostics.systemic_unavailable_reasons)) return undefined;
    const systemicUnavailable = diagnostics.systemic_unavailable_reasons as JsonObject[];
    if (diagnostics.pipeline_degraded !== (systemicUnavailable.length > 0)
      || systemicUnavailable.some((row) => row.count !== diagnostics.evaluated_candidates || row.share_pct !== 100)) {
      return undefined;
    }
  }
  if (packet.entry_ready.length !== Math.min(counts.ENTRY_READY as number, 3)
    || packet.forming.length !== Math.min(counts.FORMING as number, 6)
    || packet.active.length !== Math.min(counts.ACTIVE as number, 6)
    || packet.late.length !== Math.min(counts.LATE as number, 6)
    || diagnostics.entry_ready_zero !== ((counts.ENTRY_READY as number) === 0)) return undefined;
  return packet as unknown as DecisionTerminal;
}

export function dashboardSnapshot(value: unknown): DashboardSnapshot | undefined {
  const packet = record(value);
  if (!packet) return undefined;
  if (packet.contract_version !== "dashboard_snapshot_v2"
    || packet.schema_version !== "2.0"
    || packet.state !== "READY"
    || !Number.isInteger(packet.snapshot_version)
    || (packet.snapshot_version as number) < 1
    || !finite(packet.generated_at)
    || !Number.isInteger(packet.total)
    || (packet.total as number) < 0
    || !record(packet.candidates)
    || !Object.values(packet.candidates as JsonObject).every((candidate) => record(candidate) !== undefined)
    || !record(packet.final_ranking)
    || !record(packet.signal_funnel)) return undefined;
  const terminal = decisionTerminal(packet.decision_terminal);
  if (!terminal || Object.keys(packet.candidates as JsonObject).length !== packet.total) return undefined;
  const decisionTotal = DECISION_COUNT_KEYS.reduce((sum, key) => sum + terminal.counts[key], 0);
  if (decisionTotal !== packet.total
    || terminal.zero_entry_ready_diagnostics.evaluated_candidates !== packet.total) return undefined;
  return packet as unknown as DashboardSnapshot;
}

export function dashboardStreamEvent(value: unknown): DashboardStreamEvent | undefined {
  const packet = record(value);
  if (!packet) return undefined;
  if (packet.contract_version !== "dashboard_stream_event_v2"
    || !positiveIntegerText(packet.event_id)
    || !["snapshot", "heartbeat"].includes(String(packet.event_type))
    || !Number.isInteger(packet.snapshot_version)
    || (packet.snapshot_version as number) < 0
    || packet.schema_version !== "2.0"
    || !finite(packet.generated_at)
    || !(packet.last_event_id === null || positiveIntegerText(packet.last_event_id))
    || typeof packet.payload_hash !== "string"
    || !/^[0-9a-f]{64}$/.test(packet.payload_hash as string)
    || typeof packet.replayed !== "boolean"
    || typeof packet.full_snapshot !== "boolean") return undefined;
  if (packet.event_type === "heartbeat") {
    return packet.payload === null ? packet as unknown as DashboardStreamEvent : undefined;
  }
  const payload = dashboardSnapshot(packet.payload);
  if (!payload || payload.snapshot_version !== packet.snapshot_version) return undefined;
  return { ...packet, payload } as unknown as DashboardStreamEvent;
}
