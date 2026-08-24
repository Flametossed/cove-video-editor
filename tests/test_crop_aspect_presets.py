import sys
import unittest
from pathlib import Path

# Ensure local src/ is prioritized
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from PySide6.QtCore import QPointF, QRectF
from PySide6.QtWidgets import QApplication

# Ensure QApplication exists for Qt widget tests
_app = QApplication.instance() or QApplication(sys.argv[:1])

from cove_video_editor.crop_overlay import CROP_ASPECT_PRESETS, CropOverlay


class TestCropAspectPresets(unittest.TestCase):
    def setUp(self) -> None:
        self.overlay = CropOverlay()
        self.overlay.resize(800, 450)

    def test_presets_dictionary_completeness(self) -> None:
        """Verify all standard aspect ratio presets are defined."""
        expected_keys = [
            "Free (Custom)",
            "16:9 (Landscape / YouTube)",
            "9:16 (TikTok / Reels / Shorts)",
            "1:1 (Square / Instagram)",
            "4:5 (Portrait / Social)",
            "4:3 (Standard / Classic)",
            "21:9 (Cinematic / Ultrawide)",
        ]
        for key in expected_keys:
            self.assertIn(key, CROP_ASPECT_PRESETS)

        self.assertIsNone(CROP_ASPECT_PRESETS["Free (Custom)"])
        self.assertAlmostEqual(CROP_ASPECT_PRESETS["16:9 (Landscape / YouTube)"], 16 / 9, places=4)
        self.assertAlmostEqual(CROP_ASPECT_PRESETS["9:16 (TikTok / Reels / Shorts)"], 9 / 16, places=4)
        self.assertAlmostEqual(CROP_ASPECT_PRESETS["1:1 (Square / Instagram)"], 1.0, places=4)
        self.assertAlmostEqual(CROP_ASPECT_PRESETS["4:5 (Portrait / Social)"], 0.8, places=4)
        self.assertAlmostEqual(CROP_ASPECT_PRESETS["4:3 (Standard / Classic)"], 4 / 3, places=4)
        self.assertAlmostEqual(CROP_ASPECT_PRESETS["21:9 (Cinematic / Ultrawide)"], 21 / 9, places=4)

    def test_16x9_source_to_tiktok_9x16_crop(self) -> None:
        """From a 16:9 video, setting 9:16 crop should fit full height and center vertically."""
        self.overlay.set_video_aspect(16 / 9)
        self.overlay.set_aspect_ratio_preset(9 / 16, "9:16 (TikTok / Reels / Shorts)")

        r = self.overlay.normalized_rect()
        expected_norm_ar = (9 / 16) / (16 / 9)  # 81 / 256 ≈ 0.3164

        self.assertAlmostEqual(r.height(), 1.0, places=4)
        self.assertAlmostEqual(r.width(), expected_norm_ar, places=4)
        self.assertAlmostEqual(r.y(), 0.0, places=4)
        self.assertAlmostEqual(r.x(), (1.0 - expected_norm_ar) / 2.0, places=4)

        # Check effective pixel aspect ratio
        pixel_w = r.width() * 1920
        pixel_h = r.height() * 1080
        self.assertAlmostEqual(pixel_w / pixel_h, 9 / 16, places=4)

    def test_16x9_source_to_square_1x1_crop(self) -> None:
        """From a 16:9 video, setting 1:1 square crop should fit full height and 1080x1080 equivalent."""
        self.overlay.set_video_aspect(16 / 9)
        self.overlay.set_aspect_ratio_preset(1.0, "1:1 (Square / Instagram)")

        r = self.overlay.normalized_rect()
        expected_norm_w = 9 / 16  # 0.5625

        self.assertAlmostEqual(r.height(), 1.0, places=4)
        self.assertAlmostEqual(r.width(), expected_norm_w, places=4)
        self.assertAlmostEqual(r.y(), 0.0, places=4)
        self.assertAlmostEqual(r.x(), (1.0 - expected_norm_w) / 2.0, places=4)

        pixel_w = r.width() * 1920
        pixel_h = r.height() * 1080
        self.assertAlmostEqual(pixel_w / pixel_h, 1.0, places=4)

    def test_9x16_source_to_landscape_16x9_crop(self) -> None:
        """From a 9:16 vertical video, setting 16:9 landscape crop should fit full width and center horizontally."""
        self.overlay.set_video_aspect(9 / 16)
        self.overlay.set_aspect_ratio_preset(16 / 9, "16:9 (Landscape / YouTube)")

        r = self.overlay.normalized_rect()
        expected_norm_h = (9 / 16) / (16 / 9)  # 81 / 256

        self.assertAlmostEqual(r.width(), 1.0, places=4)
        self.assertAlmostEqual(r.height(), expected_norm_h, places=4)
        self.assertAlmostEqual(r.x(), 0.0, places=4)
        self.assertAlmostEqual(r.y(), (1.0 - expected_norm_h) / 2.0, places=4)

        pixel_w = r.width() * 1080
        pixel_h = r.height() * 1920
        self.assertAlmostEqual(pixel_w / pixel_h, 16 / 9, places=4)

    def test_aspect_preservation_during_corner_drag(self) -> None:
        """Dragging corner handle preserves target aspect ratio."""
        self.overlay.set_video_aspect(16 / 9)
        self.overlay.set_aspect_ratio_preset(9 / 16, "9:16 (TikTok / Reels / Shorts)")

        # Simulate start of drag on bottom-right handle
        self.overlay._drag_target = "br"
        self.overlay._drag_start_widget = QPointF(500, 400)
        self.overlay._drag_start_rect = QRectF(self.overlay.normalized_rect())

        # Drag inward to shrink
        self.overlay._apply_drag(QPointF(450, 350))

        r = self.overlay.normalized_rect()
        norm_ar = (9 / 16) / (16 / 9)
        self.assertAlmostEqual(r.width() / r.height(), norm_ar, places=3)

    def test_aspect_preservation_during_edge_drag(self) -> None:
        """Dragging right edge handle proportionally adjusts height to maintain aspect ratio."""
        self.overlay.set_video_aspect(16 / 9)
        self.overlay.set_aspect_ratio_preset(1.0, "1:1 (Square / Instagram)")

        self.overlay._drag_target = "r"
        self.overlay._drag_start_widget = QPointF(500, 200)
        self.overlay._drag_start_rect = QRectF(self.overlay.normalized_rect())

        # Drag right edge outward
        self.overlay._apply_drag(QPointF(530, 200))

        r = self.overlay.normalized_rect()
        norm_ar = 1.0 / (16 / 9)  # 9/16 = 0.5625
        self.assertAlmostEqual(r.width() / r.height(), norm_ar, places=3)

    def test_reset_clears_preset_and_restores_full_bounds(self) -> None:
        """Reset clears aspect ratio lock and returns to normalized (0, 0, 1, 1)."""
        self.overlay.set_video_aspect(16 / 9)
        self.overlay.set_aspect_ratio_preset(9 / 16, "9:16 (TikTok / Reels / Shorts)")

        self.overlay.reset()

        self.assertIsNone(self.overlay.aspect_ratio_preset())
        self.assertEqual(self.overlay.normalized_rect(), QRectF(0.0, 0.0, 1.0, 1.0))


if __name__ == "__main__":
    unittest.main()
