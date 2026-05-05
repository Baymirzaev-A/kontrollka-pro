import eventlet
eventlet.monkey_patch()
from flask import Flask, render_template, request, jsonify, session, redirect, url_for, send_file
from netmiko import ConnectHandler
import logging
import time
import threading
import io
import re
import json
import os
import redis
from functools import wraps
from datetime import datetime
from database import DeviceDB
from werkzeug.utils import secure_filename
# Импортируем загрузчик скриптов
from scripts import get_all_scripts, get_script
import secrets
import sqlite3
# ===== ЗАГРУЗКА ПЕРЕМЕННЫХ ИЗ .env =====
from dotenv import load_dotenv
load_dotenv()
from models.user import UserModel
from auth import authenticate, AUTH_MODE
from celery_app import execute_group_command_parallel

print("=== DEBUG ===")
print(f"AUTH_MODE = {os.environ.get('AUTH_MODE')}")
print(f"LDAP_SERVER = {os.environ.get('LDAP_SERVER')}")
print("=============")

ALLOWED_EXTENSIONS = {'py'}

app = Flask(__name__)

from ansible_routes import ansible_bp
app.register_blueprint(ansible_bp)

# ===== БЕЗОПАСНЫЙ SECRET_KEY =====
SECRET_KEY = os.environ.get('SECRET_KEY')

if not SECRET_KEY:
    # Если ключ не задан, пробуем прочитать из файла
    secret_file = os.path.join(os.path.dirname(__file__), '.secret_key')
    if os.path.exists(secret_file):
        with open(secret_file, 'r') as f:
            SECRET_KEY = f.read().strip()
    else:
        # Генерируем новый и сохраняем
        SECRET_KEY = secrets.token_hex(32)
        with open(secret_file, 'w') as f:
            f.write(SECRET_KEY)
        print(f"⚠️  Сгенерирован новый SECRET_KEY. Сохранен в {secret_file}")

app.secret_key = SECRET_KEY

app.config['MAX_CONTENT_LENGTH'] = 100 * 1024 * 1024
app.config['PERMANENT_SESSION_LIFETIME'] = 3600

# ===== АВТОМАТИЧЕСКИЙ РЕДИРЕКТ HTTP → HTTPS =====

cert_file = os.environ.get('SSL_CERT', 'certs/cert.pem')
key_file = os.environ.get('SSL_KEY', 'certs/key.pem')
HAS_SSL = os.path.exists(cert_file) and os.path.exists(key_file)

if HAS_SSL:
    @app.before_request
    def before_request_handler():
        # 1. Редирект HTTP → HTTPS
        if not request.is_secure:
            if request.path == '/health':
                return
            url = request.url.replace('http://', 'https://', 1)
            return redirect(url, code=301)

        # 2. Проверка сессии (только для HTTPS)
        if request.endpoint in ['login', 'static']:
            return

        if not session.get('logged_in'):
            return

        username = session.get('username')

        # Проверка режима аутентификации
        if session.get('auth_mode') != AUTH_MODE:
            session.clear()
            logger.warning(f"Режим аутентификации изменился, сессия завершена")
            return redirect(url_for('login'))

        # Для LDAP режима — проверяем пользователя в AD
        if AUTH_MODE == 'ldap' and username:
            from auth.ldap_auth import find_user_dn
            from auth import map_group_to_role

            user_info = find_user_dn(username)

            if not user_info:
                session.clear()
                logger.warning(f"Пользователь {username} удален из AD, сессия завершена")
                return redirect(url_for('login'))

            new_role = map_group_to_role(user_info['groups'])
            if session.get('role') != new_role:
                session['role'] = new_role
                logger.info(f"Роль пользователя {username} обновлена: {new_role}")

        # Для local режима — проверяем пользователя в БД
        elif AUTH_MODE == 'local' and username:
            from models.user import UserModel
            user_model = UserModel()
            user = user_model.get_user(username)

            if not user:
                session.clear()
                logger.warning(f"Локальный пользователь {username} не найден, сессия завершена")
                return redirect(url_for('login'))

    # HSTS заголовок
    @app.after_request
    def add_hsts_header(response):
        response.headers['Strict-Transport-Security'] = 'max-age=31536000; includeSubDomains'
        return response

    print("🔒 HTTPS режим включен. HTTP → HTTPS редирект активен.")

else:
    @app.before_request
    def before_request_handler():
        # Только проверка сессии (без HTTPS редиректа)
        if request.endpoint in ['login', 'static']:
            return


        if not session.get('logged_in'):
            return

        username = session.get('username')

        if session.get('auth_mode') != AUTH_MODE:
            session.clear()
            logger.warning(f"Режим аутентификации изменился, сессия завершена")
            return redirect(url_for('login'))

        if AUTH_MODE == 'ldap' and username:
            from auth.ldap_auth import find_user_dn
            from auth import map_group_to_role

            user_info = find_user_dn(username)
            if not user_info:
                session.clear()
                logger.warning(f"Пользователь {username} удален из AD, сессия завершена")
                return redirect(url_for('login'))

            new_role = map_group_to_role(user_info['groups'])
            if session.get('role') != new_role:
                session['role'] = new_role
                logger.info(f"Роль пользователя {username} обновлена: {new_role}")

        elif AUTH_MODE == 'local' and username:
            from models.user import UserModel
            user_model = UserModel()
            user = user_model.get_user(username)

            if not user:
                session.clear()
                logger.warning(f"Локальный пользователь {username} не найден, сессия завершена")
                return redirect(url_for('login'))

    print("⚠️  HTTP режим (без HTTPS). Редирект не активен.")


# ==== КОНФИГУРАЦИЯ ГРУППОВЫХ ОПЕРАЦИЙ ====
MAX_DEVICES_PER_GROUP = 40   # Максимум устройств за один запрос к API

# ===== ЗАПРЕЩЕННЫЕ КОМАНДЫ В КОНСОЛИ =====
DANGEROUS_COMMANDS = [
    'write', 'save', 'reload', 'reboot', 'restart',
    'configure', 'config', 'system-view', 'commit',
    'delete', 'remove', 'erase', 'format',
    'reset', 'clear', 'default', 'no ',
    'shutdown', 'undoshutdown', 'interface range',
    'copy', 'move', 'rename', 'mkdir', 'rmdir',
    'enable', 'disable', 'username', 'password',
    'ip route', 'route', 'vlan', 'vlan database',
    'snmp-server', 'logging', 'ntp', 'clock'
]


def is_dangerous_command(command):
    """Проверяет, является ли команда опасной (только для консоли)"""
    if not command:
        return False

    cmd_lower = command.lower().strip()

    # Разрешаем show/display команды
    if cmd_lower.startswith(('show', 'display', 'ping', 'traceroute', 'tracert', 'dir')):
        return False

    # Проверяем на опасные
    for dangerous in DANGEROUS_COMMANDS:
        if cmd_lower.startswith(dangerous) or f' {dangerous}' in cmd_lower:
            return True

    return False


