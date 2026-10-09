from concurrent.futures import ThreadPoolExecutor
from datetime import time
from threading import Barrier
from unittest import skipUnless
from django.core.exceptions import ValidationError
from django.db import connection, connections, close_old_connections
from django.test import TransactionTestCase
from apps.shifts.models import Organization, Employee, AuditLog
from .tests import Fixture
from .models import OfferPart, ShiftOffer, RecognitionProfile
from . import service, updates


@skipUnless(connection.vendor == "postgresql", "Requires PostgreSQL locks")
class BundleConcurrencyTests(Fixture, TransactionTestCase):
    def target(self):
        offer=self.offer("open",topic_chat_id=-100123,topic_thread_id=77)
        second=Organization.objects.create(name="Concurrency second studio")
        profile=RecognitionProfile.objects.create(organization=second)
        profile.publishers.add(self.contact)
        OfferPart.objects.create(offer=offer,organization=self.org,start_time=offer.start_time,end_time=offer.end_time,
                                 intervals=offer.intervals)
        OfferPart.objects.create(offer=offer,organization=second,start_time=time(16),end_time=time(22),
                                 intervals=[{"room":"Другой","start":"16:00","end":"22:00"}])
        from .parts import project
        project(offer);offer.save()
        return offer

    def race(self,actions):
        barrier=Barrier(len(actions))
        def run(action):
            close_old_connections()
            try:
                barrier.wait(timeout=15)
                action();return True
            except ValidationError:return False
            finally:connections.close_all()
        with ThreadPoolExecutor(len(actions)) as pool:return list(pool.map(run,actions))

    def test_only_one_employee_takes_both_parts(self):
        offer=self.target()
        Employee.objects.create(short_name="Bundle second employee",telegram_user_id=90004)
        self.assertEqual(sorted(self.race([lambda:service.claim(offer.pk,90003,offer.version),
                                         lambda:service.claim(offer.pk,90004,offer.version)])),[False,True])
        offer.refresh_from_db();self.assertEqual((offer.state,offer.parts.count()),("claimed",2))
        self.assertEqual(AuditLog.objects.filter(entity_type="shift_offer",entity_id=offer.pk,action="claimed").count(),1)

    def test_claim_and_update_cannot_use_the_same_old_version(self):
        target=self.target()
        draft=self.offer();draft.intervals=[{"room":"БОХО","start":"12:00","end":"19:00"}]
        service.recompute(draft);draft.save();self.image(draft);service.prompt(draft)
        draft=updates.choose(draft.pk,target.pk,"replace",version=draft.version)
        self.assertEqual(sorted(self.race([lambda:service.claim(target.pk,90003,target.version),
                                         lambda:updates.apply(draft.pk,version=draft.version)])),[False,True])
        target.refresh_from_db();self.assertEqual(target.parts.count(),2)
        self.assertEqual(target.parts.exclude(organization=self.org).get().end_time,time(22))

    def test_concurrent_release_returns_whole_bundle_once(self):
        target=self.target();target=service.claim(target.pk,90003,target.version)
        self.assertEqual(sorted(self.race([lambda:service.release(target.pk,user_id=90003,version=target.version),
                                         lambda:service.release(target.pk,user_id=90001,version=target.version)])),[False,True])
        target.refresh_from_db();self.assertEqual((target.state,target.employee_id,target.parts.count()),("open",None,2))
        self.assertEqual(target.deliveries.filter(purpose="release_notice").count(),1)
