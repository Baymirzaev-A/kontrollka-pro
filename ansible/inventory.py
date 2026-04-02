import os
import yaml
from database import DeviceDB

db = DeviceDB()

# ---- Словарь для маппинга ----
# 'device_type' из Kontrollka -> 'ansible_network_os'
DEVICE_TYPE_MAP = {
    # Cisco
    'cisco_ios': 'cisco.ios.ios',
    'cisco_nxos': 'cisco.nxos.nxos',
    'cisco_xr': 'cisco.iosxr.iosxr',
    'cisco_asa': 'cisco.asa.asa',
    # Huawei (используем правильную, живую коллекцию)
    'huawei': 'huawei.cloudengine.ce',
    'huawei_vrpv8': 'huawei.cloudengine.ce',
    'huawei_olt': 'huawei.cloudengine.ce',
    # Остальные вендоры
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
    'mikrotik_routeros': 'community.routeros.routeros',
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
                # Глобальные настройки для всех устройств
                'ansible_ssh_user': os.environ.get('DEVICE_USERNAME', 'admin'),
                'ansible_ssh_pass': os.environ.get('DEVICE_PASSWORD', 'admin'),
                'ansible_ssh_common_args': '-o StrictHostKeyChecking=no -o ConnectTimeout=30 -o KexAlgorithms=diffie-hellman-group14-sha1',
                'ansible_connection': 'ansible.netcommon.network_cli',
            }
        }
    }

    for device in devices:
        device_type = device.get('device_type', 'huawei')
        network_os = DEVICE_TYPE_MAP.get(device_type, 'ansible.netcommon.default')

        inventory['all']['hosts'][device['name']] = {
            'ansible_host': device['host'],
            'ansible_port': device.get('port', 22),
            'ansible_network_os': network_os,
        }

    inv_path = '/tmp/kontrollka_inventory.yml'
    with open(inv_path, 'w') as f:
        yaml.dump(inventory, f)

    return inv_path