from fastapi import APIRouter, BackgroundTasks, HTTPException
from pydantic import BaseModel, validator
from datetime import datetime
from typing import Optional
from app.core.db import get_clickhouse_client

router = APIRouter()


class TrapIngest(BaseModel):
    timestamp: str
    host: str
    type: str
    data: str
    community: Optional[str] = "public"

    @validator('timestamp')
    def validate_timestamp(cls, v):
        try:
            datetime.strptime(v, "%Y-%m-%d %H:%M:%S")
        except ValueError:
            raise ValueError('Invalid timestamp format, expected YYYY-MM-DD HH:MM:SS')
        return v


@router.post("/ingest")
async def ingest_trap(trap: TrapIngest, background_tasks: BackgroundTasks):
    background_tasks.add_task(_save_trap, trap)
    return {"status": "accepted"}


async def _save_trap(trap: TrapIngest):
    client = get_clickhouse_client()
    client.execute(
        "INSERT INTO traps (timestamp, host, type, data, community) VALUES",
        [{
            "timestamp": trap.timestamp,
            "host": trap.host,
            "type": trap.type,
            "data": trap.data,
            "community": trap.community
        }]
    )