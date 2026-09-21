import io
import json
import tempfile
from concurrent.futures import ThreadPoolExecutor
from datetime import date, time, timedelta
from pathlib import Path
from threading import Barrier
from unittest import skipUnless
from unittest.mock import patch, AsyncMock
from types import SimpleNamespace
from asgiref.sync import async_to_sync

from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection, close_old_connections
from django.test import TestCase, TransactionTestCase, SimpleTestCase, override_settings
from django.utils import timezone
from PIL import Image

from apps.ledger.models import Settings, TelegramContact, Record
from apps.shifts.models import Employee, Organization, AuditLog
from . import service
from .bot import callback_action, reply_to_question
from .delivery import claim_delivery, finish_delivery, resolve_delivery, claim_edit, finish_edit
from .models import OfferConfig, OfferDelivery, OfferImage, OfferMessageEdit, RecognitionJob, RecognitionProfile, ShiftOffer
from .recognition_worker import claim_job, finish_offer
from .recognizer import nearest_date, match_organization, merge_results, _room_labels
from .storage import store_image, media_path


def png():
    buffer = io.BytesIO()
    Image.new("RGB", (300, 600), "white").save(buffer, "PNG")
    return buffer.getvalue()


class Fixture:
    def setUp(self):
        super().setUp()
        self.temporary = tempfile.TemporaryDirectory()
        self.media = override_settings(OFFER_MEDIA_ROOT=self.temporary.name)
        self.media.enable()
        self.addCleanup(self.temporary.cleanup)
        self.addCleanup(self.media.disable)
        self.org = Organization.objects.create(name="OCR Test Studio", excel_sheet="OCR Test")
        self.chief = TelegramContact.objects.create(user_id=90001, name="Chief")
        self.contact = TelegramContact.objects.create(user_id=90002, name="Publisher")
        config, _ = Settings.objects.get_or_create(pk=1)
        config.approver = self.chief
        config.save()
        OfferConfig.objects.create(pk=1, chat_id=-100123, thread_id=77, claimed_thread_id=78, released_thread_id=79, enabled=True)
        self.profile = RecognitionProfile.objects.create(organization=self.org, rooms=[{"name":"БОХО","aliases":[]}], confirmed=True)
        self.profile.publishers.add(self.contact)
        self.employee = Employee.objects.create(short_name="OCR Employee", telegram_user_id=90003)
        self.counter = 0

    def offer(self, state="review", **extra):
        self.counter += 1
        return ShiftOffer.objects.create(source_key=f"test:{self.counter}", sender_id=self.contact.user_id,
            organization=self.org, date=timezone.localdate()+timedelta(days=2), start_time=time(13), end_time=time(20),
            intervals=[{"room":"БОХО", "start":"13:00", "end":"16:00"},{"room":"БОХО", "start":"16:30", "end":"20:00"}],
            state=state, **extra)

    def image(self, offer, message=1):
        path, digest = store_image(png())
        return OfferImage.objects.create(offer=offer, message_id=message, path=path, sha256=digest)


