# ansible/routes.py
from flask import Blueprint, render_template, jsonify, request, session
from functools import wraps
from .runner import AnsibleRunner

ansible_bp = Blueprint('ansible', __name__, url_prefix='/ansible')
runner = AnsibleRunner()

def role_required(allowed_roles):
    def decorator(f):
        @wraps(f)
        def decorated(*args, **kwargs):
            user_role = session.get('role', 'viewer')
            if user_role not in allowed_roles:
                return jsonify({'error': 'Доступ запрещен'}), 403
            return f(*args, **kwargs)
        return decorated
    return decorator

@ansible_bp.route('/')
@role_required(['admin'])
def index():
    """Страница Ansible"""
    return render_template('ansible.html')

@ansible_bp.route('/api/playbooks', methods=['GET'])
@role_required(['admin'])
def list_playbooks():
    """Список playbooks"""
    return jsonify(runner.list_playbooks())

@ansible_bp.route('/api/playbooks/<path:name>', methods=['GET'])
@role_required(['admin'])
def get_playbook(name):
    """Получить playbook"""
    content = runner.get_playbook_content(name)
    if content is None:
        return jsonify({'error': 'Playbook not found'}), 404
    return jsonify({'name': name, 'content': content})

@ansible_bp.route('/api/playbooks/<path:name>', methods=['PUT'])
@role_required(['admin'])
def save_playbook(name):
    """Сохранить playbook"""
    data = request.json
    runner.save_playbook(name, data.get('content', ''))
    return jsonify({'success': True})

@ansible_bp.route('/api/playbooks/<path:name>', methods=['DELETE'])
@role_required(['admin'])
def delete_playbook(name):
    """Удалить playbook"""
    import os
    path = os.path.join(runner.playbooks_dir, name)
    if os.path.exists(path):
        os.remove(path)
    return jsonify({'success': True})

@ansible_bp.route('/api/playbooks/<path:name>/run', methods=['POST'])
@role_required(['admin'])
def run_playbook(name):
    """Запустить playbook"""
    data = request.json
    result = runner.run_playbook(
        name,
        device_ids=data.get('device_ids', []),
        extra_vars=data.get('extra_vars', {})
    )
    return jsonify(result)

@ansible_bp.route('/playbook/<path:name>')
@role_required(['admin'])
def edit_playbook_page(name):
    """Страница редактирования playbook"""
    content = runner.get_playbook_content(name)
    if content is None:
        return "Playbook not found", 404
    return render_template('playbook_edit.html', name=name)