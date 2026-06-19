// Per-line editable table: index | start | end | Vietnamese (edit) | Chinese (ref). P4.
export default function SubtitleTable() {
  return (
    <div className="w-full border border-neutral-800 rounded overflow-hidden">
      <div className="grid grid-cols-[3rem_5rem_5rem_1fr_1fr] bg-neutral-900 text-xs text-neutral-400 px-3 py-2">
        <span>#</span>
        <span>start</span>
        <span>end</span>
        <span>Tiếng Việt (sửa)</span>
        <span>中文 (gốc)</span>
      </div>
      <div className="px-3 py-6 text-center text-neutral-600 text-sm">
        editable subtitle rows — P4
      </div>
    </div>
  );
}
