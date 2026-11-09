import { createRoot } from 'react-dom/client';
import { useEffect, useMemo, useRef, useState, useSyncExternalStore } from 'react';
import { createImageWorkflow } from './workflow.mjs';
import { createChatWorkflow } from './chat-workflow.mjs';
import ChatPanel from './ChatPanel.jsx';
import VideoWorkspace from './VideoWorkspace.jsx';
import { createVideoWorkflow } from './video-workflow.mjs';
import { createRoleWorkflow } from './role-workflow.mjs';
import RoleSettings from './RoleSettings.jsx';
import BillingBadge from './BillingBadge.jsx';
import './style.css';

function Icon({ name, size = 20, className = '' }) {
  const paths = {
    image: <><rect x="3" y="3" width="18" height="18" rx="4"/><circle cx="8.5" cy="8.5" r="1.5"/><path d="m21 15-5-5L5 21"/></>,
    upload: <><path d="M12 16V3m-5 5 5-5 5 5M4 15v5a1 1 0 0 0 1 1h14a1 1 0 0 0 1-1v-5"/></>,
    arrow: <><path d="M4 12h15m-6-6 6 6-6 6"/></>,
    close: <path d="m6 6 12 12M6 18 18 6"/>,
    check: <path d="m5 12 4 4L19 6"/>,
    spark: <><path d="m12 3 2.5 6.5L21 12l-6.5 2.5L12 21l-2.5-6.5L3 12l6.5-2.5L12 3Z"/></>,
    leaf: <><path d="M20 4c0 12-3 16-9 16a7 7 0 0 1-7-7C4 7 8 4 20 4Z"/><path d="M4 20 15 9"/></>,
    alert: <><path d="m12 3 10 18H2L12 3Z"/><path d="M12 9v5m0 3v.2"/></>,
  };
  return <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" className={className}>{paths[name]}</svg>;
}

function LocalImage({ file, className, alt }) {
  const [url, setUrl] = useState('');
  useEffect(() => {
    const next = URL.createObjectURL(file);
    setUrl(next);
    return () => URL.revokeObjectURL(next);
  }, [file]);
  return url ? <img src={url} className={className} alt={alt} /> : null;
}

