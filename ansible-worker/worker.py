import redis
import json
import subprocess
import tempfile
import os
import yaml

r = redis.Redis(host='redis', port=6379, decode_responses=True)


def generate_inventory(devices_data):
    inventory = {
        'all': {
            'hosts': {},
            'vars': {
                'ansible_ssh_user': os.environ.get('DEVICE_USERNAME', 'admin'),
                'ansible_ssh_pass': os.environ.get('DEVICE_PASSWORD', 'admin'),
                'ansible_ssh_common_args': '-o KexAlgorithms=diffie-hellman-group14-sha1,diffie-hellman-group14-sha256 -o HostKeyAlgorithms=+ssh-rsa -o StrictHostKeyChecking=no',
            }
        }
    }

    for device in devices_data:
        device_type = device.get('device_type', 'huawei')

        if 'huawei' in device_type:
            network_os = 'ce'
            connection = 'network_cli'
        elif device_type == 'linux':
            network_os = 'linux'
            connection = 'ssh'
        else:
            network_os = 'default'
            connection = 'network_cli'

        inventory['all']['hosts'][device['name']] = {
            'ansible_host': device['host'],
            'ansible_port': device.get('port', 22),
            'ansible_network_os': network_os,
            'ansible_connection': connection,
        }

    fd, path = tempfile.mkstemp(suffix='.yml', prefix='inventory_')
    with os.fdopen(fd, 'w') as f:
        yaml.dump(inventory, f)
    return path


def run_playbook(task):
    # Создаём временный файл для playbook
    with tempfile.NamedTemporaryFile(mode='w', suffix='.yml', delete=False) as f:
        f.write(task['playbook_content'])
        playbook_path = f.name

    inventory_path = generate_inventory(task['devices_data'])

    cmd = ['ansible-playbook', '-i', inventory_path, playbook_path]
    if task.get('extra_vars'):
        cmd.extend(['--extra-vars', json.dumps(task['extra_vars'])])

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        return {
            'success': result.returncode == 0,
            'stdout': result.stdout,
            'stderr': result.stderr,
            'returncode': result.returncode
        }
    except subprocess.TimeoutExpired:
        return {
            'success': False,
            'error': 'Timeout (300s)',
            'stdout': '',
            'stderr': ''
        }
    except Exception as e:
        return {
            'success': False,
            'error': str(e),
            'stdout': '',
            'stderr': ''
        }
    finally:
        # Чистим временные файлы
        if os.path.exists(playbook_path):
            os.unlink(playbook_path)
        if os.path.exists(inventory_path):
            os.unlink(inventory_path)


def main():
    print("Ansible Worker started, waiting for tasks...")
    while True:
        task_data = r.blpop('ansible:tasks', timeout=1)
        if task_data:
            _, task_json = task_data
            task = json.loads(task_json)
            print(f"Running: {task['playbook_name']} (ID: {task['playbook_id']})")
            result = run_playbook(task)
            r.set(f'ansible:result:{task["task_id"]}', json.dumps(result), ex=3600)


if __name__ == '__main__':
    main()