import { describe, expect, it } from "vitest";
import { fileProblem } from "@/lib/files";

const MB = 1024 * 1024;

describe("fileProblem", () => {
  it("accepts photos of any size, since they are shrunk before upload", () => {
    expect(fileProblem({ name: "bill.jpg", type: "image/jpeg", size: 12 * MB })).toBeNull();
    expect(fileProblem({ name: "bill.png", type: "image/png", size: 1 * MB })).toBeNull();
  });

  it("accepts a PDF up to the limit and refuses one over it", () => {
    expect(fileProblem({ name: "a.pdf", type: "application/pdf", size: 3 * MB })).toBeNull();
    expect(fileProblem({ name: "a.pdf", type: "application/pdf", size: 5 * MB })).toMatch(/larger than 4 MB/);
  });

  it("names the file and says what is accepted", () => {
    expect(fileProblem({ name: "notes.docx", type: "application/msword", size: 100 })).toBe(
      "notes.docx is not a photo (JPEG, PNG, WebP) or a PDF.",
    );
    expect(fileProblem({ name: "scan.heic", type: "image/heic", size: 100 })).toMatch(/not a photo/);
  });
});