# Кэш для активных соединений
active_connections = {}
connection_lock = threading.Lock()

class DeviceConnection:
    def __init__(self, device_id, connection):
        self.device_id = device_id
        self.connection = connection
        self.last_used = time.time()

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# ===== КЕШ СТАТУСОВ В REDIS =====
# Подключение к Redis (один раз при старте)
redis_client = redis.Redis(
    host='redis',
    port=6379,
    decode_responses=True
)

from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

# Rate Limiter с хранением в Redis
limiter = Limiter(
    app,          # ключ = IP пользователя
    default_limits=["200 per minute", "10 per second"],
    storage_uri=os.getenv('REDIS_URL', 'redis://redis:6379')
)

# Проверяем подключение к Redis
try:
    redis_client.ping()
    logger.info("✅ Redis подключен для кеширования статусов")
except Exception as e:
    logger.error(f"❌ Ошибка подключения к Redis: {e}")

def get_cached_statuses(force=False):
    """Получает статусы устройств из Redis кеша"""
    cache_key = "device_statuses"

    # Если не принудительное обновление - пробуем взять из кеша
    if not force:
        cached = redis_client.get(cache_key)
        if cached:
            return json.loads(cached)

    # Обновляем кеш - проверяем все устройства
    devices = get_cached_devices()
    if devices:
        statuses = check_devices_status(devices)
        # Сохраняем в Redis на 20 секунд
        redis_client.setex(cache_key, 20, json.dumps(statuses))
        return statuses

    return {}

# ===== ФОНОВАЯ ПРОВЕРКА СТАТУСОВ =====
def background_status_check():
    """Фоновая проверка статусов устройств (раз в 20 секунд)"""
    while True:
        time.sleep(20)
        try:
            # Принудительно обновляем кеш в Redis
            statuses = get_cached_statuses(force=True)
            # Отправляем статусы всем подключенным клиентам
            socketio.emit('status_update', statuses)
        except Exception as e:
            logger.error(f"Ошибка в фоновой проверке статусов: {e}")

# Запускаем фоновый поток
status_thread = threading.Thread(target=background_status_check, daemon=True)
status_thread.start()

from flask_socketio import SocketIO, emit

# Настройка SocketIO с поддержкой Redis (если указан)
redis_url = os.environ.get('REDIS_URL')
if redis_url:
    socketio = SocketIO(
        app,
        cors_allowed_origins="*",
        manage_session=False,
        message_queue=redis_url,
        async_mode='eventlet'
    )
    logger.info(f"✅ SocketIO настроен с Redis: {redis_url}")
else:
    socketio = SocketIO(
        app,
        cors_allowed_origins="*",
        manage_session=False
    )
    logger.info("✅ SocketIO настроен без Redis (режим разработки)")

@socketio.on('connect')
def handle_connect():
    print(f"Client connected")


# ===== WEBSOCKET ПОДПИСКИ ДЛЯ SNMP ОБНОВЛЕНИЙ =====
from collections import defaultdict
import redis

device_subscribers = defaultdict(list)


@socketio.on('connect')
def handle_connect():
    print(f"Client connected")


@socketio.on('disconnect')
def handle_disconnect():
    print(f"Client disconnected")
    # Очищаем подписки при отключении
    for device_id, subscribers in list(device_subscribers.items()):
        if request.sid in subscribers:
            subscribers.remove(request.sid)


@socketio.on('subscribe_device')
def handle_subscribe_device(data):
    device_id = data.get('device_id')
    if device_id and request.sid:
        if device_id not in device_subscribers:
            device_subscribers[device_id] = []
        if request.sid not in device_subscribers[device_id]:
            device_subscribers[device_id].append(request.sid)
            logger.info(f"Client {request.sid} subscribed to device {device_id}")


@socketio.on('unsubscribe_device')
def handle_unsubscribe_device(data):
    device_id = data.get('device_id')
    if device_id and request.sid in device_subscribers.get(device_id, []):
        device_subscribers[device_id].remove(request.sid)
        logger.info(f"Client {request.sid} unsubscribed from device {device_id}")


def start_redis_listener():
    try:
        r = redis.Redis(host='redis', port=6379, decode_responses=True)
        pubsub = r.pubsub()
        pubsub.subscribe('daria:device:updated')

        batch = []
        last_batch_time = time.time()

        for message in pubsub.listen():
            if message['type'] == 'message':
                try:
                    data = json.loads(message['data'])
                    batch.append(data)

                    now = time.time()
                    if len(batch) >= 10 or (now - last_batch_time) > 1:
                        for update in batch:
                            device_id = update.get('device_id')
                            for sid in device_subscribers.get(device_id, []):
                                socketio.emit('snmp_updated', update, room=sid)
                        batch = []
                        last_batch_time = now
                except Exception as e:
                    logger.error(f"Error processing Redis message: {e}")
    except Exception as e:
        logger.error(f"Redis listener error: {e}")


# Запускаем Redis listener в отдельном потоке
threading.Thread(target=start_redis_listener, daemon=True).start()

db = DeviceDB()

# ==== КЕШ ДЛЯ УСТРОЙСТВ ====
devices_cache = {
    'data': None,
    'timestamp': 0,
    'ttl': 300  # 5 минут (можно настроить)
}


def invalidate_devices_cache():
    """Сбрасывает кеш устройств"""
    global devices_cache
    devices_cache['data'] = None
    devices_cache['timestamp'] = 0
    logger.info("🧹 Кеш устройств сброшен")


def get_cached_devices():
    """Получает устройства из кеша или из БД"""
    global devices_cache
    current_time = time.time()

    # Проверяем, нужно ли обновить кеш
    if (devices_cache['data'] is None or
            current_time - devices_cache['timestamp'] > devices_cache['ttl']):
        devices_cache['data'] = db.get_all_devices()
        devices_cache['timestamp'] = current_time
        logger.info(f"🔄 Кеш устройств обновлен ({len(devices_cache['data'])} устройств)")

    return devices_cache['data']


# ===== НАСТРОЙКИ ПОДКЛЮЧЕНИЯ К ОБОРУДОВАНИЮ =====
DEVICE_USERNAME = os.environ.get('DEVICE_USERNAME', 'admin')
DEVICE_PASSWORD = os.environ.get('DEVICE_PASSWORD', 'admin')
DEVICE_ENABLE = os.environ.get('DEVICE_ENABLE', None)

# Отдельные учетные данные для серверов
SERVER_USERNAME = os.environ.get('SERVER_USERNAME', 'root')
SERVER_PASSWORD = os.environ.get('SERVER_PASSWORD', '')
SERVER_KEY_FILE = os.environ.get('SERVER_KEY_FILE', None)  # опционально: путь к SSH-ключу

