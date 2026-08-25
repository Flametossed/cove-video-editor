from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from cove_video_editor.clip import Clip, MediaAsset
from cove_video_editor.exporter import (
    RESOLUTION_PRESETS,
    ExportJob,
    ExportWorker,
)


def _asset(name: str, *, w: int, h: int, kind: str = "video") -> MediaAsset:
    return MediaAsset(
        path=Path(name),
        duration=1.0,
        width=w,
        height=h,
        fps=30.0,
        has_audio=False,
        kind=kind,
    )


def _resolved_size(job: ExportJob) -> tuple[int, int]:
    """Run _build_command and extract the target WxH from the filtergraph."""
    worker = ExportWorker(job)
    cmd = worker._build_command()
    # The filter_complex contains scale=WxH / color=c=black:s=WxH entries.
    # Grab the first scale=...:... occurrence to read the target size.
    fc = cmd[cmd.index("-filter_complex") + 1]
    import re
    m = re.search(r"scale=(\d+):(\d+)", fc)
    if m:
        return int(m.group(1)), int(m.group(2))
    m = re.search(r"color=c=black:s=(\d+)x(\d+)", fc)
    if m:
        return int(m.group(1)), int(m.group(2))
    raise AssertionError(f"could not find output size in filter graph: {fc}")


class ExporterResolutionTests(unittest.TestCase):
    def test_presets_include_source_and_common_sizes(self) -> None:
        self.assertIn("Source", RESOLUTION_PRESETS)
        self.assertIsNone(RESOLUTION_PRESETS["Source"])
        self.assertEqual(RESOLUTION_PRESETS["1080p"], 1080)
        self.assertEqual(RESOLUTION_PRESETS["720p"], 720)

    def test_source_leaves_canvas_unchanged(self) -> None:
        clip = Clip(_asset("v.mp4", w=1280, h=720), timeline_start=0.0)
        job = ExportJob(clips=[clip], output=Path("out.mp4"), fmt_key="MP4 (H.264 + AAC)")
        self.assertEqual(_resolved_size(job), (1280, 720))

    def test_1080p_scales_landscape_preserving_aspect(self) -> None:
        # Long-edge scaling: the preset value IS the long edge. For a 16:9
        # landscape source the long edge is the width, so 1080p → 1080x608.
        clip = Clip(_asset("v.mp4", w=1280, h=720), timeline_start=0.0)
        job = ExportJob(
            clips=[clip], output=Path("out.mp4"), fmt_key="MP4 (H.264 + AAC)",
            resolution="1080p",
        )
        w, h = _resolved_size(job)
        self.assertEqual(max(w, h), 1080)
        self.assertEqual(w, 1080)
        self.assertEqual(h, 608)

    def test_720p_scales_landscape(self) -> None:
        clip = Clip(_asset("v.mp4", w=1920, h=1080), timeline_start=0.0)
        job = ExportJob(
            clips=[clip], output=Path("out.mp4"), fmt_key="MP4 (H.264 + AAC)",
            resolution="720p",
        )
        w, h = _resolved_size(job)
        self.assertEqual(max(w, h), 720)
        self.assertEqual(w, 720)
        self.assertEqual(h, 404)

    def test_portrait_stays_portrait(self) -> None:
        # 9:16 portrait source: the long edge is the height, so 1080p keeps
        # the portrait orientation with height 1080.
        clip = Clip(_asset("v.mp4", w=1080, h=1920), timeline_start=0.0)
        job = ExportJob(
            clips=[clip], output=Path("out.mp4"), fmt_key="MP4 (H.264 + AAC)",
            resolution="1080p",
        )
        w, h = _resolved_size(job)
        self.assertEqual(max(w, h), 1080)
        self.assertEqual(w, 608)
        self.assertEqual(h, 1080)

    def test_upscale_from_small_source(self) -> None:
        # 640x360 upscaled so the long edge (width) reaches 1080.
        clip = Clip(_asset("v.mp4", w=640, h=360), timeline_start=0.0)
        job = ExportJob(
            clips=[clip], output=Path("out.mp4"), fmt_key="MP4 (H.264 + AAC)",
            resolution="1080p",
        )
        w, h = _resolved_size(job)
        self.assertEqual(max(w, h), 1080)
        self.assertEqual(w, 1080)
        self.assertEqual(h, 608)

    def test_dimensions_are_even(self) -> None:
        # Odd source dimensions must still yield even output dims.
        clip = Clip(_asset("v.mp4", w=1279, h=719), timeline_start=0.0)
        job = ExportJob(
            clips=[clip], output=Path("out.mp4"), fmt_key="MP4 (H.264 + AAC)",
            resolution="720p",
        )
        w, h = _resolved_size(job)
        self.assertEqual(w % 2, 0)
        self.assertEqual(h % 2, 0)
        self.assertEqual(max(w, h), 720)

    def test_unknown_resolution_falls_back_to_source(self) -> None:
        clip = Clip(_asset("v.mp4", w=1280, h=720), timeline_start=0.0)
        job = ExportJob(
            clips=[clip], output=Path("out.mp4"), fmt_key="MP4 (H.264 + AAC)",
            resolution="Bogus",
        )
        self.assertEqual(_resolved_size(job), (1280, 720))


if __name__ == "__main__":
    unittest.main()