from datetime import date

from django.test import TestCase

from apps.shifts.models import Employee, Organization
from .billing import approve, delete_batch, delete_invoice, prepare
from .models import Batch, Delivery, Invoice, OrganizationBilling, Record, Settings, TelegramContact
from .worker import claim


class InvoiceReplacementDeletionTests(TestCase):
    def setUp(self):
        self.organizations = list(Organization.objects.filter(is_active=True).order_by("id")[:2])
        self.organization_ids = [organization.pk for organization in self.organizations]
        self.day = date(2026, 4, 12)
        self.recipient = TelegramContact.objects.create(user_id=2301, name="Получатель")
        Settings.objects.filter(pk=1).update(approver=None)
        for organization in self.organizations:
            profile = OrganizationBilling.objects.get(organization=organization)
            profile.recipients.set([self.recipient])
            profile.payment_recipients.set([self.recipient])
            Record.objects.create(kind="oneoff", organization=organization, employee=Employee.objects.first(),
                                  date=self.day, amount=100, description="Работа")

    def versions(self):
        first = prepare(self.day, self.day, self.organization_ids)
        approve(first.pk, first.version)
        # Failed attempts survive replacement and must never become eligible for retry.
        Delivery.objects.filter(batch=first, purpose="owner").update(state="failed")
        middle = prepare(self.day, self.day, self.organization_ids, replaces=first.pk)
        approve(middle.pk, middle.version)
        newest = prepare(self.day, self.day, self.organization_ids, replaces=middle.pk)
        approve(newest.pk, newest.version)
        return first, middle, newest

    def assert_old_delivery_rejected_and_next_version_approved(self, first, newest):
        delivery = Delivery.objects.get(batch=first, invoice__organization=self.organizations[0], purpose="owner")
        response = self.client.post(f"/api/shifts/ledger/deliveries/{delivery.pk}/retry/", "{}", content_type="application/json")
        self.assertEqual(response.status_code, 400, response.content)
        delivery.refresh_from_db()
        self.assertEqual(delivery.state, "failed")
        # The worker must also reject a stale pending item from an old retry request.
        Delivery.objects.filter(pk=delivery.pk).update(state="pending")
        claimed = claim()
        self.assertEqual(claimed.batch_id, newest.pk)
        delivery.refresh_from_db()
        self.assertEqual(delivery.state, "cancelled")
        next_batch = prepare(self.day, self.day, self.organization_ids, replaces=newest.pk)
        self.assertTrue(approve(next_batch.pk, next_batch.version)["approved"])
        self.assertEqual(Invoice.objects.filter(payment_state="open").count(), 2)

    def test_delete_middle_batch_preserves_each_organization_chain(self):
        first, middle, newest = self.versions()
        middle_id = middle.pk
        delete_batch(middle_id)
        self.assertFalse(Batch.objects.filter(pk=middle_id).exists())
        for organization in self.organizations:
            original = first.invoices.get(organization=organization)
            current = newest.invoices.get(organization=organization)
            self.assertEqual(current.replaces_id, original.pk)
        self.assert_old_delivery_rejected_and_next_version_approved(first, newest)

    def test_delete_middle_invoice_preserves_its_chain_and_other_organization(self):
        first, middle, newest = self.versions()
        removed = middle.invoices.get(organization=self.organizations[0])
        retained = middle.invoices.get(organization=self.organizations[1])
        delete_invoice(removed.pk)
        middle.refresh_from_db()
        self.assertEqual(middle.organization_ids, [self.organizations[1].pk])
        self.assertTrue(Invoice.objects.filter(pk=retained.pk).exists())
        self.assertEqual(newest.invoices.get(organization=self.organizations[0]).replaces_id,
                         first.invoices.get(organization=self.organizations[0]).pk)
        self.assertEqual(newest.invoices.get(organization=self.organizations[1]).replaces_id, retained.pk)
        self.assert_old_delivery_rejected_and_next_version_approved(first, newest)
