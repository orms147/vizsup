import type { ProviderInfo } from "../api";

// Live job progress over WebSocket (download → detect → subtitle → translate). P3.
// Placeholder stepper; wire to /ws/jobs/{id} in P3.
const STAGES = ["download", "detect", "subtitle", "translate"];

export default function JobProgress({ active }: { active?: string }) {
  return (
    <ol className="flex gap-2 text-xs">
      {STAGES.map((s) => (
        <li
          key={s}
          className={`px-2 py-1 rounded ${
            s === active ? "bg-emerald-700 text-white" : "bg-neutral-800 text-neutral-400"
          }`}
        >
          {s}
        </li>
      ))}
    </ol>
  );
}

// Touch the type import so this scaffold stays in sync with api.ts.
export type _Unused = ProviderInfo;
