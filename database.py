import os
import json
from datetime import datetime
from sqlalchemy import create_engine, Column, Integer, String, Text, DateTime, ForeignKey
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, relationship

# Определяем тип БД из переменной окружения
DATABASE_URL = os.environ.get('DATABASE_URL', 'sqlite:///devices.db')

# Для SQLite нужно дополнительное подключение
if DATABASE_URL.startswith('sqlite'):
    engine = create_engine(DATABASE_URL, connect_args={'check_same_thread': False})
else:
    engine = create_engine(DATABASE_URL, pool_size=10, max_overflow=20)

SessionLocal = sessionmaker(bind=engine)
Base = declarative_base()


# ===== МОДЕЛИ =====
class Device(Base):
    __tablename__ = 'devices'

    id = Column(Integer, primary_key=True)
    name = Column(String, unique=True, nullable=False)
    host = Column(String, nullable=False)
    device_type = Column(String, nullable=False, default='huawei')
    port = Column(Integer, default=22)
    description = Column(String, default='')
    purpose = Column(String, default='router')
    created_at = Column(DateTime, default=datetime.now)

    configs = relationship('Config', back_populates='device', cascade='all, delete-orphan')
    commands = relationship('CommandHistory', back_populates='device', cascade='all, delete-orphan')


class Config(Base):
    __tablename__ = 'configs'

    id = Column(Integer, primary_key=True)
    device_id = Column(Integer, ForeignKey('devices.id', ondelete='CASCADE'))
    config_text = Column(Text)
    saved_at = Column(DateTime, default=datetime.now)
    saved_by = Column(String, nullable=True)

    device = relationship('Device', back_populates='configs')


class CommandHistory(Base):
    __tablename__ = 'command_history'

    id = Column(Integer, primary_key=True)
    device_id = Column(Integer, ForeignKey('devices.id', ondelete='CASCADE'))
    command = Column(Text)
    output = Column(Text)
    executed_at = Column(DateTime, default=datetime.now)
    executed_by = Column(String, nullable=True)

    device = relationship('Device', back_populates='commands')

class AnsibleHistory(Base):
    __tablename__ = 'ansible_history'

    id = Column(Integer, primary_key=True)
    playbook_name = Column(String)
    device_ids = Column(Text)  # JSON строка
    extra_vars = Column(Text)  # JSON строка
    executed_by = Column(String)
    executed_at = Column(DateTime, default=datetime.now)
    success = Column(Integer)  # 0/1
    stdout = Column(Text)
    stderr = Column(Text)

# Добавить после класса AnsibleHistory
class Playbook(Base):
    __tablename__ = 'playbooks'

    id = Column(Integer, primary_key=True)
    name = Column(String, unique=True, nullable=False)
    content = Column(Text, nullable=False)
    description = Column(String, default='')
    owner = Column(String, nullable=False)  # кто создал
    is_shared = Column(Integer, default=0)  # 0/1
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)
    created_by = Column(String)
    updated_by = Column(String)

