from fastapi import APIRouter, HTTPException, BackgroundTasks
from app.models.device import ScanRequest, ScanStatus
from app.services.discovery import DiscoveryEngine
import uuid
from pydantic import BaseModel

router = APIRouter()
engine = DiscoveryEngine()

@router.post("/scan", response_model=ScanStatus)
async def start_scan(request: ScanRequest, background_tasks: BackgroundTasks):
    task_id = str(uuid.uuid4())
    background_tasks.add_task(engine.start_scan, task_id, request.dict())
    return ScanStatus(task_id=task_id, status="started", devices_found=0)


@router.post("/collect/{device_id}")
async def collect_device_manual(device_id: str, background_tasks: BackgroundTasks):
    """Ручной сбор данных по одному устройству"""
    from app.services.discovery import DiscoveryEngine
    engine = DiscoveryEngine()

    background_tasks.add_task(engine.collect_single_device, device_id)

    return {
        "status": "started",
        "device_id": device_id,
        "message": f"Сбор данных для {device_id} запущен"
    }

@router.post("/collect-all")
async def collect_all_devices_manual(background_tasks: BackgroundTasks):
    """Ручной запуск сбора по всем устройствам"""
    from app.services.discovery import DiscoveryEngine
    engine = DiscoveryEngine()

    background_tasks.add_task(engine.collect_all_devices)

    return {
        "status": "started",
        "message": "Сбор данных по всем устройствам запущен"
    }

@router.get("/scan/{task_id}/status", response_model=ScanStatus)
async def get_scan_status(task_id: str):
    status = engine.get_status(task_id)
    if not status:
        raise HTTPException(status_code=404, detail="Task not found")
    return ScanStatus(task_id=task_id, **status)

class CollectRequest(BaseModel):
    snmp_version: str = "v2c"

@router.post("/collect/{device_id}")
async def collect_device_manual(device_id: str, request: CollectRequest, background_tasks: BackgroundTasks):
    background_tasks.add_task(engine.collect_single_device, device_id, request.snmp_version)