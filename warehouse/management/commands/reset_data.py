from django.core.management.base import BaseCommand

from warehouse.services import reset_operational_data


class Command(BaseCommand):
    help = ("Обнуляет все оперативные данные (движения, тары, заказы, "
            "производство, брак, аудит). Справочник номенклатуры, зависимости "
            "и реквизиты остаются. ВСЕГДА требует флаг --yes.")

    def add_arguments(self, parser):
        parser.add_argument("--yes", action="store_true",
                            help="Подтверждение (обязателен)")
        parser.add_argument("--keep-warehouses", action="store_true",
                            help="Не удалять лишние склады (MAIN/RESERVE/ZLK останутся)")

    def handle(self, *args, **options):
        if not options["yes"]:
            self.stdout.write(self.style.ERROR(
                "Отказ: скрипт требует явного --yes.\n"
                "Запусти: python manage.py reset_data --yes"
            ))
            return

        deleted = reset_operational_data(
            keep_warehouses=options["keep_warehouses"]
        )

        self.stdout.write(self.style.SUCCESS("=== Очистка завершена ==="))
        for name, count in deleted.items():
            self.stdout.write(f"  {name}: удалено {count}")
        self.stdout.write("")
        self.stdout.write("Сохранено: Product, Category, BOM, "
                          "Warehouse (MAIN/RESERVE/ZLK), User, CompanySettings")
