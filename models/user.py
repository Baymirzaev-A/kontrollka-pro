# models/user.py
import sqlite3
from datetime import datetime


class UserModel:
    def __init__(self, db_file='devices.db'):
        self.db_file = db_file
        self.init_table()

    def init_table(self):
        """Создает таблицу users если её нет"""
        with sqlite3.connect(self.db_file) as conn:
            cursor = conn.cursor()
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    username TEXT UNIQUE NOT NULL,
                    email TEXT,
                    full_name TEXT,
                    role TEXT DEFAULT 'viewer',
                    auth_source TEXT DEFAULT 'local',
                    last_login TIMESTAMP,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            conn.commit()

    def get_or_create_user(self, username, email='', full_name='', role='viewer', auth_source='ldap'):
        """Получает пользователя из БД или создает нового"""
        with sqlite3.connect(self.db_file) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()

            # Проверяем, есть ли пользователь
            cursor.execute('SELECT * FROM users WHERE username = ?', (username,))
            user = cursor.fetchone()

            if user:
                # Обновляем данные при входе
                cursor.execute('''
                    UPDATE users 
                    SET last_login = ?, email = ?, full_name = ?, role = ?, auth_source = ?
                    WHERE username = ?
                ''', (datetime.now(), email, full_name, role, auth_source, username))
                conn.commit()
                return dict(user)
            else:
                # Создаем нового
                cursor.execute('''
                    INSERT INTO users (username, email, full_name, role, auth_source, last_login)
                    VALUES (?, ?, ?, ?, ?, ?)
                ''', (username, email, full_name, role, auth_source, datetime.now()))
                conn.commit()

                cursor.execute('SELECT * FROM users WHERE username = ?', (username,))
                user = cursor.fetchone()
                return dict(user)

    def get_user(self, username):
        """Получает пользователя по имени"""
        with sqlite3.connect(self.db_file) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute('SELECT * FROM users WHERE username = ?', (username,))
            user = cursor.fetchone()
            return dict(user) if user else None

    def get_user_by_id(self, user_id):
        """Получает пользователя по ID"""
        with sqlite3.connect(self.db_file) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute('SELECT * FROM users WHERE id = ?', (user_id,))
            user = cursor.fetchone()
            return dict(user) if user else None

    def update_role(self, username, role):
        """Обновляет роль пользователя"""
        with sqlite3.connect(self.db_file) as conn:
            cursor = conn.cursor()
            cursor.execute('''
                UPDATE users SET role = ? WHERE username = ?
            ''', (role, username))
            conn.commit()
            return cursor.rowcount > 0

    def get_all_users(self):
        """Возвращает всех пользователей"""
        with sqlite3.connect(self.db_file) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute('SELECT id, username, email, full_name, role, auth_source, last_login, created_at FROM users ORDER BY username')
            return [dict(row) for row in cursor.fetchall()]

    def delete_user(self, username):
        """Удаляет пользователя"""
        with sqlite3.connect(self.db_file) as conn:
            cursor = conn.cursor()
            cursor.execute('DELETE FROM users WHERE username = ?', (username,))
            conn.commit()
            return cursor.rowcount > 0