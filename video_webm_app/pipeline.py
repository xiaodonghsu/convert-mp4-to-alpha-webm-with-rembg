from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Callable

from rembg import new_session, remove

StatusCallback = Callable[[str], None]
ProgressCallback = Callable[[float, str], None]


class ConversionError(RuntimeError):
    pass


@dataclass(slots=True)
class ConversionConfig:
    input_path: Path
    output_path: Path
    temp_root: Path | None = None
    keep_temp: bool = False
    crf: int = 28
    audio_bitrate: str = "128k"
    model_name: str = "u2net"


class VideoConverter:
    def __init__(
        self,
        status_callback: StatusCallback | None = None,
        progress_callback: ProgressCallback | None = None,
    ) -> None:
        self._status = status_callback or (lambda _: None)
        self._progress = progress_callback or (lambda _value, _message: None)

    def convert(self, config: ConversionConfig) -> Path:
        self._ensure_binary("ffmpeg")
        self._ensure_binary("ffprobe")

        temp_dir_manager: TemporaryDirectory[str] | None = None
        if config.temp_root is None:
            temp_dir_manager = TemporaryDirectory(prefix="rembg-webm-")
            workspace = Path(temp_dir_manager.name)
        else:
            workspace = config.temp_root / f"{config.input_path.stem}_webm_work"
            workspace.mkdir(parents=True, exist_ok=True)

        try:
            return self._run_pipeline(config, workspace)
        finally:
            if temp_dir_manager is not None and not config.keep_temp:
                temp_dir_manager.cleanup()
            elif config.temp_root is not None and not config.keep_temp and workspace.exists():
                shutil.rmtree(workspace, ignore_errors=True)

    def _run_pipeline(self, config: ConversionConfig, workspace: Path) -> Path:
        frames_dir = workspace / "frames"
        processed_dir = workspace / "processed"
        audio_path = workspace / "audio.webm"
        frames_dir.mkdir(parents=True, exist_ok=True)
        processed_dir.mkdir(parents=True, exist_ok=True)
        config.output_path.parent.mkdir(parents=True, exist_ok=True)

        fps = self._probe_fps(config.input_path)
        self._progress(0.05, "Extracting PNG frames")
        self._extract_frames(config.input_path, frames_dir)

        self._progress(0.2, "Extracting audio")
        has_audio = self._extract_audio(config.input_path, audio_path, config.audio_bitrate)

        self._progress(0.3, "Removing background with rembg")
        self._remove_background(frames_dir, processed_dir, config.model_name)

        self._progress(0.9, "Muxing WebM")
        self._compose_webm(processed_dir, audio_path if has_audio else None, config.output_path, fps, config.crf)
        self._progress(1.0, "Completed")
        self._status(f"Output written to: {config.output_path}")
        if config.keep_temp:
            self._status(f"Temporary files kept at: {workspace}")
        return config.output_path

    def _ensure_binary(self, binary_name: str) -> None:
        if shutil.which(binary_name) is None:
            raise ConversionError(
                f"Required binary '{binary_name}' was not found in PATH. Install FFmpeg first."
            )

    def _probe_fps(self, input_path: Path) -> Fraction:
        result = self._run_command(
            [
                "ffprobe",
                "-v",
                "error",
                "-select_streams",
                "v:0",
                "-show_entries",
                "stream=avg_frame_rate",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                str(input_path),
            ],
            "Reading video metadata failed",
            capture_output=True,
        )
        fps_raw = result.stdout.strip()
        if not fps_raw or fps_raw == "0/0":
            raise ConversionError("Unable to determine source frame rate.")
        return Fraction(fps_raw)

    def _extract_frames(self, input_path: Path, frames_dir: Path) -> None:
        self._run_command(
            [
                "ffmpeg",
                "-y",
                "-i",
                str(input_path),
                str(frames_dir / "frame-%06d.png"),
            ],
            "Frame extraction failed",
        )

    def _extract_audio(self, input_path: Path, audio_path: Path, audio_bitrate: str) -> bool:
        result = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-select_streams",
                "a:0",
                "-show_entries",
                "stream=index",
                "-of",
                "csv=p=0",
                str(input_path),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0 or not result.stdout.strip():
            self._status("No audio stream detected. Output WebM will be silent.")
            return False

        self._run_command(
            [
                "ffmpeg",
                "-y",
                "-i",
                str(input_path),
                "-vn",
                "-c:a",
                "libopus",
                "-b:a",
                audio_bitrate,
                str(audio_path),
            ],
            "Audio extraction failed",
        )
        return True

    def _remove_background(self, frames_dir: Path, processed_dir: Path, model_name: str) -> None:
        frame_paths = sorted(frames_dir.glob("frame-*.png"))
        if not frame_paths:
            raise ConversionError("No frames were extracted from the input video.")

        session = new_session(model_name=model_name)
        total = len(frame_paths)
        for index, frame_path in enumerate(frame_paths, start=1):
            with frame_path.open("rb") as source:
                result = remove(source.read(), session=session)

            output_path = processed_dir / frame_path.name
            with output_path.open("wb") as target:
                target.write(result)

            percent = 0.3 + (index / total) * 0.55
            self._progress(percent, f"Removing background: {index}/{total}")

    def _compose_webm(
        self,
        processed_dir: Path,
        audio_path: Path | None,
        output_path: Path,
        fps: Fraction,
        crf: int,
    ) -> None:
        command = [
            "ffmpeg",
            "-y",
            "-framerate",
            str(fps),
            "-i",
            str(processed_dir / "frame-%06d.png"),
        ]
        if audio_path is not None:
            command.extend(["-i", str(audio_path)])

        command.extend(
            [
                "-c:v",
                "libvpx-vp9",
                "-pix_fmt",
                "yuva420p",
                "-auto-alt-ref",
                "0",
                "-b:v",
                "0",
                "-crf",
                str(crf),
            ]
        )

        if audio_path is not None:
            command.extend(["-c:a", "copy"])

        command.append(str(output_path))
        self._run_command(command, "WebM composition failed")

    def _run_command(
        self,
        command: list[str],
        error_message: str,
        *,
        capture_output: bool = False,
    ) -> subprocess.CompletedProcess[str]:
        self._status("Running: " + " ".join(command))
        result = subprocess.run(
            command,
            capture_output=capture_output,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            details = result.stderr.strip() if result.stderr else ""
            if details:
                raise ConversionError(f"{error_message}: {details}")
            raise ConversionError(error_message)
        return result