def get_device_params(device):
    """Возвращает параметры подключения с учётом особенностей вендора"""
    # Для серверов используем отдельные учетные данные
    if device.get('purpose') == 'server':
        username = SERVER_USERNAME
        password = SERVER_PASSWORD
        enable = None  # на серверах нет enable
        device_type = 'linux' if device.get('device_type') == 'linux' else 'generic_termserver'
    else:
        username = DEVICE_USERNAME
        password = DEVICE_PASSWORD
        enable = DEVICE_ENABLE
        device_type = device['device_type']


    params = {
        'device_type': device['device_type'],
        'host': device['host'],
        'port': device.get('port', 22),
        'username': DEVICE_USERNAME,
        'password': DEVICE_PASSWORD,
        'timeout': 30,
        'session_timeout': 60,
        'global_delay_factor': 2,
    }

    # SSH-ключ для серверов (если задан)
    if device.get('purpose') == 'server' and SERVER_KEY_FILE and os.path.exists(SERVER_KEY_FILE):
        params['use_keys'] = True
        params['key_file'] = SERVER_KEY_FILE

    # Особенности для разных вендоров
    if device['device_type'] == 'cisco_asa':
        params['global_delay_factor'] = 3  # ASA медленнее
    elif device['device_type'] == 'juniper':
        params['global_delay_factor'] = 2
        params['cmd_verify'] = False  # Juniper не требует подтверждения
    elif device['device_type'] == 'mikrotik_routeros':
        # MikroTik требует специального подхода
        params['device_type'] = 'generic_termserver'
        params['username'] = DEVICE_USERNAME
        params['password'] = DEVICE_PASSWORD
    elif device['device_type'] in ['huawei_olt', 'huawei_smartax']:
        params['global_delay_factor'] = 3
        params['timeout'] = 60
    elif device['device_type'].startswith('brocade'):
        params['global_delay_factor'] = 2

    if DEVICE_ENABLE and device['device_type'] not in ['juniper', 'mikrotik_routeros', 'linux']:
        params['secret'] = DEVICE_ENABLE

    return params


# ===== ДЕКОРАТОРЫ ДЛЯ ПРОВЕРКИ АВТОРИЗАЦИИ =====
def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get('logged_in'):
            return redirect(url_for('login'))
        return f(*args, **kwargs)

    return decorated_function


def role_required(allowed_roles):
    """Декоратор для проверки ролей"""

    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            if not session.get('logged_in'):
                return redirect(url_for('login'))

            user_role = session.get('role', 'viewer')
            if user_role not in allowed_roles:
                return jsonify({'error': 'Доступ запрещен. Недостаточно прав.'}), 403

            return f(*args, **kwargs)

        return decorated_function

    return decorator


# Функция для выполнения команд с пагинацией и таймаутами
def execute_long_command(connection, command):
    """Выполняет команду с обработкой пагинации (с учётом вендоров)"""
    logger.info(f"Выполнение команды: {command}")

    # Определяем тип устройства, если есть
    device_type = getattr(connection, 'device_type', 'unknown')

    # Особенности для Juniper (нет --More--, промпт >)
    if 'juniper' in device_type:
        try:
            output = connection.send_command(command, strip_command=False)
            logger.info(f"Команда на Juniper выполнена, получено {len(output)} символов")
            return output
        except Exception as e:
            logger.error(f"Ошибка выполнения на Juniper: {e}")
            raise

    # Стандартная обработка для всех остальных
    try:
        # Отправляем команду и ждем промпт
        output = connection.send_command_timing(
            command,
            strip_prompt=False,
            strip_command=False,
            last_read=2
        )

        full_output = output

        # Разные форматы пагинации
        more_patterns = [
            r'--More--',
            r'---- More ----',
            r'--- more ---',
            r'<--- More --->',
            r'--More \(space to view, q to quit\)--'
        ]

        max_pages = 50
        page_count = 0

        while page_count < max_pages:
            has_more = any(re.search(p, full_output, re.IGNORECASE) for p in more_patterns)
            if has_more:
                more_output = connection.send_command_timing(
                    ' ',
                    strip_prompt=False,
                    strip_command=False,
                    last_read=1
                )
                if more_output:
                    full_output += more_output
                page_count += 1
            else:
                break

        logger.info(f"Команда выполнена, получено {len(full_output)} символов")
        return full_output

    except Exception as e:
        logger.error(f"Ошибка: {str(e)}")
        raise

# ==== СТРАНИЦА ВХОДА ====
@app.route('/login', methods=['GET', 'POST'])
def login():
    # Если уже залогинен - отправляем на главную
    if session.get('logged_in'):
        return redirect(url_for('index'))

    error = None
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')

        # Аутентификация через AD или локальную
        user = authenticate(username, password)

        if user:
            session['logged_in'] = True
            session['username'] = user['username']
            session['role'] = user['role']
            session['full_name'] = user['full_name']
            session['auth_mode'] = AUTH_MODE
            session.permanent = True

            # Сохраняем в БД
            user_model = UserModel()
            user_model.get_or_create_user(
                username=user['username'],
                email=user.get('email', ''),
                full_name=user.get('full_name', ''),
                role=user['role'],
                auth_source='ldap' if AUTH_MODE == 'ldap' else 'local'
            )

            logger.info(f"Пользователь {username} вошел в систему (role: {user['role']})")
            return redirect(url_for('index'))
        else:
            error = 'Неверный логин или пароль'
            logger.warning(f"Неудачная попытка входа: {username}")

    return render_template('login.html', error=error)


@app.route('/logout')
def logout():
    session.pop('logged_in', None)
    session.pop('username', None)
    session.pop('role', None)
    session.pop('full_name', None)
    logger.info("Пользователь вышел из системы")
    return redirect(url_for('login'))


# ==== ОСНОВНЫЕ СТРАНИЦЫ ====
@app.route('/')
@login_required
def index():
    """Главная страница со списком устройств (с кешированием)"""
    devices = get_cached_devices()  # ← используем кеш
    return render_template('index.html', devices=devices)


@app.route('/device/<int:device_id>')
@login_required
def device_console(device_id):
    """Страница консоли для отдельного устройства"""
    device = db.get_device(device_id)
    if not device:
        return redirect(url_for('index'))
    return render_template('console.html', device=device, user_role=session.get('role', 'viewer'))


@app.route('/group')
@login_required
def group_console():
    """Страница для групповых команд"""
    devices = db.get_all_devices()
    return render_template('group.html', devices=devices)


#@app.route('/configs')
#@login_required
#def configs_page():
    #"""Страница со всеми сохраненными конфигурациями"""
    #configs = db.get_all_configs(100)
    #return render_template('configs.html', configs=configs)


#@app.route('/config/<int:config_id>')
#@login_required
#def view_config(config_id):
#    """Страница просмотра конфигурации"""
#    config = db.get_config(config_id)
#    if not config:
#        return redirect(url_for('configs_page'))

#    device = db.get_device(config['device_id'])
#    return render_template('view_config.html', config=config, device=device)


# ==== API ДЛЯ ПРОВЕРКИ СТАТУСА УСТРОЙСТВ ====
# from utils.ping import check_devices_status, ping_device
from utils.tcp_ping import check_devices_status, ping_device

