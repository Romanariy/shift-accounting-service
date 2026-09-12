from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

from asgiref.sync import async_to_sync
from django.test import TestCase

from apps.shifts.management.commands.run_shift_bot import process_message
from apps.shifts.models import Employee, Organization
from .models import Record, Service, Settings


class BotConfirmationTests(TestCase):
    def setUp(self):
        self.employee = Employee.objects.first()
        self.employee.telegram_user_id = 1234
        self.employee.default_work_type = "small_admin"
        self.employee.save()
        self.focus = Organization.objects.get(name="Фокус")
        Service.objects.filter(legacy_code="companion").update(default_organization=self.focus)
        Settings.objects.filter(pk=1).update(service_chat=-100, service_thread=10, expense_chat=-100, expense_thread=20)

    def message(self, text, mid=1, thread=10):
        return SimpleNamespace(text=text, message_id=mid, message_thread_id=thread, chat=SimpleNamespace(id=-100, type="supergroup"),
            from_user=SimpleNamespace(id=1234, username="author", full_name="Автор"),
            date=datetime(2026, 4, 12, 6, tzinfo=timezone.utc), reply=AsyncMock())

    def process(self, message, edited=False):
        async_to_sync(process_message)(message, edited=edited)
        message.reply.assert_awaited_once()
        return message.reply.call_args.args[0]

    def test_new_multiple_services_reply_after_actual_save(self):
        message = self.message("12.04 10:00–14:00 Фокус + 2 сопр")
        response = self.process(message)
        self.assertTrue(response.startswith("Записано:"))
        self.assertIn("Фокус", response)
        self.assertEqual(Record.objects.count(), 2)
        self.assertEqual(message.message_thread_id, 10)
        self.assertNotIn("Не удалось", response)

    def test_real_telegram_reply_targets_original_message_and_topic(self):
        from aiogram.types import Message
        bot = AsyncMock()
        message = Message.model_validate({"message_id":42,"message_thread_id":10,"is_topic_message":True,
            "date":datetime(2026,4,12,6,tzinfo=timezone.utc),"chat":{"id":-100,"type":"supergroup"},
            "from":{"id":1234,"is_bot":False,"first_name":"Автор"},
            "text":"12.04 10:00–14:00 Фокус"}).as_(bot)
        async_to_sync(process_message)(message)
        bot.assert_awaited_once()
        request = bot.call_args.args[0]
        self.assertEqual(request.chat_id, -100)
        self.assertEqual(request.message_thread_id, 10)
        self.assertEqual(request.reply_parameters.message_id, 42)
        self.assertTrue(request.text.startswith("Записано:"))
        self.assertEqual(Record.objects.count(), 1)

    def test_oneoff_expense_edit_duplicate_and_invalid_input(self):
        message = self.message("12.04, 1500, Починил дверь, Фокус")
        self.assertIn("1,500.00", self.process(message))
        message = self.message("12.04, 1500, Починил дверь, Фокус")
        self.process(message)
        self.assertEqual(Record.objects.count(), 1)
        message = self.message("12.04, 1700, Починил дверь, Фокус")
        self.assertIn("Записи обновлены:", self.process(message, edited=True))
        self.assertEqual(Record.objects.get().amount, 1700)
        message = self.message("Краска, 500, Фокус", mid=2, thread=20)
        self.assertIn("Записано:", self.process(message))
        self.assertEqual(Record.objects.get(message_id=2).kind, "expense")
        message = self.message("неправильная запись", mid=3)
        self.assertIn("Не удалось сохранить:", self.process(message))
        self.assertEqual(Record.objects.count(), 2)

    def test_shared_allocation_change_is_in_confirmation(self):
        self.process(self.message("12.04 10:00–12:00 Фокус"))
        response = self.process(self.message("12.04 12:00–14:00 Фокус", mid=2))
        self.assertIn("Перераспределено:", response)
        self.assertIn("600.00 → 400.00", response)
        self.assertEqual(list(Record.objects.values_list("amount", flat=True)), [400, 400])

    def test_reply_failure_does_not_rollback_or_duplicate_record(self):
        message = self.message("12.04, 1500, Починил дверь, Фокус")
        message.reply.side_effect = TimeoutError()
        with self.assertLogs("apps.shifts.management.commands.run_shift_bot", level="ERROR") as logs:
            async_to_sync(process_message)(message)
        self.assertIn("confirmation delivery failed", logs.output[0])
        self.assertEqual(Record.objects.count(), 1)
        self.process(self.message(message.text))
        self.assertEqual(Record.objects.count(), 1)

    def test_unconfigured_topic_and_private_chat_are_ignored(self):
        message = self.message("12.04, 1500, Починил дверь, Фокус", thread=99)
        async_to_sync(process_message)(message)
        message.reply.assert_not_awaited()
        message.chat.type = "private"
        message.message_thread_id = 10
        async_to_sync(process_message)(message)
        message.reply.assert_not_awaited()
        self.assertEqual(Record.objects.count(), 0)
