from fastapi import APIRouter, HTTPException, Query
from typing import List, Optional
from app.core.db import get_neo4j_driver
from app.models.device import DeviceResponse

router = APIRouter()


@router.get("/", response_model=List[DeviceResponse])
async def get_devices(
        limit: int = Query(100, ge=1, le=1000),
        offset: int = Query(0, ge=0),
        vendor: Optional[str] = None
):
    driver = get_neo4j_driver()

    if vendor:
        query = "MATCH (d:Device {vendor: $vendor}) RETURN d SKIP $offset LIMIT $limit"
        params = {"vendor": vendor, "offset": offset, "limit": limit}
    else:
        query = "MATCH (d:Device) RETURN d SKIP $offset LIMIT $limit"
        params = {"offset": offset, "limit": limit}

    async with driver.session() as session:
        result = await session.run(query, params)
        devices = []
        async for record in result:
            d = record["d"]
            devices.append(DeviceResponse(
                id=d.get("ip"),
                ip=d.get("ip"),
                name=d.get("name"),
                vendor=d.get("vendor"),
                device_type=d.get("device_type"),
                sys_descr=d.get("sys_descr"),
                sys_object_id=d.get("sys_object_id"),
                community=d.get("community"),
                location=d.get("location"),
                contact=d.get("contact"),
                last_seen=d.get("last_seen")
            ))
        return devices


@router.get("/{device_id}", response_model=DeviceResponse)
async def get_device(device_id: str):
    driver = get_neo4j_driver()
    async with driver.session() as session:
        result = await session.run(
            "MATCH (d:Device {ip: $ip}) RETURN d",
            ip=device_id
        )
        record = await result.single()
        if not record:
            raise HTTPException(status_code=404, detail="Device not found")
        d = record["d"]
        return DeviceResponse(
            id=d.get("ip"),
            ip=d.get("ip"),
            name=d.get("name"),
            vendor=d.get("vendor"),
            device_type=d.get("device_type"),
            sys_descr=d.get("sys_descr"),
            sys_object_id=d.get("sys_object_id"),
            community=d.get("community"),
            location=d.get("location"),
            contact=d.get("contact"),
            last_seen=d.get("last_seen")
        )