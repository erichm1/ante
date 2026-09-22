from django.contrib.auth import get_user_model
from django.contrib.messages import get_messages
from django.test import TestCase

from . import permissions
from .models import AccessGroup, Department, Profile

User = get_user_model()


class AccessTestBase(TestCase):
    def make(self, name="u", staff=False, superuser=False, groups=(), allowed=(), denied=(), default_group=False):
        user = User.objects.create_user(name, password="pw", is_staff=staff, is_superuser=superuser)
        profile = user.profile
        if not default_group:
            profile.groups.clear()
        profile.groups.add(*groups)
        profile.allowed_modules, profile.denied_modules = list(allowed), list(denied)
        profile.save()
        return User.objects.get(pk=user.pk)          # a fresh object: effective_modules() memoises on the instance

    def group(self, name, modules, default=False):
        return AccessGroup.objects.create(name=name, modules=list(modules), is_default=default)

    def login(self, user):
        self.client.force_login(user)


class DefaultGroupTests(AccessTestBase):
    def test_the_migration_creates_a_standard_group_covering_everything_but_incidents(self):
        standard = AccessGroup.objects.get(name="Standard user")
        self.assertTrue(standard.is_default)
        self.assertEqual(set(standard.modules), set(permissions.ALL_KEYS) - {"incidents"})

    def test_a_new_user_gets_a_profile_and_the_default_group_automatically(self):
        user = User.objects.create_user("fresh", password="pw")
        self.assertEqual([g.name for g in user.profile.groups.all()], ["Standard user"])
        self.assertIn("mappings", permissions.effective_modules(user))
        self.assertNotIn("incidents", permissions.effective_modules(user))

    def test_only_default_groups_are_added_to_new_users(self):
        self.group("Extra", ["logs"])
        self.assertEqual([g.name for g in User.objects.create_user("x", password="pw").profile.groups.all()], ["Standard user"])


class EffectiveModulesTests(AccessTestBase):
    def test_administrators_have_every_module_regardless_of_groups(self):
        for kwargs in ({"staff": True}, {"superuser": True}):
            self.assertEqual(permissions.effective_modules(self.make(f"admin_{next(iter(kwargs))}", **kwargs)), permissions.ALL_KEYS)

    def test_a_user_with_nothing_has_nothing(self):
        self.assertEqual(permissions.effective_modules(self.make()), frozenset())

    def test_groups_add_up(self):
        user = self.make(groups=[self.group("A", ["mappings"]), self.group("B", ["plans", "chains"])])
        self.assertEqual(permissions.effective_modules(user), {"mappings", "plans", "chains"})

    def test_a_user_can_be_granted_a_module_their_groups_lack(self):
        user = self.make(groups=[self.group("A", ["mappings"])], allowed=["reports"])
        self.assertEqual(permissions.effective_modules(user), {"mappings", "reports"})

    def test_a_user_can_be_denied_a_module_their_group_grants(self):
        user = self.make(groups=[self.group("A", ["mappings", "plans"])], denied=["plans"])
        self.assertEqual(permissions.effective_modules(user), {"mappings"})

    def test_deny_beats_allow_and_unknown_keys_are_ignored(self):
        user = self.make(groups=[self.group("A", ["bogus"])], allowed=["logs"], denied=["logs"])
        self.assertEqual(permissions.effective_modules(user), frozenset())

    def test_anonymous_has_nothing(self):
        from django.contrib.auth.models import AnonymousUser
        self.assertEqual(permissions.effective_modules(AnonymousUser()), frozenset())

    def test_users_with_module_lists_active_users_who_have_it(self):
        g = self.group("A", ["jobs"])
        yes = self.make("yes", groups=[g])
        self.make("no")
        inactive = self.make("off", groups=[g])
        User.objects.filter(pk=inactive.pk).update(is_active=False)
        admin = self.make("boss", staff=True)
        self.assertEqual({u.username for u in permissions.users_with_module("jobs")}, {"yes", "boss"})


