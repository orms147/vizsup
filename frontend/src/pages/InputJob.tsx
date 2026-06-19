import { useEffect, useState } from "react";
import { fetchProviders, type Providers } from "../api";
import ProviderSelect from "../components/ProviderSelect";

// Screen 1: paste a URL, pick providers per stage, start the job.
// Functional enough to prove the /api/providers contract; full job wiring is P3.
export default function InputJob() {
  const [url, setUrl] = useState("");
  const [providers, setProviders] = useState<Providers | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    fetchProviders().then(setProviders).catch((e) => setError(String(e)));
  }, []);

  return (
    <div className="space-y-6">
      <div>
        <label className="block text-sm text-neutral-400 mb-1">Video URL (Douyin / Bilibili)</label>
        <input
          value={url}
          onChange={(e) => setUrl(e.target.value)}
          placeholder="https://www.bilibili.com/video/BV..."
          className="w-full rounded bg-neutral-900 border border-neutral-700 px-3 py-2 outline-none focus:border-neutral-500"
        />
      </div>

      {error && (
        <p className="text-sm text-amber-400">
          {error} — is the backend running? <code>uvicorn app.main:app --reload</code>
        </p>
      )}

      {providers && (
        <div className="grid grid-cols-2 gap-4">
          <ProviderSelect label="ASR (no-hardsub path)" options={providers.asr} />
          <ProviderSelect label="Translation — draft" options={providers.translation} />
          <ProviderSelect label="Translation — refine (optional)" options={providers.translation} optional />
          <ProviderSelect label="TTS voice" options={providers.tts} />
        </div>
      )}

      <button
        disabled={!url}
        className="rounded bg-emerald-600 hover:bg-emerald-500 disabled:opacity-40 px-4 py-2 text-sm font-medium"
        title="Job orchestration is P3; use the CLI for now: python -m app.cli run <url>"
      >
        Start →
      </button>
      <p className="text-xs text-neutral-500">
        Job orchestration is not wired yet (P3). For now run the pipeline headless:{" "}
        <code>python -m app.cli run &lt;url&gt;</code>.
      </p>
    </div>
  );
}
