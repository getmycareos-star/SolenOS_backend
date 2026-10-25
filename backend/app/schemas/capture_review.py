from typing import Literal, Optional

from pydantic import BaseModel, Field, model_validator


class CaptureCandidateReview(BaseModel):
    candidate_index: int = Field(ge=0)
    decision: Literal["accept", "correct", "reject"]
    corrected_subject: Optional[str] = Field(default=None, max_length=160)
    corrected_observation: Optional[str] = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def require_correction_text(self):
        if self.decision == "correct":
            if not ((self.corrected_subject or "").strip() or (self.corrected_observation or "").strip()):
                raise ValueError("A correction must include a subject or observation.")
        return self
