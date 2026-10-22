"""Public README assets, links, and architecture graphic stay usable on GitHub."""
from __future__ import annotations

from pathlib import Path
import re
import unittest
import xml.etree.ElementTree as ET

from PIL import Image


ROOT = Path(__file__).resolve().parents[1]


class ReadmeTests(unittest.TestCase):
    def test_overview_is_short_and_shows_the_new_purple_asset_and_diagram(self):
        text = (ROOT / "README.md").read_text(encoding="utf-8")
        self.assertLess(len(text.split()), 650)
        self.assertIn('src="assets/readme/helper-orb-purple.png"', text)
        self.assertNotIn('src="assets/helper_logo.png"', text)
        self.assertRegex(text, r"!\[[^\]]+\]\(assets/readme/how-helper-works\.svg\)")
        self.assertIn("## Get started", text)
        self.assertIn("## Know the limits", text)

    def test_local_links_in_overview_and_usage_guide_resolve(self):
        for document in (ROOT / "README.md", ROOT / "docs" / "USAGE.md"):
            text = document.read_text(encoding="utf-8")
            links = re.findall(r"\[[^\]]*\]\(([^)]+)\)", text)
            links += re.findall(r'(?:src|href)="([^"]+)"', text)
            for link in links:
                if link.startswith(("https://", "http://", "#")):
                    continue
                target = link.split("#", 1)[0]
                with self.subTest(document=document.name, target=target):
                    path = (document.parent / target).resolve()
                    self.assertTrue(path.is_relative_to(ROOT))
                    self.assertTrue(path.is_file(), str(path))

    def test_web_thumbnail_is_standard_rgba_and_matches_the_purple_app_artwork(self):
        with Image.open(ROOT / "assets" / "helper_logo.png") as source, Image.open(
            ROOT / "assets" / "readme" / "helper-orb-purple.png"
        ) as thumbnail:
            self.assertEqual(thumbnail.mode, "RGBA")
            self.assertEqual(thumbnail.size, (360, 360))
            expected = source.convert("RGBA").resize((360, 360), Image.Resampling.LANCZOS)
            self.assertEqual(thumbnail.tobytes(), expected.tobytes())
            red, green, blue, alpha = thumbnail.getpixel((180, 274))
            self.assertGreater(red, green + 10)
            self.assertGreater(blue, green + 30)
            self.assertEqual(alpha, 255)
            self.assertEqual(thumbnail.getpixel((0, 0))[3], 0)

    def test_svg_exposes_the_action_loop_and_separate_completion_path(self):
        path = ROOT / "assets" / "readme" / "how-helper-works.svg"
        root = ET.parse(path).getroot()
        ids = {node.get("id") for node in root.iter()}
        self.assertTrue({"observe", "decide", "validate", "act", "verify", "report",
                         "feedback-loop", "finish-path"} <= ids)
        ns = {"svg": "http://www.w3.org/2000/svg"}
        self.assertTrue(root.find("svg:title", ns).text)
        self.assertIn("separate whole-goal verifier", root.find("svg:desc", ns).text)
        for node in root.iter():
            self.assertNotIn(node.tag.rsplit("}", 1)[-1], {"script", "foreignObject", "image"})
            self.assertFalse(any(key.lower().startswith("on") for key in node.attrib))


if __name__ == "__main__":
    unittest.main()
