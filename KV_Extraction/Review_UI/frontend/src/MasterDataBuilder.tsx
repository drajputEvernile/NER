import { useEffect, useMemo, useRef, useState } from "react";
import { ArrowLeft, ChevronLeft, ChevronRight, Minus, Plus, RefreshCw } from "lucide-react";
import {
  getMasterAnnotations,
  getMasterOcrText,
  getMasterRecords,
  listMasterKeyGroups,
  masterImageUrl,
  saveMasterAnnotations,
  selectMasterRecords,
  type MasterAnnotation,
  type MasterDocument,
  type MasterPage,
} from "./api";

const IMAGE_ZOOM_MIN = 1;
const IMAGE_ZOOM_MAX = 4;
const IMAGE_ZOOM_STEP = 0.25;

type TabKey = "ocr_data" | "keys_values";

type Props = {
  onBack: () => void;
};

export default function MasterDataBuilder({ onBack }: Props) {
  const [documents, setDocuments] = useState<MasterDocument[]>([]);
  const [doc, setDoc] = useState<MasterDocument | null>(null);
  const [page, setPage] = useState<MasterPage | null>(null);
  const [jobsPaneCollapsed, setJobsPaneCollapsed] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [selectN, setSelectN] = useState("20");
  const [imageZoom, setImageZoom] = useState(1);
  const [fittedImageSize, setFittedImageSize] = useState<{ w: number; h: number } | null>(null);
  const [imageNaturalSize, setImageNaturalSize] = useState<{ w: number; h: number } | null>(null);
  const [activeTab, setActiveTab] = useState<TabKey>("ocr_data");

  const viewerScrollRef = useRef<HTMLDivElement | null>(null);
  const activeThumbRef = useRef<HTMLButtonElement | null>(null);

  async function refresh() {
    setLoading(true);
    setError("");
    try {
      const data = await getMasterRecords();
      setDocuments(data.documents || []);
      const first = data.documents?.[0] || null;
      setDoc(first);
      setPage(first?.pages[0] || null);
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : String(exc));
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void refresh();
  }, []);

  async function runSelect() {
    const n = Number.parseInt(selectN, 10);
    if (!Number.isFinite(n) || n < 1) {
      setError("N must be a positive integer");
      return;
    }
    setLoading(true);
    setError("");
    try {
      await selectMasterRecords(n);
      await refresh();
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : String(exc));
      setLoading(false);
    }
  }

  function selectDoc(next: MasterDocument) {
    setDoc(next);
    setPage(next.pages[0] || null);
    setImageZoom(1);
    setFittedImageSize(null);
    setImageNaturalSize(null);
    setActiveTab("ocr_data");
  }

  function selectPage(next: MasterPage) {
    setPage(next);
    setImageZoom(1);
    setFittedImageSize(null);
    setImageNaturalSize(null);
    setActiveTab("ocr_data");
  }

  const pageIndex = useMemo(() => {
    if (!doc || !page) return -1;
    return doc.pages.findIndex(
      (item) => item.page_number === page.page_number && item.file_name === page.file_name,
    );
  }, [doc, page]);

  function goPage(delta: number) {
    if (!doc || pageIndex < 0) return;
    const next = doc.pages[pageIndex + delta];
    if (next) selectPage(next);
  }

  useEffect(() => {
    if (!imageNaturalSize || !viewerScrollRef.current) {
      setFittedImageSize(null);
      return;
    }
    const el = viewerScrollRef.current;
    const pad = 16;
    const maxW = Math.max(el.clientWidth - pad, 1);
    const maxH = Math.max(el.clientHeight - pad, 1);
    const scale = Math.min(maxW / imageNaturalSize.w, maxH / imageNaturalSize.h, 1);
    setFittedImageSize({
      w: Math.round(imageNaturalSize.w * scale * imageZoom),
      h: Math.round(imageNaturalSize.h * scale * imageZoom),
    });
  }, [imageNaturalSize, imageZoom, page?.file_name]);

  useEffect(() => {
    activeThumbRef.current?.scrollIntoView({ block: "nearest", inline: "nearest" });
  }, [page?.page_number, page?.file_name]);

  const currentSrc =
    doc && page ? masterImageUrl(doc.record_id, page.file_name) : "";

  return (
    <div className="shell">
      <header className="topbar">
        <div className="topbar-start">
          <div className="brand">
            <div>
              <strong>KV Extraction - Master Data Builder</strong>
              <p className="accuracy-line">
                {documents.length
                  ? `${documents.length} selected records (≤ max pages from Raw_Input)`
                  : "No selected_records.json yet — choose N and Select"}
              </p>
            </div>
          </div>
          <div className="master-select-bar">
            <label>
              N
              <input
                type="number"
                min={1}
                value={selectN}
                onChange={(e) => setSelectN(e.target.value)}
                disabled={loading}
              />
            </label>
            <button type="button" className="btn primary" disabled={loading} onClick={() => void runSelect()}>
              Select records
            </button>
          </div>
        </div>
        <div className="top-actions">
          <button
            type="button"
            className="icon-ghost-btn keep-mobile"
            title="Refresh"
            aria-label="Refresh"
            onClick={() => void refresh()}
          >
            <RefreshCw size={16} aria-hidden="true" />
          </button>
          <button type="button" className="btn secondary" onClick={onBack}>
            <ArrowLeft size={14} style={{ marginRight: 6, verticalAlign: "middle" }} />
            Back to Review
          </button>
        </div>
      </header>

      {error ? (
        <div className="banner mr-error" style={{ margin: 0, borderRadius: 0 }}>
          {error}
        </div>
      ) : null}

      <div className={`workspace ${jobsPaneCollapsed ? "jobs-collapsed" : "jobs-open"}`}>
        <div className="jobs-shell">
          <aside className={`jobs-pane ${jobsPaneCollapsed ? "collapsed" : ""}`}>
            <div className="pane-title">
              <h2>Files</h2>
              <div className="pane-title-end">
                {!jobsPaneCollapsed && <span>{documents.length}</span>}
                <button
                  type="button"
                  className="pane-collapse-btn"
                  title={jobsPaneCollapsed ? "Show files panel" : "Collapse files panel"}
                  aria-label={jobsPaneCollapsed ? "Show files panel" : "Collapse files panel"}
                  aria-expanded={!jobsPaneCollapsed}
                  onClick={() => setJobsPaneCollapsed((value) => !value)}
                >
                  {jobsPaneCollapsed ? (
                    <ChevronRight size={16} aria-hidden="true" />
                  ) : (
                    <ChevronLeft size={16} aria-hidden="true" />
                  )}
                </button>
              </div>
            </div>
            {!jobsPaneCollapsed && (
              <ul className="job-list">
                {loading && <li className="empty">Loading…</li>}
                {!loading &&
                  documents.map((item) => (
                    <li key={item.record_id} className="job-row">
                      <button
                        type="button"
                        className={`job-item ${doc?.record_id === item.record_id ? "active" : ""}`}
                        onClick={() => selectDoc(item)}
                      >
                        <div className="job-item-top">
                          <span className="job-name" title={item.record_id}>
                            {item.record_id}
                          </span>
                          <span className="pill pill-queued">{item.pages.length} pg</span>
                        </div>
                      </button>
                    </li>
                  ))}
                {!loading && !documents.length && <li className="empty">No selected records</li>}
              </ul>
            )}
          </aside>
        </div>

        <section className="review-pane">
          {!doc && <div className="empty-center">Select records with N, then pick a file</div>}
          {doc && (
            <>
              <div className="review-header">
                <div className="review-title-block">
                  <div className="review-title-row">
                    <h2 title={doc.record_id}>{doc.record_id}</h2>
                    <div className="pager-nav">
                      <button
                        type="button"
                        className="btn-pill"
                        onClick={() => goPage(-1)}
                        disabled={pageIndex <= 0}
                      >
                        Previous
                      </button>
                      <span className="pager-count">
                        {page ? page.page_number : 0} / {doc.pages.length || 0}
                      </span>
                      <button
                        type="button"
                        className="btn-pill"
                        onClick={() => goPage(1)}
                        disabled={pageIndex < 0 || pageIndex >= doc.pages.length - 1}
                      >
                        Next
                      </button>
                    </div>
                  </div>
                </div>
              </div>

              <div className="review-split">
                <div className="pages-col">
                  <div className="viewer">
                    {page && !fittedImageSize && (
                      <div className="viewer-loading" aria-live="polite">
                        <span className="viewer-loading-spinner" aria-hidden="true" />
                        Loading page…
                      </div>
                    )}
                    {page && (
                      <>
                        <div className="viewer-zoom-controls" role="group" aria-label="Image zoom">
                          <button
                            type="button"
                            className="viewer-zoom-btn"
                            onClick={() => setImageZoom((z) => Math.max(IMAGE_ZOOM_MIN, z - IMAGE_ZOOM_STEP))}
                            disabled={imageZoom <= IMAGE_ZOOM_MIN}
                          >
                            <Minus size={13} aria-hidden="true" />
                          </button>
                          <button
                            type="button"
                            className="viewer-zoom-btn viewer-zoom-fit"
                            onClick={() => setImageZoom(1)}
                          >
                            {Math.round(imageZoom * 100)}%
                          </button>
                          <button
                            type="button"
                            className="viewer-zoom-btn"
                            onClick={() => setImageZoom((z) => Math.min(IMAGE_ZOOM_MAX, z + IMAGE_ZOOM_STEP))}
                            disabled={imageZoom >= IMAGE_ZOOM_MAX}
                          >
                            <Plus size={13} aria-hidden="true" />
                          </button>
                        </div>
                        <div
                          ref={viewerScrollRef}
                          className={`viewer-scroll ${imageZoom > 1 ? "zoomed" : ""}`}
                        >
                          <div
                            className="page-image-stack"
                            style={
                              fittedImageSize
                                ? { width: fittedImageSize.w, height: fittedImageSize.h }
                                : { visibility: "hidden", width: 1, height: 1 }
                            }
                          >
                            <img
                              key={`${doc.record_id}-${page.file_name}`}
                              src={currentSrc}
                              alt={`Page ${page.page_number}`}
                              className="page-image"
                              draggable={false}
                              onLoad={(e) => {
                                const img = e.currentTarget;
                                setImageNaturalSize({ w: img.naturalWidth, h: img.naturalHeight });
                              }}
                            />
                          </div>
                        </div>
                      </>
                    )}
                  </div>
                  <div className="filmstrip">
                    {doc.pages.map((item) => (
                      <button
                        key={`${item.page_number}-${item.file_name}`}
                        ref={
                          page?.page_number === item.page_number && page?.file_name === item.file_name
                            ? activeThumbRef
                            : undefined
                        }
                        type="button"
                        className={`thumb ${
                          page?.page_number === item.page_number && page?.file_name === item.file_name
                            ? "active"
                            : ""
                        }`}
                        onClick={() => selectPage(item)}
                      >
                        <img src={masterImageUrl(doc.record_id, item.file_name)} alt="" loading="lazy" />
                        <span>{item.page_number}</span>
                      </button>
                    ))}
                  </div>
                </div>

                <div className="table-wrap mr-panel">
                  <div className="table-toolbar">
                    <div className="table-toolbar-start">
                      <h3 className="output-panel-title">Master Data</h3>
                      <div className="output-tabs" role="tablist">
                        <button
                          type="button"
                          role="tab"
                          className={`output-tab ${activeTab === "ocr_data" ? "active" : ""}`}
                          onClick={() => setActiveTab("ocr_data")}
                        >
                          OCR Data
                        </button>
                        <button
                          type="button"
                          role="tab"
                          className={`output-tab ${activeTab === "keys_values" ? "active" : ""}`}
                          onClick={() => setActiveTab("keys_values")}
                        >
                          Keys & Values
                        </button>
                      </div>
                    </div>
                  </div>
                  <div className="mr-box-header">
                    <h4>
                      {activeTab === "ocr_data" ? "OCR Data" : "Keys & Values"}
                      {page ? ` — Page ${page.page_number}` : ""}
                    </h4>
                    <p>
                      {activeTab === "ocr_data"
                        ? "OCR text for this page."
                        : "Annotate key/value pairs by key group for master training data."}
                    </p>
                  </div>
                  <div className="table-panel mr-body">
                    {!page ? (
                      <div className="empty">Select a page</div>
                    ) : activeTab === "ocr_data" ? (
                      <MasterOcrTab recordId={doc.record_id} page={page} />
                    ) : (
                      <KeysValuesTab recordId={doc.record_id} page={page} />
                    )}
                  </div>
                </div>
              </div>
            </>
          )}
        </section>
      </div>
    </div>
  );
}

