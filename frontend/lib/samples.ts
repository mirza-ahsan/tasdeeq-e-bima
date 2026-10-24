import { base64 } from "./files";
import type { UploadFile } from "./types";

/**
 * Fictional sample documents for the demo, served from public/samples and rendered by
 * scripts/make_samples.py. Each set is built to show one behaviour; see that script.
 */
export interface SampleSet {
  label: string;
  files: string[];
}

export const SAMPLE_SETS: SampleSet[] = [
  { label: "Sample: surgery, no approval letter", files: ["surgery-no-approval/bill.jpg", "surgery-no-approval/discharge-summary.jpg"] },
  { label: "Sample: surgery, approved", files: ["surgery-approved/bill.jpg", "surgery-approved/pre-auth-approval.jpg"] },
];

export const sampleUrl = (file: string) => `/samples/${file}`;

export async function loadSample(set: SampleSet): Promise<UploadFile[]> {
  return Promise.all(
    set.files.map(async (file) => {
      const res = await fetch(sampleUrl(file));
      if (!res.ok) throw new Error(`missing sample ${file}`);
      return { name: file.split("/").pop()!, data: await base64(await res.blob()) };
    }),
  );
}
