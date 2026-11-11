"""Base model: snake_case in Python, camelCase on the wire and in DynamoDB."""

from __future__ import annotations

from typing import Any, Self

from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel


class RpModel(BaseModel):
    """All contract models derive from this.

    - Python attributes are snake_case; JSON/DynamoDB/S3 use camelCase aliases.
    - Unknown keys are ignored on read so older records stay readable.
    """

    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        extra="ignore",
        use_enum_values=False,
    )

    def dump(self, *, exclude_none: bool = True) -> dict[str, Any]:
        """JSON-safe dict with camelCase keys (what goes on the wire)."""
        return self.model_dump(by_alias=True, exclude_none=exclude_none, mode="json")

    def dump_json(self, *, exclude_none: bool = True) -> str:
        return self.model_dump_json(by_alias=True, exclude_none=exclude_none)

    @classmethod
    def load(cls, data: dict[str, Any]) -> Self:
        return cls.model_validate(data)
