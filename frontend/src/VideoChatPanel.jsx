import { useEffect, useRef } from 'react';

export default function VideoChatPanel({ workflow, state, videoState, draft, setDraft }) {
  const scroll = useRef(null);
  const selected = videoState.video?.video_id || null;
  const ready = !videoState.uploading && !videoState.loadingVideo && (!videoState.filename || !!selected);
  const busy = state.sending || state.loading;
  const changed = state.videoId && selected && state.videoId !== selected;
  const canSend = state.persona.configured && !busy && ready && (selected || state.videoId) && draft.trim();
  const messages = state.pending ? [...state.messages, { ...state.pending, id: 'pending' }] : state.messages;
  useEffect(() => { if (scroll.current) scroll.current.scrollTop = scroll.current.scrollHeight; }, [state.messages, state.pending]);
  const send = async (inspectVideo = false) => {
    const sent = await workflow.send(draft, null, { videoId: selected, uploadReady: ready, inspectVideo });
    if (sent) setDraft(current => current === draft ? '' : current);
  };
  return <section className="panel chat-panel video-chat-panel" aria-labelledby="video-chat-heading">
    <div className="panel-heading"><div className="section-heading"><span className="step">02</span><h2 id="video-chat-heading">和{state.persona.name}聊这段视频</h2></div>
      <button className="text-button" disabled={busy || videoState.uploading || videoState.loadingVideo} onClick={() => { if (workflow.newConversation()) setDraft(''); }}>新话题 ＋</button></div>
    <div className="chat-topic-bar"><label className="sr-only" htmlFor="video-history">切换视频话题</label>
      <select id="video-history" value={state.conversationId || ''} disabled={busy || videoState.uploading || videoState.loadingVideo}
        onChange={event => { setDraft(''); if (event.target.value) void workflow.loadConversation(event.target.value); else workflow.newConversation(); }}>
        <option value="">新话题 · 分享这段视频</option>
        {state.conversationId && !state.recent.some(item => item.id === state.conversationId) && <option value={state.conversationId}>当前视频话题</option>}
        {state.recent.map(item => <option key={item.id} value={item.id}>{item.title}</option>)}
      </select><span>{state.persona.style || '自定义人物'}</span></div>
    <div className="chat-stream" ref={scroll} role="log" aria-label="视频对话记录" aria-live="polite" aria-busy={busy}>
      {state.loading ? <p className="chat-loading">正在恢复本机视频话题…</p> : messages.length ? <>
        {state.hasOlder && <p className="chat-history-note">这里只显示最近 200 条，完整记录仍保存在本机。</p>}
        {messages.map(item => <article key={item.id} className={`chat-message ${item.role === 'user' ? 'from-user' : 'from-companion'}`}>
          <span className="chat-author">{item.role === 'user' ? '你' : state.persona.name}{item.id === 'pending' ? ' · 正在发送' : ''}</span>
          {item.video_id && <span className="shared-video-chip">▷ 已分享这段视频</span>}
          <p data-testid={item.role === 'assistant' ? 'video-companion-message' : undefined}>{item.content}</p>
        </article>)}
        {state.sending && <div className="companion-thinking"><span className="thinking-dot"/><span className="thinking-dot"/><span className="thinking-dot"/>{state.persona.name}正在看、正在接话…</div>}
      </> : <div className="chat-welcome"><span className="welcome-mark">灵</span><h3>画面之外，也听你说。</h3><p>选一个视频，再配上你想说的话。<br/>比如：今天去了西湖，看看这湖水怎么样？</p><span>接着追问，我会参考这个话题的上文。</span></div>}
    </div>
    <div className="chat-composer">
      {changed && <p className="chat-image-note">已换视频：发送时会开启新话题，原对话仍保留。</p>}
      {!ready && <p className="chat-image-note">请等待视频就绪；上传失败时请重新选择，或清空后继续原话题。</p>}
      {state.error && <div className="notice notice-error" role="alert">{state.error}</div>}
      {state.persona.configured === false && <div className="notice notice-warning">模型尚未配置。</div>}
      {state.truncated && <p className="chat-image-note">上一条回复达到长度上限，可以继续追问。</p>}
      <label className="sr-only" htmlFor="video-message">对角色说的话</label>
      <textarea id="video-message" maxLength={2000} disabled={busy} value={draft} onChange={event => setDraft(event.target.value)}
        placeholder={state.videoId ? '接着聊这段视频，也可以问问刚才说过的事…' : '嘿，我今天去了西湖玩，你看看这湖水怎么样…'}/>
      <div className="question-actions"><span className="cost-note">发送才调用模型{state.usage ? state.replayed ? ' · 已恢复原回复，未重复调用' : ` · 本次 ${state.usage.total_tokens} token` : ''}</span>
        <button className="button button-secondary" disabled={!canSend} title="根据清晰度、曝光和画面差异，自动挑选最多4张高清图；本地选图不调用AI，分析仍可能产生模型用量。" onClick={() => void send(true)}>细看并比对</button>
        <button className="button button-primary" disabled={!canSend} onClick={() => void send()}>{state.sending ? '正在回复…' : state.videoId && !changed ? '发送消息 →' : '分享并对话 →'}</button></div>
    </div>
    <div className="analysis-footer video-limit-note"><span className="subtle-dot"/><span>保存首次分享＋最近 {state.persona.context_turns} 轮 · 本机记录可恢复</span></div>
  </section>;
}
