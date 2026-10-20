from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from app.core.database import get_db
from app.services.situation import process_situation_input
from app.schemas.situation import SituationInput, SituationResponse

router = APIRouter(prefix="/api", tags=["situation"])

@router.post("/situation", response_model=SituationResponse)
def create_situation(
    situation_input: SituationInput,
    db: Session = Depends(get_db)
):
    try:
        result = process_situation_input(db, situation_input)
        return result
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Situation processing failed: {str(e)}")
