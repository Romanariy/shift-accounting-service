from datetime import date
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.test import TestCase

from apps.shifts.models import Employee, Organization
from .ingest import ingest
from .models import Record, Service


class SimpleMessageTests(TestCase):
    def setUp(self):
        self.employee = Employee.objects.first()
        self.employee.telegram_user_id = 1234
        self.employee.default_work_type = "small_admin"
        self.employee.save()
        self.org = Organization.objects.get(name="Фокус")
        Service.objects.filter(legacy_code="companion").update(default_organization=self.org)

    def ingest(self, text, **kwargs):
        return ingest(text, chat_id=-100, message_id=kwargs.pop("message_id", 1), user_id=1234,
                      author_name="Автор", today=date(2026, 9, 12), **kwargs)

    def test_simple_expenses_optional_date_decimals_and_punctuation(self):
        cases = [
            ("Краска 1500 Фокус", "Краска", "1500.00", date(2026, 9, 12)),
            ("11.09 Краска и кисти 1500,50 Фокус", "Краска и кисти", "1500.50", date(2026, 9, 11)),
            ("11.09.2025 Краска, кисти + 2 валика 1500.50 Фокус", "Краска, кисти + 2 валика", "1500.50", date(2025, 9, 11)),
            ("2 валика 1500 Фокус", "2 валика", "1500.00", date(2026, 9, 12)),
            ("Краска\u00a01500\u00a0Фокус", "Краска", "1500.00", date(2026, 9, 12)),
            ("Краска 1 500,50 Фокус", "Краска", "1500.50", date(2026, 9, 12)),
        ]
        for i, (text, description, amount, day) in enumerate(cases, 1):
            with self.subTest(text=text):
                r = self.ingest(text, message_id=i, expense=True)[0]
                self.assertEqual((r.kind, r.description, r.amount, r.date), ("expense", description, Decimal(amount), day))
                self.assertIsNone(r.employee_id)

    def test_simple_jobs_optional_date_employee_and_both_amount_positions(self):
        cases = [
            ("Починил дверь 1500 Фокус", "Починил дверь", date(2026, 9, 12)),
            ("11.09 Починил дверь 1500 Фокус", "Починил дверь", date(2026, 9, 11)),
            ("11.09 1500 Починил дверь Фокус", "Починил дверь", date(2026, 9, 11)),
            ("1500 Починил дверь Фокус", "Починил дверь", date(2026, 9, 12)),
            ("Починил дверь 1 500 Фокус", "Починил дверь", date(2026, 9, 12)),
            ("2 ручки заменил 1500 Фокус", "2 ручки заменил", date(2026, 9, 12)),
            ("Починил дверь, замок + ручку 1500 Фокус", "Починил дверь, замок + ручку", date(2026, 9, 12)),
        ]
        for i, (text, description, day) in enumerate(cases, 1):
            with self.subTest(text=text):
                r = self.ingest(text, message_id=i)[0]
                self.assertEqual((r.kind, r.description, r.date, r.amount), ("oneoff", description, day, 1500))
                self.assertEqual(r.employee_id, self.employee.pk)
        other = Employee.objects.exclude(pk=self.employee.pk).first()
        other.full_name = "Тестовый Другой Сотрудник"
        other.save()
        for i, text in enumerate(("Тестовый Другой Сотрудник 11.09 Починил дверь 1500,50 Фокус", "11.09 Тестовый Другой Сотрудник 1500.50 Починил дверь Фокус"), 20):
            r = self.ingest(text, message_id=i)[0]
            self.assertEqual(r.employee_id, other.pk)
            self.assertEqual(r.amount, Decimal("1500.50"))
            self.assertEqual(r.date, date(2026, 9, 11))

    def test_multiword_organization_alias_and_inactive_suffix(self):
        org = Organization.objects.create(name="Новый Фокус", aliases=["новая студия"], excel_sheet="Тест")
        for i, suffix in enumerate(("Новый Фокус", "новая студия", "НОВАЯ СТУДИЯ."), 1):
            self.assertEqual(self.ingest("Починил дверь 500 " + suffix, message_id=i)[0].organization_id, org.pk)
        org.is_active = False
        org.save()
        with self.assertRaises(ValidationError):
            self.ingest("Починил дверь 500 Новый Фокус", message_id=10)

    def test_invalid_new_messages_do_not_save(self):
        for expense in (False, True):
            for text in ("Покупка 0 Фокус", "Покупка -20 Фокус", "Покупка 1500,500 Фокус", "Покупка Фокус",
                         "Покупка 1500 Неизвестная", "31.02 Покупка 1500 Фокус", "1500 Фокус"):
                with self.subTest(expense=expense, text=text), self.assertRaises(ValidationError):
                    self.ingest(text, expense=expense)
        self.assertEqual(Record.objects.count(), 0)

    def test_typed_services_keep_precedence_and_plus_splitting(self):
        for i, text in enumerate(("12.09 10:00-14:00 Фокус + 2 сопр Фокус", "+ 2 сопр Фокус + Уборка Фокус",
                                  "12.09 Уборка Фокус купил краску 1500", "12.09 2 сопр Фокус доплата 1500"), 1):
            rows = self.ingest(text, message_id=i)
            self.assertTrue(all(r.kind == "service" for r in rows))
        self.assertEqual(Record.objects.filter(message_id=1).count(), 2)
        self.assertEqual(Record.objects.filter(message_id=2).count(), 2)
        with self.assertRaises(ValidationError):
            self.ingest("0 сопр Фокус", message_id=20)

    def test_legacy_messages_edit_to_new_format_without_duplicate(self):
        r = self.ingest("12.09, 1500, Починил дверь, Фокус")[0]
        updated = self.ingest("12.09 Починил дверь 1700 Фокус", edited=True)[0]
        self.assertEqual(updated.pk, r.pk)
        self.assertEqual(updated.amount, 1700)
        self.ingest("12.09 Починил дверь 1700 Фокус")
        self.assertEqual(Record.objects.count(), 1)
        r = self.ingest("12.09, Краска, кисть, 1500,50, Фокус", expense=True, message_id=2)[0]
        self.assertEqual(r.description, "Краска, кисть")
        updated = self.ingest("Краска, кисть 1700,50 Фокус", expense=True, message_id=2, edited=True)[0]
        self.assertEqual(updated.pk, r.pk)
        self.assertEqual(updated.amount, Decimal("1700.50"))
