import { useEffect, useMemo, useRef, useState } from "react";
import { ArrowLeft, ChevronLeft, ChevronRight, Minus, Plus, RefreshCw } from "lucide-react";
import {
  getMasterAnnotations,
  getMasterMeasurements,
  getMasterOcrText,
  getMasterRecords,
  listMasterKeyGroups,
  masterImageUrl,
  saveMasterAnnotations,
  saveMasterMeasurements,
  selectMasterRecords,
  type MasterAnnotation,
  type MasterDocument,
  type MasterPage,
} from "./api";

const IMAGE_ZOOM_MIN = 1;
const IMAGE_ZOOM_MAX = 4;
const IMAGE_ZOOM_STEP = 0.25;
const BAND_MAX = 0.95;

type Props = {
  onBack: () => void;
};

function clampFrac(value: number) {
  if (!Number.isFinite(value)) return 0;
  return Math.min(Math.max(value, 0), BAND_MAX);
}

function fmtPct(frac: number) {
  return `${(frac * 100).toFixed(1)}%`;
}

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
  const [headerFrac, setHeaderFrac] = useState(0);
  const [footerFrac, setFooterFrac] = useState(0);
  const [bandDirty, setBandDirty] = useState(false);
  const [bandSaving, setBandSaving] = useState(false);
  const [bandSaved, setBandSaved] = useState(false);
  const [bandError, setBandError] = useState<string | null>(null);

  const viewerScrollRef = useRef<HTMLDivElement | null>(null);
  const activeThumbRef = useRef<HTMLButtonElement | null>(null);
  const imageStackRef = useRef<HTMLDivElement | null>(null);
  const dragKindRef = useRef<"header" | "footer" | null>(null);
  const fracsRef = useRef({ header: 0, footer: 0 });

  useEffect(() => {
    fracsRef.current = { header: headerFrac, footer: footerFrac };
  }, [headerFrac, footerFrac]);

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
  }

  function selectPage(next: MasterPage) {
    setPage(next);
    setImageZoom(1);
    setFittedImageSize(null);
    setImageNaturalSize(null);
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

  useEffect(() => {
    if (!doc || !page) {
      setHeaderFrac(0);
      setFooterFrac(0);
      setBandDirty(false);
      setBandSaved(false);
      setBandError(null);
      return;
    }
    let cancelled = false;
    setBandError(null);
    setBandDirty(false);
    setBandSaved(false);
    getMasterMeasurements(doc.record_id, page.file_name, page.page_number)
      .then((data) => {
        if (cancelled) return;
        setHeaderFrac(clampFrac(data.header_frac || 0));
        setFooterFrac(clampFrac(data.footer_frac || 0));
        setBandSaved((data.header_frac || 0) > 0 || (data.footer_frac || 0) > 0);
      })
      .catch((exc) => {
        if (!cancelled) setBandError(exc instanceof Error ? exc.message : String(exc));
      });
    return () => {
      cancelled = true;
    };
  }, [doc?.record_id, page?.file_name, page?.page_number]);

  useEffect(() => {
    function onMove(event: PointerEvent) {
      const kind = dragKindRef.current;
      const stack = imageStackRef.current;
      if (!kind || !stack) return;
      const rect = stack.getBoundingClientRect();
      if (rect.height <= 0) return;
      const y = (event.clientY - rect.top) / rect.height;
      if (kind === "header") {
        const max = Math.max(0, BAND_MAX - fracsRef.current.footer);
        setHeaderFrac(clampFrac(Math.min(Math.max(y, 0), max)));
      } else {
        const fromBottom = 1 - y;
        const max = Math.max(0, BAND_MAX - fracsRef.current.header);
        setFooterFrac(clampFrac(Math.min(Math.max(fromBottom, 0), max)));
      }
      setBandDirty(true);
      setBandSaved(false);
    }
    function onUp() {
      dragKindRef.current = null;
    }
    window.addEventListener("pointermove", onMove);
    window.addEventListener("pointerup", onUp);
    window.addEventListener("pointercancel", onUp);
    return () => {
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerup", onUp);
      window.removeEventListener("pointercancel", onUp);
    };
  }, []);

  function startBandDrag(kind: "header" | "footer") {
    dragKindRef.current = kind;
  }

  async function saveBands() {
    if (!doc || !page) return;
    setBandSaving(true);
    setBandError(null);
    try {
      await saveMasterMeasurements(
        doc.record_id,
        page.file_name,
        page.page_number,
        headerFrac,
        footerFrac,
      );
      setBandDirty(false);
      setBandSaved(true);
    } catch (exc) {
      setBandError(exc instanceof Error ? exc.message : String(exc));
    } finally {
      setBandSaving(false);
    }
  }

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

              <div className="review-split master-triple-split">
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
                            ref={imageStackRef}
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
                            <div
                              className="master-band master-band-header"
                              style={{ height: `${headerFrac * 100}%` }}
                              aria-hidden={headerFrac <= 0}
                            >
                              {headerFrac > 0.02 && (
                                <span className="master-band-label">Header {fmtPct(headerFrac)}</span>
                              )}
                            </div>
                            <div
                              className="master-band-handle master-band-handle-header"
                              style={{ top: `calc(${headerFrac * 100}% - 7px)` }}
                              title="Drag down to set header range"
                              onPointerDown={(e) => {
                                e.preventDefault();
                                e.currentTarget.setPointerCapture?.(e.pointerId);
                                startBandDrag("header");
                              }}
                            />
                            <div
                              className="master-band master-band-footer"
                              style={{ height: `${footerFrac * 100}%` }}
                              aria-hidden={footerFrac <= 0}
                            >
                              {footerFrac > 0.02 && (
                                <span className="master-band-label">Footer {fmtPct(footerFrac)}</span>
                              )}
                            </div>
                            <div
                              className="master-band-handle master-band-handle-footer"
                              style={{ bottom: `calc(${footerFrac * 100}% - 7px)` }}
                              title="Drag up to set footer range"
                              onPointerDown={(e) => {
                                e.preventDefault();
                                e.currentTarget.setPointerCapture?.(e.pointerId);
                                startBandDrag("footer");
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

                <div className="table-wrap mr-panel master-side-panel">
                  <div className="table-toolbar">
                    <div className="table-toolbar-start">
                      <h3 className="output-panel-title">OCR Data</h3>
                    </div>
                  </div>
                  <div className="mr-box-header">
                    <h4>OCR Data{page ? ` — Page ${page.page_number}` : ""}</h4>
                    <p>OCR text for this page.</p>
                  </div>
                  <div className="table-panel mr-body">
                    {!page ? (
                      <div className="empty">Select a page</div>
                    ) : (
                      <MasterOcrTab recordId={doc.record_id} page={page} />
                    )}
                  </div>
                </div>

                <div className="table-wrap mr-panel master-side-panel">
                  <div className="table-toolbar">
                    <div className="table-toolbar-start">
                      <h3 className="output-panel-title">Keys & Values</h3>
                    </div>
                  </div>
                  {page && (
                    <div className="master-band-measures">
                      <div className="master-band-measures-row">
                        <div className="master-band-measures-stats">
                          <span className="master-band-stat-header">
                            Header <strong>{fmtPct(headerFrac)}</strong>
                          </span>
                          <span className="master-band-stat-footer">
                            Footer <strong>{fmtPct(footerFrac)}</strong>
                          </span>
                        </div>
                        <button
                          type="button"
                          className="btn primary"
                          disabled={bandSaving || (!bandDirty && bandSaved)}
                          onClick={() => void saveBands()}
                        >
                          {bandSaving ? "Saving…" : bandSaved && !bandDirty ? "Saved" : "Save bands"}
                        </button>
                      </div>
                      <p className="master-band-measures-hint">
                        Stretch the blue band from the top and the amber band from the bottom on the
                        page image to measure header/footer ranges.
                      </p>
                      {bandSaved && !bandDirty && (
                        <div className="mr-reviewed-banner">
                          <div>
                            <strong>Bands saved.</strong> Written to{" "}
                            <code>Header Measurements</code> / <code>Footer Measurements</code>.
                          </div>
                        </div>
                      )}
                      {bandError && <div className="banner mr-error">{bandError}</div>}
                    </div>
                  )}
                  <div className="mr-box-header">
                    <h4>Keys & Values{page ? ` — Page ${page.page_number}` : ""}</h4>
                    <p>Annotate key/value pairs by key group for master training data.</p>
                  </div>
                  <div className="table-panel mr-body">
                    {!page ? (
                      <div className="empty">Select a page</div>
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
  const [groups, setGroups] = useState<string[]>(["Member DOB"]);
  const [rows, setRows] = useState<RowState[]>([emptyRow("Member DOB")]);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    void listMasterKeyGroups().then((list) => {
      if (list.length) setGroups(list);
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
          setSaved(true);
        } else {
          setRows([emptyRow("Member DOB")]);
        }
      })
      .catch((exc) => {
        if (!cancelled) setError(exc instanceof Error ? exc.message : String(exc));
      });
    return () => {
      cancelled = true;
    };
  }, [recordId, page.file_name, page.page_number]);

  const canSave = rows.some((row) => {
    if (!row.key.trim()) return false;
    if (row.group === "E-Sign") return !!(row.value.trim() || row.value2.trim());
    return !!row.value.trim();
  });

  async function save() {
    setSaving(true);
    setError(null);
    try {
      const payload = rows
        .map((row) => ({
          group: row.group,
          key: row.key.trim(),
          value: row.value.trim(),
          value2: row.group === "E-Sign" ? row.value2.trim() : "",
        }))
        .filter((row) => {
          if (!row.key) return false;
          if (row.group === "E-Sign") return !!(row.value || row.value2);
          return !!row.value;
        });
      await saveMasterAnnotations(recordId, page.file_name, page.page_number, payload);
      setRows(payload.length ? payload : [emptyRow(groups[0] || "Member DOB")]);
      setSaved(true);
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : String(exc));
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="mr-tab-content">
      <div className="mr-missing-list">
        {rows.map((row, index) => {
          const isEsign = row.group === "E-Sign";
          return (
            <div key={index} className="mr-missing-row mr-missed-key-row master-kv-row">
              <select
                value={row.group}
                disabled={saving}
                aria-label={`Key group for row ${index + 1}`}
                onChange={(e) => {
                  const group = e.target.value;
                  setRows((prev) =>
                    prev.map((item, i) =>
                      i === index
                        ? { ...item, group, value2: group === "E-Sign" ? item.value2 : "" }
                        : item,
                    ),
                  );
                  setSaved(false);
                }}
              >
                {groups.map((name) => (
                  <option key={name} value={name}>
                    {name}
                  </option>
                ))}
              </select>
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
                    setRows((prev) =>
                      prev.map((item, i) => (i === index ? { ...item, value2 } : item)),
                    );
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
          );
        })}
        <button
          type="button"
          className="btn secondary mr-add-btn"
          disabled={saving}
          onClick={() => {
            setRows((prev) => [...prev, emptyRow(groups[0] || "Member DOB")]);
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
