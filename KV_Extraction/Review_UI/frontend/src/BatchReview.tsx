import { useCallback, useEffect, useMemo, useRef, useState, type MouseEvent } from "react";
import { ChevronLeft, ChevronRight, Home as HomeIcon, Minus, Plus, RefreshCw } from "lucide-react";
import {
  clearReview,
  getMeta,
  getPage,
  getPageOcr,
  getRun,
  imageUrl,
  savePageInfo,
  saveReview,
  type Box,
  type Candidate,
  type DocumentRow,
  type Meta,
  type OcrWord,
  type PageDetail,
  type PageOcr,
  type RunDetail,
} from "./api";
import { fmtPairs } from "./format";
import OcrPanel from "./OcrPanel";
import ReviewPanel, { type KeyNeighbors } from "./ReviewPanel";
import { makeDraft, wordsText, type Draft, type PickTarget } from "./review";

const IMAGE_ZOOM_MIN = 1;
const IMAGE_ZOOM_MAX = 4;
const IMAGE_ZOOM_STEP = 0.25;
const REVIEWER_KEY = "kv-review-reviewer";

const IMAGE_TABS = [
  { id: "raw", label: "Original" },
  { id: "overall", label: "Overall" },
  { id: "dob", label: "DOB" },
  { id: "member_id", label: "Member ID" },
  { id: "name", label: "Member Name" },
  { id: "provider_name", label: "Provider" },
  { id: "electronic_signature", label: "E-Sign" },
  { id: "dos", label: "DOS" },
  { id: "page_no", label: "Page No" },
  { id: "heading_heron", label: "Headings" },
];

type DocStatus = "Pending" | "In progress" | "Reviewed";

function documentStatus(doc: DocumentRow): DocStatus {
  if (doc.reviewed_pages >= doc.page_count && doc.page_count > 0) return "Reviewed";
  return doc.pages.some((page) => page.reviewed_fields > 0) ? "In progress" : "Pending";
}

const STATUS_PILL: Record<DocStatus, string> = {
  Reviewed: "pill-complete",
  "In progress": "pill-processing",
  Pending: "pill-queued",
};

function fmtPct(value: number | null | undefined) {
  return value == null ? "—" : `${value.toFixed(1)}%`;
}

