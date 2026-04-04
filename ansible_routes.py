from flask import Blueprint, jsonify, request, session
from database import db
import redis
import json
import uuid

ansible_bp = Blueprint('ansible', __name__, url_prefix='/ansible')
r = redis.Redis(host='redis', port=6379, decode_responses=True)


@ansible_bp.route('/api/playbooks', methods=['GET'])
def list_playbooks():
    if session.get('role') != 'admin':
        return jsonify({'error': 'Forbidden'}), 403
    playbooks = db.get_playbooks(session.get('username'), session.get('role'))
    return jsonify(playbooks)


@ansible_bp.route('/api/playbooks/<int:playbook_id>/run', methods=['POST'])
def run_playbook(playbook_id):
    if session.get('role') != 'admin':
        return jsonify({'error': 'Forbidden'}), 403

    playbook = db.get_playbook(playbook_id)
    if not playbook:
        return jsonify({'error': 'Not found'}), 404

    data = request.json
    device_ids = data.get('device_ids', [])

    devices_data = [db.get_device(id) for id in device_ids if db.get_device(id)]

    task = {
        'task_id': str(uuid.uuid4()),
        'playbook_id': playbook_id,
        'playbook_name': playbook['name'],
        'playbook_content': playbook['content'],
        'devices_data': devices_data,
        'extra_vars': data.get('extra_vars', {}),
        'executed_by': session.get('username')
    }

    r.lpush('ansible:tasks', json.dumps(task))

    # Ждём результат (или возвращаем task_id)
    for _ in range(60):
        result = r.get(f'ansible:result:{task["task_id"]}')
        if result:
            return jsonify(json.loads(result))

    return jsonify({'success': False, 'error': 'Timeout'})