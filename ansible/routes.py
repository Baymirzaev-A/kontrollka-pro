from flask import Blueprint, render_template, jsonify, request, session
from functools import wraps
from database import db
import uuid

ansible_bp = Blueprint('ansible', __name__, url_prefix='/ansible')


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
    return render_template('ansible.html')


@ansible_bp.route('/api/playbooks', methods=['GET'])
@role_required(['admin'])
def list_playbooks():
    """Список playbooks из БД"""
    username = session.get('username', 'unknown')
    user_role = session.get('role', 'viewer')
    playbooks = db.get_playbooks(username, user_role)
    return jsonify(playbooks)


@ansible_bp.route('/api/playbooks/<int:playbook_id>', methods=['GET'])
@role_required(['admin'])
def get_playbook(playbook_id):
    """Получить playbook по ID"""
    playbook = db.get_playbook(playbook_id)
    if playbook is None:
        return jsonify({'error': 'Playbook not found'}), 404
    return jsonify(playbook)


@ansible_bp.route('/api/playbooks', methods=['POST'])
@role_required(['admin'])
def create_playbook():
    """Создать новый playbook"""
    data = request.json
    name = data.get('name')
    content = data.get('content',
                       '---\n- name: New playbook\n  hosts: all\n  tasks:\n    - name: Task\n      debug:\n        msg: "Hello"')
    description = data.get('description', '')
    is_shared = data.get('is_shared', False)
    username = session.get('username', 'unknown')

    playbook_id = db.save_playbook(None, name, content, description, is_shared, username)
    return jsonify({'id': playbook_id, 'success': True})


@ansible_bp.route('/api/playbooks/<int:playbook_id>', methods=['PUT'])
@role_required(['admin'])
def update_playbook(playbook_id):
    """Обновить playbook"""
    data = request.json
    name = data.get('name')
    content = data.get('content')
    description = data.get('description', '')
    is_shared = data.get('is_shared', False)
    username = session.get('username', 'unknown')

    db.save_playbook(playbook_id, name, content, description, is_shared, username)
    return jsonify({'success': True})


@ansible_bp.route('/api/playbooks/<int:playbook_id>', methods=['DELETE'])
@role_required(['admin'])
def delete_playbook(playbook_id):
    """Удалить playbook"""
    db.delete_playbook(playbook_id)
    return jsonify({'success': True})


@ansible_bp.route('/api/playbooks/<int:playbook_id>/run', methods=['POST'])
@role_required(['admin'])
def run_playbook(playbook_id):
    """Запустить playbook"""
    data = request.json
    device_ids = data.get('device_ids', [])
    extra_vars = data.get('extra_vars', {})
    username = session.get('username', 'unknown')

    # Получаем содержимое playbook из БД
    playbook = db.get_playbook(playbook_id)
    if not playbook:
        return jsonify({'success': False, 'error': 'Playbook not found'}), 404

    # Отправляем задачу в Redis (для ansible-worker)
    import redis
    import json
    import uuid

    r = redis.Redis(host='redis', port=6379, decode_responses=True)
    task_id = str(uuid.uuid4())

    # Получаем данные устройств
    devices_data = []
    for device_id in device_ids:
        device = db.get_device(device_id)
        if device:
            devices_data.append(device)

    task = {
        'task_id': task_id,
        'playbook_id': playbook_id,
        'playbook_name': playbook['name'],
        'playbook_content': playbook['content'],
        'device_ids': device_ids,
        'devices_data': devices_data,
        'extra_vars': extra_vars,
        'executed_by': username
    }

    r.lpush('ansible:tasks', json.dumps(task))

    # Ждём результат (или возвращаем task_id для async)
    import time
    for _ in range(60):
        result = r.get(f'ansible:result:{task_id}')
        if result:
            result_data = json.loads(result)
            # Сохраняем историю
            db.save_ansible_history(
                playbook_name=playbook['name'],
                device_ids=device_ids,
                extra_vars=extra_vars,
                executed_by=username,
                success=result_data.get('success', False),
                stdout=result_data.get('stdout', ''),
                stderr=result_data.get('stderr', '')
            )
            return jsonify(result_data)
        time.sleep(1)

    return jsonify({'success': False, 'error': 'Task timeout'})


@ansible_bp.route('/playbook/<int:playbook_id>')
@role_required(['admin'])
def edit_playbook_page(playbook_id):
    """Страница редактирования playbook"""
    playbook = db.get_playbook(playbook_id)
    if playbook is None:
        return "Playbook not found", 404
    return render_template('playbook_edit.html', playbook=playbook)