@app.route('/api/devices/check-status', methods=['POST'])
@login_required
def api_check_devices_status():
    """Проверяет доступность выбранных устройств (из кеша)"""
    data = request.json

    # Получаем статусы из кеша
    all_statuses = get_cached_statuses()

    # Фильтруем если нужно
    if data.get('all'):
        statuses = all_statuses
        devices = get_cached_devices()
    else:
        device_ids = data.get('device_ids', [])
        statuses = {did: all_statuses.get(did, False) for did in device_ids}
        devices = [d for d in get_cached_devices() if d['id'] in device_ids]

    if not devices:
        return jsonify({'error': 'Нет устройств для проверки'}), 400

    # Формируем ответ
    result = {}
    for device in devices:
        result[device['id']] = {
            'host': device['host'],
            'name': device['name'],
            'online': statuses.get(device['id'], False)
        }

    online_count = sum(1 for s in statuses.values() if s)
    offline_count = len(devices) - online_count

    return jsonify({
        'success': True,
        'statuses': result,
        'summary': {
            'total': len(devices),
            'online': online_count,
            'offline': offline_count
        }
    })


@app.route('/api/devices/status-batch', methods=['POST'])
@login_required
def api_devices_status_batch():
    """
    Проверяет статусы только для указанных device_ids
    Ожидает: {"device_ids": [1,2,3]}
    """
    data = request.json
    device_ids = data.get('device_ids', [])

    if not device_ids:
        return jsonify({'error': 'Нет device_ids'}), 400

    # Получаем устройства из БД (только нужные)
    devices = []
    for device_id in device_ids:
        device = db.get_device(device_id)
        if device:
            devices.append(device)

    # Проверяем статусы только для этих устройств
    statuses = check_devices_status(devices)

    # Формируем ответ
    result = {}
    for device in devices:
        result[device['id']] = {
            'host': device['host'],
            'name': device['name'],
            'online': statuses.get(device['id'], False)
        }

    return jsonify({
        'success': True,
        'statuses': result
    })

@app.route('/api/device/<int:device_id>/ping', methods=['GET'])
@login_required
def api_ping_device(device_id):
    """
    Проверяет доступность одного устройства
    """
    device = db.get_device(device_id)
    if not device:
        return jsonify({'error': 'Устройство не найдено'}), 404

    is_online = ping_device(device['host'])

    return jsonify({
        'device_id': device_id,
        'name': device['name'],
        'host': device['host'],
        'online': is_online
    })

# ==== API ДЛЯ УПРАВЛЕНИЯ УСТРОЙСТВАМИ ====
@app.route('/api/device/<int:device_id>', methods=['GET'])
@login_required
def get_device(device_id):
    """Получает информацию об устройстве"""
    device = db.get_device(device_id)
    if not device:
        return jsonify({'error': 'Устройство не найдено'}), 404
    return jsonify(device)


@app.route('/api/device/add', methods=['POST'])
@login_required
def add_device():
    """Добавляет новое устройство"""
    data = request.json
    try:
        device_id = db.add_device(
            name=data['name'],
            host=data['host'],
            device_type=data.get('device_type', 'huawei'),
            port=int(data.get('port', 22)),
            description=data.get('description', ''),
            purpose=data.get('purpose', 'router'),
            group=data.get('group'),
            site=data.get('site')
        )
        invalidate_devices_cache()  # ← СБРАСЫВАЕМ КЕШ
        socketio.emit('devices_updated', {
            'action': 'add',
            'device_id': device_id,
            'timestamp': datetime.now().isoformat()
        })
        logger.info(f"✅ Устройство добавлено: {data['name']} (ID: {device_id})")
        return jsonify({'success': True, 'device_id': device_id})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 400


@app.route('/api/device/<int:device_id>/delete', methods=['POST'])
@login_required
def delete_device(device_id):
    """Удаляет устройство"""
    try:
        db.delete_device(device_id)
        invalidate_devices_cache()  # ← СБРАСЫВАЕМ КЕШ
        socketio.emit('devices_updated', {
            'action': 'delete',
            'device_id': device_id,
            'timestamp': datetime.now().isoformat()
        })
        logger.info(f"🗑️ Устройство удалено (ID: {device_id})")
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 400


# ==== API ДЛЯ ВЫПОЛНЕНИЯ КОМАНД ====
@app.route('/api/device/<int:device_id>/execute', methods=['POST'])
@login_required
def execute_command(device_id):
    """Выполнение команды на одном устройстве с сохранением соединения"""
    data = request.json
    command = data.get('command')

    if not command:
        return jsonify({'error': 'Команда не указана'}), 400

        # ===== ПРОВЕРКА НА ОПАСНЫЕ КОМАНДЫ =====
    if is_dangerous_command(command):
        return jsonify({
            'error': '❌ Эта команда запрещена в консоли. Используйте скрипты для изменения конфигурации.',
            'command': command,
            'allowed_only': 'show/display команды и скрипты'
        }), 403

    device = db.get_device(device_id)
    if not device:
        return jsonify({'error': 'Устройство не найдено'}), 404

    # Проверяем, есть ли уже активное соединение
    connection = None
    if device_id in active_connections:
        conn_data = active_connections[device_id]
        # Проверяем, живо ли соединение
        try:
            conn_data.connection.find_prompt()
            connection = conn_data.connection
            logger.info(f"✅ Использую существующее соединение для {device['host']}")
        except:
            logger.info(f"⚠️ Соединение для {device['host']} умерло, создаем новое")
            del active_connections[device_id]
            connection = None

    # Если нет активного соединения - создаем новое
    if connection is None:
        logger.info(f"🔌 Создаю новое соединение для {device['host']}")

        device_params = get_device_params(device)

        try:
            connection = ConnectHandler(**device_params)
            active_connections[device_id] = DeviceConnection(device_id, connection)
            logger.info(f"✅ Новое соединение создано для {device['host']}")
        except Exception as e:
            logger.error(f"❌ Ошибка подключения: {str(e)}")
            return jsonify({'error': f"Ошибка подключения: {str(e)}"}), 500

    try:
        # Обновляем время последнего использования
        active_connections[device_id].last_used = time.time()

        # Выполняем команду
        output = execute_long_command(connection, command)

        # Сохраняем в историю
        username = session.get('username', 'unknown')
        db.save_command_history(device_id, command, output, username)

        return jsonify({
            'success': True,
            'output': output,
            'command': command
        })

    except Exception as e:
        error_msg = str(e)
        logger.error(f"❌ Ошибка выполнения команды: {error_msg}")

        if device_id in active_connections:
            try:
                active_connections[device_id].connection.disconnect()
            except:
                pass
            del active_connections[device_id]

        return jsonify({'error': f"Ошибка: {error_msg}"}), 500


