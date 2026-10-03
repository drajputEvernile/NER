import { useCallback, useEffect, useState, type ReactNode } from "react";
import { BarChart3, ClipboardCheck, FolderOpen, RefreshCw, X } from "lucide-react";
import {
  getVersionsAccuracy,
  listRuns,
  listVersions,
  rerun,
  type ModelVersion,
  type RunList,
  type RunSummary,
  type VersionsAccuracy,
} from "./api";
import { accuracyTier, fmtDateTime, fmtDuration, fmtHeadingDetail, fmtNumber, fmtPairs } from "./format";
import "./landing.css";

const POLL_MS = 5000;

function Accuracy({ value }: { value: number | null | undefined }) {
  if (value == null) return <span className="tag-dash">—</span>;
  return <span className={`accuracy-value accuracy-value-${accuracyTier(value)}`}>{value.toFixed(1)}%</span>;
}

/** "NN%" for the metrics bar, or a dash before anything is reviewed. */
function pct(value: number | null | undefined): string {
  return value == null ? "—" : `${value.toFixed(0)}%`;
}

const STATUS_PILL: Record<string, string> = {
  completed: "pill-complete",
  running: "pill-processing",
  stopped: "pill-stuck",
  error: "pill-failed",
  abandoned: "pill-stuck",
};

function RunStatusPill({ run }: { run: RunSummary }) {
  if (run.status === "completed") return null;
  const label =
    run.status === "running" ? `Running ${run.completed_documents}/${run.total_documents}` : run.status;
  return <span className={`pill ${STATUS_PILL[run.status] || "pill-queued"}`}>{label}</span>;
}

