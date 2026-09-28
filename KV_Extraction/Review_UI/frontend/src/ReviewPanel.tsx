import { Fragment, useState, type ReactNode } from "react";
import { Check, ChevronDown, Crosshair, Plus, X } from "lucide-react";
import type { AddedValue, Candidate, Option, PageField, PageInfo, Verdict } from "./api";
import {
  candidateDetail,
  correctionSlots,
  draftProblem,
  EMPTY_ADDED,
  isExtracted,
  isHeading,
  otherKeys,
  wantsCorrection,
  type Draft,
  type PickTarget,
} from "./review";

export type KeyNeighbors = { before: string; after: string };

type Props = {
  fields: PageField[];
  drafts: Record<string, Draft>;
  reasons: Option[];
  headingReasons: Option[];
  levels: string[];
  idTypes: Option[];
  open: Set<string>;
  pick: PickTarget | null;
  saving: string;
  errors: Record<string, string>;
  reviewer: string;
  loading: boolean;
  hasNextPage: boolean;
  onReviewer: (name: string) => void;
  onToggle: (field: string) => void;
  onDraft: (field: string, update: (draft: Draft) => Draft) => void;
  onPick: (target: PickTarget | null) => void;
  onHoverCandidate: (candidate: Candidate | null) => void;
  keyNeighbors: (candidate: Candidate) => KeyNeighbors;
  onSave: (field: string) => void;
  onClear: (field: string) => void;
  onNextPage: () => void;
  pageKey: string;
  pageInfo: PageInfo | null;
  pageInfoPrior: PageInfo | null;
  onSavePageInfo: (pageSequence: string) => Promise<void>;
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
      {!loading && (
        <PageInfoBox
          key={props.pageKey}
          saved={props.pageInfo}
          prior={props.pageInfoPrior}
          reviewer={reviewer}
          onSave={props.onSavePageInfo}
        />
      )}
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

/** Page facts the reviewer records; nothing detects them and no accuracy counts them. */
function PageInfoBox({
  saved,
  prior,
  reviewer,
  onSave,
}: {
  saved: PageInfo | null;
  prior: PageInfo | null;
  reviewer: string;
  onSave: (pageSequence: string) => Promise<void>;
}) {
  const savedText = saved ? String(saved.page_sequence) : "";
  const [value, setValue] = useState(savedText || (prior ? String(prior.page_sequence) : ""));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const dirty = value.trim() !== savedText;
  const invalid = value.trim() !== "" && !/^[1-9]\d*$/.test(value.trim());

  async function save() {
    setBusy(true);
    setError("");
    try {
      await onSave(value.trim());
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : String(exc));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="mr-box-header kv-reviewer kv-page-info">
      <label title="Collected during review only: nothing detects it and it is not part of any accuracy">
        Correct Page Sequence Number
        <input
          inputMode="numeric"
          value={value}
          placeholder="e.g. 3"
          onChange={(event) => setValue(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter" && dirty && !invalid && reviewer.trim() && !busy) void save();
          }}
        />
        <button
          type="button"
          className="btn primary"
          disabled={!dirty || invalid || !reviewer.trim() || busy}
          onClick={() => void save()}
        >
          {busy ? "Saving…" : dirty ? "Save" : saved ? "Saved" : "Save"}
        </button>
      </label>
      {invalid ? <p className="mr-note">Enter a whole number of 1 or more (leave it empty and save to remove it).</p> : null}
      {!invalid && dirty && !reviewer.trim() ? <p className="mr-note">Enter your name above to save.</p> : null}
      {!saved && prior ? (
        <p className="mr-note">Pre-filled from {prior.run || "an earlier batch"}; save to keep it for this batch.</p>
      ) : null}
      {error ? <div className="banner mr-error">{error}</div> : null}
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
  fields,
  draft,
  reasons,
  headingReasons,
  levels,
  idTypes,
  open,
  pick,
  saving,
  errors,
  reviewer,
  onToggle,
  onDraft,
  onPick,
  onHoverCandidate,
  keyNeighbors,
  onSave,
  onClear,
}: Props & { field: PageField; draft: Draft | undefined }) {
  if (!draft) return null;
  const isOpen = open.has(field.id);
  const primary = field.candidates.filter((c) => c.selected || c.accepted);
  const rejected = field.candidates.filter((c) => !c.selected && !c.accepted);
  const problem = draftProblem(field, draft);
  const heading = isHeading(field);
  const fieldIdTypes = field.id === "member_id" ? idTypes : [];
  const extractedText = field.candidates
    .filter((c) => isExtracted(field, c))
    .map((c) => c.value)
    .join(" | ");
  const otherFields = fields
    .filter((other) => other.id !== field.id && !isHeading(other))
    .map(({ id, label }) => ({ id, label }));

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
  const addedEditor = (item: AddedValue, index: number, removable: boolean) => (
    <AddedEditor
      fieldId={field.id}
      heading={heading}
      levels={levels}
      idTypes={fieldIdTypes}
      item={item}
      index={index}
      pick={pick}
      disabled={draft.not_present}
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
        idTypes={fieldIdTypes}
        candidate={c}
        extracted={isExtracted(field, c)}
        verdict={draft.candidates[c.candidate_id]}
        reasons={heading ? headingReasons : reasons}
        otherFields={otherFields}
        otherKeys={heading ? [] : otherKeys(field, c)}
        neighbors={keyNeighbors(c)}
        disabled={draft.not_present}
        onVerdict={(v) => setVerdict(c.candidate_id, v)}
        onHover={onHoverCandidate}
      >
        {index >= 0 ? addedEditor(draft.added[index], index, false) : null}
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
              ? "This model found no headings on this page."
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
        {draft.added.map((item, index) => (item.for_candidate ? null : addedEditor(item, index, true)))}
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

        <div className="mr-text-input kv-notes">
          <input value={draft.notes} placeholder="Notes (optional)" onChange={(event) => update({ notes: event.target.value })} />
        </div>

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

function IdTypeSelect({
  value,
  idTypes,
  placeholder,
  disabled,
  onChange,
}: {
  value: string;
  idTypes: Option[];
  placeholder: string;
  disabled?: boolean;
  onChange: (idType: string) => void;
}) {
  return (
    <select className="kv-reason kv-level" value={value} disabled={disabled} onChange={(event) => onChange(event.target.value)}>
      <option value="">{placeholder}</option>
      {idTypes.map((type) => (
        <option key={type.id} value={type.id}>
          {type.label}
        </option>
      ))}
    </select>
  );
}

function AddedEditor({
  fieldId,
  heading,
  levels,
  idTypes,
  item,
  index,
  pick,
  disabled,
  onChange,
  onRemove,
  onPick,
}: {
  fieldId: string;
  heading: boolean;
  levels: string[];
  idTypes: Option[];
  item: AddedValue;
  index: number;
  pick: PickTarget | null;
  disabled: boolean;
  onChange: (change: Partial<AddedValue>) => void;
  onRemove?: () => void;
  onPick: (target: PickTarget | null) => void;
}) {
  const pickingValue = pick?.field === fieldId && pick.index === index && pick.part === "value";
  const pickingKey = pick?.field === fieldId && pick.index === index && pick.part === "key";
  // A correction keeps the wrong candidate's key, so it only asks for the value; headings have no key.
  const correction = !!item.for_candidate;
  const askKey = !correction && !heading;
  return (
    <div className={`kv-added ${correction ? "kv-correction" : ""}`}>
      {correction ? (
        <div className="kv-correction-title">{heading ? "Correct heading text (optional)" : "Correct value (optional)"}</div>
      ) : null}
      <div className="mr-missing-row">
        <input
          value={item.value}
          placeholder={
            heading ? "Heading as printed on the page" : fieldId === "page_no" ? "e.g. 2 of 5" : "Value as printed on the page"
          }
          disabled={disabled}
          onChange={(event) => onChange({ value: event.target.value })}
        />
        {heading ? (
          <LevelSelect value={item.level || ""} levels={levels} disabled={disabled} onChange={(level) => onChange({ level })} />
        ) : null}
        {idTypes.length > 0 ? (
          <IdTypeSelect
            value={item.id_type || ""}
            idTypes={idTypes}
            placeholder={correction || item.key.trim() ? "ID type (from key)" : "ID type?"}
            disabled={disabled}
            onChange={(id_type) => onChange({ id_type })}
          />
        ) : null}
        {fieldId === "electronic_signature" && (
          <input
            value={item.value2}
            placeholder="Signature date"
            disabled={disabled}
            onChange={(event) => onChange({ value2: event.target.value })}
          />
        )}
        {onRemove ? (
          <button type="button" className="mr-remove-row" onClick={onRemove}>
            Remove
          </button>
        ) : null}
      </div>
      {!askKey ? null : (
        <div className="mr-missing-row">
          <input
            value={item.key}
            placeholder="Key / label next to it (optional)"
            disabled={disabled}
            onChange={(event) => onChange({ key: event.target.value })}
          />
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
        {!askKey ? null : (
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
  idTypes,
  candidate,
  extracted,
  verdict,
  reasons,
  otherFields,
  otherKeys,
  neighbors,
  disabled,
  onVerdict,
  onHover,
  children,
}: {
  fieldId: string;
  heading: boolean;
  levels: string[];
  idTypes: Option[];
  candidate: Candidate;
  /** Counted by accuracy, so it needs a verdict. */
  extracted: boolean;
  verdict: Verdict | undefined;
  reasons: Option[];
  otherFields: Option[];
  otherKeys: Option[];
  neighbors: KeyNeighbors;
  disabled: boolean;
  onVerdict: (verdict: Verdict) => void;
  onHover: (candidate: Candidate | null) => void;
  children?: ReactNode;
}) {
  const detail = candidateDetail(fieldId, candidate);
  const state = verdict?.verdict || "";
  const blocking = state === "incorrect" && verdict?.reason === "not_a_key" && candidate.key_words.length > 0;
  const setReason = (reason: string) =>
    onVerdict({
      verdict: "incorrect",
      reason,
      belongs_to: reason === "wrong_field" ? verdict?.belongs_to || "" : "",
      prefer_candidate: reason === "other_selected" ? verdict?.prefer_candidate || "" : "",
      block_before: reason === "not_a_key" ? verdict?.block_before || "" : "",
      block_after: reason === "not_a_key" ? verdict?.block_after || "" : "",
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
                  : { verdict: "correct", reason: "", level: heading ? candidate.detail : "", id_type: candidate.id_type || "" },
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
      {idTypes.length > 0 && state === "correct" && (
        <div className="kv-level-row">
          <span>ID type</span>
          <IdTypeSelect
            value={verdict?.id_type || candidate.id_type || ""}
            idTypes={idTypes}
            placeholder="ID type?"
            onChange={(id_type) => onVerdict({ ...verdict!, id_type })}
          />
        </div>
      )}
      {state === "incorrect" && (
        <select className="kv-reason" value={verdict?.reason || ""} onChange={(event) => setReason(event.target.value)}>
          <option value="" disabled hidden>
            Why is it wrong?
          </option>
          {reasons
            // Only a selected value can lose to another key, and only when the page has one.
            .filter((reason) => reason.id !== "other_selected" || (candidate.selected && otherKeys.length > 0))
            .map((reason) => (
              <option key={reason.id} value={reason.id}>
                {reason.label}
              </option>
            ))}
        </select>
      )}
      {state === "incorrect" && verdict?.reason === "other_selected" && (
        <select
          className="kv-reason"
          value={verdict.prefer_candidate || ""}
          onChange={(event) => onVerdict({ ...verdict, prefer_candidate: event.target.value })}
        >
          <option value="" disabled hidden>
            Which key should have been selected?
          </option>
          {otherKeys.map((key) => (
            <option key={key.id} value={key.id}>
              {key.label}
            </option>
          ))}
        </select>
      )}
      {state === "incorrect" && verdict?.reason === "wrong_field" && (
        <select
          className="kv-reason"
          value={verdict.belongs_to || ""}
          onChange={(event) => onVerdict({ ...verdict, belongs_to: event.target.value })}
        >
          <option value="" disabled hidden>
            Which field does it belong to?
          </option>
          {otherFields.map((other) => (
            <option key={other.id} value={other.id}>
              {other.label}
            </option>
          ))}
        </select>
      )}
      {blocking && (
        <div className="kv-block">
          <div className="kv-block-title">
            “{candidate.key_text || candidate.key}” is not a key when it sits next to:
          </div>
          {neighbors.before || neighbors.after ? (
            <>
              {neighbors.before ? (
                <label className="kv-check">
                  <input
                    type="checkbox"
                    checked={!!verdict?.block_before}
                    onChange={(event) =>
                      onVerdict({ ...verdict!, block_before: event.target.checked ? neighbors.before : "" })
                    }
                  />
                  previous word <strong>{neighbors.before}</strong>
                </label>
              ) : null}
              {neighbors.after ? (
                <label className="kv-check">
                  <input
                    type="checkbox"
                    checked={!!verdict?.block_after}
                    onChange={(event) =>
                      onVerdict({ ...verdict!, block_after: event.target.checked ? neighbors.after : "" })
                    }
                  />
                  next word <strong>{neighbors.after}</strong>
                </label>
              ) : null}
              <p className="mr-note">Ticked words go into the key's block list on save and apply from the next run.</p>
            </>
          ) : (
            <p className="mr-note">No words next to this key on the page.</p>
          )}
        </div>
      )}
      {state === "incorrect" ? children : null}
    </div>
  );
}