class PathRuleTests(TestCase):
    def test_paths_map_to_their_module(self):
        for path, keys in [("/mappings/3/", ("mappings",)), ("/jobs/runs/1/", ("jobs",)), ("/plans/", ("plans",)), ("/chains/9/", ("chains",)),
                           ("/api/plans/2/steps/", ("plans",)), ("/api/chain-steps/1/", ("chains",)), ("/studio/tree/", ("studio",)),
                           ("/api/runs/trigger/", ("jobs",)), ("/reports/1/", ("reports",)), ("/tickets/", ("tickets",))]:
            self.assertEqual(permissions.modules_for_path(path), keys, path)

    def test_logs_is_carved_out_of_connections(self):
        self.assertEqual(permissions.modules_for_path("/connections/logs/"), ("logs",))
        self.assertEqual(permissions.modules_for_path("/connections/3/"), ("appstore",))

    def test_shared_endpoints_accept_any_of_their_modules(self):
        self.assertIn("mappings", permissions.modules_for_path("/api/entities/1/"))
        self.assertIn("chains", permissions.modules_for_path("/api/entities/1/file-preview/"))
        self.assertEqual(set(permissions.modules_for_path("/api/attachments/")), {"tickets", "incidents"})

    def test_open_paths_have_no_module(self):
        for path in ("/home/", "/accounts/profile/", "/api/notifications/", "/notifications/", "/static/x.js", "/home/rules/"):
            self.assertIsNone(permissions.modules_for_path(path), path)


class MiddlewareTests(AccessTestBase):
    def test_a_page_without_the_module_redirects_home_with_a_warning(self):
        self.login(self.make(groups=[self.group("A", ["jobs"])]))
        resp = self.client.get("/plans/")
        self.assertRedirects(resp, "/home/", fetch_redirect_response=False)
        warnings = [(m.level_tag, str(m)) for m in get_messages(resp.wsgi_request)]
        self.assertEqual(warnings[0][0], "warning")
        self.assertIn("permission to use Plans", warnings[0][1])
        self.assertIn("administrator", warnings[0][1])

    def test_the_warning_is_shown_on_the_page_the_user_lands_on(self):
        self.login(self.make(groups=[self.group("A", ["jobs"])]))
        html = self.client.get("/plans/", follow=True).content.decode()
        self.assertIn("alert-warning", html)
        self.assertIn("permission to use Plans", html)

    def test_the_json_api_answers_403_with_a_reason(self):
        self.login(self.make(groups=[self.group("A", ["jobs"])]))
        resp = self.client.get("/api/plans/")
        self.assertEqual(resp.status_code, 403)
        body = resp.json()
        self.assertTrue(body["permission_denied"])
        self.assertEqual(body["modules"], ["plans"])
        self.assertIn("permission to use Plans", body["error"])

    def test_writes_are_blocked_too(self):
        self.login(self.make(groups=[self.group("A", ["mappings"])]))
        self.assertEqual(self.client.post("/api/plans/", {"name": "x"}, content_type="application/json").status_code, 403)
        self.assertEqual(self.client.post("/api/runs/trigger/", {"mapping_id": 1}, content_type="application/json").status_code, 403)

    def test_studio_endpoints_answer_json_not_a_redirect(self):
        self.login(self.make(groups=[self.group("A", ["jobs"])]))
        for url in ("/studio/tree/", "/studio/templates/"):
            self.assertEqual(self.client.get(url).status_code, 403, url)
        self.assertEqual(self.client.get("/studio/").status_code, 302)                 # the page itself redirects with a warning

    def test_a_granted_module_is_reachable(self):
        self.login(self.make(groups=[self.group("A", ["plans"])]))
        self.assertEqual(self.client.get("/api/plans/").status_code, 200)

    def test_a_user_switched_off_for_one_module_loses_only_that_one(self):
        self.login(self.make(default_group=True, denied=["chains"]))
        self.assertEqual(self.client.get("/api/chains/").status_code, 403)
        self.assertEqual(self.client.get("/api/plans/").status_code, 200)

    def test_open_paths_never_block(self):
        self.login(self.make())                                                            # no modules at all
        for url in ("/home/", "/accounts/profile/"):
            self.assertEqual(self.client.get(url).status_code, 200, url)

    def test_administrators_pass_everything(self):
        self.login(self.make(staff=True))
        for url in ("/api/plans/", "/api/chains/", "/api/runs/", "/studio/tree/", "/incidents/"):
            self.assertEqual(self.client.get(url).status_code, 200, url)

    def test_incidents_is_off_for_standard_users_and_can_be_granted(self):
        user = self.make(default_group=True)
        self.login(user)
        self.assertEqual(self.client.get("/api/incidents/").status_code, 403)
        profile = user.profile
        profile.allowed_modules = ["incidents"]
        profile.save()
        self.assertEqual(self.client.get("/api/incidents/").status_code, 200)

    def test_the_user_admin_api_is_administrators_only_even_with_every_module(self):
        user = self.make(groups=[self.group("All", permissions.ALL_KEYS)])
        self.login(user)
        for url in ("/api/admin-users/", "/api/admin-groups/", "/api/admin-departments/", "/api/admin-modules/"):
            resp = self.client.get(url)
            self.assertEqual(resp.status_code, 403, url)
            self.assertIn("Only administrators", resp.json()["error"])


