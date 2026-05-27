"""GET /v1/models — return masqueraded model list for Claude Desktop."""

from fastapi import APIRouter
from .mapper import get_mapper

router = APIRouter()


@router.get("/v1/models")
async def list_models():
    return get_mapper().get_model_list()
