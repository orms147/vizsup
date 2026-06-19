// Screen 3: subtitle style + voice + audio-mix options, render, then download. P4.
export default function Render() {
  return (
    <div className="space-y-4 max-w-xl">
      <h2 className="text-sm text-neutral-400">Render options</h2>
      <div className="space-y-3 text-sm text-neutral-500">
        <div>Subtitle style: font / size / opaque box to cover CN hardsubs — P4</div>
        <div>TTS voice (from chosen engine) — P4</div>
        <div>Audio mix: replace / mix / duck original — P4</div>
      </div>
      <button className="rounded bg-emerald-600 hover:bg-emerald-500 px-4 py-2 text-sm font-medium">
        Render
      </button>
      <p className="text-xs text-neutral-500">
        On completion: preview + download output.mp4 / vi.srt / subs.ass.
      </p>
    </div>
  );
}
