from fastapi import APIRouter
from fastapi.responses import PlainTextResponse

from packages.observability import metrics


router = APIRouter(tags=["ops"])


@router.get("/metrics", response_class=PlainTextResponse)
def prometheus() -> str:
    return metrics.render_prometheus()