export default function BatchReview({ runId, onHome }: { runId: string; onHome: () => void }) {
  const [run, setRun] = useState<RunDetail | null>(null);
  const [meta, setMeta] = useState<Meta | null>(null);
  const [docId, setDocId] = useState("");
  const [pageIdx, setPageIdx] = useState(0);
  const [jobsPaneCollapsed, setJobsPaneCollapsed] = useState(false);
  const [imageTab, setImageTab] = useState("overall");
  const [imageZoom, setImageZoom] = useState(1);
  const [imageNaturalSize, setImageNaturalSize] = useState<{ w: number; h: number } | null>(null);
  const [fittedImageSize, setFittedImageSize] = useState<{ w: number; h: number } | null>(null);
  const [detail, setDetail] = useState<PageDetail | null>(null);
  const [ocr, setOcr] = useState<PageOcr | null>(null);
  const [pageLoading, setPageLoading] = useState(false);
  const [drafts, setDrafts] = useState<Record<string, Draft>>({});
  const [open, setOpen] = useState<Set<string>>(new Set());
  const [pick, setPick] = useState<PickTarget | null>(null);
  const [hoverCandidate, setHoverCandidate] = useState<Candidate | null>(null);
  const [hoverWord, setHoverWord] = useState<number | null>(null);
  const [reviewer, setReviewer] = useState(() => localStorage.getItem(REVIEWER_KEY) || "");
  const [saving, setSaving] = useState("");
  const [fieldErrors, setFieldErrors] = useState<Record<string, string>>({});
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  const viewerScrollRef = useRef<HTMLDivElement | null>(null);
  const activeThumbRef = useRef<HTMLButtonElement | null>(null);

  const doc = run?.documents.find((item) => item.record_id === docId) || null;
  const page = doc?.pages[pageIdx] || null;

  const loadRun = useCallback(
    async (keepSelection: boolean) => {
      try {
        const next = await getRun(runId);
        setRun(next);
        setError("");
        if (!keepSelection) {
          const first = next.documents.find((item) => documentStatus(item) !== "Reviewed") || next.documents[0];
          setDocId(first?.record_id || "");
          const firstOpen = first ? first.pages.findIndex((item) => item.reviewed_fields < next.field_count) : 0;
          setPageIdx(Math.max(0, firstOpen));
        }
      } catch (exc) {
        setError(exc instanceof Error ? exc.message : String(exc));
      }
    },
    [runId],
  );

  useEffect(() => {
    setLoading(true);
    void Promise.all([loadRun(false), getMeta().then(setMeta)]).finally(() => setLoading(false));
  }, [loadRun]);

  useEffect(() => {
    localStorage.setItem(REVIEWER_KEY, reviewer);
  }, [reviewer]);

  // Load the page's candidates, saved reviews and OCR words.
  const pageKey = page ? `${page.record_id}|${page.page_number}|${page.file_name}` : "";
  useEffect(() => {
    if (!page) {
      setDetail(null);
      setOcr(null);
      return;
    }
    let cancelled = false;
    setPageLoading(true);
    setPick(null);
    setHoverCandidate(null);
    setFieldErrors({});
    Promise.all([getPage(runId, page), getPageOcr(runId, page.record_id, page.file_name)])
      .then(([nextDetail, nextOcr]) => {
        if (cancelled) return;
        setDetail(nextDetail);
        setOcr(nextOcr);
        setDrafts(Object.fromEntries(nextDetail.fields.map((field) => [field.id, makeDraft(field)])));
        const firstOpen = nextDetail.fields.find((field) => !field.review);
        setOpen(new Set(firstOpen ? [firstOpen.id] : []));
      })
      .catch((exc) => !cancelled && setError(exc instanceof Error ? exc.message : String(exc)))
      .finally(() => !cancelled && setPageLoading(false));
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [runId, pageKey]);

  // OCR word lookup and reading order.
  const { wordById, wordOrder } = useMemo(() => {
    const byId = new Map<number, OcrWord>();
    const order = new Map<number, number>();
    let n = 0;
    for (const line of ocr?.lines || []) {
      for (const word of line) {
        byId.set(word.i, word);
        order.set(word.i, n++);
      }
    }
    return { wordById: byId, wordOrder: order };
  }, [ocr]);

  const pickedWords = useMemo(() => {
    if (!pick) return new Set<number>();
    const item = drafts[pick.field]?.added[pick.index];
    return new Set(item ? (pick.part === "value" ? item.value_words : item.key_words) : []);
  }, [pick, drafts]);

  const candidateWords = useMemo(
    () => new Set([...(hoverCandidate?.value_words || []), ...(hoverCandidate?.key_words || [])]),
    [hoverCandidate],
  );

  const toggleWord = useCallback(
    (index: number, additive: boolean) => {
      if (!pick) return;
      setDrafts((current) => {
        const draft = current[pick.field];
        const item = draft?.added[pick.index];
        if (!draft || !item) return current;
        const listName = pick.part === "value" ? "value_words" : "key_words";
        const picked = item[listName];
        let list: number[];
        if (additive) {
          list = picked.includes(index) ? picked.filter((i) => i !== index) : [...picked, index];
        } else {
          list = picked.length === 1 && picked[0] === index ? [] : [index];
        }
        const text = wordsText(list, wordOrder, wordById);
        const nextItem = { ...item, [listName]: list, [pick.part]: text };
        return {
          ...current,
          [pick.field]: {
            ...draft,
            dirty: true,
            added: draft.added.map((row, n) => (n === pick.index ? nextItem : row)),
          },
        };
      });
    },
    [pick, wordOrder, wordById],
  );

  const keyNeighbors = useCallback(
    (candidate: Candidate): KeyNeighbors => {
      if (!candidate.key_words.length) return { before: "", after: "" };
      // OCR order (word index), which is what the key finder compares, not reading order.
      const near = (from: number, step: number) => {
        for (let i = from, n = 0; i >= 0 && n < 8; i += step, n++) {
          const word = wordById.get(i);
          if (word) return word.t;
        }
        return "";
      };
      return {
        before: near(Math.min(...candidate.key_words) - 1, -1),
        after: near(Math.max(...candidate.key_words) + 1, 1),
      };
    },
    [wordById],
  );

  function selectDoc(next: DocumentRow) {
    setDocId(next.record_id);
    setPageIdx(0);
    resetImage();
  }

  function selectPage(index: number) {
    setPageIdx(index);
    resetImage();
  }

  function resetImage() {
    setImageZoom(1);
    setFittedImageSize(null);
    setImageNaturalSize(null);
  }

  function changeImageTab(next: string) {
    setImageTab(next);
    setFittedImageSize(null);
    setImageNaturalSize(null);
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
  }, [fitImage, pageKey, imageTab, jobsPaneCollapsed]);

  useEffect(() => {
    window.addEventListener("resize", fitImage);
    return () => window.removeEventListener("resize", fitImage);
  }, [fitImage]);

  useEffect(() => {
    activeThumbRef.current?.scrollIntoView({ block: "nearest", inline: "nearest" });
  }, [pageKey]);

  const visibleImageTabs = useMemo(() => {
    const present = new Set((detail?.fields || []).filter((field) => field.candidates.length).map((field) => field.id));
    return IMAGE_TABS.filter((tab) => tab.id === "raw" || tab.id === "overall" || present.has(tab.id));
  }, [detail]);

  useEffect(() => {
    if (!visibleImageTabs.some((tab) => tab.id === imageTab)) changeImageTab("overall");
  }, [visibleImageTabs, imageTab]);

  function patchReviewedFields(count: number) {
    setRun((current) => {
      if (!current || !page) return current;
      return {
        ...current,
        documents: current.documents.map((item) => {
          if (item.record_id !== page.record_id) return item;
          const pages = item.pages.map((row) =>
            row.page_number === page.page_number && row.file_name === page.file_name ? { ...row, reviewed_fields: count } : row,
          );
          return { ...item, pages, reviewed_pages: pages.filter((row) => row.reviewed_fields >= current.field_count).length };
        }),
      };
    });
  }

  async function onSave(fieldId: string) {
    if (!page || !detail) return;
    const draft = drafts[fieldId];
    setSaving(fieldId);
    setFieldErrors((current) => ({ ...current, [fieldId]: "" }));
    try {
      const result = await saveReview(runId, {
        record_id: page.record_id,
        page_number: page.page_number,
        file_name: page.file_name,
        field: fieldId,
        reviewer: reviewer.trim(),
        not_present: draft.not_present,
        candidates: draft.candidates,
        added: draft.added.filter((item) => item.value.trim()),
        notes: draft.notes,
      });
      const fields = detail.fields.map((field) =>
        field.id === fieldId ? { ...field, review: result.review, prior: null } : field,
      );
      setDetail({ ...detail, fields });
      setDrafts((current) => ({ ...current, [fieldId]: makeDraft(fields.find((field) => field.id === fieldId)!) }));
      if (pick?.field === fieldId) setPick(null);
      // Collapse the saved field and move on to the next one still to review.
      const next = fields.find((field) => field.id !== fieldId && (!field.review || drafts[field.id]?.dirty));
      setOpen(new Set(next ? [next.id] : []));
      patchReviewedFields(result.reviewed_fields);
      void loadRun(true);
    } catch (exc) {
      setFieldErrors((current) => ({ ...current, [fieldId]: exc instanceof Error ? exc.message : String(exc) }));
    } finally {
      setSaving("");
    }
  }

  async function onSavePageInfo(pageSequence: string) {
    if (!page || !detail) return;
    const result = await savePageInfo(runId, page, reviewer.trim(), pageSequence);
    setDetail((current) => (current ? { ...current, page_info: result.page_info, page_info_prior: null } : current));
  }

  async function onClear(fieldId: string) {
    if (!page || !detail) return;
    setSaving(fieldId);
    try {
      const result = await clearReview(runId, page, fieldId);
      const fields = detail.fields.map((field) => (field.id === fieldId ? { ...field, review: null } : field));
      setDetail({ ...detail, fields });
      setDrafts((current) => ({ ...current, [fieldId]: makeDraft(fields.find((field) => field.id === fieldId)!) }));
      patchReviewedFields(result.reviewed_fields);
      void loadRun(true);
    } catch (exc) {
      setFieldErrors((current) => ({ ...current, [fieldId]: exc instanceof Error ? exc.message : String(exc) }));
    } finally {
      setSaving("");
    }
  }

  function goNextPage() {
    if (!doc || !run) return;
    if (pageIdx < doc.pages.length - 1) {
      selectPage(pageIdx + 1);
      return;
    }
    const docIndex = run.documents.findIndex((item) => item.record_id === doc.record_id);
    const nextDoc = run.documents[docIndex + 1];
    if (nextDoc) selectDoc(nextDoc);
  }

  const hasNextPage =
    !!doc && !!run && (pageIdx < doc.pages.length - 1 || run.documents.findIndex((d) => d.record_id === doc.record_id) < run.documents.length - 1);

  // Boxes drawn over the page image (normalized 0..1 coordinates).
  const overlay = useMemo(() => {
    const rects: { box: Box; kind: string }[] = [];
    if (hoverCandidate?.key_box) rects.push({ box: hoverCandidate.key_box, kind: "key" });
    if (hoverCandidate?.value_box) rects.push({ box: hoverCandidate.value_box, kind: "value" });
    for (const i of pickedWords) {
      const word = wordById.get(i);
      if (word) rects.push({ box: word.b, kind: "picked" });
    }
    if (hoverWord != null && wordById.has(hoverWord)) rects.push({ box: wordById.get(hoverWord)!.b, kind: "hover" });
    return rects;
  }, [hoverCandidate, pickedWords, hoverWord, wordById]);

  function wordAt(event: MouseEvent<SVGSVGElement>): number | null {
    const rect = event.currentTarget.getBoundingClientRect();
    const x = (event.clientX - rect.left) / rect.width;
    const y = (event.clientY - rect.top) / rect.height;
    const slack = 0.003;
    for (const word of wordById.values()) {
      const [x0, y0, x1, y1] = word.b;
      if (x >= x0 - slack && x <= x1 + slack && y >= y0 - slack && y <= y1 + slack) return word.i;
    }
    return null;
  }

  const currentSrc = run && page ? imageUrl(run.id, page.record_id, page.file_name, imageTab) : "";
  const headingScores = run
    ? IMAGE_TABS.filter((tab) => run.headings?.[tab.id]?.pages).map((tab) => {
        const score = run.headings[tab.id];
        return `${tab.label} P ${fmtPct(score.precision)} R ${fmtPct(score.recall)} (${score.pages} pages)`;
      })
    : [];
  const accuracyLine = run
    ? [
        `Accuracy ${fmtPct(run.accuracy)}${
          run.accuracy_total
            ? ` (${fmtPairs(run.accuracy_correct, run.accuracy_wrong, run.accuracy_missed, "key-value pairs and headings")})`
            : ""
        }`,
        ...headingScores,
        `${run.reviewed_pages}/${run.total_pages} pages fully reviewed`,
        `model ${run.model_version}`,
        run.ner_model ? `NER ${run.ner_model}` : "",
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
              <strong>Batch Review · {runId}</strong>
              <p className="accuracy-line" title={accuracyLine}>
                {accuracyLine}
              </p>
            </div>
          </div>
        </div>
        <div className="top-actions">
          <button type="button" className="btn secondary keep-mobile" onClick={onHome}>
            <HomeIcon size={14} aria-hidden="true" /> Batches
          </button>
          <button
            type="button"
            className="icon-ghost-btn keep-mobile"
            title="Refresh"
            aria-label="Refresh"
            onClick={() => void loadRun(true)}
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
                  {jobsPaneCollapsed ? <ChevronRight size={16} aria-hidden="true" /> : <ChevronLeft size={16} aria-hidden="true" />}
                </button>
              </div>
            </div>
            {!jobsPaneCollapsed && (
              <ul className="job-list">
                {loading && <li className="empty">Loading…</li>}
                {!loading &&
                  run?.documents.map((item) => {
                    const status = documentStatus(item);
                    return (
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
                            <span className={`pill ${STATUS_PILL[status]}`} title={status}>
                              {status}
                            </span>
                          </div>
                          <div className="job-meta">
                            {item.reviewed_pages} / {item.page_count} pages reviewed
                          </div>
                        </button>
                      </li>
                    );
                  })}
                {!loading && !run?.documents.length && <li className="empty">No files in this batch</li>}
              </ul>
            )}
          </aside>
        </div>

        <section className="review-pane">
          {!doc && <div className="empty-center">{loading ? "Loading batch…" : "Select a file to review"}</div>}
          {doc && run && (
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
                    <span className="review-meta-primary" title={page?.file_name}>
                      {page?.file_name}
                    </span>
                  </div>
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
                              key={`${pageKey}-${imageTab}`}
                              src={currentSrc}
                              alt={`Page ${page.page_number} (${imageTab})`}
                              className="page-image"
                              draggable={false}
                              onLoad={(event) => {
                                const img = event.currentTarget;
                                setImageNaturalSize({ w: img.naturalWidth, h: img.naturalHeight });
                              }}
                            />
                            <svg
                              className={`page-bbox-overlay ${pick ? "picking" : ""}`}
                              viewBox="0 0 1 1"
                              preserveAspectRatio="none"
                              onClick={(event) => {
                                const index = pick ? wordAt(event) : null;
                                if (index != null) toggleWord(index, event.ctrlKey || event.metaKey);
                              }}
                              onMouseMove={(event) => pick && setHoverWord(wordAt(event))}
                              onMouseLeave={() => setHoverWord(null)}
                            >
                              {overlay.map((rect, n) => (
                                <rect
                                  key={n}
                                  className={`page-bbox-rect kv-${rect.kind}`}
                                  x={rect.box[0]}
                                  y={rect.box[1]}
                                  width={Math.max(rect.box[2] - rect.box[0], 0.002)}
                                  height={Math.max(rect.box[3] - rect.box[1], 0.002)}
                                />
                              ))}
                            </svg>
                          </div>
                        </div>
                      </>
                    )}
                  </div>

                  <div className="filmstrip">
                    {doc.pages.map((item, index) => (
                      <button
                        key={`${item.page_number}-${item.file_name}`}
                        ref={index === pageIdx ? activeThumbRef : undefined}
                        type="button"
                        className={`thumb ${index === pageIdx ? "active" : ""} ${
                          item.reviewed_fields >= run.field_count ? "kv-thumb-done" : ""
                        }`}
                        onClick={() => selectPage(index)}
                        title={`Page ${item.page_number} · ${item.reviewed_fields}/${run.field_count} fields reviewed`}
                      >
                        <img src={imageUrl(run.id, doc.record_id, item.file_name, "overall")} alt="" loading="lazy" />
                        <span>{item.page_number}</span>
                      </button>
                    ))}
                  </div>
                </div>

                <OcrPanel
                  ocr={ocr}
                  loading={pageLoading}
                  picking={!!pick}
                  picked={pickedWords}
                  highlighted={candidateWords}
                  onHoverWord={setHoverWord}
                  onClickWord={toggleWord}
                />

                <ReviewPanel
                  fields={detail?.fields || []}
                  drafts={drafts}
                  reasons={meta?.reasons || []}
                  headingReasons={meta?.heading_reasons || []}
                  levels={meta?.levels || []}
                  idTypes={meta?.id_types || []}
                  open={open}
                  pick={pick}
                  saving={saving}
                  errors={fieldErrors}
                  reviewer={reviewer}
                  loading={pageLoading}
                  hasNextPage={hasNextPage}
                  onReviewer={setReviewer}
                  onToggle={(field) =>
                    setOpen((current) => {
                      const next = new Set(current);
                      if (next.has(field)) next.delete(field);
                      else next.add(field);
                      return next;
                    })
                  }
                  onDraft={(field, change) => setDrafts((current) => ({ ...current, [field]: change(current[field]) }))}
                  onPick={setPick}
                  onHoverCandidate={setHoverCandidate}
                  keyNeighbors={keyNeighbors}
                  onSave={(field) => void onSave(field)}
                  onClear={(field) => void onClear(field)}
                  onNextPage={goNextPage}
                  pageKey={pageKey}
                  pageInfo={detail?.page_info ?? null}
                  pageInfoPrior={detail?.page_info_prior ?? null}
                  onSavePageInfo={onSavePageInfo}
                />
              </div>
            </>
          )}
        </section>
      </div>
    </div>
  );
}
