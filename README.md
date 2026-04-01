# Kontrollka PRO — Корпоративная система управления сетевым оборудованием и серверами

Корпоративная система для управления сетевым оборудованием (Huawei, Cisco, Juniper, Arista, Eltex, HP, Dell, Fortinet и 60+ других вендоров) и Linux-серверами с поддержкой Active Directory, ролевой модели, аудита действий и WebSocket для real-time обновлений.

---

## 📋 О проекте

**Kontrollka PRO** — это веб-приложение для централизованного управления сетевым оборудованием и Linux-серверами. Позволяет выполнять команды, запускать скрипты, сохранять конфигурации, отслеживать статус устройств и вести полный аудит действий пользователей.

---
## 📸 Скриншоты

![Главная страница](images/status.jpg)

![Консоль](images/console.jpg)

![Скрипты](images/scripts.jpg)

![Конфигурации](images/configs.jpg)

## 🚀 Ключевые возможности

### Управление устройствами и серверами
- CRUD операции с устройствами и серверами
- Отдельные учетные данные для серверов (логин/пароль/SSH-ключ)
- Групповые команды на нескольких устройствах (до 40 за раз)
- Python-скрипты для автоматизации
- Импорт устройств из CSV/Excel с предпросмотром
- Поиск и фильтрация по имени, IP, описанию, назначению (роутер/коммутатор/файрвол/сервер)

### Аутентификация и безопасность
- **Active Directory / LDAP** + локальный режим
- **Ролевая модель**: Admin / Operator / Viewer
- Маппинг ролей через группы AD
- **HTTPS** с поддержкой сертификатов
- **HSTS** для защиты от downgrade атак
- Запрет опасных команд в консоли (save, write, configure, reload и др.)

### Мониторинг и статусы
- **TCP ping** (проверка порта SSH) — работает даже когда ICMP заблокирован
- **WebSocket** для real-time обновления статусов
- Автоматическая проверка статусов (каждые 15-60 секунд)
- Статусы загружаются только для видимых устройств (оптимизация)

### Конфигурации и история
- Сохранение конфигураций с указанием пользователя (только для сетевого оборудования)
- Просмотр и скачивание конфигураций
- Поиск по содержимому конфигураций
- История команд с пагинацией

### Аудит
- Логирование всех команд: кто, когда, на каком устройстве/сервере выполнил
- Сохранение информации о том, кто сохранил конфигурацию
- Отдельная страница аудита для администраторов

### Масштабирование
- Кеширование устройств (TTL 300 сек)
- Кеширование статусов (TTL 15-60 сек)
- Пагинация для истории и конфигураций
- Готов к работе с PostgreSQL и Redis
- Поддержка Gunicorn + eventlet для production

---

## 🏗️ Технологии

| Компонент | Технология |
|-----------|------------|
| **Backend** | Flask 3.1.3, SQLAlchemy 2.0.48 |
| **Сетевое взаимодействие** | Netmiko 4.6.0 (60+ вендоров) |
| **База данных** | SQLite (разработка) / PostgreSQL (production) |
| **WebSocket** | Flask-SocketIO, eventlet 0.40.3 |
| **Аутентификация** | LDAP3, python-dotenv |
| **Импорт** | Pandas, openpyxl |
| **Deploy** | Docker, docker-compose, Gunicorn |

---

## 🔌 Поддерживаемые вендоры и системы

### Сетевое оборудование

| Категория | Вендоры |
|-----------|---------|
| **Cisco** | IOS, IOS-XE, NX-OS, IOS-XR, ASA |
| **Huawei** | VRP, VRPv8 (CloudEngine), OLT |
| **Juniper** | JunOS |
| **Arista** | EOS |
| **HP / Aruba** | ProCurve, Comware, ArubaOS |
| **Dell** | Force10, OS10, PowerConnect |
| **Extreme** | EXOS, ERS, NOS |
| **Fortinet** | FortiGate |
| **Alcatel / Nokia** | SR-OS |
| **Brocade / Ruckus** | FastIron, NetIron |
| **Eltex** | ESP, ESR |

