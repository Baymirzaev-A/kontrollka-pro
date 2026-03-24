import sqlite3
from datetime import datetime


class DeviceDB:
    def __init__(self, db_file='devices.db'):
        self.db_file = db_file
        self.init_db()

    def init_db(self):
        """Создает таблицы если их нет"""
        with sqlite3.connect(self.db_file) as conn:
            cursor = conn.cursor()

            # Таблица устройств
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS devices (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT UNIQUE NOT NULL,
                    host TEXT NOT NULL,
                    device_type TEXT NOT NULL,
                    port INTEGER DEFAULT 22,
                    description TEXT,
                    purpose TEXT DEFAULT 'router',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')

            # Таблица для сохраненных конфигураций
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS configs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    device_id INTEGER,
                    config_text TEXT,
                    saved_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    saved_by TEXT,
                    FOREIGN KEY (device_id) REFERENCES devices (id) ON DELETE CASCADE
                )
            ''')

            # Таблица для истории команд
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS command_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    device_id INTEGER,
                    command TEXT,
                    output TEXT,
                    executed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    executed_by TEXT,
                    FOREIGN KEY (device_id) REFERENCES devices (id) ON DELETE CASCADE
                )
            ''')

            # Добавим тестовые устройства если таблица пуста
            cursor.execute("SELECT COUNT(*) FROM devices")
            if cursor.fetchone()[0] == 0:
                self.add_test_devices(conn)

            conn.commit()

    def add_test_devices(self, conn):
        """Добавляет тестовые устройства"""
        cursor = conn.cursor()
        test_devices = [
            ('switch-01', '10.0.0.2', 'huawei', 22, 'Коммутатор Huawei S5731', 'switch'),
            ('router-01', '10.0.0.1', 'huawei', 22, 'Маршрутизатор Huawei AR', 'router')
        ]

        for name, host, dev_type, port, desc, purpose in test_devices:
            cursor.execute('''
                INSERT INTO devices (name, host, device_type, port, description, purpose)
                VALUES (?, ?, ?, ?, ?, ?)
            ''', (name, host, dev_type, port, desc, purpose))

    # ========== МЕТОДЫ ДЛЯ УСТРОЙСТВ ==========

    def get_all_devices(self):
        """Возвращает все устройства"""
        with sqlite3.connect(self.db_file) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute('SELECT * FROM devices ORDER BY name')
            return [dict(row) for row in cursor.fetchall()]

    def get_device(self, device_id):
        """Возвращает устройство по ID"""
        with sqlite3.connect(self.db_file) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute('SELECT * FROM devices WHERE id = ?', (device_id,))
            row = cursor.fetchone()
            return dict(row) if row else None

    def add_device(self, name, host, device_type='huawei', port=22, description='', purpose='router'):
        """Добавляет новое устройство"""
        with sqlite3.connect(self.db_file) as conn:
            cursor = conn.cursor()
            cursor.execute('''
                INSERT INTO devices (name, host, device_type, port, description, purpose)
                VALUES (?, ?, ?, ?, ?, ?)
            ''', (name, host, device_type, port, description, purpose))
            conn.commit()
            return cursor.lastrowid

    def delete_device(self, device_id):
        """Удаляет устройство"""
        with sqlite3.connect(self.db_file) as conn:
            cursor = conn.cursor()
            cursor.execute('DELETE FROM devices WHERE id = ?', (device_id,))
            conn.commit()

    def update_device(self, device_id, name, host, device_type, port, description, purpose):
        """Обновляет данные устройства"""
        with sqlite3.connect(self.db_file) as conn:
            cursor = conn.cursor()
            cursor.execute('''
                UPDATE devices 
                SET name = ?, host = ?, device_type = ?, port = ?, description = ?, purpose = ?
                WHERE id = ?
            ''', (name, host, device_type, port, description, purpose, device_id))
            conn.commit()

    # ========== МЕТОДЫ ДЛЯ КОНФИГУРАЦИЙ (С АУДИТОМ) ==========

    def save_config(self, device_id, config_text, saved_by=None):
        """Сохраняет конфигурацию с указанием пользователя"""
        with sqlite3.connect(self.db_file) as conn:
            cursor = conn.cursor()
            cursor.execute('''
                INSERT INTO configs (device_id, config_text, saved_by)
                VALUES (?, ?, ?)
            ''', (device_id, config_text, saved_by))
            conn.commit()
            return cursor.lastrowid

    def get_config_history(self, device_id, page=1, per_page=20):
        """Возвращает историю конфигураций с пагинацией"""
        offset = (page - 1) * per_page

        with sqlite3.connect(self.db_file) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()

            # Получаем общее количество
            cursor.execute('''
                SELECT COUNT(*) FROM configs WHERE device_id = ?
            ''', (device_id,))
            total = cursor.fetchone()[0]

            # Получаем данные для страницы
            cursor.execute('''
                SELECT * FROM configs 
                WHERE device_id = ? 
                ORDER BY saved_at DESC 
                LIMIT ? OFFSET ?
            ''', (device_id, per_page, offset))

            items = [dict(row) for row in cursor.fetchall()]

            return {
                'items': items,
                'total': total,
                'page': page,
                'per_page': per_page,
                'pages': (total + per_page - 1) // per_page
            }

    def get_config(self, config_id):
        """Возвращает конфигурацию по ID"""
        with sqlite3.connect(self.db_file) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute('SELECT * FROM configs WHERE id = ?', (config_id,))
            row = cursor.fetchone()
            return dict(row) if row else None

    def get_all_configs(self, page=1, per_page=50):
        """Возвращает все сохраненные конфигурации с пагинацией"""
        offset = (page - 1) * per_page

        with sqlite3.connect(self.db_file) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()

            # Получаем общее количество
            cursor.execute('SELECT COUNT(*) FROM configs')
            total = cursor.fetchone()[0]

            # Получаем данные для страницы
            cursor.execute('''
                SELECT c.*, d.name as device_name, d.host 
                FROM configs c
                JOIN devices d ON c.device_id = d.id
                ORDER BY c.saved_at DESC 
                LIMIT ? OFFSET ?
            ''', (per_page, offset))

            items = [dict(row) for row in cursor.fetchall()]

            return {
                'items': items,
                'total': total,
                'page': page,
                'per_page': per_page,
                'pages': (total + per_page - 1) // per_page
            }

    def delete_config(self, config_id):
        """Удаляет конфигурацию по ID"""
        with sqlite3.connect(self.db_file) as conn:
            cursor = conn.cursor()
            cursor.execute('DELETE FROM configs WHERE id = ?', (config_id,))
            conn.commit()

    # ========== МЕТОДЫ ДЛЯ ИСТОРИИ КОМАНД (С АУДИТОМ) ==========

    def save_command_history(self, device_id, command, output, executed_by=None):
        """Сохраняет команду в историю с указанием пользователя"""
        with sqlite3.connect(self.db_file) as conn:
            cursor = conn.cursor()
            cursor.execute('''
                INSERT INTO command_history (device_id, command, output, executed_by)
                VALUES (?, ?, ?, ?)
            ''', (device_id, command, output[:10000], executed_by))
            conn.commit()

    def get_command_history(self, device_id, page=1, per_page=50):
        """Возвращает историю команд с пагинацией"""
        offset = (page - 1) * per_page

        with sqlite3.connect(self.db_file) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()

            # Получаем общее количество
            cursor.execute('''
                SELECT COUNT(*) FROM command_history WHERE device_id = ?
            ''', (device_id,))
            total = cursor.fetchone()[0]

            # Получаем данные для страницы
            cursor.execute('''
                SELECT * FROM command_history 
                WHERE device_id = ? 
                ORDER BY executed_at DESC 
                LIMIT ? OFFSET ?
            ''', (device_id, per_page, offset))

            items = [dict(row) for row in cursor.fetchall()]

            return {
                'items': items,
                'total': total,
                'page': page,
                'per_page': per_page,
                'pages': (total + per_page - 1) // per_page
            }

    def get_command_history_all(self, page=1, per_page=100):
        """Возвращает всю историю команд с пагинацией (для администратора)"""
        offset = (page - 1) * per_page

        with sqlite3.connect(self.db_file) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()

            # Получаем общее количество
            cursor.execute('SELECT COUNT(*) FROM command_history')
            total = cursor.fetchone()[0]

            # Получаем данные для страницы
            cursor.execute('''
                SELECT ch.*, d.name as device_name, d.host 
                FROM command_history ch
                JOIN devices d ON ch.device_id = d.id
                ORDER BY ch.executed_at DESC 
                LIMIT ? OFFSET ?
            ''', (per_page, offset))

            items = [dict(row) for row in cursor.fetchall()]

            return {
                'items': items,
                'total': total,
                'page': page,
                'per_page': per_page,
                'pages': (total + per_page - 1) // per_page
            }