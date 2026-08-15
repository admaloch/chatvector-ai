import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import RetrievalSettingsPanel from "./RetrievalSettingsPanel";
import { DEFAULT_RETRIEVAL_SETTINGS } from "../lib/retrievalSettings";

describe("RetrievalSettingsPanel", () => {
  it("calls scope and match count handlers when controls change", async () => {
    const onScopeChange = vi.fn();
    const onMatchCountChange = vi.fn();

    render(
      <RetrievalSettingsPanel
        settings={DEFAULT_RETRIEVAL_SETTINGS}
        onScopeChange={onScopeChange}
        onMatchCountChange={onMatchCountChange}
      />
    );

    await userEvent.click(screen.getByText("Retrieval settings"));

    await userEvent.click(screen.getByRole("radio", { name: "Tenant" }));
    expect(onScopeChange).toHaveBeenCalledWith("tenant");

    const matchCountInput = screen.getByRole("spinbutton", { name: "Match count" });
    await userEvent.clear(matchCountInput);
    await userEvent.type(matchCountInput, "8");
    expect(onMatchCountChange).toHaveBeenCalled();
  });
});
