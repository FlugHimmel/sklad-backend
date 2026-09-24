# Склад — учёт тар и производственных операций

Backend для системы складского учёта на производстве. Клиент — Flutter Web
(отдельный репозиторий: [sklad-client](https://github.com/FlugHimmel/sklad-client)).

## Что решает

Производственный цикл (литьё → токарка → фрезеровка → упаковка → отгрузка),
где:

* из одной заготовки получается несколько разных деталей (до 8);
* каждая деталь кладётся в свою тару-приёмник;
* возможен добор из нескольких тар одного артикула;
* есть автовозврат остатка в исходную тару;
* брак учитывается с указанием причины (4 вида);
* всё фиксируется постфактум — оператор сначала сделал, потом записал.

## Ключевые сущности

| Модель | Что |
|---|---|
| Container / ContainerLine | Тара и её содержимое (может быть несколько артикулов) |
| Operation / OperationLine | Одна операция: взял (`from`) → положил (`to`) + брак (`scrap`) |
| Product | Номенклатура: `casting` (литьё), `part` (деталь), `raw`, `finished` |
| Product.alias_of | Дубль артикула: старый артикул ссылается на главный |

Баланс операции: `SUM(from.qty) == SUM(to.qty) + SUM(scrap.qty)`.

## Роли

| Роль | Что видит |
|---|---|
| user | Полный доступ кроме управления юзерами |
| foundry | Только тары/операции склада литейки |
| admin | Всё, кроме склада литейки + пользователи, аудит, опасная зона |

## Стек

Python 3.14 · Django 5 · DRF · PostgreSQL 18 · Gunicorn · Apache 2.4 ·
Let's Encrypt · JWT (access 8ч / refresh 14 дней).

## Установка

```bash
git clone https://github.com/FlugHimmel/sklad-backend.git
cd sklad-backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
# Отредактируй .env: SECRET_KEY, DB_*, ALLOWED_HOSTS

python manage.py migrate
python manage.py createsuperuser
python manage.py runserver
```

## Основные эндпоинты

| Метод | URL | Назначение |
|---|---|---|
| POST | /api/auth/login/ | Логин (JWT) |
| GET | /api/containers/ | Тары (фильтры, пагинация) |
| GET | /api/containers/by-code/?code=X | Тара по коду |
| POST | /api/operations/ | Создать операцию |
| POST | /api/operations/<pk>/rollback/ | Откат операции |
| GET | /api/operations/meta/ | Справочники |
| GET | /api/containers/<pk>/simple-packing-pdf/ | PDF упаковочного листа |
| POST | /api/containers/bulk-packing-pdf/ | Массовый PDF |
| POST | /api/shipment-notes/create-for-today/ | Накладная за день |
| GET | /api/reports/production-ops/ | Отчёт производства |
| GET | /api/reports/scrap-ops/ | Отчёт брака |
| GET | /api/reports/ready-to-ship/ | Готово к отгрузке |
| GET | /api/reports/monthly-summary/ | Сводная по месяцам |
| GET | /api/reports/events/ | История событий |
| GET | /api/reports/stock/ | Остатки |
| GET | /api/products/resolve/?article=X | Резолв через alias |

Полный список — в `warehouse/urls.py`.

## Структура

```
accounts/           — пользователи, роли, JWT
audit/              — журнал изменений
company_settings/   — реквизиты для PDF (2 блока: обычный и литейка)
config/             — Django settings, urls, wsgi
inventory/          — продукты, категории, BOM, aliases
orders/             — заказы, план/факт, импорт из Excel
warehouse/          — тары, операции, отчёты, PDF, накладные
scripts/            — backup, api_check, run_tests
templates/          — docx-шаблон упаковочного листа
```

## Тесты

```bash
.venv/bin/python manage.py full_test
```

Полный тест: 40 проверок (инфра, модели, алиасы, авторизация, все API,
отчёты, resolve). Пароль читается из `TEST_ADMIN_PASSWORD` (по умолчанию `admin`).

## Бэкапы

`scripts/backup_db.sh` — дамп PostgreSQL в `backups/` с ротацией (8 последних).

Cron (3:00 ночи):

```
0 3 * * * /bin/bash /path/to/scripts/backup_db.sh
```

## Лицензия

MIT. См. [LICENSE](LICENSE).
