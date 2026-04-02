# ansible/inventory.py
import os
import yaml
from database import DeviceDB

db = DeviceDB()

# Этот словарь - "переводчик". Он говорит Ansible, какой драйвер использовать для каждого типа твоего устройства.
# Я добавил сюда все основные вендоры, которые мы обсуждали.
NETWORK_OS_MAP = {
    # --- Cisco ---
    'cisco_ios': 'ios',
    'cisco_nxos': 'nxos',
    'cisco_xr': 'iosxr',
    'cisco_asa': 'asa',
    'huawei': 'ce',
    'huawei_vrpv8': 'ce',
    'huawei_olt': 'ce',
    # --- Остальные ---
    'juniper': 'junos',
    'arista_eos': 'eos',
    'hp_procurve': 'procurve',
    'hp_comware': 'comware',
    'aruba_os': 'aruba',
    'dell_force10': 'dellos10',
    'dell_os10': 'dellos10',
    'extreme_exos': 'exos',
    'fortinet': 'fortios',
    'eltex': 'eltex',
    'linux': 'linux',
    'mikrotik_routeros': 'community.network.routeros',
    'generic_termserver': 'default'
}


def generate_inventory(device_ids=None):
    if device_ids:
        devices = [db.get_device(id) for id in device_ids if db.get_device(id)]
    else:
        devices = db.get_all_devices()

    inventory = {
        'all': {
            'hosts': {},
            'vars': {
                'ansible_ssh_user': os.environ.get('DEVICE_USERNAME', 'admin'),
                'ansible_ssh_pass': os.environ.get('DEVICE_PASSWORD', 'admin'),
                'ansible_ssh_common_args': '-o KexAlgorithms=diffie-hellman-group14-sha1,diffie-hellman-group14-sha256,diffie-hellman-group16-sha512 -o HostKeyAlgorithms=+ssh-rsa -o PubkeyAcceptedAlgorithms=+ssh-rsa',
                'ansible_ssh_extra_args': '-o StrictHostKeyChecking=no -o ConnectTimeout=30'
            }
        }
    }

    for device in devices:
        # Получаем тип устройства из БД
        device_type = device.get('device_type', 'huawei')

        # Получаем нужный драйвер из нашего словаря
        network_os = NETWORK_OS_MAP.get(device_type, 'default')

        # Важно: для некоторых ОС нужно использовать полное имя коллекции
        if device_type == 'mikrotik_routeros':
            connection_type = 'ansible.netcommon.network_cli'
        else:
            connection_type = 'network_cli'

        # Формируем запись для инвентори
        inventory['all']['hosts'][device['name']] = {
            'ansible_host': device['host'],
            'ansible_port': device.get('port', 22),
            'ansible_network_os': network_os,
            'ansible_connection': connection_type,
        }

    inv_path = '/tmp/kontrollka_inventory.yml'
    with open(inv_path, 'w') as f:
        yaml.dump(inventory, f)

    return inv_path