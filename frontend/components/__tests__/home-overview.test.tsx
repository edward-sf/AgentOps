import { render, screen } from "@testing-library/react";
import { expect, test } from "vitest";

import { HomeOverview } from "../home-overview";

test("renders database status and names", async () => {
  render(
    <HomeOverview
      load={async () => ({
        database: "ok",
        agents: ["weather-bot"],
        datasets: ["weather-basics"],
      })}
    />,
  );
  expect(await screen.findByText("Database: ok")).toBeInTheDocument();
  expect(screen.getByText("weather-bot")).toBeInTheDocument();
  expect(screen.getByText("weather-basics")).toBeInTheDocument();
});

test("renders empty states", async () => {
  render(<HomeOverview load={async () => ({ database: "ok", agents: [], datasets: [] })} />);
  expect(await screen.findByText("No agents yet")).toBeInTheDocument();
  expect(screen.getByText("No datasets yet")).toBeInTheDocument();
});

test("renders error when api unreachable", async () => {
  render(<HomeOverview load={() => Promise.reject(new Error("down"))} />);
  expect(await screen.findByText("API unreachable")).toBeInTheDocument();
});
