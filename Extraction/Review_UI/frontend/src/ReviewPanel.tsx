import { Fragment, type ReactNode } from "react";
import { Check, ChevronDown, Crosshair, Plus, X } from "lucide-react";
import type { AddedValue, Candidate, Option, PageField, Verdict } from "./api";
import {
  candidateDetail,
  correctionSlots,
  draftProblem,
  EMPTY_ADDED,
  GROUP_REASON,
  isExtracted,
  isHeading,
  needsKey,
  wantsCorrection,
  type Draft,
  type PickTarget,
} from "./review";

type Props = {
  fields: PageField[];
  drafts: Record<string, Draft>;
  reasons: Option[];
  headingReasons: Option[];
  levels: string[];
  /** The key-value groups a key can belong to instead. */
  groups: Option[];
  open: Set<string>;
  pick: PickTarget | null;
  saving: string;
  errors: Record<string, string>;
  reviewer: string;
  loading: boolean;
  hasNextPage: boolean;
  /** The OCR words of a selection, joined in reading order. */
  wordsText: (indexes: number[]) => string;
  onReviewer: (name: string) => void;
  onToggle: (field: string) => void;
  onDraft: (field: string, update: (draft: Draft) => Draft) => void;
  onPick: (target: PickTarget | null) => void;
  onHoverCandidate: (candidate: Candidate | null) => void;
  onSave: (field: string) => void;
  onClear: (field: string) => void;
  onNextPage: () => void;
};

export default function ReviewPanel(props: Props) {
  const { fields, drafts, loading, reviewer } = props;
  const reviewed = fields.filter((field) => field.review && !drafts[field.id]?.dirty).length;
  const allDone = fields.length > 0 && reviewed === fields.length;

  return (
    <div className="table-wrap mr-panel master-side-panel">
      <div className="table-toolbar">
        <div className="table-toolbar-start">
          <h3 className="output-panel-title">Review</h3>
        </div>
        <span className="mr-progress">
          {reviewed} / {fields.length} fields reviewed
        </span>
      </div>
      <div className="mr-box-header kv-reviewer">
        <label>
          Reviewer
          <input
            value={reviewer}
            placeholder="Your name"
            onChange={(event) => props.onReviewer(event.target.value)}
          />
        </label>
      </div>
      <div className="table-panel mr-body">
        {loading && <div className="empty">Loading page…</div>}
        {!loading && (
          <div className="mr-accordion">
            {fields.map((field, n) => (
              <Fragment key={field.id}>
                {isHeading(field) && !isHeading(fields[n - 1] || field) ? (
                  <div className="kv-group-label">Page structure</div>
                ) : null}
                <FieldSection field={field} draft={drafts[field.id]} {...props} />
              </Fragment>
            ))}
          </div>
        )}
        {!loading && allDone && (
          <div className="mr-reviewed-banner kv-done">
            All fields on this page are reviewed.
            {props.hasNextPage ? (
              <button type="button" className="mr-modify-btn" onClick={props.onNextPage}>
                Next page
              </button>
            ) : null}
          </div>
        )}
      </div>
    </div>
  );
}

function Badge({ field, draft }: { field: PageField; draft: Draft | undefined }) {
  if (draft?.dirty && draft.prefilled) return <span className="mr-accordion-badge ready">Pre-filled</span>;
  if (draft?.dirty) return <span className="mr-accordion-badge ready">Unsaved</span>;
  if (field.review) {
    return field.review.correct ? (
      <span className="mr-accordion-badge saved">Correct</span>
    ) : (
      <span className="mr-accordion-badge moot">Wrong</span>
    );
  }
  return <span className="mr-accordion-badge">To review</span>;
}

