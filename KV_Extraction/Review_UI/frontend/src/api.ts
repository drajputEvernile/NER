export type RunStatus = "running" | "completed" | "stopped" | "error" | "abandoned" | "unknown";

export type RunSummary = {
  id: string;
  status: RunStatus;
  start_time: string | null;
  end_time: string | null;
  total_time_seconds: number;
  avg_time_per_page: number | null;
  total_documents: number;
  total_pages: number;
  completed_documents: number;
  completed_pages: number;
  model_version: string;
  ner_model: string;
  source_run: string | null;
  reviewable: boolean;
  reviewed_pages: number;
  reviewed_page_fields: number;
  /** Every module (KV fields + headings) as pairs: accuracy = correct / total, total = correct + wrong + missed. */
  accuracy: number | null;
  accuracy_correct: number;
  accuracy_wrong: number;
  accuracy_missed: number;
  accuracy_total: number;
  /** Key-value fields only. */
  kv_accuracy: number | null;
  headings: Record<string, HeadingScore>;
};

export type HeadingScore = {
  tp: number;
  fp: number;
  fn: number;
  level_ok: number;
  pages: number;
  pages_exact: number;
  precision: number | null;
  recall: number | null;
  level_accuracy: number | null;
  page_accuracy: number | null;
};

export type RunTotals = {
  runs: number;
  documents: number;
  pages: number;
  avg_pages_per_document: number | null;
  avg_time_per_page: number | null;
  accuracy: number | null;
  accuracy_correct: number;
  accuracy_wrong: number;
  accuracy_missed: number;
  accuracy_total: number;
};

export type RunList = {
  runs: RunSummary[];
  active_run: string | null;
  totals: RunTotals;
};

export type PageRow = {
  record_id: string;
  page_number: string;
  file_name: string;
  reviewed_fields: number;
};

export type DocumentRow = {
  record_id: string;
  page_count: number;
  reviewed_pages: number;
  pages: PageRow[];
};

export type RunDetail = RunSummary & {
  field_count: number;
  documents: DocumentRow[];
};

export type Option = { id: string; label: string };

export type Meta = {
  fields: Option[];
  reasons: Option[];
  heading_reasons: Option[];
  /** Reasons whose candidate gets a correction box. */
  correction_reasons: string[];
  levels: string[];
  /** Kinds of Member ID (member_id, mrn, ssn, encounter, other). */
  id_types: Option[];
};

export type Box = [number, number, number, number];

export type Candidate = {
  candidate_id: string;
  key: string;
  key_text: string;
  region: string;
  source: string;
  score: string;
  accepted: boolean;
  selected: boolean;
  /** Why the rules rejected it (headings: "low score", "KV key", ...). */
  note: string;
  value: string;
  value_norm: string;
  detail: string;
  key_words: number[];
  value_words: number[];
  key_box: Box | null;
  value_box: Box | null;
  /** Member ID: ID type guessed from the key ("" for other fields). */
  id_type: string;
};

export type Verdict = {
  verdict: "" | "correct" | "incorrect";
  reason: string;
  /** Field the value really belongs to (reason "wrong_field"). */
  belongs_to?: string;
  /** Candidate whose key should have been used instead (reason "other_selected"). */
  prefer_candidate?: string;
  /** Heading / Subheading of a heading marked correct. */
  level?: string;
  /** ID type of a Member ID marked correct. */
  id_type?: string;
  /** Neighbour words that make the key prose (reason "not_a_key"). */
  block_before?: string;
  block_after?: string;
};

export type AddedValue = {
  value: string;
  value2: string;
  key: string;
  /** The candidate this value corrects; empty for a value the rules missed entirely. */
  for_candidate?: string;
  /** Heading / Subheading (heading fields). */
  level?: string;
  /** Member ID type; empty = guessed from the key on save. */
  id_type?: string;
  value_words: number[];
  key_words: number[];
};

export type FieldReview = {
  not_present: boolean;
  candidates: Record<string, Verdict & { value: string; value_norm: string; selected: boolean }>;
  added: (AddedValue & { value_norm: string })[];
  truth: { value: string; value_norm: string; source: string; level?: string; value_words?: number[] }[];
  extracted: { value: string }[];
  /** Key-value pairs of this page-field (not stored for headings or older reviews). */
  pairs?: { right: number; wrong: number; missed: number };
  correct: boolean;
  notes: string;
  reviewer: string;
  reviewed_at: string;
  run?: string;
};

export type PageField = {
  id: string;
  label: string;
  kind: "kv" | "heading";
  ocr_sha1: string;
  candidates: Candidate[];
  review: FieldReview | null;
  prior: FieldReview | null;
};

