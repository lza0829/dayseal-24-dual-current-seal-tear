from fastapi import APIRouter, Body
from app.services import close_service as svc

router = APIRouter(prefix="/close", tags=["close"])


@router.get("/preview")
def preview(business_day: str | None = None):
    return svc.preview(business_day)


@router.get("/current")
def current(business_day: str | None = None):
    """当前营业日（或指定日）唯一现行批；无现行批时返回 null。"""
    return svc.current(business_day)


@router.post("/seal")
def seal(body: dict = Body(...)):
    return svc.seal(body)


@router.get("/batches")
def batches(current_only: bool = False):
    return svc.list_batches(current_only=current_only)


@router.get("/batches/current")
def current_batches():
    return svc.list_batches(current_only=True)


@router.get("/variances")
def variances(current_only: bool = False):
    return svc.list_variances(current_only=current_only)


@router.get("/variances/current")
def current_variances():
    return svc.list_variances(current_only=True)


@router.get("/policy")
def get_policy():
    return svc.get_policy()


@router.put("/policy")
def set_policy(body: dict = Body(...)):
    return svc.set_policy(body)


@router.get("/integrity")
def integrity():
    return svc.integrity()
