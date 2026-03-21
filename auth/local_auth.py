# auth/local_auth.py
def local_authenticate(username, password):
    """Проверка для режима разработки"""
    APP_USERNAME = "admin"
    APP_PASSWORD = "admin"

    if username == APP_USERNAME and password == APP_PASSWORD:
        return {
            'username': username,
            'role': 'admin',
            'full_name': 'Administrator',
            'email': 'admin@local.dev'
        }
    return None