from flask import Flask, render_template, request, jsonify, session, redirect, url_for, send_file
from netmiko import ConnectHandler
import logging
import time
import threading
import io
import re
import json
import os
import ssl
import socket
from functools import wraps
from datetime import datetime
from database import DeviceDB
import shutil
from werkzeug.utils import secure_filename
# Импортируем загрузчик скриптов
from scripts import get_all_scripts, get_script

ALLOWED_EXTENSIONS = {'py'}

app = Flask(__name__)
app.secret_key = 'super-secret-key-for-network-console'
app.config['MAX_CONTENT_LENGTH'] = 100 * 1024 * 1024
app.config['PERMANENT_SESSION_LIFETIME'] = 3600  # Сессия живет 1 час

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

db = DeviceDB()

# ==== НАСТРОЙКИ ПОДКЛЮЧЕНИЯ К ОБОРУДОВАНИЮ (меняются через веб) ====
SETTINGS_FILE = 'device_settings.json'


def load_device_settings():
    """Загружает настройки подключения к оборудованию"""
    default_settings = {
        'device_username': 'admin',
        'device_password': 'DefHccb01',
        'device_enable': None,
        'last_updated': None
    }

    try:
        if os.path.exists(SETTINGS_FILE):
            with open(SETTINGS_FILE, 'r') as f:
                return json.load(f)
        else:
            # Создаем файл с настройками по умолчанию
            with open(SETTINGS_FILE, 'w') as f:
                json.dump(default_settings, f, indent=4)
            return default_settings
    except Exception as e:
        logger.error(f"Ошибка загрузки настроек: {e}")
        return default_settings


def save_device_settings(settings):
    """Сохраняет настройки подключения к оборудованию"""
    try:
        settings['last_updated'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        with open(SETTINGS_FILE, 'w') as f:
            json.dump(settings, f, indent=4)
        return True
    except Exception as e:
        logger.error(f"Ошибка сохранения настроек: {e}")
        return False


# Загружаем настройки при старте
device_settings = load_device_settings()

# Для удобства доступа
DEVICE_USERNAME = device_settings['device_username']
DEVICE_PASSWORD = device_settings['device_password']
DEVICE_ENABLE = device_settings['device_enable']

# ==== УЧЕТНЫЕ ДАННЫЕ ДЛЯ ВХОДА В ПРОГРАММУ ====
APP_USERNAME = "admin"
APP_PASSWORD = "admin"


# ==== ДЕКОРАТОР ДЛЯ ПРОВЕРКИ АВТОРИЗАЦИИ ====
def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get('logged_in'):
            return redirect(url_for('login'))
        return f(*args, **kwargs)

    return decorated_function


# Функция для выполнения команд с пагинацией и таймаутами
def execute_long_command(connection, command):
    """Выполняет команду с обработкой пагинации"""
    logger.info(f"Выполнение команды: {command}")

    try:
        # Отправляем команду и ждем промпт
        output = connection.send_command_timing(
            command,
            strip_prompt=False,
            strip_command=False,
            last_read=2
        )

        full_output = output

        # Продолжаем читать пока есть --More--
        max_pages = 50
        page_count = 0

        while page_count < max_pages:
            if re.search(r'--More--|---- More ----|--- more ---', full_output, re.IGNORECASE):
                # Отправляем пробел и читаем следующую порцию
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


@app.route('/settings')
@login_required
def settings_page():
    """Страница настроек подключения"""
    return render_template('settings.html', settings=device_settings)


@app.route('/api/settings/update', methods=['POST'])
@login_required
def update_settings():
    """Обновляет настройки подключения к оборудованию"""
    global device_settings, DEVICE_USERNAME, DEVICE_PASSWORD, DEVICE_ENABLE

    new_settings = {
        'device_username': request.form.get('device_username', '').strip(),
        'device_password': request.form.get('device_password', '').strip(),
        'device_enable': request.form.get('device_enable', '').strip() or None,
        'last_updated': device_settings.get('last_updated')
    }

    if save_device_settings(new_settings):
        # Обновляем глобальные переменные
        device_settings = load_device_settings()
        DEVICE_USERNAME = device_settings['device_username']
        DEVICE_PASSWORD = device_settings['device_password']
        DEVICE_ENABLE = device_settings['device_enable']

        logger.info(f"Настройки подключения обновлены пользователем {session.get('username')}")
        return redirect(url_for('settings_page'))
    else:
        return "Ошибка сохранения настроек", 500


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

        if username == APP_USERNAME and password == APP_PASSWORD:
            session['logged_in'] = True
            session.permanent = True
            logger.info(f"Пользователь {username} вошел в систему")
            return redirect(url_for('index'))
        else:
            error = 'Неверный логин или пароль'
            logger.warning(f"Неудачная попытка входа: {username}")

    return render_template('login.html', error=error)


@app.route('/logout')
def logout():
    session.pop('logged_in', None)
    logger.info("Пользователь вышел из системы")
    return redirect(url_for('login'))


# ==== ОСНОВНЫЕ СТРАНИЦЫ ====
@app.route('/')
@login_required
def index():
    """Главная страница со списком устройств"""
    devices = db.get_all_devices()
    return render_template('index.html', devices=devices)


@app.route('/device/<int:device_id>')
@login_required
def device_console(device_id):
    """Страница консоли для отдельного устройства"""
    device = db.get_device(device_id)
    if not device:
        return redirect(url_for('index'))

    return render_template('console.html', device=device)


@app.route('/group')
@login_required
def group_console():
    """Страница для групповых команд"""
    devices = db.get_all_devices()
    return render_template('group.html', devices=devices)


@app.route('/configs')
@login_required
def configs_page():
    """Страница со всеми сохраненными конфигурациями"""
    configs = db.get_all_configs(100)
    return render_template('configs.html', configs=configs)


@app.route('/config/<int:config_id>')
@login_required
def view_config(config_id):
    """Страница просмотра конфигурации"""
    config = db.get_config(config_id)
    if not config:
        return redirect(url_for('configs_page'))

    device = db.get_device(config['device_id'])
    return render_template('view_config.html', config=config, device=device)


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
            description=data.get('description', '')
        )
        return jsonify({'success': True, 'device_id': device_id})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 400


