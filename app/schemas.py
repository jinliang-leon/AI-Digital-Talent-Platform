"""API 输入视图；显式禁止批量赋值未声明字段。"""

from pydantic import BaseModel, ConfigDict, Field
from typing import Annotated


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid")


class LoginInput(Input):
    username: str = Field(min_length=1, max_length=80)
    password: str = Field(min_length=1, max_length=128, repr=False)


class CreateUserInput(Input):
    username: str = Field(min_length=2, max_length=80, pattern=r"^[\w\-\u4e00-\u9fff]+$")
    password: str = Field(min_length=10, max_length=128, repr=False)
    roles: list[str] = Field(min_length=1, max_length=2)


class UpdateUserInput(Input):
    is_active: bool | None = None
    roles: list[str] | None = Field(default=None, min_length=1, max_length=2)


class NoteInput(Input):
    content: str = Field(min_length=1, max_length=5000)


class ProfileUpdateInput(Input):
    base_revision: int = Field(ge=1)
    field_changes: dict = Field(max_length=12)
    evidence_refs: dict[str, list[dict]]


class ReparseInput(Input):
    base_revision: int = Field(ge=1)


FactText = Annotated[str, Field(min_length=1, max_length=2000)]


class ProfileFacts(Input):
    name: str | None = Field(default=None, max_length=120)
    skills: list[FactText] = Field(default_factory=list, max_length=40)
    experience_months: int | None = Field(default=None, ge=0, le=720, strict=True)
    projects: list[FactText] = Field(default_factory=list, max_length=30)
    experiences: list[FactText] = Field(default_factory=list, max_length=30)
    education: list[FactText] = Field(default_factory=list, max_length=20)


class TagInput(Input):
    name: str = Field(min_length=1, max_length=60)


class TagsInput(Input):
    tag_ids: list[str] = Field(max_length=30)


class HardConditions(Input):
    required_skills: list[str] = Field(default_factory=list, max_length=30)
    min_experience_months: int | None = Field(default=None, ge=0, le=720)
    required_projects: list[str] = Field(default_factory=list, max_length=20)


class JobInput(Input):
    title: str = Field(min_length=1, max_length=150)
    raw_jd: str = Field(min_length=20, max_length=16000)
    hard_conditions: HardConditions = Field(default_factory=HardConditions)
    hard_confirmed: bool = False


class SearchInput(Input):
    question: str = Field(min_length=2, max_length=2000)
    filters: dict = Field(default_factory=dict, max_length=3)