function FieldSection({
  field,
  draft,
  reasons,
  headingReasons,
  levels,
  groups,
  open,
  pick,
  saving,
  errors,
  reviewer,
  wordsText,
  onToggle,
  onDraft,
  onPick,
  onHoverCandidate,
  onSave,
  onClear,
}: Props & { field: PageField; draft: Draft | undefined }) {
  if (!draft) return null;
  const isOpen = open.has(field.id);
  const primary = field.candidates.filter((c) => c.selected || c.accepted);
  const rejected = field.candidates.filter((c) => !c.selected && !c.accepted);
  const problem = draftProblem(field, draft);
  const heading = isHeading(field);
  const extractedText = field.candidates
    .filter((c) => isExtracted(field, c))
    .map((c) => c.value)
    .join(" | ");
  const otherGroups = groups.filter((group) => group.id !== field.id);

  const update = (change: Partial<Draft>) => onDraft(field.id, (current) => ({ ...current, ...change, dirty: true }));
  const setVerdict = (cid: string, verdict: Verdict) => {
    // Dropping a correction slot shifts the indexes a pick target points at.
    const dropsSlot = !wantsCorrection(verdict) && draft.added.some((item) => item.for_candidate === cid);
    if (dropsSlot && pick?.field === field.id) onPick(null);
    onDraft(field.id, (current) => {
      const next = { ...current.candidates };
      if (verdict.verdict) next[cid] = verdict;
      else delete next[cid];
      return { ...current, candidates: next, added: correctionSlots(field, next, current.added), dirty: true };
    });
  };
  const wordsEditor = (item: AddedValue, index: number, removable: boolean) => (
    <WordsEditor
      fieldId={field.id}
      heading={heading}
      levels={levels}
      item={item}
      index={index}
      pick={pick}
      disabled={draft.not_present}
      // A Wrong Value correction keeps the candidate's key; a Wrong Key & Value / Wrong Position one
      // offers the key to select; a missed value may have a key.
      keyMode={heading ? "none" : item.for_candidate ? (needsKey(draft.candidates[item.for_candidate]) ? "required" : "none") : "optional"}
      valueText={wordsText(item.value_words)}
      keyText={wordsText(item.key_words)}
      onChange={(change) =>
        update({ added: draft.added.map((row, n) => (n === index ? { ...row, ...change } : row)) })
      }
      onRemove={
        removable
          ? () => {
              if (pick?.field === field.id) onPick(null);
              update({ added: draft.added.filter((_, n) => n !== index) });
            }
          : undefined
      }
      onPick={onPick}
    />
  );
  const candidateRow = (c: Candidate) => {
    const index = draft.added.findIndex((item) => item.for_candidate === c.candidate_id);
    return (
      <CandidateRow
        key={c.candidate_id}
        fieldId={field.id}
        heading={heading}
        levels={levels}
        candidate={c}
        extracted={isExtracted(field, c)}
        verdict={draft.candidates[c.candidate_id]}
        reasons={heading ? headingReasons : reasons}
        groups={otherGroups}
        disabled={draft.not_present}
        onVerdict={(v) => setVerdict(c.candidate_id, v)}
        onHover={onHoverCandidate}
      >
        {index >= 0 ? wordsEditor(draft.added[index], index, false) : null}
      </CandidateRow>
    );
  };

  return (
    <div className={`mr-accordion-item ${isOpen ? "open" : ""}`}>
      <button type="button" className="mr-accordion-head" aria-expanded={isOpen} onClick={() => onToggle(field.id)}>
        <span className="kv-acc-title">
          <span className="mr-accordion-title">{field.label}</span>
          <span className="kv-acc-sub" title={extractedText}>
            {extractedText || (heading ? "no headings detected" : "nothing extracted")}
          </span>
        </span>
        <span className="mr-accordion-state">
          <Badge field={field} draft={draft} />
          <ChevronDown size={16} className="mr-accordion-chevron" aria-hidden="true" />
        </span>
      </button>
      <div className={`mr-accordion-body ${isOpen ? "" : "collapsed"}`}>
        {draft.prefilled && draft.dirty ? (
          <p className="mr-note">Pre-filled from the review in {draft.prefilled}. Check it and save.</p>
        ) : null}

        {primary.length === 0 && rejected.length === 0 ? (
          <p className="mr-note">
            {heading
              ? "The heading model found no headings on this page."
              : "The rules found no candidates for this field on this page."}
          </p>
        ) : null}
        {primary.map(candidateRow)}
        {rejected.length > 0 && (
          <>
            <div className="kv-section-label">
              {heading ? "Near misses, not taken as headings" : "Rejected by the rules"} ({rejected.length})
            </div>
            {rejected.map(candidateRow)}
          </>
        )}

        <div className="kv-section-label">{heading ? "Missed headings" : "Missed values"}</div>
        {draft.added.map((item, index) => (item.for_candidate ? null : wordsEditor(item, index, true)))}
        <button
          type="button"
          className="btn secondary mr-add-btn"
          disabled={draft.not_present}
          onClick={() => {
            update({ added: [...draft.added, { ...EMPTY_ADDED }] });
            onPick({ field: field.id, index: draft.added.length, part: "value" });
          }}
        >
          <Plus size={13} aria-hidden="true" /> {heading ? "Add missed heading" : "Add missed value"}
        </button>

        <label className="kv-check">
          <input
            type="checkbox"
            checked={draft.not_present}
            onChange={(event) => {
              if (pick?.field === field.id) onPick(null);
              update({ not_present: event.target.checked });
            }}
          />
          {heading ? "No headings on this page" : `${field.label} is not on this page`}
        </label>

        {errors[field.id] && <div className="banner mr-error">{errors[field.id]}</div>}
        {draft.dirty && problem ? <p className="mr-note">{problem}</p> : null}
        {draft.dirty && !problem && !reviewer.trim() ? <p className="mr-note">Enter your name above to save.</p> : null}
        <div className="mr-actions kv-actions">
          {field.review ? (
            <button type="button" className="kv-link-btn danger" disabled={saving === field.id} onClick={() => onClear(field.id)}>
              Clear review
            </button>
          ) : (
            <span />
          )}
          <button
            type="button"
            className="btn primary"
            disabled={!draft.dirty || !!problem || !reviewer.trim() || saving === field.id}
            onClick={() => onSave(field.id)}
          >
            {saving === field.id ? "Saving…" : field.review && !draft.dirty ? "Saved" : "Save"}
          </button>
        </div>
      </div>
    </div>
  );
}

