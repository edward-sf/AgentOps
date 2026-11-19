import createClient from "openapi-fetch";

import type { paths } from "./schema";

export const api = createClient<paths>({
  baseUrl: process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000",
});

export type Overview = {
  database: "ok" | "error";
  agents: string[];
  datasets: string[];
};

/** Fetch health plus agent and dataset names. Throws if the API is unreachable or errors. */
export async function fetchOverview(): Promise<Overview> {
  const [health, agents, datasets] = await Promise.all([
    api.GET("/health"),
    api.GET("/api/v1/agents", { params: { query: { limit: 50 } } }),
    api.GET("/api/v1/datasets", { params: { query: { limit: 50 } } }),
  ]);
  if (agents.error || !agents.data) throw new Error("Failed to load agents");
  if (datasets.error || !datasets.data) throw new Error("Failed to load datasets");
  // /health answers 503 with a JSON body when the database is down, so read either branch.
  const body = (health.data ?? health.error) as { database?: string } | undefined;
  if (!body) throw new Error("Failed to load health");
  return {
    database: body.database === "ok" ? "ok" : "error",
    agents: agents.data.items.map((a) => a.name),
    datasets: datasets.data.items.map((d) => d.name),
  };
}
