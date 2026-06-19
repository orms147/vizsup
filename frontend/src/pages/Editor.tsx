import SubtitleTimeline from "../components/SubtitleTimeline";
import SubtitleTable from "../components/SubtitleTable";

// Screen 2: THE EDIT GATE (P4 centerpiece). Video + waveform + draggable timeline
// + editable line table. "Approve & Render" gates everything downstream.
// Scaffolded structure; wire to /api/jobs/{id}/subtitles + wavesurfer.js +
// react-timeline-editor in P4. See docs/ui-design-prompt.md.
export default function Editor() {
  return (
    <div className="space-y-4">
      <div className="aspect-video w-full max-w-3xl bg-neutral-900 border border-neutral-800 rounded grid place-items-center text-neutral-600">
        video player (HTML5 &lt;video&gt;) — P4
      </div>
      <SubtitleTimeline />
      <SubtitleTable />
      <div className="flex justify-end">
        <button className="rounded bg-emerald-600 hover:bg-emerald-500 px-4 py-2 text-sm font-medium">
          Approve &amp; Render →
        </button>
      </div>
      <p className="text-xs text-neutral-500">
        Nothing downstream (TTS, render) runs until you approve.
      </p>
    </div>
  );
}
