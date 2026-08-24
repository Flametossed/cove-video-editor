"""Unit tests for the hardware-accelerated video encoding paths (NVENC & AMF).

These exercise the configuration maps, the encoder-argument builder, the
availability probe's caching and graceful-failure behaviour, and the export
command resolver. None of these need a physical NVIDIA or AMD GPU present.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from cove_video_editor import ffmpeg_utils as ff  # noqa: E402
from cove_video_editor.clip import Clip, MediaAsset  # noqa: E402
from cove_video_editor.exporter import (  # noqa: E402
    ExportJob,
    ExportWorker,
    build_export_video_encoder_args,
)


def _val(args: list[str], flag: str) -> str | None:
    """Return the token following `flag` in an ffmpeg arg list, or None."""
    for i, tok in enumerate(args[:-1]):
        if tok == flag:
            return args[i + 1]
    return None


class ConfigConsistencyTest(unittest.TestCase):
    def test_encoder_key_map_covers_all_options(self):
        self.assertEqual(set(ff.ENCODER_KEY_MAP), set(ff.ENCODER_OPTIONS))
        self.assertEqual(
            set(ff.ENCODER_KEY_MAP.values()), {"auto", "cpu", "nvenc", "amf"}
        )

    def test_formats_hardware_codec_naming(self):
        for name, spec in ff.EXPORT_FORMATS.items():
            if spec.get("nvenc_codec"):
                self.assertTrue(
                    spec["nvenc_codec"].endswith("_nvenc"),
                    f"{name} nvenc_codec does not end with _nvenc: {spec['nvenc_codec']}",
                )
            if spec.get("amf_codec"):
                self.assertTrue(
                    spec["amf_codec"].endswith("_amf"),
                    f"{name} amf_codec does not end with _amf: {spec['amf_codec']}",
                )

    def test_audio_and_unsupported_formats_have_no_gpu_codecs(self):
        for name in ("WebM (VP9 + Opus)", "AVI (MPEG-4 + MP3)", "GIF (animation)", "MP3 (audio only)"):
            spec = ff.EXPORT_FORMATS[name]
            self.assertIsNone(spec.get("nvenc_codec"), f"{name} should not have nvenc_codec")
            self.assertIsNone(spec.get("amf_codec"), f"{name} should not have amf_codec")


class EncoderArgsBuilderTest(unittest.TestCase):
    def test_nvenc_h264_args(self):
        args = build_export_video_encoder_args("h264_nvenc", fps=60)
        self.assertEqual(_val(args, "-c:v"), "h264_nvenc")
        self.assertEqual(_val(args, "-preset"), "p6")
        self.assertEqual(_val(args, "-tune"), "hq")
        self.assertEqual(_val(args, "-rc"), "vbr")
        self.assertEqual(_val(args, "-cq"), "22")
        self.assertEqual(_val(args, "-b:v"), "0")
        self.assertEqual(_val(args, "-r"), "60")
        self.assertNotIn("-crf", args)

    def test_nvenc_hevc_args(self):
        args = build_export_video_encoder_args("hevc_nvenc", fps=None)
        self.assertEqual(_val(args, "-c:v"), "hevc_nvenc")
        self.assertEqual(_val(args, "-preset"), "p6")
        self.assertEqual(_val(args, "-tune"), "hq")
        self.assertEqual(_val(args, "-rc"), "vbr")
        self.assertEqual(_val(args, "-cq"), "26")
        self.assertEqual(_val(args, "-b:v"), "0")
        self.assertNotIn("-r", args)

    def test_amf_h264_args(self):
        args = build_export_video_encoder_args("h264_amf", fps=30)
        self.assertEqual(_val(args, "-c:v"), "h264_amf")
        self.assertEqual(_val(args, "-quality"), "balanced")
        self.assertEqual(_val(args, "-usage"), "transcoding")
        self.assertEqual(_val(args, "-rc"), "cqp")
        self.assertEqual(_val(args, "-qp"), "23")
        self.assertEqual(_val(args, "-r"), "30")
        self.assertNotIn("-crf", args)

    def test_amf_hevc_args(self):
        args = build_export_video_encoder_args("hevc_amf", fps=None)
        self.assertEqual(_val(args, "-c:v"), "hevc_amf")
        self.assertEqual(_val(args, "-quality"), "balanced")
        self.assertEqual(_val(args, "-usage"), "transcoding")
        self.assertEqual(_val(args, "-rc"), "cqp")
        self.assertEqual(_val(args, "-qp"), "27")

    def test_cpu_x264_args(self):
        args = build_export_video_encoder_args("libx264", fps=24)
        self.assertEqual(
            args,
            ["-c:v", "libx264", "-crf", "20", "-preset", "medium", "-r", "24"],
        )

    def test_cpu_x265_args(self):
        args = build_export_video_encoder_args("libx265", fps=None)
        self.assertEqual(
            args,
            ["-c:v", "libx265", "-crf", "24", "-preset", "medium"],
        )


class ProbeAvailabilityCacheTest(unittest.TestCase):
    def test_missing_ffmpeg_reports_unavailable_nvenc(self):
        with patch.object(ff, "require_ffmpeg", side_effect=ff.FFmpegMissingError("no ffmpeg")):
            ff._nvenc_cache.clear()
            try:
                self.assertFalse(ff.nvenc_available("hevc_nvenc"))
                self.assertFalse(ff.nvenc_available("hevc_nvenc"))
                self.assertIn("hevc_nvenc", ff._nvenc_cache)
            finally:
                ff._nvenc_cache.clear()

    def test_missing_ffmpeg_reports_unavailable_amf(self):
        with patch.object(ff, "require_ffmpeg", side_effect=ff.FFmpegMissingError("no ffmpeg")):
            ff._amf_cache.clear()
            try:
                self.assertFalse(ff.amf_available("hevc_amf"))
                self.assertFalse(ff.amf_available("hevc_amf"))
                self.assertIn("hevc_amf", ff._amf_cache)
            finally:
                ff._amf_cache.clear()


class ExporterCommandResolutionTest(unittest.TestCase):
    def _dummy_clip(self) -> Clip:
        asset = MediaAsset(
            id="a1", path=Path("test.mp4"), kind="video",
            duration=5.0, width=1920, height=1080, fps=30.0, has_audio=True,
        )
        return Clip(
            id="c1", asset=asset, timeline_start=0.0,
            src_start=0.0, src_end=5.0,
        )

    def test_forced_cpu_preference_uses_software_encoder(self):
        job = ExportJob(
            clips=[self._dummy_clip()],
            output=Path("out.mp4"),
            fmt_key="MP4 (H.264 + AAC)",
            encoder_pref="cpu",
        )
        worker = ExportWorker(job)
        with patch.object(ff, "nvenc_available", return_value=True), \
             patch.object(ff, "amf_available", return_value=True):
            cmd = worker._build_command()
            self.assertEqual(_val(cmd, "-c:v"), "libx264")

    def test_auto_prefers_nvenc_when_available(self):
        job = ExportJob(
            clips=[self._dummy_clip()],
            output=Path("out.mp4"),
            fmt_key="MP4 (H.264 + AAC)",
            encoder_pref="auto",
        )
        worker = ExportWorker(job)
        with patch.object(ff, "nvenc_available", return_value=True), \
             patch.object(ff, "amf_available", return_value=True):
            cmd = worker._build_command()
            self.assertEqual(_val(cmd, "-c:v"), "h264_nvenc")

    def test_auto_falls_back_to_amf_when_nvenc_unavailable(self):
        job = ExportJob(
            clips=[self._dummy_clip()],
            output=Path("out.mp4"),
            fmt_key="MP4 (H.264 + AAC)",
            encoder_pref="auto",
        )
        worker = ExportWorker(job)
        with patch.object(ff, "nvenc_available", return_value=False), \
             patch.object(ff, "amf_available", return_value=True):
            cmd = worker._build_command()
            self.assertEqual(_val(cmd, "-c:v"), "h264_amf")

    def test_auto_falls_back_to_cpu_when_no_gpu_available(self):
        job = ExportJob(
            clips=[self._dummy_clip()],
            output=Path("out.mp4"),
            fmt_key="MP4 (H.265 + AAC)",
            encoder_pref="auto",
        )
        worker = ExportWorker(job)
        with patch.object(ff, "nvenc_available", return_value=False), \
             patch.object(ff, "amf_available", return_value=False):
            cmd = worker._build_command()
            self.assertEqual(_val(cmd, "-c:v"), "libx265")

    def test_forced_nvenc_falls_back_gracefully_if_unavailable(self):
        job = ExportJob(
            clips=[self._dummy_clip()],
            output=Path("out.mp4"),
            fmt_key="MP4 (H.264 + AAC)",
            encoder_pref="nvenc",
        )
        worker = ExportWorker(job)
        with patch.object(ff, "nvenc_available", return_value=False):
            cmd = worker._build_command()
            self.assertEqual(_val(cmd, "-c:v"), "libx264")

    def test_vp9_always_uses_cpu_even_if_auto_or_nvenc(self):
        job = ExportJob(
            clips=[self._dummy_clip()],
            output=Path("out.webm"),
            fmt_key="WebM (VP9 + Opus)",
            encoder_pref="nvenc",
        )
        worker = ExportWorker(job)
        with patch.object(ff, "nvenc_available", return_value=True):
            cmd = worker._build_command()
            self.assertEqual(_val(cmd, "-c:v"), "libvpx-vp9")


if __name__ == "__main__":
    unittest.main()
