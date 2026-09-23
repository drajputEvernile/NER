import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ChevronLeft, ChevronRight, Minus, Plus, RefreshCw } from "lucide-react";
import {
  getRun,
  imageUrl,
  listRuns,
  type DocumentRow,
  type Hit,
  type PageRow,
  type RunDetail,
  type RunSummary,
} from "./api";
import ManualReviewPanel from "./ManualReviewPanel";

const IMAGE_ZOOM_MIN = 1;
const IMAGE_ZOOM_MAX = 4;
const IMAGE_ZOOM_STEP = 0.25;

const IMAGE_TABS = [
  { id: "raw", label: "Original" },
  { id: "overall", label: "Overall" },
  { id: "dob", label: "DOB" },
  { id: "ID", label: "ID" },
  { id: "MName", label: "MName" },
  { id: "PName", label: "PName" },
  { id: "ESig", label: "Esign" },
] as const;

type ImageTab = (typeof IMAGE_TABS)[number]["id"];

type AccuracyStats = {
  overall: number | null;
  dob: number | null;
  ID: number | null;
  MName: number | null;
  PName: number | null;
  ESig: number | null;
};

/** Accuracy is per key–value pair: both must be correct to count as accurate. */
function isHitReviewed(hit: Hit): boolean {
  return !!hit.key_accuracy && !!hit.value_accuracy;
}

function isHitCorrect(hit: Hit): boolean {
  return hit.key_accuracy === "correct" && hit.value_accuracy === "correct";
}

function computeAccuracy(hits: Hit[]): AccuracyStats {
  function pct(field?: string): number | null {
    const subset = field ? hits.filter((hit) => hit.field === field) : hits;
    const reviewed = subset.filter(isHitReviewed);
    if (!reviewed.length) return null;
    const correct = reviewed.filter(isHitCorrect).length;
    return Math.round((correct / reviewed.length) * 100);
  }
  return {
    overall: pct(),
    dob: pct("dob"),
    ID: pct("ID"),
    MName: pct("MName"),
    PName: pct("PName"),
    ESig: pct("ESig"),
  };
}

type DocStatus = "Pending" | "InProgress" | "Reviewed";

function documentStatus(doc: DocumentRow): DocStatus {
  const hits = doc.pages.flatMap((pageRow) => pageRow.hits);
  if (!hits.length) return "Pending";
  const reviewed = hits.filter(isHitReviewed).length;
  if (reviewed === 0) return "Pending";
  if (reviewed >= hits.length) return "Reviewed";
  return "InProgress";
}

function formatAccuracyLine(stats: AccuracyStats): string {
  const fmt = (value: number | null) => (value == null ? "—" : `${value}%`);
  return [
    `Accuracy_Overall: ${fmt(stats.overall)}`,
    `Accuracy_DOB: ${fmt(stats.dob)}`,
    `Accuracy_ID: ${fmt(stats.ID)}`,
    `Accuracy_MName: ${fmt(stats.MName)}`,
    `Accuracy_PName: ${fmt(stats.PName)}`,
    `Accuracy_Esign: ${fmt(stats.ESig)}`,
  ].join(" - ");
}

