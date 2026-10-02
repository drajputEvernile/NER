export type RunStatus = "running" | "completed" | "stopped" | "error" | "abandoned" | "unknown";

/** Key-value pairs: accuracy = right / (right + wrong + missed). */
export type Score = {
  accuracy: number | null;
  right: number;
  wrong: number;
  missed: number;
};

/** Headings: the same pairs plus line-level precision and recall. */
export type HeadingScore = Score & {
  precision: number | null;
  recall: number | null;
};

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
  /** One version covers both models: KV_Extraction and Heading_Detector. */
  model_version: string;
  source_run: string | null;
  records: string[];
  reviewed_pages: number;
  reviewed_documents: number;
  kv: Score;
  heading: HeadingScore;
};

export type RunTotals = {
  /** Distinct sets of documents across runs (a rerun of a batch is the same batch). */
  batches: number;
  runs: number;
  documents: number;
  pages: number;
  avg_pages_per_document: number | null;
  avg_time_per_page: number | null;
  /** The newest run that has reviews. */
  latest: {
    run: string;
    model_version: string;
    kv_accuracy: number | null;
    heading_accuracy: number | null;
    documents: number;
  } | null;
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
  /** Reviews are written to the run's workbook a few seconds after the last change. */
  saving: boolean;
  /** Set when the workbook could not be written (open in Excel); the reviews are kept. */
  save_error: string;
};

export type Option = { id: string; label: string };

export type Meta = {
  fields: Option[];
  reasons: Option[];
  heading_reasons: Option[];
  /** Reasons that need the right words selected on the OCR. */
  correction_reasons: string[];
  /** Of those, the ones that need the key selected as well as the value. */
  key_required_reasons: string[];
  /** The reason that asks which group the key belongs to instead. */
  group_reason: string;
  /** The key-value groups (fields) a key can belong to. */
  groups: Option[];
  levels: string[];
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
};

export type Verdict = {
  verdict: "" | "correct" | "incorrect";
  reason: string;
  /** The group the key really belongs to (reason "wrong_group"). */
  belongs_to?: string;
  /** Heading / Subheading of a heading marked correct. */
  level?: string;
};

/** Words the reviewer selected on the OCR: a value the run missed, or the right value for a wrong candidate. */
export type AddedValue = {
  /** The candidate this corrects; empty for a value the run missed entirely. */
  for_candidate: string;
  /** Heading / Subheading (heading fields). */
  level: string;
  value_words: number[];
  key_words: number[];
};

export type FieldReview = {
  not_present: boolean;
  candidates: Record<string, Verdict & { value: string; value_norm: string; selected: boolean }>;
  added: (AddedValue & { value: string; key: string; value_norm: string })[];
  truth: { value: string; value_norm: string; source: string; level?: string; value_words?: number[] }[];
  extracted: { value: string }[];
  /** Key-value pairs of this page-field (not stored for headings). */
  pairs?: { right: number; wrong: number; missed: number };
  correct: boolean;
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

export type PageDetail = {
  record_id: string;
  page_number: string;
  file_name: string;
  fields: PageField[];
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

/** Detection details of a heading detector beside its right / wrong / missed pairs. */
export type HeadingDetail = {
  tp: number;
  fp: number;
  fn: number;
  pages: number;
  precision: number | null;
  recall: number | null;
  level_accuracy: number | null;
  page_accuracy: number | null;
};

export type VersionRun = {
  id: string;
  start_time: string | null;
  status: RunStatus;
  accuracy: number | null;
  correct: number;
  wrong: number;
  missed: number;
  total: number;
  /** KV fields and heading detectors, each as pairs. */
  fields: Record<string, FieldScore>;
  /** KV_Extraction: the key-value fields only. */
  kv: FieldScore;
  /** Heading_Detector: its pairs plus line-level precision and recall. */
  heading: FieldScore & { precision: number | null; recall: number | null };
  headings: Record<string, HeadingDetail>;
};

export type VersionsAccuracy = {
  run_id: string;
  documents: number;
  fields: Option[];
  heading_fields: Option[];
  versions: { model_version: string; label: string; latest: VersionRun; runs: VersionRun[] }[];
};

export type ModelVersion = {
  id: string;
  label: string;
  description: string;
  trained_at: string | null;
  runnable: boolean;
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

/** The original page image: the only picture the review shows. */
export function imageUrl(runId: string, recordId: string, fileName: string) {
  return `${runPath(runId, "/image")}?${query({ record_id: recordId, file_name: fileName })}`;
}

export type SaveStatus = { reviewed_fields: number; saving: boolean; save_error: string };

export function saveReview(runId: string, body: ReviewSubmission) {
  return request<SaveStatus & { review: FieldReview }>(runPath(runId, "/review"), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

export function clearReview(runId: string, page: PageRow, field: string) {
  return request<SaveStatus>(
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
