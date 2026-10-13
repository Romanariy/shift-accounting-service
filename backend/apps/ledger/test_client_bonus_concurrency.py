from concurrent.futures import ThreadPoolExecutor
from datetime import date
from threading import Barrier
from unittest import skipUnless

from django.core.exceptions import ValidationError
from django.db import close_old_connections, connection, connections
from django.test import TransactionTestCase, override_settings

from apps.shifts.models import Employee, Organization
from .models import Rate, Service, Settings, ClientBonusCharge, BonusClient, Record
from .engine import save_record
from .client_bonuses import save_client, save_rule, save_booking, recalculate


@skipUnless(connection.vendor == 'postgresql', 'Requires PostgreSQL row locks')
@override_settings(SHIFT_SYNC_AFTER_WRITE=False)
class ClientBonusConcurrencyTests(TransactionTestCase):
    def setUp(self):
        Settings.objects.get_or_create(pk=1)
        self.org=Organization.objects.create(name='Concurrent bonus studio')
        self.employee=Employee.objects.create(short_name='Concurrent bonus employee')
        self.service=Service.objects.create(name='Concurrent bonus admin',legacy_code='small_admin')
        Rate.objects.create(service=self.service,organization=self.org,calculation='hourly',price=200,start=date(2026,1,1))
        self.client_id=save_client({'name':'Concurrent school','phones':['+79191234567']})['id']
        rule=save_rule({'name':'Concurrent rule','organization':self.org.pk,'clients':[self.client_id],'amount':'200','start':'2026-01-01'})
        request={'start':'2026-04-12','end':'2026-04-12','rule':rule['id'],'revision':rule['revision']}
        preview=recalculate(request);recalculate({**request,'fingerprint':preview['fingerprint']})
        self.work=save_record({'kind':'service','service':self.service.pk,'organization':self.org.pk,'employee':self.employee.pk,
                              'date':'2026-04-12','start_time':'10:00','end_time':'18:00'})

    def concurrent(self, fn):
        barrier=Barrier(2)
        def run(i):
            close_old_connections()
            try:
                barrier.wait(timeout=10)
                try:return fn(i)
                except ValidationError:return 'stale'
            finally:connections.close_all()
        with ThreadPoolExecutor(max_workers=2) as pool:return list(pool.map(run,[0,1]))

    def test_competing_confirmations_charge_once(self):
        booking=save_booking({'client':self.client_id,'organization':self.org.pk,'date':'2026-04-12','start_time':'12:00','end_time':'13:00'})
        result=self.concurrent(lambda i:save_booking({'revision':booking['revision']},booking['id'],'confirm')['state'])
        self.assertCountEqual(result,['confirmed','stale'])
        self.work.refresh_from_db();self.assertEqual(self.work.client_bonus,200)
        self.assertEqual(ClientBonusCharge.objects.filter(state='applied').count(),1)

    def test_competing_phone_registration_has_one_owner(self):
        result=self.concurrent(lambda i:save_client({'name':str(i),'phones':['8 (999) 222-33-44']})['id'])
        self.assertEqual(result.count('stale'),1)
        self.assertEqual(sum('+79992223344' in c.phones for c in BonusClient.objects.all()),1)

    def test_work_edit_and_confirmation_keep_current_amount(self):
        booking=save_booking({'client':self.client_id,'organization':self.org.pk,'date':'2026-04-12','start_time':'12:00','end_time':'13:00'})
        def action(i):
            if i:return save_record({'end_time':'17:00'},Record.objects.get(pk=self.work.pk)).pk
            return save_booking({'revision':booking['revision']},booking['id'],'confirm')['id']
        self.concurrent(action)
        self.work.refresh_from_db();self.assertEqual((self.work.amount,self.work.client_bonus),(1600,200))

    def test_two_workers_enqueue_existing_photos_once(self):
        from apps.offers.models import ShiftOffer,OfferImage,RecognitionJob
        from apps.offers.client_scans import enqueue_existing
        offer=ShiftOffer.objects.create(source_key='concurrent-client-scan',sender_id=1,organization=self.org,
                                       date=date(2026,4,12),state='claimed')
        OfferImage.objects.create(offer=offer,path='test.png',sha256='test',message_id=1)
        results=self.concurrent(lambda i:enqueue_existing())
        self.assertCountEqual(results,[0,1])
        self.assertEqual(RecognitionJob.objects.filter(clients_only=True).count(),1)
