import { useEffect, useState } from 'react';

export default function BillingBadge() {
  const [info,setInfo]=useState(null);
  useEffect(()=>{
    let disposed=false, timer, controller, active=false;
    async function refresh(){
      if(disposed || active || document.visibilityState==='hidden') return;
      clearTimeout(timer);active=true;controller=new AbortController();
      const deadline=setTimeout(()=>controller.abort(),5000);
      let delay=30000;
      try{
        const response=await fetch('/api/billing/period',{cache:'no-store',signal:controller.signal});
        if(!response.ok)throw new Error('unavailable');
        const value=await response.json();
        if(!['peak','off_peak','unknown'].includes(value.period) || typeof value.rule!=='string')throw new Error('invalid');
        if(!disposed){setInfo(value);delay=Math.max(1000,Math.min(60000,Number(value.refresh_after_seconds)*1000 || 60000));}
      }catch{if(!disposed)setInfo(null);}
      finally{clearTimeout(deadline);active=false;if(!disposed && document.visibilityState!=='hidden')timer=setTimeout(refresh,delay);}
    }
    const visible=()=>{clearTimeout(timer);if(document.visibilityState==='visible'){setInfo(null);void refresh();}};
    void refresh();document.addEventListener('visibilitychange',visible);
    return()=>{disposed=true;clearTimeout(timer);controller?.abort();document.removeEventListener('visibilitychange',visible);};
  },[]);
  const label=info?.label || '时段待确认';
  return <span className={`billing-badge billing-${info?.period || 'unknown'}`} role="status" title={info?.rule || '无法读取后端时间，稍后自动重试；实际计费以平台为准。'} aria-label={`DeepSeek计费提示：${label}`}>
    <span className="billing-dot" aria-hidden="true"/><strong>{label}</strong><small>北京时间</small>
  </span>;
}
