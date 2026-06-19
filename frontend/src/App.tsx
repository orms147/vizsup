import { useState } from "react";
import InputJob from "./pages/InputJob";
import Editor from "./pages/Editor";
import Render from "./pages/Render";

type Tab = "input" | "editor" | "render";

const TABS: { id: Tab; label: string }[] = [
  { id: "input", label: "1 · Input" },
  { id: "editor", label: "2 · Edit subtitles" },
  { id: "render", label: "3 · Render" },
];

export default function App() {
  const [tab, setTab] = useState<Tab>("input");

  return (
    <div className="min-h-screen">
      <header className="border-b border-neutral-800 px-6 py-3 flex items-center gap-6">
        <h1 className="font-semibold tracking-tight">vizsup</h1>
        <nav className="flex gap-1">
          {TABS.map((t) => (
            <button
              key={t.id}
              onClick={() => setTab(t.id)}
              className={`px-3 py-1.5 rounded text-sm ${
                tab === t.id ? "bg-neutral-800 text-white" : "text-neutral-400 hover:text-neutral-200"
              }`}
            >
              {t.label}
            </button>
          ))}
        </nav>
        <span className="ml-auto text-xs text-neutral-500">CN → VI subtitles + dubbing</span>
      </header>

      <main className="p-6 max-w-6xl mx-auto">
        {tab === "input" && <InputJob />}
        {tab === "editor" && <Editor />}
        {tab === "render" && <Render />}
      </main>
    </div>
  );
}
