from copy import deepcopy
from datetime import date
from decimal import Decimal
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings
from PIL import Image

from apps.shifts.models import Employee, Organization
from apps.offers.models import ShiftOffer, OfferImage, RecognitionJob
from apps.offers.client_scans import enqueue_existing, finish_scan
from apps.offers.recognition_worker import run_once, claim_job
from apps.offers.storage import store_image
from .models import BonusClient, BonusRule, ClientBooking, Service, Rate, OrganizationBilling, TelegramContact
from .client_bonuses import save_rule, save_client
from .engine import save_record
from .billing import prepare, refresh, approve


class AutomaticClientBonusTests(TestCase):
    def setUp(self):
        self.directory = TemporaryDirectory(dir=Path(__file__).resolve().parents[3]/"tmp")
        self.addCleanup(self.directory.cleanup)
        settings = override_settings(OFFER_MEDIA_ROOT=Path(self.directory.name))
        settings.enable(); self.addCleanup(settings.disable)
        self.day = date(2026, 4, 12)
        self.org = Organization.objects.create(name="Auto studio")
        self.employee = Employee.objects.create(full_name="Auto employee", short_name="Auto employee")
        self.service = Service.objects.create(name="Auto admin", input_type="time", legacy_code="small_admin")
        Rate.objects.create(service=self.service, organization=self.org, calculation="hourly", price=200, start=self.day)
        self.rule = save_rule({"name":"Школьные съёмки", "organization":self.org.pk, "amount":"200", "start":str(self.day),
            "client_entries":[{"name":"Школа", "phones":["+79191234567", "89191234568"]}]})
        self.client_id = self.rule["clients"][0]
        self.offer = ShiftOffer.objects.create(source_key="automatic-old", sender_id=999, organization=self.org,
            date=self.day, state="claimed", employee=self.employee, comment="Исходное предложение")
        stream = BytesIO(); Image.new("RGB", (200, 300), "white").save(stream, format="PNG")
        path, sha = store_image(stream.getvalue())
        self.image = OfferImage.objects.create(offer=self.offer, path=path, sha256=sha, message_id=1)

    def work(self):
        return save_record({"kind":"service", "service":self.service.pk, "organization":self.org.pk,
            "employee":self.employee.pk, "date":str(self.day), "start_time":"10:00", "end_time":"18:00",
            "bonus":"500", "bonus_reason":"Дополнительная работа"})

    def result(self):
        return {"date":str(self.day), "headers":["Зал"], "intervals":[{"room":"Зал", "start":"12:00", "end":"13:00",
            "phone_candidates":[{"number":"+79191234567", "score":.99}]}]}

    def scan(self, result=None):
        self.assertEqual(enqueue_existing(),1)
        with patch("apps.offers.client_scans.analyze_image", return_value=deepcopy(result or self.result())):
            self.assertTrue(run_once(object()))

    def test_inline_clients_atomic_rule_creation_and_normalisation(self):
        self.assertFalse(self.rule["pending"])
        self.assertEqual(BonusClient.objects.get(pk=self.client_id).phones,["+79191234567", "+79191234568"])
        with self.assertRaises(ValidationError):
            save_rule({"name":"Ошибка", "organization":self.org.pk, "amount":"200", "start":str(self.day),
                "client_entries":[{"phones":["+79990000001"]}, {"phones":["+79191234567"]}]})
        self.assertEqual(BonusClient.objects.count(),1)
        self.assertEqual(BonusRule.objects.count(),1)

    def test_old_photographs_automatically_charge_without_republishing_or_confirmation(self):
        row = self.work()
        self.scan()
        row.refresh_from_db(); self.offer.refresh_from_db()
        self.assertEqual((row.client_bonus,row.bonus,row.amount),(200,500,2300))
        self.assertEqual(row.client_bonus_details[0]["rule"],"Школьные съёмки")
        self.assertEqual(ClientBooking.objects.get().state,"confirmed")
        self.assertEqual((self.offer.state,self.offer.version,self.offer.employee_id),("claimed",1,self.employee.pk))
        self.assertFalse(self.offer.deliveries.exists())
        self.assertEqual(enqueue_existing(),0)

    def test_planned_offer_only_waits_and_new_work_receives_bonus(self):
        self.scan()
        self.assertEqual(ClientBooking.objects.get().charge.state,"waiting")
        row = self.work(); self.assertEqual(row.client_bonus,200)

    def test_repeated_photos_and_same_client_in_two_halls_count_once(self):
        row = self.work()
        OfferImage.objects.create(offer=self.offer,path=self.image.path,sha256=self.image.sha256,message_id=2)
        result = self.result()
        result["intervals"].append({**deepcopy(result["intervals"][0]),"room":"Другой зал"})
        self.scan(result)
        row.refresh_from_db(); self.assertEqual(row.client_bonus,200)
        self.assertEqual(ClientBooking.objects.count(),1)
        self.assertEqual(len(ClientBooking.objects.get().rooms),2)

    def test_uncertain_phone_never_silently_charges(self):
        row = self.work(); result = self.result()
        result["intervals"][0]["phone_candidates"][0]["score"] = .7
        self.scan(result)
        row.refresh_from_db(); self.assertEqual(row.client_bonus,0)
        self.assertTrue(row.client_bonus_review)
        self.assertEqual(ClientBooking.objects.get().state,"draft")

    def test_stale_lease_and_offer_edits_cancel_scan_without_mutation(self):
        enqueue_existing(); job=claim_job()
        self.offer.version += 1; self.offer.save()
        finish_scan(job,job.client_scan_token,[(self.image,self.org.pk,self.result())])
        job.refresh_from_db(); self.assertEqual(job.state,"cancelled")
        self.assertFalse(ClientBooking.objects.exists())
        self.assertEqual(enqueue_existing(),1)

    def test_newly_registered_client_triggers_rescan_of_old_image(self):
        self.scan()
        save_client({"name":"Новый", "phones":["+79990000002"]})
        self.assertEqual(enqueue_existing(),1)
        self.assertEqual(enqueue_existing(),0)

    def test_two_studios_use_each_images_part(self):
        from apps.offers.models import OfferPart
        other = Organization.objects.create(name="Other studio")
        one=OfferPart.objects.create(offer=self.offer,organization=self.org)
        two=OfferPart.objects.create(offer=self.offer,organization=other)
        self.image.part=one;self.image.save()
        OfferImage.objects.create(offer=self.offer,part=two,path=self.image.path,sha256=self.image.sha256,message_id=2)
        self.scan()
        self.assertEqual(set(ClientBooking.objects.values_list("organization_id",flat=True)),{self.org.pk,other.pk})

    def test_backfill_preserves_approved_work_and_excel(self):
        row=self.work(); batch=prepare(self.day,self.day,[self.org.pk])
        contact=TelegramContact.objects.create(user_id=800001,name="Owner")
        OrganizationBilling.objects.get(organization=self.org).recipients.add(contact)
        refresh(batch);approve(batch.pk,batch.version)
        invoice=batch.invoices.get();artifact=bytes(invoice.artifact)
        self.scan()
        row.refresh_from_db();invoice.refresh_from_db()
        self.assertEqual((row.client_bonus,row.amount),(0,Decimal("2100")))
        self.assertEqual(bytes(invoice.artifact),artifact)

    def test_missing_local_photo_does_not_fail_offer(self):
        Path(self.directory.name,self.image.path).unlink()
        self.scan()
        job=RecognitionJob.objects.get(clients_only=True)
        self.assertIn("Недоступно",job.error)
        self.offer.refresh_from_db();self.assertEqual(self.offer.state,"claimed")

    def test_new_offer_uses_automatic_evidence_immediately(self):
        from .booking_imports import sync_offer, sanitize_phones
        row=self.work();result=self.result();sanitize_phones([result])
        self.offer.intervals=result["intervals"]
        with patch("apps.offers.service.allowed_organizations",return_value={self.org.pk}):
            sync_offer(self.offer);sync_offer(self.offer)
        row.refresh_from_db()
        self.assertEqual(row.client_bonus,200)
        self.assertEqual(ClientBooking.objects.get().state,"confirmed")

    def test_unresolved_image_studio_is_not_assigned_to_other_studio(self):
        self.offer.questions=[{"key":"image:0:organization"}];self.offer.save()
        self.assertEqual(enqueue_existing(),0)

    def test_same_photo_clarification_upgrades_partial_evidence_in_place(self):
        from .booking_imports import stage_bookings
        row=self.work()
        source={"type":"offer","id":self.offer.pk,"image_id":self.image.pk}
        item={"room":"Зал","start":"12:00","end":None,"clients":[self.client_id]}
        ids=stage_bookings([item],self.org.pk,self.day,source,automatic=True)
        item["end"]="13:00"
        self.assertEqual(stage_bookings([item],self.org.pk,self.day,source,automatic=True),ids)
        self.assertEqual(ClientBooking.objects.count(),1)
        self.assertEqual(ClientBooking.objects.get().state,"confirmed")

    def test_ocr_failure_retries_without_changing_claimed_offer(self):
        enqueue_existing()
        with patch("apps.offers.client_scans.analyze_image",side_effect=RuntimeError("Private OCR output")):
            run_once(object())
        job=RecognitionJob.objects.get(clients_only=True);self.offer.refresh_from_db()
        self.assertEqual((job.state,job.attempts),("pending",1))
        self.assertNotIn("Private",job.error)
        self.assertEqual(self.offer.state,"claimed")
