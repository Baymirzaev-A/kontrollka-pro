from fastapi import APIRouter, HTTPException, Query
from typing import List, Optional, Dict, Any
from app.core.db import get_neo4j_driver, get_clickhouse_client
from app.models.device import DeviceResponse
import logging

logger = logging.getLogger(__name__)

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


@router.get("/{device_id}")
async def get_device(device_id: str) -> Dict[str, Any]:
    """Получить полную информацию об устройстве из ClickHouse и Neo4j"""
    driver = get_neo4j_driver()
    clickhouse = get_clickhouse_client()

    # 1. Базовые данные из Neo4j
    async with driver.session() as session:
        result = await session.run(
            "MATCH (d:Device {ip: $ip}) RETURN d",
            ip=device_id
        )
        record = await result.single()
        if not record:
            raise HTTPException(status_code=404, detail="Device not found")
        d = record["d"]

    # 2. Данные SNMP из ClickHouse (последний снапшот)
    snmp_data = {
        "firmware": "Unknown",
        "serial": "Unknown",
        "interfaces": [],
        "neighbors": [],
        "config": "",
        "last_collected": None
    }

    try:
        # Получаем последний снапшот
        snapshot_result = clickhouse.execute("""
            SELECT firmware, serial, config, last_collected, interfaces_count
            FROM device_snapshots 
            WHERE ip = %(ip)s 
            ORDER BY last_collected DESC 
            LIMIT 1
        """, {"ip": device_id})

        if snapshot_result:
            row = snapshot_result[0]
            snmp_data["firmware"] = row[0] or "Unknown"
            snmp_data["serial"] = row[1] or "Unknown"
            snmp_data["config"] = row[2] or ""
            snmp_data["last_collected"] = row[3]

        # Получаем интерфейсы из истории
        interfaces_result = clickhouse.execute("""
            SELECT DISTINCT 
                interface_name as name,
                interface_index as index,
                interface_type as type,
                speed,
                admin_status,
                oper_status,
                in_errors,
                out_errors,
                in_discards,
                out_discards
            FROM interface_history 
            WHERE device_ip = %(ip)s 
            ORDER BY collected_at DESC
            LIMIT 1000
        """, {"ip": device_id})

        snmp_data["interfaces"] = []
        for row in interfaces_result:
            snmp_data["interfaces"].append({
                "name": row[0],
                "index": row[1],
                "type": row[2],
                "speed": row[3],
                "admin_status": row[4],
                "oper_status": row[5],
                "in_errors": row[6] or 0,
                "out_errors": row[7] or 0,
                "in_discards": row[8] or 0,
                "out_discards": row[9] or 0
            })

        # LLDP/CDP соседи (если есть в ClickHouse, иначе из Neo4j)
        # Пока оставим из Neo4j, но можно добавить таблицу

    except Exception as e:
        logger.error(f"ClickHouse query failed: {e}")

    # 3. Соседей пока берем из Neo4j (если есть)
    neighbors = []
    async with driver.session() as session:
        neighbors_result = await session.run("""
            MATCH (d:Device {ip: $ip})-[r:CONNECTS_TO]->(n:Device)
            RETURN n.ip as neighbor_id, r.local_port as local_port, r.remote_port as remote_port, r.protocol as protocol
        """, ip=device_id)
        async for record in neighbors_result:
            neighbors.append({
                "neighbor_id": record["neighbor_id"],
                "local_port": record["local_port"] or "unknown",
                "remote_port": record["remote_port"] or "unknown",
                "protocol": record["protocol"] or "unknown"
            })

    # 4. Формируем полный ответ
    return {
        "ip": d.get("ip"),
        "name": d.get("name"),
        "vendor": d.get("vendor") or snmp_data.get("vendor") or "Unknown",
        "device_type": d.get("device_type"),
        "sys_descr": d.get("sys_descr"),
        "sys_object_id": d.get("sys_object_id"),
        "community": d.get("community"),
        "location": d.get("location") or snmp_data.get("location", ""),
        "contact": d.get("contact") or snmp_data.get("contact", ""),
        "last_seen": d.get("last_seen"),
        "snmp_version": d.get("snmp_version", "v2c"),
        "snmp_v3_config": d.get("snmp_v3_config"),
        "firmware": snmp_data["firmware"],
        "serial": snmp_data["serial"],
        "interfaces": snmp_data["interfaces"],
        "neighbors": neighbors,
        "config": snmp_data["config"],
        "last_collected": snmp_data["last_collected"]
    }