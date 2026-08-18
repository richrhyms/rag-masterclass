"""GET /api/health — the one live endpoint owned by G-4. No auth (design.md
API Contracts: "Auth: none")."""
from fastapi import APIRouter

router = APIRouter(tags=["health"])


@router.get("/api/health")
async def health() -> dict:
    return {"status": "ok"}
