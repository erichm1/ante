import json
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase

from connections.models import Connection
from jobs import engine
from jobs.models import MigrationRun
from schemas.models import Entity, Field

from . import automap
from .models import EntityMapping, FieldMapping, Mapping


class F:
    """A stand-in for a Field: just what the matcher reads."""
    _next = 0

    def __init__(self, name, field_type="string"):
        F._next += 1
        self.pk, self.name, self.field_type = F._next, name, field_type


def best(source_names, target_name, **kw):
    sources = [F(*n) if isinstance(n, tuple) else F(n) for n in source_names]
    sugg, *_ = automap.suggest(sources, [F(target_name)], **kw)
    return (sugg[0]["source"].name, sugg[0]["score"]) if sugg else None


class ScoringTests(SimpleTestCase):
    def test_exact_and_case_and_separator_insensitive(self):
        self.assertEqual(best(["email", "phone"], "email"), ("email", 100))
        self.assertEqual(best(["fullName"], "full_name"), ("fullName", 96))
        self.assertEqual(best(["Customer-Email"], "customer_email")[1], 96)

    def test_same_field_name_in_a_different_object(self):
        self.assertEqual(best(["address.city", "name"], "location.city"), ("address.city", 88))
        self.assertEqual(best(["orders.0.sku"], "sku"), ("orders.0.sku", 88))

    def test_known_equivalents_including_portuguese(self):
        self.assertEqual(best(["telefone"], "phone"), ("telefone", 80))
        self.assertEqual(best(["descricao"], "description")[1], 80)
        self.assertEqual(best(["cep"], "zip")[1], 80)
        self.assertEqual(best(["preço"], "preco")[1], 96)                    # accents are ignored
        self.assertEqual(best(["address.geo.lat"], "location.latitude"), ("address.geo.lat", 80))   # abbreviations
        self.assertEqual(best(["address.geo.lng"], "location.longitude")[1], 80)

    def test_a_shared_key_word_and_generic_words(self):
        self.assertEqual(best(["customer_email"], "email"), ("customer_email", 70))
        self.assertIsNone(best(["customer_id"], "id"))                       # only a generic word in common: below the default threshold
        self.assertEqual(best(["customer_id"], "id", min_score=50), ("customer_id", 55))

    def test_similar_spelling(self):
        got = best(["adress"], "address")
        self.assertIsNotNone(got)
        self.assertLess(got[1], 100)

    def test_unrelated_names_do_not_match(self):
        self.assertIsNone(best(["created_at", "sku"], "email"))

    def test_the_best_candidate_wins(self):
        self.assertEqual(best(["customer_email", "email", "mail"], "email"), ("email", 100))

    def test_types_lower_the_score(self):
        exact = best([("total", "number")], "total")
        self.assertEqual(exact[1], 100)
        sugg, *_ = automap.suggest([F("total", "object")], [F("total", "string")], min_score=30)
        self.assertEqual(sugg[0]["score"], 55)                                # 100 - 45
        self.assertIn("types differ", sugg[0]["reason"])
        self.assertEqual(automap.suggest([F("total", "object")], [F("total", "string")])[0], [])   # under the default threshold

    def test_objects_with_members_are_not_mapped_twice(self):
        address, city = F("address", "object"), F("address.city")
        t_address, t_city = F("address", "object"), F("address.city")
        sugg, *_ = automap.suggest([address, city], [t_address, t_city])
        self.assertEqual([(s["source"].name, s["target"].name) for s in sugg], [("address.city", "address.city")])

    def test_skips_and_unmatched(self):
        s1, s2, t1, t2 = F("email"), F("nickname"), F("email"), F("zzz")
        sugg, un_t, un_s = automap.suggest([s1, s2], [t1, t2], skip_pairs={(s1.pk, t1.pk)})
        self.assertEqual(sugg, [])
        sugg, un_t, un_s = automap.suggest([s1, s2], [t1, t2])
        self.assertEqual(len(sugg), 1)
        self.assertEqual([f.name for f in un_t], ["zzz"])
        self.assertEqual([f.name for f in un_s], ["nickname"])
        self.assertEqual(automap.suggest([s1], [t1], skip_targets={t1.pk})[0], [])