### Серверы

| Тип | Описание |
|-----|----------|
| **Linux / Unix** | Полная поддержка SSH (Ubuntu, CentOS, Debian, RHEL и др.) |
| **Generic Terminal Server** | Любые устройства с CLI через SSH |

> 💡 **Для серверов:**
> - Используются отдельные учетные данные (логин/пароль или SSH-ключ)
> - Кнопка "Сохранить конфиг" скрыта
> - Доступны стандартные Linux-команды (`ls`, `df`, `systemctl`, `cat`, `grep` и др.)

---

## 👥 Ролевая модель

| Роль | Консоль | Скрипты | Конфигурации | Управление пользователями | Аудит |
|------|---------|---------|--------------|---------------------------|-------|
| **Admin** | ✅ Выполнение команд | ✅ Запуск и управление | ✅ Полный доступ | ✅ | ✅ |
| **Operator** | ✅ Выполнение команд | ✅ Запуск скриптов | ✅ Просмотр и сохранение | ❌ | ❌ |
| **Viewer** | ✅ Выполнение команд | ❌ Не видит вкладку | ✅ Просмотр | ❌ | ❌ |

### Маппинг групп Active Directory

В файле `.env` настраивается соответствие групп AD ролям:

```bash
# Группа администраторов (полный доступ)
AD_GROUP_ADMIN=CN=Network-Admins,OU=Groups,DC=company,DC=local

# Группа операторов (выполнение команд и скриптов)
AD_GROUP_OPERATOR=CN=Network-Operators,OU=Groups,DC=company,DC=local

# Группа наблюдателей (только консоль и просмотр)
AD_GROUP_VIEWER=CN=Network-Viewers,OU=Groups,DC=company,DC=local
```

### 🔧 Настройки (.env)

```bash
# Основные настройки
SECRET_KEY=сгенерируйте-случайную-строку

# Режим аутентификации: local / ldap
AUTH_MODE=local

# Учетные данные для сетевого оборудования
DEVICE_USERNAME=admin
DEVICE_PASSWORD=DefHccb01
DEVICE_ENABLE=

# Учетные данные для серверов (Linux/Unix)
SERVER_USERNAME=root
SERVER_PASSWORD=
# SERVER_KEY_FILE=/path/to/ssh/key.pem

# База данных (SQLite для разработки, PostgreSQL для production)
DATABASE_URL=sqlite:///devices.db
# DATABASE_URL=postgresql://user:pass@localhost:5432/kontrollka

# Redis для координации WebSocket (опционально)
# REDIS_URL=redis://localhost:6379

# HTTPS (опционально)
SSL_CERT=certs/cert.pem
SSL_KEY=certs/key.pem

# Хост и порт
HOST=0.0.0.0
PORT=5000

# LDAP (если AUTH_MODE=ldap)
# LDAP_SERVER=ldap://dc.company.local
# LDAP_DOMAIN=company
# LDAP_BASE_DN=DC=company,DC=local
# LDAP_BIND_USER=CN=service,OU=Users,DC=company,DC=local
# LDAP_BIND_PASSWORD=password
```

# 📦 Установка и настройка

Требования
Python 3.13 или выше

Docker (опционально, для контейнерного развертывания)

PostgreSQL (опционально, для production)

Redis (опционально, для масштабирования)

## Вариант 1: Локальная установка (разработка)
1. Клонирование репозитория
```bash
git clone https://github.com/Baymirzaev-A/kontrollka-pro.git
cd kontrollka-pro
```
2. Создание виртуального окружения
bash
### Linux / macOS
python3.13 -m venv venv
source venv/bin/activate

### Windows
python -m venv venv
venv\Scripts\activate
3. Установка зависимостей
```bash
pip install -r requirements.txt
```
4. Настройка окружения
```bash
cp .env.template .env
Отредактируйте .env:
```

