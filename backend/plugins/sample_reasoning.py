from fastapi import APIRouter

router = APIRouter(prefix="/sample", tags=["sample"])

@router.get("/")
async def sample_endpoint():
    return {"message": "This is a sample reasoning module plugin"}