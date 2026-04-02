# ansible/inventory.py
import os
import yaml
from database import DeviceDB

db = DeviceDB()


def generate_inventory(device_ids=None):
    """Генерирует inventory из устройств в БД"""
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
                # Отключаем проверку ключа хоста
                'ansible_ssh_extra_args': '-o StrictHostKeyChecking=no -o ConnectTimeout=30',
                # Явно указываем совместимые алгоритмы (подходят для старого Cisco/Huawei)
                'ansible_ssh_common_args': '-o KexAlgorithms=diffie-hellman-group14-sha256,diffie-hellman-group-exchange-sha256 -o HostKeyAlgorithms=+ssh-rsa -o PubkeyAcceptedKeyTypes=+ssh-rsa'
            }
        }
    }

    for device in devices:
        inventory['all']['hosts'][device['name']] = {
            'ansible_host': device['host'],
            'ansible_port': device.get('port', 22),
            'device_type': device.get('device_type', 'huawei'),
        }

    inv_path = '/tmp/kontrollka_inventory.yml'
    with open(inv_path, 'w') as f:
        yaml.dump(inventory, f)

    return inv_path