import { useEffect, useMemo, useState } from "react";
import { Check, X } from "lucide-react";
import {
  getPageOcrText,
  setAccuracy,
  setMissedKeys,
  type Hit,
  type MissedKey,
  type PageRow,
} from "./api";

export type FieldKey = "dob" | "ID" | "MName" | "PName" | "ESig";

type TabKey = "ocr_data" | FieldKey | "missed_keys";

const FIELD_TABS: { key: FieldKey; label: string }[] = [
  { key: "dob", label: "DOB" },
  { key: "ID", label: "Member ID" },
  { key: "MName", label: "Member Name" },
  { key: "PName", label: "Provider Name" },
  { key: "ESig", label: "E Signature" },
];

const EXTRACTOR_OPTIONS: { value: FieldKey; label: string }[] = [
  { value: "dob", label: "DOB" },
  { value: "ID", label: "Member ID" },
  { value: "MName", label: "Member Name" },
  { value: "PName", label: "Provider Name" },
  { value: "ESig", label: "E Signature" },
];

const TAB_BLURB: Record<TabKey, string> = {
  ocr_data: "Reference view of the OCR text extracted for this page, in reading order.",
  dob: "Confirm whether every date of birth extraction on this page is correct.",
  ID: "Confirm whether every member ID extraction on this page is correct.",
  MName: "Confirm whether every member name extraction on this page is correct.",
  PName: "Confirm whether every provider name extraction on this page is correct.",
  ESig: "Confirm whether every electronic signature extraction on this page is correct.",
  missed_keys: "Record key/value pairs that should have been extracted on this page but were missed.",
};

type Props = {
  runId: string;
  recordId: string;
  page: PageRow | null;
  onHitAccuracy: (
    hitId: string,
    keyAccuracy: string,
    valueAccuracy: string,
    reason?: string,
    actualKey?: string,
    actualValue?: string,
  ) => void;
  onMissedKeysSaved: (keys: MissedKey[]) => void;
};

function fieldDone(hits: Hit[]): boolean {
  return hits.length > 0 && hits.every((hit) => !!hit.key_accuracy && !!hit.value_accuracy);
}

export default function ManualReviewPanel({
  runId,
  recordId,
  page,
  onHitAccuracy,
  onMissedKeysSaved,
}: Props) {
  const [activeTab, setActiveTab] = useState<TabKey>("ocr_data");

  useEffect(() => {
    setActiveTab("ocr_data");
  }, [page?.page_number, page?.file_name]);

  const hitsByField = useMemo(() => {
    const groups: Record<FieldKey, Hit[]> = {
      dob: [],
      ID: [],
      MName: [],
      PName: [],
      ESig: [],
    };
    for (const hit of page?.hits || []) {
      if (hit.field in groups) {
        groups[hit.field as FieldKey].push(hit);
      }
    }
    return groups;
  }, [page?.hits]);

  const visibleTabs = useMemo(() => {
    const fieldTabs = FIELD_TABS.filter((tab) => hitsByField[tab.key].length > 0);
    return [
      { key: "ocr_data" as const, label: "OCR Data" },
      ...fieldTabs,
      { key: "missed_keys" as const, label: "Missed Keys" },
    ];
  }, [hitsByField]);

  useEffect(() => {
    if (!visibleTabs.some((tab) => tab.key === activeTab)) {
      setActiveTab("ocr_data");
    }
  }, [visibleTabs, activeTab]);

  if (!page) {
    return (
      <div className="table-wrap mr-panel">
        <div className="empty">Select a page to begin manual review</div>
      </div>
    );
  }

  const activeLabel = visibleTabs.find((tab) => tab.key === activeTab)?.label || "";

  return (
    <div className="table-wrap mr-panel">
      <div className="table-toolbar">
        <div className="table-toolbar-start">
          <h3 className="output-panel-title">Manual Review</h3>
          <div className="output-tabs" role="tablist" aria-label="Manual review tabs">
            {visibleTabs.map((tab) => (
              <button
                key={tab.key}
                type="button"
                role="tab"
                aria-selected={activeTab === tab.key}
                className={`output-tab ${activeTab === tab.key ? "active" : ""}`}
                onClick={() => setActiveTab(tab.key)}
              >
                {tab.label}
                {tab.key !== "ocr_data" &&
                  tab.key !== "missed_keys" &&
                  fieldDone(hitsByField[tab.key]) && (
                    <span className="mr-tab-dot" aria-hidden="true" title="Reviewed" />
                  )}
                {tab.key === "missed_keys" && (page.missed_keys?.length || 0) > 0 && (
                  <span className="mr-tab-dot" aria-hidden="true" title="Saved" />
                )}
              </button>
            ))}
          </div>
        </div>
      </div>

      <div className="mr-box-header">
        <h4>
          {activeLabel} — Page {page.page_number}
        </h4>
        <p>{TAB_BLURB[activeTab]}</p>
      </div>

      <div className="table-panel mr-body">
        {activeTab === "ocr_data" ? (
          <OcrDataTab runId={runId} recordId={recordId} page={page} />
        ) : activeTab === "missed_keys" ? (
          <MissedKeysTab
            runId={runId}
            recordId={recordId}
            page={page}
            onSaved={onMissedKeysSaved}
          />
        ) : (
          <FieldReviewTab
            runId={runId}
            field={activeTab}
            hits={hitsByField[activeTab]}
            onHitAccuracy={onHitAccuracy}
          />
        )}
      </div>
    </div>
  );
}

