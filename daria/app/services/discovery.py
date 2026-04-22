import asyncio
import os
import asyncpg
import logging
import json
from datetime import datetime
from typing import List, Dict, Optional
from pysnmp.smi import builder, view
from app.core.db import get_neo4j_driver, get_clickhouse_client
from pysnmp.hlapi.v3arch.asyncio import (
    get_cmd, set_cmd, next_cmd,
    CommunityData, UsmUserData, ContextData,
    usmHMACSHAAuthProtocol, usmHMACMD5AuthProtocol,
    usmAesCfb128Protocol, usmDESPrivProtocol,
    ObjectType, ObjectIdentity, SnmpEngine
)
from pysnmp.hlapi.v3arch.asyncio.transport import UdpTransportTarget

logger = logging.getLogger(__name__)

# MIB билдер для определения вендоров
mib_builder = builder.MibBuilder()
mib_builder.loadModules('SNMPv2-MIB', 'SNMPv2-SMI')
mib_view = view.MibViewController(mib_builder)

CONFIG_MIBS = {
    # Cisco
    'cisco': {
        'table': '1.3.6.1.4.1.9.9.96.1.1.1.1',
        'type_oid': 2,    # ciscoCopyProtocol
        'source_oid': 3,  # ciscoCopySourceFileType (1=running, 2=startup)
        'dest_oid': 4,    # ciscoCopyDestFileType
        'server_oid': 5,  # ciscoCopyServerAddress
        'file_oid': 6,    # ciscoCopyFileName
        'status_oid': 14, # ciscoCopyState
        'source_value': 1,  # running
        'protocol': 1,      # tftp
    },
    # Huawei
    'huawei': {
        'table': '1.3.6.1.4.1.2011.6.10.1.2.4.1',
        'type_oid': 2,    # hwCfgOperateType (3=running2Net)
        'protocol_oid': 3, # hwCfgOperateProtocol (2=tftp)
        'file_oid': 4,    # hwCfgOperateFileName
        'server_oid': 5,  # hwCfgOperateServerAddress
        'status_oid': 10, # hwCfgOperateState (3=success)
        'source_value': 3,
        'protocol': 2,
    },
    # Juniper (SNMP выгрузка есть, но сложная)
    'juniper': {
        'method': 'ssh',  # через SNMP проблематично, лучше SSH
    },
    # Arista
    'arista': {
        'method': 'ssh',
    },
    # Nokia/Alcatel-Lucent
    'nokia': {
        'table': '1.3.6.1.4.1.6527.3.1.2.4.1',
        'type_oid': 2,
        'file_oid': 4,
        'server_oid': 5,
        'status_oid': 10,
        'source_value': 3,
        'protocol': 1,
    },
    # H3C
    'h3c': {
        'table': '1.3.6.1.4.1.25506.2.1.1.1',
        'type_oid': 2,
        'file_oid': 4,
        'server_oid': 5,
        'status_oid': 10,
        'source_value': 3,
        'protocol': 2,
    },
    # Dell/Force10
    'dell': {
        'table': '1.3.6.1.4.1.6027.3.10.1.1',
        'type_oid': 2,
        'file_oid': 4,
        'server_oid': 5,
        'status_oid': 10,
        'source_value': 2,
        'protocol': 1,
    },
    # Extreme
    'extreme': {
        'table': '1.3.6.1.4.1.1916.1.2.1.1',
        'type_oid': 2,
        'file_oid': 4,
        'server_oid': 5,
        'status_oid': 10,
        'source_value': 2,
        'protocol': 1,
    },
    # Brocade
    'brocade': {
        'table': '1.3.6.1.4.1.1588.2.1.1.1',
        'type_oid': 2,
        'file_oid': 4,
        'server_oid': 5,
        'status_oid': 10,
        'source_value': 2,
        'protocol': 1,
    },
    # Fortinet
    'fortinet': {
        'method': 'ssh',  # FortiGate конфиги через SNMP не выгружаются
    },
    # MikroTik
    'mikrotik': {
        'method': 'ssh',  # RouterOS через SSH
    },
    # Eltex
    'eltex': {
        'table': '1.3.6.1.4.1.35265.1.1.1.1',
        'type_oid': 2,
        'file_oid': 4,
        'server_oid': 5,
        'status_oid': 10,
        'source_value': 3,
        'protocol': 2,
    },
    # ZTE
    'zte': {
        'table': '1.3.6.1.4.1.3902.3.10.1.1',
        'type_oid': 2,
        'file_oid': 4,
        'server_oid': 5,
        'status_oid': 10,
        'source_value': 3,
        'protocol': 2,
    },
    # Linux/Generic
    'linux': {
        'method': 'none',  # конфиги не собираем
    },
}

