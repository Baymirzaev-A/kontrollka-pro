import time
import os
import redis
import json
import subprocess
import tempfile
import yaml
from celery import group, chord
import logging

# Настройка логирования
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def wait_for_redis():
    """Ждём готовности Redis перед созданием Celery"""
    redis_url = os.environ.get('REDIS_URL', 'redis://redis:6379/0')
    max_retries = 30

    # Парсим хост и порт
    if redis_url.startswith('redis://'):
        parts = redis_url.replace('redis://', '').split(':')
        host = parts[0]
        port = int(parts[1].split('/')[0]) if len(parts) > 1 else 6379
    else:
        host = 'redis'
        port = 6379

    for i in range(max_retries):
        try:
            r = redis.Redis(host=host, port=port, socket_connect_timeout=2)
            if r.ping():
                logger.info(f"Redis is ready after {i + 1} attempts")
                return True
        except Exception as e:
            if i < max_retries - 1:
                logger.info(f"Waiting for Redis ({i + 1}/{max_retries}): {e}")
                time.sleep(1)
            else:
                logger.error(f"Redis not available after {max_retries} attempts")
                raise

    return True


# Ждём Redis перед созданием Celery
wait_for_redis()

# Дальше ваш существующий код
from celery import Celery

# Celery конфигурация
REDIS_URL = os.environ.get('REDIS_URL', 'redis://redis:6379/0')
app = Celery('ansible', broker=REDIS_URL, backend=REDIS_URL)

app.conf.update(
    task_serializer='json',
    accept_content=['json'],
    result_serializer='json',
    timezone='UTC',
    enable_utc=True,
    task_track_started=True,
    task_time_limit=300,
    task_soft_time_limit=280,
    worker_prefetch_multiplier=1,
    task_acks_late=True,
)

# ===== ПЛАНИРОВЩИК UCMDB (РАЗ В НЕДЕЛЮ) =====
from celery.schedules import crontab


def parse_cron(cron_string: str):
    """Преобразует строку cron в crontab объект"""
    parts = cron_string.split()
    if len(parts) != 5:
        return crontab(day_of_week=0, hour=2, minute=0)  # дефолт: воскресенье 2:00

    minute, hour, day_of_month, month, day_of_week = parts
    return crontab(
        minute=minute if minute != '*' else '*',
        hour=hour if hour != '*' else '*',
        day_of_month=day_of_month if day_of_month != '*' else '*',
        month_of_year=month if month != '*' else '*',
        day_of_week=day_of_week if day_of_week != '*' else '*'
    )


# ===== ЗАДАЧИ ДЛЯ DARIA (МАССОВЫЙ SNMP СБОР) =====
@app.task(bind=True, name='daria.tasks.collect_all_devices')
def collect_all_devices_task(self):
    """Массовый сбор SNMP данных по всем устройствам"""
    import requests
    try:
        response = requests.post(
            'http://daria-api:8000/api/discovery/collect-all',
            timeout=5
        )
        logger.info(f"DARIA collect all started: {response.json()}")
        return {'status': 'started', 'response': response.json()}
    except Exception as e:
        logger.error(f"DARIA collect all failed: {e}")
        return {'status': 'failed', 'error': str(e)}


@app.task(bind=True, name='daria.tasks.collect_device')
def collect_device_task(self, device_id: int):
    """Сбор SNMP данных по одному устройству"""
    import requests
    try:
        response = requests.post(
            f'http://daria-api:8000/api/discovery/collect/{device_id}',
            timeout=60
        )
        return {'status': 'started', 'response': response.json()}
    except Exception as e:
        logger.error(f"DARIA collect device {device_id} failed: {e}")
        return {'status': 'failed', 'error': str(e)}

app.conf.beat_schedule = {
    'daria-collect-weekly': {
        'task': 'daria.tasks.collect_all_devices',
        'schedule': parse_cron(os.environ.get('DARIA_SCHEDULE', '0 2 * * 0')),
    },
}

# SSH аргументы
SSH_COMMON_ARGS = (
    '-o StrictHostKeyChecking=no '
    '-o ConnectTimeout=30 '
    '-o KexAlgorithms=diffie-hellman-group14-sha1,diffie-hellman-group16-sha512,diffie-hellman-group15-sha512,diffie-hellman-group-exchange-sha256,diffie-hellman-group14-sha256 '
    '-o HostKeyAlgorithms=+ssh-rsa '
    '-o PubkeyAcceptedAlgorithms=+ssh-rsa'
)