function LevelSelect({
  value,
  levels,
  disabled,
  onChange,
}: {
  value: string;
  levels: string[];
  disabled?: boolean;
  onChange: (level: string) => void;
}) {
  return (
    <select className="kv-reason kv-level" value={value} disabled={disabled} onChange={(event) => onChange(event.target.value)}>
      <option value="" disabled hidden>
        Level?
      </option>
      {levels.map((level) => (
        <option key={level} value={level}>
          {level}
        </option>
      ))}
    </select>
  );
}

/**
 * The words the reviewer selects on the OCR (on the page image or in the OCR text): nothing is
 * typed, and what is selected may be wrong or oddly formatted; it only has to be on the OCR.
 */
function WordsEditor({
  fieldId,
  heading,
  levels,
  item,
  index,
  pick,
  disabled,
  keyMode,
  valueText,
  keyText,
  onChange,
  onRemove,
  onPick,
}: {
  fieldId: string;
  heading: boolean;
  levels: string[];
  item: AddedValue;
  index: number;
  pick: PickTarget | null;
  disabled: boolean;
  keyMode: "none" | "optional" | "required";
  valueText: string;
  keyText: string;
  onChange: (change: Partial<AddedValue>) => void;
  onRemove?: () => void;
  onPick: (target: PickTarget | null) => void;
}) {
  const pickingValue = pick?.field === fieldId && pick.index === index && pick.part === "value";
  const pickingKey = pick?.field === fieldId && pick.index === index && pick.part === "key";
  const correction = !!item.for_candidate;
  const title = correction
    ? keyMode === "required"
      ? "Select the right key and value on the OCR (optional)"
      : heading
        ? "Select the right heading words on the OCR (optional)"
        : "Select the right value on the OCR (optional)"
    : null;
  return (
    <div className={`kv-added ${correction ? "kv-correction" : ""}`}>
      {title ? <div className="kv-correction-title">{title}</div> : null}
      {fieldId === "electronic_signature" ? (
        <p className="mr-note">Select the whole signature in one go: the name and the date together.</p>
      ) : null}
      <div className="mr-missing-row">
        <span className="kv-selected-text" title={valueText}>
          {valueText || <em>{heading ? "no heading words selected yet" : "no value words selected yet"}</em>}
        </span>
        {heading ? (
          <LevelSelect value={item.level || ""} levels={levels} disabled={disabled} onChange={(level) => onChange({ level })} />
        ) : null}
        {onRemove ? (
          <button type="button" className="mr-remove-row" onClick={onRemove}>
            Remove
          </button>
        ) : null}
      </div>
      {keyMode === "none" ? null : (
        <div className="mr-missing-row">
          <span className="kv-selected-text" title={keyText}>
            {keyText ? `key: ${keyText}` : <em>no key selected (optional)</em>}
          </span>
        </div>
      )}
      <div className="kv-pick-row">
        <button
          type="button"
          className={`kv-pick-btn ${pickingValue ? "active" : ""}`}
          disabled={disabled}
          onClick={() => onPick(pickingValue ? null : { field: fieldId, index, part: "value" })}
        >
          <Crosshair size={13} aria-hidden="true" />
          {pickingValue ? "Done picking" : heading ? "Pick heading words" : "Pick value words"} ({item.value_words.length})
        </button>
        {keyMode === "none" ? null : (
          <button
            type="button"
            className={`kv-pick-btn key ${pickingKey ? "active" : ""}`}
            disabled={disabled}
            onClick={() => onPick(pickingKey ? null : { field: fieldId, index, part: "key" })}
          >
            <Crosshair size={13} aria-hidden="true" />
            {pickingKey ? "Done picking" : "Pick key words"} ({item.key_words.length})
          </button>
        )}
      </div>
    </div>
  );
}

