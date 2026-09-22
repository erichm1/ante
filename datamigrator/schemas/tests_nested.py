from unittest import mock

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase

from connections.models import Connection
from jobs.engine import build_payload
from mappings.models import EntityMapping, FieldMapping, Mapping
from studio.templates import auto_map

from . import discovery
from .models import Entity, Field
from .paths import get_path, leaf_fields, set_path

RECORD = {
    "id": 7,
    "name": "Ada",
    "address": {"city": "London", "geo": {"lat": 51.5, "lng": -0.12}},
    "tags": ["a", "b"],
    "orders": [{"sku": "X1", "qty": 2}, {"sku": "X2", "qty": 1}],
}


class PathTests(SimpleTestCase):
    def test_get_walks_objects_and_lists(self):
        self.assertEqual(get_path(RECORD, "address.geo.lat"), 51.5)
        self.assertEqual(get_path(RECORD, "orders.1.sku"), "X2")
        self.assertIsNone(get_path(RECORD, "address.zip"))
        self.assertIsNone(get_path(RECORD, "orders.9.sku"))

    def test_a_flat_key_with_dots_wins(self):
        self.assertEqual(get_path({"a.b": 1, "a": {"b": 2}}, "a.b"), 1)

    def test_set_builds_objects_and_lists(self):
        out = {}
        set_path(out, "customer.address.city", "Rome")
        set_path(out, "lines.1.sku", "Z")
        set_path(out, "plain", 5)
        self.assertEqual(out, {"customer": {"address": {"city": "Rome"}}, "lines": [None, {"sku": "Z"}], "plain": 5})

    def test_writing_inside_a_copied_object_never_touches_the_source(self):
        source = {"address": {"city": "London"}}
        out = {}
        set_path(out, "address", source["address"])
        set_path(out, "address.city", "Paris")
        self.assertEqual(source["address"]["city"], "London")
        self.assertEqual(out["address"]["city"], "Paris")


class FlattenTests(SimpleTestCase):
    def test_objects_are_listed_whole_and_member_by_member(self):
        got = {n: t for n, t, _ in discovery.flatten_record(RECORD)}
        self.assertEqual(got["address"], "object")
        self.assertEqual(got["address.city"], "string")
        self.assertEqual(got["address.geo"], "object")
        self.assertEqual(got["address.geo.lat"], "number")
        self.assertEqual(got["orders"], "array")
        self.assertEqual(got["orders.0.sku"], "string")
        self.assertNotIn("tags.0", got)                       # a list of scalars stays one field

    def test_depth_is_limited(self):
        deep = {"a": {"b": {"c": {"d": {"e": {"f": 1}}}}}}
        names = [n for n, _, _ in discovery.flatten_record(deep)]
        self.assertIn("a.b.c.d", names)
        self.assertNotIn("a.b.c.d.e.f", names)

    def test_object_samples_are_json(self):
        sample = {n: s for n, _, s in discovery.flatten_record(RECORD)}
        self.assertEqual(sample["address.city"], "London")
        self.assertTrue(sample["address"].startswith('{"city": "London"'))

    def test_openapi_schemas_unfold_refs_and_survive_cycles(self):
        schemas = {
            "Customer": {"type": "object", "required": ["name"], "properties": {
                "name": {"type": "string"},
                "address": {"$ref": "#/components/schemas/Address"},
                "friends": {"type": "array", "items": {"$ref": "#/components/schemas/Customer"}}}},
            "Address": {"type": "object", "properties": {"city": {"type": "string"}, "geo": {"type": "object", "properties": {"lat": {"type": "number"}}}}},
        }
        got = {n: (t, r) for n, t, r in discovery.flatten_schema(schemas["Customer"], schemas, seen=frozenset({"Customer"}))}
        self.assertEqual(got["name"], ("string", True))
        self.assertEqual(got["address"][0], "object")
        self.assertEqual(got["address.geo.lat"][0], "number")
        self.assertEqual(got["friends"][0], "array")
        self.assertFalse([n for n in got if n.startswith("friends.0.")])     # the recursive $ref is not followed forever


