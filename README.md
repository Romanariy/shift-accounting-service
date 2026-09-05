# Shift Accounting Service

Отдельный сервис для учета смен, сопровождений, оплат и Excel-отчетов.

## Возможности

- Telegram-бот читает сообщения из группы или конкретного топика.
- Дата необязательна: без неё используется сегодняшний день в `DJANGO_TIME_ZONE` (по умолчанию Екатеринбург). В конце обязательно название организации или её алиас.
- Примеры: `10:00-16:00 фокус`, `01.04 10:00-16:00 + 2 сопр фотобар`, `Уборка к`, `Покраска циклораммы квин`, `+ 2 сопр фотобар`.
- Ручное назначение автора: `Рамис 01.04 10:00-16:00 фокус` или `01.04 Наташа 10:00-16:00 квин`.
- Требуются время, вид работы или положительное количество сопровождений; `привет фокус` не создаёт запись. Только сопровождения не создают пустую смену.
- История изменений для записей, сотрудников и правил оплаты.
- Справочники сотрудников, организаций (названия, алиасы, активность и лист Excel) и стоимости оплаты.
- Тариф смены определяется организацией, типом работы и датой. Большой/малый админ, уборка и покраска настраиваются отдельно для каждой организации. Сопровождения и телефоны используют общие тарифы.
- Excel: смены Фокуса и Фотобара первоначально на листе `Фокус и Фотобар`, Квина — на `Квин`; `Сопровождения` и `Телефоны` общие. Одинаковый лист в настройках объединяет организации, в том числе при экспорте прошлых месяцев.
- Очередь синхронизации с глобальной БД после новых данных и по расписанию.

## Backend

После обновления выполнить `python manage.py migrate`. Миграция `0002_organizations` создаёт организации и независимые копии существующих общих тарифов для каждой из них. Если правил нет, используются исходные ставки: большой админ 1400 ₽, малый 200 ₽/час (600–1200 ₽), уборка 700 ₽, покраска 1000 ₽. Новая организация получает копию расписания тарифов Фокуса.

Тип работы «Фотобар» удалён: старые смены и настройки сотрудников переводятся в «Малый админ» без изменения сохранённых сумм, прежние тарифы архивируются в истории. Старые записи остаются без организации и могут быть исправлены вручную; такие смены экспортируются на лист `Без организации`. Для их редактирования сохранены общие тарифы. Изменение тарифа не пересчитывает записи автоматически; сохранение отредактированной смены пересчитывает её по действующему на дату тарифу. При отсутствии тарифа сохранение отклоняется.

Миграция переноса данных необратима средствами Django: перед обновлением рабочей базы сделайте резервную копию. Отключение организации сохраняет её историю и отчёты, но исключает её из новых записей.

API справочника: `GET/POST /api/shifts/organizations/`, `GET/PUT/DELETE /api/shifts/organizations/<id>/` (`DELETE` отключает организацию). Поля: `name`, `aliases`, `excelSheet`, `isActive`. У записей добавлены `organizationId` и `organizationName`, у тарифов — `organizationId`. Организация обязательна для новых ручных записей и новых тарифов смен; для тарифов сопровождений и телефонов поле должно быть пустым. Новые сообщения очереди синхронизации включают организацию.

Проверки: `python manage.py test apps.shifts`, `python manage.py check`, `python manage.py makemigrations --check --dry-run`; во `frontend` — `npm run build`.

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

Интерфейс автоматически обновляет данные каждые 5 секунд и при возврате на вкладку,
поэтому записи от Telegram-бота появляются без ручного обновления страницы.

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

### Защита сайта логином и паролем

Внешний доступ к сайту, админке и API закрыт через Caddy Basic Auth.
Сгенерируйте хэш пароля на VPS:

```bash
docker run --rm --entrypoint caddy caddy:2-alpine hash-password --plaintext 'your-strong-password'
```

Вставьте результат в `.env` в одинарных кавычках:

```env
SITE_BASIC_AUTH_USER=admin
SITE_BASIC_AUTH_PASSWORD_HASH='$2a$14$...'
```

После изменения `.env` пересоздайте Caddy:

```bash
docker compose up -d --force-recreate caddy
```

Теперь браузер будет спрашивать логин и пароль перед открытием сайта.

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

Автоматический сжатый бэкап с хранением последних 14 дней:

```bash
sh deploy/backup-db.sh
```

Добавить ежедневный бэкап в cron:

```bash
crontab -e
```

```cron
15 3 * * * cd /opt/shift-accounting-service && sh deploy/backup-db.sh >> backups/backup.log 2>&1
```

Восстановление:

```bash
docker compose exec -T db sh -c 'psql -U "$POSTGRES_USER" "$POSTGRES_DB"' < backups/file.sql
```

Если бэкап сжатый:

```bash
gzip -dc backups/file.sql.gz | docker compose exec -T db sh -c 'psql -U "$POSTGRES_USER" "$POSTGRES_DB"'
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