```bash
# Обязательно: сгенерируйте секретный ключ
SECRET_KEY=$(openssl rand -hex 32)

# Режим аутентификации (local / ldap)
AUTH_MODE=local

# Учетные данные для оборудования
DEVICE_USERNAME=admin
DEVICE_PASSWORD=DefHccb01

# База данных (SQLite для разработки)
DATABASE_URL=sqlite:///devices.db
```

5. Запуск приложения
```bash
python app.py
Приложение будет доступно по адресу: https://localhost:5000 (если настроены сертификаты) или http://localhost:5000

Логин по умолчанию: admin / admin
```
## Вариант 2: Установка через Docker (рекомендуется для production)
1. Клонирование репозитория
```bash
git clone https://github.com/Baymirzaev-A/kontrollka-pro.git
cd kontrollka-pro
```
2. Настройка окружения
```bash
cp .env.production.template .env
```
Отредактируйте .env:

```bash
# Обязательно: сгенерируйте секретный ключ
SECRET_KEY=$(openssl rand -hex 32)

# Режим аутентификации
AUTH_MODE=local

# Учетные данные для оборудования
DEVICE_USERNAME=admin
DEVICE_PASSWORD=DefHccb01

# PostgreSQL (для production)
DATABASE_URL=postgresql://kontrollka:${DB_PASSWORD}@postgres:5432/kontrollka
DB_PASSWORD=secure_password

# Redis (для WebSocket между workers)
REDIS_URL=redis://redis:6379

```

3. HTTPS

ДЛЯ ЗАПУСКА HTTPS ТРЕБУЕТСЯ РАСКОММЕНТИРОВАТЬ СТРОКУ В dockerfile:
```dockerfile
CMD ["gunicorn", "-c", "gunicorn.conf.py", "--certfile=/app/certs/cert.pem", "--keyfile=/app/certs/key.pem", "app:app"]
```
Создание папки для сертификатов
```bash
mkdir certs

# Поместите ваши сертификаты в папку certs/
# certs/cert.pem - сертификат
# certs/key.pem - приватный ключ

```
4. Запуск контейнеров
```bash
docker-compose -f docker-compose.prod.yml up -d
```
5. Проверка работы
```bash
docker-compose -f docker-compose.prod.yml logs -f kontrollka
Приложение будет доступно по адресу: https://ваш-сервер:5000
```

### 📈 Масштабирование

### Для большинства случаев (до 50 пользователей)

По умолчанию используется **1 worker** и **10 потоков**. Этого достаточно для одновременной работы 10–20 администраторов.

Конфигурация в `gunicorn.conf.py`:

```python
workers = 1
threads = 10
```
Если требуется больше одновременных пользователей, можно увеличить количество потоков или добавить workers.

1. Увеличить количество потоков
В файле .env добавьте:

```bash
GUNICORN_WORKERS=1
GUNICORN_THREADS=40
```
Это позволит обрабатывать больше запросов параллельно, но все они будут идти через один порт (5000).

2. Использовать несколько workers + Nginx
Для более высокой нагрузки (50+ пользователей) рекомендуется использовать 4 workers и Nginx.
Редактируем файлы:
docker-compose.prod.yml
```yaml
ports:
  - "5000:5000"
  - "5001:5001"
  - "5002:5002"
  - "5003:5003"
```
gunicorn.conf.py
```python
bind = ["0.0.0.0:5000", "0.0.0.0:5001", "0.0.0.0:5002", "0.0.0.0:5003"] - для большего количества воркеров
```

