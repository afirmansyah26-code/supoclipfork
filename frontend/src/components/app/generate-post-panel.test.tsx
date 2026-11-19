import { NextIntlClientProvider } from "next-intl";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import messages from "../../../messages/en/post.json";
import { GeneratePostPanel } from "./generate-post-panel";

afterEach(() => vi.unstubAllGlobals());

function renderPanel() {
  return render(
    <NextIntlClientProvider locale="en" messages={{ post: messages }}>
      <GeneratePostPanel taskId="task-1" clipId="clip-1" />
    </NextIntlClientProvider>,
  );
}

describe("GeneratePostPanel", () => {
  it("keeps the save action visible above the editable fields", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(
      Response.json({ hook_title: "Saved hook", title: "Saved title", description: "Saved description" }),
    ));

    renderPanel();

    expect(await screen.findByRole("button", { name: "Save to clip" })).toBeVisible();
    expect(screen.getByPlaceholderText("On-screen headline")).toHaveValue("Saved hook");
    expect(screen.getByPlaceholderText("Post title")).toHaveValue("Saved title");
    expect(screen.getByPlaceholderText("Post description with hashtags")).toHaveValue("Saved description");
  });

  it("persists all three fields when saving", async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(Response.json({ hook_title: "", title: "", description: "" }))
      .mockResolvedValueOnce(Response.json({ hook_title: "New hook", title: "New title", description: "New description" }));
    vi.stubGlobal("fetch", fetchMock);

    renderPanel();
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));

    fireEvent.change(screen.getByPlaceholderText("On-screen headline"), { target: { value: "New hook" } });
    fireEvent.change(screen.getByPlaceholderText("Post title"), { target: { value: "New title" } });
    fireEvent.change(screen.getByPlaceholderText("Post description with hashtags"), { target: { value: "New description" } });
    fireEvent.click(screen.getByRole("button", { name: "Save to clip" }));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
    expect(fetchMock.mock.calls[1]?.[1]).toMatchObject({
      method: "PATCH",
      body: JSON.stringify({ hook_title: "New hook", title: "New title", description: "New description" }),
    });
  });
});
