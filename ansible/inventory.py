import os
import yaml
from database import DeviceDB

db = DeviceDB()

NETWORK_OS_MAP = {
    'cisco_ios': 'ios',
    'cisco_nxos': 'nxos',
    'cisco_xr': 'iosxr',
    'cisco_asa': 'asa',
    'huawei': 'huawei',
    'huawei_vrpv8': 'huawei',
    'huawei_olt': 'huawei',
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
        device_type = device.get('device_type', 'huawei')
        network_os = NETWORK_OS_MAP.get(device_type, 'ios')

        inventory['all']['hosts'][device['name']] = {
            'ansible_host': device['host'],
            'ansible_port': device.get('port', 22),
            'ansible_network_os': network_os,
            'ansible_connection': 'network_cli' if network_os != 'linux' else 'ssh',
        }

    inv_path = '/tmp/kontrollka_inventory.yml'
    with open(inv_path, 'w') as f:
        yaml.dump(inventory, f)

    return inv_path