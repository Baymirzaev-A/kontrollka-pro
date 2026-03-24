import socket
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed

logger = logging.getLogger(__name__)


def tcp_ping(host, port=22, timeout=2):
    """
    Проверяет доступность TCP порта (SSH по умолчанию)

    Args:
        host: IP адрес или имя хоста
        port: номер порта (по умолчанию 22)
        timeout: таймаут в секундах

    Returns:
        bool: True если порт открыт, False если нет
    """
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        result = sock.connect_ex((host, port))
        sock.close()

        if result == 0:
            logger.debug(f"✅ TCP ping успешен: {host}:{port}")
        else:
            logger.debug(f"❌ TCP ping неудачен: {host}:{port} (код: {result})")

        return result == 0

    except socket.gaierror:
        logger.debug(f"❌ Не удается разрешить имя хоста: {host}")
        return False
    except socket.timeout:
        logger.debug(f"❌ Таймаут TCP ping: {host}:{port}")
        return False
    except Exception as e:
        logger.debug(f"❌ Ошибка TCP ping для {host}:{port}: {e}")
        return False


def check_devices_status(devices, max_workers=50):
    """
    Проверяет статус нескольких устройств параллельно по TCP порту

    Args:
        devices: список словарей с устройствами (должны содержать ключи 'host' и 'port')
        max_workers: максимальное количество параллельных проверок

    Returns:
        dict: {device_id: True/False}
    """
    results = {}

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        # Запускаем проверки
        future_to_device = {
            executor.submit(tcp_ping, device['host'], device.get('port', 22)): device
            for device in devices
        }

        # Собираем результаты
        for future in as_completed(future_to_device):
            device = future_to_device[future]
            try:
                results[device['id']] = future.result()
            except Exception as e:
                logger.error(f"Ошибка при проверке {device['host']}:{device.get('port', 22)}: {e}")
                results[device['id']] = False

    online_count = sum(1 for status in results.values() if status)
    logger.info(f"📊 Статус проверен: {online_count}/{len(devices)} устройств онлайн")

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


# Для обратной совместимости с существующим кодом
ping_device = tcp_ping