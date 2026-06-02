from pydantic import BaseModel, Field


class ClassProbability(BaseModel):
    class_id: int
    label: str
    confidence: float = Field(..., ge=0, le=1)


class ClassificationResponse(BaseModel):
    filename: str
    predicted_class_id: int
    predicted_class: str
    confidence: float = Field(..., ge=0, le=1)
    probabilities: list[ClassProbability]
