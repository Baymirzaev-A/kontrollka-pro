# auth/__init__.py
import os
import logging
from .local_auth import local_authenticate
from .ldap_auth import ldap_authenticate, get_user_groups

logger = logging.getLogger(__name__)

AUTH_MODE = os.environ.get('AUTH_MODE', 'local')


def authenticate(username, password):
    print(f"=== AUTHENTICATE CALLED: mode={AUTH_MODE}, user={username} ===")
    """Главная функция аутентификации, выбирает режим"""
    if AUTH_MODE == 'local':
        logger.debug(f"Локальная аутентификация для {username}")
        return local_authenticate(username, password)
    elif AUTH_MODE == 'ldap':
        logger.debug(f"LDAP аутентификация для {username}")
        return ldap_authenticate(username, password)
    else:
        raise ValueError(f"Unknown AUTH_MODE: {AUTH_MODE}")


def get_user_role(username):
    """Получает роль пользователя (для LDAP - из групп, для local - admin)"""
    if AUTH_MODE == 'local':
        return 'admin'
    else:
        groups = get_user_groups(username)
        return map_group_to_role(groups)


def map_group_to_role(groups):
    """Маппинг AD групп на роли"""
    ROLE_MAPPING = {
        os.environ.get('AD_GROUP_ADMIN'): 'admin',
        os.environ.get('AD_GROUP_OPERATOR'): 'operator',
        os.environ.get('AD_GROUP_VIEWER'): 'viewer',
    }

    for group in groups:
        if group in ROLE_MAPPING:
            return ROLE_MAPPING[group]

    return 'viewer'  # по умолчанию