import { useEffect, useState } from 'react';
import { roleText, ROLE_TEXT_LIMIT } from './role-import.mjs';
const categoryName = { preference: '偏好', fact: '用户信息', summary: '话题摘要' };

function MemoryItem({ memory, workflow, busy }) {
  const [editing, setEditing] = useState(false);
  const [text, setText] = useState(memory.content);
  useEffect(() => setText(memory.content), [memory.content]);
  return <article className="memory-card">
    <div className="memory-card-title"><strong>{categoryName[memory.category]}</strong><span className={`memory-badge ${memory.status}`}>{memory.source_enabled === false ? '来源未启用' : memory.status === 'confirmed' ? '已确认' : '待确认'}</span></div>
    {memory.source_enabled === false && <p className="role-form-note">原卡片已保留，当前不用于角色回复。来源为工作、未分类或已不存在的话题；旧话题需先归入角色对话。</p>}
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
  const [previewMessage, setPreviewMessage] = useState('今天终于把一个小功能做出来了，花了好久。');
  const [source, setSource] = useState('');
  const [manualCategory, setManualCategory] = useState('preference');
  const [manualText, setManualText] = useState('');
  const [filter, setFilter] = useState('all');
  useEffect(() => { void workflow.initialize(); }, [workflow]);
  useEffect(() => { if (source && !state.sources.some(item => item.id === source)) setSource(''); }, [state.sources, source]);
  const busy = state.busy || state.loading;
  const canModel = state.configured && !busy;
  const visible = state.memories.filter(item => filter === 'all' || item.status === filter);
  const name = state.form?.name || '未命名角色';
  return <main className="role-workspace">
    <div className="role-page-title"><div><p className="eyebrow">角色与记忆</p><h1>从一张白纸，写下你的人物。</h1></div>
      <button className="button button-secondary" disabled={busy} onClick={() => void workflow.reload()}>重新读取</button></div>
    <div className="role-layout"><aside className="role-sidebar"><div className="role-portrait">{name.slice(0,1)}</div><h2>{name}</h2><span className="role-active">当前编辑的角色卡</span>
      <nav aria-label="角色设置栏目"><button aria-pressed={tab === 'profile'} onClick={() => setTab('profile')}>人格卡</button><button aria-pressed={tab === 'memory'} onClick={() => setTab('memory')}>记忆卡 <span>{state.memories.filter(item => item.status === 'confirmed').length}</span></button></nav>
      <p>当前使用一张角色卡。名字、背景和性格都可以重新编写。</p></aside>
      <div className="role-content">
        {(state.error || state.notice) && <div className={`notice ${state.error ? 'notice-error' : 'role-notice'}`} role={state.error ? 'alert' : 'status'}>{state.error || state.notice}</div>}
        {state.usage && <p className="role-usage">{state.cached ? '恢复已有结果，本次未重复调用模型。' : `本次用量 ${state.usage.total_tokens} token`}</p>}
        {!state.configured && !state.loading && <p className="chat-history-note">模型尚未配置，仍可编写和保存人物；试说和话题整理需要模型。</p>}
        {state.loading ? <div className="role-loading">正在读取本机角色资料…</div> : state.form && tab === 'profile' ? <section className="panel role-free-panel">
          <div className="panel-heading"><h2>人物设定</h2><button className="button button-secondary" disabled={busy} onClick={() => workflow.startBlank()}>从空白开始</button></div>
          <p className="role-version-label">{state.dirty ? '有未保存修改' : `使用版本 ${state.card.revision} · 草稿 ${state.card.draft_revision}`}</p>
          <div className="role-free-body">
            <label className="role-field" htmlFor="role-name"><span>角色名称</span><input id="role-name" maxLength={64} value={state.form.name} disabled={busy} onChange={event => workflow.update('name',event.target.value)} placeholder="想叫什么，由你决定"/></label>
            <label className="role-field role-definition-field" htmlFor="role-definition"><span>自由人物设定</span><textarea id="role-definition" maxLength={ROLE_TEXT_LIMIT} value={roleText(state.form)} disabled={busy} onChange={event => workflow.update('definition',event.target.value)} placeholder={'把你和其他 AI 聊好的人物设定直接粘贴在这里，也可以从头写。\n\n背景、性格、口吻、世界观、对话例子……按你喜欢的方式组织，不需要模板或 JSON。'}/></label>
            <p className="role-form-note">整段原文作为人物设定保存，不自动拆分、改写或补上旧性格。粘贴和保存不调用模型。</p>
            <details className="role-free-extra"><summary>试说与历史版本</summary>
              <label className="role-field" htmlFor="role-preview"><span>用一句话试试角色回应</span><textarea id="role-preview" value={previewMessage} maxLength={1000} disabled={busy} onChange={event => setPreviewMessage(event.target.value)}/></label>
              <button className="button button-secondary" disabled={!canModel || !roleText(state.form).trim() || !previewMessage.trim()} onClick={() => void workflow.preview(previewMessage)}>预览回应</button><span className="cost-note"> 仅试说调用模型，不写入正式记忆</span>
              {state.preview && <p className="role-preview-answer" data-testid="role-preview-answer">{state.preview}</p>}
              <label className="role-field" htmlFor="role-version"><span>载入历史版本到草稿</span><select id="role-version" defaultValue="" disabled={busy} onChange={event => { if(event.target.value !== '') workflow.loadVersion(event.target.value); event.target.value=''; }}><option value="">选择历史设定…</option>{state.card.versions.map(item => <option key={item.revision} value={item.revision}>版本 {item.revision} · {item.profile.name}</option>)}</select></label>
            </details>
          </div>
          <div className="role-save-bar"><button className="button button-secondary" disabled={busy} onClick={() => void workflow.save(false)}>保存草稿</button><button className="button button-primary" disabled={busy || !roleText(state.form).trim()} onClick={() => void workflow.save(true)}>保存并生效</button></div>
        </section> : state.card && tab === 'memory' ? <section className="role-memory-section">
          <div className="panel memory-tools"><div className="panel-heading"><h2>整理一段聊过的事</h2><span className="soft-label">先候选，后确认</span></div>
            <div className="memory-extract-row"><label className="sr-only" htmlFor="memory-source">选择整理的话题</label><select id="memory-source" value={source} disabled={busy} onChange={event => setSource(event.target.value)}><option value="">选择一段话题…</option>{state.sources.map(item => <option key={item.id} value={item.id}>{item.title} · {item.message_count} 条</option>)}</select><button className="button button-primary" disabled={!canModel || !source} onClick={() => void workflow.extract(source)}>整理话题记忆</button></div>
            <p className="role-form-note">这里只列角色话题，工作解析不进入角色记忆。点击后把有限聊天内容交给模型；摘要和候选记忆需要确认后才供角色对话参考，同一段未更新内容会复用结果。</p>
            <div className="memory-manual"><label className="sr-only" htmlFor="memory-category">手动记忆类型</label><select id="memory-category" value={manualCategory} disabled={busy} onChange={event => setManualCategory(event.target.value)}><option value="preference">偏好</option><option value="fact">用户信息</option></select>
              <label className="sr-only" htmlFor="memory-content">手动记忆内容</label><input id="memory-content" value={manualText} maxLength={240} disabled={busy} onChange={event => setManualText(event.target.value)} placeholder="手动写一条你明确希望她记住的事…"/>
              <button className="button button-secondary" disabled={busy || !manualText.trim()} onClick={async () => { if (await workflow.addMemory(manualCategory,manualText)) setManualText(''); }}>添加记忆</button></div>
          </div>
          <div className="memory-list-heading"><h2>角色记忆卡</h2><label className="sr-only" htmlFor="memory-filter">筛选记忆状态</label><select id="memory-filter" value={filter} onChange={event => setFilter(event.target.value)}><option value="all">全部条目</option><option value="pending">待确认</option><option value="confirmed">已确认</option></select></div>
          <p className="role-form-note">记忆卡和原始聊天分别保存。暂停或移除卡片后，原聊天记录仍在；最近对话中提过的内容也可能仍被参考。</p>
          <div className="memory-list">{visible.length ? visible.map(memory => <MemoryItem key={memory.id} memory={memory} workflow={workflow} busy={busy}/>) : <div className="memory-empty">这里还没有对应的记忆。可以先手动添加，或选择一段话题整理。</div>}</div>
        </section> : null}
      </div>
    </div>

  </main>;
}
