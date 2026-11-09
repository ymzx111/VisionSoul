from fastapi import APIRouter, Response
from app.services.billing_period import billing_period

router=APIRouter(prefix="/api/billing",tags=["计费提示"])

@router.get('/period',summary="查看当前计费时段提示（不调用模型）")
def period(response: Response):
    response.headers['Cache-Control']='no-store'
    return billing_period()
