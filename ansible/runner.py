# ansible/runner.py
import os
import json
import subprocess
from .inventory import generate_inventory


class AnsibleRunner:
    def __init__(self):
        self.playbooks_dir = 'ansible/playbooks'
        os.makedirs(self.playbooks_dir, exist_ok=True)

    def list_playbooks(self):
        """Возвращает список доступных playbooks"""
        playbooks = []
        for file in os.listdir(self.playbooks_dir):
            if file.endswith(('.yml', '.yaml')):
                playbooks.append({'name': file})
        return playbooks

    def get_playbook_content(self, name):
        """Возвращает содержимое playbook"""
        path = os.path.join(self.playbooks_dir, name)
        if not os.path.exists(path):
            return None
        with open(path, 'r') as f:
            return f.read()

    def save_playbook(self, name, content):
        """Сохраняет playbook"""
        path = os.path.join(self.playbooks_dir, name)
        with open(path, 'w') as f:
            f.write(content)
        return path

    def run_playbook(self, playbook_name, device_ids=None, extra_vars=None):
        """Запускает playbook"""
        playbook_path = os.path.join(self.playbooks_dir, playbook_name)
        if not os.path.exists(playbook_path):
            return {'success': False, 'error': f'Playbook {playbook_name} not found'}

        inventory_path = generate_inventory(device_ids)

        cmd = ['ansible-playbook', '-i', inventory_path, playbook_path]

        if extra_vars:
            cmd.extend(['--extra-vars', json.dumps(extra_vars)])

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