import { validateCodeDebug } from './code-debug.mjs';

export default function VideoCodeDebug({ report, frames, onSelect, formatTime, stale }) {
  try { validateCodeDebug(report, frames); }
  catch { return <p className="notice notice-error">排错依据格式异常，暂不提供跳转。</p>; }
  return <section className="code-debug-report" aria-label="代码排错报告">
    <h3>{stale ? '旧排错报告 · 待更新' : report.status === 'likely' ? '有依据的可能原因' : '还需要更清楚的信息'}</h3>
    <p className="chat-history-note">建议尚未执行或测试，请核对后修改、重新运行。</p>
    {report.visible_error && <pre>{report.visible_error}</pre>}
    {report.causes.map((cause, i) => <article key={i}><strong>{cause.hypothesis}</strong><p>验证：{cause.check}</p></article>)}
    {!!report.steps.length && <ol>{report.steps.map((step, i) => <li key={i}>{step}</li>)}</ol>}
    {report.suggested_code && <><h4>建议片段 · 请核对缩进和变量名</h4><pre>{report.suggested_code}</pre></>}
    {!!report.missing.length && <p>需要补充：{report.missing.join('；')}</p>}
    <h4>可读画面依据</h4>
    {report.evidence.map((item, i) => {
      const frame = frames.find(row => row.index === item.frame_index);
      return <article key={i}><button className="text-button" onClick={() => onSelect(frame)}>回看 {formatTime(frame.time_seconds)}{item.line_number != null ? ` · 画面行号${item.line_number}` : ''}</button><pre>{item.excerpt}</pre></article>;
    })}
  </section>;
}
