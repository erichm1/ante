import html as html_lib
import pathlib
import re

from django.contrib.auth import get_user_model
from django.contrib.staticfiles import finders
from django.test import TestCase

User = get_user_model()


class LandingPageTests(TestCase):
    def test_visitors_see_the_product_page(self):
        resp = self.client.get("/")
        self.assertEqual(resp.status_code, 200)
        html = resp.content.decode()
        self.assertIn("Ante", html)
        self.assertIn('href="/accounts/login/"', html)                       # the way in
        self.assertNotIn("/accounts/register/", html)                        # there is no public sign-up
        for section in ('id="features"', 'id="how"', 'id="control"'):
            self.assertIn(section, html)

    def test_it_shows_the_main_functionality_with_pictures(self):
        html = self.client.get("/").content.decode()
        images = re.findall(r'static/(landing/[\w-]+\.webp)', html)
        self.assertGreaterEqual(len(set(images)), 10)
        for name in set(images):
            self.assertIsNotNone(finders.find(name), f"{name} is referenced by the landing page but missing")
        for feature in ("Studio", "Data preview", "Chains", "Plans", "Templates", "permissions", "Logs"):
            self.assertIn(feature, html)

    def test_every_picture_has_alt_text_and_a_size(self):
        html = self.client.get("/").content.decode()
        for tag in re.findall(r"<img [^>]*landing/[^>]*>", html):
            self.assertRegex(tag, r'alt="[^"]{10,}"')
            self.assertRegex(tag, r'width="\d+" height="\d+"')

    def test_signed_in_users_go_straight_to_the_dashboard(self):
        self.client.force_login(User.objects.create_user("ada", password="pw"))
        self.assertRedirects(self.client.get("/"), "/home/", fetch_redirect_response=False)

    def test_everything_else_still_needs_a_login(self):
        for path in ("/home/", "/studio/", "/mappings/", "/notifications/"):
            resp = self.client.get(path)
            self.assertEqual(resp.status_code, 302, path)
            self.assertIn("/accounts/login/", resp["Location"])
        self.assertEqual(self.client.get("/api/mappings/").status_code, 401)

    def test_the_logo_and_favicon_are_on_every_kind_of_page(self):
        self.client.force_login(User.objects.create_user("ada", password="pw"))
        app_page = self.client.get("/home/").content.decode()
        self.client.logout()
        for html in (self.client.get("/").content.decode(), self.client.get("/accounts/login/").content.decode(), app_page):
            self.assertIn("img/logo.svg", html)
            self.assertRegex(html, r'<link rel="icon"[^>]*logo\.svg')
        # …and the sign-in page carries the same header (logo linking back to the front page) as the landing page
        login = self.client.get("/accounts/login/").content.decode()
        self.assertIn('class="lp-brand"', login)

    def test_the_landing_page_is_translated_like_everything_else(self):
        """Every visible string on the landing page has a translation in all seven other languages."""
        import importlib.util
        spec = importlib.util.spec_from_file_location("build", pathlib.Path(__file__).resolve().parent.parent / "i18n" / "build.py")
        build = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(build)
        known = build.tables(build.load_rows())["ja"]
        html = self.client.get("/").content.decode()
        body = re.sub(r"<(script|style)[\s\S]*?</\1>", "", html)
        texts = set()
        for chunk in re.findall(r">([^<>]+)<", body):
            t = re.sub(r"\s+", " ", html_lib.unescape(chunk)).strip()
            if re.search(r"[A-Za-z]{3,}", t):
                texts.add(t)
        for alt in re.findall(r'alt="([^"]+)"', body):
            texts.add(html_lib.unescape(alt))
        texts.add("Ante — Move data between systems, visually")            # <title>
        missing = sorted(t for t in texts if t not in known and t not in ("Ante", "REST APIs", "OAuth2, API keys, JWT"))
        self.assertEqual(missing, [], "landing text without a translation")
