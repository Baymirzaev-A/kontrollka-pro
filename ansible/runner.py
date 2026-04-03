import os
import json
import tempfile
import ansible_runner
from .inventory import generate_inventory   # используем твой inventory.py

class AnsibleRunner:
    def __init__(self):
        self.playbooks_dir = 'ansible/playbooks'
        os.makedirs(self.playbooks_dir, exist_ok=True)

    def list_playbooks(self):
        playbooks = []
        for file in os.listdir(self.playbooks_dir):
            if file.endswith(('.yml', '.yaml')):
                playbooks.append({'name': file})
        return playbooks

    def get_playbook_content(self, name):
        path = os.path.join(self.playbooks_dir, name)
        if not os.path.exists(path):
            return None
        with open(path, 'r') as f:
            return f.read()

    def save_playbook(self, name, content):
        path = os.path.join(self.playbooks_dir, name)
        with open(path, 'w') as f:
            f.write(content)
        return path

    def run_playbook(self, playbook_name, device_ids=None, extra_vars=None, executed_by=None):
        playbook_path = os.path.join(self.playbooks_dir, playbook_name)
        if not os.path.exists(playbook_path):
            return {'success': False, 'error': f'Playbook {playbook_name} not found'}

        # Генерируем inventory через твою функцию (она возвращает путь к файлу)
        inv_path = generate_inventory(device_ids)

        with tempfile.TemporaryDirectory() as tmpdir:
            try:
                r = ansible_runner.run(
                    private_data_dir=tmpdir,
                    playbook=playbook_path,
                    inventory=inv_path,          # передаём путь к файлу
                    extravars=extra_vars or {},
                    verbosity=0
                )

                stdout = r.stdout.read() if r.stdout else ''
                stderr = r.stderr.read() if r.stderr else ''

                # Сохраняем историю (опционально)
                from database import DeviceDB
                db = DeviceDB()
                db.save_ansible_history(
                    playbook_name=playbook_name,
                    device_ids=device_ids,
                    extra_vars=extra_vars or {},
                    executed_by=executed_by or 'unknown',
                    success=r.rc == 0,
                    stdout=stdout,
                    stderr=stderr
                )

                return {
                    'success': r.rc == 0,
                    'stdout': stdout,
                    'stderr': stderr,
                    'returncode': r.rc
                }
            except Exception as e:
                return {
                    'success': False,
                    'error': str(e),
                    'stdout': '',
                    'stderr': ''
                }