function OcrDataTab({
  runId,
  recordId,
  page,
}: {
  runId: string;
  recordId: string;
  page: PageRow;
}) {
  const [text, setText] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setText(null);
    getPageOcrText(runId, recordId, page.file_name)
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
  }, [runId, recordId, page.file_name]);

  return (
    <div className="mr-tab-content">
      {loading ? <div className="empty">Loading OCR output…</div> : <pre className="mr-ocr-text">{text}</pre>}
    </div>
  );
}

function TickCross({
  value,
  onChange,
  disabled,
  labelFor,
}: {
  value: boolean | null | undefined;
  onChange: (value: boolean) => void;
  disabled?: boolean;
  labelFor: string;
}) {
  return (
    <div className="mr-tickcross-buttons">
      <button
        type="button"
        className={`mr-tick-btn ${value === true ? "active" : ""}`}
        disabled={disabled}
        onClick={() => onChange(true)}
        aria-label={`Mark ${labelFor} correct`}
        title="Correct"
      >
        <Check size={14} aria-hidden="true" />
      </button>
      <button
        type="button"
        className={`mr-cross-btn ${value === false ? "active" : ""}`}
        disabled={disabled}
        onClick={() => onChange(false)}
        aria-label={`Mark ${labelFor} incorrect`}
        title="Incorrect"
      >
        <X size={14} aria-hidden="true" />
      </button>
    </div>
  );
}

