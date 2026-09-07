import time
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from connections.models import Connection
from integrations.models import Integration, InstalledIntegration
from jobs.models import MigrationLog, MigrationRun
from mappings.models import EntityMapping, FieldMapping, Mapping
from schemas.models import Entity, Field


class Command(BaseCommand):
    help = (
        "Seeds two App Store integrations (installed as connections), a demo "
        "mapping between them, and a handful of historical + active migration "
        "runs so the App Store and Runs pages aren't empty. Idempotent — safe "
        "to run more than once."
    )

    def handle(self, *args, **options):
        shopify_conn, crm_conn, erp_conn = self._seed_integrations()
        mapping1 = self._seed_mapping(
            name="Shopify customers -> Internal CRM",
            description="Demo: sync Shopify customers into the internal CRM as contacts.",
            source=shopify_conn, destination=crm_conn,
            source_entity_name="Customer", source_endpoint="/customers",
            source_fields=[("email", Field.TYPE_STRING), ("first_name", Field.TYPE_STRING)],
            target_entity_name="Contact", target_endpoint="/contacts",
            target_fields=[("email", Field.TYPE_STRING), ("full_name", Field.TYPE_STRING)],
            field_pairs=[("email", "email"), ("first_name", "full_name")],
        )
        mapping2 = self._seed_mapping(
            name="Acme ERP products -> Internal CRM",
            description="Demo: sync Acme ERP product catalog into the internal CRM as items.",
            source=erp_conn, destination=crm_conn,
            source_entity_name="Product", source_endpoint="/products",
            source_fields=[("sku", Field.TYPE_STRING), ("name", Field.TYPE_STRING)],
            target_entity_name="Item", target_endpoint="/items",
            target_fields=[("sku", Field.TYPE_STRING), ("name", Field.TYPE_STRING)],
            field_pairs=[("sku", "sku"), ("name", "name")],
        )

        self._seed_runs(mapping1, mapping2)

        self.stdout.write(self.style.SUCCESS(
            "Seeded: 2 integrations, 3 connections, 2 mappings, "
            f"{MigrationRun.objects.count()} runs."
        ))

    def _seed_integrations(self):
        shopify, _ = Integration.objects.get_or_create(
            slug="shopify",
            defaults=dict(
                name="Shopify", description="E-commerce storefront and customer data.",
                category=Integration.Category.ECOMMERCE, icon="bi-shop",
                auth_type=Connection.AUTH_OAUTH2,
                default_base_url="https://shopify-demo.example.com/admin/api/2024-01",
                oauth_authorize_url="https://shopify-demo.example.com/oauth/authorize",
                oauth_token_url="https://shopify-demo.example.com/oauth/token",
                oauth_client_id="demo-client-id", oauth_client_secret="demo-client-secret",
                oauth_scope="read_products,read_customers", is_featured=True,
            ),
        )
        erp, _ = Integration.objects.get_or_create(
            slug="acme-erp",
            defaults=dict(
                name="Acme ERP", description="Internal product/inventory system of record.",
                category=Integration.Category.ERP, icon="bi-building",
                auth_type=Connection.AUTH_BEARER,
                default_base_url="https://acme-erp-demo.example.com/api/v1",
            ),
        )

        shopify_conn, created = Connection.objects.get_or_create(
            name="Shopify (demo store)",
            defaults=dict(base_url=shopify.default_base_url, auth_type=Connection.AUTH_OAUTH2),
        )
        if created:
            shopify_conn.auth_config = {
                "client_id": shopify.oauth_client_id, "client_secret": shopify.oauth_client_secret,
                "authorize_url": shopify.oauth_authorize_url, "token_url": shopify.oauth_token_url,
                "scope": shopify.oauth_scope,
            }
            shopify_conn.secrets = {
                "access_token": "demo-access-token", "refresh_token": "demo-refresh-token",
                "expires_at": time.time() + 3600,
            }
            shopify_conn.save()
            InstalledIntegration.objects.get_or_create(integration=shopify, connection=shopify_conn)

        erp_conn, created = Connection.objects.get_or_create(
            name="Acme ERP (demo)",
            defaults=dict(base_url=erp.default_base_url, auth_type=Connection.AUTH_BEARER),
        )
        if created:
            erp_conn.merge_secrets({"token": "demo-bearer-token"})
            erp_conn.save()
            InstalledIntegration.objects.get_or_create(integration=erp, connection=erp_conn)

        crm_conn, created = Connection.objects.get_or_create(
            name="Internal CRM",
            defaults=dict(base_url="https://internal-crm.example.com/api", auth_type=Connection.AUTH_BASIC),
        )
        if created:
            crm_conn.merge_secrets({"username": "demo-user", "password": "demo-password"})
            crm_conn.save()

        return shopify_conn, crm_conn, erp_conn

    def _seed_mapping(
        self, *, name, description, source, destination,
        source_entity_name, source_endpoint, source_fields,
        target_entity_name, target_endpoint, target_fields, field_pairs,
    ):
        source_entity, _ = Entity.objects.get_or_create(
            connection=source, name=source_entity_name,
            defaults=dict(endpoint_path=source_endpoint, source=Entity.SOURCE_MANUAL),
        )
        for field_name, field_type in source_fields:
            Field.objects.get_or_create(entity=source_entity, name=field_name, defaults=dict(field_type=field_type))

        target_entity, _ = Entity.objects.get_or_create(
            connection=destination, name=target_entity_name,
            defaults=dict(endpoint_path=target_endpoint, source=Entity.SOURCE_MANUAL),
        )
        for field_name, field_type in target_fields:
            Field.objects.get_or_create(entity=target_entity, name=field_name, defaults=dict(field_type=field_type))

        mapping, _ = Mapping.objects.get_or_create(
            name=name, defaults=dict(source_connection=source, description=description),
        )
        mapping.destination_connections.add(destination)

        entity_mapping, _ = EntityMapping.objects.get_or_create(
            mapping=mapping, source_entity=source_entity, target_entity=target_entity,
        )
        for source_field_name, target_field_name in field_pairs:
            FieldMapping.objects.get_or_create(
                entity_mapping=entity_mapping,
                source_field=Field.objects.get(entity=source_entity, name=source_field_name),
                target_field=Field.objects.get(entity=target_entity, name=target_field_name),
            )

        return mapping

    def _seed_runs(self, mapping1, mapping2):
        if MigrationRun.objects.filter(mapping__in=[mapping1, mapping2]).exists():
            self.stdout.write("Runs already seeded for these mappings — skipping.")
            return

        now = timezone.now()
        specs = [
            # (mapping, status, read, written, failed, requests, started_ago, finished_ago, logs)
            (mapping1, MigrationRun.STATUS_SUCCESS, 120, 118, 2, 125, timedelta(days=2), timedelta(days=2, hours=-1), [
                (MigrationLog.LEVEL_INFO, "Reading Customer ..."),
                (MigrationLog.LEVEL_INFO, "Fetched 120 record(s)."),
                (MigrationLog.LEVEL_WARNING, "2 record(s) missing email — skipped on write."),
                (MigrationLog.LEVEL_INFO, "Migration finished: 118 written, 2 failed."),
            ]),
            (mapping1, MigrationRun.STATUS_SUCCESS, 86, 86, 0, 89, timedelta(days=1), timedelta(days=1, hours=-1), [
                (MigrationLog.LEVEL_INFO, "Reading Customer ..."),
                (MigrationLog.LEVEL_INFO, "Fetched 86 record(s)."),
                (MigrationLog.LEVEL_INFO, "Migration finished: 86 written, 0 failed."),
            ]),
            (mapping2, MigrationRun.STATUS_FAILED, 10, 3, 7, 11, timedelta(hours=6), timedelta(hours=6, minutes=-2), [
                (MigrationLog.LEVEL_INFO, "Reading Product ..."),
                (MigrationLog.LEVEL_INFO, "Fetched 10 record(s)."),
                (MigrationLog.LEVEL_ERROR, "Failed to write record to Internal CRM: 502 Bad Gateway"),
                (MigrationLog.LEVEL_ERROR, "Migration aborted: too many consecutive write failures."),
            ]),
            (mapping2, MigrationRun.STATUS_SUCCESS, 64, 64, 0, 66, timedelta(hours=1), timedelta(minutes=58), [
                (MigrationLog.LEVEL_INFO, "Reading Product ..."),
                (MigrationLog.LEVEL_INFO, "Fetched 64 record(s)."),
                (MigrationLog.LEVEL_INFO, "Migration finished: 64 written, 0 failed."),
            ]),
            (mapping1, MigrationRun.STATUS_RUNNING, 40, 25, 0, 41, timedelta(minutes=3), None, [
                (MigrationLog.LEVEL_INFO, "Reading Customer ..."),
                (MigrationLog.LEVEL_INFO, "Fetched 40 record(s)."),
                (MigrationLog.LEVEL_INFO, "Writing 40 record(s) to Internal CRM ..."),
            ]),
            (mapping2, MigrationRun.STATUS_PENDING, 0, 0, 0, 0, timedelta(seconds=20), None, [
                (MigrationLog.LEVEL_INFO, "Run queued."),
            ]),
        ]

        for mapping, status, read, written, failed, requests, started_ago, finished_ago, logs in specs:
            started_at = now - started_ago
            finished_at = (now - finished_ago) if finished_ago is not None else None
            run = MigrationRun.objects.create(
                mapping=mapping, status=status, records_read=read, records_written=written,
                records_failed=failed, requests_made=requests, finished_at=finished_at,
            )
            MigrationRun.objects.filter(pk=run.pk).update(started_at=started_at)
            for offset, (level, message) in enumerate(logs):
                log = MigrationLog.objects.create(run=run, level=level, message=message)
                MigrationLog.objects.filter(pk=log.pk).update(created_at=started_at + timedelta(seconds=offset * 5))

        self.stdout.write(
            "  Note: the RUNNING/PENDING demo runs are static seed data (no "
            "background thread drives them) — they won't self-resolve; "
            "that's expected, it's just something to look at in the Active "
            "runs view."
        )