function MasterOcrTab({ recordId, page }: { recordId: string; page: MasterPage }) {
  const [text, setText] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    getMasterOcrText(recordId, page.file_name)
      .then((value) => {
        if (!cancelled) setText(value);
      })
      .catch(() => {
        if (!cancelled) setText("Failed to load OCR output.");
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [recordId, page.file_name]);

  return (
    <div className="mr-tab-content">
      {loading ? <div className="empty">Loading OCR output…</div> : <pre className="mr-ocr-text">{text}</pre>}
    </div>
  );
}

type RowState = MasterAnnotation;

function emptyRow(group: string): RowState {
  return { group, key: "", value: "", value2: "" };
}

function KeysValuesTab({ recordId, page }: { recordId: string; page: MasterPage }) {
  const [groups, setGroups] = useState<string[]>([]);
  const [group, setGroup] = useState("Member DOB");
  const [rows, setRows] = useState<RowState[]>([emptyRow("Member DOB")]);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    void listMasterKeyGroups().then((list) => {
      if (list.length) {
        setGroups(list);
        setGroup((current) => (list.includes(current) ? current : list[0]));
      }
    });
  }, []);

  useEffect(() => {
    let cancelled = false;
    setError(null);
    setSaved(false);
    getMasterAnnotations(recordId, page.file_name, page.page_number)
      .then((data) => {
        if (cancelled) return;
        const anns = data.annotations || [];
        if (anns.length) {
          setRows(anns);
          setGroup(anns[0].group || "Member DOB");
          setSaved(true);
        } else {
          setRows([emptyRow(group)]);
        }
      })
      .catch((exc) => {
        if (!cancelled) setError(exc instanceof Error ? exc.message : String(exc));
      });
    return () => {
      cancelled = true;
    };
    // intentionally re-load when page changes; group default applied after
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [recordId, page.file_name, page.page_number]);

  const isEsign = group === "E-Sign";

  function changeGroup(next: string) {
    setGroup(next);
    setRows((prev) => prev.map((row) => ({ ...row, group: next })));
    setSaved(false);
  }

  const canSave = rows.some((row) => {
    if (!row.key.trim()) return false;
    if (isEsign) return !!(row.value.trim() || row.value2.trim());
    return !!row.value.trim();
  });

  async function save() {
    setSaving(true);
    setError(null);
    try {
      const payload = rows
        .map((row) => ({
          group,
          key: row.key.trim(),
          value: row.value.trim(),
          value2: isEsign ? row.value2.trim() : "",
        }))
        .filter((row) => {
          if (!row.key) return false;
          if (isEsign) return !!(row.value || row.value2);
          return !!row.value;
        });
      await saveMasterAnnotations(recordId, page.file_name, page.page_number, payload);
      setRows(payload.length ? payload : [emptyRow(group)]);
      setSaved(true);
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : String(exc));
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="mr-tab-content">
      <div className="mr-text-input" style={{ marginBottom: "0.75rem" }}>
        <label htmlFor="key-group">Key Group</label>
        <select
          id="key-group"
          value={group}
          disabled={saving}
          onChange={(e) => changeGroup(e.target.value)}
        >
          {(groups.length ? groups : [group]).map((name) => (
            <option key={name} value={name}>
              {name}
            </option>
          ))}
        </select>
      </div>

      <div className="mr-missing-list">
        {rows.map((row, index) => (
          <div key={index} className="mr-missing-row mr-missed-key-row master-kv-row">
            <input
              type="text"
              placeholder="Key"
              value={row.key}
              disabled={saving}
              onChange={(e) => {
                const key = e.target.value;
                setRows((prev) => prev.map((item, i) => (i === index ? { ...item, key } : item)));
                setSaved(false);
              }}
            />
            <input
              type="text"
              placeholder={isEsign ? "Value 1" : "Value"}
              value={row.value}
              disabled={saving}
              onChange={(e) => {
                const value = e.target.value;
                setRows((prev) => prev.map((item, i) => (i === index ? { ...item, value } : item)));
                setSaved(false);
              }}
            />
            {isEsign && (
              <input
                type="text"
                placeholder="Value 2"
                value={row.value2}
                disabled={saving}
                onChange={(e) => {
                  const value2 = e.target.value;
                  setRows((prev) => prev.map((item, i) => (i === index ? { ...item, value2 } : item)));
                  setSaved(false);
                }}
              />
            )}
            {rows.length > 1 && (
              <button
                type="button"
                className="mr-remove-row"
                disabled={saving}
                onClick={() => {
                  setRows((prev) => prev.filter((_, i) => i !== index));
                  setSaved(false);
                }}
              >
                Remove
              </button>
            )}
          </div>
        ))}
        <button
          type="button"
          className="btn secondary mr-add-btn"
          disabled={saving}
          onClick={() => {
            setRows((prev) => [...prev, emptyRow(group)]);
            setSaved(false);
          }}
        >
          Add another
        </button>
      </div>

      {saved && (
        <div className="mr-reviewed-banner">
          <div>
            <strong>Saved.</strong> Written to <code>master_data.json</code> and <code>master_data.xlsx</code>.
          </div>
        </div>
      )}
      {error && <div className="banner mr-error">{error}</div>}
      <div className="mr-actions">
        <button type="button" className="btn primary" disabled={!canSave || saving} onClick={() => void save()}>
          {saving ? "Saving…" : "Save"}
        </button>
      </div>
    </div>
  );
}
