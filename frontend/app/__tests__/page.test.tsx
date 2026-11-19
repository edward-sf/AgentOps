import { render, screen } from "@testing-library/react";
import { expect, test, vi } from "vitest";

vi.mock("@/lib/api/client", () => ({
  fetchOverview: async () => ({ database: "ok", agents: [], datasets: [] }),
}));

import Page from "../page";

test("renders the AgentOps heading", async () => {
  render(<Page />);
  expect(screen.getByRole("heading", { name: "AgentOps" })).toBeInTheDocument();
  expect(await screen.findByText("Database: ok")).toBeInTheDocument();
});