@app.route('/api/device/<int:device_id>/delete', methods=['POST'])
@login_required
def delete_device(device_id):
    """Удаляет устройство"""
    try:
        db.delete_device(device_id)
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
        db.save_command_history(device_id, command, output)

        # Сохраняем конфиг если нужно
        if 'display current-configuration' in command or command.strip() in ['display cur', 'disp cur',
                                                                             'display current']:
            db.save_config(device_id, output)

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
def execute_group_command():
    """Выполнение команды на нескольких выбранных устройствах"""
    data = request.json
    command = data.get('command')
    device_ids = data.get('device_ids', [])

    if not command:
        return jsonify({'error': 'Команда не указана'}), 400

    if not device_ids:
        return jsonify({'error': 'Не выбрано ни одного устройства'}), 400

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
            logger.info(f"Групповая задача: подключение к {device['host']}")
            connection = ConnectHandler(**device_params)
            output = execute_long_command(connection, command)

            db.save_command_history(device_id, command, output)

            if 'display current-configuration' in command or command.strip() in ['display cur', 'disp cur',
                                                                                 'display current']:
                db.save_config(device_id, output)

            results.append({
                'device_id': device_id,
                'device_name': device['name'],
                'success': True,
                'output': output
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

    return jsonify({
        'success': True,
        'results': results
    })


# ==== API ДЛЯ СКРИПТОВ =====
@app.route('/api/scripts')
@login_required
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
    device_params = {
        'device_type': device['device_type'],
        'host': device['host'],
        'port': device['port'],
        'username': DEVICE_USERNAME,
        'password': DEVICE_PASSWORD,
        'timeout': 60,
        'session_timeout': 120,
        'global_delay_factor': 2,
    }

    if DEVICE_ENABLE:
        device_params['secret'] = DEVICE_ENABLE

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
        db.save_command_history(device_id, f"SCRIPT: {script.get_name()}", output)

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

        device_params = {
            'device_type': device['device_type'],
            'host': device['host'],
            'port': device['port'],
            'username': DEVICE_USERNAME,
            'password': DEVICE_PASSWORD,
            'timeout': 60,
            'session_timeout': 120,
            'global_delay_factor': 2,
        }

        if DEVICE_ENABLE:
            device_params['secret'] = DEVICE_ENABLE

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
            db.save_command_history(device_id, f"SCRIPT: {script.get_name()}", output)

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
            config_id = db.save_config(device_id, output)
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
    """Возвращает историю команд"""
    history = db.get_command_history(device_id)
    return jsonify(history)


@app.route('/api/device/<int:device_id>/configs')
@login_required
def get_configs(device_id):
    """Возвращает историю сохраненных конфигураций"""
    configs = db.get_config_history(device_id)
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
        from werkzeug.utils import secure_filename
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

    try:
        db.update_device(device_id, name, host, device_type, port, description)
        return redirect(url_for('index'))
    except Exception as e:
        return f"Ошибка: {e}", 400


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

# Запускаем фоновый поток для очистки
cleanup_thread = threading.Thread(target=cleanup_old_connections, daemon=True)
cleanup_thread.start()

if __name__ == '__main__':
    app.run(
        debug=False,
        host='0.0.0.0',
        port=5000
    )