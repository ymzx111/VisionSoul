import { useEffect, useRef, useState } from 'react';

async function readApprovedFile(file) {
  if (file.size > 8192 || !/\.(txt|csv|json)$/i.test(file.name)) throw new Error();
  const content = new TextDecoder('utf-8', { fatal: true }).decode(await file.arrayBuffer());
  return { filename: file.name, content };
}

function PythonRun({ initial, runId, disabled }) {
  const [latest, setLatest] = useState(null);
  const [input, setInput] = useState('');
  const [running, setRunning] = useState(false);
  const [error, setError] = useState('');
  const filePicker = useRef(null);
  const result = latest || initial;

  async function rerun(event) {
    event.preventDefault();
    if (running || disabled) return;
    setRunning(true);
    setError('');
    try {
      const file = filePicker.current?.files[0];
      const body = { run_id: runId, stdin: input };
      if (file) {
        try { body.approved_file = await readApprovedFile(file); }
        catch {
          setError('请选择不超过 8 KiB 的 UTF-8 TXT、CSV 或 JSON 文件。');
          return;
        }
      }
      const response = await fetch('/api/chat/python-runs', {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
      });
      const data = await response.json();
      if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : '试跑请求无效，请检查输入。');
      setLatest(data);
    } catch (error) {
      setError(error instanceof TypeError ? '连接中断，请重新试跑。' : error.message);
    } finally {
      if (filePicker.current) filePicker.current.value = '';
      setRunning(false);
    }
  }

  return <div className="python-check python-run">
    <strong>Python 运行结果</strong>
    <p role="status">{running ? '正在本机沙箱试跑…' : result.detail}</p>
    {!!result.case_results?.length && result.status !== 'awaiting_input' ? <ul className="python-case-results">
      {result.case_results.map((row, index) => <li key={index}>
        <strong>{row.name} · {row.status === 'passed' ? '通过' : '未通过'}</strong>
        <pre>输入：{row.stdin || '（空）'}</pre>
        <pre>预期：{row.expected || '（空）'}</pre>
        <pre>实际：{row.stdout || '（无输出）'}</pre>
        {row.stderr && <pre>{row.stderr}</pre>}
      </li>)}
    </ul> : <>
      <strong>实际输出</strong>
      <pre>{result.stdout || '（无输出）'}</pre>
      {result.stderr && result.status !== 'awaiting_input' && <><strong>错误输出</strong><pre>{result.stderr}</pre></>}
    </>}
    {initial.repair_usage && <p>修复模型用量 {initial.repair_usage.total_tokens} token</p>}
    {initial.repaired_code && <details><summary>修复版源码</summary><pre>{initial.repaired_code}</pre></details>}
    {error && <p className="notice notice-error" role="alert">{error}</p>}
    <details open={result.status === 'awaiting_input' ? true : undefined}>
      <summary>补充输入并试跑</summary>
      <form onSubmit={rerun} className="python-test">
        <label>标准输入
          <textarea rows={3} maxLength={4096} value={input} disabled={running || disabled}
            onChange={event => setInput(event.target.value)} placeholder="按题目格式一次填完所有输入"/>
        </label>
        <label>本轮只读文件（可选）
          <input type="file" accept=".txt,.csv,.json" ref={filePicker} disabled={running || disabled}/>
        </label>
        <button className="button button-secondary" type="submit" disabled={running || disabled}>本机试跑</button>
      </form>
    </details>
  </div>;
}