class NestedMappingTests(TestCase):
    def setUp(self):
        self.client.force_login(get_user_model().objects.create_user("t", password="pw"))
        self.conn = Connection.objects.create(name="Api", base_url="https://x.example.com", auth_type=Connection.AUTH_NONE)
        self.entity = Entity.objects.create(connection=self.conn, name="Customer", endpoint_path="/customers", source=Entity.SOURCE_SAMPLED)

    def test_a_sample_discovery_saves_every_level(self):
        client = mock.Mock()
        client.get.return_value.json.return_value = [RECORD]
        entity = discovery.discover_from_sample(self.conn, "Customer2", "/c", client)
        names = set(entity.fields.values_list("name", flat=True))
        self.assertTrue({"address", "address.city", "address.geo.lat", "orders.0.sku"} <= names)

    def test_flat_entity_can_be_unfolded_later(self):
        Field.objects.create(entity=self.entity, name="address", field_type="object", sample_value="{}")
        client = mock.Mock()
        client.get.return_value.json.return_value = {"items": [RECORD]}
        added = discovery.discover_nested_fields(self.entity, client)
        self.assertGreater(added, 3)
        self.assertTrue(self.entity.fields.filter(name="address.geo.lat").exists())
        self.assertEqual(discovery.discover_nested_fields(self.entity, client), 0)   # idempotent

    def test_unfolding_falls_back_to_stored_json_samples(self):
        Field.objects.create(entity=self.entity, name="address", field_type="object", sample_value='{"city": "Rome"}')
        added = discovery.discover_nested_fields(self.entity, None)
        self.assertEqual(added, 1)
        self.assertTrue(self.entity.fields.filter(name="address.city").exists())

    def test_nothing_to_unfold_from_is_a_clear_error(self):
        with self.assertRaises(ValueError):
            discovery.discover_nested_fields(self.entity, None)

    def test_endpoint(self):
        Field.objects.create(entity=self.entity, name="address", field_type="object", sample_value="{}")
        with mock.patch("schemas.views.ConnectionClient") as client_cls:
            client_cls.return_value.get.return_value.json.return_value = RECORD
            resp = self.client.post(f"/api/entities/{self.entity.pk}/discover-nested/")
        self.assertEqual(resp.status_code, 200)
        names = [f["name"] for f in resp.json()["entity"]["fields"]]
        self.assertIn("address.geo.lat", names)
        self.assertGreater(resp.json()["added"], 0)

    def test_endpoint_reports_failures(self):
        with mock.patch("schemas.views.ConnectionClient") as client_cls:
            client_cls.return_value.get.side_effect = RuntimeError("boom")
            resp = self.client.post(f"/api/entities/{self.entity.pk}/discover-nested/")
        self.assertEqual(resp.status_code, 400)
        self.assertIn("boom", resp.json()["error"])

    def test_run_reads_a_nested_source_and_writes_a_nested_target(self):
        target_conn = Connection.objects.create(name="Dst", base_url="https://y.example.com", auth_type=Connection.AUTH_NONE)
        src_city = Field.objects.create(entity=self.entity, name="address.city", field_type="string")
        src_lat = Field.objects.create(entity=self.entity, name="address.geo.lat", field_type="number")
        target = Entity.objects.create(connection=target_conn, name="Contact", endpoint_path="/contacts")
        dst_city = Field.objects.create(entity=target, name="location.town", field_type="string")
        dst_lat = Field.objects.create(entity=target, name="location.coords.0", field_type="number")
        mapping = Mapping.objects.create(name="M", source_connection=self.conn)
        pair = EntityMapping.objects.create(mapping=mapping, source_entity=self.entity, target_entity=target)
        fms = [FieldMapping.objects.create(entity_mapping=pair, source_field=src_city, target_field=dst_city),
               FieldMapping.objects.create(entity_mapping=pair, source_field=src_lat, target_field=dst_lat)]
        self.assertEqual(build_payload(RECORD, fms), {"location": {"town": "London", "coords": [51.5]}})

    def test_auto_map_wires_leaves_not_the_object_and_its_members_twice(self):
        for name, kind in [("name", "string"), ("address", "object"), ("address.city", "string")]:
            Field.objects.create(entity=self.entity, name=name, field_type=kind)
        target_conn = Connection.objects.create(name="Dst", base_url="https://y.example.com", auth_type=Connection.AUTH_NONE)
        target = Entity.objects.create(connection=target_conn, name="Contact", endpoint_path="/contacts")
        for name, kind in [("name", "string"), ("address", "object"), ("address.city", "string")]:
            Field.objects.create(entity=target, name=name, field_type=kind)
        mapping = Mapping.objects.create(name="M", source_connection=self.conn)
        pair = EntityMapping.objects.create(mapping=mapping, source_entity=self.entity, target_entity=target)
        matched, unmatched = auto_map(pair)
        wired = sorted(pair.field_mappings.values_list("target_field__name", flat=True))
        self.assertEqual((matched, unmatched, wired), (2, [], ["address.city", "name"]))

    def test_leaf_fields(self):
        fields = [Field(name="a", entity=self.entity), Field(name="a.b", entity=self.entity), Field(name="c", entity=self.entity)]
        self.assertEqual([f.name for f in leaf_fields(fields)], ["a.b", "c"])
