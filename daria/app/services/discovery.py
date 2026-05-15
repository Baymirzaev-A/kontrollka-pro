import asyncio
import os
import asyncpg
import logging
import json
import subprocess
from datetime import datetime
from typing import List, Dict, Optional
from app.core.db import get_neo4j_driver, get_clickhouse_client

logger = logging.getLogger(__name__)

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

from app.services.oid_resolver import OIDResolver

class DiscoveryEngine:
    def __init__(self):
        self.semaphore = asyncio.Semaphore(200)
        self.ssh_semaphore = asyncio.Semaphore(50)
        self.tasks = {}
        self.oid_resolver = OIDResolver()

    def clean_value(self, value):
        if not value:
            return ""
        value = value.strip()
        if value.startswith('"') and value.endswith('"'):
            value = value[1:-1]
        if value.startswith("'") and value.endswith("'"):
            value = value[1:-1]
        value = value.replace('\\"', '"')
        return value

    async def _get_vendor(self, ip: str, snmp_version: str) -> str:
        """Определяет вендора по sysObjectID"""
        sys_object_id = await self._snmp_get(ip, snmp_version, "1.3.6.1.2.1.1.2.0")
        if not sys_object_id:
            return "Unknown"

        sys_object_id = sys_object_id.replace("iso.", "")
        logger.info(f"sysObjectID for {ip}: {sys_object_id}")

        # Проверяем вхождение OID вендора
        if "1.3.6.1.4.1.2011" in sys_object_id or "3.6.1.4.1.2011" in sys_object_id:
            return "Huawei"
        elif "1.3.6.1.4.1.9" in sys_object_id or "3.6.1.4.1.9" in sys_object_id:
            return "Cisco"
        elif "1.3.6.1.4.1.2636" in sys_object_id or "3.6.1.4.1.2636" in sys_object_id:
            return "Juniper"
        elif "1.3.6.1.4.1.30065" in sys_object_id or "3.6.1.4.1.30065" in sys_object_id:
            return "Arista"
        elif "1.3.6.1.4.1.6527" in sys_object_id or "3.6.1.4.1.6527" in sys_object_id:
            return "Nokia"
        return "Unknown"

    async def _get_vendor_name(self, ip: str, snmp_version: str) -> str:
        """Определяет вендора для OIDResolver (возвращает ключ для YAML)"""
        vendor = await self._get_vendor(ip, snmp_version)
        vendor_lower = vendor.lower()
        if vendor_lower in ["huawei", "cisco", "juniper", "arista", "nokia"]:
            return vendor_lower
        return "default"

    async def _get_full_firmware(self, ip: str, snmp_version: str) -> str:
        """Возвращает полную версию ПО"""
        result = await self._snmp_get(ip, snmp_version, "1.3.6.1.2.1.1.1.0")
        if not result or "No Such" in result:
            return "Unknown"
        result = self.clean_value(result)
        import re
        match = re.search(r'Version\s+(.+?)(?:\"|$)', result, re.IGNORECASE)
        if match:
            return match.group(1).strip()
        return result[:100]

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
        errors = {}

        # Получаем все данные через SNMP
        try:
            firmware = await self._get_full_firmware(ip, snmp_version)
        except Exception as e:
            firmware = "Unknown"
            errors["firmware"] = str(e)

        try:
            serial = await self._get_serial(ip, snmp_version)
        except Exception as e:
            serial = "Unknown"
            errors["serial"] = str(e)

        try:
            vendor = await self._get_vendor(ip, snmp_version)
        except Exception as e:
            vendor = "Unknown"
            errors["vendor"] = str(e)

        try:
            location = await self._get_location(ip, snmp_version)
        except Exception as e:
            location = ""
            errors["location"] = str(e)

        try:
            contact = await self._get_contact(ip, snmp_version)
        except Exception as e:
            contact = ""
            errors["contact"] = str(e)

        try:
            interfaces = await self._get_interfaces(ip, snmp_version)
        except Exception as e:
            interfaces = []
            errors["interfaces"] = str(e)

        try:
            neighbors = await self._get_neighbors(ip, snmp_version)
        except Exception as e:
            neighbors = []
            errors["neighbors"] = str(e)

        # Конфиг
        try:
            config = await self._get_config(ip, snmp_version, device.get("device_type", ""))
            if not config or len(config) < 100:
                config = await self._get_config_ssh(ip, device.get("device_type", ""))
                if config and len(config) >= 100:
                    logger.info(f"SSH config collected for {ip}")
                else:
                    logger.warning(f"No config for {ip}")
                    errors["config"] = "No config collected"
                    config = ""
        except Exception as e:
            config = ""
            errors["config"] = str(e)

        current_time = datetime.now()
        sysname = await self._get_sysname(ip, snmp_version)

        data = {
            "ip": ip,
            "name": device["name"],
            "device_type": device["device_type"],
            "firmware": firmware or "Unknown",
            "serial": serial or "Unknown",
            "vendor": vendor or "Unknown",
            "location": location or "",
            "contact": contact or "",
            "interfaces": interfaces or [],
            "neighbors": neighbors or [],
            "sysname": sysname,
            "config": config or "",
            "last_collected": current_time
        }

        logger.info(f"Final config for {ip}: length={len(data['config'])}")
        logger.info(
            f"Saving to Neo4j and ClickHouse: {len(interfaces)} interfaces, firmware={firmware}, serial={serial}")

        await self._save_to_neo4j(data)
        await self._save_to_clickhouse(data)

        if errors:
            logger.warning(f"Partial collection for {ip}: {errors}")

    async def _get_firmware(self, ip: str, snmp_version: str) -> str:
        result = await self._snmp_get(ip, snmp_version, "1.3.6.1.2.1.1.1.0")
        if not result or "No Such" in result:
            return "Unknown"
        result = self.clean_value(result)
        # Извлекаем номер версии (для Huawei)
        import re
        match = re.search(r'Version\s+(.+?)(?:\"|$)', result)
        if match:
            full_version = match.group(1).strip()
            # Можно оставить как есть, например "5.170 (S5731 V200R022C10SPC500)"
            return full_version
        # Для Cisco
        match = re.search(r'Version\s+(\S+)', result)
        if match:
            return match.group(1)
        # Если ничего не нашли — возвращаем первые 50 символов без кавычек
        return result.strip('"')[:50]

    async def _get_serial(self, ip: str, snmp_version: str) -> str:
        vendor = await self._get_vendor_name(ip, snmp_version)
        oids = self.oid_resolver.get_oid_list(vendor, 'system', 'serial')
        if not oids:
            oids = [
                "1.3.6.1.2.1.47.1.1.1.1.11.1",
                "1.3.6.1.4.1.2011.6.2.1.1.1.5",
            ]
        for oid in oids:
            result = await self._snmp_get(ip, snmp_version, oid)
            if result and "No Such" not in result:
                return result.strip()
        return "Unknown"

    async def _get_location(self, ip: str, snmp_version: str) -> str:
        result = await self._snmp_get(ip, snmp_version, "1.3.6.1.2.1.1.6.0")
        return self.clean_value(result) or ""

    async def _get_contact(self, ip: str, snmp_version: str) -> str:
        result = await self._snmp_get(ip, snmp_version, "1.3.6.1.2.1.1.4.0")
        return self.clean_value(result) or ""

    async def _get_sysname(self, ip: str, snmp_version: str) -> str:
        result = await self._snmp_get(ip, snmp_version, "1.3.6.1.2.1.1.5.0")
        return self.clean_value(result) or ""

    async def _get_interfaces(self, ip: str, snmp_version: str) -> List[Dict]:
        """Список интерфейсов с поддержкой разных вендоров"""
        interfaces = []
        vendor = await self._get_vendor_name(ip, snmp_version)

        # Получаем все имена интерфейсов через snmpwalk
        name_oids = await self._snmp_walk(ip, snmp_version, "1.3.6.1.2.1.2.2.1.2")
        if not name_oids:
            return interfaces

        async def get_value(field: str, if_index: int, default=0) -> int:
            oids = self.oid_resolver.get_oid_list(vendor, 'interfaces', field, if_index)
            for oid in oids:
                val = await self._snmp_get(ip, snmp_version, oid)
                if val and 'No Such' not in val:
                    try:
                        return int(val)
                    except (ValueError, TypeError):
                        pass
            return default

        for i, name in enumerate(name_oids, start=1):
            type_oid = self.oid_resolver.get_oid(vendor, 'interfaces', 'if_type', i)

            iface = {
                "index": i,
                "name": self.clean_value(name) or f"interface_{i}",
                "type": await self._snmp_get(ip, snmp_version, type_oid) or "unknown",
                "speed": await get_value('if_speed', i),
                "in_errors": await get_value('in_errors', i),
                "out_errors": await get_value('out_errors', i),
                "in_discards": await get_value('in_discards', i),
                "out_discards": await get_value('out_discards', i),
            }
            interfaces.append(iface)

        return interfaces

    async def _get_neighbors(self, ip: str, snmp_version: str) -> List[Dict]:
        """LLDP/CDP соседи"""
        neighbors = []

        # Пробуем CDP (Cisco)
        cdp = await self._get_cdp_neighbors(ip, snmp_version)
        if cdp:
            return cdp

        # Пробуем LLDP
        lldp = await self._get_lldp_neighbors(ip, snmp_version)
        if lldp:
            return lldp

        return neighbors

    async def _get_cdp_neighbors(self, ip: str, snmp_version: str) -> List[Dict]:
        neighbors = []
        vendor = await self._get_vendor_name(ip, snmp_version)

        base_oid = self.oid_resolver.get_cdp_oid(vendor, 'base_oid')
        if not base_oid:
            return neighbors

        for index in range(1, 100):
            device_id_oid = self.oid_resolver.get_cdp_oid(vendor, 'neighbor_device_id', index)
            if not device_id_oid:
                break

            device_id = await self._snmp_get(ip, snmp_version, device_id_oid)
            if not device_id:
                break

            local_port_oid = self.oid_resolver.get_cdp_oid(vendor, 'local_port', index)
            platform_oid = self.oid_resolver.get_cdp_oid(vendor, 'platform', index)

            local_port = await self._snmp_get(ip, snmp_version, local_port_oid) if local_port_oid else "unknown"
            platform = await self._snmp_get(ip, snmp_version, platform_oid) if platform_oid else "unknown"

            neighbors.append({
                "protocol": "CDP",
                "neighbor_id": device_id,
                "local_port": local_port or "unknown",
                "remote_port": "unknown",
                "platform": platform or "unknown"
            })
        return neighbors

    async def _get_lldp_neighbors(self, ip: str, snmp_version: str) -> List[Dict]:
        neighbors = []
        vendor = await self._get_vendor_name(ip, snmp_version)

        base_oid = self.oid_resolver.get_lldp_oid(vendor, 'base_oid')
        if not base_oid:
            return neighbors

        for index in range(1, 100):
            chassis_oid = self.oid_resolver.get_lldp_oid(vendor, 'chassis_id', index)
            if not chassis_oid:
                break

            chassis = await self._snmp_get(ip, snmp_version, chassis_oid)
            if not chassis:
                break

            remote_port_oid = self.oid_resolver.get_lldp_oid(vendor, 'remote_port', index)
            local_port_oid = self.oid_resolver.get_lldp_oid(vendor, 'local_port', index)
            sys_name_oid = self.oid_resolver.get_lldp_oid(vendor, 'sys_name', index)

            remote_port = await self._snmp_get(ip, snmp_version, remote_port_oid) if remote_port_oid else "unknown"
            local_port = await self._snmp_get(ip, snmp_version, local_port_oid) if local_port_oid else "unknown"
            sys_name = await self._snmp_get(ip, snmp_version, sys_name_oid) if sys_name_oid else None

            neighbors.append({
                "protocol": "LLDP",
                "neighbor_id": sys_name or chassis,
                "neighbor_chassis": chassis,
                "local_port": local_port or "unknown",
                "remote_port": remote_port or "unknown",
                "platform": "unknown"
            })
        return neighbors

    async def _get_config(self, ip: str, snmp_version: str, device_type: str) -> str:
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
            config = await self._get_config_snmp(ip, snmp_version, mib)
            if config:
                return config
            # Fallback на SSH
            return await self._get_config_ssh(ip, device_type)
        elif method == 'ssh':
            return await self._get_config_ssh(ip, device_type)
        else:
            return ""

    async def _get_config_snmp(self, ip: str, snmp_version: str, mib: dict) -> Optional[str]:
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
            await self._snmp_set(ip, snmp_version, f"{base}.{mib['type_oid']}.{index}", mib['source_value'])

            # Протокол (tftp)
            if 'protocol_oid' in mib:
                await self._snmp_set(ip, snmp_version, f"{base}.{mib['protocol_oid']}.{index}", mib['protocol'])

            # Имя файла
            await self._snmp_set(ip, snmp_version, f"{base}.{mib['file_oid']}.{index}", filename)

            # TFTP сервер
            await self._snmp_set(ip, snmp_version, f"{base}.{mib['server_oid']}.{index}", tftp_server)

            # Запуск
            status_oid = f"{base}.{mib['status_oid']}.{index}"
            await self._snmp_set(ip, snmp_version, status_oid, 4)

            # Ждём завершения
            for _ in range(30):
                state = await self._snmp_get(ip, snmp_version, status_oid)  # ← auth заменили
                if state == '3':
                    break
                elif state == '4':
                    error_oid = f"{base}.13.{index}" if mib.get('error_oid') else None
                    if error_oid:
                        error = await self._snmp_get(ip, snmp_version, error_oid)  # ← auth заменили
                        raise Exception(f"Config copy failed: {error}")
                    raise Exception("Config copy failed")
                await asyncio.sleep(1)

            # Читаем файл
            config = await self._read_tftp_file(filename)

            # Очищаем
            await self._snmp_set(ip, snmp_version, status_oid, 6)

            return config

        except Exception as e:
            logger.error(f"SNMP config failed for {ip}: {e}")
            return None

    async def _get_config_ssh(self, ip: str, device_type: str) -> str:
        """SSH fallback"""
        async with self.ssh_semaphore:
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
                connection.disable_paging()

                config = connection.send_command(cmd, read_timeout=60)
                logger.info(f"SSH config length: {len(config)} characters")
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

    async def _snmp_set(self, ip: str, snmp_version: str, oid: str, value) -> bool:
        async with self.semaphore:
            try:
                val_type = 'i' if isinstance(value, int) else 's'
                if snmp_version == "v3":
                    cmd = ['snmpset', '-v3', '-u', os.getenv("SNMP_V3_USER", "daria"),
                           '-l', 'authPriv', '-a', 'SHA', '-A', os.getenv("SNMP_V3_AUTH_PASSWORD", ""),
                           '-x', 'AES', '-X', os.getenv("SNMP_V3_PRIV_PASSWORD", ""),
                           ip, oid, val_type, str(value)]
                else:
                    cmd = ['snmpset', '-v2c', '-c', os.getenv("SNMP_COMMUNITY", "public"),
                           ip, oid, val_type, str(value)]
                # Асинхронный запуск
                process = await asyncio.create_subprocess_exec(
                    *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
                )
                stdout, stderr = await process.communicate()
                if process.returncode == 0:
                    logger.info(f"SNMP SET success: {oid}={value}")
                    return True
                else:
                    logger.error(f"SNMP SET failed: {stderr.decode()}")
                    return False
            except Exception as e:
                logger.error(f"SNMP SET exception: {e}")
                return False

    async def _snmp_walk(self, ip: str, snmp_version: str, base_oid: str) -> Optional[List[str]]:
        async with self.semaphore:
            try:
                if snmp_version == "v3":
                    cmd = [
                        'snmpwalk', '-v3', '-Oqv',
                        '-u', os.getenv("SNMP_V3_USER", "daria"),
                        '-l', 'authPriv',
                        '-a', os.getenv("SNMP_V3_AUTH_PROTOCOL", "SHA"),
                        '-A', os.getenv("SNMP_V3_AUTH_PASSWORD", ""),
                        '-x', os.getenv("SNMP_V3_PRIV_PROTOCOL", "AES"),
                        '-X', os.getenv("SNMP_V3_PRIV_PASSWORD", ""),
                        ip, base_oid
                    ]
                else:
                    cmd = [
                        'snmpwalk', '-v2c', '-Oqv',
                        '-c', os.getenv("SNMP_COMMUNITY", "public"),
                        ip, base_oid
                    ]

                process = await asyncio.create_subprocess_exec(
                    *cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE
                )
                stdout, stderr = await process.communicate()

                if process.returncode == 0 and stdout:
                    lines = stdout.decode().strip().split('\n')
                    return [line.strip() for line in lines if line.strip()]
                return None
            except Exception as e:
                logger.debug(f"SNMP walk failed for {ip} {base_oid}: {e}")
                return None

    async def _snmp_get(self, ip: str, snmp_version: str, oid: str) -> Optional[str]:
        async with self.semaphore:
            try:
                if snmp_version == "v3":
                    cmd = [
                        'snmpget', '-v3', '-Oqv',
                        '-u', os.getenv("SNMP_V3_USER", "daria"),
                        '-l', 'authPriv',
                        '-a', os.getenv("SNMP_V3_AUTH_PROTOCOL", "SHA"),
                        '-A', os.getenv("SNMP_V3_AUTH_PASSWORD", ""),
                        '-x', os.getenv("SNMP_V3_PRIV_PROTOCOL", "AES"),
                        '-X', os.getenv("SNMP_V3_PRIV_PASSWORD", ""),
                        ip, oid
                    ]
                else:
                    cmd = [
                        'snmpget', '-v2c', '-Oqv',
                        '-c', os.getenv("SNMP_COMMUNITY", "public"),
                        ip, oid
                    ]

                process = await asyncio.create_subprocess_exec(
                    *cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE
                )
                stdout, stderr = await process.communicate()

                if process.returncode == 0 and stdout:
                    return stdout.decode().strip()
                return None
            except Exception as e:
                logger.error(f"SNMP GET failed for {ip} {oid}: {e}")
                return None

    async def start_scan(self, task_id: str, params: dict):
        """Запуск сканирования сети по расписанию или вручную"""
        self.tasks[task_id] = {"status": "running", "devices_found": 0}
        try:
            device_id = params.get("device_id")
            if device_id:
                await self.collect_single_device(device_id)
                self.tasks[task_id]["devices_found"] = 1
            else:
                await self.collect_all_devices()
                conn = await asyncpg.connect(
                    host=os.getenv("POSTGRES_HOST", "postgres"),
                    database=os.getenv("POSTGRES_DB", "kontrollka"),
                    user=os.getenv("POSTGRES_USER", "kontrollka"),
                    password=os.getenv("POSTGRES_PASSWORD", "")
                )
                count = await conn.fetchval('SELECT COUNT(*) FROM devices')
                await conn.close()
                self.tasks[task_id]["devices_found"] = count
            self.tasks[task_id]["status"] = "completed"
        except Exception as e:
            logger.error(f"Scan {task_id} failed: {e}")
            self.tasks[task_id] = {
                "status": "failed",
                "devices_found": 0,
                "error": str(e)
            }

    def get_status(self, task_id: str) -> Optional[dict]:
        """Получить статус задачи сканирования"""
        return self.tasks.get(task_id)

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
                    d.sysname = $sysname,
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
                        i.oper_status = $oper_status,
                        i.in_errors = $in_errors,
                        i.out_errors = $out_errors,
                        i.in_discards = $in_discards,
                        i.out_discards = $out_discards
                """,
                                  ip=data["ip"],
                                  name=iface.get("name"),
                                  index=iface.get("index"),
                                  type=iface.get("type"),
                                  speed=iface.get("speed"),
                                  admin_status=iface.get("admin_status", "unknown"),
                                  oper_status=iface.get("oper_status", "unknown"),
                                  in_errors=iface.get("in_errors", 0),
                                  out_errors=iface.get("out_errors", 0),
                                  in_discards=iface.get("in_discards", 0),
                                  out_discards=iface.get("out_discards", 0))

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
        client = get_clickhouse_client()
        logger.info(f"DEBUG sysname: {data.get('sysname', 'NOT FOUND')}")

        # 1. Вставляем снапшот устройства
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
            "sysname": data.get("sysname", ""),
            "config": data.get("config", ""),
            "interfaces_count": len(data.get("interfaces", [])),
            "last_collected": data["last_collected"]
        }])

        logger.info(f"DEBUG sysname inserted for {data['ip']}")

        # 2. Батчевая вставка интерфейсов
        if data.get("interfaces"):
            batch_size = 1000
            interfaces_batch = []

            for iface in data.get("interfaces", []):
                interfaces_batch.append({
                    "device_ip": data["ip"],
                    "interface_name": iface["name"],
                    "interface_index": iface["index"],
                    "interface_type": iface["type"],
                    "speed": iface.get("speed"),
                    "admin_status": iface.get("admin_status", "unknown"),
                    "oper_status": iface.get("oper_status", "unknown"),
                    "in_errors": iface.get("in_errors", 0),
                    "out_errors": iface.get("out_errors", 0),
                    "in_discards": iface.get("in_discards", 0),
                    "out_discards": iface.get("out_discards", 0),
                    "collected_at": data["last_collected"]
                })

                if len(interfaces_batch) >= batch_size:
                    client.execute("""
                        INSERT INTO interface_history (
                            device_ip, interface_name, interface_index,
                            interface_type, speed, admin_status, oper_status,
                            in_errors, out_errors, in_discards, out_discards, collected_at
                        ) VALUES
                    """, interfaces_batch)
                    interfaces_batch = []

            # Оставшиеся интерфейсы
            if interfaces_batch:
                client.execute("""
                    INSERT INTO interface_history (
                        device_ip, interface_name, interface_index,
                        interface_type, speed, admin_status, oper_status,
                        in_errors, out_errors, in_discards, out_discards, collected_at
                    ) VALUES
                """, interfaces_batch)

        # 3. Redis уведомление
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