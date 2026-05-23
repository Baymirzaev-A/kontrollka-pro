FROM python:3.13

# Устанавливаем системные зависимости
RUN apt-get update && apt-get install -y \
    gcc \
    libpq-dev \
    python3-dev \
    openssh-client \
    && rm -rf /var/lib/apt/lists/*

RUN mkdir -p /etc/ssh && \
    echo "Host *" >> /etc/ssh/ssh_config && \
    echo "    KexAlgorithms +diffie-hellman-group1-sha1,diffie-hellman-group14-sha1,diffie-hellman-group-exchange-sha1" >> /etc/ssh/ssh_config && \
    echo "    HostKeyAlgorithms +ssh-rsa,ssh-dss" >> /etc/ssh/ssh_config && \
    echo "    Ciphers +aes128-cbc,aes192-cbc,aes256-cbc,3des-cbc" >> /etc/ssh/ssh_config && \
    echo "    MACs +hmac-sha1,hmac-md5" >> /etc/ssh/ssh_config

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
#CMD ["gunicorn", "-c", "gunicorn.conf.py", "app:app"]
CMD ["gunicorn", "-c", "gunicorn.conf.py", "--certfile=/app/certs/cert.pem", "--keyfile=/app/certs/key.pem", "app:app"]