# Shift Accounting Service

Отдельный сервис для учета смен, сопровождений, оплат и Excel-отчетов.

## Возможности

- Telegram-бот читает сообщения из группы или конкретного топика.
- Поддерживаются шаблоны `01.04 10:00-16:00`, `01.04 10:00-16:00 + 2 сопр`, `01.04 Фотобар`, `01.04 Покраска Циклораммы`, `01.04 Уборка`.
- Ручное назначение автора: `Рамис 01.04 10:00-16:00` или `01.04 Наташа 10:00-16:00`.
- История изменений для записей, сотрудников и правил оплаты.
- Справочники сотрудников и стоимости оплаты.
- Excel-отчет по месяцу с листами `Смены`, `Сопровождения`, `Телефоны`.
- Очередь синхронизации с глобальной БД после новых данных и по расписанию.

## Backend

```powershell
cd backend
pip install -r requirements.txt
python manage.py migrate
python manage.py createsuperuser
python manage.py runserver 127.0.0.1:8000
```

Запуск бота:

```powershell
$env:TELEGRAM_BOT_TOKEN="token"
python manage.py run_shift_bot
```

Запуск синхронизации каждый час:

```powershell
$env:SHIFT_SYNC_ENDPOINT="https://example.com/api/sync"
$env:SHIFT_SYNC_TOKEN="secret"
python manage.py sync_shifts --loop --interval 3600
```

## Frontend

```powershell
cd frontend
npm install
npm run dev
```

Откройте `http://127.0.0.1:3000`.

## Docker-развертывание на VPS

В Docker-режиме поднимаются:

- `db` — PostgreSQL.
- `backend` — Django + Gunicorn.
- `frontend` — Next.js production server.
- `caddy` — reverse proxy на 80/443.
- `bot` — Telegram-бот, включается профилем `bot`.
- `sync` — фоновая синхронизация, включается профилем `sync`.

### Первый запуск

На сервере установите Docker и Docker Compose plugin, затем положите проект, например в
`/opt/shift-accounting-service`.
Если используете домен, A-запись домена должна смотреть на VPS, а порты `80` и `443`
должны быть открыты в firewall.

```bash
cd /opt/shift-accounting-service
cp .env.example .env
nano .env
```

Минимально поменяйте в `.env`:

```env
APP_DOMAIN=your-domain.example
POSTGRES_PASSWORD=strong-db-password
DJANGO_SECRET_KEY=strong-django-secret-key
DJANGO_ALLOWED_HOSTS=your-domain.example,backend
DJANGO_CSRF_TRUSTED_ORIGINS=https://your-domain.example
DJANGO_SECURE_SSL_REDIRECT=1
TELEGRAM_BOT_TOKEN=123456:telegram-token
DJANGO_API_BASE_URL=http://backend:8000
```

Если домена пока нет, оставьте:

```env
APP_DOMAIN=:80
DJANGO_ALLOWED_HOSTS=localhost,127.0.0.1,backend
DJANGO_CSRF_TRUSTED_ORIGINS=http://localhost,http://127.0.0.1
DJANGO_SECURE_SSL_REDIRECT=0
```

Запуск сайта и бота:

```bash
docker compose --profile bot up -d --build
```

Создать администратора:

```bash
docker compose exec backend python manage.py createsuperuser
```

Посмотреть логи:

```bash
docker compose logs -f backend
docker compose logs -f frontend
docker compose logs -f bot
```

Остановить:

```bash
docker compose --profile bot down
```

### Обновление на сервере

Лучший вариант — хранить проект в Git-репозитории и обновлять сервер через `git pull`.

```bash
cd /opt/shift-accounting-service
git pull
docker compose --profile bot up -d --build --remove-orphans
docker image prune -f
```

Миграции и сбор статических файлов выполняет сервис `migrate` при каждом `up`.

### Бэкап базы

```bash
mkdir -p backups
docker compose exec -T db sh -c 'pg_dump -U "$POSTGRES_USER" "$POSTGRES_DB"' > backups/shift_accounting_$(date +%F_%H-%M).sql
```

Восстановление:

```bash
docker compose exec -T db sh -c 'psql -U "$POSTGRES_USER" "$POSTGRES_DB"' < backups/file.sql
```

### Как быстро выгружать проект на сервер

Рекомендуемый путь:

1. Завести приватный GitHub/GitLab репозиторий.
2. На компьютере коммитить изменения.
3. На VPS делать `git pull` и `docker compose --profile bot up -d --build --remove-orphans`.

Если Git пока не нужен, можно копировать через `rsync`:

```bash
rsync -av --delete \
  --exclude .venv \
  --exclude frontend/node_modules \
  --exclude frontend/.next \
  --exclude backend/db.sqlite3 \
  ./ root@your-server:/opt/shift-accounting-service/
```
