import { validateComparison } from './video-comparison.mjs';

export default function VideoComparison({ comparison, frames, onSelect, formatTime, question }) {
  let report;
  try { report = validateComparison(comparison,frames); }
  catch { return <p className="notice notice-error">比对资料暂时无法展示，原对话仍保留；不会自动重新分析。</p>; }
  if (!report) return null;
  return <section className="video-comparison" key={question || report.conclusion}>
    <h3 className="comparison-title">辨识与比对 · {report.verdict === 'insufficient' ? '线索不足' : '暂定候选'}</h3>
    <div className="comparison-content">
      <p className="comparison-source">仅根据画面与模型知识推断 · 未联网核实</p>
      {question && <p className="comparison-question">本次问题：{question}</p>}
      <h3>{report.target}</h3><p className="comparison-conclusion">{report.conclusion}</p>
      <div className="comparison-features">{report.features.map((feature,index) => {
        const frame = frames.find(frame => frame.index === feature.frame_index);
        return <div className="comparison-feature" key={index}><button onClick={() => onSelect(frame)} aria-label={`回看比对依据 ${index+1}`}><img src={frame.url} alt={`依据 ${index+1}：${feature.detail}`}/><span>{formatTime(frame.time_seconds)} · {feature.detail}</span></button>
          {frame.detail_url && <a href={frame.detail_url} target="_blank" rel="noreferrer">查看细节图 ↗</a>}</div>;
      })}</div>
      {report.candidates.map((candidate,index) => <article className="comparison-candidate" key={index}><strong>候选 {index+1} · {candidate.name}</strong>
        <p>可见依据：{candidate.supporting_features.map(index => report.features[index].detail).join('；')}</p><p>区别与未确定点：{candidate.differences}</p></article>)}
      {!!report.missing_features.length && <p className="comparison-missing">还缺少：{report.missing_features.join('；')}</p>}
      <p className="comparison-next">建议补拍：{report.next_shot}</p>
    </div>
  </section>;
}
