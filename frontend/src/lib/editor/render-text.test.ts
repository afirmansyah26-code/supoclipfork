import { expect, it } from "vitest";
import { draft } from "./document.test-fixture";
import { formatHookEditorText, hookTitleLayout, prepareTextDraft, textEditorLayout, textLayerBounds } from "./render";

function context() {
  return {
    font: "10px sans-serif",
    measureText(this: { font: string }, text: string) {
      const px = Number.parseFloat(this.font);
      return { width: text.length * px * .5, fontBoundingBoxAscent: px * .9, fontBoundingBoxDescent: px * .3 };
    },
  } as unknown as CanvasRenderingContext2D;
}

it.each(["hook", "captions"] as const)("uses the canvas font-height correction for the %s native caret", layer => {
  const layout = textEditorLayout(context(), { ...draft, hookTitle: "Editable title" }, 1080, 1920, .2, layer);
  const assPixels = layer === "hook" ? 44 : 54;
  expect(layout.fontSize).toBeCloseTo(assPixels / 1.2);
  expect(layout.lineHeight).toBeCloseTo(assPixels * 1.2);
});

it("keeps original word timings and highlights in an unchanged editing draft", () => {
  const before = structuredClone(draft);
  const scratch = prepareTextDraft(draft, .2, "captions", "Hello world today");
  expect(scratch.captionWords).toEqual(draft.words);
  expect(prepareTextDraft(draft, .2, "captions", "A revised phrase").captionWords?.map(word => word.text)).toEqual(["A", "revised", "phrase"]);
  expect(draft).toEqual(before);
});

it("balances hook editor lines without changing stored casing or eating a typed space", () => {
  const text = "Mixed Case Äpfel remain editable while typing ";
  const formatted = formatHookEditorText({ ...draft, captions: { ...draft.captions, uppercase: true } }, 1080, 1920, text);
  expect(formatted.replace(/\s+/g, " ")).toBe(text);
  expect(prepareTextDraft(draft, .2, "hook", formatted).hookText).toBe(text.trim());
});

it("re-wraps a larger hook title to fit within the frame", () => {
  const doc = {
    ...draft,
    hookTitle: "A long hook title that needs to wrap when its font gets larger",
    textTransforms: { hook: { x: 0, y: 0, scale: 1.8 } },
  };
  const layout = hookTitleLayout(doc, 360, 640);
  const longestWidth = Math.max(...layout.lines.map(line => line.length * layout.px * 0.52));
  expect(layout.lines.length).toBeGreaterThan(1);
  expect(longestWidth).toBeLessThanOrEqual(layout.usable);
  expect(layout.x).toBeGreaterThanOrEqual(0);
  expect(layout.x).toBeLessThanOrEqual(360);
  expect(layout.top).toBeGreaterThanOrEqual(8);
  expect(layout.top + layout.lines.length * layout.px * 1.2).toBeLessThanOrEqual(640);
});

it("keeps the hook selection bounds aligned with the measured visible title", () => {
  const doc = {
    ...draft,
    hookTitle: "A larger title to check its selectable and resizable bounds",
    textTransforms: { hook: { x: 0, y: 0.4, scale: 1.8 } },
  };
  const ctx = context();
  const bounds = textLayerBounds(ctx, doc, 360, 640, 0.2, "hook");
  expect(bounds).not.toBeNull();
  expect(bounds!.x).toBeGreaterThanOrEqual(0);
  expect(bounds!.y).toBeGreaterThanOrEqual(0);
  expect(bounds!.x + bounds!.width).toBeLessThanOrEqual(360);
  expect(bounds!.y + bounds!.height).toBeLessThanOrEqual(640);
  expect(bounds!.width).toBeGreaterThan(0);
  expect(bounds!.height).toBeGreaterThan(0);
});
