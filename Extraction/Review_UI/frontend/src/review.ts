import type { AddedValue, Candidate, OcrWord, PageField, Verdict } from "./api";

export type Draft = {
  not_present: boolean;
  candidates: Record<string, Verdict>;
  added: AddedValue[];
  dirty: boolean;
  /** Run the draft was pre-filled from. */
  prefilled: string | null;
};

export type PickTarget = { field: string; index: number; part: "value" | "key" };

/** Reasons that offer the right words to select on the OCR (the same sets as Training/labels.py). */
export const CORRECTION_REASONS = new Set(["wrong_value", "wrong_key_value", "wrong_position"]);
/** Of those, the reasons that also offer the key to select. */
export const KEY_REQUIRED_REASONS = new Set(["wrong_key_value", "wrong_position"]);
export const GROUP_REASON = "wrong_group";

export const EMPTY_ADDED: AddedValue = { for_candidate: "", level: "", value_words: [], key_words: [] };

const EMPTY_DRAFT: Draft = { not_present: false, candidates: {}, added: [], dirty: false, prefilled: null };

export function isHeading(field: PageField): boolean {
  return field.kind === "heading";
}

/** Level a correction slot starts with: the level the detector gave the candidate. */
function candidateLevels(field: PageField): Record<string, string> {
  return isHeading(field) ? Object.fromEntries(field.candidates.map((c) => [c.candidate_id, c.detail])) : {};
}

export function makeDraft(field: PageField): Draft {
  const source = field.review || field.prior;
  if (!source) return { ...EMPTY_DRAFT };
  const ids = new Set(field.candidates.map((c) => c.candidate_id));
  const candidates: Record<string, Verdict> = {};
  for (const [cid, item] of Object.entries(source.candidates || {})) {
    if (ids.has(cid) && item.verdict) {
      candidates[cid] = {
        verdict: item.verdict,
        reason: item.reason || "",
        belongs_to: item.belongs_to || "",
        level: item.level || "",
      };
    }
  }
  const added = (source.added || [])
    .filter((item) => !item.for_candidate || wantsCorrection(candidates[item.for_candidate]))
    .map((item) => ({
      for_candidate: item.for_candidate || "",
      level: item.level || "",
      value_words: item.value_words || [],
      key_words: item.key_words || [],
    }));
  return {
    not_present: source.not_present,
    candidates,
    added: withCorrectionSlots(candidates, added, candidateLevels(field)),
    dirty: !field.review,
    prefilled: field.review ? null : source.run || "another run",
  };
}

/** Wrong Value, Wrong Key & Value and Wrong Position get words to select under the candidate. */
export function wantsCorrection(verdict: Verdict | undefined): boolean {
  return verdict?.verdict === "incorrect" && CORRECTION_REASONS.has(verdict.reason);
}

/** Whether this wrong candidate's correction also offers the key to select. */
export function needsKey(verdict: Verdict | undefined): boolean {
  return verdict?.verdict === "incorrect" && KEY_REQUIRED_REASONS.has(verdict.reason);
}

/** One correction slot under every candidate that wants one, none under the others. */
function withCorrectionSlots(
  candidates: Record<string, Verdict>,
  added: AddedValue[],
  levels: Record<string, string> = {},
): AddedValue[] {
  const kept = added.filter((item) => !item.for_candidate || wantsCorrection(candidates[item.for_candidate]));
  const linked = new Set(kept.map((item) => item.for_candidate).filter(Boolean));
  for (const [cid, verdict] of Object.entries(candidates)) {
    if (wantsCorrection(verdict) && !linked.has(cid)) kept.push({ ...EMPTY_ADDED, for_candidate: cid, level: levels[cid] || "" });
  }
  return kept;
}

export function correctionSlots(field: PageField, candidates: Record<string, Verdict>, added: AddedValue[]): AddedValue[] {
  return withCorrectionSlots(candidates, added, candidateLevels(field));
}

/**
 * What a review must judge: every key-value pair the run extracted (the pairs accuracy
 * counts), or the lines detected as headings.
 */
export function isExtracted(field: PageField, c: Candidate): boolean {
  return c.selected || (!isHeading(field) && c.accepted && !!c.value_norm);
}

/** Why a draft can't be saved yet, or "" when it can. */
export function draftProblem(field: PageField, draft: Draft): string {
  const heading = isHeading(field);
  const extracted = field.candidates.filter((c) => isExtracted(field, c));
  if (extracted.some((c) => !draft.candidates[c.candidate_id]?.verdict)) {
    return heading ? "Mark every detected heading correct or incorrect." : "Mark every extracted value correct or incorrect.";
  }
  if (extracted.some((c) => draft.candidates[c.candidate_id]?.verdict === "incorrect" && !draft.candidates[c.candidate_id]?.reason)) {
    return heading ? "Pick a reason for each detected heading marked incorrect." : "Pick a reason for each extracted value marked incorrect.";
  }
  if (Object.values(draft.candidates).some((v) => v.verdict === "incorrect" && v.reason === GROUP_REASON && !v.belongs_to)) {
    return "Pick the group each 'Key Belongs to Another Group' key belongs to.";
  }
  // Selecting the right words is optional; a slot with no words is left out when saving.
  for (const item of draft.added) {
    if (item.value_words.length && heading && !item.level) return "Pick the level of each added heading.";
  }
  const correct = Object.values(draft.candidates).some((v) => v.verdict === "correct");
  const added = draft.added.some((item) => item.value_words.length);
  if (draft.not_present && (correct || added)) {
    return heading
      ? "Marked as no headings, but a heading is ticked or added."
      : "Marked not on this page, but a value is marked correct or added.";
  }
  if (!draft.not_present && !correct && !added) {
    return heading
      ? "Tick a heading, add a missed one, or mark that the page has no headings."
      : "Mark a correct value, add the missed value, or mark it not on this page.";
  }
  return "";
}

export function candidateDetail(fieldId: string, c: Candidate): string {
  if (!c.detail) return "";
  if (fieldId === "page_no") return `of ${c.detail}`;
  return c.detail;
}

/** Words joined in reading order. */
export function wordsText(indexes: number[], order: Map<number, number>, words: Map<number, OcrWord>): string {
  return [...indexes]
    .filter((i) => words.has(i))
    .sort((a, b) => (order.get(a) ?? 0) - (order.get(b) ?? 0))
    .map((i) => words.get(i)!.t)
    .join(" ");
}
