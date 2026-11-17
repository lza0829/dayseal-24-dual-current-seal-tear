from fastapi import APIRouter, Body
from app.services import settings_service as svc

router = APIRouter(prefix="/settings", tags=["settings"])


@router.get("/business-day")
def get_day():
    return svc.get_day()


@router.put("/business-day")
def set_day(body: dict = Body(...)):
    return svc.set_day(body)


@router.get("/reseal-policy")
def get_reseal_policy():
    return svc.get_reseal_policy()


@router.put("/reseal-policy")
def set_reseal_policy(body: dict = Body(...)):
    return svc.set_reseal_policy(body)