class DiscoveryEngine:
    def __init__(self):
        self.snmp_engine = SnmpEngine()
        self.semaphore = asyncio.Semaphore(50)

    async def _create_snmp_auth(self, device: dict, snmp_version: str):
        if snmp_version == "v3":
            # берём v3 настройки из .env
            return UsmUserData(
                os.getenv("SNMP_V3_USER", "daria"),
                os.getenv("SNMP_V3_USER", "daria"),
                authKey=os.getenv("SNMP_V3_AUTH_PASSWORD", ""),
                privKey=os.getenv("SNMP_V3_PRIV_PASSWORD", ""),
                authProtocol=usmHMACSHAAuthProtocol if os.getenv(
                    "SNMP_V3_AUTH_PROTOCOL") == "SHA" else usmHMACMD5AuthProtocol,
                privProtocol=usmAesCfb128Protocol if os.getenv(
                    "SNMP_V3_PRIV_PROTOCOL") == "AES" else usmDESPrivProtocol,
            )
        else:  # v1 или v2c
            mpModel = 1 if snmp_version == "v2c" else 0
            community = os.getenv("SNMP_COMMUNITY", "public")
            return CommunityData(community, mpModel=mpModel)

    async def collect_all_devices(self):
        """Сбор данных по всем устройствам из PostgreSQL"""
        conn = await asyncpg.connect(
            host=os.getenv("POSTGRES_HOST", "postgres"),
            database=os.getenv("POSTGRES_DB", "kontrollka"),
            user=os.getenv("POSTGRES_USER", "kontrollka"),
            password=os.getenv("POSTGRES_PASSWORD", "")
        )

        devices = await conn.fetch('SELECT id, name, host, device_type, snmp_version, "group" FROM devices')
        await conn.close()

        logger.info(f"Starting collection for {len(devices)} devices")

        for device in devices:
            try:
                await self._collect_device_data(device)
            except Exception as e:
                logger.error(f"Failed to collect {device['name']}: {e}")

    async def collect_single_device(self, device_id: str):
        """Сбор данных по одному устройству"""
        conn = await asyncpg.connect(
            host=os.getenv("POSTGRES_HOST", "postgres"),
            database=os.getenv("POSTGRES_DB", "kontrollka"),
            user=os.getenv("POSTGRES_USER", "kontrollka"),
            password=os.getenv("POSTGRES_PASSWORD", "")
        )

        device = await conn.fetchrow(
            'SELECT id, name, host, device_type, snmp_version, "group" FROM devices WHERE id = $1',
            int(device_id)
        )
        await conn.close()

        if not device:
            logger.error(f"Device {device_id} not found")
            return

        await self._collect_device_data(device, device.get("snmp_version", "v2c"))
        logger.info(f"Collected data for {device['name']}")

    async def _collect_device_data(self, device, snmp_version: str = "v2c"):
        ip = device["host"]

        # 👇 Используем snmp_version для аутентификации
        auth = await self._create_snmp_auth(device, snmp_version)

        data = {
            "ip": ip,
            "name": device["name"],
            "device_type": device["device_type"],
            "firmware": await self._get_firmware(ip, auth),
            "serial": await self._get_serial(ip, auth),
            "vendor": await self._get_vendor(ip, auth),
            "location": await self._get_location(ip, auth),
            "contact": await self._get_contact(ip, auth),
            "interfaces": await self._get_interfaces(ip, auth),
            "neighbors": await self._get_neighbors(ip, auth),
            "config": await self._get_config(ip, auth, device.get("device_type", "")),
            "last_collected": datetime.now()
        }

        await self._save_to_neo4j(data)
        await self._save_to_clickhouse(data)

    async def _get_firmware(self, ip: str, auth) -> str:
        """Версия прошивки через SNMP (sysDescr)"""
        result = await self._snmp_get(ip, auth, "1.3.6.1.2.1.1.1.0")
        return result or "Unknown"

    async def _get_serial(self, ip: str, auth) -> str:
        """Серийный номер через SNMP (entPhysicalSerialNum)"""
        result = await self._snmp_get(ip, auth, "1.3.6.1.2.1.47.1.1.1.1.11.1")
        return result or "Unknown"

    async def _get_vendor(self, ip: str, auth) -> str:
        """Вендор через sysObjectID"""
        sys_object_id = await self._snmp_get(ip, auth, "1.3.6.1.2.1.1.2.0")
        if sys_object_id:
            return self._vendor_from_oid(sys_object_id)
        return "Unknown"

    async def _get_location(self, ip: str, auth) -> str:
        """Локация устройства"""
        result = await self._snmp_get(ip, auth, "1.3.6.1.2.1.1.6.0")
        return result or ""

    async def _get_contact(self, ip: str, auth) -> str:
        """Контактное лицо"""
        result = await self._snmp_get(ip, auth, "1.3.6.1.2.1.1.4.0")
        return result or ""

    async def _get_interfaces(self, ip: str, auth) -> List[Dict]:
        """Список интерфейсов с ошибками"""
        interfaces = []

        if_number = await self._snmp_get(ip, auth, "1.3.6.1.2.1.2.1.0")
        if not if_number:
            return interfaces

        for i in range(1, int(if_number) + 1):
            iface = {
                "index": i,
                "name": await self._snmp_get(ip, auth, f"1.3.6.1.2.1.2.2.1.2.{i}"),
                "type": await self._snmp_get(ip, auth, f"1.3.6.1.2.1.2.2.1.3.{i}"),
                "speed": await self._snmp_get(ip, auth, f"1.3.6.1.2.1.2.2.1.5.{i}"),
                "in_errors": int(await self._snmp_get(ip, auth, f"1.3.6.1.2.1.2.2.1.14.{i}") or 0),  # ifInErrors
                "out_errors": int(await self._snmp_get(ip, auth, f"1.3.6.1.2.1.2.2.1.20.{i}") or 0),  # ifOutErrors
                "in_discards": int(await self._snmp_get(ip, auth, f"1.3.6.1.2.1.2.2.1.13.{i}") or 0),  # ifInDiscards
                "out_discards": int(await self._snmp_get(ip, auth, f"1.3.6.1.2.1.2.2.1.19.{i}") or 0),  # ifOutDiscards
            }
            interfaces.append(iface)

        return interfaces

    async def _get_neighbors(self, ip: str, auth) -> List[Dict]:
        """LLDP/CDP соседи"""
        neighbors = []

        # Пробуем CDP (Cisco)
        cdp = await self._get_cdp_neighbors(ip, auth)
        if cdp:
            return cdp

        # Пробуем LLDP
        lldp = await self._get_lldp_neighbors(ip, auth)
        if lldp:
            return lldp

        return neighbors

    async def _get_cdp_neighbors(self, ip: str, auth) -> List[Dict]:
        """CDP соседи"""
        neighbors = []
        base_oid = "1.3.6.1.4.1.9.9.23.1.2.1.1"

        for index in range(1, 100):
            device_id = await self._snmp_get(ip, auth, f"{base_oid}.6.{index}")
            if not device_id:
                break

            local_port = await self._snmp_get(ip, auth, f"{base_oid}.7.{index}")
            platform = await self._snmp_get(ip, auth, f"{base_oid}.8.{index}")

            neighbors.append({
                "protocol": "CDP",
                "neighbor_id": device_id,
                "local_port": local_port or "unknown",
                "remote_port": "unknown",
                "platform": platform or "unknown"
            })

        return neighbors

    async def _get_lldp_neighbors(self, ip: str, auth) -> List[Dict]:
        """LLDP соседи"""
        neighbors = []
        base_oid = "1.0.8802.1.1.2.1.4.1.1"

        for index in range(1, 100):
            chassis = await self._snmp_get(ip, auth, f"{base_oid}.5.{index}")
            if not chassis:
                break

            remote_port = await self._snmp_get(ip, auth, f"{base_oid}.6.{index}")
            local_port = await self._snmp_get(ip, auth, f"{base_oid}.7.{index}")
            sys_name = await self._snmp_get(ip, auth, f"{base_oid}.9.{index}")

            neighbors.append({
                "protocol": "LLDP",
                "neighbor_id": sys_name or chassis,
                "neighbor_chassis": chassis,
                "local_port": local_port or "unknown",
                "remote_port": remote_port or "unknown",
                "platform": "unknown"
            })

        return neighbors

    async def _get_config(self, ip: str, auth, device_type: str) -> str:
        """Универсальный сбор конфигурации через SNMP или SSH"""

        # Определяем вендора по device_type
        vendor = device_type.split('_')[0]

        mib = CONFIG_MIBS.get(vendor)
        if not mib:
            # Пробуем по префиксам
            for v, cfg in CONFIG_MIBS.items():
                if device_type.startswith(v):
                    mib = cfg
                    vendor = v
                    break

        if not mib:
            logger.warning(f"No config method for {device_type}")
            return ""

        # Проверяем метод сбора
        method = mib.get('method', 'snmp')

        if method == 'snmp':
            config = await self._get_config_snmp(ip, auth, mib)
            if config:
                return config
            # Fallback на SSH
            return await self._get_config_ssh(ip, device_type)
        elif method == 'ssh':
            return await self._get_config_ssh(ip, device_type)
        else:
            return ""

    async def _get_config_snmp(self, ip: str, auth, mib: dict) -> Optional[str]:
        """Универсальная SNMP выгрузка конфига"""

        tftp_server = os.getenv("TFTP_SERVER", "")
        if not tftp_server:
            logger.warning("TFTP_SERVER not set, skipping SNMP config")
            return None

        import random, time
        index = random.randint(1, 65535)
        filename = f"config_{ip.replace('.', '_')}_{int(time.time())}.cfg"

        base = mib['table']

        try:
            # Тип операции (running config)
            await self._snmp_set(ip, auth, f"{base}.{mib['type_oid']}.{index}", mib['source_value'])

            # Протокол (tftp)
            if 'protocol_oid' in mib:
                await self._snmp_set(ip, auth, f"{base}.{mib['protocol_oid']}.{index}", mib['protocol'])

            # Имя файла
            await self._snmp_set(ip, auth, f"{base}.{mib['file_oid']}.{index}", filename)

            # TFTP сервер
            await self._snmp_set(ip, auth, f"{base}.{mib['server_oid']}.{index}", tftp_server)

            # Запуск
            status_oid = f"{base}.{mib['status_oid']}.{index}"
            await self._snmp_set(ip, auth, status_oid, 4)  # createAndGo

            # Ждём завершения
            for _ in range(30):
                state = await self._snmp_get(ip, auth, status_oid)
                if state == '3':  # success
                    break
                elif state == '4':  # failed
                    error_oid = f"{base}.13.{index}" if mib.get('error_oid') else None
                    if error_oid:
                        error = await self._snmp_get(ip, auth, error_oid)
                        raise Exception(f"Config copy failed: {error}")
                    raise Exception("Config copy failed")
                await asyncio.sleep(1)

            # Читаем файл
            config = await self._read_tftp_file(filename)

            # Очищаем
            await self._snmp_set(ip, auth, status_oid, 6)  # destroy

            return config

        except Exception as e:
            logger.error(f"SNMP config failed for {ip}: {e}")
            return None

    async def _get_config_ssh(self, ip: str, device_type: str) -> str:
        """SSH fallback"""
        from netmiko import ConnectHandler

        commands = {
            # Cisco
            'cisco': 'show running-config',
            'cisco_ios': 'show running-config',
            'cisco_nxos': 'show running-config',
            'cisco_xr': 'show running-config',
            'cisco_asa': 'show running-config',
            # Huawei
            'huawei': 'display current-configuration',
            'huawei_vrpv8': 'display current-configuration',
            'huawei_olt': 'display current-configuration',
            # Juniper
            'juniper': 'show configuration | display set',
            # Arista
            'arista': 'show running-config',
            'arista_eos': 'show running-config',
            # HP/Aruba
            'hp_procurve': 'show running-config',
            'hp_comware': 'display current-configuration',
            'aruba_os': 'show running-config',
            # Dell
            'dell_force10': 'show running-config',
            'dell_os10': 'show running-config',
            'dell_powerconnect': 'show running-config',
            # Extreme
            'extreme_exos': 'show configuration',
            'extreme_ers': 'show config',
            'extreme_nos': 'show running-config',
            # Nokia/Alcatel
            'alcatel_sros': 'show configuration',
            # Brocade
            'brocade_fastiron': 'show running-config',
            'brocade_netiron': 'show running-config',
            # Fortinet
            'fortinet': 'show full-configuration',
            # MikroTik
            'mikrotik_routeros': 'export',
            # Eltex
            'eltex': 'show running-config',
            'eltex_esr': 'show running-config',
            # ZTE
            'zte': 'show running-config',
            # Linux
            'linux': 'cat /etc/passwd',  # не храним конфиги серверов
        }

        cmd = commands.get(device_type.split('_')[0], 'show running-config')

        try:
            connection = ConnectHandler(
                device_type=device_type,
                host=ip,
                username=os.getenv("DEVICE_USERNAME"),
                password=os.getenv("DEVICE_PASSWORD"),
                timeout=30,
            )
            config = connection.send_command(cmd, expect_string=r'[>#]')
            connection.disconnect()
            return config
        except Exception as e:
            logger.error(f"SSH config failed for {ip}: {e}")
            return ""

    async def _read_tftp_file(self, filename: str) -> str:
        """Чтение файла с TFTP сервера"""
        tftp_server = os.getenv("TFTP_SERVER", "")
        tftp_dir = os.getenv("TFTP_DIR", "/var/lib/tftpboot")

        # Ждём появления файла
        for _ in range(10):
            filepath = os.path.join(tftp_dir, filename)
            if os.path.exists(filepath):
                with open(filepath, 'r') as f:
                    content = f.read()
                os.remove(filepath)
                return content
            await asyncio.sleep(0.5)

        return ""

    async def _snmp_set(self, ip: str, auth, oid: str, value) -> Optional[bool]:
        async with self.semaphore:
            try:
                error_indication, error_status, error_index, var_binds = await set_cmd(
                    self.snmp_engine,
                    auth,
                    UdpTransportTarget.create((ip, 161)),
                    ContextData(),
                    ObjectType(ObjectIdentity(oid), value),
                    timeout=5,
                    retries=2
                )
                return error_indication is None and error_status == 0
            except Exception as e:
                logger.error(f"SNMP SET failed for {ip}: {e}")
                return False

    async def _snmp_walk(self, ip: str, auth, base_oid: str) -> Optional[List[str]]:
        results = []
        try:
            iterator = next_cmd(
                self.snmp_engine,
                auth,
                UdpTransportTarget.create((ip, 161)),
                ContextData(),
                ObjectType(ObjectIdentity(base_oid)),
                timeout=3,
                retries=2
            )
            async for error_indication, error_status, error_index, var_binds in iterator:
                if error_indication or error_status:
                    break
                for var_bind in var_binds:
                    results.append(str(var_bind[1]))
        except Exception as e:
            logger.debug(f"SNMP walk failed for {ip}: {e}")
            return None
        return results if results else None

    async def _snmp_get(self, ip: str, auth, oid: str) -> Optional[str]:
        async with self.semaphore:
            try:
                error_indication, error_status, error_index, var_binds = await get_cmd(
                    self.snmp_engine,
                    auth,
                    UdpTransportTarget.create((ip, 161)),
                    ContextData(),
                    ObjectType(ObjectIdentity(oid)),
                    timeout = 2,
                    retries = 1
                )
                if error_indication or error_status:
                    return None
                for var_bind in var_binds:
                    return str(var_bind[1])
            except Exception:
                return None
            return None

    def _vendor_from_oid(self, sys_object_id: str) -> str:
        """Определение вендора по sysObjectID"""
        vendors = {
            "1.3.6.1.4.1.9": "Cisco",
            "1.3.6.1.4.1.2011": "Huawei",
            "1.3.6.1.4.1.2636": "Juniper",
            "1.3.6.1.4.1.30065": "Arista",
            "1.3.6.1.4.1.6527": "Nokia",
            "1.3.6.1.4.1.25506": "H3C",
            "1.3.6.1.4.1.674": "Dell",
            "1.3.6.1.4.1.11": "HP",
            "1.3.6.1.4.1.45": "Aruba",
            "1.3.6.1.4.1.12356": "Fortinet",
            "1.3.6.1.4.1.25461": "PaloAlto",
            "1.3.6.1.4.1.2620": "CheckPoint",
            "1.3.6.1.4.1.1916": "Extreme",
            "1.3.6.1.4.1.1588": "Brocade",
            "1.3.6.1.4.1.35265": "Eltex",
            "1.3.6.1.4.1.14988": "MikroTik",
            "1.3.6.1.4.1.41112": "Ubiquiti",
            "1.3.6.1.4.1.8072": "Linux",
            "1.3.6.1.4.1.12325": "FreeBSD",
            "1.3.6.1.4.1.6876": "VMware",
        }

        for oid, vendor in vendors.items():
            if sys_object_id.startswith(oid):
                return vendor
        return "Unknown"

    async def _save_to_neo4j(self, data: dict):
        """Сохранение в Neo4j"""
        driver = get_neo4j_driver()
        async with driver.session() as session:
            await session.run("""
                MERGE (d:Device {ip: $ip})
                SET d.name = $name,
                    d.device_type = $device_type,
                    d.vendor = $vendor,
                    d.firmware = $firmware,
                    d.serial = $serial,
                    d.location = $location,
                    d.contact = $contact,
                    d.config = $config,
                    d.last_collected = $last_collected
            """, **data)

            for iface in data.get("interfaces", []):
                await session.run("""
                    MATCH (d:Device {ip: $ip})
                    MERGE (i:Interface {device_ip: $ip, name: $name})
                    SET i.index = $index,
                        i.type = $type,
                        i.speed = $speed,
                        i.admin_status = $admin_status,
                        i.oper_status = $oper_status
                """, ip=data["ip"], **iface)

            for neighbor in data.get("neighbors", []):
                await session.run("""
                    MATCH (d:Device {ip: $ip})
                    MERGE (n:Device {ip: $neighbor_id})
                    MERGE (d)-[r:CONNECTS_TO {local_port: $local_port}]->(n)
                    SET r.protocol = $protocol,
                        r.remote_port = $remote_port,
                        r.last_seen = datetime()
                """, ip=data["ip"], **neighbor)

    async def _save_to_clickhouse(self, data: dict):
        """Сохранение в ClickHouse"""
        client = get_clickhouse_client()

        client.execute("""
            INSERT INTO device_snapshots (
                ip, name, device_type, vendor, firmware, serial,
                location, contact, config, interfaces_count, last_collected
            ) VALUES
        """, [{
            "ip": data["ip"],
            "name": data["name"],
            "device_type": data["device_type"],
            "vendor": data["vendor"],
            "firmware": data["firmware"],
            "serial": data["serial"],
            "location": data["location"],
            "contact": data["contact"],
            "config": data.get("config", ""),
            "interfaces_count": len(data.get("interfaces", [])),
            "last_collected": data["last_collected"]
        }])

        for iface in data.get("interfaces", []):
            client.execute("""
                INSERT INTO interface_history (
                    device_ip, interface_name, interface_index,
                    interface_type, speed, admin_status, oper_status, collected_at
                ) VALUES
            """, [{
                "device_ip": data["ip"],
                "interface_name": iface["name"],
                "interface_index": iface["index"],
                "interface_type": iface["type"],
                "speed": iface.get("speed"),
                "admin_status": iface["admin_status"],
                "oper_status": iface["oper_status"],
                "collected_at": data["last_collected"]
            }])

        try:
            import redis.asyncio as redis
            r = await redis.from_url(os.getenv("REDIS_URL", "redis://redis:6379"))
            await r.publish("daria:device:updated", json.dumps({
                "device_id": data.get("id") or data.get("ip"),
                "device_ip": data["ip"],
                "device_name": data["name"],
                "timestamp": datetime.now().isoformat()
            }))
        except Exception as e:
            logger.warning(f"Failed to publish to Redis: {e}")