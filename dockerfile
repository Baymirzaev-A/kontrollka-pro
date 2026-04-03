FROM python:3.13

# Устанавливаем системные зависимости
RUN apt-get update && apt-get install -y \
    gcc \
    libpq-dev \
    python3-dev \
    ansible \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Копируем зависимости
COPY requirements.txt .

RUN pip install --no-cache-dir -r requirements.txt

# Копируем приложение
COPY . .

# Создаём папки для данных
RUN mkdir -p data logs certs ssh_keys ansible/playbooks

RUN ansible-galaxy collection install ansible.netcommon && \
    ansible-galaxy collection install community.network && \
    ansible-galaxy collection install cisco.ios && \
    ansible-galaxy collection install cisco.nxos && \
    ansible-galaxy collection install junipernetworks.junos && \
    ansible-galaxy collection install arista.eos && \
    ansible-galaxy collection install fortinet.fortios && \
    ansible-galaxy collection install huawei.cloudengine || true

# Переменные окружения
ENV PYTHONUNBUFFERED=1

# Запуск через Gunicorn
#CMD ["gunicorn", "-c", "gunicorn.conf.py", "app:app"]
CMD ["gunicorn", "-c", "gunicorn.conf.py", "--certfile=/app/certs/cert.pem", "--keyfile=/app/certs/key.pem", "app:app"]