class OfferFlowTests(Fixture, TestCase):
    def test_permissions_are_per_organization(self):
        self.assertEqual(service.allowed_organizations(90002), {self.org.pk})
        with self.assertRaises(ValidationError): service.authorize_sender(123)
        self.profile.publishers.clear()
        with self.assertRaises(ValidationError): service.authorize_sender(90002)
        self.assertIn(self.org.pk, service.allowed_organizations(90001))

    def test_disabled_contact_and_organization(self):
        self.profile.active = False; self.profile.save()
        with self.assertRaises(ValidationError): service.authorize_sender(90002)
        self.org.is_active = False; self.org.save()
        self.assertNotIn(self.org.pk, service.allowed_organizations(90001))

    def test_album_idempotence_and_first_caption(self):
        def receive(message, caption):
            path, digest = store_image(png())
            return service.receive_image(90002,"Publisher","album:123",message,path,digest,caption=caption)[0]
        with self.captureOnCommitCallbacks(execute=True):
            offer=receive(20,"Second"); receive(10,"First"); receive(10,"Repeated")
        offer.refresh_from_db()
        self.assertEqual(offer.images.count(),2)
        self.assertEqual(offer.comment,"First")
        self.assertEqual(offer.jobs.filter(state="pending").count(),1)
        self.assertEqual(len(list(Path(self.temporary.name).rglob("*.png"))),2)

    def test_publish_orders_photos_before_control(self):
        offer=self.offer(); self.image(offer)
        service.publish(offer.pk,user_id=90002,version=offer.version)
        photos=claim_delivery(); self.assertEqual(photos.purpose,"photos")
        self.assertIsNone(claim_delivery())
        finish_delivery(photos.pk,[10])
        control=claim_delivery(); self.assertEqual(control.purpose,"topic")
        finish_delivery(control.pk,[11]); offer.refresh_from_db()
        self.assertEqual(offer.state,"open")
        self.assertEqual(Record.objects.count(),0)

    def test_past_and_unresolved_never_publish(self):
        offer=self.offer(); offer.date=timezone.localdate()-timedelta(days=1); offer.save()
        with self.assertRaises(ValidationError): service.publish(offer.pk)
        offer.date=timezone.localdate()+timedelta(days=1); offer.questions=[{"key":"date"}]; offer.save()
        with self.assertRaises(ValidationError): service.publish(offer.pk)

    def test_permission_is_rechecked_at_publication(self):
        offer=self.offer(); self.profile.publishers.clear()
        with self.assertRaises(ValidationError): service.publish(offer.pk)

    def test_claim_release_and_audit(self):
        offer=self.offer("open")
        claimed=service.claim(offer.pk,90003,offer.version)
        self.assertEqual(claimed.employee,self.employee)
        self.assertEqual(set(claimed.deliveries.filter(purpose="assignment").values_list("recipient",flat=True)),{90001,90003})
        with self.assertRaises(ValidationError): service.release(offer.pk,user_id=90002)
        with self.assertRaises(ValidationError): service.claim(offer.pk,90003,offer.version)
        service.release(offer.pk,user_id=90003,version=claimed.version)
        offer.refresh_from_db(); self.assertEqual(offer.state,"open"); self.assertIsNone(offer.employee_id)
        self.assertEqual(AuditLog.objects.filter(entity_type="shift_offer",entity_id=offer.pk).count(),2)
        self.assertEqual(Record.objects.count(),0)

    def test_inactive_or_ambiguous_employee_cannot_claim(self):
        offer=self.offer("open")
        self.employee.is_active=False; self.employee.save()
        with self.assertRaises(ValidationError): service.claim(offer.pk,90003)
        self.employee.is_active=True; self.employee.save()
        Employee.objects.create(short_name="Duplicate Telegram",telegram_user_id=90003)
        with self.assertRaises(ValidationError): service.claim(offer.pk,90003)

    def test_overlapping_assignments_rejected(self):
        first=self.offer("open"); second=self.offer("open")
        service.claim(first.pk,90003)
        with self.assertRaises(ValidationError): service.claim(second.pk,90003)

    def test_active_assignment_cannot_silently_change_time(self):
        offer=service.claim(self.offer("open").pk,90003)
        with self.assertRaises(ValidationError): service.edit_published(offer.pk,{"start_time":"14:00"})
        service.edit_published(offer.pk,{"comment":"Bring keys"})

    def test_end_time_closes_claimed_and_open(self):
        opened=self.offer("open"); claimed=service.claim(self.offer("open").pk,90003)
        yesterday=timezone.localdate()-timedelta(days=1)
        ShiftOffer.objects.filter(pk__in=[opened.pk,claimed.pk]).update(date=yesterday)
        service.expire_offers(); opened.refresh_from_db(); claimed.refresh_from_db()
        self.assertEqual((opened.state,claimed.state),("expired","completed"))
        self.assertIsNotNone(claimed.closed_at)
        with self.assertRaises(ValidationError): service.release(claimed.pk,user_id=90003)

    def test_cancel_cannot_be_claimed(self):
        offer=self.offer("open"); service.cancel(offer.pk)
        with self.assertRaises(ValidationError): service.claim(offer.pk,90003)

    def test_interval_question_recomputes_bounds(self):
        offer=self.offer("needs_input", questions=[{"key":"interval:1:end","label":"End?","type":"time"}])
        offer.intervals[1]["end"]=None; offer.save()
        result=service.answer(offer.pk,{"interval:1:end":"20:30"},user_id=90002,version=offer.version)
        self.assertEqual(result.end_time,time(20,30)); self.assertEqual(result.state,"review")
        self.assertTrue(result.intervals[1]["human_verified"])
        audit=AuditLog.objects.get(entity_type="shift_offer",entity_id=offer.pk,action="clarified")
        self.assertIsNone(audit.diff["intervals"]["from"][1]["end"])
        self.assertEqual(audit.diff["intervals"]["to"][1]["end"],"20:30")

    def test_manual_intervals_clear_only_interval_questions(self):
        offer=self.offer("needs_input",questions=[{"key":"intervals"},{"key":"interval:1:end"},{"key":"date"}])
        result=service.answer(offer.pk,{"intervals":[{"start":"10:00","end":"12:00"}]})
        self.assertEqual(result.questions,[{"key":"date"}])
        with self.assertRaises(ValidationError): service.answer(offer.pk,{"intervals":[{"start":"12:00","end":"10:00"}]})

    def test_mixed_album_must_split(self):
        offer=self.offer("needs_input",questions=[{"key":"split"}]); one=self.image(offer,1); two=self.image(offer,2)
        with self.assertRaises(ValidationError): service.answer(offer.pk,{"split":"ok"})
        with self.assertRaises(ValidationError): service.split_offer(offer.pk,[[one.pk],[one.pk]])
        children=service.split_offer(offer.pk,[[one.pk],[two.pk]])
        self.assertEqual(len(children),2); offer.refresh_from_db(); self.assertEqual(offer.state,"cancelled")
        self.assertEqual(offer.images.count(),0); self.assertEqual(RecognitionJob.objects.filter(offer_id__in=children).count(),2)
        one.refresh_from_db(); self.assertTrue(media_path(one.path).is_file())

    def test_stale_worker_cannot_overwrite_late_album(self):
        offer=self.offer("collecting"); im=self.image(offer)
        job=RecognitionJob.objects.create(offer=offer,revision=offer.version,available_at=timezone.now())
        lease=claim_job()
        offer.version+=1; offer.save()
        finish_offer(lease,[im],[{}],{},10)
        job.refresh_from_db(); self.assertEqual(job.state,"cancelled")

    def test_expired_lease_recovered_and_old_attempt_cannot_cancel_it(self):
        offer=self.offer("collecting"); im=self.image(offer)
        job=RecognitionJob.objects.create(offer=offer,revision=offer.version,available_at=timezone.now())
        old=claim_job(); RecognitionJob.objects.filter(pk=job.pk).update(started_at=timezone.now()-timedelta(minutes=4))
        new=claim_job(); self.assertEqual(new.attempts,2)
        finish_offer(old,[im],[{}],{},10)
        job.refresh_from_db(); self.assertEqual(job.state,"running")

    def test_duplicate_image_set_is_not_republished(self):
        first=self.offer("collecting"); im=self.image(first)
        RecognitionJob.objects.create(offer=first,revision=first.version,available_at=timezone.now())
        result={"date":str(first.date),"organization":self.org.pk,"intervals":first.intervals,"questions":[],"evidence":{}}
        finish_offer(claim_job(),[im],[{}],result,10)
        second=self.offer("collecting"); im2=self.image(second)
        RecognitionJob.objects.create(offer=second,revision=second.version,available_at=timezone.now())
        finish_offer(claim_job(),[im2],[{}],result,10)
        second.refresh_from_db(); self.assertEqual(second.state,"duplicate"); self.assertEqual(second.duplicate_of_id,first.pk)

    def test_unknown_delivery_stays_blocked_until_reconciled(self):
        offer=self.offer(); self.image(offer); published=service.publish(offer.pk)
        d=claim_delivery(); OfferDelivery.objects.filter(pk=d.pk).update(started_at=timezone.now()-timedelta(minutes=4))
        self.assertIsNone(claim_delivery()); d.refresh_from_db(); self.assertEqual(d.state,"unknown")
        resolve_delivery(offer.pk,d.pk,"sent",[51],published.version)
        self.assertEqual(claim_delivery().purpose,"topic")

    def test_delayed_publication_rechecks_time_and_sender_permission(self):
        for revoked in (False, True):
            offer=self.offer(); self.image(offer); service.publish(offer.pk)
            if revoked:
                self.profile.publishers.clear()
            else:
                ShiftOffer.objects.filter(pk=offer.pk).update(date=timezone.localdate()-timedelta(days=1))
            result=claim_delivery()
            self.assertTrue(result is None or result.purpose == "review")
            offer.refresh_from_db(); self.assertEqual(offer.state,"failed")
            self.assertEqual(offer.deliveries.filter(purpose__in=("photos","topic"),state="cancelled").count(),2)
            # Do not leave the first iteration's private notification at the head of the queue.
            offer.deliveries.exclude(purpose__in=("photos","topic")).update(state="cancelled")

    def test_old_edit_cannot_erase_new_revision(self):
        offer=self.offer("open"); delivery=service.enqueue(offer,"topic",-100123)
        delivery.state="sent"; delivery.message_ids=[1]; delivery.save()
        OfferMessageEdit.objects.create(delivery=delivery,available_at=timezone.now())
        old=claim_edit(); service.claim(offer.pk,90003)
        finish_edit(old); old.refresh_from_db(); self.assertEqual(old.state,"pending")

    def test_old_delivery_cannot_overwrite_reconciled_retry(self):
        offer=self.offer(); self.image(offer); service.publish(offer.pk)
        old=claim_delivery()
        OfferDelivery.objects.filter(pk=old.pk).update(state="unknown")
        offer.refresh_from_db()
        resolve_delivery(offer.pk,old.pk,"not_sent",[],offer.version)
        new=claim_delivery()
        finish_delivery(old.pk,[100],attempt=old.attempts)
        new.refresh_from_db(); self.assertEqual(new.state,"sending"); self.assertEqual(new.message_ids,[])
        finish_delivery(new.pk,[101],attempt=new.attempts)
        new.refresh_from_db(); self.assertEqual(new.message_ids,[101])

    def test_failed_edit_is_visible_and_retryable_without_new_message(self):
        offer=self.offer("open"); delivery=service.enqueue(offer,"topic",-100123)
        delivery.state="sent"; delivery.message_ids=[1]; delivery.save()
        edit=OfferMessageEdit.objects.create(delivery=delivery,state="failed",error="No access",available_at=timezone.now())
        response=self.client.get(f"/api/shifts/ledger/offers/{offer.pk}/")
        self.assertEqual(response.json()["deliveries"][0]["edit_state"],"failed")
        resolve_delivery(offer.pk,delivery.pk,"retry_edit",[],offer.version)
        edit.refresh_from_db(); self.assertEqual(edit.state,"pending")
        self.assertEqual(offer.deliveries.count(),1)

    def test_callbacks_must_come_from_known_message(self):
        offer=self.offer("open")
        with self.assertRaises(ValidationError): callback_action(f"offer:claim:{offer.pk}:{offer.version}",90003,-100123,12)
        delivery=service.enqueue(offer,"topic",-100123); delivery.state="sent"; delivery.message_ids=[12]; delivery.save()
        callback_action(f"offer:claim:{offer.pk}:{offer.version}",90003,-100123,12)
        offer.refresh_from_db(); self.assertEqual(offer.state,"claimed")

    def test_replies_are_bound_to_sender_message_and_version(self):
        offer=self.offer("needs_input",questions=[{"key":"date","label":"Date","type":"date"}])
        delivery=service.enqueue(offer,"question",90002,prompt_key="date")
        delivery.state="sent"; delivery.message_ids=[70]; delivery.save()
        self.assertFalse(reply_to_question(777,70,"2027-01-01"))
        self.assertTrue(reply_to_question(90002,70,"2027-01-01"))
        self.assertFalse(reply_to_question(90002,70,"2028-01-01"))

    def test_interval_reply_does_not_silently_ignore_unreadable_parts(self):
        offer=self.offer("needs_input",questions=[{"key":"intervals","type":"intervals"}])
        delivery=service.enqueue(offer,"question",90002,prompt_key="intervals")
        delivery.state="sent"; delivery.message_ids=[71]; delivery.save()
        with self.assertRaises(ValidationError): reply_to_question(90002,71,"13:00–16:00, 17:00–?")
        self.assertTrue(reply_to_question(90002,71,"13:00–16:00, 17:00–20:00"))

    def test_retention_preserves_recent_and_open_images(self):
        old=self.offer("cancelled",closed_at=timezone.now()-timedelta(days=31)); image=self.image(old)
        recent=self.offer("completed",closed_at=timezone.now()-timedelta(days=29)); keep=self.image(recent)
        opened=self.image(self.offer("open"))
        path=media_path(image.path); service.purge_images(); image.refresh_from_db()
        self.assertIsNotNone(image.purged_at); self.assertFalse(path.exists())
        self.assertTrue(media_path(keep.path).exists()); self.assertTrue(media_path(opened.path).exists())

    def test_sample_upload_and_binary_private_image(self):
        response=self.client.post(f"/api/shifts/ledger/offer-profiles/{self.org.pk}/sample/",{"image":SimpleUploadedFile("ref.png",png(),content_type="image/png")})
        self.assertEqual(response.status_code,200,response.content)
        self.profile.refresh_from_db(); self.assertTrue(self.profile.sample_path)
        self.assertTrue(RecognitionJob.objects.filter(profile=self.profile).exists())
        image=self.image(self.offer())
        response=self.client.get(f"/api/shifts/ledger/offer-images/{image.pk}/")
        self.assertEqual(response["Content-Type"],"image/png"); self.assertEqual(b"".join(response.streaming_content),png())
        self.assertEqual(response["Cache-Control"],"private, no-store")

    def test_profile_requires_valid_contact_and_explicit_confirmation(self):
        url=f"/api/shifts/ledger/offer-profiles/{self.org.pk}/"
        response=self.client.post(url,json.dumps({"rooms":[{"name":"БОХО"}],"publishers":[999999]}),content_type="application/json")
        self.assertEqual(response.status_code,400)
        response=self.client.post(url,json.dumps({"rooms":[{"name":"БОХО"}],"publishers":[self.contact.pk]}),content_type="application/json")
        self.assertEqual(response.status_code,200); self.assertFalse(response.json()["confirmed"])

    def test_settings_reject_existing_accounting_topic(self):
        settings=Settings.objects.get(pk=1); settings.service_chat=-10055; settings.service_thread=None; settings.save()
        response=self.client.post("/api/shifts/ledger/offer-config/",json.dumps({"enabled":True,"chat_id":-10055,"thread_id":22}),content_type="application/json")
        self.assertEqual(response.status_code,400)

    def test_web_action_requires_version(self):
        offer=self.offer("open")
        response=self.client.post(f"/api/shifts/ledger/offers/{offer.pk}/cancel/",json.dumps({}),content_type="application/json")
        self.assertEqual(response.status_code,400); offer.refresh_from_db(); self.assertEqual(offer.state,"open")


