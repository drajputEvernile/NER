import { useCallback, useEffect, useRef, useState } from "react";
import { Check, ChevronLeft, ChevronRight, Minus, Plus, RefreshCw } from "lucide-react";
import { getRecords, imageUrl, selectRecord, unselectRecord, type RecordList, type RecordRow } from "./api";

const IMAGE_ZOOM_MIN = 1;
const IMAGE_ZOOM_MAX = 4;
const IMAGE_ZOOM_STEP = 0.25;

export default function Chooser() {
  const [data, setData] = useState<RecordList | null>(null);
  const [docId, setDocId] = useState("");
  const [pageIdx, setPageIdx] = useState(0);
  const [jobsPaneCollapsed, setJobsPaneCollapsed] = useState(false);
  const [imageZoom, setImageZoom] = useState(1);
  const [imageNaturalSize, setImageNaturalSize] = useState<{ w: number; h: number } | null>(null);
  const [fittedImageSize, setFittedImageSize] = useState<{ w: number; h: number } | null>(null);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  const viewerScrollRef = useRef<HTMLDivElement | null>(null);
  const activeThumbRef = useRef<HTMLButtonElement | null>(null);

  const doc = data?.records.find((item) => item.record_id === docId) || null;
  const fileName = doc?.pages[pageIdx] || "";
  const pageKey = doc ? `${doc.record_id}|${fileName}` : "";

  const load = useCallback(async (keepSelection: boolean) => {
    setLoading(true);
    try {
      const next = await getRecords();
      setData(next);
      setError("");
      if (!keepSelection) {
        const first = next.records.find((item) => !item.selected) || next.records[0];
        setDocId(first?.record_id || "");
        setPageIdx(0);
      }
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : String(exc));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load(false);
  }, [load]);

  function resetImage() {
    setImageZoom(1);
    setFittedImageSize(null);
    setImageNaturalSize(null);
  }

  function selectDoc(next: RecordRow) {
    setDocId(next.record_id);
    setPageIdx(0);
    resetImage();
  }

  function selectPage(index: number) {
    setPageIdx(index);
    resetImage();
  }

  const fitImage = useCallback(() => {
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
  }, [imageNaturalSize, imageZoom]);

  useEffect(() => {
    fitImage();
  }, [fitImage, pageKey, jobsPaneCollapsed]);

  useEffect(() => {
    window.addEventListener("resize", fitImage);
    return () => window.removeEventListener("resize", fitImage);
  }, [fitImage]);

  useEffect(() => {
    activeThumbRef.current?.scrollIntoView({ block: "nearest", inline: "nearest" });
  }, [pageKey]);

  async function toggleSelected() {
    if (!doc) return;
    setSaving(true);
    try {
      const result = await (doc.selected ? unselectRecord(doc.record_id) : selectRecord(doc.record_id));
      setData((current) => {
        if (!current) return current;
        const records = current.records.map((item) =>
          item.record_id === doc.record_id ? { ...item, selected: result.selected } : item,
        );
        return { ...current, records, selected: records.filter((item) => item.selected).length };
      });
      setError("");
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : String(exc));
    } finally {
      setSaving(false);
    }
  }

  const limit = data?.n ? `${data.n} or fewer pages` : "any number of pages";
  const summary = data
    ? [
        `${data.shown} records with ${limit}`,
        `${data.selected} selected`,
        data.hidden_over_n ? `${data.hidden_over_n} hidden (more than ${data.n} pages)` : "",
      ]
        .filter(Boolean)
        .join(" · ")
    : "";

  return (
    <div className="shell">
      <header className="topbar">
        <div className="topbar-start">
          <div className="brand">
            <div>
              <strong>Data Set Chooser</strong>
              <p className="accuracy-line" title={summary}>
                {summary}
              </p>
              {data ? (
                <p className="chooser-paths" title={`${data.raw_path} → ${data.selected_path}`}>
                  {data.raw_path} → {data.selected_path}
                </p>
              ) : null}
            </div>
          </div>
        </div>
        <div className="top-actions">
          <button
            type="button"
            className="icon-ghost-btn keep-mobile"
            title="Refresh"
            aria-label="Refresh"
            onClick={() => void load(true)}
          >
            <RefreshCw size={16} aria-hidden="true" />
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
              <h2>Records</h2>
              <div className="pane-title-end">
                {!jobsPaneCollapsed && <span>{data?.records.length || 0}</span>}
                <button
                  type="button"
                  className="pane-collapse-btn"
                  title={jobsPaneCollapsed ? "Show records panel" : "Collapse records panel"}
                  aria-label={jobsPaneCollapsed ? "Show records panel" : "Collapse records panel"}
                  aria-expanded={!jobsPaneCollapsed}
                  onClick={() => setJobsPaneCollapsed((value) => !value)}
                >
                  {jobsPaneCollapsed ? <ChevronRight size={16} aria-hidden="true" /> : <ChevronLeft size={16} aria-hidden="true" />}
                </button>
              </div>
            </div>
            {!jobsPaneCollapsed && (
              <ul className="job-list">
                {loading && !data && <li className="empty">Loading…</li>}
                {data?.records.map((item) => (
                  <li key={item.record_id} className="job-row">
                    <button
                      type="button"
                      className={`job-item ${docId === item.record_id ? "active" : ""}`}
                      onClick={() => selectDoc(item)}
                    >
                      <div className="job-item-top">
                        <span className="job-name" title={item.record_id}>
                          {item.record_id}
                        </span>
                        <span className={`pill ${item.selected ? "pill-complete" : "pill-queued"}`}>
                          {item.selected ? "Selected" : "Not selected"}
                        </span>
                      </div>
                      <div className="job-meta">
                        {item.page_count} {item.page_count === 1 ? "page" : "pages"}
                      </div>
                    </button>
                  </li>
                ))}
                {data && !data.records.length && <li className="empty">No records to show in {data.raw_path}</li>}
              </ul>
            )}
          </aside>
        </div>

        <section className="review-pane">
          {!doc && <div className="empty-center">{loading ? "Loading records…" : "Select a record to view"}</div>}
          {doc && (
            <>
              <div className="review-header">
                <div className="review-title-block">
                  <div className="review-title-row">
                    <h2 title={doc.record_id}>{doc.record_id}</h2>
                    <div className="pager-nav">
                      <button type="button" className="btn-pill" onClick={() => selectPage(pageIdx - 1)} disabled={pageIdx <= 0}>
                        Previous
                      </button>
                      <span className="pager-count">
                        {pageIdx + 1} / {doc.pages.length}
                      </span>
                      <button
                        type="button"
                        className="btn-pill"
                        onClick={() => selectPage(pageIdx + 1)}
                        disabled={pageIdx >= doc.pages.length - 1}
                      >
                        Next
                      </button>
                    </div>
                    <span className="review-meta-primary" title={fileName}>
                      {fileName}
                    </span>
                    <div className="chooser-actions">
                      <button
                        type="button"
                        className={`btn ${doc.selected ? "secondary" : "primary"}`}
                        onClick={() => void toggleSelected()}
                        disabled={saving}
                        title={doc.selected ? "Remove this record's copied images from the training folder" : "Copy this record to the training folder"}
                      >
                        {doc.selected ? (
                          <>
                            <Check size={14} aria-hidden="true" /> Selected · Remove
                          </>
                        ) : (
                          "Select for training"
                        )}
                      </button>
                    </div>
                  </div>
                </div>
              </div>

              <div className="review-split chooser-split">
                <div className="pages-col">
                  <div className="viewer">
                    {!fittedImageSize && (
                      <div className="viewer-loading" aria-live="polite">
                        <span className="viewer-loading-spinner" aria-hidden="true" />
                        Loading page…
                      </div>
                    )}
                    <div className="viewer-zoom-controls" role="group" aria-label="Image zoom">
                      <button
                        type="button"
                        className="viewer-zoom-btn"
                        onClick={() => setImageZoom((z) => Math.max(IMAGE_ZOOM_MIN, z - IMAGE_ZOOM_STEP))}
                        disabled={imageZoom <= IMAGE_ZOOM_MIN}
                        title="Zoom out"
                        aria-label="Zoom out"
                      >
                        <Minus size={13} aria-hidden="true" />
                      </button>
                      <button
                        type="button"
                        className="viewer-zoom-btn viewer-zoom-fit"
                        onClick={() => setImageZoom(1)}
                        title="Fit full page"
                        aria-label="Fit full page"
                      >
                        {Math.round(imageZoom * 100)}%
                      </button>
                      <button
                        type="button"
                        className="viewer-zoom-btn"
                        onClick={() => setImageZoom((z) => Math.min(IMAGE_ZOOM_MAX, z + IMAGE_ZOOM_STEP))}
                        disabled={imageZoom >= IMAGE_ZOOM_MAX}
                        title="Zoom in"
                        aria-label="Zoom in"
                      >
                        <Plus size={13} aria-hidden="true" />
                      </button>
                    </div>
                    <div ref={viewerScrollRef} className={`viewer-scroll ${imageZoom > 1 ? "zoomed" : ""}`}>
                      <div
                        className="page-image-stack"
                        style={
                          fittedImageSize
                            ? { width: fittedImageSize.w, height: fittedImageSize.h }
                            : { visibility: "hidden", width: 1, height: 1 }
                        }
                      >
                        <img
                          key={pageKey}
                          src={imageUrl(doc.record_id, fileName)}
                          alt={`${doc.record_id} page ${pageIdx + 1}`}
                          className="page-image"
                          draggable={false}
                          onLoad={(event) => {
                            const img = event.currentTarget;
                            setImageNaturalSize({ w: img.naturalWidth, h: img.naturalHeight });
                          }}
                        />
                      </div>
                    </div>
                  </div>

                  <div className="filmstrip">
                    {doc.pages.map((name, index) => (
                      <button
                        key={name}
                        ref={index === pageIdx ? activeThumbRef : undefined}
                        type="button"
                        className={`thumb ${index === pageIdx ? "active" : ""}`}
                        onClick={() => selectPage(index)}
                        title={name}
                      >
                        <img src={imageUrl(doc.record_id, name)} alt="" loading="lazy" />
                        <span>{index + 1}</span>
                      </button>
                    ))}
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
