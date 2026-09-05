import unittest
import sys
import types


dotenv = types.ModuleType("dotenv")
dotenv.load_dotenv = lambda: None
sys.modules.setdefault("dotenv", dotenv)

gspread = types.ModuleType("gspread")
gspread.authorize = lambda credentials: None
gspread_exceptions = types.ModuleType("gspread.exceptions")


class WorksheetNotFound(Exception):
    pass


gspread_exceptions.WorksheetNotFound = WorksheetNotFound
sys.modules.setdefault("gspread", gspread)
sys.modules.setdefault("gspread.exceptions", gspread_exceptions)

google = types.ModuleType("google")
google_oauth2 = types.ModuleType("google.oauth2")
google_service_account = types.ModuleType("google.oauth2.service_account")


class Credentials:
    @classmethod
    def from_service_account_info(cls, creds_info, scopes):
        return cls()


google_service_account.Credentials = Credentials
sys.modules.setdefault("google", google)
sys.modules.setdefault("google.oauth2", google_oauth2)
sys.modules.setdefault("google.oauth2.service_account", google_service_account)

import instagram_automation as app


class InstagramAutomationParsingTests(unittest.TestCase):
    def test_instagram_host_validation_rejects_lookalikes(self):
        self.assertTrue(app.is_instagram_url("https://www.instagram.com/store.pk/"))
        self.assertTrue(app.is_instagram_url("m.instagram.com/_u/store.pk"))
        self.assertFalse(app.is_instagram_url("https://fakeinstagram.com/store"))
        self.assertFalse(app.is_instagram_url("https://instagram.com.example.net/store"))

    def test_handle_from_owned_paths(self):
        cases = {
            "https://www.instagram.com/Ana.Apparels/": "ana.apparels",
            "https://www.instagram.com/gogo.fashion3/reels/": "gogo.fashion3",
            "https://www.instagram.com/ana.apparels/p/Dc3clFKE0Se/": "ana.apparels",
            "https://m.instagram.com/_u/Zen_Echo_599": "zen_echo_599",
            "https://www-fallback.instagram.com/flinsyshop2": "flinsyshop2",
        }
        for url, expected in cases.items():
            with self.subTest(url=url):
                self.assertEqual(app.handle_from_path(url), expected)

    def test_reserved_routes_do_not_become_handles(self):
        cases = [
            "https://www.instagram.com/reel/DZpusYgzRXE/",
            "https://www.instagram.com/p/Dc3clFKE0Se/",
            "https://www.instagram.com/explore/tags/cod/",
            "https://www.instagram.com/accounts/login/",
            "https://www.instagram.com/shop/",
            "https://www.instagram.com/store-name/",
        ]
        for url in cases:
            with self.subTest(url=url):
                self.assertEqual(app.handle_from_path(url), "")

    def test_extract_handle_from_title_and_snippet(self):
        self.assertEqual(
            app.extract_handle(
                "https://www.instagram.com/reel/DZpusYgzRXE/",
                '@store.pk on Instagram: "COD available"',
                "",
            ),
            ("store.pk", "text"),
        )
        self.assertEqual(
            app.extract_handle(
                "https://www.instagram.com/reel/DZpusYgzRXE/",
                "",
                '1,234 likes - Store PK (@store.pk) on August 12, 2024: "Order now"',
            ),
            ("store.pk", "text"),
        )

    def test_title_cleanup_handles_normal_instagram_bullet(self):
        self.assertEqual(
            app.parse_name_from_title(
                "Jane's Boutique (@janesboutique) \u2022 Instagram photos and videos",
                "janesboutique",
            ),
            "Jane's Boutique (@janesboutique)",
        )

    def test_filter_accepts_broad_ecommerce_and_currency_formats(self):
        passing_texts = [
            "Online store in Pakistan. Delivery available.",
            "New arrivals PKR1500 order now",
            "Kurti price Rs 2500 WhatsApp for order",
            "Shipping all over India INR999",
            "Inbox to order. Online shopping.",
        ]
        for text in passing_texts:
            with self.subTest(text=text):
                self.assertTrue(app.passes_filter(text))

    def test_filter_rejects_noise_and_substring_false_positive(self):
        self.assertFalse(app.passes_filter("Digital marketing agency. Grow your business."))
        self.assertFalse(app.passes_filter("Orders. Years. Hours."))
        self.assertFalse(app.passes_filter("Public figure with shop now highlights."))


if __name__ == "__main__":
    unittest.main()