class RecognitionRuleTests(SimpleTestCase):
    def test_halls_below_old_date_cutoff_identify_studio(self):
        # OCR boxes from the 720x1280 screenshot, normalized to width 900.
        # Every hall ends below the former 128 + .10*1600 = 288 cutoff.
        rows = [{"text": text, "box": box} for text, box in (
            ("27 сентября", [34, 82, 253, 128]),
            ("БОХО", [151, 263, 259, 308]),
            ("Модерн", [329, 264, 478, 313]),
            ("INLIGHT", [525, 264, 676, 304]),
            ("Стол в...", [714, 264, 871, 310]),
        )]
        headers = [r["text"] for r in _room_labels(rows, 128, 328, 1600, 900)]
        self.assertEqual(headers, ["БОХО", "Модерн", "INLIGHT", "Стол в..."])
        profiles = [{"organization": 1, "rooms": [{"name": name} for name in
                    ("БОХО", "Модерн", "INLIGHT", "Стол визажный")]}]
        image = {"date": "2026-09-27", "date_ambiguous": False, "headers": headers,
                 "intervals": [{"room": "Модерн", "start": "14:00", "end": None}]}
        merged = merge_results([image], profiles)
        self.assertEqual(merged["organization"], 1)
        self.assertEqual([q["key"] for q in merged["questions"]], ["interval:0:end"])

    def test_calendar_text_cannot_become_a_hall_header(self):
        rows = [{"text": "Зал", "box": [150, 200, 250, 230]},
                {"text": "10:00", "box": [5, 240, 90, 265]},
                {"text": "Сегодня", "box": [400, 201, 520, 230]},
                {"text": "Подпись карточки", "box": [150, 280, 320, 310]},
                {"text": "Ещё подпись", "box": [400, 280, 530, 310]}]
        self.assertEqual([r["text"] for r in _room_labels(rows, 100, 240, 1600, 900)], ["Зал"])

    def test_distinct_parallel_bookings_are_not_deduplicated_within_image(self):
        item={"room":"БОХО","start":"13:00","end":"16:00"}
        result=merge_results([{"headers":["БОХО"],"intervals":[item,item]}]*2,
            [{"organization":1,"rooms":[{"name":"БОХО"}]}])
        self.assertEqual(len(result["intervals"]),2)

    def test_incomplete_card_always_requires_all_intervals(self):
        for issue in ("partial_booking", "unreadable_booking", "partial_booking_at_bottom"):
            image={"date":"2026-09-12","date_ambiguous":False,"headers":["БОХО"],"issues":[issue],
                "intervals":[{"room":"БОХО","start":"13:00","end":"16:00"}]}
            result=merge_results([image],[{"organization":1,"rooms":[{"name":"БОХО"}]}])
            self.assertIn("intervals",[q["key"] for q in result["questions"]])

    def test_year_rollover_and_past_confirmation(self):
        self.assertEqual(nearest_date(2,1,date(2026,12,30)),("2027-01-02",False))
        self.assertEqual(nearest_date(12,9,date(2026,9,16)),("2026-09-12",True))
        self.assertEqual(nearest_date(31,2,date(2026,1,1)),(None,False))

    def test_truncated_room_and_ambiguous_organizations(self):
        profiles=[{"organization":1,"rooms":[{"name":"БОХО"},{"name":"Модерн"},{"name":"INLIGHT"},{"name":"Стол визажный"}]}]
        self.assertEqual(match_organization(["БОХО","Модерн","INLIGHT","Стол ви…"],profiles)[0],1)
        self.assertIsNone(match_organization(["Стол ви…"],profiles+[dict(profiles[0],organization=2)])[0])
        self.assertIsNone(match_organization([],profiles)[0])

    def test_cropped_end_is_question_even_if_other_record_covers_it(self):
        image={"date":"2026-09-12","date_ambiguous":False,"headers":["БОХО"],"intervals":[{"room":"БОХО","start":"13:00","end":"20:00"},{"room":"Модерн","start":"16:30","end":None}]}
        profiles=[{"organization":1,"rooms":[{"name":"БОХО"}]}]
        result=merge_results([image,image],profiles)
        self.assertEqual(len(result["intervals"]),2)
        self.assertIn("interval:1:end",[q["key"] for q in result["questions"]])

    def test_different_dates_force_split(self):
        image={"date":"2026-09-12","date_ambiguous":False,"headers":[],"intervals":[]}
        result=merge_results([image,dict(image,date="2026-09-13")],[])
        self.assertIn("split",[q["key"] for q in result["questions"]])

    @override_settings(OFFER_AUTO_PUBLISH=True,OFFER_QUALITY_REPORT="missing-report.json")
    def test_auto_publication_requires_quality_evidence(self):
        self.assertFalse(service.quality_gate())

    def test_image_path_escape_and_malformed_upload(self):
        with self.assertRaises(ValidationError): media_path("../../secret")
        with self.assertRaises(ValidationError): store_image(b"not an image")


