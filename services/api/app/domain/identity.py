"""Workspace identities and role types, without API or storage dependencies."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

Role = Literal["owner", "admin", "member", "viewer"]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Credentials(StrictModel):
    email: str = Field(min_length=5, max_length=254)
    password: str = Field(min_length=12, max_length=128)

    @field_validator("email")
    @classmethod
    def canonical_email(cls, email: str) -> str:
        import re

        value = email.strip().casefold()
        if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", value):
            raise ValueError("电子邮箱格式不正确")
        return value


class WorkspaceCreate(StrictModel):
    name: str = Field(min_length=2, max_length=100)


class MembershipCreate(StrictModel):
    user_email: str = Field(min_length=5, max_length=254)
    role: Literal["admin", "member", "viewer"]

    @field_validator("user_email")
    @classmethod
    def canonical_email(cls, value: str) -> str:
        return Credentials.canonical_email(value)


class Actor(StrictModel):
    id: str
    email: str


class WorkspaceScope(StrictModel):
    user_id: str
    workspace_id: str
    role: Role

    @property
    def can_edit(self) -> bool:
        return self.role in ("owner", "admin", "member")

    @property
    def can_manage_members(self) -> bool:
        return self.role in ("owner", "admin")
