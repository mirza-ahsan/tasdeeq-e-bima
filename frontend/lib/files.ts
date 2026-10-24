import type { UploadFile } from "./types";

/**
 * Getting the clerk's documents ready to send.
 *
 * Photos are shrunk in the browser before upload: a phone photo is several megabytes and
 * Qwen-VL reads print just as well at 1,600 pixels. Redrawing it also drops the photo's
 * metadata (location, device) before anything leaves the machine. PDFs go as they are;
 * the server renders their pages. The server checks everything again either way.
 */

export const MAX_FILES = 3;
export const MAX_FILE_MB = 4;
const MAX_SIDE = 1600;
const IMAGE_TYPES = ["image/jpeg", "image/png", "image/webp"];
export const ACCEPT = [...IMAGE_TYPES, "application/pdf"].join(",");

/** Why this file cannot be used, or null if it can. */
export function fileProblem(file: { name: string; type: string; size: number }): string | null {
  if (![...IMAGE_TYPES, "application/pdf"].includes(file.type)) {
    return `${file.name} is not a photo (JPEG, PNG, WebP) or a PDF.`;
  }
  // Photos are shrunk before upload, so only a PDF has to fit as it is.
  if (file.type === "application/pdf" && file.size > MAX_FILE_MB * 1024 * 1024) {
    return `${file.name} is larger than ${MAX_FILE_MB} MB.`;
  }
  return null;
}

export function base64(blob: Blob): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result).split(",", 2)[1] ?? "");
    reader.onerror = () => reject(reader.error);
    reader.readAsDataURL(blob);
  });
}

async function shrink(file: File): Promise<Blob> {
  const bitmap = await createImageBitmap(file, { imageOrientation: "from-image" });
  const scale = Math.min(1, MAX_SIDE / Math.max(bitmap.width, bitmap.height));
  const canvas = document.createElement("canvas");
  canvas.width = Math.round(bitmap.width * scale);
  canvas.height = Math.round(bitmap.height * scale);
  const ctx = canvas.getContext("2d");
  if (!ctx) throw new Error("no canvas");
  ctx.fillStyle = "#fff";             // a transparent PNG would otherwise turn black
  ctx.fillRect(0, 0, canvas.width, canvas.height);
  ctx.drawImage(bitmap, 0, 0, canvas.width, canvas.height);
  bitmap.close();
  return new Promise((resolve, reject) =>
    canvas.toBlob((b) => (b ? resolve(b) : reject(new Error("encode failed"))), "image/jpeg", 0.85),
  );
}

export async function prepareUpload(file: File): Promise<UploadFile> {
  const blob = file.type === "application/pdf" ? file : await shrink(file);
  return { name: file.name.slice(0, 120), data: await base64(blob) };
}