@app.route('/api/group/execute', methods=['POST'])
@login_required
@limiter.limit("5 per minute")
def execute_group_command():
    """Выполнение команды на нескольких выбранных устройствах (параллельно через Celery)"""
    data = request.json
    command = data.get('command')
    device_ids = data.get('device_ids', [])

    if not command:
        return jsonify({'error': 'Команда не указана'}), 400

    # ===== ПРОВЕРКА НА ОПАСНЫЕ КОМАНДЫ =====
    if is_dangerous_command(command):
        return jsonify({
            'error': '❌ Эта команда запрещена в консоли. Используйте скрипты для изменения конфигурации.',
            'command': command
        }), 403

    if not device_ids:
        return jsonify({'error': 'Не выбрано ни одного устройства'}), 400

    if len(device_ids) > MAX_DEVICES_PER_GROUP:
        return jsonify({
            'error': f'Слишком много устройств. Максимум {MAX_DEVICES_PER_GROUP} за раз.',
            'limit': MAX_DEVICES_PER_GROUP,
            'selected': len(device_ids)
        }), 400

    # Получаем параметры подключения для каждого устройства
    devices_info = []
    valid_device_ids = []

    for device_id in device_ids:
        device = db.get_device(device_id)
        if device:
            valid_device_ids.append(device_id)
            devices_info.append(get_device_params(device))

    if not valid_device_ids:
        return jsonify({'error': 'Нет доступных устройств'}), 400

    # Запускаем параллельное выполнение через Celery
    username = session.get('username', 'unknown')
    task_id = execute_group_command_parallel(
        valid_device_ids,
        command,
        username,
        devices_info
    )

    logger.info(f"Group command task created: {task_id}, devices: {len(valid_device_ids)}")

    return jsonify({
        'success': True,
        'task_id': task_id,
        'message': f'Запущено параллельное выполнение на {len(valid_device_ids)} устройствах',
        'devices_count': len(valid_device_ids)
    })

# ==== API ДЛЯ СКРИПТОВ =====
@app.route('/api/scripts')
@login_required
@role_required(['admin', 'operator'])
def list_scripts():
    """Возвращает список доступных скриптов"""
    scripts = get_all_scripts()
    return jsonify([{
        'id': s['id'],
        'name': s['name'],
        'description': s['description']
    } for s in scripts])


@app.route('/api/device/<int:device_id>/execute_script', methods=['POST'])
@login_required
def execute_script(device_id):
    """Выполняет скрипт на одном устройстве"""
    data = request.json
    script_id = data.get('script_id')

    if not script_id:
        return jsonify({'error': 'Не указан ID скрипта'}), 400

    script = get_script(script_id)
    if not script:
        return jsonify({'error': 'Скрипт не найден'}), 404

    device = db.get_device(device_id)
    if not device:
        return jsonify({'error': 'Устройство не найдено'}), 404

    # Подключаемся
    device_params = get_device_params(device)

    connection = None
    try:
        logger.info(f"Выполнение скрипта {script_id} на {device['host']}")
        connection = ConnectHandler(**device_params)

        # Pre-check
        pre_ok, pre_msg = script.pre_check(connection, device)
        if not pre_ok:
            return jsonify({
                'success': False,
                'error': pre_msg,
                'phase': 'pre_check'
            })

        # Execute
        output = script.execute(connection, device)

        # Post-check
        post_ok, post_msg = script.post_check(connection, device)

        # Сохраняем в историю
        username = session.get('username', 'unknown')
        db.save_command_history(device_id, f"SCRIPT: {script.get_name()}", output, username)

        return jsonify({
            'success': True,
            'output': output,
            'post_check': {'success': post_ok, 'message': post_msg} if not post_ok else None
        })

    except Exception as e:
        logger.error(f"Ошибка выполнения скрипта: {str(e)}")
        return jsonify({'error': str(e)}), 500

    finally:
        if connection:
            connection.disconnect()


@app.route('/api/group/execute_script', methods=['POST'])
@login_required
def execute_group_script():
    """Выполняет скрипт на нескольких выбранных устройствах"""
    data = request.json
    script_id = data.get('script_id')
    device_ids = data.get('device_ids', [])

    if not script_id:
        return jsonify({'error': 'Не указан ID скрипта'}), 400

    if not device_ids:
        return jsonify({'error': 'Не выбрано ни одного устройства'}), 400

    script = get_script(script_id)
    if not script:
        return jsonify({'error': 'Скрипт не найден'}), 404

    results = []

    for device_id in device_ids:
        device = db.get_device(device_id)
        if not device:
            results.append({
                'device_id': device_id,
                'device_name': 'Неизвестно',
                'success': False,
                'error': 'Устройство не найдено'
            })
            continue

        device_params = get_device_params(device)

        connection = None
        try:
            connection = ConnectHandler(**device_params)

            # Pre-check
            pre_ok, pre_msg = script.pre_check(connection, device)
            if not pre_ok:
                results.append({
                    'device_id': device_id,
                    'device_name': device['name'],
                    'success': False,
                    'error': f"Pre-check failed: {pre_msg}"
                })
                continue

            # Execute
            output = script.execute(connection, device)

            # Post-check
            post_ok, post_msg = script.post_check(connection, device)

            # Сохраняем в историю
            username = session.get('username', 'unknown')
            db.save_command_history(device_id, f"SCRIPT: {script.get_name()}", output, username)

            results.append({
                'device_id': device_id,
                'device_name': device['name'],
                'success': True,
                'output': output,
                'post_check': {'success': post_ok, 'message': post_msg} if not post_ok else None
            })

        except Exception as e:
            logger.error(f"Ошибка для устройства {device['name']}: {str(e)}")
            results.append({
                'device_id': device_id,
                'device_name': device['name'],
                'success': False,
                'error': str(e)
            })
        finally:
            if connection:
                connection.disconnect()

    return jsonify({'success': True, 'results': results})


@app.route('/api/device/<int:device_id>/save_config', methods=['POST'])
@login_required
def save_config(device_id):
    """Сохраняет конфигурацию"""
    device = db.get_device(device_id)
    if not device:
        return jsonify({'error': 'Устройство не найдено'}), 404

    device_params = {
        'device_type': device['device_type'],
        'host': device['host'],
        'port': device['port'],
        'username': DEVICE_USERNAME,
        'password': DEVICE_PASSWORD,
        'timeout': 30,
        'session_timeout': 60,
        'global_delay_factor': 2,
    }

    if DEVICE_ENABLE:
        device_params['secret'] = DEVICE_ENABLE

    connection = None
    try:
        connection = ConnectHandler(**device_params)

        commands_to_try = ['display current-configuration', 'display cur', 'disp cur']
        output = ""

        for cmd in commands_to_try:
            try:
                output = execute_long_command(connection, cmd)
                if output and "Error" not in output:
                    break
            except:
                continue

        if output:
            username = session.get('username', 'unknown')
            config_id = db.save_config(device_id, output, username)
            return jsonify({'success': True, 'message': 'Конфигурация сохранена', 'config_id': config_id})
        else:
            return jsonify({'error': 'Не удалось получить конфигурацию'}), 500

    except Exception as e:
        return jsonify({'error': str(e)}), 500
    finally:
        if connection:
            connection.disconnect()