function FieldReviewTab({
  runId,
  hits,
  onHitAccuracy,
}: {
  runId: string;
  field: FieldKey;
  hits: Hit[];
  onHitAccuracy: (
    hitId: string,
    keyAccuracy: string,
    valueAccuracy: string,
    reason?: string,
    actualKey?: string,
    actualValue?: string,
  ) => void;
}) {
  const [keyTicks, setKeyTicks] = useState<Record<string, boolean>>({});
  const [valueTicks, setValueTicks] = useState<Record<string, boolean>>({});
  const [reasons, setReasons] = useState<Record<string, string>>({});
  const [actualKeys, setActualKeys] = useState<Record<string, string>>({});
  const [actuals, setActuals] = useState<Record<string, string>>({});
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [editing, setEditing] = useState(false);

  useEffect(() => {
    const nextKeys: Record<string, boolean> = {};
    const nextValues: Record<string, boolean> = {};
    const nextReasons: Record<string, string> = {};
    const nextActualKeys: Record<string, string> = {};
    const nextActuals: Record<string, string> = {};
    for (const hit of hits) {
      if (hit.key_accuracy === "correct") nextKeys[hit.id] = true;
      else if (hit.key_accuracy === "incorrect") nextKeys[hit.id] = false;
      if (hit.value_accuracy === "correct") nextValues[hit.id] = true;
      else if (hit.value_accuracy === "incorrect") nextValues[hit.id] = false;
      nextReasons[hit.id] = hit.reason || "";
      nextActualKeys[hit.id] = hit.actual_key || "";
      nextActuals[hit.id] = hit.actual_value || "";
    }
    setKeyTicks(nextKeys);
    setValueTicks(nextValues);
    setReasons(nextReasons);
    setActualKeys(nextActualKeys);
    setActuals(nextActuals);
    setError(null);
    setEditing(false);
  }, [hits]);

  const locked = fieldDone(hits) && !editing;
  const allJudged = hits.every(
    (hit) => keyTicks[hit.id] !== undefined && valueTicks[hit.id] !== undefined,
  );
  const incorrectReady = hits.every((hit) => {
    const keyBad = keyTicks[hit.id] === false;
    const valueBad = valueTicks[hit.id] === false || keyBad;
    if (!keyBad && !valueBad) return true;
    if (!(reasons[hit.id] || "").trim()) return false;
    if (keyBad && !(actualKeys[hit.id] || "").trim()) return false;
    if (valueBad && !(actuals[hit.id] || "").trim()) return false;
    return true;
  });
  const canSave = allJudged && incorrectReady;

  function setKeyJudgment(hitId: string, correct: boolean) {
    setKeyTicks((prev) => ({ ...prev, [hitId]: correct }));
    if (!correct) {
      setValueTicks((prev) => ({ ...prev, [hitId]: false }));
    }
  }

  function setValueJudgment(hitId: string, correct: boolean) {
    if (keyTicks[hitId] === false) return;
    setValueTicks((prev) => ({ ...prev, [hitId]: correct }));
  }

  async function save() {
    setSaving(true);
    setError(null);
    try {
      for (const hit of hits) {
        const keyCorrect = keyTicks[hit.id] === true;
        const valueCorrect = keyCorrect && valueTicks[hit.id] === true;
        const keyAccuracy = keyCorrect ? "correct" : "incorrect";
        const valueAccuracy = valueCorrect ? "correct" : "incorrect";
        const reason =
          keyAccuracy === "incorrect" || valueAccuracy === "incorrect"
            ? (reasons[hit.id] || "").trim()
            : "";
        const actualKey = keyAccuracy === "incorrect" ? (actualKeys[hit.id] || "").trim() : "";
        const actualValue = valueAccuracy === "incorrect" ? (actuals[hit.id] || "").trim() : "";
        const unchanged =
          hit.key_accuracy === keyAccuracy &&
          hit.value_accuracy === valueAccuracy &&
          (hit.reason || "") === reason &&
          (hit.actual_key || "") === actualKey &&
          (hit.actual_value || "") === actualValue;
        if (unchanged) continue;
        await setAccuracy(runId, hit.id, {
          key_accuracy: keyAccuracy,
          value_accuracy: valueAccuracy,
          reason,
          actual_key: actualKey,
          actual_value: actualValue,
        });
        onHitAccuracy(hit.id, keyAccuracy, valueAccuracy, reason, actualKey, actualValue);
      }
      setEditing(false);
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : String(exc));
    } finally {
      setSaving(false);
    }
  }

  if (locked) {
    return (
      <div className="mr-tab-content">
        <HitList
          hits={hits}
          keyTicks={keyTicks}
          valueTicks={valueTicks}
          reasons={reasons}
          actualKeys={actualKeys}
          actuals={actuals}
          disabled
        />
        <div className="mr-reviewed-banner">
          <div>
            <strong>Reviewed.</strong> Saved to <code>manual_review.xlsx</code> in this run folder.
          </div>
          <button type="button" className="mr-modify-btn" onClick={() => setEditing(true)}>
            Modify
          </button>
        </div>
        <div className="mr-recap">
          <p>
            Key correct:{" "}
            {hits.filter((hit) => hit.key_accuracy === "correct").map(hitLabel).join(", ") || "none"}
          </p>
          <p>
            Value correct:{" "}
            {hits.filter((hit) => hit.value_accuracy === "correct").map(hitLabel).join(", ") || "none"}
          </p>
        </div>
      </div>
    );
  }

  return (
    <div className="mr-tab-content">
      <p className="mr-statement">Mark Key and Value separately. If Key is incorrect, Value is auto-incorrect.</p>
      <HitList
        hits={hits}
        keyTicks={keyTicks}
        valueTicks={valueTicks}
        reasons={reasons}
        actualKeys={actualKeys}
        actuals={actuals}
        disabled={saving}
        onKeyChange={setKeyJudgment}
        onValueChange={setValueJudgment}
        onReasonChange={(hitId, value) => setReasons((prev) => ({ ...prev, [hitId]: value }))}
        onActualKeyChange={(hitId, value) => setActualKeys((prev) => ({ ...prev, [hitId]: value }))}
        onActualChange={(hitId, value) => setActuals((prev) => ({ ...prev, [hitId]: value }))}
      />
      {!allJudged && <p className="mr-note">Mark Key and Value for every row before saving.</p>}
      {allJudged && !incorrectReady && (
        <p className="mr-note">
          Fill Reason, and Actual Correct Key / Actual Correct Value for any Incorrect marks.
        </p>
      )}
      <div className="mr-disclaimer">
        Key incorrect forces Value incorrect. Incorrect key needs Actual Correct Key; incorrect value
        needs Actual Correct Value. Reviews go to <code>manual_review.xlsx</code>, not extraction.xlsx.
      </div>
      {error && <div className="banner mr-error">{error}</div>}
      <div className="mr-actions">
        <button type="button" className="btn primary" disabled={!canSave || saving} onClick={() => void save()}>
          {saving ? "Saving…" : "Save"}
        </button>
      </div>
    </div>
  );
}

