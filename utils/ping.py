# utils/ping.py
import subprocess
import platform
from concurrent.futures import ThreadPoolExecutor, as_completed
import logging

logger = logging.getLogger(__name__)


def ping_device(host, timeout=2):
    """
    Проверяет доступность устройства по ICMP

    Args:
        host: IP адрес или имя хоста
        timeout: таймаут в секундах

    Returns:
        bool: True если устройство доступно, False если нет
    """
    # Определяем параметры ping в зависимости от ОС
    param = '-n' if platform.system().lower() == 'windows' else '-c'
    command = ['ping', param, '1', '-W', str(timeout), host]

    try:
        result = subprocess.run(command, capture_output=True, timeout=timeout + 1)
        return result.returncode == 0
    except subprocess.TimeoutExpired:
        logger.debug(f"Ping timeout для {host}")
        return False
    except Exception as e:
        logger.debug(f"Ошибка ping для {host}: {e}")
        return False


def check_devices_status(devices, max_workers=50):
    """
    Проверяет статус нескольких устройств параллельно

    Args:
        devices: список словарей с устройствами (должны содержать ключ 'host')
        max_workers: максимальное количество параллельных проверок

    Returns:
        dict: {device_id: True/False}
    """
    results = {}

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        # Запускаем проверки
        future_to_device = {
            executor.submit(ping_device, device['host']): device
            for device in devices
        }

        # Собираем результаты
        for future in as_completed(future_to_device):
            device = future_to_device[future]
            try:
                results[device['id']] = future.result()
            except Exception as e:
                logger.error(f"Ошибка при проверке {device['host']}: {e}")
                results[device['id']] = False

    return results


def get_online_devices(devices, max_workers=50):
    """
    Возвращает только доступные устройства

    Returns:
        list: доступные устройства
        dict: статусы всех устройств
    """
    statuses = check_devices_status(devices, max_workers)
    online_devices = [d for d in devices if statuses.get(d['id'], False)]
    return online_devices, statuses