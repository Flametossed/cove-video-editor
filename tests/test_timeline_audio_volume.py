import unittest
from pathlib import Path
from PySide6.QtWidgets import QApplication

from cove_video_editor.clip import AddedAudio, Clip, MediaAsset
from cove_video_editor.exporter import AudioTrack, ExportJob, ExportWorker
from cove_video_editor.timeline_widget import TimelineWidget

# Ensure QApplication exists for widget testing
_app = QApplication.instance() or QApplication([])


class TestTimelineAudioVolume(unittest.TestCase):
    def setUp(self) -> None:
        self.timeline = TimelineWidget()
        self.asset = MediaAsset(
            path=Path("dummy.mp4"),
            duration=10.0,
            has_audio=True,
            width=1920,
            height=1080,
            fps=30.0,
        )
        self.clip = Clip(asset=self.asset, src_start=0.0, src_end=10.0, audio_volume=1.0)
        self.added_audio = AddedAudio(path=Path("bgm.mp3"), duration=30.0, volume=1.0)

    def test_added_audio_volume_fields(self) -> None:
        """AddedAudio contains volume and muted fields with standard defaults."""
        audio = AddedAudio(path=Path("test.mp3"), duration=15.0)
        self.assertEqual(audio.volume, 1.0)
        self.assertFalse(audio.muted)

        audio.volume = 1.5
        audio.muted = True
        self.assertEqual(audio.volume, 1.5)
        self.assertTrue(audio.muted)

    def test_clip_audio_volume_fields(self) -> None:
        """Clip contains audio_volume and muted fields."""
        c = Clip(asset=self.asset, audio_volume=0.5, muted=True)
        self.assertEqual(c.audio_volume, 0.5)
        self.assertTrue(c.muted)

    def test_timeline_volume_signals(self) -> None:
        """Timeline emits clipAudioVolumeChanged and addedAudioVolumeChanged."""
        received_clip: list[tuple[str, float]] = []
        received_audio: list[tuple[str, float]] = []

        self.timeline.clipAudioVolumeChanged.connect(lambda cid, v: received_clip.append((cid, v)))
        self.timeline.addedAudioVolumeChanged.connect(lambda aid, v: received_audio.append((aid, v)))

        self.timeline.clipAudioVolumeChanged.emit("c1", 1.25)
        self.timeline.addedAudioVolumeChanged.emit("a1", 0.75)

        self.assertEqual(received_clip, [("c1", 1.25)])
        self.assertEqual(received_audio, [("a1", 0.75)])

    def test_exporter_clip_volume_filter(self) -> None:
        """Exporter includes volume filter for clips with non-1.0 volume."""
        c_boost = Clip(asset=self.asset, src_start=0.0, src_end=5.0, audio_volume=1.5)
        job = ExportJob(
            clips=[c_boost],
            output=Path("out.mp4"),
            fmt_key="MP4 (H.264 + AAC)",
        )
        worker = ExportWorker(job)
        cmd = worker._build_command()
        # Find filter_complex argument
        fc_idx = cmd.index("-filter_complex")
        fc = cmd[fc_idx + 1]
        self.assertIn("volume=1.500", fc)

    def test_exporter_added_audio_volume_filter(self) -> None:
        """Exporter includes volume filter for added audio tracks."""
        track = AudioTrack(path=Path("music.mp3"), duration=10.0, volume=0.8)
        job = ExportJob(
            clips=[self.clip],
            output=Path("out.mp4"),
            fmt_key="MP4 (H.264 + AAC)",
            audio_tracks=[track],
        )
        worker = ExportWorker(job)
        cmd = worker._build_command()
        fc_idx = cmd.index("-filter_complex")
        fc = cmd[fc_idx + 1]
        self.assertIn("volume=0.800", fc)


if __name__ == "__main__":
    unittest.main()