export default function Home({ onReview }: { onReview: (runId: string) => void }) {
  const [data, setData] = useState<RunList | null>(null);
  const [error, setError] = useState("");
  const [rerunFor, setRerunFor] = useState<RunSummary | null>(null);
  const [detailFor, setDetailFor] = useState<RunSummary | null>(null);

  const refresh = useCallback(async () => {
    try {
      setData(await listRuns());
      setError("");
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : String(exc));
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  useEffect(() => {
    if (!data?.active_run) return;
    const timer = window.setInterval(() => void refresh(), POLL_MS);
    return () => window.clearInterval(timer);
  }, [data?.active_run, refresh]);

  const totals = data?.totals;
  const latest = totals?.latest;
  const runs = data?.runs || [];

  return (
    <div className="shell shell-landing">
      <header className="topbar">
        <div className="topbar-start">
          <div className="brand">
            <div>
              <strong>Extraction</strong>
              <p>Runs, manual review and extraction model versions</p>
            </div>
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
        </div>
      </header>

      {error ? (
        <div className="banner mr-error" style={{ margin: 0, borderRadius: 0 }}>
          {error}
        </div>
      ) : null}

      <main className="landing">
        <div className="landing-home">
          <div className="landing-stats" role="group" aria-label="Run summary">
            <Stat value={fmtNumber(totals?.batches)} label="total batches" />
            <Divider />
            <Stat value={fmtNumber(totals?.runs)} label="total runs" />
            <Divider />
            <Stat value={fmtNumber(totals?.documents)} label="total documents" />
            <Divider />
            <Stat value={fmtNumber(totals?.pages)} label="total pages" />
            <Divider />
            <Stat value={fmtNumber(totals?.avg_pages_per_document, 1)} label="avg pages / document" />
            <Divider />
            <Stat value={fmtDuration(totals?.avg_time_per_page)} label="avg time / page" />
            <Divider />
            <div
              className="landing-stat"
              title={latest ? `${latest.run} · model ${latest.model_version}` : "Nothing is reviewed yet"}
            >
              <span className="landing-stat-value">
                {latest ? `KV: ${pct(latest.kv_accuracy)}, H: ${pct(latest.heading_accuracy)}` : "—"}
              </span>
              <span className="landing-stat-label">
                latest model accuracy{latest ? ` (based on ${fmtNumber(latest.documents)} documents)` : ""}
              </span>
            </div>
          </div>

          <section className="landing-history" aria-label="Runs">
            <div className="landing-history-header">
              <h2>Runs</h2>
              <span className="landing-history-count">{runs.length}</span>
              {data?.active_run ? (
                <span className="landing-history-note">A run is in progress · refreshing every 5s</span>
              ) : null}
            </div>
            <div className="landing-history-scroll">
              <table className="landing-history-table">
                <thead>
                  <tr>
                    <th>Run Name</th>
                    <th className="num">Total Documents</th>
                    <th className="num">Total Pages</th>
                    <th className="num">Reviewed Pages</th>
                    <th>Current KV_Accuracy</th>
                    <th>Current H_Accuracy</th>
                    <th>Model</th>
                    <th>Start Time</th>
                    <th>Total Time</th>
                    <th>Actions</th>
                  </tr>
                </thead>
                <tbody>
                  {!runs.length && (
                    <tr>
                      <td colSpan={10} className="landing-history-empty">
                        <div className="landing-empty-state">
                          <span className="landing-empty-icon">
                            <FolderOpen size={20} aria-hidden="true" />
                          </span>
                          <h3>No runs yet</h3>
                          <p>Each run of Extraction/run.py creates a KV_Run_* folder with a KV_Extraction folder of CSV tables under the output path in config.py.</p>
                        </div>
                      </td>
                    </tr>
                  )}
                  {runs.map((run) => (
                    <tr key={run.id}>
                      <td>
                        <div className="kv-run-name">
                          <span className="landing-col-name" title={run.id}>
                            {run.id}
                          </span>
                          <RunStatusPill run={run} />
                        </div>
                        {run.source_run ? <div className="kv-run-sub">rerun of {run.source_run}</div> : null}
                      </td>
                      <td className="num accuracy-value">{fmtNumber(run.total_documents)}</td>
                      <td className="num accuracy-value">{fmtNumber(run.total_pages)}</td>
                      <td className="num accuracy-value">
                        {run.reviewed_pages} / {run.total_pages}
                      </td>
                      <td
                        title={
                          run.kv.right + run.kv.wrong + run.kv.missed
                            ? fmtPairs(run.kv.right, run.kv.wrong, run.kv.missed)
                            : "Not reviewed yet"
                        }
                      >
                        <Accuracy value={run.kv.accuracy} />
                      </td>
                      <td
                        title={
                          run.heading.right + run.heading.wrong + run.heading.missed
                            ? `${fmtPairs(run.heading.right, run.heading.wrong, run.heading.missed)} · P ${
                                run.heading.precision ?? "—"
                              }% R ${run.heading.recall ?? "—"}%`
                            : "Not reviewed yet"
                        }
                      >
                        <Accuracy value={run.heading.accuracy} />
                      </td>
                      <td className="landing-col-uploaded">{run.model_version}</td>
                      <td className="landing-col-uploaded">{fmtDateTime(run.start_time)}</td>
                      <td className="landing-col-uploaded">{fmtDuration(run.total_time_seconds)}</td>
                      <td>
                        <div className="landing-row-actions">
                          <button
                            type="button"
                            className="landing-icon-btn success"
                            title="Review this run"
                            aria-label={`Review ${run.id}`}
                            disabled={run.status === "running"}
                            onClick={() => onReview(run.id)}
                          >
                            <ClipboardCheck size={15} aria-hidden="true" />
                          </button>
                          <button
                            type="button"
                            className="landing-icon-btn info"
                            title={data?.active_run ? "A run is already in progress" : "Rerun these documents"}
                            aria-label={`Rerun ${run.id}`}
                            disabled={!!data?.active_run}
                            onClick={() => setRerunFor(run)}
                          >
                            <RefreshCw size={15} aria-hidden="true" />
                          </button>
                          <button
                            type="button"
                            className="landing-icon-btn"
                            title="Accuracy by extraction model version"
                            aria-label={`Detailed view of ${run.id}`}
                            onClick={() => setDetailFor(run)}
                          >
                            <BarChart3 size={15} aria-hidden="true" />
                          </button>
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>
        </div>
      </main>

      {rerunFor && (
        <RerunModal
          run={rerunFor}
          onClose={() => setRerunFor(null)}
          onStarted={() => {
            setRerunFor(null);
            void refresh();
            // The new run folder appears once the model has loaded.
            window.setTimeout(() => void refresh(), 4000);
          }}
        />
      )}
      {detailFor && <DetailModal run={detailFor} onClose={() => setDetailFor(null)} />}
    </div>
  );
}

function Stat({ value, label }: { value: string; label: string }) {
  return (
    <div className="landing-stat">
      <span className="landing-stat-value">{value}</span>
      <span className="landing-stat-label">{label}</span>
    </div>
  );
}

function Divider() {
  return <span className="landing-stat-divider" aria-hidden="true" />;
}

function Modal({
  title,
  wide,
  onClose,
  children,
}: {
  title: string;
  wide?: boolean;
  onClose: () => void;
  children: ReactNode;
}) {
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => event.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);
  return (
    <div className="landing-modal-backdrop" onMouseDown={(event) => event.target === event.currentTarget && onClose()}>
      <div className={`landing-modal ${wide ? "landing-modal-wide" : ""}`} role="dialog" aria-modal="true" aria-label={title}>
        <div className="landing-modal-header">
          <h3>{title}</h3>
          <button type="button" className="landing-modal-close" aria-label="Close" onClick={onClose}>
            <X size={16} aria-hidden="true" />
          </button>
        </div>
        <div className="landing-modal-body">{children}</div>
      </div>
    </div>
  );
}

function RerunModal({ run, onClose, onStarted }: { run: RunSummary; onClose: () => void; onStarted: () => void }) {
  const [versions, setVersions] = useState<ModelVersion[] | null>(null);
  const [choice, setChoice] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    listVersions()
      .then((rows) => {
        setVersions(rows);
        const runnable = rows.filter((row) => row.runnable);
        setChoice(runnable[runnable.length - 1]?.id || "");
      })
      .catch((exc) => setError(exc instanceof Error ? exc.message : String(exc)));
  }, []);

  async function start() {
    setBusy(true);
    setError("");
    try {
      await rerun(run.id, choice);
      onStarted();
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : String(exc));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal title={`Rerun ${run.id}`} onClose={onClose}>
      <p className="landing-modal-lead">
        Runs extraction again on the same {run.total_documents} documents ({run.total_pages} pages) into a new run.
        Existing reviews of these pages score the new run automatically.
      </p>
      <div className="kv-version-list" role="radiogroup" aria-label="Extraction model version">
        {!versions && !error && <div className="empty">Loading versions…</div>}
        {versions?.map((version) => (
          <label
            key={version.id}
            className={`kv-version-option ${choice === version.id ? "active" : ""} ${version.runnable ? "" : "disabled"}`}
          >
            <input
              type="radio"
              name="model-version"
              value={version.id}
              checked={choice === version.id}
              disabled={!version.runnable}
              onChange={() => setChoice(version.id)}
            />
            <span className="kv-version-text">
              <strong>{version.label}</strong>
              <span>{version.description}</span>
              {version.trained_at ? <span>Trained {fmtDateTime(version.trained_at)}</span> : null}
              {!version.runnable ? <span className="kv-version-note">Not runnable yet</span> : null}
            </span>
          </label>
        ))}
      </div>
      {error && <div className="banner mr-error kv-pre">{error}</div>}
      <div className="landing-modal-actions">
        <button type="button" className="btn secondary" onClick={onClose}>
          Cancel
        </button>
        <button type="button" className="btn primary" disabled={!choice || busy} onClick={() => void start()}>
          {busy ? "Starting…" : "Start rerun"}
        </button>
      </div>
    </Modal>
  );
}

function DetailModal({ run, onClose }: { run: RunSummary; onClose: () => void }) {
  const [data, setData] = useState<VersionsAccuracy | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    getVersionsAccuracy(run.id)
      .then(setData)
      .catch((exc) => setError(exc instanceof Error ? exc.message : String(exc)));
  }, [run.id]);

  const versions = data?.versions || [];
  const judged = versions.some((version) => version.latest.total > 0);

  const cell = (
    key: string,
    score: { accuracy: number | null; correct: number; wrong: number; missed: number; total: number } | undefined,
    title?: string,
  ) => (
    <td key={key} className="num" title={title ?? (score ? fmtPairs(score.correct, score.wrong, score.missed) : "")}>
      <Accuracy value={score?.accuracy} />
      {score?.total ? (
        <span className="kv-count">
          {" "}
          ({score.correct}/{score.total})
        </span>
      ) : null}
    </td>
  );

  return (
    <Modal title={`Accuracy by extraction model version · ${run.id}`} wide onClose={onClose}>
      <p className="landing-modal-lead">
        Every run over these {data?.documents ?? run.total_documents} documents, scored on the same labels: the latest
        manual review of each page, from any run. Accuracy counts key-value pairs: an extracted pair is right when both
        its key and value are correct, wrong when either is wrong, and a reviewed pair the run did not extract is
        missed. Headings count the same way: a detected heading is right when its text and level are correct, wrong
        when it is no heading or has the wrong level, and a reviewed heading not detected is missed. Accuracy = right /
        (right + wrong + missed). KV_Extraction and Heading_Detector are two separate models; All modules adds them up.
      </p>
      {error && <div className="banner mr-error">{error}</div>}
      {!data && !error && <div className="empty">Loading…</div>}
      {data && !judged && (
        <div className="landing-empty-state">
          <h3>No reviews yet</h3>
          <p>Review pages of this run; accuracy per version shows here as soon as the first field is saved.</p>
        </div>
      )}
      {data && judged && (
        <>
          <div className="kv-table-wrap">
            <table className="landing-history-table">
              <thead>
                <tr>
                  <th>Field</th>
                  {versions.map((version) => (
                    <th key={version.model_version} className="num" title={version.latest.id}>
                      {version.label}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {data.fields.map((field) => (
                  <tr key={field.id}>
                    <td>{field.label}</td>
                    {versions.map((version) => cell(version.model_version, version.latest.fields[field.id]))}
                  </tr>
                ))}
                <tr className="kv-subtotal-row">
                  <td>KV_Extraction</td>
                  {versions.map((version) => cell(version.model_version, version.latest.kv))}
                </tr>
                {data.heading_fields.map((field) => (
                  <tr key={field.id}>
                    <td>{field.label}</td>
                    {versions.map((version) => {
                      const detail = version.latest.headings[field.id];
                      const score = version.latest.fields[field.id];
                      return cell(
                        version.model_version,
                        score,
                        score && detail
                          ? `${fmtPairs(score.correct, score.wrong, score.missed)} · ${fmtHeadingDetail(detail)}`
                          : undefined,
                      );
                    })}
                  </tr>
                ))}
                <tr className="kv-subtotal-row">
                  <td>Heading_Detector</td>
                  {versions.map((version) =>
                    cell(
                      version.model_version,
                      version.latest.heading,
                      `${fmtPairs(version.latest.heading.correct, version.latest.heading.wrong, version.latest.heading.missed)} · precision ${
                        version.latest.heading.precision ?? "—"
                      }% · recall ${version.latest.heading.recall ?? "—"}%`,
                    ),
                  )}
                </tr>
                <tr className="kv-total-row">
                  <td>All modules</td>
                  {versions.map((version) => cell(version.model_version, version.latest))}
                </tr>
              </tbody>
            </table>
          </div>
          <h4 className="kv-subhead">Runs</h4>
          <div className="kv-table-wrap">
            <table className="landing-history-table">
              <thead>
                <tr>
                  <th>Run</th>
                  <th>Version</th>
                  <th>Start Time</th>
                  <th className="num" title="Key-value pairs and headings: right + wrong + missed">
                    Pairs
                  </th>
                  <th>KV_Extraction</th>
                  <th>Heading_Detector</th>
                  <th>All modules</th>
                </tr>
              </thead>
              <tbody>
                {versions.flatMap((version) =>
                  version.runs.map((item) => (
                    <tr key={item.id}>
                      <td className="landing-col-name">{item.id}</td>
                      <td className="landing-col-uploaded">{version.model_version}</td>
                      <td className="landing-col-uploaded">{fmtDateTime(item.start_time)}</td>
                      <td className="num accuracy-value">{item.total}</td>
                      <td>
                        <Accuracy value={item.kv.accuracy} />
                      </td>
                      <td>
                        <Accuracy value={item.heading.accuracy} />
                      </td>
                      <td>
                        <Accuracy value={item.accuracy} />
                      </td>
                    </tr>
                  )),
                )}
              </tbody>
            </table>
          </div>
        </>
      )}
    </Modal>
  );
}
