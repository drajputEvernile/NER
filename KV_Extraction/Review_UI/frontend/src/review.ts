import type { AddedValue, Candidate, OcrWord, PageField, Verdict } from "./api";

export type Draft = {
  not_present: boolean;
  candidates: Record<string, Verdict>;
  added: AddedValue[];
  notes: string;
  dirty: boolean;
  /** Run the draft was pre-filled from. */
  prefilled: string | null;
};

export type PickTarget = { field: string; index: number; part: "value" | "key" };

export const EMPTY_ADDED: AddedValue = {
  value: "",
  value2: "",
  key: "",
  for_candidate: "",
  level: "",
  id_type: "",
  value_words: [],
  key_words: [],
};

const EMPTY_DRAFT: Draft = { not_present: false, candidates: {}, added: [], notes: "", dirty: false, prefilled: null };

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
        prefer_candidate: item.prefer_candidate || "",
        level: item.level || "",
        id_type: item.id_type || "",
        block_before: item.block_before || "",
        block_after: item.block_after || "",
      };
    }
  }
  const added = (source.added || [])
    .filter((item) => !item.for_candidate || wantsCorrection(candidates[item.for_candidate]))
    .map((item) => ({
      value: item.value,
      value2: item.value2 || "",
      key: item.key || "",
      for_candidate: item.for_candidate || "",
      level: item.level || "",
      id_type: item.id_type || "",
      value_words: item.value_words || [],
      key_words: item.key_words || [],
    }));
  return {
    not_present: source.not_present,
    candidates,
    added: withCorrectionSlots(candidates, added, candidateLevels(field)),
    notes: source.notes || "",
    dirty: !field.review,
    prefilled: field.review ? null : source.run || "another run",
  };
}

/** "Wrong value" (KV) and "Wrong text" (headings) get a correction box under the candidate. */
export function wantsCorrection(verdict: Verdict | undefined): boolean {
  return verdict?.verdict === "incorrect" && (verdict.reason === "wrong_value" || verdict.reason === "wrong_span");
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
  if (Object.values(draft.candidates).some((v) => v.verdict === "incorrect" && v.reason === "wrong_field" && !v.belongs_to)) {
    return "Pick the field each 'Belongs to another field' value belongs to.";
  }
  if (Object.values(draft.candidates).some((v) => v.verdict === "incorrect" && v.reason === "other_selected" && !v.prefer_candidate)) {
    return "Pick the key that should have been selected instead.";
  }
  if (heading && draft.added.some((item) => item.value.trim() && !item.level)) {
    return "Pick the level of each added heading.";
  }
  if (
    field.id === "member_id" &&
    draft.added.some((item) => item.value.trim() && !item.for_candidate && !item.key.trim() && !item.id_type)
  ) {
    return "Pick the ID type of each added Member ID (or enter its key).";
  }
  const correct = Object.values(draft.candidates).some((v) => v.verdict === "correct");
  const added = draft.added.some((item) => item.value.trim());
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
  if (fieldId === "electronic_signature") return `signed ${c.detail}`;
  if (fieldId === "page_no") return `of ${c.detail}`;
  return c.detail;
}

/**
 * The field's other keys found on the page (one option per distinct key position), for
 * "Should not be selected (another key was right)".
 */
export function otherKeys(field: PageField, c: Candidate): { id: string; label: string }[] {
  const own = c.key_words.join(",");
  const seen = new Set<string>([own]);
  const out: { id: string; label: string }[] = [];
  for (const other of field.candidates) {
    const at = other.key_words.join(",");
    if (!other.key_words.length || seen.has(at)) continue;
    seen.add(at);
    const key = other.key_text || other.key;
    out.push({ id: other.candidate_id, label: other.value ? `${key} → ${other.value}` : `${key} (no value)` });
  }
  return out;
}

/** Words joined in reading order. */
export function wordsText(indexes: number[], order: Map<number, number>, words: Map<number, OcrWord>): string {
  return [...indexes]
    .filter((i) => words.has(i))
    .sort((a, b) => (order.get(a) ?? 0) - (order.get(b) ?? 0))
    .map((i) => words.get(i)!.t)
    .join(" ");
}