# ===== КЛАСС ДЛЯ РАБОТЫ С БД (СОВМЕСТИМЫЙ СО СТАРЫМ КОДОМ) =====
class DeviceDB:
    def __init__(self):
        self.init_db()

    def init_db(self):
        """Создает таблицы если их нет"""
        Base.metadata.create_all(engine)
        # Добавляем тестовые устройства если таблица пуста
        session = SessionLocal()
        try:
            if session.query(Device).count() == 0:
                self._add_test_devices(session)
                session.commit()
        finally:
            session.close()

    def _add_test_devices(self, session):
        """Добавляет тестовые устройства"""
        test_devices = [
            ('switch-01', '10.0.0.2', 'huawei', 22, 'Коммутатор Huawei S5731', 'switch'),
            ('router-01', '10.0.0.1', 'huawei', 22, 'Маршрутизатор Huawei AR', 'router')
        ]

        for name, host, dev_type, port, desc, purpose in test_devices:
            device = Device(
                name=name,
                host=host,
                device_type=dev_type,
                port=port,
                description=desc,
                purpose=purpose
            )
            session.add(device)


    # ========== МЕТОДЫ ДЛЯ УСТРОЙСТВ ==========

    def get_all_devices(self):
        """Возвращает все устройства"""
        session = SessionLocal()
        try:
            devices = session.query(Device).order_by(Device.name).all()
            return [self._device_to_dict(d) for d in devices]
        finally:
            session.close()

    def get_device(self, device_id):
        """Возвращает устройство по ID"""
        session = SessionLocal()
        try:
            device = session.query(Device).filter(Device.id == device_id).first()
            return self._device_to_dict(device) if device else None
        finally:
            session.close()

    def add_device(self, name, host, device_type='huawei', port=22, description='', purpose='router'):
        """Добавляет новое устройство"""
        session = SessionLocal()
        try:
            device = Device(
                name=name,
                host=host,
                device_type=device_type,
                port=port,
                description=description,
                purpose=purpose
            )
            session.add(device)
            session.commit()
            return device.id
        finally:
            session.close()

    def delete_device(self, device_id):
        """Удаляет устройство"""
        session = SessionLocal()
        try:
            device = session.query(Device).filter(Device.id == device_id).first()
            if device:
                session.delete(device)
                session.commit()
        finally:
            session.close()

    def update_device(self, device_id, name, host, device_type, port, description, purpose):
        """Обновляет данные устройства"""
        session = SessionLocal()
        try:
            device = session.query(Device).filter(Device.id == device_id).first()
            if device:
                device.name = name
                device.host = host
                device.device_type = device_type
                device.port = port
                device.description = description
                device.purpose = purpose
                session.commit()
        finally:
            session.close()

    # ========== МЕТОДЫ ДЛЯ КОНФИГУРАЦИЙ ==========

    def save_config(self, device_id, config_text, saved_by=None):
        """Сохраняет конфигурацию с указанием пользователя"""
        session = SessionLocal()
        try:
            config = Config(
                device_id=device_id,
                config_text=config_text,
                saved_by=saved_by
            )
            session.add(config)
            session.commit()
            return config.id
        finally:
            session.close()

    def get_config_history(self, device_id, page=1, per_page=20):
        """Возвращает историю конфигураций с пагинацией"""
        offset = (page - 1) * per_page
        session = SessionLocal()
        try:
            query = session.query(Config).filter(Config.device_id == device_id)
            total = query.count()
            items = query.order_by(Config.saved_at.desc()).offset(offset).limit(per_page).all()

            return {
                'items': [self._config_to_dict(c) for c in items],
                'total': total,
                'page': page,
                'per_page': per_page,
                'pages': (total + per_page - 1) // per_page
            }
        finally:
            session.close()

    def get_config(self, config_id):
        """Возвращает конфигурацию по ID"""
        session = SessionLocal()
        try:
            config = session.query(Config).filter(Config.id == config_id).first()
            return self._config_to_dict(config) if config else None
        finally:
            session.close()

    def get_all_configs(self, page=1, per_page=50):
        """Возвращает все сохраненные конфигурации с пагинацией"""
        offset = (page - 1) * per_page
        session = SessionLocal()
        try:
            query = session.query(Config)
            total = query.count()
            items = query.order_by(Config.saved_at.desc()).offset(offset).limit(per_page).all()

            result = []
            for c in items:
                device = session.query(Device).filter(Device.id == c.device_id).first()
                item = self._config_to_dict(c)
                if device:
                    item['device_name'] = device.name
                    item['host'] = device.host
                result.append(item)

            return {
                'items': result,
                'total': total,
                'page': page,
                'per_page': per_page,
                'pages': (total + per_page - 1) // per_page
            }
        finally:
            session.close()

    def delete_config(self, config_id):
        """Удаляет конфигурацию по ID"""
        session = SessionLocal()
        try:
            config = session.query(Config).filter(Config.id == config_id).first()
            if config:
                session.delete(config)
                session.commit()
        finally:
            session.close()

    # ========== МЕТОДЫ ДЛЯ ИСТОРИИ КОМАНД ==========

    def save_command_history(self, device_id, command, output, executed_by=None):
        """Сохраняет команду в историю с указанием пользователя"""
        session = SessionLocal()
        try:
            history = CommandHistory(
                device_id=device_id,
                command=command,
                output=output[:10000],  # ограничение длины
                executed_by=executed_by
            )
            session.add(history)
            session.commit()
        finally:
            session.close()

    def get_command_history(self, device_id, page=1, per_page=50):
        """Возвращает историю команд с пагинацией"""
        offset = (page - 1) * per_page
        session = SessionLocal()
        try:
            query = session.query(CommandHistory).filter(CommandHistory.device_id == device_id)
            total = query.count()
            items = query.order_by(CommandHistory.executed_at.desc()).offset(offset).limit(per_page).all()

            return {
                'items': [self._command_to_dict(c) for c in items],
                'total': total,
                'page': page,
                'per_page': per_page,
                'pages': (total + per_page - 1) // per_page
            }
        finally:
            session.close()

    def get_command_history_all(self, page=1, per_page=100):
        """Возвращает всю историю команд с пагинацией (для администратора)"""
        offset = (page - 1) * per_page
        session = SessionLocal()
        try:
            query = session.query(CommandHistory)
            total = query.count()
            items = query.order_by(CommandHistory.executed_at.desc()).offset(offset).limit(per_page).all()

            result = []
            for ch in items:
                device = session.query(Device).filter(Device.id == ch.device_id).first()
                item = self._command_to_dict(ch)
                if device:
                    item['device_name'] = device.name
                    item['host'] = device.host
                result.append(item)

            return {
                'items': result,
                'total': total,
                'page': page,
                'per_page': per_page,
                'pages': (total + per_page - 1) // per_page
            }
        finally:
            session.close()

    def save_ansible_history(self, playbook_name, device_ids, extra_vars, executed_by, success, stdout, stderr):
        """Сохраняет историю запуска Ansible playbook"""
        session = SessionLocal()
        try:
            history = AnsibleHistory(
                playbook_name=playbook_name,
                device_ids=json.dumps(device_ids) if device_ids else '[]',
                extra_vars=json.dumps(extra_vars) if extra_vars else '{}',
                executed_by=executed_by,
                success=1 if success else 0,
                stdout=stdout[:10000] if stdout else '',
                stderr=stderr[:10000] if stderr else ''
            )
            session.add(history)
            session.commit()
        finally:
            session.close()

    # ========== МЕТОДЫ ДЛЯ PLAYBOOKS ==========

    def get_playbooks(self, username, user_role):
        """Получить список доступных плейбуков (только для admin)"""
        if user_role != 'admin':
            return []

        session = SessionLocal()
        try:
            query = session.query(Playbook).order_by(Playbook.name)
            playbooks = query.all()
            return [{
                'id': p.id,
                'name': p.name,
                'description': p.description,
                'owner': p.owner,
                'is_shared': p.is_shared,
                'updated_at': p.updated_at.isoformat() if p.updated_at else None
            } for p in playbooks]
        finally:
            session.close()

    def get_playbook(self, playbook_id):
        """Получить содержимое плейбука по ID"""
        session = SessionLocal()
        try:
            p = session.query(Playbook).filter(Playbook.id == playbook_id).first()
            if not p:
                return None
            return {
                'id': p.id,
                'name': p.name,
                'content': p.content,
                'description': p.description,
                'owner': p.owner,
                'is_shared': p.is_shared
            }
        finally:
            session.close()

    def get_playbook_by_name(self, name):
        """Получить плейбук по имени"""
        session = SessionLocal()
        try:
            p = session.query(Playbook).filter(Playbook.name == name).first()
            if not p:
                return None
            return {
                'id': p.id,
                'name': p.name,
                'content': p.content,
                'description': p.description,
                'owner': p.owner,
                'is_shared': p.is_shared
            }
        finally:
            session.close()

    def save_playbook(self, playbook_id, name, content, description, is_shared, username):
        """Создать или обновить плейбук"""
        session = SessionLocal()
        try:
            if playbook_id:
                # Обновление
                p = session.query(Playbook).filter(Playbook.id == playbook_id).first()
                if p:
                    p.name = name
                    p.content = content
                    p.description = description
                    p.is_shared = 1 if is_shared else 0
                    p.updated_by = username
                    session.commit()
                    return p.id
            else:
                # Создание
                p = Playbook(
                    name=name,
                    content=content,
                    description=description,
                    owner=username,
                    created_by=username,
                    is_shared=1 if is_shared else 0
                )
                session.add(p)
                session.commit()
                return p.id
        finally:
            session.close()

    def delete_playbook(self, playbook_id):
        """Удалить плейбук"""
        session = SessionLocal()
        try:
            p = session.query(Playbook).filter(Playbook.id == playbook_id).first()
            if p:
                session.delete(p)
                session.commit()
                return True
            return False
        finally:
            session.close()

    # ========== ВСПОМОГАТЕЛЬНЫЕ МЕТОДЫ ==========

    def _device_to_dict(self, device):
        """Преобразует Device в словарь"""
        return {
            'id': device.id,
            'name': device.name,
            'host': device.host,
            'device_type': device.device_type,
            'port': device.port,
            'description': device.description,
            'purpose': device.purpose,
            'created_at': device.created_at.isoformat() if device.created_at else None
        }

    def _config_to_dict(self, config):
        """Преобразует Config в словарь"""
        return {
            'id': config.id,
            'device_id': config.device_id,
            'config_text': config.config_text,
            'saved_at': config.saved_at.isoformat() if config.saved_at else None,
            'saved_by': config.saved_by
        }

    def _command_to_dict(self, command):
        """Преобразует CommandHistory в словарь"""
        return {
            'id': command.id,
            'device_id': command.device_id,
            'command': command.command,
            'output': command.output,
            'executed_at': command.executed_at.isoformat() if command.executed_at else None,
            'executed_by': command.executed_by
        }

# ===== ГЛОБАЛЬНЫЙ ЭКЗЕМПЛЯР =====
db = DeviceDB()