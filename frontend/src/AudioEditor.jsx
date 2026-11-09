import { useEffect, useMemo, useRef, useSyncExternalStore } from 'react';
import { createAudioEditWorkflow } from './audio-edit-workflow.mjs';

export default function AudioEditor({ videoId, duration, disabled, onSaved, onBusyChange }) {
  const saved = useRef(onSaved);
  saved.current = onSaved;
  const workflow = useMemo(() => createAudioEditWorkflow({ videoId, duration, onSaved: record => saved.current(record) }), [videoId, duration]);
  const state = useSyncExternalStore(workflow.subscribe, workflow.getState);
  useEffect(() => () => workflow.dispose(), [workflow]);
  const working = state.loading || state.saving;
  useEffect(() => { onBusyChange(working); return () => onBusyChange(false); }, [working, onBusyChange]);
  return <>
    <button className="text-button" disabled={disabled || working} onClick={() => void workflow.open()}>校对白</button>
    {state.open && <div className="audio-editor-backdrop">
      <section className="audio-editor-dialog" role="dialog" aria-modal="true" aria-labelledby="audio-editor-title">
        <h2 id="audio-editor-title">校对视频对白</h2>
        <p>修改错字或起止秒数。原始转写保留；保存不调用 API，也不会自动重算旧分析。</p>
        {state.loading ? <p role="status">正在读取对白…</p> : <div className="audio-editor-segments">{state.segments.map(segment => {
          const original = state.record.original.segments[segment.id];
          return <fieldset key={segment.id}><legend>第 {segment.id + 1} 段</legend>
            <p className="chat-history-note">原始 {original.start}—{original.end}秒：{original.text}</p>
            <label>对白 {segment.id + 1}<textarea aria-label={`第${segment.id + 1}段对白`} maxLength={300} value={segment.text} disabled={working}
              onChange={event => workflow.edit(segment.id, { text: event.target.value })}/></label>
            <div className="audio-editor-times"><label>开始秒数<input aria-label={`第${segment.id + 1}段开始秒数`} type="number" min="0" max={duration} step="0.01" value={Number.isFinite(segment.start) ? segment.start : ''} disabled={working}
              onChange={event => workflow.edit(segment.id, { start: event.target.valueAsNumber })}/></label>
              <label>结束秒数<input aria-label={`第${segment.id + 1}段结束秒数`} type="number" min="0" max={duration} step="0.01" value={Number.isFinite(segment.end) ? segment.end : ''} disabled={working}
              onChange={event => workflow.edit(segment.id, { end: event.target.valueAsNumber })}/></label></div>
          </fieldset>;
        })}</div>}
        {state.error && <p className="notice notice-error" role="alert">{state.error}</p>}
        <div className="question-actions"><button className="button button-secondary" disabled={working} onClick={() => workflow.close()}>取消</button>
          <button className="button button-primary" disabled={working || !state.record} onClick={() => void workflow.save()}>{state.saving ? '正在保存…' : '保存校对 · 不调用 API'}</button></div>
      </section>
    </div>}
  </>;
}
