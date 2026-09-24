"""
READ-ONLY автопроверка системы «Склад» через HTTP.

Бьёт по http://127.0.0.1:8000 (Gunicorn напрямую).
Никаких сторонних зависимостей — только urllib из stdlib.
НИЧЕГО НЕ ПИШЕТ В БД. Только GET и один POST на /api/auth/login/
(побочный эффект — Django обновит last_login у admin, это норма).

Запуск:
    cd /opt/sklad
    .venv/bin/python manage.py full_test
"""
import os
import time
import glob
import sys
import json
from urllib.parse import quote
from urllib import request as urlreq
from urllib import error as urlerr

from django.core.management.base import BaseCommand
from django.db import connection


BASE = 'http://127.0.0.1:8000'
TIMEOUT = 15

PASS = '\033[92m✓\033[0m'
FAIL = '\033[91m✗\033[0m'
WARN = '\033[93m!\033[0m'
DIM  = '\033[2m'
RST  = '\033[0m'


class HttpResult:
    def __init__(self, status, body):
        self.status_code = status
        self.text = body

    def json(self):
        return json.loads(self.text)


class Command(BaseCommand):
    help = 'READ-ONLY автопроверка системы «Склад» (HTTP, stdlib)'

    def handle(self, *args, **opts):
        self.passed = 0
        self.failed = 0
        self.warnings = 0
        self.token = None

        self.stdout.write('=' * 62)
        self.stdout.write('  READ-ONLY АВТОПРОВЕРКА «СКЛАД» (HTTP через Gunicorn)')
        self.stdout.write(f'  цель: {BASE}')
        self.stdout.write('=' * 62)

        self.sec('0. ЖИВОЙ СЕРВЕР')
        self.test_server_alive()

        self.sec('1. ИНФРАСТРУКТУРА')
        self.test_infra()
        self.test_backups()

        self.sec('2. МОДЕЛИ И ДАННЫЕ')
        self.test_models()

        self.sec('3. ЦЕЛОСТНОСТЬ АЛИАСОВ')
        self.test_alias_integrity()

        self.sec('4. АВТОРИЗАЦИЯ')
        self.test_login()

        self.sec('5. API: READ-ЭНДПОИНТЫ')
        self.test_read_endpoints()

        self.sec('6. ОТЧЁТЫ')
        self.test_reports()

        self.sec('7. ALIAS RESOLVE (GET по существующим парам)')
        self.test_alias_resolve()

        self.stdout.write('')
        self.stdout.write('=' * 62)
        self.stdout.write(f'  ИТОГО:  {PASS} {self.passed}     {FAIL} {self.failed}     {WARN} {self.warnings}')
        self.stdout.write('=' * 62)

        if self.failed:
            self.stdout.write(FAIL + '  Есть ошибки — смотри выше.')
            sys.exit(1)
        else:
            self.stdout.write(PASS + '  Всё чисто.')

    def ok(self, msg, extra=''):
        self.stdout.write(f'  {PASS} {msg}' + (f'  {DIM}{extra}{RST}' if extra else ''))
        self.passed += 1

    def fail(self, msg, err=None):
        self.stdout.write(f'  {FAIL} {msg}')
        if err is not None:
            text = str(err)
            if len(text) > 500:
                text = text[:500] + '...'
            for line in text.splitlines():
                self.stdout.write(f'      {line}')
        self.failed += 1

    def warn(self, msg):
        self.stdout.write(f'  {WARN} {msg}')
        self.warnings += 1

    def sec(self, name):
        self.stdout.write('')
        self.stdout.write(f'-- {name} ' + '-' * (55 - len(name)))

    def _http(self, url, method='GET', payload=None, token=None, timeout=TIMEOUT):
        full = url if url.startswith('http') else BASE + url
        headers = {'Accept': 'application/json'}
        if token:
            headers['Authorization'] = f'Bearer {token}'
        data = None
        if payload is not None:
            data = json.dumps(payload).encode('utf-8')
            headers['Content-Type'] = 'application/json'
        req = urlreq.Request(full, data=data, method=method, headers=headers)
        try:
            with urlreq.urlopen(req, timeout=timeout) as resp:
                body = resp.read().decode('utf-8', errors='replace')
                return HttpResult(resp.status, body)
        except urlerr.HTTPError as e:
            body = e.read().decode('utf-8', errors='replace')
            return HttpResult(e.code, body)

    def get(self, url):
        return self._http(url, method='GET', token=self.token)

    # ---------- 0 ----------

    def test_server_alive(self):
        try:
            r = self._http('/api/', method='GET')
        except urlerr.URLError as e:
            self.fail(f'Gunicorn на {BASE} не отвечает', e)
            self.stdout.write('      Проверь: sudo systemctl status sklad')
            sys.exit(1)
        except Exception as e:
            self.fail('HTTP к Gunicorn', e)
            sys.exit(1)
        self.ok(f'Gunicorn отвечает', f'HTTP {r.status_code} на /api/')

    # ---------- 1 ----------

    def test_infra(self):
        try:
            with connection.cursor() as c:
                c.execute('SELECT version()')
                v = c.fetchone()[0]
            self.ok('БД PostgreSQL', v.split(',')[0])
        except Exception as e:
            self.fail('БД недоступна', e)
            return

        try:
            from accounts.models import User
            n = User.objects.count()
            self.ok(f'Пользователи: {n}')
            for role in ('admin', 'foundry', 'user'):
                if User.objects.filter(role=role).exists():
                    self.ok(f'  роль {role}: есть')
                else:
                    self.warn(f'  роль {role}: пользователей нет')
        except Exception as e:
            self.fail('accounts.User', e)

        try:
            from warehouse.models import Warehouse
            field = 'code' if any(f.name == 'code' for f in Warehouse._meta.fields) else 'name'
            codes = list(Warehouse.objects.values_list(field, flat=True))[:10]
            self.ok(f'Склады ({field}): {codes}')
        except Exception as e:
            self.fail('warehouse.Warehouse', e)

    def test_backups(self):
        d = '/opt/sklad/backups'
        if not os.path.isdir(d):
            self.warn(f'Нет директории бэкапов: {d}')
            return
        pats = ['*.sql', '*.sql.gz', '*.dump', '*.dump.gz', '*.tar.gz', '*.zip', '*.backup', '*.json']
        files = []
        for p in pats:
            files.extend(glob.glob(os.path.join(d, p)))
        files = [f for f in files if os.path.isfile(f)]
        real = [f for f in files if 'pre-test' not in os.path.basename(f)]
        if not real:
            self.warn('настоящих бэкапов (не pre-test) нет — проверь крон!')
            self.stdout.write(f'      {DIM}всего файлов: {len(files)}, все с меткой pre-test{RST}')
            return
        real.sort(key=os.path.getmtime)
        newest = real[-1]
        age_h = (time.time() - os.path.getmtime(newest)) / 3600
        name = os.path.basename(newest)
        if age_h < 26:
            self.ok(f'Последний бэкап: {name}', f'{age_h:.1f} ч назад')
        elif age_h < 48:
            self.warn(f'Бэкап старый: {name}, {age_h:.1f} ч назад')
        else:
            self.fail(f'Бэкап очень старый: {name}, {age_h:.1f} ч назад')
        self.stdout.write(f'      {DIM}всего файлов: {len(files)} (вкл. pre-test){RST}')

    # ---------- 2 ----------

    def test_models(self):
        try:
            from inventory.models import Product
            self.ok(f'Продуктов: {Product.objects.count()}')
        except Exception as e:
            self.fail('Product', e)
        try:
            from warehouse.models import Container
            self.ok(f'Тар всего: {Container.objects.count()}')
        except Exception as e:
            self.fail('Container', e)
        try:
            from warehouse.models import Operation
            self.ok(f'Операций: {Operation.objects.count()}')
        except Exception as e:
            self.fail('Operation', e)
        try:
            from warehouse.models import Movement
            self.ok(f'Движений: {Movement.objects.count()}')
        except Exception as e:
            self.fail('Movement', e)
        try:
            from warehouse.models import ShipmentNote
            self.ok(f'Накладных: {ShipmentNote.objects.count()}')
        except Exception as e:
            self.fail('ShipmentNote', e)

    # ---------- 3 ----------

    def test_alias_integrity(self):
        try:
            from inventory.models import Product
        except Exception as e:
            self.fail('Product import', e)
            return

        self_loops = 0
        chains = 0
        aliases = 0
        try:
            qs = Product.objects.filter(alias_of__isnull=False).select_related('alias_of')
            for p in qs.iterator():
                aliases += 1
                if p.alias_of_id == p.id:
                    self_loops += 1
                elif p.alias_of and p.alias_of.alias_of_id:
                    chains += 1
        except Exception as e:
            self.fail('Alias integrity', e)
            return

        self.ok(f'Алиасов: {aliases}')
        if self_loops:
            self.fail(f'Самоссылки alias_of: {self_loops}')
        else:
            self.ok('Самоссылок нет')
        if chains:
            self.fail(f'Цепочки alias_of (alias у alias): {chains}')
        else:
            self.ok('Цепочек нет')

        known = [
            ('6060986', '2478853'),
            ('6060985', '6050235'),
            ('6061566', '2479355'),
            ('6061954', '6050299'),
        ]
        for old, new in known:
            try:
                p_old = Product.objects.filter(article=old).first()
                p_new = Product.objects.filter(article=new).first()
                if not p_old or not p_new:
                    self.warn(f'пара {old} <-> {new}: артикул не найден')
                    continue
                if p_old.alias_of_id == p_new.id or p_new.alias_of_id == p_old.id:
                    self.ok(f'пара {old} <-> {new}')
                else:
                    self.fail(f'пара {old} <-> {new}: связь отсутствует')
            except Exception as e:
                self.fail(f'пара {old} <-> {new}', e)

    # ---------- 4 ----------

    def test_login(self):
        try:
            r = self._http('/api/auth/login/', method='POST',
                           payload={'username': 'admin', 'password': os.environ.get('TEST_ADMIN_PASSWORD', 'admin')})
        except Exception as e:
            self.fail('login: exception', e)
            return
        if r.status_code != 200:
            self.fail(f'login: HTTP {r.status_code}', r.text[:400])
            return
        try:
            d = r.json()
        except Exception:
            self.fail('login: не JSON', r.text[:300])
            return
        tok = d.get('access') or d.get('token') or d.get('access_token')
        if not tok:
            self.fail('login: нет access-токена', str(d)[:200])
            return
        self.token = tok
        self.ok('login admin: OK', f'{tok[:24]}...')
        self.stdout.write(f'      {DIM}(побочный эффект: Django обновил last_login — это норма){RST}')

    # ---------- 5 ----------

    def test_read_endpoints(self):
        if not self.token:
            self.warn('пропущено — нет токена')
            return
        urls = [
            ('/api/containers/', 'список тар'),
            ('/api/containers/?packed=1', 'упакованные'),
            ('/api/operations/', 'операции'),
            ('/api/operations/meta/', 'мета операций'),
            ('/api/products/', 'продукты'),
            ('/api/shipment-notes/', 'накладные'),
            ('/api/movements/', 'движения'),
        ]
        for url, label in urls:
            try:
                r = self.get(url)
            except Exception as e:
                self.fail(f'{label} ({url})', e)
                continue
            if r.status_code == 200:
                self.ok(label, url)
            else:
                self.fail(f'{label} ({url}): HTTP {r.status_code}', r.text[:300])

        try:
            r = self.get('/api/containers/')
            data = r.json()
            if isinstance(data, dict) and 'results' in data:
                rows = data['results']
            else:
                rows = data
            if isinstance(rows, list) and rows:
                code = rows[0].get('code')
                if code:
                    url = f'/api/containers/by-code/?code={quote(code)}'
                    r2 = self.get(url)
                    if r2.status_code == 200:
                        self.ok(f'by-code {code}')
                    else:
                        self.fail(f'by-code {code}: HTTP {r2.status_code}', r2.text[:200])
                else:
                    self.warn('by-code: у первой тары нет поля code')
            else:
                self.warn('by-code: тар нет')
        except Exception as e:
            self.fail('by-code', e)

    # ---------- 6 ----------

    def test_reports(self):
        if not self.token:
            self.warn('пропущено — нет токена')
            return
        checks = [
            '/api/reports/production-ops/',
            '/api/reports/scrap-ops/',
            '/api/reports/ready-to-ship/',
            '/api/reports/monthly-summary/',
            '/api/reports/events/',
            '/api/reports/stock/',
        ]
        for url in checks:
            try:
                r = self.get(url)
                if r.status_code != 200:
                    self.fail(f'{url}: HTTP {r.status_code}', r.text[:200])
                    continue
                d = r.json()
                if isinstance(d, list):
                    self.ok(f'{url} -> список ({len(d)})')
                elif isinstance(d, dict):
                    keys = list(d.keys())
                    self.ok(f'{url} -> dict {keys[:4]}')
                else:
                    self.warn(f'{url}: тип {type(d).__name__}')
            except Exception as e:
                self.fail(f'{url}', e)

    # ---------- 7 ----------

    def test_alias_resolve(self):
        if not self.token:
            self.warn('пропущено — нет токена')
            return
        try:
            from inventory.models import Product
        except Exception as e:
            self.fail('Product import', e)
            return

        pairs = list(
            Product.objects
            .filter(alias_of__isnull=False)
            .select_related('alias_of')[:5]
        )
        if not pairs:
            self.warn('нет существующих alias-пар для проверки')
            return

        for p_alias in pairs:
            old = p_alias.article
            new = p_alias.alias_of.article
            main_id = p_alias.alias_of_id
            try:
                url = f'/api/products/resolve/?article={quote(old)}'
                r = self.get(url)
                if r.status_code != 200:
                    self.fail(f'resolve {old} -> {new}: HTTP {r.status_code}', r.text[:200])
                    continue
                d = r.json()
                got = d.get('id') or d.get('pk') or (d.get('product') or {}).get('id')
                if got and str(got) == str(main_id):
                    self.ok(f'resolve {old} -> {new}')
                else:
                    self.fail(f'resolve {old}: ожидали id={main_id}, получили {str(d)[:200]}')
            except Exception as e:
                self.fail(f'resolve {old}', e)
