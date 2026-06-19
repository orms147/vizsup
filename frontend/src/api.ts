// Thin API client. The Vite dev server proxies /api to the FastAPI backend.

export interface ProviderInfo {
  name: string;
  display_name: string;
  cost_note: string;
  local: boolean;
  available: boolean;
}

export interface Providers {
  translation: ProviderInfo[];
  tts: ProviderInfo[];
  asr: ProviderInfo[];
}

export async function fetchProviders(): Promise<Providers> {
  const res = await fetch("/api/providers");
  if (!res.ok) throw new Error(`GET /api/providers failed: ${res.status}`);
  return res.json();
}

export interface Cue {
  index: number;
  start: number;
  end: number;
  text: string;
}
