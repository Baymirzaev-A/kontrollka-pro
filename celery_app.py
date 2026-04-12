import time
import os
import redis
import json
import subprocess
import tempfile
import yaml

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