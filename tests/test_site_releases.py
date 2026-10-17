import sys
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import site_releases

BODY = """A short summary with `code`.

## Highlights

- **One-step install.** Writes `settings.json`
  and keeps going on a second line.
- Plain item with [a link](https://example.com/x?a=1&b=2).

## Bug fixes

- Fixed <script>alert(1)</script> handling.
- A [bad link](javascript:alert(1)) stays text.

## Known issues

- Windows untested.

**Full changelog**: https://github.com/raj-rangani/kiasi/compare/v0.7.0...v0.8.0
"""


def release(tag, date, body=BODY, **extra):
    r = {"tag_name": tag, "name": "Kiasi " + tag[1:], "draft": False, "prerelease": False,
         "published_at": date + "T10:00:00Z", "html_url": "https://github.com/raj-rangani/kiasi/releases/tag/" + tag, "body": body}
    r.update(extra)
    return r


class ParseBody(unittest.TestCase):
    def setUp(self):
        self.summary, self.compare, self.sections = site_releases.parse_body(BODY)

    def test_summary(self):
        self.assertEqual(self.summary, "A short summary with <code>code</code>.")

    def test_sections_in_order(self):
        self.assertEqual([s["heading"] for s in self.sections], ["Highlights", "Bug fixes", "Known issues"])

    def test_continuation_lines_join(self):
        first = self.sections[0]["items"][0]
        self.assertEqual(len(self.sections[0]["items"]), 2)
        self.assertIn("<code>settings.json</code> and keeps going on a second line.", first["html"])

    def test_lead(self):
        items = self.sections[0]["items"]
        self.assertEqual(items[0]["lead"], "One-step install.")
        self.assertTrue(items[0]["html"].startswith("<b>One-step install.</b> "))
        self.assertEqual(items[1]["lead"], "")

    def test_compare_is_not_an_item(self):
        self.assertEqual(self.compare, "https://github.com/raj-rangani/kiasi/compare/v0.7.0...v0.8.0")
        self.assertNotIn("Full changelog", str(self.sections))

    def test_no_summary(self):
        summary, compare, sections = site_releases.parse_body("## Highlights\n\n- One\n")
        self.assertEqual((summary, compare, len(sections)), ("", "", 1))

    def test_bullets_without_heading(self):
        summary, compare, sections = site_releases.parse_body("Intro.\n\n- One\n- Two\n")
        self.assertEqual(summary, "Intro.")
        self.assertEqual([(s["heading"], len(s["items"])) for s in sections], [("Changes", 2)])

    def test_http_link(self):
        self.assertIn('<a href="https://example.com/x?a=1&amp;b=2">a link</a>', self.sections[0]["items"][1]["html"])

    def test_script_is_escaped(self):
        html = self.sections[1]["items"][0]["html"]
        self.assertNotIn("<script>", html)
        self.assertIn("&lt;script&gt;", html)

    def test_non_http_link_stays_text(self):
        html = self.sections[1]["items"][1]["html"]
        self.assertNotIn("<a ", html)
        self.assertIn("[bad link](javascript:alert(1))", html)


class Convert(unittest.TestCase):
    def test_drafts_skipped_and_newest_first(self):
        api = [release("v0.1.0", "2026-01-01"), release("v0.3.0", "2026-03-01", draft=True), release("v0.2.0", "2026-02-01")]
        out = site_releases.convert(api)
        self.assertEqual([r["tag"] for r in out], ["v0.2.0", "v0.1.0"])
        self.assertEqual(out[0]["version"], "0.2.0")

    def test_latest_skips_prerelease(self):
        api = [release("v0.2.0", "2026-02-01", prerelease=True), release("v0.1.0", "2026-01-01")]
        out = site_releases.convert(api)
        self.assertEqual([r["latest"] for r in out], [False, True])


class Output(unittest.TestCase):
    def test_atom_parses(self):
        out = site_releases.convert([release("v0.2.0", "2026-02-01"), release("v0.1.0", "2026-01-01")])
        root = ET.fromstring('<?xml version="1.0" encoding="utf-8"?>\n' + site_releases.atom(out))
        ns = {"a": site_releases.ATOM}
        entries = root.findall("a:entry", ns)
        self.assertEqual(len(entries), 2)
        self.assertEqual(entries[0].find("a:link", ns).get("href"), "https://raj-rangani.github.io/kiasi/releases/#v0.2.0")
        self.assertIn("&lt;script&gt;", entries[0].find("a:content", ns).text)


if __name__ == "__main__":
    unittest.main()
