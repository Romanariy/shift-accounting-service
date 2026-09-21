from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest import skipUnless

from django.core.exceptions import ValidationError
from django.db import close_old_connections, connection
from django.test import TransactionTestCase

from . import service, updates
from .models import ShiftOffer
from .tests import Fixture


@skipUnless(connection.vendor == "postgresql", "PostgreSQL row locks required")
class UpdateConcurrencyTests(Fixture, TransactionTestCase):
    def setup_update(self, claimed=False):
        target=self.offer(state="open",topic_chat_id=-100123,topic_thread_id=77)
        if claimed: target=service.claim(target.pk,90003,target.version)
        draft=self.offer();draft.intervals[0]["start"]="12:00";service.recompute(draft);draft.save();self.image(draft)
        service.prompt(draft)
        return target,updates.choose(draft.pk,target.pk,"replace",user_id=90002,version=draft.version)

    def compete(self, operations):
        barrier=Barrier(2)
        def run(operation):
            close_old_connections();barrier.wait()
            try: operation();return True
            except ValidationError:return False
            finally:close_old_connections()
        with ThreadPoolExecutor(2) as pool:return list(pool.map(run,operations))

    def test_simultaneous_confirmation_applies_once(self):
        target,draft=self.setup_update()
        apply=lambda:updates.apply(draft.pk,user_id=90002,version=draft.version)
        self.assertEqual(sorted(self.compete([apply,apply])),[False,True])
        self.assertEqual(target.deliveries.filter(purpose="update_notice").count(),1)

    def test_claim_races_update_without_overwriting_assignment(self):
        target,draft=self.setup_update()
        self.assertEqual(sorted(self.compete([
            lambda:updates.apply(draft.pk,user_id=90002,version=draft.version),
            lambda:service.claim(target.pk,90003,target.version)])),[False,True])
        target.refresh_from_db();draft.refresh_from_db()
        self.assertTrue((target.state=="claimed" and draft.state=="update_review") or (target.state=="open" and draft.state=="applied"))

    def test_release_races_update_without_resurrecting_private_buttons(self):
        target,draft=self.setup_update(claimed=True)
        self.assertEqual(sorted(self.compete([
            lambda:updates.apply(draft.pk,user_id=90002,version=draft.version),
            lambda:service.release(target.pk,user_id=90003,version=target.version)])),[False,True])
        target.refresh_from_db()
        if target.state=="open":
            self.assertFalse(target.deliveries.filter(purpose="assignment",delete_requested=False).exists())
