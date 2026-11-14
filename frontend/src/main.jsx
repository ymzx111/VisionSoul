import { createRoot } from 'react-dom/client';
import { useEffect, useMemo, useState, useSyncExternalStore } from 'react';
import { createMediaWorkflow } from './media-workflow.mjs';
import MediaWorkspace from './MediaWorkspace.jsx';
import BillingBadge from './BillingBadge.jsx';
import './style.css';

function Leaf() {
  return <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" aria-hidden="true"><path d="M20 4c0 12-3 16-9 16a7 7 0 0 1-7-7C4 7 8 4 20 4Z"/><path d="M4 20 15 9"/></svg>;
}

function App() {
  const workflow = useMemo(() => createMediaWorkflow({ space: 'work' }), []);
  const state = useSyncExternalStore(workflow.subscribe, workflow.getState);
  const [draft, setDraft] = useState('');
  useEffect(() => {
    if (location.hash === '#/roles') history.replaceState(history.state, '', location.pathname + location.search);
    void workflow.initialize();
    return () => workflow.dispose();
  }, [workflow]);
  const configured = state.image.configured;
  return <div className="app-shell">
    <header className="topbar">
      <a href="/app/" className="brand" aria-label="VisionSoul 工作台"><span className="brand-mark"><Leaf/></span><span>VisionSoul</span></a>
      <div className="flex items-center gap-5">
        <BillingBadge/>
        <span className="model-state"><span className={`status-dot ${configured ? 'is-ready' : ''}`}/>{configured ? 'DeepSeek Flash · 已配置' : state.image.configError ? '后端连接待检查' : configured === false ? '模型待配置' : '正在检查配置'}</span>
        <a className="docs-link" href="/docs" target="_blank" rel="noreferrer">接口文档 ↗</a>
      </div>
    </header>
    <main className="workspace">
      <div className="workspace-title"><h1>本机编程工作台</h1></div>
      <MediaWorkspace workflow={workflow} state={state} draft={draft} setDraft={setDraft}/>
    </main>
    <footer className="page-footer">
      <span>VisionSoul</span>
      <details className="model-disclosure">
        <summary>模型与数据说明 · AI辅助生成</summary>
        <div className="model-disclosure-content">
          <p><strong>DeepSeek Flash（云端 API）</strong>用于读题和生成代码。请求会发送相关图片或视频抽帧、对白文字、提问及本题上下文。模型调用会产生 API 用量。</p>
          <p><strong>Whisper-small（本机模型）</strong>：通过faster-whisper识别视频对白，使用SYSTRAN提供的转换权重。原模型由OpenAI提供；本机转写不上传原始音轨，转写文字可用于上述云端分析。</p>
          <p>上述模型均为第三方组件。VisionSoul 实现素材上传、抽帧、音画关联、源码交付和本机沙箱试跑。请按题目要求核对生成结果。</p>
        </div>
      </details>
    </footer>
  </div>;
}

createRoot(document.getElementById('root')).render(<App/>);
