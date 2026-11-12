import { cpSync, mkdirSync, copyFileSync } from "node:fs";
import { createRequire } from "node:module";
import { dirname, join } from "node:path";

const require = createRequire(import.meta.url);
const source = dirname(require.resolve("pdfjs-dist/package.json"));
const output = new URL("../public/pdf-assets/", import.meta.url);
mkdirSync(output, { recursive: true });
copyFileSync(join(source, "build/pdf.worker.min.mjs"), new URL("pdf.worker.min.mjs", output));
for (const name of ["cmaps", "standard_fonts", "wasm"]) {
  cpSync(join(source, name), new URL(name, output), { recursive: true });
}
