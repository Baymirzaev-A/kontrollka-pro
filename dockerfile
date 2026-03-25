FROM python:3.13

# Устанавливаем системные зависимости
RUN apt-get update && apt-get install -y \
    gcc \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Копируем зависимости
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Копируем приложение
COPY . .

# Создаём папки для данных
RUN mkdir -p data logs certs ssh_keys

# Переменные окружения
ENV PYTHONUNBUFFERED=1

# Запуск через Gunicorn
CMD ["gunicorn", "-c", "gunicorn.conf.py", "app:app"]