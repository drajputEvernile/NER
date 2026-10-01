export type RecordRow = {
  record_id: string;
  page_count: number;
  pages: string[];
  selected: boolean;
};

export type RecordList = {
  raw_path: string;
  selected_path: string;
  n: number;
  records: RecordRow[];
  shown: number;
  selected: number;
  hidden_over_n: number;
};

async function request<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, init);
  if (!response.ok) {
    let detail = `${response.status} ${response.statusText}`;
    try {
      const body = await response.json();
      if (body?.detail) detail = String(body.detail);
    } catch {
      /* keep the status text */
    }
    throw new Error(detail);
  }
  return response.json() as Promise<T>;
}

export const getRecords = () => request<RecordList>("/api/records");

export const imageUrl = (recordId: string, fileName: string) =>
  `/api/image?record_id=${encodeURIComponent(recordId)}&file_name=${encodeURIComponent(fileName)}`;

export const selectRecord = (recordId: string) =>
  request<{ selected: boolean }>(`/api/records/${encodeURIComponent(recordId)}/select`, { method: "POST" });

export const unselectRecord = (recordId: string) =>
  request<{ selected: boolean }>(`/api/records/${encodeURIComponent(recordId)}/select`, { method: "DELETE" });
