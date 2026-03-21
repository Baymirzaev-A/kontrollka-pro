# auth/ldap_auth.py
import os
import logging

logger = logging.getLogger(__name__)

# Читаем настройки из .env
LDAP_SERVER = os.environ.get('LDAP_SERVER')
LDAP_DOMAIN = os.environ.get('LDAP_DOMAIN')
LDAP_BASE_DN = os.environ.get('LDAP_BASE_DN')
LDAP_BIND_USER = os.environ.get('LDAP_BIND_USER')
LDAP_BIND_PASSWORD = os.environ.get('LDAP_BIND_PASSWORD')


def get_ldap_connection(user_dn=None, password=None):
    """Создает соединение с LDAP"""
    try:
        import ldap3
    except ImportError:
        logger.error("python-ldap не установлен. Установите: pip install python-ldap")
        return None

    server = ldap3.Server(LDAP_SERVER, get_info=ldap3.ALL)

    if user_dn:
        # Аутентификация пользователя
        conn = ldap3.Connection(server, user=user_dn, password=password, auto_bind=True)
    else:
        # Сервисное подключение (для поиска)
        conn = ldap3.Connection(server, user=LDAP_BIND_USER, password=LDAP_BIND_PASSWORD, auto_bind=True)

    return conn


def find_user_dn(username):
    """Находит DN пользователя по логину"""
    try:
        import ldap3
    except ImportError:
        logger.error("python-ldap не установлен")
        return None

    try:
        conn = get_ldap_connection()
        if not conn:
            return None

        # Ищем пользователя по sAMAccountName
        search_filter = f"(&(objectClass=user)(sAMAccountName={username}))"
        conn.search(
            search_base=LDAP_BASE_DN,
            search_filter=search_filter,
            attributes=['sAMAccountName', 'displayName', 'mail', 'memberOf']
        )

        if conn.entries:
            entry = conn.entries[0]
            return {
                'dn': entry.entry_dn,
                'username': entry.sAMAccountName.value,
                'full_name': entry.displayName.value if entry.displayName else username,
                'email': entry.mail.value if entry.mail else '',
                'groups': [str(g) for g in entry.memberOf] if entry.memberOf else []
            }

        return None

    except Exception as e:
        logger.error(f"Ошибка поиска пользователя {username}: {e}")
        return None
    finally:
        if conn:
            conn.unbind()


def ldap_authenticate(username, password):
    print(f"=== LDAP AUTH: {username} ===")  # ← добавить
    """Проверка пользователя в AD"""
    try:
        import ldap3
    except ImportError:
        logger.error("python-ldap не установлен")
        return None

    try:
        # 1. Находим пользователя
        user_info = find_user_dn(username)
        if not user_info:
            logger.warning(f"Пользователь {username} не найден в AD")
            return None

        # 2. Пробуем аутентифицироваться
        conn = ldap3.Connection(
            ldap3.Server(LDAP_SERVER),
            user=user_info['dn'],
            password=password,
            auto_bind=True
        )
        conn.unbind()

        # 3. Определяем роль по группам
        from . import map_group_to_role
        role = map_group_to_role(user_info['groups'])

        return {
            'username': user_info['username'],
            'role': role,
            'full_name': user_info['full_name'],
            'email': user_info['email'],
            'groups': user_info['groups']
        }

    except ldap3.core.exceptions.LDAPException as e:
        logger.warning(f"Ошибка аутентификации {username}: {e}")
        return None


def get_user_groups(username):
    """Получает группы пользователя (без аутентификации)"""
    user_info = find_user_dn(username)
    return user_info['groups'] if user_info else []