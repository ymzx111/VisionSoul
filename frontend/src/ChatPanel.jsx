import { useEffect, useRef } from 'react';

export default function ChatPanel({ workflow, state, draft, setDraft, imageState, configured }) {
  const scroll = useRef(null);
  const multiple = imageState.files.length > 1;
  const pendingImage = imageState.files.length === 1 ? imageState.upload?.image_id : null;
  const imageReady = imageState.files.length === 0 || imageState.uploadStage === 'ready';
  const changedImage = state.imageId && pendingImage && state.imageId !== pendingImage;
  const canSend = configured && !state.sending && !state.loading && imageReady && !multiple && draft.trim();
  useEffect(() => { if (scroll.current) scroll.current.scrollTop = scroll.current.scrollHeight; }, [state.messages, state.pending]);
  const submit = async () => {
    const sent = await workflow.send(draft, pendingImage || null, { imageCount: imageState.files.length, uploadReady: imageReady });
    if (sent) setDraft(current => current === draft ? '' : current);
  };
  const messages = state.pending ? [...state.messages, { ...state.pending, id: 'pending' }] : state.messages;
  return <section className="panel chat-panel" aria-labelledby="chat-heading">
    <div className="panel-heading"><div className="section-heading"><span className="step">02</span><h2 id="chat-heading">和{state.persona.name}聊聊</h2></div>
      <button className="text-button" disabled={state.sending || state.loading} onClick={() => { if (workflow.newConversation()) setDraft(''); }}>新话题 ＋</button></div>
    <div className="chat-topic-bar"><label className="sr-only" htmlFor="chat-history">切换近期话题</label><select id="chat-history" value={state.conversationId || ''} disabled={state.sending || state.loading}
      onChange={event => { if (event.target.value) { setDraft(''); void workflow.loadConversation(event.target.value); } else workflow.newConversation(); }}>
      <option value="">新话题 · 从这里开始</option>{state.conversationId && !state.recent.some(item => item.id === state.conversationId) && <option value={state.conversationId}>当前话题</option>}{state.recent.map(item => <option key={item.id} value={item.id}>{item.title}</option>)}
    </select><span>{state.persona.style}</span></div>
    <div className="chat-stream" ref={scroll} role="log" aria-label="对话记录" aria-live="polite" aria-busy={state.sending || state.loading}>
      {state.loading ? <p className="chat-loading">正在读取本机话题…</p> : messages.length ? <>{state.hasOlder && <p className="chat-history-note">这里只显示最近 200 条，完整记录保存在本机。</p>}{messages.map(item => <article key={item.id} className={`chat-message ${item.role === 'user' ? 'from-user' : 'from-companion'}`}>
        <span className="chat-author">{item.role === 'user' ? '你' : state.persona.name}{item.id === 'pending' ? ' · 正在发送' : ''}</span>
        {item.image_id && <a href={`/api/images/${item.image_id}`} target="_blank" rel="noreferrer" className="chat-image-link"><img src={`/api/images/${item.image_id}`} alt="这一轮分享的图片"/></a>}
        <p data-testid={item.role === 'assistant' ? 'companion-message' : undefined}>{item.content}</p>
      </article>)}{state.sending && <div className="companion-thinking"><span className="thinking-dot"/><span className="thinking-dot"/><span className="thinking-dot"/>{state.persona.name}正在接话…</div>}</>
        : <div className="chat-welcome"><span className="welcome-mark">灵</span><h3>嗨，我是{state.persona.name}。</h3><p>今天有什么新鲜事？<br/>一张照片，或一句随口的想法，都可以从这里开始。</p><span>按你设定的人物，接着聊下去。</span></div>}
    </div>
    <div className="chat-composer">
      {multiple && <div className="notice notice-warning" role="alert">目前只能分析单个图像。请仅保留一张，或清空图片后继续文字对话。</div>}
      {state.persona.configured === false && <div className="notice notice-warning" role="status">模型尚未配置，请先完成后端密钥设置。</div>}
      {changedImage && <p className="chat-image-note">已换图：发送下一条消息时，将为这张图片开启新话题。</p>}
      {!imageReady && !multiple && <p className="chat-image-note">请等待图片上传成功；上传失败时，可重新选择或清空图片后聊文字。</p>}
      {state.error && <div className="notice notice-error" role="alert">{state.error}</div>}
      {state.truncated && <p className="chat-image-note">上一条回复达到长度上限，可继续追问。</p>}
      <label className="sr-only" htmlFor="chat-message">发送给角色的消息</label><textarea id="chat-message" maxLength={2000} value={draft} disabled={state.sending || state.loading}
        onChange={event => setDraft(event.target.value)} placeholder="说点什么，也可以结合左侧图片追问…"/>
      <div className="question-actions"><span className="cost-note">发送才调用模型{state.usage ? ` · ${state.replayed ? '恢复上次回复' : '本次 ' + state.usage.total_tokens + ' token'}` : ''}</span>
        <button className="button button-primary" disabled={!canSend} onClick={() => void submit()}>{state.sending ? '正在回复…' : '发送消息 →'}</button></div>
    </div>
    <div className="analysis-footer"><span className="subtle-dot"/><span>参考最近 {state.persona.context_turns} 轮 · 本机保存</span><span>{state.imageId ? '本话题含单张图片' : '图片可选，也能直接聊文字'}</span></div>
  </section>;
}
