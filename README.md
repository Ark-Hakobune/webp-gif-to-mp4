# WEBP/GIF to MP4 Converter

A small command-line tool for converting `.webp` and `.gif` files in a folder to playable `.mp4` videos.

By default, each input file is converted into its own MP4 file. A merged MP4 can also be created with `--merge`.

## Features

- Converts animated WEBP and GIF files to MP4.
- Processes files in natural filename order, for example `1.webp`, `2.gif`, `10.webp`.
- Default mode creates one MP4 per input file.
- Optional `--merge` mode creates an additional merged MP4.
- Supports custom output directories for individual and merged MP4 files.
- Handles transparent frames with a configurable background color.
- Outputs widely compatible `H.264 + yuv420p` MP4 files.
- Validates output with `ffprobe` when available.

## Requirements

- Python 3.8+
- Pillow
- FFmpeg and FFprobe

Install Python dependency:

```bash
pip install -r requirements.txt
```

FFmpeg must be available in `PATH`, or placed next to the script as:

```text
ffmpeg/bin/ffmpeg.exe
ffmpeg/bin/ffprobe.exe
```

## Usage

Convert all `.webp` and `.gif` files in the current folder:

```bash
python convert_webp_gif_to_mp4.py
```

Convert files in a specific folder:

```bash
python convert_webp_gif_to_mp4.py "D:\input-folder"
```

Write individual MP4 files to another folder:

```bash
python convert_webp_gif_to_mp4.py "D:\input-folder" --output-dir "D:\mp4-output"
```

Also create a merged MP4:

```bash
python convert_webp_gif_to_mp4.py "D:\input-folder" --merge
```

Choose merged output directory and filename:

```bash
python convert_webp_gif_to_mp4.py "D:\input-folder" --merge --merge-output-dir "D:\merged-output" --merge-output final.mp4
```

Use smoother timing:

```bash
python convert_webp_gif_to_mp4.py "D:\input-folder" --fps 60
```

Use a white background for transparent frames:

```bash
python convert_webp_gif_to_mp4.py "D:\input-folder" --background white
```

## Options

| Option | Default | Description |
| --- | --- | --- |
| `folder` | `.` | Folder containing `.webp` and `.gif` files. |
| `--output-dir` | input folder | Directory for individual MP4 files. |
| `--merge` | off | Also create one merged MP4 in natural filename order. |
| `--merge-output-dir` | `--output-dir` | Directory for the merged MP4. |
| `--merge-output` | `merged.mp4` | Merged MP4 filename. |
| `--fps` | `30` | Constant output FPS. Use `60` for smoother timing. |
| `--crf` | `23` | x264 quality. Lower means better quality and larger files. |
| `--preset` | `veryfast` | x264 encoding preset, such as `ultrafast`, `veryfast`, `medium`. |
| `--background` | `black` | Background for transparent frames: `black`, `white`, `gray`, or `#RRGGBB`. |
| `--log` | off | Write `webp_gif_to_mp4.log`. |

## Notes

The tool uses Pillow to decode WEBP/GIF frames and sends PNG frames through a pipe to FFmpeg. This avoids writing temporary frame files to disk while keeping MP4 output compatible with common players.

Timing is approximated by repeating frames into a constant-FPS video stream. This is usually stable for GIF/WEBP animations and keeps output broadly playable.