# ═══ Administration API ═══════════════════════════════════════════════════════════════════════════════════════
import json as _json


class AdminApiBase(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user("root", password="pw", is_staff=True)
        self.client.force_login(self.admin)
        self.dept = Department.objects.create(name="Finance")
        self.group = AccessGroup.objects.create(name="Ops", modules=["jobs", "chains"])

    def call(self, method, url, body=None):
        return getattr(self.client, method)(url, _json.dumps(body) if body is not None else None, content_type="application/json")

    def make_user(self, name="ada", **kw):
        return User.objects.create_user(name, password="pw", **kw)


class AdminApiAccessTests(AdminApiBase):
    def test_only_administrators_get_in(self):
        self.client.force_login(self.make_user())
        for path in ("/api/admin-users/", "/api/admin-groups/", "/api/admin-departments/", "/api/admin-modules/"):
            self.assertEqual(self.client.get(path).status_code, 403, path)

    def test_module_list(self):
        keys = [m["key"] for m in self.client.get("/api/admin-modules/").json()]
        self.assertEqual(keys, [m.key for m in permissions.MODULES])


class AdminGroupTests(AdminApiBase):
    def test_crud_and_module_validation(self):
        made = self.call("post", "/api/admin-groups/", {"name": "Analysts", "modules": ["reports", "reports", "logs"]})
        self.assertEqual(made.status_code, 201)
        self.assertEqual(made.json()["modules"], ["reports", "logs"])                       # canonical order, no duplicates
        gid = made.json()["id"]
        self.assertEqual(self.call("post", "/api/admin-groups/", {"name": "Bad", "modules": ["nope"]}).status_code, 400)
        self.assertEqual(self.call("patch", f"/api/admin-groups/{gid}/", {"modules": ["studio"]}).json()["modules"], ["studio"])
        self.assertEqual(self.call("delete", f"/api/admin-groups/{gid}/").status_code, 204)

    def test_member_count(self):
        self.make_user().profile.groups.add(self.group)
        row = next(g for g in self.client.get("/api/admin-groups/").json() if g["id"] == self.group.pk)
        self.assertEqual(row["member_count"], 1)

    def test_editing_a_group_changes_its_members_access_at_once(self):
        user = self.make_user()
        user.profile.groups.set([self.group])
        self.assertIn("jobs", permissions.effective_modules(User.objects.get(pk=user.pk)))
        self.call("patch", f"/api/admin-groups/{self.group.pk}/", {"modules": ["chains"]})
        self.assertNotIn("jobs", permissions.effective_modules(User.objects.get(pk=user.pk)))


class AdminDepartmentTests(AdminApiBase):
    def test_crud_and_unique_name(self):
        self.assertEqual(self.call("post", "/api/admin-departments/", {"name": "Legal"}).status_code, 201)
        self.assertEqual(self.call("post", "/api/admin-departments/", {"name": "Legal"}).status_code, 400)
        self.assertEqual(self.call("post", "/api/admin-departments/", {"name": "  "}).status_code, 400)

    def test_deleting_a_department_keeps_its_people(self):
        user = self.make_user()
        user.profile.department = self.dept
        user.profile.save()
        self.assertEqual(self.call("delete", f"/api/admin-departments/{self.dept.pk}/").status_code, 204)
        user.profile.refresh_from_db()
        self.assertIsNone(user.profile.department)


class AdminUserTests(AdminApiBase):
    def test_create_with_everything(self):
        resp = self.call("post", "/api/admin-users/", {
            "username": "grace", "password": "S3cure-passphrase!", "first_name": "Grace", "email": "g@example.com",
            "department": self.dept.pk, "groups": [self.group.pk], "allowed_modules": ["reports"], "denied_modules": ["chains"]})
        self.assertEqual(resp.status_code, 201, resp.content)
        body = resp.json()
        self.assertEqual((body["department"], body["groups"], body["allowed_modules"], body["denied_modules"]),
                         (self.dept.pk, [self.group.pk], ["reports"], ["chains"]))
        self.assertEqual(body["effective_modules"], ["jobs", "reports"])                     # group jobs+chains, +reports, −chains
        self.assertNotIn("password", body)
        self.assertTrue(User.objects.get(username="grace").check_password("S3cure-passphrase!"))

    def test_a_new_user_without_groups_gets_the_default_group(self):
        resp = self.call("post", "/api/admin-users/", {"username": "new", "password": "S3cure-passphrase!"})
        default = AccessGroup.objects.get(is_default=True)
        self.assertEqual(resp.json()["groups"], [default.pk])

    def test_password_rules(self):
        self.assertEqual(self.call("post", "/api/admin-users/", {"username": "x"}).status_code, 400)
        weak = self.call("post", "/api/admin-users/", {"username": "x", "password": "123"})
        self.assertEqual(weak.status_code, 400)
        self.assertIn("password", weak.json())

    def test_update_leaves_the_password_alone_unless_given(self):
        user = self.make_user()
        self.call("patch", f"/api/admin-users/{user.pk}/", {"first_name": "Ada", "password": ""})
        user.refresh_from_db()
        self.assertTrue(user.check_password("pw"))
        self.assertEqual(user.first_name, "Ada")
        self.call("patch", f"/api/admin-users/{user.pk}/", {"password": "An0ther-passphrase!"})
        user.refresh_from_db()
        self.assertTrue(user.check_password("An0ther-passphrase!"))

    def test_permissions_can_be_switched_per_module(self):
        user = self.make_user()
        resp = self.call("patch", f"/api/admin-users/{user.pk}/", {"groups": [], "allowed_modules": ["tickets"], "denied_modules": []})
        self.assertEqual(resp.json()["effective_modules"], ["tickets"])

    def test_a_module_cannot_be_both_on_and_off(self):
        user = self.make_user()
        resp = self.call("patch", f"/api/admin-users/{user.pk}/", {"allowed_modules": ["jobs"], "denied_modules": ["jobs"]})
        self.assertEqual(resp.status_code, 400)

    def test_unknown_modules_are_rejected(self):
        user = self.make_user()
        self.assertEqual(self.call("patch", f"/api/admin-users/{user.pk}/", {"allowed_modules": ["warp-drive"]}).status_code, 400)

    def test_the_change_takes_effect_on_the_users_next_request(self):
        user = self.make_user()
        self.client.force_login(user)
        self.assertEqual(self.client.get("/jobs/").status_code, 200)
        self.client.force_login(self.admin)
        self.call("patch", f"/api/admin-users/{user.pk}/", {"denied_modules": ["jobs"]})
        self.client.force_login(user)
        self.assertEqual(self.client.get("/jobs/").status_code, 302)

    def test_filters(self):
        a, b = self.make_user("ada"), self.make_user("bob")
        b.profile.department = self.dept
        b.profile.save()
        names = lambda qs: [u["username"] for u in self.client.get(f"/api/admin-users/{qs}").json()]
        self.assertEqual(names(f"?department={self.dept.pk}"), ["bob"])
        self.assertEqual(names("?q=ad"), ["ada"])

    # -- guard rails
    def test_you_cannot_lock_yourself_out(self):
        self.make_user("second_admin", is_staff=True)                     # so "last admin" isn't what stops us
        me = f"/api/admin-users/{self.admin.pk}/"
        self.assertEqual(self.call("patch", me, {"is_staff": False}).status_code, 400)
        self.assertEqual(self.call("patch", me, {"is_active": False}).status_code, 400)
        self.assertEqual(self.call("delete", me).status_code, 400)
        self.admin.refresh_from_db()
        self.assertTrue(self.admin.is_staff and self.admin.is_active)
        self.assertTrue(self.call("get", me).json()["is_you"])

    def test_the_sole_administrator_cannot_be_removed(self):
        me = f"/api/admin-users/{self.admin.pk}/"
        for body in ({"is_staff": False}, {"is_active": False}):
            resp = self.call("patch", me, body)
            self.assertEqual(resp.status_code, 400)
        self.assertEqual(self.call("delete", me).status_code, 400)
        self.assertEqual(User.objects.filter(is_staff=True, is_active=True).count(), 1)

    def test_only_a_superuser_touches_superusers(self):
        boss = User.objects.create_user("boss", password="pw", is_superuser=True, is_staff=True)
        url = f"/api/admin-users/{boss.pk}/"
        self.assertEqual(self.call("patch", url, {"first_name": "x"}).status_code, 400)
        self.assertEqual(self.call("delete", url).status_code, 400)
        ada = self.make_user()
        self.assertEqual(self.call("patch", f"/api/admin-users/{ada.pk}/", {"is_superuser": True}).status_code, 400)
        self.assertEqual(self.call("post", "/api/admin-users/", {"username": "z", "password": "S3cure-passphrase!", "is_superuser": True}).status_code, 400)

    def test_a_superuser_can_grant_admin_rights(self):
        boss = User.objects.create_user("boss", password="pw", is_superuser=True, is_staff=True)
        self.client.force_login(boss)
        ada = self.make_user()
        resp = self.call("patch", f"/api/admin-users/{ada.pk}/", {"is_staff": True, "is_superuser": True})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(resp.json()["effective_modules"]), len(permissions.MODULES))

    def test_deleting_an_ordinary_user(self):
        ada = self.make_user()
        self.assertEqual(self.call("delete", f"/api/admin-users/{ada.pk}/").status_code, 204)
        self.assertFalse(User.objects.filter(pk=ada.pk).exists())


