import { useEffect, useRef, useState } from 'react';

const fields = [
  ['identity','身份定位',120], ['traits','核心性格',200], ['background','背景设定',240],
  ['speaking_style','说话方式',200], ['interests','兴趣与审美',160], ['relationship','相处方式',160], ['avoidances','减少的表达',200],
];
const categoryName = { preference: '偏好', fact: '用户信息', summary: '话题摘要' };

function MemoryItem({ memory, workflow, busy }) {
  const [editing, setEditing] = useState(false);
  const [text, setText] = useState(memory.content);
  useEffect(() => setText(memory.content), [memory.content]);
  return <article className="memory-card">
    <div className="memory-card-title"><strong>{categoryName[memory.category]}</strong><span className={`memory-badge ${memory.status}`}>{memory.status === 'confirmed' ? '正在使用' : '待确认'}</span></div>
    {editing ? <textarea aria-label="编辑记忆内容" value={text} maxLength={memory.category === 'summary' ? 600 : 240} disabled={busy} onChange={event => setText(event.target.value)}/> : <p className="memory-content">{memory.content}</p>}
    <details className="memory-source"><summary>{memory.source_title ? `来源：${memory.source_title}` : '来源：手动填写'}</summary>
      {memory.source_quote && <blockquote>{memory.source_quote}</blockquote>}
      {memory.category === 'summary' && <p>根据选定话题的有限聊天窗口整理；继续交流后可再次整理。</p>}
      {memory.edited === 1 && <p>此条经过手动修订，原话用于追溯。</p>}
      <span>{new Date(memory.updated_at).toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai' })}</span>
    </details>
    <div className="memory-buttons">{editing ? <>
      <button className="button button-secondary" disabled={busy || !text.trim()} onClick={async () => { if (await workflow.changeMemory(memory, { content: text.trim() })) setEditing(false); }}>保存修改</button>
      <button className="text-button" disabled={busy} onClick={() => { setText(memory.content); setEditing(false); }}>取消</button>
    </> : <>
      <button className="button button-secondary" disabled={busy} onClick={() => void workflow.changeMemory(memory, { status: memory.status === 'confirmed' ? 'pending' : 'confirmed' })}>{memory.status === 'confirmed' ? '暂停使用' : '确认使用'}</button>
      <button className="text-button" disabled={busy} onClick={() => setEditing(true)}>编辑</button>
      <button className="text-button" disabled={busy} onClick={() => void workflow.changeMemory(memory, {}, true)}>移除</button>
    </>}</div>
  </article>;
}

export default function RoleSettings({ workflow, state }) {
  const [tab, setTab] = useState('profile');
  const [discussion, setDiscussion] = useState('');
  const [previewMessage, setPreviewMessage] = useState('今天终于把一个小功能做出来了，花了好久。');
  const [source, setSource] = useState('');
  const [manualCategory, setManualCategory] = useState('preference');
  const [manualText, setManualText] = useState('');
  const [filter, setFilter] = useState('all');
  const stream = useRef(null);
  useEffect(() => { void workflow.initialize(); }, [workflow]);
  useEffect(() => { if (source && !state.sources.some(item => item.id === source)) setSource(''); }, [state.sources, source]);
  useEffect(() => { if (stream.current) stream.current.scrollTop = stream.current.scrollHeight; }, [state.card?.messages]);
  const busy = state.busy || state.loading;
  const canModel = state.configured && !busy;
  const visible = state.memories.filter(item => filter === 'all' || item.status === filter);
  const sendDiscussion = async () => { if (await workflow.discuss(discussion)) setDiscussion(''); };
  return <main className="role-workspace">
    <div className="role-page-title"><div><p className="eyebrow">角色与记忆</p><h1>慢慢认识小灵，也让她记住重要的事。</h1></div>
      <button className="button button-secondary" disabled={busy} onClick={() => void workflow.reload()}>重新读取</button></div>
    <div className="role-layout"><aside className="role-sidebar"><div className="role-portrait">灵</div><h2>小灵</h2><span className="role-active">当前唯一启用的角色</span>
      <nav aria-label="角色设置栏目"><button aria-pressed={tab === 'profile'} onClick={() => setTab('profile')}>人格卡</button><button aria-pressed={tab === 'memory'} onClick={() => setTab('memory')}>记忆卡 <span>{state.memories.filter(item => item.status === 'confirmed').length}</span></button></nav>
      <p>先把小灵打磨好。其他角色保留给后续扩展，当前项目不切换角色。</p></aside>
      <div className="role-content">
        {(state.error || state.notice) && <div className={`notice ${state.error ? 'notice-error' : 'role-notice'}`} role={state.error ? 'alert' : 'status'}>{state.error || state.notice}</div>}
        {state.usage && <p className="role-usage">{state.cached ? '恢复已有结果，本次未重复调用模型。' : `本次用量 ${state.usage.total_tokens} token`}</p>}
        {!state.configured && !state.loading && <p className="chat-history-note">模型尚未配置，仍可手动编辑和保存；设定对话、预览与话题整理需要模型。</p>}
        {state.loading ? <div className="role-loading">正在读取本机角色资料…</div> : state.form && tab === 'profile' ? <div className="role-profile-grid">
          <section className="panel role-discussion"><div className="panel-heading"><h2>聊聊她是什么样的人</h2><span className="soft-label">只整理草稿</span></div>
            <div className="role-discussion-stream" role="log" aria-label="角色设定讨论" ref={stream} aria-live="polite">
              {state.card.messages.length ? state.card.messages.map((item,index) => <article key={index} className={`chat-message ${item.role === 'user' ? 'from-user' : 'from-companion'}`}><span className="chat-author">{item.role === 'user' ? '你' : '设定助手'}</span><p>{item.content}</p></article>)
                : <div className="role-discussion-empty"><h3>从几个具体的习惯开始。</h3><p>她喜欢留意什么？怎样表达不同意见？遇到严肃的话题，会怎么回应？</p><p>你说想法，右侧逐步整理成草稿。保存并生效后才会影响正式对话。</p></div>}
            </div>
            <div className="chat-composer"><label className="question-label" htmlFor="role-discussion">你希望小灵怎样回应你？</label>
              <textarea id="role-discussion" value={discussion} disabled={busy} maxLength={1500} onChange={event => setDiscussion(event.target.value)} placeholder="比如：更有自己的主见，喜欢自然；可以轻轻吐槽，但别总是夸我…"/>
              <div className="question-actions"><span className="cost-note">点击讨论才调用模型</span><button className="button button-primary" disabled={!canModel || !discussion.trim()} onClick={() => void sendDiscussion()}>{state.busy ? '正在处理…' : '讨论并整理草稿'}</button></div></div>
            <div className="role-preview"><h3>试试她会怎么说</h3><label className="sr-only" htmlFor="role-preview">角色预览消息</label><textarea id="role-preview" value={previewMessage} maxLength={1000} disabled={busy} onChange={event => setPreviewMessage(event.target.value)}/>
              <div className="question-actions"><span className="cost-note">使用当前草稿，不写入正式记忆</span><button className="button button-secondary" disabled={!canModel || !previewMessage.trim()} onClick={() => void workflow.preview(previewMessage)}>预览回应</button></div>
              {state.preview && <p className="role-preview-answer" data-testid="role-preview-answer">{state.preview}</p>}</div>
          </section>
          <section className="panel role-form-panel"><div className="panel-heading"><h2>小灵的人格卡</h2><span className="soft-label">{state.dirty ? '有未保存修改' : `使用版本 ${state.card.revision} · 草稿 ${state.card.draft_revision}`}</span></div>
            <div className="role-fields"><p className="role-form-note">以下为可编辑草稿。名字保留为小灵，性格、背景和表达方式可以一起打磨。</p>
              {fields.map(([key,label,max]) => <label className="role-field" key={key} htmlFor={`role-${key}`}><span>{label}</span><textarea id={`role-${key}`} maxLength={max} value={state.form[key]} disabled={busy} onChange={event => workflow.update(key,event.target.value)}/></label>)}
              <label className="role-field" htmlFor="role-version"><span>历史版本</span><select id="role-version" defaultValue="" disabled={busy} onChange={event => { if (event.target.value !== '') workflow.loadVersion(event.target.value); event.target.value=''; }}><option value="">载入一份历史设定到草稿…</option>{state.card.versions.map(item => <option key={item.revision} value={item.revision}>版本 {item.revision}</option>)}</select></label>
            </div>
            <div className="role-save-bar"><button className="button button-secondary" disabled={busy} onClick={() => void workflow.save(false)}>只保存草稿</button><button className="button button-primary" disabled={busy} onClick={() => void workflow.save(true)}>保存并生效</button></div>
          </section>
        </div> : state.card && tab === 'memory' ? <section className="role-memory-section">
          <div className="panel memory-tools"><div className="panel-heading"><h2>整理一段聊过的事</h2><span className="soft-label">先候选，后确认</span></div>
            <div className="memory-extract-row"><label className="sr-only" htmlFor="memory-source">选择整理的话题</label><select id="memory-source" value={source} disabled={busy} onChange={event => setSource(event.target.value)}><option value="">选择一段话题…</option>{state.sources.map(item => <option key={item.id} value={item.id}>{item.title} · {item.message_count} 条</option>)}</select><button className="button button-primary" disabled={!canModel || !source} onClick={() => void workflow.extract(source)}>整理话题记忆</button></div>
            <p className="role-form-note">仅在点击时，把这段话题的有限聊天内容交给模型。摘要和候选记忆需要确认后才用于对话；同一段未更新的内容会复用结果。</p>
            <div className="memory-manual"><label className="sr-only" htmlFor="memory-category">手动记忆类型</label><select id="memory-category" value={manualCategory} disabled={busy} onChange={event => setManualCategory(event.target.value)}><option value="preference">偏好</option><option value="fact">用户信息</option></select>
              <label className="sr-only" htmlFor="memory-content">手动记忆内容</label><input id="memory-content" value={manualText} maxLength={240} disabled={busy} onChange={event => setManualText(event.target.value)} placeholder="手动写一条你明确希望她记住的事…"/>
              <button className="button button-secondary" disabled={busy || !manualText.trim()} onClick={async () => { if (await workflow.addMemory(manualCategory,manualText)) setManualText(''); }}>添加记忆</button></div>
          </div>
          <div className="memory-list-heading"><h2>小灵的记忆卡</h2><label className="sr-only" htmlFor="memory-filter">筛选记忆状态</label><select id="memory-filter" value={filter} onChange={event => setFilter(event.target.value)}><option value="all">全部条目</option><option value="pending">待确认</option><option value="confirmed">正在使用</option></select></div>
          <p className="role-form-note">记忆卡和原始聊天分别保存。暂停或移除卡片后，原聊天记录仍在；最近对话中提过的内容也可能仍被参考。</p>
          <div className="memory-list">{visible.length ? visible.map(memory => <MemoryItem key={memory.id} memory={memory} workflow={workflow} busy={busy}/>) : <div className="memory-empty">这里还没有对应的记忆。可以先手动添加，或选择一段话题整理。</div>}</div>
        </section> : null}
      </div>
    </div>
  </main>;
}