function CandidateRow({
  fieldId,
  heading,
  levels,
  candidate,
  extracted,
  verdict,
  reasons,
  groups,
  disabled,
  onVerdict,
  onHover,
  children,
}: {
  fieldId: string;
  heading: boolean;
  levels: string[];
  candidate: Candidate;
  /** Counted by accuracy, so it needs a verdict. */
  extracted: boolean;
  verdict: Verdict | undefined;
  reasons: Option[];
  groups: Option[];
  disabled: boolean;
  onVerdict: (verdict: Verdict) => void;
  onHover: (candidate: Candidate | null) => void;
  children?: ReactNode;
}) {
  const detail = candidateDetail(fieldId, candidate);
  const state = verdict?.verdict || "";
  const setReason = (reason: string) =>
    onVerdict({
      verdict: "incorrect",
      reason,
      belongs_to: reason === GROUP_REASON ? verdict?.belongs_to || "" : "",
    });
  return (
    <div
      className={`kv-cand ${extracted ? "extracted" : ""} ${candidate.accepted || candidate.selected ? "" : "rejected"} ${state}`}
      onMouseEnter={() => onHover(candidate)}
      onMouseLeave={() => onHover(null)}
    >
      <div className="kv-cand-top">
        <div className="kv-cand-main">
          <div className="kv-cand-value">
            {candidate.value || <em>key found, no value</em>}
            {detail ? <span className="kv-cand-detail"> · {detail}</span> : null}
          </div>
          <div className="kv-cand-meta">
            {extracted ? <span className="kv-chip extracted">{heading ? "Detected" : "Extracted"}</span> : null}
            {!heading && candidate.selected ? (
              <span className="kv-chip" title="The value chosen for the record's output">
                Selected
              </span>
            ) : null}
            {heading ? null : (
              <span className="kv-chip" title={candidate.key}>
                {candidate.key_text ? `key “${candidate.key_text}”` : candidate.key ? candidate.key : "no key"}
              </span>
            )}
            {candidate.region ? <span className="kv-chip">{candidate.region}</span> : null}
            {candidate.source ? (
              <span className="kv-chip">
                {candidate.source}
                {heading && candidate.score ? ` ${Number(candidate.score).toFixed(2)}` : ""}
              </span>
            ) : null}
            {candidate.note ? <span className="kv-chip kv-chip-note">{candidate.note}</span> : null}
          </div>
        </div>
        <div className="mr-tickcross-buttons">
          <button
            type="button"
            className={`mr-tick-btn ${state === "correct" ? "active" : ""}`}
            title="Correct"
            aria-label="Correct"
            disabled={disabled || !candidate.value}
            onClick={() =>
              onVerdict(
                state === "correct"
                  ? { verdict: "", reason: "" }
                  : { verdict: "correct", reason: "", level: heading ? candidate.detail : "" },
              )
            }
          >
            <Check size={14} aria-hidden="true" />
          </button>
          <button
            type="button"
            className={`mr-cross-btn ${state === "incorrect" ? "active" : ""}`}
            title="Incorrect"
            aria-label="Incorrect"
            disabled={disabled}
            onClick={() =>
              onVerdict(state === "incorrect" ? { verdict: "", reason: "" } : { verdict: "incorrect", reason: verdict?.reason || "" })
            }
          >
            <X size={14} aria-hidden="true" />
          </button>
        </div>
      </div>
      {heading && state === "correct" && (
        <div className="kv-level-row">
          <span>Level</span>
          <LevelSelect
            value={verdict?.level || candidate.detail}
            levels={levels}
            onChange={(level) => onVerdict({ ...verdict!, level })}
          />
        </div>
      )}
      {state === "incorrect" && (
        <select className="kv-reason" value={verdict?.reason || ""} onChange={(event) => setReason(event.target.value)}>
          <option value="" disabled hidden>
            Why is it wrong?
          </option>
          {reasons.map((reason) => (
            <option key={reason.id} value={reason.id}>
              {reason.label}
            </option>
          ))}
        </select>
      )}
      {state === "incorrect" && verdict?.reason === GROUP_REASON && (
        <select
          className="kv-reason"
          value={verdict.belongs_to || ""}
          onChange={(event) => onVerdict({ ...verdict, belongs_to: event.target.value })}
        >
          <option value="" disabled hidden>
            Which group does the key belong to?
          </option>
          {groups.map((group) => (
            <option key={group.id} value={group.id}>
              {group.label}
            </option>
          ))}
        </select>
      )}
      {state === "incorrect" ? children : null}
    </div>
  );
}