# ═══ Who can sign in ═════════════════════════════════════════════════════════════════════════════════════════
from django.test import override_settings


class SignInPolicyTests(TestCase):
    """Only an account that already exists — created by an administrator — can sign in."""

    def setUp(self):
        self.user = User.objects.create_user("ada", password="Correct-horse-9battery")

    def sign_in(self, username, password):
        return self.client.post("/accounts/login/", {"username": username, "password": password})

    def test_an_existing_account_signs_in(self):
        resp = self.sign_in("ada", "Correct-horse-9battery")
        self.assertRedirects(resp, "/home/", fetch_redirect_response=False)
        self.assertEqual(self.client.get("/home/").status_code, 200)

    def test_an_account_that_does_not_exist_cannot(self):
        resp = self.sign_in("nobody", "Correct-horse-9battery")
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "correct username and password")
        self.assertEqual(self.client.get("/home/").status_code, 302)

    def test_a_deactivated_account_cannot(self):
        User.objects.filter(pk=self.user.pk).update(is_active=False)
        resp = self.sign_in("ada", "Correct-horse-9battery")
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "correct username and password")

    def test_the_error_does_not_reveal_which_usernames_exist(self):
        wrong_password = self.sign_in("ada", "wrong").content.decode()
        unknown_user = self.sign_in("ghost", "wrong").content.decode()
        message = "Please enter a correct username and password."
        self.assertIn(message, wrong_password)
        self.assertIn(message, unknown_user)

    def test_public_sign_up_is_closed_by_default(self):
        resp = self.client.get("/accounts/register/", follow=True)
        self.assertRedirects(resp, "/accounts/login/")
        self.assertContains(resp, "Accounts are created by an administrator")
        made = self.client.post("/accounts/register/", {"username": "mallory", "password1": "Correct-horse-9battery", "password2": "Correct-horse-9battery"})
        self.assertEqual(made.status_code, 302)
        self.assertFalse(User.objects.filter(username="mallory").exists())

    def test_the_sign_in_page_no_longer_offers_a_register_link(self):
        html = self.client.get("/accounts/login/").content.decode()
        self.assertNotIn("/accounts/register/", html)
        self.assertIn("Accounts are created by an administrator", html)

    @override_settings(ALLOW_SELF_REGISTRATION=True)
    def test_open_sign_up_can_be_switched_back_on(self):
        html = self.client.get("/accounts/login/").content.decode()
        self.assertIn("/accounts/register/", html)
        self.assertEqual(self.client.get("/accounts/register/").status_code, 200)
        made = self.client.post("/accounts/register/", {"username": "grace", "password1": "Correct-horse-9battery", "password2": "Correct-horse-9battery"})
        self.assertEqual(made.status_code, 302)
        self.assertTrue(User.objects.filter(username="grace").exists())

    def test_an_administrator_created_account_can_sign_in(self):
        admin = User.objects.create_user("root", password="pw", is_staff=True)
        self.client.force_login(admin)
        made = self.client.post("/api/admin-users/", '{"username": "newhire", "password": "Correct-horse-9battery"}', content_type="application/json")
        self.assertEqual(made.status_code, 201)
        self.client.logout()
        self.assertRedirects(self.sign_in("newhire", "Correct-horse-9battery"), "/home/", fetch_redirect_response=False)

    def test_logging_out_lands_on_the_public_front_page(self):
        self.client.force_login(self.user)
        resp = self.client.post("/accounts/logout/")
        self.assertRedirects(resp, "/", fetch_redirect_response=False)
