import yaml
import os
from typing import Dict, List, Optional
import logging

logger = logging.getLogger(__name__)

class OIDResolver:
    def __init__(self):
        self.oids = self._load_oids()

    def _load_oids(self) -> Dict:
        """Загружает OID из YAML файла"""
        yaml_path = os.path.join(os.path.dirname(__file__), 'vendor_oids.yaml')
        try:
            with open(yaml_path, 'r') as f:
                return yaml.safe_load(f)
        except Exception as e:
            logger.error(f"Failed to load OID map: {e}")
            return {'default': {}}

    def get_oid(self, vendor: str, category: str, field: str, if_index: int = None) -> Optional[str]:
        """
        Получает OID для конкретного вендора.
        Если нет специфичного - берет из default.
        """
        # Пытаемся найти OID для конкретного вендора
        vendor_oids = self.oids.get(vendor.lower(), {})
        category_oids = vendor_oids.get(category, {})

        if field in category_oids:
            oid = category_oids[field]
            if isinstance(oid, list):
                oid = oid[0]  # Берем первый из списка
            if if_index and '{ifIndex}' in oid:
                oid = oid.replace('{ifIndex}', str(if_index))
            return oid

        # Если нет - берем из default
        default_oids = self.oids.get('default', {})
        default_category = default_oids.get(category, {})

        if field in default_category:
            oid = default_category[field]
            if isinstance(oid, list):
                oid = oid[0]
            if if_index and '{ifIndex}' in oid:
                oid = oid.replace('{ifIndex}', str(if_index))
            return oid

        return None

    def get_oid_list(self, vendor: str, category: str, field: str, if_index: int = None) -> List[str]:
        """
        Возвращает список OID для попытки опроса (для полей, где может быть несколько вариантов).
        """
        oids = []

        # Сначала специфичные для вендора
        vendor_oids = self.oids.get(vendor.lower(), {})
        category_oids = vendor_oids.get(category, {})

        if field in category_oids:
            field_oids = category_oids[field]
            if isinstance(field_oids, list):
                oids.extend(field_oids)
            else:
                oids.append(field_oids)

        # Потом стандартные
        default_oids = self.oids.get('default', {})
        default_category = default_oids.get(category, {})

        if field in default_category:
            field_oids = default_category[field]
            if isinstance(field_oids, list):
                oids.extend(field_oids)
            else:
                oids.append(field_oids)

        # Убираем дубликаты и заменяем ifIndex
        unique_oids = []
        for oid in oids:
            if if_index and '{ifIndex}' in oid:
                oid = oid.replace('{ifIndex}', str(if_index))
            if oid not in unique_oids:
                unique_oids.append(oid)

        return unique_oids

    def get_cdp_oid(self, vendor: str, field: str, index: int = None) -> Optional[str]:
        """Получает OID для CDP соседей"""
        vendor_oids = self.oids.get(vendor.lower(), {})
        cdp_config = vendor_oids.get('cdp', self.oids.get('default', {}).get('cdp', {}))

        oid = cdp_config.get(field)
        if oid and index:
            oid = f"{cdp_config.get('base_oid', '1.3.6.1.4.1.9.9.23.1.2.1.1')}.{oid}.{index}"
        return oid

    def get_lldp_oid(self, vendor: str, field: str, index: int = None) -> Optional[str]:
        """Получает OID для LLDP соседей"""
        vendor_oids = self.oids.get(vendor.lower(), {})
        lldp_config = vendor_oids.get('lldp', self.oids.get('default', {}).get('lldp', {}))

        oid = lldp_config.get(field)
        if oid and index:
            oid = f"{lldp_config.get('base_oid', '1.0.8802.1.1.2.1.4.1.1')}.{oid}.{index}"
        return oid