def generate_inventory(devices_data):
    """Генерация инвентаря"""
    inventory = {
        'all': {
            'hosts': {},
            'vars': {
                'ansible_user': os.environ.get('DEVICE_USERNAME', 'admin'),
                'ansible_password': os.environ.get('DEVICE_PASSWORD', 'admin'),
                'ansible_ssh_common_args': SSH_COMMON_ARGS,
            }
        }
    }

    for device in devices_data:
        device_type = device.get('device_type', 'huawei')
        connection = 'ssh' if device_type == 'linux' else 'network_cli'

        inventory['all']['hosts'][device['host']] = {
            'ansible_host': device['host'],
            'ansible_port': device.get('port', 22),
            'ansible_connection': connection,
        }

    fd, path = tempfile.mkstemp(suffix='.yml', prefix='inventory_')
    with os.fdopen(fd, 'w') as f:
        yaml.dump(inventory, f)
    return path


@app.task(bind=True, name='ansible.run_playbook')
def run_playbook_task(self, task_data):
    """Celery задача для выполнения playbook"""
    task_id = self.request.id
    logger.info(f"Task {task_id}: Starting playbook {task_data.get('playbook_name')}")

    playbook_path = None
    inventory_path = None

    try:
        # Создаем playbook файл
        with tempfile.NamedTemporaryFile(mode='w', suffix='.yml', delete=False) as f:
            f.write(task_data['playbook_content'])
            playbook_path = f.name

        # Генерируем инвентарь
        inventory_path = generate_inventory(task_data.get('devices_data', []))

        # Формируем команду
        cmd = ['ansible-playbook', '-i', inventory_path, playbook_path]
        if task_data.get('extra_vars'):
            cmd.extend(['--extra-vars', json.dumps(task_data['extra_vars'])])

        logger.info(f"Task {task_id}: Running command: {' '.join(cmd)}")

        # Выполняем
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)

        return {
            'success': result.returncode == 0,
            'stdout': result.stdout,
            'stderr': result.stderr,
            'returncode': result.returncode,
            'task_id': task_id
        }

    except subprocess.TimeoutExpired:
        logger.error(f"Task {task_id}: Timeout")
        return {
            'success': False,
            'error': 'Timeout after 300 seconds',
            'task_id': task_id
        }
    except Exception as e:
        logger.error(f"Task {task_id}: Error - {str(e)}")
        return {
            'success': False,
            'error': str(e),
            'task_id': task_id
        }
    finally:
        for path in [playbook_path, inventory_path]:
            if path and os.path.exists(path):
                os.unlink(path)
                logger.debug(f"Task {task_id}: Removed temp file {path}")


# ===== CELERY CANVAS: ПАРАЛЛЕЛЬНОЕ ВЫПОЛНЕНИЕ КОМАНД =====

@app.task(bind=True, max_retries=2, soft_time_limit=120, time_limit=150)
def execute_device_command_task(self, device_id: int, command: str, username: str, device_params: dict):
    """
    Выполнить команду на одном устройстве через Netmiko
    Запускается параллельно для каждого устройства (group)
    """
    from netmiko import ConnectHandler

    logger.info(f"Task {self.request.id}: Executing on device {device_id}")

    try:
        connection = ConnectHandler(**device_params)
        output = connection.send_command(command, read_timeout=60)
        connection.disconnect()

        # Сохраняем в историю (опционально, можно через API)
        # _save_command_history(device_id, command, output, username)

        return {
            'device_id': device_id,
            'device_name': device_params.get('host', 'unknown'),
            'success': True,
            'output': output
        }
    except Exception as e:
        logger.error(f"Task {self.request.id}: Failed on device {device_id} - {e}")
        return {
            'device_id': device_id,
            'device_name': device_params.get('host', 'unknown'),
            'success': False,
            'error': str(e)
        }


@app.task
def notify_completion(results, command: str, username: str):
    """
    Callback после выполнения всех команд (chord)
    """
    total = len(results)
    success_count = sum(1 for r in results if r['success'])
    error_count = total - success_count

    logger.info(f"✅ Команда '{command}' выполнена: успешно={success_count}, ошибок={error_count}")

    # TODO: Отправить WebSocket уведомление через Redis
    # r = redis.Redis(host='redis', port=6379)
    # r.publish('group_command_complete', json.dumps({'total': total, 'success': success_count}))

    return {
        'total': total,
        'success': success_count,
        'failed': error_count,
        'results': results
    }


def execute_group_command_parallel(device_ids: list, command: str, username: str, devices_info: list):
    """
    Параллельное выполнение команды на группе устройств
    devices_info: список словарей с параметрами подключения для каждого устройства
    """
    # Создаём группу задач (все запускаются параллельно)
    tasks = [
        execute_device_command_task.s(device_id, command, username, device_params)
        for device_id, device_params in zip(device_ids, devices_info)
    ]

    # chord = group + callback (выполняется после завершения всех)
    callback = notify_completion.s(command, username)
    result = chord(tasks)(callback)

    return result.id