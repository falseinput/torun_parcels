#!/usr/bin/env node
/**
 * Generate MapLibre SDF glyph PBFs for the parcel-number labels.
 *
 * MapLibre cannot render a symbol layer without a `glyphs` endpoint, and every
 * hosted one wants an API key. The page is otherwise self-contained, so we
 * bake our own: one font stack, two Unicode ranges, served as static files
 * from the same origin as the tiles.
 *
 * The output is a build input, not a build artefact -- parcel labels are
 * digits and '/', which do not change. Regenerate only when the font or the
 * label alphabet changes:
 *
 *     make glyphs
 *
 * Only range 0-255 is generated: parcel numbers are digits and '/'. A label
 * carrying Polish diacritics would need 256-511 (Latin Extended-A) added to
 * RANGES -- MapLibre requests ranges lazily, so an unused one is dead weight
 * in the repo rather than a cost to the visitor.
 */

"use strict";

const fs = require("fs");
const path = require("path");
const fontnik = require("fontnik");

// Pinned to a tag rather than main: an upstream reflow of the font would
// silently change every glyph's metrics on the next regeneration.
const FONT_URL =
  "https://github.com/notofonts/notofonts.github.io/raw/" +
  "noto-monthly-release-2026.08.01/fonts/NotoSans/hinted/ttf/NotoSans-Regular.ttf";

const FONT_STACK = "Noto Sans Regular";
const RANGES = [[0, 255]];

const ROOT = path.resolve(__dirname, "..");
const CACHE = path.join(ROOT, "build", "NotoSans-Regular.ttf");
const OUT_DIR = path.join(ROOT, "site", "glyphs", FONT_STACK);

async function font() {
  if (fs.existsSync(CACHE)) {
    console.log(`  cached ${path.relative(ROOT, CACHE)}`);
    return fs.readFileSync(CACHE);
  }
  console.log(`  fetching ${FONT_URL}`);
  const response = await fetch(FONT_URL);
  if (!response.ok) {
    throw new Error(`FAIL: font download returned ${response.status}`);
  }
  const buffer = Buffer.from(await response.arrayBuffer());
  fs.mkdirSync(path.dirname(CACHE), { recursive: true });
  fs.writeFileSync(CACHE, buffer);
  return buffer;
}

function range(buffer, start, end) {
  return new Promise((resolve, reject) => {
    fontnik.range({ font: buffer, start, end }, (err, data) =>
      err ? reject(err) : resolve(data)
    );
  });
}

async function main() {
  const buffer = await font();

  // Assert the stack name the style references is the one the font declares.
  // A mismatch produces no error anywhere -- the labels just never appear.
  const faces = await new Promise((resolve, reject) =>
    fontnik.load(buffer, (err, f) => (err ? reject(err) : resolve(f)))
  );
  const declared = `${faces[0].family_name} ${faces[0].style_name}`;
  if (declared !== FONT_STACK) {
    throw new Error(`FAIL: font declares "${declared}", style wants "${FONT_STACK}"`);
  }

  fs.mkdirSync(OUT_DIR, { recursive: true });
  for (const [start, end] of RANGES) {
    const pbf = await range(buffer, start, end);
    const file = path.join(OUT_DIR, `${start}-${end}.pbf`);
    fs.writeFileSync(file, pbf);
    console.log(`  ${path.relative(ROOT, file)}  (${(pbf.length / 1024).toFixed(1)} kB)`);
  }
}

main().catch((err) => {
  console.error(err.message || err);
  process.exit(1);
});