export default function MediaChatPanel({ workflow, state, draft, setDraft }) {
  const scroll = useRef(null);
  const chat = state.chat;
  const companion = state.space === 'companion';
  const [legacyId, setLegacyId] = useState('');
  const [cases, setCases] = useState([{ name: '', stdin: '', expected: '' }]);
  const [approvedFile, setApprovedFile] = useState(null);
  const [testError, setTestError] = useState('');
  const [readingFile, setReadingFile] = useState(false);
  const fileInput = useRef(null);
  const busy = readingFile || workflow.busy();
  const workMedia = !companion && (state.kind === 'image' || state.kind === 'video');
  const check = chat.localSource?.python_check;
  const ready = !state.kind || (state.kind === 'image' ? state.image.upload?.image_id || state.restoredImageId : state.video.video?.video_id);
  const canSend = chat.persona.configured && !busy && !state.error && ready && (draft.trim() || (state.kind && !chat.conversationId));
  const messages = chat.pending ? [...chat.messages, { ...chat.pending, id: 'pending' }] : chat.messages;
  useEffect(() => { if (scroll.current) scroll.current.scrollTop = scroll.current.scrollHeight; }, [chat.messages, chat.pending]);
  function clearTest() {
    setCases([{ name: '', stdin: '', expected: '' }]);
    setApprovedFile(null); setTestError('');
    if (fileInput.current) fileInput.current.value = '';
  }
  useEffect(clearTest, [state.kind, state.image.upload?.image_id, state.restoredImageId, state.video.video?.video_id]);
  const send = async () => {
    if (busy) return;
    let pythonTest = null;
    const filled = cases.filter(row => row.stdin.trim() || row.expected.trim());
    if (workMedia && (filled.length || approvedFile)) {
      pythonTest = {};
      if (filled.length) {
        pythonTest.cases = filled.slice(0, 8).map((row, index) => ({
          name: row.name.trim() || `用例${index + 1}`,
          stdin: row.stdin,
          expected: row.expected,
        }));
      }
      if (approvedFile) {
        setReadingFile(true);
        try {
          pythonTest.approved_file = await readApprovedFile(approvedFile);
        } catch {
          setTestError('请选择不超过 8 KiB 的 UTF-8 TXT、CSV 或 JSON 文件。');
          return;
        } finally {
          setReadingFile(false);
        }
      }
    }
    const sent = await workflow.send(draft, { pythonTest });
    if (sent) {
      setDraft(value => value === draft ? '' : value);
      clearTest();
    }
  };
  return <section className="panel chat-panel video-chat-panel" aria-labelledby="media-chat-heading">
    <div className="panel-heading"><div className="section-heading"><span className="step">02</span><h2 id="media-chat-heading">{companion ? `${chat.persona.name}的对话` : '分析与对话'}</h2></div>
      <button className="text-button" disabled={busy} onClick={() => { if (workflow.clear()) setDraft(''); }}>新话题 ＋</button></div>
    <div className="chat-topic-bar"><label className="sr-only" htmlFor="media-history">切换历史话题</label>
      <select id="media-history" value={chat.conversationId || ''} disabled={busy} onChange={event => {
        setDraft(''); if (event.target.value) void workflow.loadConversation(event.target.value); else workflow.clear();
      }}><option value="">新话题 · 图片、视频或文字</option>
        {chat.conversationId && !chat.recent.some(row => row.id === chat.conversationId) && <option value={chat.conversationId}>当前话题</option>}
        {chat.recent.map(row => <option key={row.id} value={row.id}>{row.video_id ? '视频' : row.image_id ? '图片' : '文字'} · {row.title}</option>)}
      </select><span>{state.kind === 'video' ? '视频' : state.kind === 'image' ? '单张图片' : '也可以直接聊文字'}</span></div>
    {!!chat.legacy?.length && <details className="legacy-topics"><summary>未分类旧话题（{chat.legacy.length}）</summary>
      <p>旧记录保持原样。选择后归入当前空间；归入工作解析后，旧角色口吻仍保留，新回复采用客观分析。归类后不支持跨区移动。</p>
      <div><label className="sr-only" htmlFor="legacy-topic">选择未分类旧话题</label><select id="legacy-topic" value={legacyId} disabled={busy} onChange={event => setLegacyId(event.target.value)}>
        <option value="">选择旧话题…</option>{chat.legacy.map(row => <option key={row.id} value={row.id}>{row.title}</option>)}
      </select><button className="button button-secondary" disabled={busy || !legacyId || !chat.legacy.some(row => row.id === legacyId)} onClick={async () => {
        if (await workflow.chat.classifyLegacy(legacyId)) { setLegacyId(''); setDraft(''); }
      }}>归入{companion ? '角色对话' : '工作解析'}</button></div>
    </details>}
    <div className="chat-stream" ref={scroll} role="log" aria-label="分析与对话记录" aria-live="polite" aria-busy={chat.sending || chat.loading}>
      {chat.loading ? <p className="chat-loading">正在恢复本机话题…</p> : messages.length ? <>
        {chat.hasOlder && <p className="chat-history-note">显示最近200条，完整记录保存在本机。</p>}
        {messages.map(item => <article key={item.id} className={`chat-message ${item.role === 'user' ? 'from-user' : 'from-companion'}`}>
          <span className="chat-author">{item.role === 'user' ? '你' : chat.persona.name}{item.id === 'pending' ? ' · 正在发送' : ''}</span>
          {item.video_id && <span className="shared-video-chip">▷ 已分享视频</span>}
          {item.image_id && <a href={`/api/images/${item.image_id}`} target="_blank" rel="noreferrer" className="chat-image-link"><img src={`/api/images/${item.image_id}`} width="160" height="110" alt="这一轮分享的图片"/></a>}
          <p data-testid={item.role === 'assistant' ? 'media-analysis-message' : undefined}>{item.content}</p>
          {check && chat.localSource?.python_run_id && item.id === chat.messages.at(-1)?.id &&
            <PythonRun key={chat.localSource.python_run_id} initial={check} runId={chat.localSource.python_run_id} disabled={busy}/>}
          {check && !chat.localSource?.python_run_id && item.id === chat.messages.at(-1)?.id && <details className="python-check">
            <summary>沙箱验收 · {check.detail}</summary>
            {check.exit_code != null && <p>退出码 {check.exit_code} · 试跑 {check.attempts} 次</p>}
            {!!check.case_results?.length && <ul className="python-case-results">
              {check.case_results.map((row, index) => <li key={index}>
                {row.status === 'passed' ? '通过' : '未通过'} · {row.name}
                {' '}输入「{row.stdin.trim() || '空'}」预期「{row.expected}」实际「{row.stdout.trim()}」
              </li>)}
            </ul>}
            {check.stdout && <><strong>运行输出</strong><pre>{check.stdout}</pre></>}
            {check.stderr && <><strong>错误输出</strong><pre>{check.stderr}</pre></>}
            {check.repaired_code && <><strong>修复版源码</strong><pre>{check.repaired_code}</pre></>}
            {check.repair_usage && <p>修复模型用量 {check.repair_usage.total_tokens} token</p>}
          </details>}
        </article>)}
        {chat.sending && <div className="companion-thinking"><span className="thinking-dot"/><span className="thinking-dot"/><span className="thinking-dot"/>正在分析与回复…</div>}
      </> : <div className="chat-welcome"><span className="welcome-mark">▷</span><h3>{companion ? `和${chat.persona.name}聊聊吧。` : '一张图片，或一段视频。'}</h3>
        <p>上传后点击“开始分析”，也可以配上你想问的问题。</p><span>换素材会开启新话题，旧记录仍可恢复。</span></div>}
    </div>
    <div className="chat-composer">
      {chat.persona.configured === false && <p className="notice notice-warning">模型尚未配置。</p>}
      {chat.error && <div className="notice notice-error" role="alert">{chat.error}</div>}
      {chat.localSource && (chat.localSource.status === 'error'
        ? <p className="notice notice-error" role="alert">{chat.localSource.detail}</p>
        : <p className="chat-image-note" role="status">
            {chat.localSource.detail}{chat.localSource.path && <> <code>{chat.localSource.path}</code></>}
          </p>)}
      {chat.truncated && <p className="chat-image-note">上一条回复达到长度上限，可以继续追问。</p>}
      {state.kind === 'video' && <p className="chat-image-note">输入「重点看 3–5 秒」可对指定区间补抽，每次最多 5 秒。读题或排错最多使用 4 张高清图。</p>}
      {workMedia && <details className="python-test">
        <summary>验收样例（Python / C / C++，可选）</summary>
        <p className="chat-image-note">每组用例独立运行、逐组判定，可覆盖正常、空值、边界。Python 在 WASI 沙箱里跑，C 与 C++ 在独立环境中编译，再在 WASI 里运行。手填样例仅在本机使用，失败时最多自动修复 3 次。留空时可运行 AI 自测；其预期输出由模型生成，自测失败不会自动修复。</p>
        {cases.map((row, index) => <div className="python-case" key={index}>
          <label className="sr-only" htmlFor={`python-case-name-${index}`}>用例名称</label>
          <input id={`python-case-name-${index}`} placeholder={`用例${index + 1}`} maxLength={40} disabled={busy}
            value={row.name} onChange={event => setCases(rows => rows.map((item, i) => i === index ? { ...item, name: event.target.value } : item))}/>
          <label className="sr-only" htmlFor={`python-stdin-${index}`}>样例输入</label>
          <textarea id={`python-stdin-${index}`} placeholder="样例输入" maxLength={4096} disabled={busy}
            value={row.stdin} onChange={event => setCases(rows => rows.map((item, i) => i === index ? { ...item, stdin: event.target.value } : item))}/>
          <label className="sr-only" htmlFor={`python-expected-${index}`}>预期输出</label>
          <textarea id={`python-expected-${index}`} placeholder="预期输出" maxLength={4096} disabled={busy}
            value={row.expected} onChange={event => setCases(rows => rows.map((item, i) => i === index ? { ...item, expected: event.target.value } : item))}/>
          {cases.length > 1 && <button type="button" className="text-button" disabled={busy}
            onClick={() => setCases(rows => rows.filter((_, i) => i !== index))}>删除本组</button>}
        </div>)}
        <button type="button" className="text-button" disabled={busy || cases.length >= 8}
          onClick={() => setCases(rows => [...rows, { name: '', stdin: '', expected: '' }])}>＋ 添加用例</button>
        <label htmlFor="python-test-file">本轮允许读取的文件（TXT、CSV 或 JSON，最多 8 KiB）</label>
        <input id="python-test-file" ref={fileInput} type="file" accept=".txt,.csv,.json" disabled={busy}
          onChange={event => { setApprovedFile(event.target.files?.[0] || null); setTestError(''); }}/>
        {testError && <p className="notice notice-error" role="alert">{testError}</p>}
      </details>}
      <label className="sr-only" htmlFor="media-message">问题或补充说明</label>
      <textarea id="media-message" maxLength={2000} disabled={busy} value={draft} onChange={event => setDraft(event.target.value)}
        placeholder={chat.conversationId ? '继续追问，或补充你的说明…'
          : state.kind === 'video' ? '输入你的要求，或留空，让助手从视频里判断。'
          : state.kind === 'image' && workMedia ? '例如：请按照片里的题目写完整的程序，语言按题面。'
          : '可选：你想了解什么？不填写也可以开始分析。'}/>
      <div className="question-actions media-chat-actions"><span className="cost-note">点击后才调用模型{workMedia && state.kind === 'video' ? ' · 格式错误时可能重试一次' : ''}{chat.usage ? chat.replayed ? ' · 已恢复原回复' : ` · 本次${chat.usage.total_tokens} token` : ''}</span>
        <button className="button button-primary" disabled={!canSend} onClick={() => void send()}>{chat.sending ? '正在回复…' : chat.conversationId || !state.kind ? '发送消息 →' : '开始分析 →'}</button>
      </div>
    </div>
    <div className="analysis-footer"><span className="subtle-dot"/><span>{companion ? '角色话题独立保存 · 记忆需在角色设置中整理和确认' : '工作话题独立保存 · 不进入角色记忆'}</span></div>
  </section>;
}