function hitLabel(hit: Hit): string {
  if (hit.field === "ESig") {
    const parts = [hit.value, hit.signature_date].filter(Boolean);
    return parts.length ? parts.join(" · ") : hit.key;
  }
  return hit.value || hit.key;
}

function HitList({
  hits,
  keyTicks,
  valueTicks,
  reasons,
  actualKeys,
  actuals,
  disabled,
  onKeyChange,
  onValueChange,
  onReasonChange,
  onActualKeyChange,
  onActualChange,
}: {
  hits: Hit[];
  keyTicks: Record<string, boolean>;
  valueTicks: Record<string, boolean>;
  reasons: Record<string, string>;
  actualKeys: Record<string, string>;
  actuals: Record<string, string>;
  disabled?: boolean;
  onKeyChange?: (hitId: string, value: boolean) => void;
  onValueChange?: (hitId: string, value: boolean) => void;
  onReasonChange?: (hitId: string, value: string) => void;
  onActualKeyChange?: (hitId: string, value: string) => void;
  onActualChange?: (hitId: string, value: string) => void;
}) {
  return (
    <div className="mr-heading-review-list">
      {hits.map((hit) => {
        const keyBad = keyTicks[hit.id] === false;
        const valueBad = valueTicks[hit.id] === false || keyBad;
        const effectiveValue = keyBad ? false : valueTicks[hit.id];
        return (
          <div key={hit.id} className="mr-hit-block">
            <div className="mr-heading-review-text" title={hit.sentence || hit.key}>
              <div style={{ fontWeight: 600 }}>{hitLabel(hit)}</div>
              <div style={{ color: "var(--muted)", fontSize: "0.78rem", marginTop: 2 }}>
                {hit.key}
                {hit.region ? ` · ${hit.region}` : ""}
                {hit.score ? ` · ${hit.score}` : ""}
                {hit.selected === "yes" ? " · selected" : ""}
                {hit.source ? ` · ${hit.source}` : ""}
              </div>
            </div>
            <div className="mr-kv-judgments">
              <div className="mr-kv-row">
                <span className="mr-kv-label">Key</span>
                <TickCross
                  value={keyTicks[hit.id]}
                  onChange={(value) => onKeyChange?.(hit.id, value)}
                  disabled={disabled || !onKeyChange}
                  labelFor={`key ${hitLabel(hit)}`}
                />
              </div>
              <div className="mr-kv-row">
                <span className="mr-kv-label">Value</span>
                <TickCross
                  value={effectiveValue}
                  onChange={(value) => onValueChange?.(hit.id, value)}
                  disabled={disabled || !onValueChange || keyBad}
                  labelFor={`value ${hitLabel(hit)}`}
                />
              </div>
            </div>
            {valueBad && (
              <div className="mr-options-block mr-incorrect-fields">
                <div className="mr-text-input">
                  <label htmlFor={`reason-${hit.id}`}>Reason For Incorrect</label>
                  <input
                    id={`reason-${hit.id}`}
                    type="text"
                    value={reasons[hit.id] || ""}
                    disabled={disabled || !onReasonChange}
                    onChange={(e) => onReasonChange?.(hit.id, e.target.value)}
                    placeholder="Why is this wrong?"
                  />
                </div>
                {keyBad && (
                  <div className="mr-text-input">
                    <label htmlFor={`actual-key-${hit.id}`}>Actual Correct Key</label>
                    <input
                      id={`actual-key-${hit.id}`}
                      type="text"
                      value={actualKeys[hit.id] || ""}
                      disabled={disabled || !onActualKeyChange}
                      onChange={(e) => onActualKeyChange?.(hit.id, e.target.value)}
                      placeholder="What should the key be?"
                    />
                  </div>
                )}
                <div className="mr-text-input">
                  <label htmlFor={`actual-${hit.id}`}>Actual Correct Value</label>
                  <input
                    id={`actual-${hit.id}`}
                    type="text"
                    value={actuals[hit.id] || ""}
                    disabled={disabled || !onActualChange}
                    onChange={(e) => onActualChange?.(hit.id, e.target.value)}
                    placeholder="What should the value be?"
                  />
                </div>
              </div>
            )}
            {disabled && (hit.key_accuracy === "incorrect" || hit.value_accuracy === "incorrect") && (
              <div className="mr-recap" style={{ marginTop: "0.35rem" }}>
                <p>
                  Key: {hit.key_accuracy || "—"} · Value: {hit.value_accuracy || "—"}
                </p>
                <p>Reason: {hit.reason || "—"}</p>
                {hit.key_accuracy === "incorrect" && <p>Actual key: {hit.actual_key || "—"}</p>}
                {hit.value_accuracy === "incorrect" && <p>Actual value: {hit.actual_value || "—"}</p>}
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}

type MissedRow = { field: FieldKey; key: string; value: string };

function MissedKeysTab({
  runId,
  recordId,
  page,
  onSaved,
}: {
  runId: string;
  recordId: string;
  page: PageRow;
  onSaved: (keys: MissedKey[]) => void;
}) {
  const [rows, setRows] = useState<MissedRow[]>([{ field: "dob", key: "", value: "" }]);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    const existing = page.missed_keys || [];
    if (existing.length) {
      setRows(
        existing.map((item) => ({
          field: (EXTRACTOR_OPTIONS.some((opt) => opt.value === item.field)
            ? item.field
            : "dob") as FieldKey,
          key: item.key,
          value: item.value || "",
        })),
      );
      setSaved(true);
    } else {
      setRows([{ field: "dob", key: "", value: "" }]);
      setSaved(false);
    }
    setError(null);
  }, [page.page_number, page.file_name, page.missed_keys]);

  const filled = rows
    .map((row) => ({ field: row.field, key: row.key.trim(), value: row.value.trim() }))
    .filter((row) => row.key && row.value);
  const canSave = filled.length > 0;

  async function save() {
    if (!canSave) return;
    setSaving(true);
    setError(null);
    try {
      await setMissedKeys(runId, recordId, page.file_name, page.page_number, filled);
      onSaved(filled);
      setSaved(true);
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : String(exc));
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="mr-tab-content">
      <p className="mr-statement">Add key/value pairs that were present on this page but not extracted.</p>
      <div className="mr-missing-list">
        {rows.map((row, index) => (
          <div key={index} className="mr-missing-row mr-missed-key-row">
            <select
              value={row.field}
              disabled={saving}
              aria-label={`Extractor for missed key ${index + 1}`}
              onChange={(e) => {
                const field = e.target.value as FieldKey;
                setRows((prev) => prev.map((item, i) => (i === index ? { ...item, field } : item)));
                setSaved(false);
              }}
            >
              {EXTRACTOR_OPTIONS.map((opt) => (
                <option key={opt.value} value={opt.value}>
                  {opt.label}
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
              placeholder="Value"
              value={row.value}
              disabled={saving}
              onChange={(e) => {
                const value = e.target.value;
                setRows((prev) => prev.map((item, i) => (i === index ? { ...item, value } : item)));
                setSaved(false);
              }}
            />
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
            setRows((prev) => [...prev, { field: "dob", key: "", value: "" }]);
            setSaved(false);
          }}
        >
          Add another
        </button>
      </div>
      {saved && (
        <div className="mr-reviewed-banner">
          <div>
            <strong>Saved.</strong> Written to <code>manual_review.xlsx</code> sheet Missed_Keys.
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