function App() {
  const workflow = useMemo(() => createImageWorkflow(), []);
  const state = useSyncExternalStore(workflow.subscribe, workflow.getState);
  const chatWorkflow = useMemo(() => createChatWorkflow(), []);
  const chatState = useSyncExternalStore(chatWorkflow.subscribe, chatWorkflow.getState);
  const videoWorkflow = useMemo(() => createVideoWorkflow(), []);
  const videoState = useSyncExternalStore(videoWorkflow.subscribe, videoWorkflow.getState);
  const videoChatWorkflow = useMemo(() => createChatWorkflow({ mode: 'video' }), []);
  const videoChatState = useSyncExternalStore(videoChatWorkflow.subscribe, videoChatWorkflow.getState);
  const [videoDraft, setVideoDraft] = useState('');
  const [mode, setMode] = useState('video');
  const [chatDraft, setChatDraft] = useState('');
  const input = useRef(null);
  const [question, setQuestion] = useState('请描述这张图片的主要场景、物体和细节。');
  const [dragging, setDragging] = useState(false);
  const roleWorkflow = useMemo(() => createRoleWorkflow(), []);
  const roleState = useSyncExternalStore(roleWorkflow.subscribe, roleWorkflow.getState);
  const [page, setPage] = useState(location.hash === '#/roles' ? 'roles' : 'chat');
  useEffect(() => { const change = () => setPage(location.hash === '#/roles' ? 'roles' : 'chat'); window.addEventListener('hashchange',change); return () => window.removeEventListener('hashchange',change); }, []);
  useEffect(() => () => roleWorkflow.dispose(), [roleWorkflow]);
  useEffect(() => { if (page === 'chat') { void chatWorkflow.refreshPersona(); void videoChatWorkflow.refreshPersona(); } }, [page,chatWorkflow,videoChatWorkflow]);
  useEffect(() => { void workflow.initialize(); return () => workflow.dispose(); }, [workflow]);
  useEffect(() => { void chatWorkflow.initialize(); return () => chatWorkflow.dispose(); }, [chatWorkflow]);
  useEffect(() => { void videoWorkflow.initialize(); return () => videoWorkflow.dispose(); }, [videoWorkflow]);
  useEffect(() => { void videoChatWorkflow.initialize(); return () => videoChatWorkflow.dispose(); }, [videoChatWorkflow]);
  useEffect(() => {
    if (videoChatState.videoId && videoChatState.videoId !== videoWorkflow.getState().video?.video_id) void videoWorkflow.loadVideo(videoChatState.videoId);
  }, [videoChatState.videoId, videoWorkflow]);
  const busy = state.analyzing || chatState.sending || videoState.uploading || videoState.loadingVideo || videoState.analyzing || videoChatState.sending || videoChatState.loading || roleState.busy;
  const count = state.files.length;
  const multiple = count > 1;
  const canAnalyze = count === 1 && state.uploadStage === 'ready' && state.configured === true && question.trim() && !state.analyzing;
  const ready = state.uploadStage === 'ready';
  const choose = () => input.current?.click();
  const drop = event => {
    event.preventDefault(); setDragging(false);
    if (!busy) void workflow.selectFiles(event.dataTransfer.files);
  };

  return <div className="app-shell">
    <header className="topbar">
      <a href="/app/" className="brand" aria-label="VisionSoul 工作台"><span className="brand-mark"><Icon name="leaf" size={22}/></span><span>VisionSoul<span className="brand-caption">画面 · 发现 · 陪伴</span></span></a>
      <div className="flex items-center gap-5">
        <BillingBadge/>
        <span className="model-state"><span className={`status-dot ${state.configured ? 'is-ready' : ''}`}/>{state.configured ? 'DeepSeek Flash · 已配置' : state.configError ? '后端连接待检查' : state.configured === false ? '模型待配置' : '正在检查配置'}</span>
        <button className="role-entry" disabled={busy} onClick={() => { location.hash = page === 'roles' ? '' : '/roles'; }}>{page === 'roles' ? '← 返回对话' : '角色与记忆'}</button>
        <a className="docs-link" href="/docs" target="_blank" rel="noreferrer">接口文档 <span aria-hidden="true">↗</span></a>
      </div>
    </header>

    {page === 'roles' ? <RoleSettings workflow={roleWorkflow} state={roleState}/> : <main className="workspace">
      <div className="workspace-title"><div><p className="eyebrow">VisionSoul 工作台</p><h1>{mode === 'video' ? '让画面，连成一段发现。' : mode === 'image' ? '看见画面，发现细节。' : `和${chatState.persona.name}，聊聊此刻。`}</h1></div>
        <div className="mode-tabs" role="tablist" aria-label="工作台模式"><button role="tab" aria-selected={mode === 'video'} disabled={busy} onClick={() => setMode('video')}>视频观察</button><button role="tab" aria-selected={mode === 'image'} disabled={busy} onClick={() => setMode('image')}>图片理解</button><button role="tab" aria-selected={mode === 'chat'} disabled={busy} onClick={() => setMode('chat')}>伙伴对话</button></div></div>
      {mode === 'video' ? <VideoWorkspace workflow={videoWorkflow} state={videoState} chatWorkflow={videoChatWorkflow} chatState={videoChatState} draft={videoDraft} setDraft={setVideoDraft}/> : <div className="work-grid">
        <section className={`panel image-panel ${dragging ? 'is-dragging' : ''}`} aria-labelledby="image-heading"
          onDragOver={event => { event.preventDefault(); if (!busy) setDragging(true); }}
          onDragLeave={event => { if (!event.currentTarget.contains(event.relatedTarget instanceof Node ? event.relatedTarget : null)) setDragging(false); }} onDrop={drop}>
          <div className="panel-heading"><div className="section-heading"><span className="step">01</span><h2 id="image-heading">{mode === 'chat' ? '图片（可选）' : '你的图片'}</h2></div>
            {count > 0 && <button className="text-button" onClick={() => void workflow.clear()} disabled={busy}>清空</button>}</div>
          <input ref={input} className="sr-only" tabIndex={-1} type="file" accept="image/jpeg,image/png,image/webp" multiple aria-label="选择图片文件" disabled={busy}
            onChange={event => { const files = Array.from(event.target.files || []); event.target.value = ''; if (files.length) void workflow.selectFiles(files); }}/>

          <div className={`preview-zone ${!count ? 'is-empty' : ''}`}>
            {!count ? <div className="empty-upload"><span className="empty-icon"><Icon name="image" size={38}/></span><h3>{mode === 'chat' ? '也可以直接聊聊' : '从一张图片开始'}</h3><p>{mode === 'chat' ? '右侧可以直接发送文字，图片随你选择' : '拖动图片到这里，或从设备中选择'}</p><button className="button button-primary" onClick={choose} disabled={busy}><Icon name="upload" size={17}/>选择图片</button><span className="format-note">JPG / PNG / WebP · 每张不超过 10 MB</span></div>
              : multiple ? <div className="multi-grid" data-testid="multi-preview">{state.files.map((entry, index) => <figure className="thumbnail" key={entry.id}><LocalImage file={entry.file} alt={`待选图片 ${index + 1}`}/><button className="remove-image" onClick={() => void workflow.removeFile(entry.id)} disabled={busy} aria-label={`移除图片 ${index + 1}`}><Icon name="close" size={15}/></button><figcaption title={entry.file.name}>{entry.file.name}</figcaption></figure>)}</div>
              : <LocalImage key={state.files[0].id} file={state.files[0].file} className="single-preview" alt="当前待分析图片"/>}
          </div>

          {state.selectionError && <div className="notice notice-error" role="alert"><Icon name="alert" size={17}/><span>{state.selectionError}</span></div>}
          <div className="image-footer">
            <div className="image-info">{count === 0 ? <><span className="subtle-dot"/>尚未选择图片</> : multiple ? <><Icon name="image" size={16}/><span>已选择 {count} 张 · 仅作预览</span></> : <><span className={`status-dot ${ready ? 'is-ready' : ''}`}/><span className="filename" title={state.files[0].file.name}>{state.files[0].file.name}</span><span className="image-state">{state.uploadStage === 'uploading' ? '上传中…' : ready ? '已就绪' : '上传失败'}</span></>}</div>
            {count > 0 && <button className="button button-secondary" onClick={choose} disabled={busy}>更换图片</button>}
          </div>
          {ready && <div className="linked-note"><Icon name="check" size={14}/><span>图片已自动关联，无需填写编号</span><span>{state.upload.width} × {state.upload.height}</span></div>}
        </section>

        {mode === 'chat' ? <ChatPanel workflow={chatWorkflow} state={chatState} draft={chatDraft} setDraft={setChatDraft} imageState={state} configured={chatState.persona.configured === true}/> : <section className="panel analysis-panel" aria-labelledby="analysis-heading">
          <div className="panel-heading"><div className="section-heading"><span className="step">02</span><h2 id="analysis-heading">关于这张图片</h2></div><span className="soft-label">图片理解</span></div>
          <div className="question-section">
            {multiple && <div className="notice notice-warning" role="alert"><Icon name="alert" size={18}/><div><strong>目前只能分析单个图像</strong><p>当前选择了 {count} 张，请移除多余图片，仅保留一张后再分析。</p></div></div>}
            {(state.configError || state.configured === false) && <div className="notice notice-warning" role="status"><Icon name="alert" size={17}/><span>{state.configError || '模型尚未配置，请先完成后端密钥设置。'}</span></div>}
            <label className="question-label" htmlFor="question">你想了解什么？</label>
            <textarea id="question" value={question} maxLength={2000} disabled={state.analyzing} onChange={event => setQuestion(event.target.value)} placeholder="说说你想了解的画面、物体或细节…"/>
            <div className="question-actions"><span className="cost-note">点击分析才会调用模型并产生用量</span><button className="button button-primary analyze-button" disabled={!canAnalyze} onClick={() => void workflow.analyze(question)}>{state.analyzing ? <><span className="spinner"/>正在分析…</> : <>开始分析<Icon name="arrow" size={18}/></>}</button></div>
          </div>

          <div className="result-heading"><h3>分析结果</h3>{state.result?.usage && <span>本次用量 {state.result.usage.total_tokens} token</span>}</div>
          <div className="result-body" aria-live="polite" aria-busy={state.analyzing}>
            {state.analyzing ? <div className="result-empty"><span className="result-orbit"><Icon name="spark" size={30}/></span><h3>正在观察图片中的细节</h3><p>请稍等，回复会显示在这里。</p></div>
              : state.analysisError ? <div className="notice notice-error" role="alert"><Icon name="alert" size={19}/><div><strong>这次分析没有完成</strong><p>{state.analysisError}</p></div></div>
              : state.result ? <article className="answer"><div className="answer-source"><span className="answer-avatar"><Icon name="leaf" size={16}/></span>{chatState.persona.name}<span>· DeepSeek Flash</span></div><p className="answer-text" data-testid="analysis-answer">{state.result.answer}</p>{state.result.truncated && <p className="truncated-note">本次回复达到长度上限，部分内容可能未显示。</p>}</article>
              : <div className="result-empty"><span className="result-orbit"><Icon name="spark" size={30}/></span><h3>让好奇心，走进画面</h3><p>选择一张图片，写下问题，<br/>再点击“开始分析”。</p></div>}
          </div>
          <div className="analysis-footer"><span className="subtle-dot"/><span>当前版本：单张图片理解</span><span>上传后不会自动分析</span></div>
        </section>}
      </div>}
    </main>}
    <footer className="page-footer">
      <span>VisionSoul <span className="footer-separator">/</span> 从看见，到理解</span>
      <details className="model-disclosure">
        <summary>模型与数据说明 · AI辅助生成</summary>
        <div className="model-disclosure-content">
          <p><strong>DeepSeek Flash（云端API）</strong>：用于图片、视频画面理解及角色对话。请求会发送相关图片或视频抽帧、对白文字，以及本次对话所需的提问、上下文、角色设定和已启用记忆；模型调用会产生API用量。</p>
          <p><strong>Whisper-small（本机模型）</strong>：通过faster-whisper识别视频对白，使用SYSTRAN提供的转换权重。原模型由OpenAI提供；本机转写不上传原始音轨，转写文字可用于上述云端分析。</p>
          <p>上述模型均为第三方组件，非本项目自行训练。VisionSoul实现上传、抽帧与选帧、音画候选关联、引用校验、角色与记忆管理及交互界面。AI结果可能有误，重要名称和时间请回看、回听核对。</p>
        </div>
      </details>
    </footer>
  </div>;
}

createRoot(document.getElementById('root')).render(<App/>);
