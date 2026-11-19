"use client";

import { useEffect, useState } from "react";

import { fetchOverview, type Overview } from "@/lib/api/client";

type State =
  | { status: "loading" }
  | { status: "error" }
  | { status: "ready"; overview: Overview };

function NameList({ title, names, empty }: { title: string; names: string[]; empty: string }) {
  return (
    <section>
      <h2 className="text-lg font-medium">{title}</h2>
      {names.length === 0 ? (
        <p className="text-muted-foreground">{empty}</p>
      ) : (
        <ul className="list-disc pl-5">
          {names.map((name) => (
            <li key={name}>{name}</li>
          ))}
        </ul>
      )}
    </section>
  );
}

export function HomeOverview({ load = fetchOverview }: { load?: () => Promise<Overview> }) {
  const [state, setState] = useState<State>({ status: "loading" });

  useEffect(() => {
    let cancelled = false;
    load().then(
      (overview) => !cancelled && setState({ status: "ready", overview }),
      () => !cancelled && setState({ status: "error" }),
    );
    return () => {
      cancelled = true;
    };
  }, [load]);

  if (state.status === "loading") return <p>Loading…</p>;
  if (state.status === "error") return <p role="alert">API unreachable</p>;
  const { database, agents, datasets } = state.overview;
  return (
    <div className="flex flex-col gap-4">
      <p>Database: {database}</p>
      <NameList title="Agents" names={agents} empty="No agents yet" />
      <NameList title="Datasets" names={datasets} empty="No datasets yet" />
    </div>
  );
}
