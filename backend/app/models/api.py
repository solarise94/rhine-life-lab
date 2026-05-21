from __future__ import annotations

from pydantic import BaseModel


class StartRunBlockedDetail(BaseModel):
    message: str
    block_details: dict
