"""Request and response models for the HTTP API."""

from pydantic import BaseModel, Field, model_validator

# Bounds the amount of work a single request can trigger.
MAX_ITEMS_PER_LIST = 1_000


class PayloadCreateRequest(BaseModel):
    """Two equally long lists of strings to interleave."""

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "list_1": ["first string", "second string", "third string"],
                    "list_2": ["other string", "another string", "last string"],
                }
            ]
        }
    }

    list_1: list[str] = Field(min_length=1, max_length=MAX_ITEMS_PER_LIST)
    list_2: list[str] = Field(min_length=1, max_length=MAX_ITEMS_PER_LIST)

    @model_validator(mode="after")
    def _lists_must_have_equal_length(self) -> "PayloadCreateRequest":
        if len(self.list_1) != len(self.list_2):
            raise ValueError("list_1 and list_2 must have the same length")
        return self


class PayloadCreateResponse(BaseModel):
    id: str = Field(description="Identifier to read the payload back with")
    message: str
    reused: bool = Field(description="True when an identical payload already existed")


class PayloadReadResponse(BaseModel):
    output: str


class HealthResponse(BaseModel):
    status: str