# ==== API ДЛЯ ИСТОРИИ ====
@app.route('/api/device/<int:device_id>/history')
@login_required
def get_history(device_id):
    """Возвращает историю команд с пагинацией"""
    page = request.args.get('page', 1, type=int)
    per_page = request.args.get('per_page', 50, type=int)
    history = db.get_command_history(device_id, page, per_page)
    return jsonify(history)


@app.route('/api/device/<int:device_id>/configs')
@login_required
def get_configs(device_id):
    """Возвращает историю сохраненных конфигураций с пагинацией"""
    page = request.args.get('page', 1, type=int)
    per_page = request.args.get('per_page', 20, type=int)
    configs = db.get_config_history(device_id, page, per_page)
    return jsonify(configs)

@app.route('/api/configs/list')
@login_required
def api_configs_list():
    """Возвращает список всех конфигураций с пагинацией"""
    page = request.args.get('page', 1, type=int)
    per_page = request.args.get('per_page', 50, type=int)
    configs = db.get_all_configs(page, per_page)
    return jsonify(configs)

@app.route('/api/config/<int:config_id>/download')
@login_required
def download_config(config_id):
    """Скачивает конфигурацию"""
    config = db.get_config(config_id)
    if not config:
        return jsonify({'error': 'Конфигурация не найдена'}), 404

    device = db.get_device(config['device_id'])
    filename = f"{device['name']}_config_{config['saved_at'][:10]}.txt"

    return send_file(
        io.BytesIO(config['config_text'].encode('utf-8')),
        mimetype='text/plain',
        as_attachment=True,
        download_name=filename
    )


# ==== API ДЛЯ СКРИПТОВ (НОВЫЕ) ====
@app.route('/api/scripts/list')
@login_required
@role_required(['admin', 'operator'])
def api_list_scripts():
    """Возвращает список всех скриптов с именами файлов"""
    import os
    from scripts import get_all_scripts

    scripts = get_all_scripts()
    result = []

    for script in scripts:
        # Получаем имя файла из ID скрипта
        script_id = script['id']
        module_name = script_id.split('.')[0]
        filename = f"{module_name}.py"

        result.append({
            'id': script_id,
            'name': script['name'],
            'description': script['description'],
            'filename': filename
        })

    return jsonify(result)


@app.route('/api/scripts/<path:script_id>/download')
@login_required
@role_required(['admin', 'operator'])
def download_script(script_id):
    """Скачивает файл скрипта"""
    import os
    from scripts import get_script

    script = get_script(script_id)
    if not script:
        return jsonify({'error': 'Скрипт не найден'}), 404

    # Получаем имя модуля из ID
    module_name = script_id.split('.')[0]

    # Путь к файлу скрипта
    script_path = os.path.join('scripts', f"{module_name}.py")

    if not os.path.exists(script_path):
        return jsonify({'error': 'Файл скрипта не найден'}), 404

    try:
        with open(script_path, 'r', encoding='utf-8') as f:
            content = f.read()

        # Создаем имя файла для скачивания
        download_name = f"{module_name}.py"

        return send_file(
            io.BytesIO(content.encode('utf-8')),
            mimetype='text/plain',
            as_attachment=True,
            download_name=download_name
        )
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/scripts')
@login_required
@role_required(['admin', 'operator'])
def scripts_page():
    """Страница со списком скриптов"""
    return render_template('scripts.html')


def cleanup_old_connections():
    """Закрывает соединения, которые не использовались больше 5 минут"""
    while True:
        time.sleep(60)  # Проверяем раз в минуту
        current_time = time.time()
        to_delete = []

        for device_id, conn_data in active_connections.items():
            if current_time - conn_data.last_used > 300:  # 5 минут
                logger.info(f"🧹 Закрываю неактивное соединение для устройства {device_id}")
                try:
                    conn_data.connection.disconnect()
                except:
                    pass
                to_delete.append(device_id)

        for device_id in to_delete:
            del active_connections[device_id]


