"use client";

import { useRef, useState } from "react";
import { IconFile, IconSparkle } from "@/components/icons";
import { ApiError, api } from "@/lib/api";
import { ACCEPT, MAX_FILES, fileProblem, prepareUpload } from "@/lib/files";
import type { Reading, UploadFile } from "@/lib/types";

/**
 * The optional first step: photograph or upload the bill and its papers, and Qwen-VL
 * reads what is printed on them. It fills in the form below; it decides nothing. Every
 * value it reads is shown with the words it was read from, for the clerk to check.
 */
export function DocumentReader({
  onRead,
  samples,
}: {
  onRead: (reading: Reading) => void;
  /** Ready-made documents for a demo, when the app ships some. */
  samples?: { label: string; load: () => Promise<UploadFile[]> }[];
}) {
  const [files, setFiles] = useState<File[]>([]);
  const [reading, setReading] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [dragging, setDragging] = useState(false);
  const input = useRef<HTMLInputElement>(null);

  function add(list: FileList | null) {
    if (!list) return;
    setError(null);
    const incoming = Array.from(list);
    const problem = incoming.map(fileProblem).find(Boolean);
    if (problem) return setError(problem);
    const next = [...files, ...incoming];
    if (next.length > MAX_FILES) return setError(`Add at most ${MAX_FILES} documents at a time.`);
    setFiles(next);
  }

  async function read(load: () => Promise<UploadFile[]>, what: string) {
    setError(null);
    setReading(what);
    try {
      onRead(await api.readDocuments(await load()));
      setFiles([]);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "These documents could not be prepared. Try a clearer photo or a PDF.");
    } finally {
      setReading(null);
    }
  }

  const count = files.length;

  return (
    <section className="card p-6 sm:p-8" aria-labelledby="docs-title">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <span className="eyebrow">Optional · fastest</span>
          <h2 id="docs-title" className="mt-1.5 text-[19px] font-semibold tracking-[-0.015em]">
            Start from the documents
          </h2>
          <p className="mt-1.5 max-w-[540px] text-[13.5px] leading-relaxed text-ink-2">
            Photograph or upload the bill and its papers. Qwen reads what is printed on them and
            fills in the form; you check every value before the check starts.
          </p>
        </div>
      </div>

      {reading ? (
        <div role="status" className="mt-5 flex items-center gap-3 rounded-2xl border border-line bg-surface-2 px-4 py-4 text-[13.5px] text-ink-2">
          <span className="pulse-dot h-2.5 w-2.5 shrink-0 rounded-full bg-brand" />
          <span>
            Qwen is reading {reading}… This usually takes 10–15 seconds.
          </span>
        </div>
      ) : (
        <>
          <label
            onDragOver={(e) => {
              e.preventDefault();
              setDragging(true);
            }}
            onDragLeave={() => setDragging(false)}
            onDrop={(e) => {
              e.preventDefault();
              setDragging(false);
              add(e.dataTransfer.files);
            }}
            className={`mt-5 flex cursor-pointer flex-col items-center justify-center gap-1.5 rounded-2xl border border-dashed px-4 py-7 text-center transition ${
              dragging ? "border-brand bg-brand-wash" : "border-line-strong bg-surface hover:bg-surface-2"
            }`}
          >
            <IconFile size={22} className="text-ink-3" />
            <span className="text-[14px] font-medium">Drop photos or PDFs here, or choose files</span>
            <span className="text-[12px] text-ink-3">
              Up to {MAX_FILES}: the bill, a pre-authorisation letter, a discharge summary
            </span>
            <input
              ref={input}
              type="file"
              multiple
              accept={ACCEPT}
              className="sr-only"
              onChange={(e) => {
                add(e.target.files);
                e.target.value = "";     // choosing the same file again still fires
              }}
            />
          </label>

          {count > 0 && (
            <ul className="mt-3 flex flex-wrap gap-2" aria-label="Documents to read">
              {files.map((f, i) => (
                <li key={`${f.name}-${i}`} className="inline-flex max-w-full items-center gap-1.5 rounded-full border border-line bg-surface px-3 py-1 text-[12.5px] text-ink-2">
                  <span className="truncate">{f.name}</span>
                  <button
                    type="button"
                    onClick={() => setFiles(files.filter((_, j) => j !== i))}
                    className="-mr-1 grid h-5 w-5 place-items-center rounded-full text-ink-3 hover:bg-surface-2 hover:text-ink"
                    aria-label={`Remove ${f.name}`}
                  >
                    ×
                  </button>
                </li>
              ))}
            </ul>
          )}

          <div className="mt-4 flex flex-wrap items-center gap-2.5">
            <button
              type="button"
              disabled={count === 0}
              onClick={() =>
                read(() => Promise.all(files.map(prepareUpload)), count === 1 ? "1 document" : `${count} documents`)
              }
              className="inline-flex h-10 items-center gap-2 rounded-full bg-brand px-5 text-[13.5px] font-medium text-brand-ink transition hover:bg-brand-hover disabled:cursor-not-allowed disabled:opacity-40"
            >
              <IconSparkle size={15} /> Read {count > 1 ? `${count} documents` : "documents"}
            </button>
            {samples?.map((s) => (
              <button
                key={s.label}
                type="button"
                onClick={() => read(s.load, "the sample documents")}
                className="inline-flex h-10 items-center rounded-full border border-line-strong bg-surface px-4 text-[13px] text-ink-2 transition hover:text-ink"
              >
                {s.label}
              </button>
            ))}
          </div>
        </>
      )}

      {error && (
        <p role="alert" className="mt-4 rounded-xl bg-crit-wash px-4 py-3 text-[13px] text-crit-ink">
          {error}
        </p>
      )}
    </section>
  );
}