/** Collected during review only; no extractor detects it and no accuracy counts it. */
export type PageInfo = {
  page_sequence: number;
  reviewer: string;
  saved_at: string;
  run?: string;
};

export type PageDetail = {
  record_id: string;
  page_number: string;
  file_name: string;
  fields: PageField[];
  page_info: PageInfo | null;
  /** Saved for the same page in another batch (shown until this batch saves its own). */
  page_info_prior: PageInfo | null;
};

export type OcrWord = { i: number; t: string; b: Box };

export type PageOcr = {
  available: boolean;
  lines: OcrWord[][];
  text: string;
};

export type ReviewSubmission = {
  record_id: string;
  page_number: string;
  file_name: string;
  field: string;
  reviewer: string;
  not_present: boolean;
  candidates: Record<string, Verdict>;
  added: AddedValue[];
  notes: string;
};

export type ModelVersion = {
  id: string;
  label: string;
  description: string;
  trained_at: string | null;
  runnable: boolean;
};

/** Key-value pairs of one field: correct = right, total = right + wrong + missed. */
export type FieldScore = {
  correct: number;
  wrong: number;
  missed: number;
  total: number;
  page_fields: number;
  accuracy: number | null;
};

export type VersionRun = {
  id: string;
  start_time: string | null;
  status: RunStatus;
  ner_model: string;
  accuracy: number | null;
  correct: number;
  wrong: number;
  missed: number;
  total: number;
  /** KV fields and heading detectors, each as pairs. */
  fields: Record<string, FieldScore>;
  /** Key-value fields only. */
  kv: FieldScore;
  headings: Record<string, HeadingScore>;
};

export type VersionsAccuracy = {
  run_id: string;
  documents: number;
  fields: Option[];
  heading_fields: Option[];
  versions: { model_version: string; label: string; latest: VersionRun; runs: VersionRun[] }[];
};

async function request<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, init);
  if (!response.ok) {
    let detail = `${response.status} ${response.statusText}`;
    try {
      const body = await response.json();
      if (body?.detail) detail = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail);
    } catch {
      /* non-JSON error body */
    }
    throw new Error(detail);
  }
  return response.json() as Promise<T>;
}

function runPath(runId: string, suffix = "") {
  return `/api/runs/${encodeURIComponent(runId)}${suffix}`;
}

function query(params: Record<string, string>) {
  return new URLSearchParams(params).toString();
}

export function listRuns() {
  return request<RunList>("/api/runs");
}

export function getMeta() {
  return request<Meta>("/api/meta");
}

export function getRun(runId: string) {
  return request<RunDetail>(runPath(runId));
}

export function getPage(runId: string, page: PageRow) {
  return request<PageDetail>(
    `${runPath(runId, "/page")}?${query({
      record_id: page.record_id,
      page_number: page.page_number,
      file_name: page.file_name,
    })}`,
  );
}

export function getPageOcr(runId: string, recordId: string, fileName: string) {
  return request<PageOcr>(`${runPath(runId, "/page-ocr")}?${query({ record_id: recordId, file_name: fileName })}`);
}

export function imageUrl(runId: string, recordId: string, fileName: string, kind: string) {
  return `${runPath(runId, "/image")}?${query({ record_id: recordId, file_name: fileName, kind })}`;
}

export function saveReview(runId: string, body: ReviewSubmission) {
  return request<{ review: FieldReview; reviewed_fields: number }>(runPath(runId, "/review"), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

export function savePageInfo(runId: string, page: PageRow, reviewer: string, pageSequence: string) {
  return request<{ page_info: PageInfo | null }>(runPath(runId, "/page-info"), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      record_id: page.record_id,
      page_number: page.page_number,
      file_name: page.file_name,
      reviewer,
      page_sequence: pageSequence,
    }),
  });
}

export function clearReview(runId: string, page: PageRow, field: string) {
  return request<{ reviewed_fields: number }>(
    `${runPath(runId, "/review")}?${query({
      record_id: page.record_id,
      page_number: page.page_number,
      file_name: page.file_name,
      field,
    })}`,
    { method: "DELETE" },
  );
}

export function listVersions() {
  return request<ModelVersion[]>("/api/versions");
}

export function rerun(runId: string, modelVersion: string) {
  return request<{ started: boolean; pid: number; log: string }>(runPath(runId, "/rerun"), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ model_version: modelVersion }),
  });
}

export function getVersionsAccuracy(runId: string) {
  return request<VersionsAccuracy>(runPath(runId, "/versions-accuracy"));
}
