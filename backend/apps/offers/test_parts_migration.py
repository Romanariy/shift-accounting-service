from datetime import date, time
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase


class PartsMigrationTests(TransactionTestCase):
    def test_old_offer_migrates_without_republication(self):
        executor=MigrationExecutor(connection)
        original=executor.loader.graph.leaf_nodes()
        self.addCleanup(lambda:MigrationExecutor(connection).migrate(original))
        previous=[('offers','0003_offerimage_active_shiftoffer_amount_and_more')]
        executor.migrate(previous)
        apps=executor.loader.project_state(previous).apps
        org=apps.get_model('shifts','Organization').objects.create(name='Migration studio')
        offer=apps.get_model('offers','ShiftOffer').objects.create(source_key='migration:legacy',sender_id=10,
            organization_id=org.pk,date=date(2026,10,27),start_time=time(10),end_time=time(18),state='claimed',version=8,
            delivery_generation=3,assignee_user_id=11,employee_name='Legacy employee',intervals=[{'room':'Зал','start':'10:00','end':'18:00'}])
        image=apps.get_model('offers','OfferImage').objects.create(offer_id=offer.pk,path='images/example.png',sha256='a'*64,message_id=1)
        executor=MigrationExecutor(connection);executor.migrate(original)
        from .models import ShiftOffer, OfferImage, OfferDelivery
        current=ShiftOffer.objects.get(pk=offer.pk);part=current.parts.get()
        self.assertEqual((current.version,current.delivery_generation,current.state,current.assignee_user_id),(8,3,'claimed',11))
        self.assertEqual((part.organization_id,part.start_time,part.end_time),(org.pk,time(10),time(18)))
        self.assertEqual(OfferImage.objects.get(pk=image.pk).part_id,part.pk)
        self.assertFalse(OfferDelivery.objects.filter(offer=current).exists())
