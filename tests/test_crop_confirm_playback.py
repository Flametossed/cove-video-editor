import sys
import unittest
from pathlib import Path

# Ensure local src/ is prioritized
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QKeyEvent, QMouseEvent
from PySide6.QtWidgets import QApplication

# Ensure QApplication exists for Qt widget tests
_app = QApplication.instance() or QApplication(sys.argv[:1])

from cove_video_editor.app import MainWindow, VideoView
from cove_video_editor.clip import Clip, MediaAsset, split_clip
from cove_video_editor.crop_overlay import CropOverlay
from cove_video_editor.exporter import ExportJob, ExportWorker


def _make_dummy_asset(path: str = "dummy.mp4", width: int = 1920, height: int = 1080, duration: float = 10.0) -> MediaAsset:
    return MediaAsset(
        path=Path(path),
        duration=duration,
        width=width,
        height=height,
        fps=30.0,
        has_audio=True,
    )


class TestCropConfirmPlayback(unittest.TestCase):
    def test_clip_crop_persistence_and_clone(self) -> None:
        """Verify Clip dataclass preserves crop_rect, preset, and fit_mode across clone and split."""
        asset = _make_dummy_asset()
        clip = Clip(
            asset=asset,
            timeline_start=0.0,
            src_start=0.0,
            src_end=10.0,
            crop_rect=(0.25, 0.0, 0.5, 1.0),
            crop_preset="9:16 (TikTok / Reels / Shorts)",
            crop_fit_mode="fill",
        )
        self.assertEqual(clip.crop_rect, (0.25, 0.0, 0.5, 1.0))
        self.assertEqual(clip.crop_preset, "9:16 (TikTok / Reels / Shorts)")
        self.assertEqual(clip.crop_fit_mode, "fill")

        cloned = clip.clone()
        self.assertEqual(cloned.crop_rect, (0.25, 0.0, 0.5, 1.0))
        self.assertEqual(cloned.crop_preset, "9:16 (TikTok / Reels / Shorts)")
        self.assertEqual(cloned.crop_fit_mode, "fill")

        # Split clip preserves crop
        right = split_clip(clip, 5.0)
        self.assertIsNotNone(right)
        self.assertEqual(right.crop_rect, (0.25, 0.0, 0.5, 1.0))
        self.assertEqual(clip.crop_rect, (0.25, 0.0, 0.5, 1.0))

    def test_crop_overlay_confirm_signals(self) -> None:
        """Verify CropOverlay emits confirmRequested on double click and Enter key."""
        overlay = CropOverlay()
        overlay.resize(800, 450)
        overlay.set_video_aspect(16 / 9)
        overlay.set_normalized_rect(QRectF(0.2, 0.2, 0.6, 0.6))

        confirm_emitted = []
        overlay.confirmRequested.connect(lambda: confirm_emitted.append(True))

        # Double click inside crop rect
        crop_px = overlay._crop_rect_widget()
        center_pt = crop_px.center()
        dbl_event = QMouseEvent(
            QMouseEvent.Type.MouseButtonDblClick,
            center_pt,
            center_pt,
            Qt.MouseButton.LeftButton,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
        )
        overlay.mouseDoubleClickEvent(dbl_event)
        self.assertEqual(len(confirm_emitted), 1)

        # Enter key press
        key_event = QKeyEvent(QKeyEvent.KeyPress, Qt.Key_Return, Qt.NoModifier)
        overlay.keyPressEvent(key_event)
        self.assertEqual(len(confirm_emitted), 2)

    def test_video_view_crop_framing(self) -> None:
        """Verify VideoView sets sceneRect to the cropped subregion when crop_rect is applied."""
        view = VideoView()
        view.resize(800, 450)

        # 1920x1080 uncropped
        view.update_canvas(1920, 1080, None, "fill", crop_rect=None)
        self.assertEqual(view._scene.sceneRect(), QRectF(0, 0, 1920, 1080))

        # Crop to center 960x1080 (0.25, 0.0, 0.5, 1.0)
        view.update_canvas(1920, 1080, 9 / 16, "fill", crop_rect=QRectF(0.25, 0.0, 0.5, 1.0))
        self.assertEqual(view._scene.sceneRect(), QRectF(480, 0, 960, 1080))

        # Reset crop
        view.update_canvas(1920, 1080, None, "fill", crop_rect=None)
        self.assertEqual(view._scene.sceneRect(), QRectF(0, 0, 1920, 1080))

    def test_main_window_crop_confirm_flow(self) -> None:
        """Verify crop toggling, confirming, reset, and crop_pixels extraction in MainWindow."""
        win = MainWindow()
        asset = _make_dummy_asset()
        clip = Clip(asset=asset, timeline_start=0.0, src_start=0.0, src_end=10.0)
        win._clips = [clip]
        win.timeline.set_clips(win._clips)
        win.timeline.select_clip(clip.id)
        win._set_preview_clip(clip)

        # Initially no crop
        self.assertIsNone(clip.crop_rect)
        self.assertIsNone(win._crop_pixels())
        self.assertEqual(win.crop_btn.text(), "Crop")

        # Open crop editing mode
        win.crop_btn.setChecked(True)
        self.assertFalse(win.crop_aspect_combo.isHidden())
        self.assertFalse(win.crop_confirm_btn.isHidden())
        self.assertFalse(win.crop_reset_btn.isHidden())

        # Set 9:16 aspect preset
        win.crop_aspect_combo.setCurrentText("9:16 (TikTok / Reels / Shorts)")
        norm_r = win.crop_overlay.normalized_rect()
        self.assertAlmostEqual(norm_r.height(), 1.0, places=3)
        self.assertAlmostEqual(norm_r.width(), (9 / 16) / (16 / 9), places=3)

        # Confirm crop
        win._on_crop_confirmed()

        # Check that editing mode closed and confirmed crop is stored on clip
        self.assertFalse(win.crop_btn.isChecked())
        self.assertTrue(win.crop_confirm_btn.isHidden())
        self.assertTrue(win.crop_overlay.isHidden())
        self.assertIsNotNone(clip.crop_rect)
        self.assertEqual(clip.crop_preset, "9:16 (TikTok / Reels / Shorts)")
        self.assertIn("9:16", win.crop_btn.text())

        # VideoView displays full video with active crop indicator & darkened outer regions
        expected_w = norm_r.width() * 1920
        self.assertEqual(win.video_view._scene.sceneRect(), QRectF(0, 0, 1920, 1080))
        self.assertTrue(win.video_view._crop_indicator_active)
        self.assertEqual(win.video_view._crop_indicator_rect, norm_r)
        self.assertEqual(win.video_view._crop_indicator_tag, "9:16")

        # _crop_pixels returns pixel bounds
        pixels = win._crop_pixels()
        self.assertIsNotNone(pixels)
        x, y, w, h = pixels
        self.assertEqual(h, 1080)
        expected_even_w = int(round(expected_w)) - (int(round(expected_w)) % 2)
        self.assertEqual(w, expected_even_w)

        # Reset crop clears crop and removes indicator
        win._on_crop_reset()
        self.assertIsNone(clip.crop_rect)
        self.assertFalse(win.video_view._crop_indicator_active)
        self.assertEqual(win.crop_btn.text(), "Crop")
        self.assertEqual(win.video_view._scene.sceneRect(), QRectF(0, 0, 1920, 1080))

    def test_exporter_per_clip_crop(self) -> None:
        """Verify exporter builds filtergraph honoring per-clip crop_rect."""
        asset = _make_dummy_asset()
        clip = Clip(
            asset=asset,
            timeline_start=0.0,
            src_start=0.0,
            src_end=10.0,
            crop_rect=(0.25, 0.0, 0.5, 1.0),
            crop_preset="9:16 (TikTok / Reels / Shorts)",
            crop_fit_mode="fill",
        )
        job = ExportJob(
            clips=[clip],
            output=Path("out.mp4"),
            fmt_key="MP4 (H.264 + AAC)",
            canvas_fit="fill",
            canvas_aspect=9 / 16,
        )
        worker = ExportWorker(job)
        cmd = worker._build_command()
        cmd_str = " ".join(cmd)

        # Should contain crop=960:1080:480:0
        self.assertIn("crop=960:1080:480:0", cmd_str)


if __name__ == "__main__":
    unittest.main()
