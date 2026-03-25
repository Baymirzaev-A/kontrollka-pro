import os
import multiprocessing

# Привязка к порту
bind = f"0.0.0.0:{os.environ.get('PORT', 5000)}"

# Количество workers (обычно = количество ядер CPU * 2 + 1)
workers = int(os.environ.get('GUNICORN_WORKERS', 4))

# Worker класс для WebSocket (eventlet)
worker_class = "eventlet"

# Максимум соединений на worker
worker_connections = 1000

# Таймауты
timeout = 120
graceful_timeout = 30
keepalive = 5

# Логирование
accesslog = "-"
errorlog = "-"
loglevel = os.environ.get('LOG_LEVEL', 'info')

# Перезагрузка при изменении кода (только для разработки)
# reload = True  # раскомментировать для разработки