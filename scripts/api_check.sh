#!/bin/bash
# Проверка API endpoints
# Запуск: bash scripts/api_check.sh

BASE="http://127.0.0.1:8000"
USER="admin"
PASS="changeme-admin"

echo "══════════════════════════════════════════════════"
echo "  API CHECK"
echo "══════════════════════════════════════════════════"

# Логин
TOKEN=$(curl -s -X POST $BASE/api/auth/login/ \
  -H "Content-Type: application/json" \
  -d "{\"username\":\"$USER\",\"password\":\"$PASS\"}" \
  | python3 -c "import sys,json;print(json.load(sys.stdin).get('access',''))")

if [ -z "$TOKEN" ]; then
  echo "❌ Логин не прошёл"
  exit 1
fi
echo "✅ Логин OK (токен получен)"

check() {
  local name="$1"
  local url="$2"
  local expected="$3"
  local code=$(curl -s -o /dev/null -w "%{http_code}" \
    "$BASE$url" -H "Authorization: Bearer $TOKEN")
  if [ "$code" = "$expected" ]; then
    echo "✅ $name ($code)"
  else
    echo "❌ $name — код $code, ожидали $expected"
  fi
}

echo ""
echo "─── Читаем (GET) ───"
check "Контейнеры"           "/api/containers/"                 "200"
check "Операции meta"        "/api/operations/meta/"            "200"
check "Операции список"      "/api/operations/"                 "200"
check "Пустые тары"          "/api/containers/empty/"           "200"
check "Производство (отчёт)" "/api/reports/production-ops/"     "200"
check "Брак (отчёт)"         "/api/reports/scrap-ops/"          "200"
check "Готово к отгрузке"    "/api/reports/ready-to-ship/"      "200"
check "Сводная (месяц)"      "/api/reports/monthly-summary/"    "200"
check "Остатки"              "/api/reports/stock/"              "200"
check "Движения"             "/api/movements/"                  "200"
check "Заказы"               "/api/orders/"                     "200"
check "Склады"               "/api/warehouses/"                 "200"
check "Продукты"             "/api/products/"                   "200"

echo ""
echo "─── Удалены (404) ───"
check "Старый production issue" "/api/production/issue/"        "404"
check "Старый production run"   "/api/production/run/"          "404"

echo ""
echo "══════════════════════════════════════════════════"
