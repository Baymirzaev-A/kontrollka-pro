from celery import Celery
import os
import redis
import json
import subprocess
import tempfile
import yaml
import logging

# Настройка логирования
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

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
    logging.info(f"Task {task_id}: Starting playbook {task_data.get('playbook_name')}")

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

        logging.info(f"Task {task_id}: Running command: {' '.join(cmd)}")

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
        logging.error(f"Task {task_id}: Timeout")
        return {
            'success': False,
            'error': 'Timeout after 300 seconds',
            'task_id': task_id
        }
    except Exception as e:
        logging.error(f"Task {task_id}: Error - {str(e)}")
        return {
            'success': False,
            'error': str(e),
            'task_id': task_id
        }
    finally:
        for path in [playbook_path, inventory_path]:
            if path and os.path.exists(path):
                os.unlink(path)
                logging.debug(f"Task {task_id}: Removed temp file {path}")