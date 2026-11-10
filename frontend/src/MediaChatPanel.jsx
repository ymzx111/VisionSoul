import { useEffect, useRef } from 'react';

export default function MediaChatPanel({ workflow, state, draft, setDraft }) {
  const scroll = useRef(null);
  const chat = state.chat;
  const busy = workflow.busy();
  const ready = !state.kind || (state.kind === 'image' ? state.image.upload?.image_id || state.restoredImageId : state.video.video?.video_id);
  const canSend = chat.persona.configured && !busy && !state.error && ready && (draft.trim() || (state.kind && !chat.conversationId));
  const messages = chat.pending ? [...chat.messages, { ...chat.pending, id: 'pending' }] : chat.messages;
  useEffect(() => { if (scroll.current) scroll.current.scrollTop = scroll.current.scrollHeight; }, [chat.messages, chat.pending]);
  const send = async options => {
    const sent = await workflow.send(draft, options);
    if (sent) setDraft(value => value === draft ? '' : value);
  };
  return <section className="panel chat-panel video-chat-panel" aria-labelledby="media-chat-heading">
    <div className="panel-heading"><div className="section-heading"><span className="step">02</span><h2 id="media-chat-heading">分析与对话</h2></div>
      <button className="text-button" disabled={busy} onClick={() => { if (workflow.clear()) setDraft(''); }}>新话题 ＋</button></div>
    <div className="chat-topic-bar"><label className="sr-only" htmlFor="media-history">切换历史话题</label>
      <select id="media-history" value={chat.conversationId || ''} disabled={busy} onChange={event => {
        setDraft(''); if (event.target.value) void workflow.loadConversation(event.target.value); else workflow.clear();
      }}><option value="">新话题 · 图片、视频或文字</option>
        {chat.conversationId && !chat.recent.some(row => row.id === chat.conversationId) && <option value={chat.conversationId}>当前话题</option>}
        {chat.recent.map(row => <option key={row.id} value={row.id}>{row.video_id ? '视频' : row.image_id ? '图片' : '文字'} · {row.title}</option>)}
      </select><span>{state.kind === 'video' ? '视频' : state.kind === 'image' ? '单张图片' : '也可以直接聊文字'}</span></div>
    <div className="chat-stream" ref={scroll} role="log" aria-label="分析与对话记录" aria-live="polite" aria-busy={chat.sending || chat.loading}>
      {chat.loading ? <p className="chat-loading">正在恢复本机话题…</p> : messages.length ? <>
        {chat.hasOlder && <p className="chat-history-note">显示最近200条，完整记录保存在本机。</p>}
        {messages.map(item => <article key={item.id} className={`chat-message ${item.role === 'user' ? 'from-user' : 'from-companion'}`}>
          <span className="chat-author">{item.role === 'user' ? '你' : chat.persona.name}{item.id === 'pending' ? ' · 正在发送' : ''}</span>
          {item.video_id && <span className="shared-video-chip">▷ 已分享视频</span>}
          {item.image_id && <a href={`/api/images/${item.image_id}`} target="_blank" rel="noreferrer" className="chat-image-link"><img src={`/api/images/${item.image_id}`} width="160" height="110" alt="这一轮分享的图片"/></a>}
          <p data-testid={item.role === 'assistant' ? 'media-analysis-message' : undefined}>{item.content}</p>
        </article>)}
        {chat.sending && <div className="companion-thinking"><span className="thinking-dot"/><span className="thinking-dot"/><span className="thinking-dot"/>正在分析与回复…</div>}
      </> : <div className="chat-welcome"><span className="welcome-mark">▷</span><h3>一张图片，或一段视频。</h3>
        <p>上传后点击“开始分析”，也可以配上你想问的问题。<br/>视频里的代码和报错，可使用“代码排错”。</p><span>换素材会开启新话题，旧记录仍可恢复。</span></div>}
    </div>
    <div className="chat-composer">
      {chat.persona.configured === false && <p className="notice notice-warning">模型尚未配置。</p>}
      {chat.error && <div className="notice notice-error" role="alert">{chat.error}</div>}
      {chat.truncated && <p className="chat-image-note">上一条回复达到长度上限，可以继续追问。</p>}
      <label className="sr-only" htmlFor="media-message">问题或补充说明</label>
      <textarea id="media-message" maxLength={2000} disabled={busy} value={draft} onChange={event => setDraft(event.target.value)}
        placeholder={chat.conversationId ? '继续追问，或补充你的说明…' : '可选：你想了解什么？不填写也可以开始分析。'}/>
      <div className="question-actions"><span className="cost-note">点击后才调用模型{chat.usage ? chat.replayed ? ' · 已恢复原回复' : ` · 本次${chat.usage.total_tokens} token` : ''}</span>
        {state.kind === 'video' && <>
          <button className="button button-secondary" disabled={!canSend} onClick={() => void send({ inspectVideo: true })}>细看并比对</button>
          <button className="button button-secondary" disabled={!canSend} onClick={() => void send({ debugCode: true })}>代码排错</button>
        </>}
        <button className="button button-primary" disabled={!canSend} onClick={() => void send()}>{chat.sending ? '正在回复…' : chat.conversationId || !state.kind ? '发送消息 →' : '开始分析 →'}</button>
      </div>
    </div>
    <div className="analysis-footer"><span className="subtle-dot"/><span>本机保存话题 · 可继续追问和恢复</span></div>
  </section>;
}
