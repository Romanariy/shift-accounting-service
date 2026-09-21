import json
from datetime import date, time, timedelta
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest import skipUnless
from django.core.exceptions import ValidationError
from django.db import connection, close_old_connections
from django.test import TestCase, TransactionTestCase, Client
from django.utils import timezone
from apps.shifts.models import Employee, Organization, AuditLog
from apps.offers.models import OfferConfig, ShiftOffer
from apps.offers import service
from .models import Settings, TelegramContact, Batch, Invoice, Delivery


class WorkspaceTests(TestCase):
    def setUp(self):
        self.chief=TelegramContact.objects.create(user_id=76001,name="Chief")
        self.person=TelegramContact.objects.create(user_id=76002,name="Bot name",username="worker")
        self.config=Settings.objects.get(pk=1)
        self.config.approver=self.chief; self.config.service_chat=-1001; self.config.service_thread=10
        self.config.expense_chat=-1001; self.config.expense_thread=20; self.config.save()
        self.offers=OfferConfig.objects.create(pk=1,enabled=True,chat_id=-1001,thread_id=30,claimed_thread_id=40,released_thread_id=50)
        self.org=Organization.objects.first()
        self.employee=Employee.objects.create(short_name="Site name")

    def put(self,path,data):
        return self.client.put(path,json.dumps(data),content_type="application/json")

    def invoice(self,state="draft",payment="none",day=date(2025,4,1)):
        batch=Batch.objects.create(start=day,end=day,state=state)
        return Invoice.objects.create(batch=batch,organization=self.org,organization_name=self.org.name,
            payment_state=payment,total=1200,recipients=[76002],artifact=b"private",lines=[{"private":"large"}])

    def test_invoice_list_all_states_global_debt_and_pagination(self):
        old=self.invoice("approved","open")
        draft=self.invoice(); paid=self.invoice("approved","paid"); replaced=self.invoice("approved","superseded")
        Delivery.objects.create(invoice=old,batch=old.batch,purpose="owner",recipient=76002,version=1,key="listing",state="unknown")
        response=self.client.get("/api/shifts/ledger/invoices/?limit=2")
        data=response.json(); self.assertEqual(data["count"],4); self.assertEqual(data["open_count"],1)
        self.assertEqual([i["id"] for i in data["invoices"]],[replaced.pk,paid.pk])
        data=self.client.get("/api/shifts/ledger/invoices/?offset=2&limit=2").json()
        self.assertEqual([i["id"] for i in data["invoices"]],[draft.pk,old.pk])
        self.assertEqual(data["invoices"][1]["delivery_counts"],{"unknown":1})
        self.assertNotIn("artifact",data["invoices"][1]); self.assertNotIn("lines",data["invoices"][1])
        data=self.client.get("/api/shifts/ledger/invoices/?payment_state=none").json()
        self.assertEqual(data["count"],1); self.assertEqual(data["open_count"],1)

    def test_invoice_filters_overlap_dates_and_updated_payment(self):
        invoice=self.invoice("approved","open")
        self.invoice(day=date(2026,9,1))
        url=f"/api/shifts/ledger/invoices/?organization={self.org.pk}&start=2025-04-01&end=2025-04-02&state=approved&payment_state=open"
        self.assertEqual(self.client.get(url).json()["count"],1)
        Invoice.objects.filter(pk=invoice.pk).update(payment_state="paid",paid_by=self.person.user_id,paid_at=timezone.now())
        data=self.client.get(url).json(); self.assertEqual((data["count"],data["open_count"]),(0,0))
        data=self.client.get("/api/shifts/ledger/invoices/?payment_state=paid").json()
        self.assertEqual(data["invoices"][0]["paid_by_name"],self.person.name)
        for query in ("state=invalid","payment_state=invalid","start=2026-09-01&end=2025-01-01"):
            self.assertEqual(self.client.get("/api/shifts/ledger/invoices/?"+query).status_code,400)

    def test_joint_settings_are_atomic_and_allow_swapping_routes(self):
        url="/api/shifts/ledger/settings/"
        response=self.put(url,{"hour":12,"offer_config":{"chat_id":-1001,"thread_id":10,"enabled":True}})
        self.assertEqual(response.status_code,400)
        self.config.refresh_from_db(); self.offers.refresh_from_db()
        self.assertEqual(self.config.hour,10); self.assertEqual(self.offers.thread_id,30)
        response=self.put(url,{"hour":12,"service_thread":30,"offer_config":{"thread_id":10}})
        self.assertEqual(response.status_code,200,response.content)
        bootstrap=self.client.get("/api/shifts/ledger/bootstrap/").json()
        self.assertEqual(bootstrap["offer_config"]["thread_id"],10)
        self.assertEqual(bootstrap["settings"]["service_thread"],30)

    def test_route_conflicts_checked_from_both_apis_including_whole_group(self):
        for fields in ({"service_thread":30},{"expense_thread":30},{"service_thread":None},{"approver":None}):
            self.assertEqual(self.put("/api/shifts/ledger/settings/",fields).status_code,400)
        response=self.client.post("/api/shifts/ledger/offer-config/",json.dumps({"thread_id":20}),content_type="application/json")
        self.assertEqual(response.status_code,400)
        self.offers.refresh_from_db(); self.assertEqual(self.offers.thread_id,30)

    def bind(self,employee=None,**extra):
        employee=employee or self.employee
        return self.put(f"/api/shifts/employees/{employee.pk}/",{"telegramUserId":self.person.user_id,**extra})

    def test_select_registered_contact_preserves_employee_name_and_can_unlink(self):
        response=self.bind(); self.assertEqual(response.status_code,200,response.content)
        self.employee.refresh_from_db(); self.assertEqual(self.employee.short_name,"Site name")
        self.assertEqual(self.employee.telegram_username,"worker")
        entry=next(e for e in self.client.get("/api/shifts/ledger/bootstrap/").json()["employees"] if e["id"]==self.employee.pk)
        self.assertEqual(entry["telegram_contact"]["name"],"Bot name")
        self.assertEqual(self.bind(telegramUserId="").json()["telegramUserId"],None)

    def test_unknown_existing_id_preserved_but_new_unknown_id_rejected(self):
        Employee.objects.filter(pk=self.employee.pk).update(telegram_user_id=76099)
        response=self.put(f"/api/shifts/employees/{self.employee.pk}/",{"fullName":"Full site name"})
        self.assertEqual(response.status_code,200,response.content)
        self.assertEqual(response.json()["telegramUserId"],76099)
        self.assertEqual(self.bind(telegramUserId=76100).status_code,400)

    def test_duplicate_activation_and_existing_conflicts(self):
        self.bind()
        other=Employee.objects.create(short_name="Other",telegram_user_id=self.person.user_id,is_active=False)
        self.assertEqual(self.bind(other,isActive=True).status_code,400)
        Employee.objects.filter(pk=other.pk).update(is_active=True)
        people=self.client.get("/api/shifts/ledger/bootstrap/").json()["employees"]
        self.assertTrue(all(e["telegram_conflict"] for e in people if e["id"] in (other.pk,self.employee.pk)))
        # Editing a name must not silently repair or reassign legacy duplicate IDs.
        self.assertEqual(self.put(f"/api/shifts/employees/{self.employee.pk}/",{"fullName":"Changed"}).status_code,200)

    def test_claim_release_identity_and_binding_change_guard(self):
        self.bind()
        offer=ShiftOffer.objects.create(source_key="workspace-flow",sender_id=self.chief.user_id,organization=self.org,
            date=timezone.localdate()+timedelta(days=1),start_time=time(13),end_time=time(20),state="open")
        assigned=service.claim(offer.pk,self.person.user_id)
        for new_id in (None,self.chief.user_id):
            self.assertEqual(self.bind(telegramUserId=new_id).status_code,400)
        with self.assertRaises(ValidationError): service.release(offer.pk,user_id=999)
        with self.assertRaises(ValidationError): service.cancel(offer.pk,user_id=self.person.user_id)
        service.release(offer.pk,user_id=self.person.user_id,version=assigned.version)
        self.assertEqual(self.bind(telegramUserId=None).status_code,200)


@skipUnless(connection.vendor=="postgresql","PostgreSQL row locks required")
class TeamBindingConcurrencyTests(TransactionTestCase):
    def test_only_one_active_employee_can_acquire_contact(self):
        Settings.objects.get_or_create(pk=1)
        contact=TelegramContact.objects.create(user_id=76501,name="Concurrency")
        employees=[Employee.objects.create(short_name=f"Concurrent binding {n}") for n in range(2)]
        barrier=Barrier(2)
        def bind(pk):
            close_old_connections()
            try:
                barrier.wait(timeout=10)
                return Client().put(f"/api/shifts/employees/{pk}/",json.dumps({"telegramUserId":contact.user_id}),content_type="application/json").status_code
            finally: close_old_connections()
        with ThreadPoolExecutor(max_workers=2) as pool:
            results=list(pool.map(bind,[e.pk for e in employees]))
        self.assertEqual(sorted(results),[200,400])
        self.assertEqual(Employee.objects.filter(is_active=True,telegram_user_id=contact.user_id).count(),1)