class DeliveryIntegrationTests(Fixture, TestCase):
    def test_backup_restore_quarantines_sends_but_keeps_delivered_ids(self):
        from django.core.management import call_command
        offer=self.offer("open")
        pending=service.enqueue(offer,"review",90002)
        sent=service.enqueue(offer,"topic",-100123); sent.state="sent"; sent.message_ids=[88]; sent.save()
        job=RecognitionJob.objects.create(offer=offer,revision=offer.version,state="running",available_at=timezone.now())
        call_command("prepare_offer_restore",stdout=io.StringIO())
        pending.refresh_from_db(); sent.refresh_from_db(); job.refresh_from_db()
        self.assertEqual(pending.state,"unknown"); self.assertEqual(sent.message_ids,[88]); self.assertEqual(sent.state,"sent")
        self.assertEqual(job.state,"pending")

    def test_duplicate_album_images_need_one_ocr_inference(self):
        from .recognition_worker import run_once
        offer=self.offer("collecting"); self.image(offer,1); self.image(offer,2)
        RecognitionJob.objects.create(offer=offer,revision=offer.version,available_at=timezone.now())
        result={"date":str(offer.date),"date_ambiguous":False,"headers":["БОХО"],"intervals":offer.intervals,"issues":[]}
        with patch("apps.offers.recognition_worker.analyze_image",return_value=result) as analyze:
            run_once(object())
        self.assertEqual(analyze.call_count,1)
        offer.refresh_from_db(); self.assertEqual(len(offer.intervals),2)

    def bot(self):
        return SimpleNamespace(send_photo=AsyncMock(return_value=SimpleNamespace(message_id=50)),
            send_media_group=AsyncMock(return_value=[SimpleNamespace(message_id=50),SimpleNamespace(message_id=51)]),
            send_message=AsyncMock(return_value=SimpleNamespace(message_id=52)),edit_message_text=AsyncMock(),delete_message=AsyncMock(),edit_message_reply_markup=AsyncMock())

    def test_receive_recognize_confirm_publish_take_release_without_network(self):
        from .recognition_worker import run_once as recognize
        from .delivery import run_once as deliver
        path,digest=store_image(png())
        offer,_=service.receive_image(90002,"Publisher","integration",1,path,digest,caption="Test comment")
        offer.jobs.update(available_at=timezone.now())
        result={"date":str(timezone.localdate()+timedelta(days=1)),"date_ambiguous":False,"headers":["БОХО"],
            "intervals":[{"room":"БОХО","start":"13:00","end":"16:00"},{"room":"БОХО","start":"16:30","end":"20:00"}],"issues":[]}
        with patch("apps.offers.recognition_worker.analyze_image",return_value=result):
            recognize(object())
        offer.refresh_from_db(); self.assertEqual(offer.state,"review"); self.assertEqual(offer.end_time,time(20))
        bot=self.bot()
        async_to_sync(deliver)(bot)
        review=offer.deliveries.get(purpose="review")
        self.assertEqual(review.state,"sent")
        callback_action(f"offer:publish:{offer.pk}:{offer.version}",90002,90002,52)
        for _ in range(25): async_to_sync(deliver)(bot)
        offer.refresh_from_db(); self.assertEqual(offer.state,"open")
        self.assertEqual(bot.send_photo.await_count,1)
        callback_action(f"offer:claim:{offer.pk}:{offer.version}",90003,-100123,52)
        for _ in range(25): async_to_sync(deliver)(bot)
        offer.refresh_from_db(); self.assertEqual(offer.state,"claimed")
        assignment=offer.deliveries.get(purpose="assignment",recipient=90003)
        self.assertEqual(assignment.state,"sent")
        callback_action(f"offer:release:{offer.pk}:{offer.version}",90003,90003,52)
        for _ in range(25): async_to_sync(deliver)(bot)
        offer.refresh_from_db(); self.assertEqual(offer.state,"open")
        self.assertEqual(Record.objects.count(),0)

    def test_uncertain_send_is_not_retried_automatically(self):
        from .delivery import run_once
        offer=self.offer(); self.image(offer); service.publish(offer.pk)
        bot=self.bot(); bot.send_photo.side_effect=TimeoutError()
        async_to_sync(run_once)(bot); async_to_sync(run_once)(bot)
        self.assertEqual(bot.send_photo.await_count,1)
        self.assertEqual(offer.deliveries.get(purpose="photos").state,"unknown")
        self.assertEqual(bot.send_message.await_count,0)

    def test_identical_album_images_are_published_once(self):
        from .delivery import run_once
        offer=self.offer(); self.image(offer,1); self.image(offer,2); service.publish(offer.pk)
        bot=self.bot(); async_to_sync(run_once)(bot)
        self.assertEqual(bot.send_photo.await_count,1)
        self.assertEqual(bot.send_media_group.await_count,0)


@skipUnless(connection.vendor == "postgresql", "PostgreSQL row locks required")
class ConcurrentClaimTests(Fixture, TransactionTestCase):
    def test_exactly_one_winner(self):
        offer=self.offer("open")
        Employee.objects.create(short_name="Second OCR Employee",telegram_user_id=90004)
        barrier=Barrier(2)
        def compete(user):
            close_old_connections(); barrier.wait()
            try:
                service.claim(offer.pk,user,offer.version)
                return True
            except ValidationError:
                return False
            finally:
                close_old_connections()
        with ThreadPoolExecutor(2) as pool:
            results=list(pool.map(compete,[90003,90004]))
        self.assertEqual(sorted(results),[False,True])
        self.assertEqual(AuditLog.objects.filter(entity_type="shift_offer",entity_id=offer.pk,action="claimed").count(),1)
