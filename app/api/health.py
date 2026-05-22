from fastapi import APIRouter

router = APIRouter()


@router.get("/health")
def health() -> dict[str, str]:
    """Simple health endpoint for startup checks."""
    return {"status": "ok"}
