"""按公开时段规则估算提示，不查询账户、不产生模型调用。"""
from datetime import datetime, timedelta, timezone

BEIJING = timezone(timedelta(hours=8), "Asia/Shanghai")
PRICING_SOURCE = "https://api-docs.deepseek.com/zh-cn/quick_start/pricing/"
# 国办发明电〔2025〕7号；未知年份的工作日高峰窗口不猜测。
HOLIDAY_SOURCE = "https://www.beijing.gov.cn/cs/gncs/zcwj/202603/t20260327_4568275.html"
HOLIDAY_RANGES = {2026: [(1,1,1,3),(2,15,2,23),(4,4,4,6),(5,1,5,5),(6,19,6,21),(9,25,9,27),(10,1,10,7)]}


def billing_period(now=None):
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError("timestamp must include timezone")
    local = now.astimezone(BEIJING)
    minutes = local.hour*60+local.minute
    window = 540 <= minutes < 720 or 840 <= minutes < 1080
    phase = "off_peak"
    if local.weekday() < 5 and window:
        ranges = HOLIDAY_RANGES.get(local.year)
        if ranges is None:
            phase = "unknown"
        elif not any(datetime(local.year,m,d).date() <= local.date() <= datetime(local.year,em,ed).date() for m,d,em,ed in ranges):
            phase = "peak"
    label = {"peak":"高峰时段","off_peak":"空闲时段","unknown":"时段待核对"}[phase]
    boundaries = [local.replace(hour=hour,minute=0,second=0,microsecond=0) for hour in [9,12,14,18]]
    boundaries.append((local+timedelta(days=1)).replace(hour=0,minute=0,second=0,microsecond=0))
    delay = min((value-local).total_seconds() for value in boundaries if value>local)
    return {"period":phase,"label":label,"beijing_time":local.isoformat(),"timezone":"Asia/Shanghai",
        "refresh_after_seconds":max(1,min(60,delay+0.1)),"estimated":True,
        "rule":"北京时间周一至周五9:00–12:00、14:00–18:00为高峰；周末及节假日为空闲。提示按已公布日历估算，实际计费以平台为准。",
        "source_url":PRICING_SOURCE,"calendar_years":sorted(HOLIDAY_RANGES)}