@app.route('/api/scripts/upload', methods=['POST'])
@login_required
@role_required(['admin', 'operator'])
def script_upload():
    """Загрузка скрипта .py"""
    try:
        if 'script_file' not in request.files:
            return jsonify({'error': 'Нет файла'}), 400

        file = request.files['script_file']
        if file.filename == '':
            return jsonify({'error': 'Файл не выбран'}), 400

        # Проверяем расширение
        if not file.filename.endswith('.py'):
            return jsonify({'error': 'Только .py файлы разрешены'}), 400

        # Безопасное имя файла
        filename = secure_filename(file.filename)

        # Путь к папке scripts (относительно app.py)
        scripts_dir = os.path.join(os.path.dirname(__file__), 'scripts')

        # Убедимся что папка существует
        if not os.path.exists(scripts_dir):
            os.makedirs(scripts_dir)

        # Полный путь к файлу
        filepath = os.path.join(scripts_dir, filename)

        # Если файл уже существует - добавляем номер
        counter = 1
        original_name = filename
        while os.path.exists(filepath):
            name, ext = os.path.splitext(original_name)
            filepath = os.path.join(scripts_dir, f"{name}_{counter}{ext}")
            counter += 1

        # Сохраняем файл
        file.save(filepath)

        # Сбрасываем кэш скриптов
        try:
            from scripts import _scripts_cache
            _scripts_cache = None
        except ImportError:
            pass

        return jsonify({
            'success': True,
            'filename': os.path.basename(filepath),
            'message': '✅ Скрипт успешно загружен'
        })

    except Exception as e:
        logger.error(f"❌ Ошибка загрузки скрипта: {str(e)}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/scripts/template/download', methods=['GET'])
@login_required
@role_required(['admin', 'operator'])
def download_script_template():
    """Скачивание шаблона скрипта"""
    template_content = '''from .base_script import BaseScript
import logging

logger = logging.getLogger(__name__)

class ScriptName(BaseScript):
    """
    Шаблон для создания своего скрипта
    Замените название класса, методы и добавьте свою логику
    """

    def get_name(self):
        """Название скрипта (будет отображаться в списке)"""
        return "Название скрипта"

    def get_description(self):
        """Описание скрипта"""
        return "Описание скрипта"

    def execute(self, connection, device_info):
        """
        Основная логика скрипта
        connection - уже подключенное устройство
        device_info - информация об устройстве
        """
        logger.info(f"Выполнение на {device_info['name']}")

        # Твой код здесь
        commands = [
            "system-view",
            "команда1",
            "команда2",
            "return"
        ]

        results = []
        for cmd in commands:
            output = connection.send_command_timing(cmd, strip_command=False)
            results.append(f"> {cmd}\\n{output}")
            # Обработка подтверждений
            if "[Y/N]" in output:
                output = connection.send_command_timing("Y", strip_command=False)
                results.append(f"> Y\\n{output}")

        return "\\n".join(results)

    def pre_check(self, connection, device_info):
        """
        Проверка перед выполнением (опционально)
        Верни (True, "OK") если можно выполнять
        Верни (False, "Причина") если нельзя
        """
        return True, "OK"

    def post_check(self, connection, device_info):
        """
        Проверка после выполнения (опционально)
        """
        return True, "OK"
'''

    return send_file(
        io.BytesIO(template_content.encode('utf-8')),
        mimetype='text/plain',
        as_attachment=True,
        download_name='script_template.py'
    )

@app.route('/api/scripts/<script_id>/delete', methods=['DELETE'])
@login_required
@role_required(['admin', 'operator'])
def script_delete(script_id):
    """Удаление скрипта"""
    try:
        # Получаем имя файла из ID скрипта
        module_name = script_id.split('.')[0]

        # Путь к папке scripts
        scripts_dir = os.path.join(os.path.dirname(__file__), 'scripts')
        filepath = os.path.join(scripts_dir, f"{module_name}.py")

        # Проверяем, существует ли файл
        if not os.path.exists(filepath):
            return jsonify({'error': 'Файл не найден'}), 404

        # Удаляем файл
        os.remove(filepath)

        # Сбрасываем кэш скриптов
        try:
            from scripts import _scripts_cache
            _scripts_cache = None
        except ImportError:
            pass

        return jsonify({'success': True, 'message': 'Скрипт удален'})

    except Exception as e:
        logger.error(f"Ошибка удаления скрипта: {str(e)}")
        return jsonify({'error': str(e)}), 500

@app.route('/device/<int:device_id>/edit')
@login_required
def edit_device_page(device_id):
    """Страница редактирования устройства"""
    device = db.get_device(device_id)
    if not device:
        return redirect(url_for('index'))
    return render_template('edit_device.html', device=device)


@app.route('/device/<int:device_id>/update', methods=['POST'])
@login_required
def update_device(device_id):
    """Обновляет данные устройства"""
    name = request.form.get('name')
    host = request.form.get('host')
    device_type = request.form.get('device_type')
    port = int(request.form.get('port', 22))
    description = request.form.get('description', '')
    purpose = request.form.get('purpose', 'router')
    snmp_version = request.form.get('snmp_version', 'v2c')
    group = request.form.get('group')
    site = request.form.get('site')

    try:
        db.update_device(device_id, name, host, device_type, port, description, purpose, snmp_version=snmp_version, group=group, site=site)
        invalidate_devices_cache()  # ← СБРАСЫВАЕМ КЕШ
        logger.info(f"✏️ Устройство обновлено: {name} (ID: {device_id})")
        return redirect(url_for('index'))
    except Exception as e:
        return f"Ошибка: {e}", 400

@app.route('/api/cache/invalidate', methods=['POST'])
@login_required
def invalidate_cache():
    """Ручной сброс кеша (только для админов)"""
    invalidate_devices_cache()
    return jsonify({'success': True, 'message': 'Кеш сброшен'})


@app.route('/api/cache/status', methods=['GET'])
@login_required
def cache_status():
    """Проверка статуса кеша"""
    global devices_cache
    current_time = time.time()
    cache_age = int(current_time - devices_cache['timestamp']) if devices_cache['timestamp'] > 0 else 0

    return jsonify({
        'cached': devices_cache['data'] is not None,
        'devices_count': len(devices_cache['data']) if devices_cache['data'] else 0,
        'cache_age_seconds': cache_age,
        'ttl_seconds': devices_cache['ttl'],
        'expires_in': max(0, devices_cache['ttl'] - cache_age)
    })

@app.route('/device/<int:device_id>/delete')
@login_required
def delete_device_page(device_id):
    """Удаляет устройство"""
    try:
        db.delete_device(device_id)
        return redirect(url_for('index'))
    except Exception as e:
        return f"Ошибка: {e}", 400

@app.route('/config/<int:config_id>/delete')
@login_required
def delete_config(config_id):
    """Удаляет сохраненную конфигурацию"""
    try:
        db.delete_config(config_id)
        return redirect(url_for('configs_page'))
    except Exception as e:
        return f"Ошибка: {e}", 400


@app.route('/api/configs/search', methods=['POST'])
@login_required
def search_configs():
    """Поиск по всем конфигурациям"""
    data = request.json
    query = data.get('query', '').strip()
    page = data.get('page', 1, type=int)
    per_page = data.get('per_page', 50, type=int)

    if not query:
        return jsonify({'error': 'Введите текст для поиска'}), 400

    offset = (page - 1) * per_page

    with sqlite3.connect('devices.db') as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        # Ищем по конфигурациям
        search_pattern = f'%{query}%'

        # Получаем общее количество
        cursor.execute('''
            SELECT COUNT(*) 
            FROM configs c
            JOIN devices d ON c.device_id = d.id
            WHERE c.config_text LIKE ? OR d.name LIKE ? OR d.host LIKE ?
        ''', (search_pattern, search_pattern, search_pattern))
        total = cursor.fetchone()[0]

        # Получаем данные для страницы
        cursor.execute('''
            SELECT c.*, d.name as device_name, d.host 
            FROM configs c
            JOIN devices d ON c.device_id = d.id
            WHERE c.config_text LIKE ? OR d.name LIKE ? OR d.host LIKE ?
            ORDER BY c.saved_at DESC 
            LIMIT ? OFFSET ?
        ''', (search_pattern, search_pattern, search_pattern, per_page, offset))

        items = [dict(row) for row in cursor.fetchall()]

        # Для каждого конфига находим фрагмент с искомым текстом
        for item in items:
            config_text = item['config_text']
            # Находим контекст вокруг искомого текста
            pos = config_text.lower().find(query.lower())
            if pos != -1:
                start = max(0, pos - 100)
                end = min(len(config_text), pos + len(query) + 200)
                context = config_text[start:end]
                # Добавляем подсветку
                context = context.replace(query, f'<mark style="background: #ffc107; color: #000;">{query}</mark>')
                item['context'] = '...' + context + '...'
            else:
                item['context'] = ''

        return jsonify({
            'items': items,
            'total': total,
            'page': page,
            'per_page': per_page,
            'pages': (total + per_page - 1) // per_page,
            'query': query
        })

@app.route('/api/import/preview', methods=['POST'])
@login_required
def import_preview():
    """Предпросмотр файла с устройствами"""
    import pandas as pd
    import io

    if 'file' not in request.files:
        return jsonify({'error': 'Файл не загружен'}), 400

    file = request.files['file']
    skip_first_row = request.form.get('skip_first_row', 'true').lower() == 'true'

    try:
        # Читаем файл в зависимости от расширения
        filename = file.filename.lower()

        if filename.endswith('.csv'):
            # Пробуем разные кодировки для CSV
            content = file.read()
            try:
                df = pd.read_csv(io.BytesIO(content), encoding='utf-8-sig')
            except:
                try:
                    df = pd.read_csv(io.BytesIO(content), encoding='cp1251')
                except:
                    df = pd.read_csv(io.BytesIO(content), encoding='utf-8')
        elif filename.endswith(('.xlsx', '.xls')):
            df = pd.read_excel(io.BytesIO(file.read()))
        else:
            return jsonify({'error': 'Неподдерживаемый формат файла'}), 400

        # Показываем первые 5 строк для предпросмотра
        preview_data = df.head(5).fillna('').values.tolist()

        return jsonify({
            'headers': df.columns.tolist(),
            'preview': preview_data,
            'total_rows': len(df)
        })

    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/import/devices', methods=['POST'])
@login_required
def import_devices():
    """Импорт устройств из файла"""
    import pandas as pd
    import io

    if 'file' not in request.files:
        return jsonify({'error': 'Файл не загружен'}), 400

    file = request.files['file']
    skip_first_row = request.form.get('skip_first_row', 'true').lower() == 'true'

    try:
        # Читаем файл
        filename = file.filename.lower()

        if filename.endswith('.csv'):
            content = file.read()
            try:
                df = pd.read_csv(io.BytesIO(content), encoding='utf-8-sig')
            except:
                try:
                    df = pd.read_csv(io.BytesIO(content), encoding='cp1251')
                except:
                    df = pd.read_csv(io.BytesIO(content), encoding='utf-8')
        elif filename.endswith(('.xlsx', '.xls')):
            df = pd.read_excel(io.BytesIO(file.read()))
        else:
            return jsonify({'error': 'Неподдерживаемый формат файла'}), 400

        # Нормализуем названия колонок (нижний регистр, убираем пробелы)
        df.columns = [col.lower().strip() for col in df.columns]

        # Проверяем обязательные поля
        required_cols = ['name', 'host']
        missing_cols = [col for col in required_cols if col not in df.columns]
        if missing_cols:
            return jsonify({'error': f'Отсутствуют обязательные колонки: {", ".join(missing_cols)}'}), 400

        results = {
            'added': 0,
            'skipped': 0,
            'errors': []
        }

        # Получаем список существующих устройств для проверки дубликатов
        existing_devices = db.get_all_devices()
        existing_names = [d['name'] for d in existing_devices]

        # Импортируем каждую строку
        for idx, row in df.iterrows():
            try:
                # Проверяем обязательные поля
                if pd.isna(row.get('name')) or pd.isna(row.get('host')):
                    results['errors'].append(f"Строка {idx + 2}: пропущены обязательные поля")
                    continue

                name = str(row['name']).strip()
                host = str(row['host']).strip()

                # Проверяем дубликаты
                if name in existing_names:
                    results['skipped'] += 1
                    continue

                # Получаем остальные поля с значениями по умолчанию
                device_type = str(row.get('device_type', 'huawei')).strip() or 'huawei'

                try:
                    port = int(float(row.get('port', 22)))
                except:
                    port = 22

                description = str(row.get('description', '')).strip()
                purpose = str(row.get('purpose', 'router')).strip() or 'router'

                # Добавляем устройство
                db.add_device(
                    name=name,
                    host=host,
                    device_type=device_type,
                    port=port,
                    description=description,
                    purpose=purpose
                )

                # Добавляем имя в список существующих, чтобы не создавать дубликаты в этом же импорте
                existing_names.append(name)
                results['added'] += 1

            except Exception as e:
                results['errors'].append(f"Строка {idx + 2}: {str(e)}")

        # Сбрасываем кеш устройств
        invalidate_devices_cache()

        # Отправляем WebSocket уведомление всем клиентам
        socketio.emit('devices_updated', {
            'action': 'import',
            'added': results['added'],
            'timestamp': datetime.now().isoformat()
        })

        return jsonify({'success': True, **results})

    except Exception as e:
        return jsonify({'error': str(e)}), 500

# Запускаем фоновый поток для очистки
cleanup_thread = threading.Thread(target=cleanup_old_connections, daemon=True)
cleanup_thread.start()

@app.route('/api/devices/list', methods=['GET'])
@login_required
def api_devices_list():
    devices = get_cached_devices()
    return jsonify({'success': True, 'devices': devices})

@app.route('/audit')
@login_required
@role_required(['admin'])
def audit_page():
    """Страница аудита (только для админов)"""
    return render_template('audit.html')

@app.route('/api/audit/commands')
@login_required
@role_required(['admin'])
def api_audit_commands():
    page = request.args.get('page', 1, type=int)
    per_page = request.args.get('per_page', 50, type=int)
    history = db.get_command_history_all(page, per_page)
    return jsonify(history)


@app.route('/api/device/<int:device_id>/rediscover', methods=['POST'])
@login_required
@limiter.limit("10 per minute")
def api_rediscover_device(device_id):
    """Принудительный сбор данных по устройству"""
    import requests

    try:
        response = requests.post(
            f'https://daria-api:8000/api/discovery/collect/{device_id}',
            timeout=5,
            verify = False
        )
        return jsonify({'success': True, 'message': 'Сбор данных запущен'})
    except requests.exceptions.RequestException as e:
        logger.error(f"DARIA API error: {e}")
        return jsonify({'success': False, 'error': 'DARIA service unavailable'}), 503


@app.route('/api/devices/rediscover-all', methods=['POST'])
@login_required
def api_rediscover_all_devices():
    """Принудительный сбор по всем устройствам"""
    from celery_app import collect_all_devices_task

    task = collect_all_devices_task.delay()
    return jsonify({'success': True, 'task_id': task.id, 'message': 'Сбор данных запущен в очередь'})

@app.context_processor
def inject_daria_url():
    return {
        'daria_api_url': os.getenv('DARIA_API_URL', 'http://daria-api:8000')
    }


@app.route('/api/task/<task_id>/status', methods=['GET'])
@login_required
def task_status(task_id):
    from celery import current_app
    task = current_app.AsyncResult(task_id)

    if task.ready():
        if task.successful():
            result = task.result
            return jsonify({
                'status': 'completed',
                'result': result
            })
        else:
            return jsonify({
                'status': 'failed',
                'error': str(task.info)
            })
    else:
        return jsonify({
            'status': 'pending',
            'task_id': task_id
        })

if __name__ == '__main__':
    cert_file = os.environ.get('SSL_CERT', 'certs/cert.pem')
    key_file = os.environ.get('SSL_KEY', 'certs/key.pem')
    cert_exists = os.path.exists(cert_file) and os.path.exists(key_file)

    if cert_exists:
        print("\n" + "=" * 60)
        print("🔒 Kontrollka PRO запущена в режиме HTTPS")
        print(f"📍 https://{os.environ.get('HOST', '0.0.0.0')}:{os.environ.get('PORT', 5000)}")
        print("✅ Используется сертификат из папки certs/")
        print("=" * 60 + "\n")

        socketio.run(
            app,
            host=os.environ.get('HOST', '0.0.0.0'),
            port=int(os.environ.get('PORT', 5000)),
            debug=True,
            ssl_context=(cert_file, key_file)
        )
    else:
        print("\n" + "=" * 60)
        print("⚠️  Kontrollka PRO запущена в режиме HTTP (без HTTPS)")
        print(f"📍 http://{os.environ.get('HOST', '0.0.0.0')}:{os.environ.get('PORT', 5000)}")
        print("=" * 60 + "\n")

        socketio.run(
            app,
            host=os.environ.get('HOST', '0.0.0.0'),
            port=int(os.environ.get('PORT', 5000)),
            debug=True
        )