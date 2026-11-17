import { render, screen } from "@testing-library/react";
import { expect, test } from "vitest";

import Page from "../page";

test("renders the AgentOps heading", () => {
  render(<Page />);
  expect(screen.getByRole("heading", { name: "AgentOps" })).toBeInTheDocument();
});
