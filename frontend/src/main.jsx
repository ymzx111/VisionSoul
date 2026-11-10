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
  const workflows = useMemo(() => ({ work: createMediaWorkflow({ space: 'work' }), companion: createMediaWorkflow({ space: 'companion' }) }), []);
  const workState = useSyncExternalStore(workflows.work.subscribe, workflows.work.getState);
  const companionState = useSyncExternalStore(workflows.companion.subscribe, workflows.companion.getState);
  const [space, setSpace] = useState(location.hash === '#/roles' ? 'companion' : 'work');
  const workflow = workflows[space];
  const state = space === 'work' ? workState : companionState;
  const roleWorkflow = useMemo(() => createRoleWorkflow(), []);
  const roleState = useSyncExternalStore(roleWorkflow.subscribe, roleWorkflow.getState);
  const [drafts, setDrafts] = useState({ work: '', companion: '' });
  const draft = drafts[space];
  const setDraft = value => setDrafts(previous => ({ ...previous, [space]: typeof value === 'function' ? value(previous[space]) : value }));
  const [page, setPage] = useState(location.hash === '#/roles' ? 'roles' : 'chat');
  useEffect(() => { const change = () => { const roles = location.hash === '#/roles'; setPage(roles ? 'roles' : 'chat'); if (roles) setSpace('companion'); }; window.addEventListener('hashchange',change); return () => window.removeEventListener('hashchange',change); }, []);
  useEffect(() => { void workflow.initialize(); void workflow.chat.refreshLegacy(); }, [workflow]);
  useEffect(() => () => { workflows.work.dispose(); workflows.companion.dispose(); }, [workflows]);
  useEffect(() => () => roleWorkflow.dispose(), [roleWorkflow]);
  useEffect(() => { if (page === 'chat') void workflow.chat.refreshPersona(); }, [page,workflow]);
  const busy = workflows.work.busy() || workflows.companion.busy() || roleState.busy;
  const configured = state.image.configured;
  return <div className="app-shell">
    <header className="topbar">
      <a href="/app/" className="brand" aria-label="VisionSoul 工作台"><span className="brand-mark"><Leaf/></span><span>VisionSoul<span className="brand-caption">画面 · 理解 · 日常</span></span></a>
      <div className="flex items-center gap-5">
        <BillingBadge/>
        <span className="model-state"><span className={`status-dot ${configured ? 'is-ready' : ''}`}/>{configured ? 'DeepSeek Flash · 已配置' : state.image.configError ? '后端连接待检查' : configured === false ? '模型待配置' : '正在检查配置'}</span>
        {space === 'companion' && <button className="role-entry" disabled={busy} onClick={() => { location.hash = page === 'roles' ? '' : '/roles'; }}>{page === 'roles' ? '← 返回角色对话' : '角色设置'}</button>}
        <a className="docs-link" href="/docs" target="_blank" rel="noreferrer">接口文档 ↗</a>
      </div>
    </header>
    <nav className="space-switch" aria-label="选择对话空间">
      <div className="space-switch-buttons">{[['work', '工作解析'], ['companion', '角色对话']].map(([value, label]) =>
        <button key={value} aria-pressed={space === value} disabled={busy} onClick={() => { setSpace(value); setPage('chat'); location.hash = ''; }}>{label}</button>)}</div>
      <span>{space === 'work' ? '客观分析 · 保留本话题上下文 · 不读取角色记忆' : '个性化交流 · 使用角色设定与已确认记忆'}</span>
    </nav>
    {page === 'roles' ? <RoleSettings workflow={roleWorkflow} state={roleState}/> : <main className="workspace">
      <div className="workspace-title"><div><p className="eyebrow">{space === 'work' ? '工作解析' : '角色对话'}</p><h1>{space === 'work' ? '一张图片，或一段视频。' : `和${state.chat.persona.name}分享这一刻。`}</h1></div><span className="version-chip">单张图片 · 视频最长30秒</span></div>
      <MediaWorkspace key={space} workflow={workflow} state={state} draft={draft} setDraft={setDraft}/>
    </main>}
    <footer className="page-footer">
      <span>VisionSoul <span className="footer-separator">/</span> 从看见，到理解</span>
      <details className="model-disclosure">
        <summary>模型与数据说明 · AI辅助生成</summary>
        <div className="model-disclosure-content">
          <p><strong>DeepSeek Flash（云端API）</strong>：用于图片、视频画面理解和对话。请求会发送相关图片或视频抽帧、对白文字、提问及本话题上下文；仅角色对话附带角色设定与已确认记忆，工作解析不读取角色记忆。模型调用会产生API用量。</p>
          <p><strong>Whisper-small（本机模型）</strong>：通过faster-whisper识别视频对白，使用SYSTRAN提供的转换权重。原模型由OpenAI提供；本机转写不上传原始音轨，转写文字可用于上述云端分析。</p>
          <p>上述模型均为第三方组件，非本项目自行训练。VisionSoul实现上传、抽帧与选帧、音画候选关联、引用校验、角色与记忆管理及交互界面。AI结果可能有误，重要名称和时间请回看、回听核对。</p>
        </div>
      </details>
    </footer>
  </div>;
}

createRoot(document.getElementById('root')).render(<App/>);