export default function App() {
  const [runs, setRuns] = useState<RunSummary[]>([]);
  const [runId, setRunId] = useState<string>("");
  const [run, setRun] = useState<RunDetail | null>(null);
  const [doc, setDoc] = useState<DocumentRow | null>(null);
  const [page, setPage] = useState<PageRow | null>(null);
  const [jobsPaneCollapsed, setJobsPaneCollapsed] = useState(false);
  const [imageTab, setImageTab] = useState<ImageTab>("overall");
  const [imageZoom, setImageZoom] = useState(1);
  const [fittedImageSize, setFittedImageSize] = useState<{ w: number; h: number } | null>(null);
  const [imageNaturalSize, setImageNaturalSize] = useState<{ w: number; h: number } | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  const viewerScrollRef = useRef<HTMLDivElement | null>(null);
  const pageImageRef = useRef<HTMLImageElement | null>(null);
  const activeThumbRef = useRef<HTMLButtonElement | null>(null);

  const refreshRuns = useCallback(async () => {
    try {
      const rows = await listRuns();
      setRuns(rows);
      if (!runId && rows.length) {
        setRunId(rows[0].id);
      }
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : String(exc));
    }
  }, [runId]);

  useEffect(() => {
    void refreshRuns();
  }, [refreshRuns]);

  const loadRun = useCallback(async (id: string) => {
    if (!id) {
      setRun(null);
      setDoc(null);
      setPage(null);
      return;
    }
    setLoading(true);
    setError("");
    try {
      const detail = await getRun(id);
      setRun(detail);
      const firstDoc = detail.documents[0] || null;
      setDoc(firstDoc);
      setPage(firstDoc?.pages[0] || null);
      setImageZoom(1);
      setFittedImageSize(null);
      setImageNaturalSize(null);
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : String(exc));
      setRun(null);
      setDoc(null);
      setPage(null);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadRun(runId);
  }, [runId, loadRun]);

  function selectDoc(next: DocumentRow) {
    setDoc(next);
    setPage(next.pages[0] || null);
    setImageZoom(1);
    setFittedImageSize(null);
    setImageNaturalSize(null);
  }

  function selectPage(next: PageRow) {
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

  function patchHitAccuracy(
    hitId: string,
    keyAccuracy: string,
    valueAccuracy: string,
    reason = "",
    actualKey = "",
    actualValue = "",
  ) {
    const patchHits = (hits: Hit[]) =>
      hits.map((item) =>
        item.id === hitId
          ? {
              ...item,
              key_accuracy: keyAccuracy,
              value_accuracy: valueAccuracy,
              reason: keyAccuracy === "incorrect" || valueAccuracy === "incorrect" ? reason : "",
              actual_key: keyAccuracy === "incorrect" ? actualKey : "",
              actual_value: valueAccuracy === "incorrect" ? actualValue : "",
            }
          : item,
      );
    setRun((current) => {
      if (!current) return current;
      return {
        ...current,
        documents: current.documents.map((document) => ({
          ...document,
          pages: document.pages.map((pageRow) => ({
            ...pageRow,
            hits: patchHits(pageRow.hits),
          })),
        })),
        review_count: current.documents
          .flatMap((document) => document.pages)
          .flatMap((pageRow) => pageRow.hits)
          .map((item) =>
            item.id === hitId
              ? keyAccuracy && valueAccuracy
                ? "done"
                : ""
              : item.key_accuracy && item.value_accuracy
                ? "done"
                : "",
          )
          .filter(Boolean).length,
      };
    });
    setPage((current) => (current ? { ...current, hits: patchHits(current.hits) } : current));
    setDoc((current) =>
      current
        ? {
            ...current,
            pages: current.pages.map((pageRow) => ({
              ...pageRow,
              hits: patchHits(pageRow.hits),
            })),
          }
        : current,
    );
  }

  // Fit image into viewer when natural size is known.
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
  }, [imageNaturalSize, imageZoom, page?.file_name, imageTab]);

  useEffect(() => {
    activeThumbRef.current?.scrollIntoView({ block: "nearest", inline: "nearest" });
  }, [page?.page_number, page?.file_name]);

  const pageFields = useMemo(() => {
    const present = new Set<string>();
    for (const hit of page?.hits || []) {
      present.add(hit.field);
    }
    return present;
  }, [page?.hits]);

  const visibleImageTabs = useMemo(
    () =>
      IMAGE_TABS.filter((tab) => tab.id === "raw" || tab.id === "overall" || pageFields.has(tab.id)),
    [pageFields],
  );

  useEffect(() => {
    if (!visibleImageTabs.some((tab) => tab.id === imageTab)) {
      setImageTab("overall");
      setFittedImageSize(null);
      setImageNaturalSize(null);
    }
  }, [visibleImageTabs, imageTab]);

  function patchMissedKeys(keys: { field: string; key: string; value: string }[]) {
    setPage((current) => (current ? { ...current, missed_keys: keys } : current));
    setDoc((current) =>
      current && page
        ? {
            ...current,
            pages: current.pages.map((pageRow) =>
              pageRow.page_number === page.page_number && pageRow.file_name === page.file_name
                ? { ...pageRow, missed_keys: keys }
                : pageRow,
            ),
          }
        : current,
    );
    setRun((current) => {
      if (!current || !doc || !page) return current;
      return {
        ...current,
        documents: current.documents.map((document) =>
          document.record_id !== doc.record_id
            ? document
            : {
                ...document,
                pages: document.pages.map((pageRow) =>
                  pageRow.page_number === page.page_number && pageRow.file_name === page.file_name
                    ? { ...pageRow, missed_keys: keys }
                    : pageRow,
                ),
              },
        ),
      };
    });
  }

  const currentSrc =
    run && doc && page ? imageUrl(run.id, doc.record_id, page.file_name, imageTab) : "";

  const runAccuracyLine = useMemo(() => {
    if (!run) return formatAccuracyLine(computeAccuracy([]));
    const hits = run.documents.flatMap((document) => document.pages.flatMap((pageRow) => pageRow.hits));
    return formatAccuracyLine(computeAccuracy(hits));
  }, [run]);

  const docAccuracyLine = useMemo(() => {
    if (!doc) return formatAccuracyLine(computeAccuracy([]));
    const hits = doc.pages.flatMap((pageRow) => pageRow.hits);
    return formatAccuracyLine(computeAccuracy(hits));
  }, [doc]);

  function changeImageTab(next: ImageTab) {
    setImageTab(next);
    setFittedImageSize(null);
    setImageNaturalSize(null);
  }

  return (
    <div className="shell">
      <header className="topbar">
        <div className="topbar-start">
          <div className="brand">
            <div>
              <strong>
                KV Extraction - Manual Review
                {runId ? ` - ${runId}` : ""}
              </strong>
              <p className="accuracy-line" title={runAccuracyLine}>
                {runAccuracyLine}
              </p>
            </div>
          </div>
        </div>
        <div className="top-actions">
          <button
            type="button"
            className="icon-ghost-btn keep-mobile"
            title="Refresh run"
            aria-label="Refresh run"
            onClick={() => {
              void refreshRuns();
              if (runId) void loadRun(runId);
            }}
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
              <h2>Files</h2>
              <div className="pane-title-end">
                {!jobsPaneCollapsed && <span>{run?.documents.length || 0}</span>}
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
                  run?.documents.map((item) => {
                    const status = documentStatus(item);
                    const pillClass =
                      status === "Reviewed"
                        ? "pill-complete"
                        : status === "InProgress"
                          ? "pill-processing"
                          : "pill-queued";
                    return (
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
                            <span className={`pill ${pillClass}`} title={status}>
                              {status}
                            </span>
                          </div>
                          <div className="job-meta">{item.pages.length} pages</div>
                        </button>
                      </li>
                    );
                  })}
                {!loading && !run?.documents.length && <li className="empty">No files yet</li>}
              </ul>
            )}
          </aside>
        </div>

        <section className="review-pane">
          {!doc && <div className="empty-center">Select a file to review pages + output</div>}
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
                    <span className="review-meta-primary">{doc.pages.length} pg</span>
                  </div>
                  <p className="accuracy-line" title={docAccuracyLine}>
                    {docAccuracyLine}
                  </p>
                  <div className="image-tabs review-image-ribbon" role="tablist" aria-label="Page image overlay">
                    {visibleImageTabs.map((tab) => (
                      <button
                        key={tab.id}
                        type="button"
                        role="tab"
                        aria-selected={imageTab === tab.id}
                        className={`image-tab output-tab ${imageTab === tab.id ? "active" : ""}`}
                        onClick={() => changeImageTab(tab.id)}
                      >
                        {tab.label}
                      </button>
                    ))}
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
                              ref={pageImageRef}
                              key={`${doc.record_id}-${page.file_name}-${imageTab}`}
                              src={currentSrc}
                              alt={`Page ${page.page_number} (${imageTab})`}
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
                        title={`Page ${item.page_number}`}
                      >
                        <img
                          src={imageUrl(run!.id, doc.record_id, item.file_name, "overall")}
                          alt=""
                          loading="lazy"
                        />
                        <span>{item.page_number}</span>
                      </button>
                    ))}
                  </div>
                </div>

                <ManualReviewPanel
                  runId={run!.id}
                  recordId={doc.record_id}
                  page={page}
                  onHitAccuracy={patchHitAccuracy}
                  onMissedKeysSaved={patchMissedKeys}
                />
              </div>
            </>
          )}
        </section>
      </div>
    </div>
  );
}
