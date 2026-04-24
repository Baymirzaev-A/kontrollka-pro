from fastapi import APIRouter, HTTPException, BackgroundTasks
from app.models.device import ScanRequest, ScanStatus
from app.services.discovery import DiscoveryEngine
from app.core.db import get_clickhouse_client
import uuid

router = APIRouter()
engine = DiscoveryEngine()


@router.post("/scan", response_model=ScanStatus)
async def start_scan(request: ScanRequest, background_tasks: BackgroundTasks):
    task_id = str(uuid.uuid4())
    background_tasks.add_task(engine.start_scan, task_id, request.dict())
    return ScanStatus(task_id=task_id, status="started", devices_found=0)


@router.get("/scan/{task_id}/status", response_model=ScanStatus)
async def get_scan_status(task_id: str):
    status = engine.get_status(task_id)
    if not status:
        raise HTTPException(status_code=404, detail="Task not found")
    return ScanStatus(task_id=task_id, **status)


@router.get("/device/{ip}/configs")
async def get_device_configs(ip: str, per_page: int = 100):
    """Получить историю конфигов устройства из ClickHouse"""
    clickhouse = get_clickhouse_client()

    result = clickhouse.execute("""
        SELECT config, last_collected 
        FROM device_snapshots 
        WHERE ip = %(ip)s 
        ORDER BY last_collected DESC
        LIMIT %(limit)s
    """, {"ip": ip, "limit": per_page})

    items = []
    for idx, row in enumerate(result):
        items.append({
            "id": idx,
            "config_text": row[0],
            "saved_at": row[1].isoformat() if row[1] else None,
            "saved_by": "DARIA"
        })

    return {"items": items, "total": len(items), "page": 1, "pages": 1}

@router.post("/collect/{device_id}")
async def collect_device_manual(device_id: str, background_tasks: BackgroundTasks):
    background_tasks.add_task(engine.collect_single_device, device_id)
    return {"status": "started", "device_id": device_id}


@router.post("/collect-all")
async def collect_all_devices_manual():
    await engine.collect_all_devices()
    return {"status": "completed"}