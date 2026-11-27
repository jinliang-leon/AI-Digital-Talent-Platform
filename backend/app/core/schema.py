from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

Short = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=160)]
Text = Annotated[str, StringConstraints(strip_whitespace=True, max_length=8000)]
Reason = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=800)]


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Version(Input):
    expected_version: int = Field(ge=1)
