/**
 * Candidate shape and shared guards.
 *
 * These lived in components/score-card.tsx, whose ScoreCard component rendered
 * the retired Score-V2 generation (score_v2_watch_v1, trade_eligible,
 * score_components, TRIGGERED/ARMED/REJECTED pills). That component was
 * unreachable — nothing imported it — but the file survived because three live
 * modules imported these helpers from it. They belong in lib, not in a dead
 * component.
 *
 * Signatures are preserved exactly as they were, including asRecord returning
 * undefined rather than an empty object: callers branch on that.
 */

export type Candidate = Record<string, unknown>;
type RecordValue = Record<string, unknown>;

export function asRecord(value: unknown): RecordValue | undefined {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    ? (value as RecordValue)
    : undefined;
}

export function isLiveCandidate(candidate: Candidate, hasFreshSnapshot: boolean): boolean {
  return hasFreshSnapshot && candidate.data_status === "live";
}
