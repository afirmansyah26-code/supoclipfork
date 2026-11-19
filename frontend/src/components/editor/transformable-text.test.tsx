import { createRef } from "react";
import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { draft } from "@/lib/editor/document.test-fixture";
import { TransformableText } from "./transformable-text";

const bounds = { x: 100, y: 100, width: 100, height: 80 };
const originalPointerCapture = Object.getOwnPropertyDescriptor(HTMLElement.prototype, "setPointerCapture");

afterEach(() => {
  vi.restoreAllMocks();
  if (originalPointerCapture) Object.defineProperty(HTMLElement.prototype, "setPointerCapture", originalPointerCapture);
  else Reflect.deleteProperty(HTMLElement.prototype, "setPointerCapture");
});

it("moves the hook by the pointer delta", () => {
  Object.defineProperty(HTMLElement.prototype, "setPointerCapture", { configurable: true, value: vi.fn() });
  vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockReturnValue({
    x: 0, y: 0, width: 360, height: 640, top: 0, right: 360, bottom: 640,
    left: 0, toJSON() {},
  } as DOMRect);
  const frame = createRef<HTMLDivElement>();
  let updated = draft;
  render(
    <div ref={frame}>
      <TransformableText
        doc={draft}
        layer="hook"
        label="Hook title"
        value="Hook title"
        buttonLabel="Hook title"
        bounds={bounds}
        width={360}
        height={640}
        frame={frame}
        measure={() => bounds}
        pause={() => {}}
        update={change => { updated = change(updated); }}
        onDraft={() => {}}
        onCommit={() => {}}
      />
    </div>,
  );

  const title = screen.getByRole("button", { name: "Hook title" });
  fireEvent.pointerDown(title, { pointerId: 1, clientX: 100, clientY: 100, buttons: 1 });
  fireEvent.pointerMove(title, { pointerId: 1, clientX: 136, clientY: 132, buttons: 1 });
  fireEvent.pointerUp(title, { pointerId: 1, buttons: 0 });

  expect(updated.textTransforms?.hook?.x).toBeCloseTo(0.1);
  expect(updated.textTransforms?.hook?.y).toBeCloseTo(0.05);
});

it("resizes the hook from a corner handle", () => {
  const capture = vi.fn();
  Object.defineProperty(HTMLElement.prototype, "setPointerCapture", { configurable: true, value: capture });
  vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockReturnValue({
    x: 0, y: 0, width: 360, height: 640, top: 0, right: 360, bottom: 640,
    left: 0, toJSON() {},
  } as DOMRect);
  const frame = createRef<HTMLDivElement>();
  let updated = draft;
  render(
    <div ref={frame}>
      <TransformableText
        doc={draft}
        layer="hook"
        label="Hook title"
        value="Hook title"
        buttonLabel="Hook title"
        bounds={bounds}
        width={360}
        height={640}
        frame={frame}
        measure={() => bounds}
        pause={() => {}}
        update={change => { updated = change(updated); }}
        onDraft={() => {}}
        onCommit={() => {}}
      />
    </div>,
  );

  const handle = screen.getByRole("button", { name: "Resize Hook title, bottom right" });
  fireEvent.pointerDown(handle, { pointerId: 1, clientX: 100, clientY: 100, buttons: 1 });
  fireEvent.pointerMove(handle, { pointerId: 1, clientX: 120, clientY: 120, buttons: 1 });
  fireEvent.pointerUp(handle, { pointerId: 1, buttons: 0 });

  expect(capture).toHaveBeenCalledOnce();
  expect(updated.textTransforms?.hook?.scale).toBeGreaterThan(1);
});
