import { useMemo, useState } from "react";
import type { PageOcr } from "./api";

type Props = {
  ocr: PageOcr | null;
  loading: boolean;
  picking: boolean;
  picked: Set<number>;
  highlighted: Set<number>;
  onHoverWord: (index: number | null) => void;
  onClickWord: (index: number, additive: boolean) => void;
};

export default function OcrPanel({ ocr, loading, picking, picked, highlighted, onHoverWord, onClickWord }: Props) {
  const [search, setSearch] = useState("");
  const needle = search.trim().toLowerCase();

  const matches = useMemo(() => {
    const found = new Set<number>();
    if (!needle || !ocr) return found;
    for (const line of ocr.lines) {
      for (const word of line) if (word.t.toLowerCase().includes(needle)) found.add(word.i);
    }
    return found;
  }, [needle, ocr]);

  return (
    <div className="table-wrap mr-panel master-side-panel">
      <div className="table-toolbar">
        <div className="table-toolbar-start">
          <h3 className="output-panel-title">OCR Data</h3>
        </div>
        {needle ? <span className="kv-count">{matches.size} matches</span> : null}
      </div>
      <div className="mr-box-header kv-ocr-header">
        <input
          className="kv-search"
          type="search"
          placeholder="Find in OCR text"
          value={search}
          onChange={(event) => setSearch(event.target.value)}
        />
        <p>
          {picking
            ? "Pick mode: click a word here or on the page image; Ctrl+click to add or remove more words."
            : "Hover a word to find it on the page."}
        </p>
      </div>
      <div className={`table-panel mr-body ${picking ? "kv-picking" : ""}`}>
        {loading && <div className="empty">Loading OCR…</div>}
        {!loading && ocr && !ocr.available && <div className="empty">OCR output is not available for this page.</div>}
        {!loading && ocr?.available && (
          <div className="kv-ocr-lines" onMouseLeave={() => onHoverWord(null)}>
            {ocr.lines.map((line, n) => (
              <div key={n} className="kv-ocr-line">
                {line.map((word) => {
                  const classes = [
                    "kv-word",
                    picked.has(word.i) ? "picked" : "",
                    highlighted.has(word.i) ? "cand" : "",
                    matches.has(word.i) ? "match" : "",
                  ]
                    .filter(Boolean)
                    .join(" ");
                  return (
                    <span
                      key={word.i}
                      className={classes}
                      onMouseEnter={() => onHoverWord(word.i)}
                      onClick={(event) => picking && onClickWord(word.i, event.ctrlKey || event.metaKey)}
                    >
                      {word.t}
                    </span>
                  );
                })}
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
