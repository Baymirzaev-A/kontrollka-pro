from flask import Blueprint, jsonify, request, session, render_template
from database import db
import redis
import json
import uuid

ansible_bp = Blueprint('ansible', __name__, url_prefix='/ansible')
r = redis.Redis(host='redis', port=6379, decode_responses=True)


@ansible_bp.route('/')
def index():
    return render_template('ansible.html')


@ansible_bp.route('/api/playbooks', methods=['GET'])
def list_playbooks():
    if session.get('role') != 'admin':
        return jsonify({'error': 'Forbidden'}), 403
    playbooks = db.get_playbooks(session.get('username'), session.get('role'))
    return jsonify(playbooks)


@ansible_bp.route('/api/playbooks', methods=['POST'])
def create_playbook():
    if session.get('role') != 'admin':
        return jsonify({'error': 'Forbidden'}), 403

    data = request.json
    name = data.get('name')
    content = data.get('content', '')
    description = data.get('description', '')
    is_shared = data.get('is_shared', False)
    username = session.get('username', 'unknown')

    playbook_id = db.save_playbook(None, name, content, description, is_shared, username)
    return jsonify({'id': playbook_id, 'success': True})


@ansible_bp.route('/api/playbooks/<int:playbook_id>', methods=['GET'])
def get_playbook(playbook_id):
    if session.get('role') != 'admin':
        return jsonify({'error': 'Forbidden'}), 403

    playbook = db.get_playbook(playbook_id)
    if not playbook:
        return jsonify({'error': 'Not found'}), 404
    return jsonify(playbook)


@ansible_bp.route('/api/playbooks/<int:playbook_id>', methods=['PUT'])
def update_playbook(playbook_id):
    if session.get('role') != 'admin':
        return jsonify({'error': 'Forbidden'}), 403

    data = request.json
    name = data.get('name')
    content = data.get('content')
    description = data.get('description', '')
    is_shared = data.get('is_shared', False)
    username = session.get('username', 'unknown')

    db.save_playbook(playbook_id, name, content, description, is_shared, username)
    return jsonify({'success': True})


@ansible_bp.route('/api/playbooks/<int:playbook_id>', methods=['DELETE'])
def delete_playbook(playbook_id):
    if session.get('role') != 'admin':
        return jsonify({'error': 'Forbidden'}), 403

    db.delete_playbook(playbook_id)
    return jsonify({'success': True})


@ansible_bp.route('/api/playbooks/<int:playbook_id>/run', methods=['POST'])
def run_playbook(playbook_id):
    if session.get('role') != 'admin':
        return jsonify({'error': 'Forbidden'}), 403

    playbook = db.get_playbook(playbook_id)
    if not playbook:
        return jsonify({'error': 'Not found'}), 404

    data = request.json
    device_ids = data.get('device_ids', [])

    # Получаем устройства
    if device_ids:
        devices_data = [db.get_device(id) for id in device_ids if db.get_device(id)]
    else:
        devices_data = db.get_all_devices()

    if not devices_data:
        return jsonify({'success': False, 'error': 'Нет устройств для выполнения'}), 400

    task_id = str(uuid.uuid4())
    task = {
        'task_id': task_id,
        'playbook_id': playbook_id,
        'playbook_name': playbook['name'],
        'playbook_content': playbook['content'],
        'devices_data': devices_data,
        'extra_vars': data.get('extra_vars', {}),
        'executed_by': session.get('username')
    }

    # Отправляем задачу в очередь
    r.lpush('ansible:tasks', json.dumps(task))

    # Возвращаем task_id сразу, не ждем результат
    return jsonify({'task_id': task_id, 'status': 'started'})

@ansible_bp.route('/api/playbooks/task/<task_id>/status', methods=['GET'])
def get_task_status(task_id):
    """Получить статус выполнения задачи"""
    result = r.get(f'ansible:result:{task_id}')
    if result:
        return jsonify(json.loads(result))
    return jsonify({'status': 'running', 'task_id': task_id})


@ansible_bp.route('/result/<task_id>')
def result_page(task_id):
    """Страница с результатом выполнения playbook"""
    result = r.get(f'ansible:result:{task_id}')
    if not result:
        return render_template('result.html', task_id=task_id, result=None, not_found=True)

    result_data = json.loads(result)
    return render_template('result.html', task_id=task_id, result=result_data, not_found=False)

@ansible_bp.route('/playbook/<int:playbook_id>')
def edit_playbook_page(playbook_id):
    if session.get('role') != 'admin':
        return "Forbidden", 403

    playbook = db.get_playbook(playbook_id)
    if not playbook:
        return "Playbook not found", 404
    return render_template('playbook_edit.html', playbook=playbook)