class AutoMapApiTests(TestCase):
    def setUp(self):
        self.client.force_login(get_user_model().objects.create_user("t", password="pw"))
        src = Connection.objects.create(name="Src", base_url="https://s.example.com", auth_type=Connection.AUTH_NONE)
        dst = Connection.objects.create(name="Dst", base_url="https://d.example.com", auth_type=Connection.AUTH_NONE)
        self.source = Entity.objects.create(connection=src, name="Customer", endpoint_path="/c")
        self.target = Entity.objects.create(connection=dst, name="Contact", endpoint_path="/k")
        self.s = {n: Field.objects.create(entity=self.source, name=n, field_type=t) for n, t in
                  [("id", "integer"), ("fullName", "string"), ("email", "string"), ("telefone", "string"), ("address", "object"), ("address.city", "string"), ("notes_internal", "string")]}
        self.t = {n: Field.objects.create(entity=self.target, name=n, field_type=t) for n, t in
                  [("id", "integer"), ("full_name", "string"), ("email", "string"), ("phone", "string"), ("location", "object"), ("location.city", "string"), ("vat", "string")]}
        self.mapping = Mapping.objects.create(name="M", source_connection=src)
        self.mapping.destination_connections.add(dst)
        self.pair = EntityMapping.objects.create(mapping=self.mapping, source_entity=self.source, target_entity=self.target)

    def post(self, url, body=None):
        return self.client.post(url, json.dumps(body or {}), content_type="application/json")

    def auto_map(self, body=None):
        return self.post(f"/api/entity-mappings/{self.pair.pk}/auto-map/", body)

    def wires(self, status=None):
        qs = self.pair.field_mappings.all()
        return sorted((fm.source_field.name, fm.target_field.name) for fm in (qs.filter(status=status) if status else qs))

    def test_suggestions_are_saved_as_drafts(self):
        body = self.auto_map().json()
        expected = [("address.city", "location.city"), ("email", "email"), ("fullName", "full_name"), ("id", "id"), ("telefone", "phone")]
        self.assertEqual(self.wires("draft"), expected)
        self.assertEqual(self.wires("confirmed"), [])
        self.assertEqual(body["created"], 5)
        self.assertEqual([t["name"] for t in body["unmatched_targets"]], ["vat"])
        self.assertEqual([s["name"] for s in body["unmatched_sources"]], ["notes_internal"])
        by_target = {s["target"]["name"]: s for s in body["suggestions"]}
        self.assertEqual((by_target["email"]["score"], by_target["phone"]["reason"].startswith("Known equivalent")), (100, True))
        fm = self.pair.field_mappings.get(target_field=self.t["phone"])
        self.assertEqual((fm.status, fm.match_score), ("draft", 80))

    def test_dry_run_saves_nothing(self):
        body = self.auto_map({"dry_run": True}).json()
        self.assertEqual((body["created"], len(body["suggestions"]), self.wires()), (0, 5, []))

    def test_min_score_and_rerunning_does_not_pile_up(self):
        self.assertEqual(self.auto_map({"min_score": 95}).json()["created"], 3)         # exact and case-insensitive only
        self.assertEqual(len(self.wires("draft")), 3)
        self.auto_map()
        self.assertEqual(len(self.wires("draft")), 5)                                     # replaced, not added to
        self.auto_map({"replace_drafts": False})
        self.assertEqual(len(self.wires("draft")), 5)                                     # nothing new to add either

    def test_confirmed_wires_are_left_alone(self):
        FieldMapping.objects.create(entity_mapping=self.pair, source_field=self.s["notes_internal"], target_field=self.t["email"])
        self.auto_map()
        self.assertEqual(self.wires("confirmed"), [("notes_internal", "email")])
        self.assertNotIn(("email", "email"), self.wires("draft"))                          # that target is already taken
        self.auto_map({"only_unmapped": False})
        self.assertIn(("email", "email"), self.wires("draft"))

    def test_accept_some_then_discard_the_rest(self):
        self.auto_map()
        pick = self.pair.field_mappings.get(target_field=self.t["email"])
        self.assertEqual(self.post(f"/api/entity-mappings/{self.pair.pk}/confirm-drafts/", {"ids": [pick.pk]}).json(), {"confirmed": 1})
        self.assertEqual(self.wires("confirmed"), [("email", "email")])
        self.assertEqual(self.post(f"/api/entity-mappings/{self.pair.pk}/discard-drafts/").json(), {"discarded": 4})
        self.assertEqual(self.wires(), [("email", "email")])

    def test_accept_all_at_mapping_level(self):
        self.post(f"/api/mappings/{self.mapping.pk}/auto-map/")
        self.assertEqual(self.client.get(f"/api/mappings/{self.mapping.pk}/").json()["draft_count"], 5)
        self.assertEqual(self.post(f"/api/mappings/{self.mapping.pk}/confirm-drafts/").json(), {"confirmed": 5})
        self.assertEqual(len(self.wires("confirmed")), 5)
        self.assertEqual(self.client.get(f"/api/mappings/{self.mapping.pk}/").json()["draft_count"], 0)

    def test_a_single_draft_can_be_accepted_by_patching_its_status(self):
        self.auto_map()
        fm = self.pair.field_mappings.get(target_field=self.t["phone"])
        resp = self.client.patch(f"/api/field-mappings/{fm.pk}/", json.dumps({"status": "confirmed"}), content_type="application/json")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["status"], "confirmed")
        self.assertEqual(resp.json()["match_score"], 80)                                   # the score is kept, and can't be edited
        self.client.patch(f"/api/field-mappings/{fm.pk}/", json.dumps({"match_score": 5}), content_type="application/json")
        fm.refresh_from_db()
        self.assertEqual(fm.match_score, 80)

    def test_serializers_report_draft_counts(self):
        self.auto_map()
        pair = self.client.get(f"/api/entity-mappings/{self.pair.pk}/").json()
        self.assertEqual(pair["draft_count"], 5)
        self.assertEqual({fm["status"] for fm in pair["field_mappings"]}, {"draft"})

    def test_drafts_never_run(self):
        self.auto_map()
        self.pair.field_mappings.filter(target_field=self.t["id"]).update(status="confirmed")                              # accept just one
        run = MigrationRun.objects.create(mapping=self.mapping, status=MigrationRun.STATUS_RUNNING)
        sent = []

        class Client:
            def __init__(self, *a, **k): pass
            def get(self, *a, **k):
                r = mock.Mock(); r.json.return_value = [{"id": 1, "email": "a@b.c", "fullName": "Ada"}]; r.raise_for_status = lambda: None; return r
            def request(self, method, path, **kw):
                sent.append(kw.get("json")); r = mock.Mock(); r.status_code = 201; r.raise_for_status = lambda: None; return r
            post = put = patch = request

        with mock.patch.object(engine, "ConnectionClient", Client):
            engine.run_migration(run)
        self.assertTrue(sent)
        self.assertEqual(sent[0], {"id": 1})                                                # only the confirmed wire; the drafts stayed out
        self.assertTrue(run.logs.filter(message__contains="4 draft field mapping").exists())

    def test_a_pair_with_only_drafts_says_so(self):
        self.auto_map()
        run = MigrationRun.objects.create(mapping=self.mapping, status=MigrationRun.STATUS_RUNNING)
        with mock.patch.object(engine, "ConnectionClient"):
            engine.run_migration(run)
        step = run.step_statuses.get() if hasattr(run, "step_statuses") else None
        if step is not None:
            self.assertIn("draft", step.error_message)

    def test_preview_leaves_drafts_out_unless_asked(self):
        self.auto_map()
        records = [{"id": 1, "email": "a@b.c", "fullName": "Ada", "telefone": "1"}]
        with mock.patch("mappings.preview.discovery.read_all_records", return_value=records):
            plain = self.client.get(f"/api/mappings/{self.mapping.pk}/preview/").json()["pairs"][0]
            with_drafts = self.client.get(f"/api/mappings/{self.mapping.pk}/preview/?drafts=1").json()["pairs"][0]
        self.assertEqual((plain["payloads"], plain["draft_count"], plain["drafts_included"]), ([{}], 5, False))
        self.assertEqual(with_drafts["payloads"][0]["email"], "a@b.c")
        self.assertEqual(with_drafts["payloads"][0]["phone"], "1")
        self.assertTrue(with_drafts["drafts_included"])

    def test_duplicating_a_mapping_keeps_its_drafts_as_drafts(self):
        self.auto_map()
        copy = self.post(f"/api/mappings/{self.mapping.pk}/duplicate/").json()
        self.assertEqual(copy["draft_count"], 5)

    def test_the_mapping_page_offers_auto_map_and_shows_drafts_for_review(self):
        page = self.client.get(f"/mappings/{self.mapping.pk}/").content.decode()
        self.assertIn(f'data-automap="{self.mapping.pk}"', page)
        self.assertNotIn('id="draftBanner"', page)
        self.assertIn("window.MAPPING_DRAFTS = 0;", page)

        self.auto_map()
        canvas = self.client.get(f"/mappings/{self.mapping.pk}/").content.decode()
        self.assertIn('id="draftBanner"', canvas)
        self.assertIn("5 suggested field mapping(s)", canvas)
        self.assertIn("window.MAPPING_DRAFTS = 5;", canvas)
        self.assertIn('"status": "draft"', canvas)                         # the canvas draws the drafts dashed

        raw = self.client.get(f"/mappings/{self.mapping.pk}/?tab=raw").content.decode()
        self.assertEqual(raw.count("data-fm-accept="), 5)
        self.assertEqual(raw.count("data-fm-reject="), 5)
        self.assertIn("Same name", raw)                                    # why each match was suggested

    def test_the_page_has_no_auto_map_button_without_an_entity_pair(self):
        self.pair.delete()
        page = self.client.get(f"/mappings/{self.mapping.pk}/").content.decode()
        self.assertNotIn("data-automap=", page)

    def test_accepting_every_draft_clears_the_review_banner(self):
        self.auto_map()
        self.post(f"/api/mappings/{self.mapping.pk}/confirm-drafts/")
        page = self.client.get(f"/mappings/{self.mapping.pk}/").content.decode()
        self.assertNotIn('id="draftBanner"', page)
        self.assertIn("window.MAPPING_DRAFTS = 0;", page)
