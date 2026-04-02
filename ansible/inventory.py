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
                'ansible_ssh_common_args': (
                    '-o KexAlgorithms=diffie-hellman-group1-sha1,'
                    'diffie-hellman-group14-sha1,'
                    'diffie-hellman-group-exchange-sha1,'
                    'diffie-hellman-group14-sha256,'
                    'diffie-hellman-group16-sha512 '
                    '-o HostKeyAlgorithms=+ssh-rsa,ssh-dss '
                    '-o PubkeyAcceptedKeyTypes=+ssh-rsa,ssh-dss '
                    '-o Ciphers=aes256-cbc,aes128-cbc,3des-cbc'
                ),
                'ansible_ssh_extra_args': '-o StrictHostKeyChecking=no -o ConnectTimeout=30'
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