## Вариант 3: Production-установка с Gunicorn
1. Установка PostgreSQL и Redis
```bash
# Ubuntu / Debian
sudo apt update
sudo apt install postgresql redis-server -y

# Запуск сервисов
sudo systemctl start postgresql redis
sudo systemctl enable postgresql redis
```
2. Создание базы данных
```bash
sudo -u postgres psql -c "CREATE DATABASE kontrollka;"
sudo -u postgres psql -c "CREATE USER kontrollka WITH PASSWORD 'your_password';"
sudo -u postgres psql -c "GRANT ALL PRIVILEGES ON DATABASE kontrollka TO kontrollka;"
```
3. Установка приложения
```bash
# Клонирование
git clone https://github.com/Baymirzaev-A/kontrollka-pro.git
cd kontrollka-pro

# Виртуальное окружение
python3.12 -m venv venv
source venv/bin/activate

# Зависимости
pip install -r requirements.txt

# Настройка
cp .env.template .env
nano .env  # отредактируйте DATABASE_URL, REDIS_URL
```
4. Настройка HTTPS
```bash
# Создание папки для сертификатов
mkdir certs

# Поместите ваши сертификаты в папку certs/
# certs/cert.pem - сертификат
# certs/key.pem - приватный ключ
```
5. Запуск через Gunicorn
```bash
gunicorn -k eventlet -w 4 app:app -b 0.0.0.0:5000
```
6. Настройка автозапуска (systemd)
Создайте файл /etc/systemd/system/kontrollka.service:

```ini
[Unit]
Description=Kontrollka PRO
After=network.target postgresql.service redis.service

[Service]
User=www-data
Group=www-data
WorkingDirectory=/opt/kontrollka-pro
EnvironmentFile=/opt/kontrollka-pro/.env
ExecStart=/opt/kontrollka-pro/venv/bin/gunicorn -k eventlet -w 4 app:app -b 0.0.0.0:5000
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
Запустите сервис:

bash
sudo systemctl enable kontrollka
sudo systemctl start kontrollka
```
### 🔧 Настройка после установки
Первый вход
Откройте браузер по адресу https://ваш-сервер:5000 (или http://... если HTTPS не настроен)

Войдите с учетными данными: admin / admin

Сразу смените пароль администратора (через интерфейс или БД)

Добавление устройств
Нажмите "➕ Добавить устройство"

Заполните поля:

Имя — любое удобное название

IP адрес — IP или hostname устройства

Тип устройства — выберите из списка (Cisco, Huawei, Juniper и др.)

Назначение — роутер, коммутатор, файрвол, сервер

Нажмите "Сохранить"

Импорт устройств из CSV
Нажмите "📥 Импорт из CSV/Excel"

Выберите файл с колонками: name, host, device_type, port, description, purpose

Нажмите "Предпросмотр" для проверки

Нажмите "Импортировать"

### ⚙️ Настройка LDAP/AD
В .env установите AUTH_MODE=ldap

Заполните параметры LDAP:

```bash
LDAP_SERVER=ldap://dc.company.local
LDAP_DOMAIN=company
LDAP_BASE_DN=DC=company,DC=local
LDAP_BIND_USER=CN=service,OU=Users,DC=company,DC=local
LDAP_BIND_PASSWORD=password
AD_GROUP_ADMIN=CN=Network-Admins,OU=Groups,DC=company,DC=local
AD_GROUP_OPERATOR=CN=Network-Operators,OU=Groups,DC=company,DC=local
AD_GROUP_VIEWER=CN=Network-Viewers,OU=Groups,DC=company,DC=local
```
Перезапустите приложение

### 🔄 Обновление
Через Git
```bash
cd /path/to/kontrollka-pro
git pull
pip install -r requirements.txt --upgrade
# перезапустить приложение
```
Через Docker
```bash
docker-compose -f docker-compose.prod.yml down
docker-compose -f docker-compose.prod.yml pull
docker-compose -f docker-compose.prod.yml up -d --build
```

### 🛡️ Безопасность
HTTPS с автоматическим редиректом

HSTS заголовки

Запрет опасных команд в консоли (save, write, configure, reload, system-view, shutdown, reboot)

Проверка сессии перед каждым запросом

Ролевая модель доступа

Аудит всех действий
