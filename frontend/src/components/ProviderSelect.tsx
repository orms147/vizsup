import type { ProviderInfo } from "../api";

// A dropdown for one pluggable stage. Unavailable engines are disabled.
export default function ProviderSelect({
  label,
  options,
  optional,
}: {
  label: string;
  options: ProviderInfo[];
  optional?: boolean;
}) {
  return (
    <div>
      <label className="block text-sm text-neutral-400 mb-1">{label}</label>
      <select className="w-full rounded bg-neutral-900 border border-neutral-700 px-3 py-2 text-sm">
        {optional && <option value="">— none —</option>}
        {options.map((o) => (
          <option key={o.name} value={o.name} disabled={!o.available}>
            {o.display_name} · {o.cost_note}
            {o.available ? "" : "  (set key)"}
          </option>
        ))}
      </select>
    </div>
  );
}
