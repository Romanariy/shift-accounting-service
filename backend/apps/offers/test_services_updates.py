import io
from datetime import timedelta, time
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from asgiref.sync import async_to_sync
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.utils import timezone
from PIL import Image

from apps.ledger.models import Service, Record
from . import manual, service, updates, wizard
from .bot import process_private
from .delivery import run_once, claim_delivery, finish_delivery
from .models import ShiftOffer, OfferImage, OfferWizard, RecognitionJob
from .presentation import render
from .storage import media_path, store_image
from .tests import Fixture, png


class ServicesAndUpdatesTests(Fixture, TestCase):
    def data(self, **kwargs):
        return dict(organization=self.org.pk, date=(timezone.localdate()+timedelta(days=2)).isoformat(),
                    service_name="Помощь на площадке", input_type="mark", **kwargs)

    def make(self, **kwargs):
        data = self.data(); data.update(kwargs)
        return manual.create(data, user_id=90002)

    def image_variant(self, offer, color="red"):
        output = io.BytesIO(); Image.new("RGB", (300, 600), color).save(output, format="PNG")
        path, digest = store_image(output.getvalue())
        return OfferImage.objects.create(offer=offer, path=path, sha256=digest, message_id=1)

    def drain(self):
        counter = iter(range(1000, 5000))
        async def send(**kwargs): return SimpleNamespace(message_id=next(counter))
        async def album(**kwargs): return [SimpleNamespace(message_id=next(counter)) for _ in kwargs["media"]]
        bot = SimpleNamespace(send_message=AsyncMock(side_effect=send), send_photo=AsyncMock(side_effect=send),
            send_media_group=AsyncMock(side_effect=album), delete_message=AsyncMock(), edit_message_text=AsyncMock(), edit_message_reply_markup=AsyncMock())
        for _ in range(150):
            if not async_to_sync(run_once)(bot): return bot
        self.fail("Queue did not settle")

    def pair(self, claimed=False):
        target = self.offer(state="open", topic_chat_id=-100123, topic_thread_id=77)
        self.image(target)
        if claimed: target = service.claim(target.pk, 90003, target.version)
        draft = self.offer()
        draft.intervals = [{"room":"БОХО", "start":"12:30", "end":"15:00"}]
        service.recompute(draft); draft.save()
        self.image_variant(draft)
        service.prompt(draft)
        self.assertEqual(draft.state, "update_review")
        return target, draft

    def test_formats_catalog_snapshot_and_no_accrual(self):
        count = Record.objects.count()
        for mode, fields in [("time", {"units":"2.5"}), ("time", {"start_time":"23:00", "end_time":"01:00"}),
                             ("quantity", {"units":"3"}), ("amount", {"amount":"0"}), ("mark", {})]:
            offer = self.make(input_type=mode, **fields)
            self.assertEqual(offer.kind, "service")
            self.assertGreater(service.end_at(offer), timezone.now())
        catalog = Service.objects.create(name="Монтаж", input_type="quantity")
        offer = self.make(service=catalog.pk, units="4")
        catalog.name="Новое имя"; catalog.input_type="amount"; catalog.save()
        offer.refresh_from_db()
        self.assertEqual((offer.service_name,offer.input_type,offer.units),("Монтаж","quantity",Decimal("4")))
        manual.edit(offer.pk, {"comment":"Комментарий"}, version=offer.version)
        self.assertEqual(Record.objects.count(), count)

    def test_permissions_past_and_invalid_values(self):
        with self.assertRaises(ValidationError): manual.create(self.data(),user_id=777)
        for mode, value in [("quantity","NaN"),("time","-1"),("quantity","1.234")]:
            with self.assertRaises(ValidationError): self.make(input_type=mode, units=value)
        with self.assertRaises(ValidationError): self.make(date=(timezone.localdate()-timedelta(days=1)).isoformat())

    def test_service_without_images_publishes_and_completes_at_midnight(self):
        offer=self.make(); service.publish(offer.pk); self.drain(); offer.refresh_from_db()
        self.assertEqual(offer.state,"open")
        self.assertFalse(offer.deliveries.filter(purpose__in=service.PHOTO_PURPOSES).exists())
        claimed=service.claim(offer.pk,90003,offer.version)
        other=self.make(); service.publish(other.pk); self.drain()
        boundary=service.end_at(claimed)
        with patch("apps.offers.service.timezone.now",return_value=boundary): service.expire_offers()
        claimed.refresh_from_db(); other.refresh_from_db()
        self.assertEqual((claimed.state,other.state),("completed","expired"))

    def test_service_day_and_timed_overlap_are_allowed(self):
        offer=self.make(); service.publish(offer.pk); self.drain(); offer.refresh_from_db()
        service.claim(offer.pk,90003,offer.version)
        shift=self.offer(state="open"); service.claim(shift.pk,90003,shift.version)
        timed=self.make(input_type="time",start_time="14:00",end_time="15:00")
        service.publish(timed.pk); self.drain(); timed.refresh_from_db()
        timed=service.claim(timed.pk,90003,timed.version)
        self.assertEqual((timed.state,timed.employee_id),("claimed",self.employee.pk))

    def test_manual_attachment_never_queues_recognition(self):
        offer=self.make()
        path,digest=store_image(png())
        manual.attach(offer.pk,path,digest,20,user_id=90002,version=offer.version)
        self.assertFalse(RecognitionJob.objects.filter(offer=offer).exists())
        service.publish(offer.pk); bot=self.drain()
        self.assertEqual(bot.send_photo.await_count,1)

    def test_semantic_duplicate_across_senders_and_aliases(self):
        target=self.offer(state="open"); self.image(target)
        self.profile.rooms=[{"name":"БОХО","aliases":["Бо хо…"]}]; self.profile.save()
        incoming=self.offer(); incoming.sender_id=90001
        incoming.intervals=[{**i,"room":"Бо хо…"} for i in target.intervals]; incoming.save()
        self.image_variant(incoming); service.prompt(incoming)
        self.assertEqual((incoming.state,incoming.duplicate_of_id),("duplicate",target.pk))
        self.assertFalse(target.deliveries.filter(purpose="update_notice").exists())

    def test_internal_changes_with_same_total_are_detected(self):
        target=self.offer(state="open")
        draft=self.offer(); draft.intervals[0]["end"]="15:30"; draft.save(); self.image(draft)
        service.prompt(draft)
        self.assertEqual(draft.state,"update_review")
        target.refresh_from_db(); self.assertEqual(target.intervals[0]["end"],"16:00")

    def test_uncertain_input_is_not_update_until_answered(self):
        target=self.offer(state="open")
        draft=self.offer(state="needs_input",questions=[{"key":"interval:0:end","label":"Конец?","type":"time"}])
        draft.intervals[0]["end"]=None; draft.save(); self.image(draft)
        service.prompt(draft); self.assertEqual(draft.state,"needs_input")
        draft=service.answer(draft.pk,{"interval:0:end":"15:00"},user_id=90002,version=draft.version)
        self.assertEqual(draft.state,"update_review")

    def test_merge_preserves_old_records_and_replacement_requires_confirmation(self):
        target,draft=self.pair()
        selected=updates.choose(draft.pk,target.pk,"merge",user_id=90002,version=draft.version)
        target.refresh_from_db(); self.assertEqual(target.start_time,time(13))
        self.assertEqual(len(updates.preview(selected)["intervals"]),3)
        updates.apply(selected.pk,user_id=90002,version=selected.version)
        target.refresh_from_db(); self.assertEqual((target.start_time,target.end_time),(time(12,30),time(20)))
        self.assertEqual(len(target.intervals),3)
        self.assertEqual(target.images.filter(active=True).count(),2)

    def test_claimed_update_keeps_worker_replaces_photos_and_notifies(self):
        target,draft=self.pair(claimed=True); self.drain()
        old=list(target.deliveries.filter(purpose__in=service.CLAIMED_PURPOSES))
        selected=updates.choose(draft.pk,target.pk,"replace",user_id=90002,version=draft.version)
        result=updates.apply(selected.pk,user_id=90002,version=selected.version)
        target.refresh_from_db(); self.assertEqual((target.state,target.assignee_user_id),("claimed",90003))
        self.assertEqual((target.start_time,target.end_time),(time(12,30),time(15)))
        self.assertEqual(target.images.filter(active=True).count(),1)
        self.assertEqual(set(target.deliveries.filter(purpose="update_notice").values_list("recipient",flat=True)),{-100123,90003})
        self.drain()
        for d in old: d.refresh_from_db(); self.assertIsNotNone(d.deleted_at)
        count=target.deliveries.count()
        with self.assertRaises(ValidationError): updates.apply(selected.pk,user_id=90002,version=selected.version)
        self.assertEqual(target.deliveries.count(),count)
        result.closed_at=timezone.now()-timedelta(days=31); result.save()
        service.purge_images()
        self.assertTrue(media_path(target.images.get(active=True).path).exists())

    def test_stale_update_is_blocked_but_new_overlap_is_allowed(self):
        target,draft=self.pair(claimed=True)
        selected=updates.choose(draft.pk,target.pk,"replace",user_id=90002,version=draft.version)
        service.changed(target,"edited","web")
        with self.assertRaises(ValidationError): updates.apply(draft.pk,user_id=90002,version=selected.version)
        draft.refresh_from_db(); self.assertTrue(updates.preview(draft)["stale"])
        selected=updates.choose(draft.pk,target.pk,"replace",user_id=90002,version=draft.version)
        conflict=self.offer(state="claimed",employee=self.employee,assignee_user_id=90003)
        conflict.start_time=time(12);conflict.end_time=time(13);conflict.save()
        result=updates.apply(draft.pk,user_id=90002,version=selected.version)
        target.refresh_from_db()
        self.assertEqual((target.start_time,target.end_time),(time(12,30),time(15)))
        self.assertTrue(target.deliveries.filter(purpose="update_notice").exists())
        self.assertEqual(result.state,"applied")

    def test_different_owner_escalates_to_chief(self):
        target,draft=self.pair(); target.sender_id=90001;target.save()
        selected=updates.choose(draft.pk,target.pk,"merge",user_id=90002,version=draft.version)
        with self.assertRaises(ValidationError): updates.apply(draft.pk,user_id=90002,version=selected.version)
        updates.escalate(draft.pk,user_id=90002,version=selected.version)
        self.assertTrue(draft.deliveries.filter(purpose="update_review",recipient=90001).exists())
        result=updates.apply(draft.pk,user_id=90001,version=selected.version)
        self.assertEqual(result.state,"applied")

    def test_multiple_candidates_require_selection_and_closed_are_ignored(self):
        first=self.offer(state="open"); second=self.offer(state="claimed")
        self.offer(state="cancelled")
        draft=self.offer(); self.image(draft);service.prompt(draft)
        self.assertIsNone(draft.update_target_id)
        self.assertEqual({o["id"] for o in updates.preview(draft)["candidates"]},{first.pk,second.pk})
        with self.assertRaises(ValidationError): updates.apply(draft.pk,user_id=90002,version=draft.version)

    def test_api_creation_upload_filters_sorting_and_idempotence(self):
        data=self.data();data["request_key"]="example"
        response=self.client.post("/api/shifts/ledger/offers/",data,content_type="application/json")
        self.assertEqual(response.status_code,201,response.content)
        result=response.json()
        retry=self.client.post("/api/shifts/ledger/offers/",data,content_type="application/json").json()
        self.assertEqual(result["id"],retry["id"])
        upload=self.client.post(f'/api/shifts/ledger/offers/{result["id"]}/image/',{"image":SimpleUploadedFile("image.png",png(),content_type="image/png"),"version":result["version"],"message_id":-1})
        self.assertEqual(upload.status_code,200,upload.content)
        self.assertEqual(self.client.get(upload.json()["images"][0]["url"]).status_code,200)
        cancelled=self.make(service_name="Отмена");service.cancel(cancelled.pk)
        self.make(service_name="Альфа")
        listing=self.client.get("/api/shifts/ledger/offers/",{"kind":"service","service":"free","ordering":"service_name"}).json()
        self.assertEqual(listing["count"],2);self.assertEqual(listing["items"][0]["service_name"],"Альфа")
        archive=self.client.get("/api/shifts/ledger/offers/",{"section":"cancelled"}).json()
        self.assertEqual([i["id"] for i in archive["items"]],[cancelled.pk])
        self.assertEqual(self.client.get("/api/shifts/ledger/offers/",{"search":"площадке"}).json()["count"],1)
        self.assertEqual(self.client.get("/api/shifts/ledger/offers/",{"ordering":"unknown"}).status_code,400)

    def test_bot_wizard_survives_restart_and_repeated_messages(self):
        values=["/offer","2","Уборка","2",str(self.org.pk),(timezone.localdate()+timedelta(days=2)).strftime("%d.%m.%Y"),"3","/skip","/done"]
        for number,value in enumerate(values,100):
            self.assertTrue(wizard.consume(90002,"Publisher",number,value))
            self.assertTrue(wizard.consume(90002,"Publisher",number,value))
        current=OfferWizard.objects.get(pk=90002)
        self.assertEqual(current.step,"done")
        self.assertEqual((current.offer.state,current.offer.units),("review",Decimal("3")))
        self.assertEqual(ShiftOffer.objects.filter(kind="service").count(),1)
        self.assertEqual(current.offer.deliveries.filter(purpose="review").count(),1)

    def test_private_offer_command_is_routed(self):
        message=SimpleNamespace(chat=SimpleNamespace(type="private"),from_user=SimpleNamespace(id=90002,full_name="Publisher"),
            message_id=80,text="/offer",reply=AsyncMock())
        self.assertTrue(async_to_sync(process_private)(message,SimpleNamespace()))
        self.assertEqual(OfferWizard.objects.get(pk=90002).step,"source")
        message.reply.assert_not_called()

    def test_catalog_wizard_all_formats_and_attachment_redelivery(self):
        for index,(mode,value) in enumerate((("time","10:00–12:30"),("quantity","2"),("amount","1500"),("mark",None))):
            catalog=Service.objects.create(name=f"Каталог {mode}",input_type=mode)
            values=["/offer","1",str(catalog.pk),str(self.org.pk),(timezone.localdate()+timedelta(days=2)).strftime("%d.%m.%Y")]
            if value: values.append(value)
            values.append("Описание")
            for number,text in enumerate(values,1000+index*100): wizard.consume(90002,"Publisher",number,text)
            current=OfferWizard.objects.get(pk=90002)
            offer=current.offer
            self.assertEqual((offer.service_id,offer.input_type),(catalog.pk,mode))
            photo_id=11000+index
            path,digest=store_image(png())
            manual.attach(offer.pk,path,digest,photo_id,user_id=90002)
            wizard.consume(90002,"Publisher",photo_id+100,"/done")
            count=ShiftOffer.objects.count()
            message=SimpleNamespace(chat=SimpleNamespace(type="private"),from_user=SimpleNamespace(id=90002,full_name="Publisher"),
                message_id=photo_id,text=None,photo=[SimpleNamespace(file_size=100,file_id="test")],reply=AsyncMock())
            self.assertTrue(async_to_sync(process_private)(message,SimpleNamespace()))
            self.assertEqual(ShiftOffer.objects.count(),count)
            self.assertFalse(offer.jobs.exists())

    def test_old_screenshot_after_update_is_a_reversion_not_hash_duplicate(self):
        from .recognition_worker import claim_job,finish_offer
        target,draft=self.pair()
        original=list(target.intervals)
        selected=updates.choose(draft.pk,target.pk,"replace",user_id=90002,version=draft.version)
        updates.apply(draft.pk,user_id=90002,version=selected.version)
        reverted=self.offer(state="collecting");image=self.image(reverted)
        RecognitionJob.objects.create(offer=reverted,revision=reverted.version,available_at=timezone.now())
        result={"date":str(target.date),"organization":self.org.pk,"intervals":original,"questions":[],"evidence":{}}
        finish_offer(claim_job(),[image],[{}],result,10)
        reverted.refresh_from_db()
        self.assertEqual((reverted.state,reverted.update_target_id),("update_review",target.pk))

    def test_late_recognition_finds_already_published_newer_offer(self):
        draft=self.offer(); self.image(draft)
        target=self.offer(state="open")
        service.prompt(draft)
        self.assertEqual((draft.state,draft.duplicate_of_id),("duplicate",target.pk))

    def test_update_bot_buttons_use_current_versions(self):
        from .bot import callback_action
        target,draft=self.pair(); self.drain()
        delivery=draft.deliveries.get(purpose="update_review")
        text,markup=render(delivery)
        self.assertIn("Прежние записи",text)
        choose_button=markup.inline_keyboard[0][0]
        callback_action(choose_button.callback_data,90002,90002,delivery.message_ids[0])
        self.drain();draft.refresh_from_db()
        current=draft.deliveries.filter(purpose="update_review",version=draft.version).first()
        _,markup=render(current)
        apply_button=next(b for row in markup.inline_keyboard for b in row if b.text=="Подтвердить обновление")
        callback_action(apply_button.callback_data,90002,90002,current.message_ids[0])
        with self.assertRaises(ValidationError): callback_action(apply_button.callback_data,90002,90002,current.message_ids[0])
