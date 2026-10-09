import json
from datetime import time, timedelta
from unittest.mock import patch
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.utils import timezone
from apps.shifts.models import Organization
from apps.ledger.models import Record
from .test_services_updates import ServicesAndUpdatesTests
from .tests import Fixture, png
from .models import RecognitionProfile, ShiftOffer, RecognitionJob, OfferPart
from . import manual, service, updates
from .recognizer import merge_results
from .recognition_worker import claim_job, finish_offer
from .presentation import describe
from .storage import store_image, media_path


class OfferPartsTests(Fixture, TestCase):
    drain = ServicesAndUpdatesTests.drain

    def setUp(self):
        super().setUp()
        self.second = Organization.objects.create(name="Вторая студия")
        self.second_profile = RecognitionProfile.objects.create(organization=self.second, confirmed=True,
            rooms=[{"name":"Портретный","aliases":[]}])
        self.second_profile.publishers.add(self.contact)
        self.day=(timezone.localdate()+timedelta(days=2)).isoformat()

    def bundle(self, **kwargs):
        return manual.create({"kind":"shift","date":self.day,"parts":[
            {"organization":self.org.pk,"start_time":"10:00","end_time":"18:00"},
            {"organization":self.second.pk,"start_time":"16:00","end_time":"20:00"}],**kwargs},user_id=90002)

    def results(self):
        return [{"date":self.day,"date_ambiguous":False,"headers":[room],"intervals":[
            {"room":room,"start":start,"end":end}],"issues":[]} for room,start,end in
            [("БОХО","10:00","18:00"),("Портретный","16:00","20:00")]]

    def profiles(self):
        return [{"organization":p.organization_id,"rooms":p.rooms} for p in RecognitionProfile.objects.all()]

    def test_manual_create_images_claim_release_routes_and_no_finance(self):
        offer=self.bundle()
        for n,part in enumerate(offer.parts.all()):
            path,digest=store_image(png())
            offer=manual.attach(offer.pk,path,digest,n+1,part_id=part.pk,version=offer.version,user_id=90002)
        service.publish(offer.pk,version=offer.version)
        self.drain();offer.refresh_from_db()
        self.assertEqual(offer.state,"open")
        offer=service.claim(offer.pk,90003,offer.version)
        self.assertEqual(offer.parts.count(),2)
        self.assertIn(self.second.name,describe(offer))
        self.assertEqual(service.end_at(offer).hour,20)
        # One source photo may be identical across studios: both links remain.
        self.assertEqual(offer.images.count(),2)
        self.drain()
        self.assertTrue(offer.deliveries.filter(purpose="claimed_topic",thread_id=78,state="sent").exists())
        self.assertTrue(offer.deliveries.filter(purpose="assignment_photos",recipient=90003,state="sent").exists())
        offer=service.release(offer.pk,user_id=90003,version=offer.version)
        self.drain()
        self.assertEqual((offer.state,offer.employee_id),("open",None))
        self.assertTrue(offer.deliveries.filter(purpose="release_notice",thread_id=79,state="sent").exists())
        self.assertFalse(Record.objects.exists())
        with self.assertRaises(ValidationError):service.claim(offer.pk,90003,offer.version-1)

    def test_both_permissions_and_shapes_required(self):
        self.second_profile.publishers.clear()
        with self.assertRaises(ValidationError):self.bundle()
        self.assertFalse(ShiftOffer.objects.exists())
        self.second_profile.publishers.add(self.contact)
        with self.assertRaises(ValidationError):self.bundle(parts=[{"organization":self.org.pk}]*2)
        with self.assertRaises(ValidationError):self.bundle(parts=[{"organization":self.org.pk}]*3)
        with self.assertRaises(ValidationError):self.bundle(parts=[{"organization":self.org.pk,"start_time":"18:00","end_time":"10:00"}])

    def test_album_two_studios_same_date_groups_and_deduplicates(self):
        results=self.results();merged=merge_results([*results,results[0]],self.profiles())
        self.assertEqual(len(merged["parts"]),2)
        self.assertEqual(len(merged["intervals"]),2)
        self.assertEqual(merged["questions"],[])
        offer=None
        for n in range(3):
            path,digest=store_image(png())
            offer,_=service.receive_image(90002,"Отправитель","album:test",n+1,path,digest)
        RecognitionJob.objects.filter(offer=offer,state="pending").update(available_at=timezone.now())
        job=claim_job();images=list(offer.images.all());finish_offer(job,images,[*results,results[0]],merged,10)
        offer.refresh_from_db()
        self.assertEqual((offer.state,offer.parts.count(),offer.end_time),("review",2,time(20)))
        self.assertEqual(set(offer.images.values_list("part__organization_id",flat=True)),{self.org.pk,self.second.pk})
        service.publish(offer.pk,version=offer.version);self.drain()

    def test_same_rooms_do_not_merge_different_studios(self):
        # Org headers differ, but a common room exists in both profiles.
        results=self.results()
        for r in results:r["intervals"]=[{"room":"Общий зал","start":"12:00","end":"15:00"}]
        merged=merge_results(results,self.profiles())
        self.assertEqual(len(merged["intervals"]),2)

    def test_unknown_org_and_cropped_time_are_scoped_to_image(self):
        results=self.results();results[1]["headers"]=["Неизвестный зал"];results[1]["intervals"][0]["end"]=None
        merged=merge_results(results,self.profiles())
        self.assertIn("image:1:organization",[q["key"] for q in merged["questions"]])
        offer=self.offer(state="processing")
        offer.organization=None;offer.save()
        self.image(offer,1);self.image(offer,2)
        job=RecognitionJob.objects.create(offer=offer,revision=offer.version,available_at=timezone.now(),state="running",attempts=1)
        finish_offer(job,list(offer.images.all()),results,merged,20)
        offer.refresh_from_db()
        offer=service.answer(offer.pk,{"image:1:organization":self.second.pk},version=offer.version,user_id=90002)
        key=next(q["key"] for q in offer.questions if q["type"]=="time")
        offer=service.answer(offer.pk,{key:"20:00"},version=offer.version,user_id=90002)
        self.assertEqual((offer.state,offer.parts.count(),offer.end_time),("review",2,time(20)))
        self.assertEqual(offer.parts.get(organization=self.org).end_time,time(18))

    def test_more_than_two_studios_or_dates_requires_split(self):
        results=self.results();third=Organization.objects.create(name="Третья")
        profiles=self.profiles()+[{"organization":third.pk,"rooms":[{"name":"Третий","aliases":[]}]}]
        self.assertIn("split",[q["key"] for q in merge_results(results+[{**results[0],"headers":["Третий"]}],profiles)["questions"]])
        results[1]["date"]=(timezone.localdate()+timedelta(days=3)).isoformat()
        self.assertIn("split",[q["key"] for q in merge_results(results,profiles)["questions"]])

    def test_filter_second_organization_returns_parent_once(self):
        offer=self.bundle()
        data=self.client.get(f"/api/shifts/ledger/offers/?organization={self.second.pk}").json()
        self.assertEqual((data["count"],data["items"][0]["id"]),(1,offer.pk))
        data=self.client.get(f"/api/shifts/ledger/offers/?organization={self.org.pk}").json()
        self.assertEqual(data["count"],1)

    def test_update_only_one_part_keeps_second_and_assignment(self):
        target=self.bundle(parts=[{"organization":self.org.pk,"intervals":self.results()[0]["intervals"]},
                                 {"organization":self.second.pk,"intervals":self.results()[1]["intervals"]}])
        for n,part in enumerate(target.parts.all()):
            path,digest=store_image(png());target=manual.attach(target.pk,path,digest,n+1,part_id=part.pk,version=target.version)
        service.publish(target.pk);self.drain();target.refresh_from_db();target=service.claim(target.pk,90003,target.version)
        second=target.parts.get(organization=self.second);photo=second.images.get();before_intervals=second.intervals
        draft=self.offer();draft.start_time=time(11);draft.end_time=time(17)
        draft.intervals=[{"room":"БОХО","start":"11:00","end":"17:00"}];draft.save();self.image(draft)
        service.prompt(draft)
        self.assertEqual(draft.update_target_id,target.pk)
        updates.choose(draft.pk,target.pk,"replace",version=draft.version)
        draft.refresh_from_db();updates.apply(draft.pk,version=draft.version)
        target.refresh_from_db();second.refresh_from_db();photo.refresh_from_db()
        self.assertEqual((target.employee_id,target.parts.count(),target.end_time),(self.employee.pk,2,time(20)))
        self.assertEqual(second.intervals,before_intervals);self.assertTrue(photo.active);self.assertTrue(media_path(photo.path).is_file())
        self.assertEqual(target.parts.get(organization=self.org).start_time,time(11))
        self.assertTrue(target.deliveries.filter(purpose="update_notice",recipient=90003).exists())

    def test_complete_at_latest_end_and_legacy_create(self):
        offer=self.bundle();service.publish(offer.pk);self.drain();offer.refresh_from_db();offer=service.claim(offer.pk,90003,offer.version)
        with patch("apps.offers.service.timezone.now",return_value=service.end_at(offer)-timedelta(hours=1)):service.expire_offers()
        offer.refresh_from_db();self.assertEqual(offer.state,"claimed")
        with patch("apps.offers.service.timezone.now",return_value=service.end_at(offer)):service.expire_offers()
        offer.refresh_from_db();self.assertEqual(offer.state,"completed")
        single=manual.create({"kind":"shift","organization":self.org.pk,"date":self.day,"start_time":"10:00","end_time":"18:00"})
        self.assertEqual(single.parts.count(),1)

    def test_partial_site_edit_preserves_other_studio_and_photos(self):
        offer=self.bundle();offer.state="open";offer.save()
        first,second=list(offer.parts.order_by("id"))
        photo=self.image(offer);photo.part=second;photo.save()
        edited=service.edit_published(offer.pk,{"parts":[{"id":first.pk,"organization":self.org.pk,
                                                       "start_time":"11:00","end_time":"17:00"}]},offer.version)
        self.assertEqual((edited.parts.count(),edited.end_time),(2,time(20)))
        photo.refresh_from_db();self.assertEqual(photo.part_id,second.pk)
        self.assertTrue(edited.deliveries.filter(purpose="topic").exists())

    def test_part_range_cannot_bypass_unresolved_booking(self):
        offer=self.bundle();offer.state="needs_input";offer.questions=[{"key":"interval:0:end","type":"time","label":"Окончание"}];offer.save()
        with self.assertRaises(ValidationError):
            service.answer(offer.pk,{"parts":[{"organization":self.org.pk,"start_time":"10:00","end_time":"18:00"},
                {"organization":self.second.pk,"start_time":"16:00","end_time":"20:00"}]},version=offer.version)
        self.assertTrue(ShiftOffer.objects.get(pk=offer.pk).questions)
