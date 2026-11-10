import { createRoot } from 'react-dom/client';
import { useEffect, useMemo, useState, useSyncExternalStore } from 'react';
import { createMediaWorkflow } from './media-workflow.mjs';
import { createRoleWorkflow } from './role-workflow.mjs';
import MediaWorkspace from './MediaWorkspace.jsx';
import RoleSettings from './RoleSettings.jsx';
import BillingBadge from './BillingBadge.jsx';
import './style.css';

function Leaf() {
  return <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" aria-hidden="true"><path d="M20 4c0 12-3 16-9 16a7 7 0 0 1-7-7C4 7 8 4 20 4Z"/><path d="M4 20 15 9"/></svg>;
}

function App() {
  const workflow = useMemo(() => createMediaWorkflow(), []);
  const state = useSyncExternalStore(workflow.subscribe, workflow.getState);
  const roleWorkflow = useMemo(() => createRoleWorkflow(), []);
  const roleState = useSyncExternalStore(roleWorkflow.subscribe, roleWorkflow.getState);
  const [draft, setDraft] = useState('');
  const [page, setPage] = useState(location.hash === '#/roles' ? 'roles' : 'chat');
  useEffect(() => { const change = () => setPage(location.hash === '#/roles' ? 'roles' : 'chat'); window.addEventListener('hashchange',change); return () => window.removeEventListener('hashchange',change); }, []);
  useEffect(() => { void workflow.initialize(); return () => workflow.dispose(); }, [workflow]);
  useEffect(() => () => roleWorkflow.dispose(), [roleWorkflow]);
  useEffect(() => { if (page === 'chat') void workflow.chat.refreshPersona(); }, [page,workflow]);
  const busy = workflow.busy() || roleState.busy;
  const configured = state.image.configured;
  return <div className="app-shell">
    <header className="topbar">
      <a href="/app/" className="brand" aria-label="VisionSoul 工作台"><span className="brand-mark"><Leaf/></span><span>VisionSoul<span className="brand-caption">画面 · 理解 · 日常</span></span></a>
      <div className="flex items-center gap-5">
        <BillingBadge/>
        <span className="model-state"><span className={`status-dot ${configured ? 'is-ready' : ''}`}/>{configured ? 'DeepSeek Flash · 已配置' : state.image.configError ? '后端连接待检查' : configured === false ? '模型待配置' : '正在检查配置'}</span>
        <button className="role-entry" disabled={busy} onClick={() => { location.hash = page === 'roles' ? '' : '/roles'; }}>{page === 'roles' ? '← 返回对话' : '角色与记忆'}</button>
        <a className="docs-link" href="/docs" target="_blank" rel="noreferrer">接口文档 ↗</a>
      </div>
    </header>
    {page === 'roles' ? <RoleSettings workflow={roleWorkflow} state={roleState}/> : <main className="workspace">
      <div className="workspace-title"><div><p className="eyebrow">VisionSoul 工作台</p><h1>一张图片，或一段视频。</h1></div><span className="version-chip">单张图片 · 视频最长30秒</span></div>
      <MediaWorkspace workflow={workflow} state={state} draft={draft} setDraft={setDraft}/>
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
