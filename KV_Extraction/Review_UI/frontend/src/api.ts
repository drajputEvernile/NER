export type RunSummary = {
  id: string;
  path: string;
  has_excel: boolean;
  documents: number;
  pages: number;
  mtime: number;
};

export type Hit = {
  id: string;
  field: string;
  key: string;
  region: string;
  sentence: string;
  ner_text: string;
  value: string;
  signature_date: string;
  score: string;
  accepted: string;
  selected: string;
  source: string;
  key_accuracy: string;
  value_accuracy: string;
  reason: string;
  actual_key: string;
  actual_value: string;
};

export type MissedKey = {
  field: string;
  key: string;
  value: string;
};

export type PageRow = {
  page_number: string;
  file_name: string;
  hits: Hit[];
  missed_keys: MissedKey[];
};

export type DocumentRow = {
  record_id: string;
  page_count: string;
  pages: PageRow[];
};

export type RunDetail = {
  id: string;
  documents: DocumentRow[];
  review_count: number;
};

export type AccuracyPayload = {
  key_accuracy: string;
  value_accuracy: string;
  reason?: string;
  actual_key?: string;
  actual_value?: string;
};

async function getJson<T>(url: string): Promise<T> {
  const response = await fetch(url);
  if (!response.ok) {
    throw new Error(`${response.status} ${response.statusText}`);
  }
  return response.json() as Promise<T>;
}

export function listRuns() {
  return getJson<RunSummary[]>("/api/runs");
}

export function getRun(runId: string) {
  return getJson<RunDetail>(`/api/runs/${encodeURIComponent(runId)}`);
}

export function imageUrl(runId: string, recordId: string, fileName: string, kind: string) {
  const params = new URLSearchParams({
    record_id: recordId,
    file_name: fileName,
    kind,
  });
  return `/api/runs/${encodeURIComponent(runId)}/image?${params.toString()}`;
}

export async function getPageOcrText(runId: string, recordId: string, fileName: string) {
  const params = new URLSearchParams({
    record_id: recordId,
    file_name: fileName,
  });
  const response = await fetch(`/api/runs/${encodeURIComponent(runId)}/ocr-text?${params.toString()}`);
  if (!response.ok) {
    throw new Error(`${response.status} ${response.statusText}`);
  }
  return response.text();
}

export async function setAccuracy(runId: string, hitId: string, payload: AccuracyPayload) {
  const response = await fetch(`/api/runs/${encodeURIComponent(runId)}/accuracy`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      hit_id: hitId,
      key_accuracy: payload.key_accuracy,
      value_accuracy: payload.value_accuracy,
      reason: payload.reason || "",
      actual_key: payload.actual_key || "",
      actual_value: payload.actual_value || "",
    }),
  });
  if (!response.ok) {
    const detail = await response.text();
    throw new Error(detail || `${response.status} ${response.statusText}`);
  }
  return response.json();
}

export async function setMissedKeys(
  runId: string,
  recordId: string,
  fileName: string,
  pageNumber: string,
  keys: MissedKey[],
) {
  const response = await fetch(`/api/runs/${encodeURIComponent(runId)}/missed-keys`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      record_id: recordId,
      file_name: fileName,
      page_number: pageNumber,
      keys,
    }),
  });
  if (!response.ok) {
    const detail = await response.text();
    throw new Error(detail || `${response.status} ${response.statusText}`);
  }
  return response.json();
}
