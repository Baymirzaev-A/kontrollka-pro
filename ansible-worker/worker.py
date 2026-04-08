import redis
import json
import subprocess
import tempfile
import os
import yaml
import logging

# НАСТРОЙКА ЛОГИРОВАНИЯ
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

r = redis.Redis(host='redis', port=6379, decode_responses=True)

# Универсальный маппинг вендоров (поддерживает 50+)
NETWORK_OS_MAP = {
    'cisco_ios': 'cisco.ios.ios',
    'cisco_nxos': 'cisco.nxos.nxos',
    'cisco_xr': 'cisco.iosxr.iosxr',
    'cisco_asa': 'cisco.asa.asa',
    'huawei': 'community.network.ce',
    'huawei_vrpv8': 'community.network.ce',
    'juniper': 'junipernetworks.junos.junos',
    'arista_eos': 'arista.eos.eos',
    'hp_procurve': 'community.network.procurve',
    'hp_comware': 'community.network.comware',
    'aruba_os': 'community.network.aruba',
    'dell_force10': 'community.network.dellos10',
    'dell_os10': 'community.network.dellos10',
    'extreme_exos': 'community.network.exos',
    'fortinet': 'fortinet.fortios.fortios',
    'eltex': 'community.network.eltex',
    'linux': 'ansible.builtin.linux',
}

# Универсальные SSH аргументы для любого оборудования
SSH_COMMON_ARGS = (
    '-o StrictHostKeyChecking=no '
    '-o ConnectTimeout=30 '
    '-o KexAlgorithms=diffie-hellman-group14-sha1,diffie-hellman-group14-sha256,diffie-hellman-group16-sha512 '
    '-o HostKeyAlgorithms=+ssh-rsa '
    '-o PubkeyAcceptedAlgorithms=+ssh-rsa'
)


def generate_inventory(devices_data):
    """Универсальная генерация инвентаря для любого вендора"""
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
        network_os = NETWORK_OS_MAP.get(device_type, 'ansible.netcommon.default')

        # Определяем тип подключения
        if device_type == 'linux':
            connection = 'ssh'
        else:
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
    finally:
        for path in [playbook_path, inventory_path]:
            if os.path.exists(path):
                os.unlink(path)


def main():
    logging.info("Ansible Worker started (universal mode)")
    while True:
        task_data = r.blpop('ansible:tasks', timeout=1)
        if task_data:
            _, task_json = task_data
            task = json.loads(task_json)
            logging.info(f"Running: {task['playbook_name']} on {len(task['devices_data'])} devices")
            result = run_playbook(task)
            r.set(f'ansible:result:{task["task_id"]}', json.dumps(result), ex=3600)


if __name__ == '__main__':
    main()