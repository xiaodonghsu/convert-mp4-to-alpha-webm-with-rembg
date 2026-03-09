# MP4 to Transparent WebM Desktop App

Python desktop application that converts an MP4 video into a transparent WebM file.

Pipeline:

1. Extract MP4 frames as PNG.
2. Extract audio and re-encode it to WebM-compatible Opus.
3. Remove frame backgrounds with `rembg` in CPU mode.
4. Merge transparent PNG frames and audio into a VP9 WebM.

## Requirements

- Python 3.12+
- `uv` for environment management
- `ffmpeg` and `ffprobe` available in `PATH`

## Install

```powershell
uv sync
```

`rembg` will run with the CPU extra because the project depends on `rembg[cpu]`.

## Run

```powershell
uv run python main.py
```

Or use the script entry point:

```powershell
uv run convert-webm-rembg
```

## Usage

1. Choose an input `.mp4` file.
2. Choose an output `.webm` path.
3. Optionally choose a temp directory if you want to inspect extracted frames.
4. Click `Start Conversion`.

## Notes

- The default `rembg` model is `u2net`.
- Lower `CRF` means larger output and better quality.
- Transparent WebM output uses VP9 with alpha (`yuva420p`).
- If the source video has no audio stream, the result will be silent.
- First run may be slower because `rembg` can download model assets.

## FFmpeg example installation

Make sure these commands work before running the app:

```powershell
ffmpeg -version
ffprobe -version
```

## package to .exe

```powershell
uv run pyinstaller --onefile --icon favicon.ico --name mp4-to-webm-with-alpha-channel main.py
```
