import os
import yaml
from database import DeviceDB

db = DeviceDB()

# Маппинг device_type → ansible_network_os
NETWORK_OS_MAP = {
    'cisco_ios': 'cisco.ios.ios',
    'cisco_nxos': 'cisco.nxos.nxos',
    'cisco_xr': 'cisco.iosxr.iosxr',
    'cisco_asa': 'cisco.asa.asa',
    'huawei': 'ce',
    'huawei_vrpv8': 'ce',
    'huawei_olt': 'ce',
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
    'generic_termserver': 'ansible.netcommon.default'
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
                'ansible_user': os.environ.get('DEVICE_USERNAME', 'admin'),
                'ansible_password': os.environ.get('DEVICE_PASSWORD', 'admin'),
                'ansible_ssh_common_args': '-o StrictHostKeyChecking=no -o ConnectTimeout=30 -o KexAlgorithms=diffie-hellman-group14-sha1,diffie-hellman-group14-sha256',
                'ansible_ssh_extra_args': '-o ServerAliveInterval=15'
            }
        }
    }

    for device in devices:
        device_type = device.get('device_type', 'huawei')
        network_os = NETWORK_OS_MAP.get(device_type, 'ansible.netcommon.default')

        # Для разных типов подключения
        if device_type == 'linux':
            connection = 'ssh'
        else:
            connection = 'ansible.netcommon.network_cli'

        inventory['all']['hosts'][device['name']] = {
            'ansible_host': device['host'],
            'ansible_port': device.get('port', 22),
            'ansible_network_os': network_os,
            'ansible_connection': connection,
        }

    inv_path = '/tmp/kontrollka_inventory.yml'
    with open(inv_path, 'w') as f:
        yaml.dump(inventory, f)

    return inv_path