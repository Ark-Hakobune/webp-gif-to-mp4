import argparse
import concurrent.futures
import io
import logging
import os
import re
import shutil
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Iterable, List, Optional, Tuple

from PIL import Image, ImageSequence


SUPPORTED_EXTENSIONS = {".webp", ".gif"}


def configure_logging(log_to_file: bool = False) -> None:
    handlers = [logging.StreamHandler()]
    if log_to_file:
        handlers.append(logging.FileHandler("webp_gif_to_mp4.log", encoding="utf-8"))

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(levelname)s - %(message)s",
        handlers=handlers,
    )


def natural_sort_key(path: Path):
    return [
        int(part) if part.isdigit() else part.lower()
        for part in re.split(r"(\d+)", path.name)
    ]


def find_ffmpeg(script_dir: Path) -> str:
    local_ffmpeg = script_dir / "ffmpeg" / "bin" / "ffmpeg.exe"
    if local_ffmpeg.exists():
        return str(local_ffmpeg)

    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg:
        return ffmpeg

    raise RuntimeError(
        "FFmpeg was not found. Install FFmpeg and reopen the terminal, "
        "or put ffmpeg.exe in ffmpeg/bin next to this script."
    )


def find_ffprobe(script_dir: Path) -> Optional[str]:
    local_ffprobe = script_dir / "ffmpeg" / "bin" / "ffprobe.exe"
    if local_ffprobe.exists():
        return str(local_ffprobe)
    return shutil.which("ffprobe")


def list_input_files(folder: Path) -> List[Path]:
    files = [
        path
        for path in folder.iterdir()
        if path.is_file() and path.suffix.lower() in SUPPORTED_EXTENSIONS
    ]
    return sorted(files, key=natural_sort_key)


def get_image_size(path: Path) -> Tuple[int, int]:
    with Image.open(path) as image:
        return image.size


def get_canvas_size(files: Iterable[Path]) -> Tuple[int, int]:
    max_width = 0
    max_height = 0
    for path in files:
        width, height = get_image_size(path)
        max_width = max(max_width, width)
        max_height = max(max_height, height)

    if max_width <= 0 or max_height <= 0:
        raise RuntimeError("Could not determine output size.")

    if max_width % 2:
        max_width += 1
    if max_height % 2:
        max_height += 1

    return max_width, max_height


def frame_duration_ms(frame: Image.Image, image: Image.Image, default_duration: int) -> int:
    duration = frame.info.get("duration")
    if duration is None:
        duration = image.info.get("duration")
    if not duration or duration <= 0:
        duration = default_duration
    return max(1, int(duration))


def frame_repeat_count(duration_ms: int, fps: float) -> int:
    return max(1, int(round(duration_ms * fps / 1000.0)))


def parse_color(value: str) -> Tuple[int, int, int]:
    named = {
        "black": (0, 0, 0),
        "white": (255, 255, 255),
        "gray": (128, 128, 128),
        "grey": (128, 128, 128),
    }

    lowered = value.strip().lower()
    if lowered in named:
        return named[lowered]

    match = re.fullmatch(r"#?([0-9a-fA-F]{6})", lowered)
    if not match:
        raise ValueError("Background must be black, white, gray, or a hex color like #000000.")

    hex_value = match.group(1)
    return (
        int(hex_value[0:2], 16),
        int(hex_value[2:4], 16),
        int(hex_value[4:6], 16),
    )


def frame_to_canvas_png_bytes(
    frame: Image.Image,
    canvas_size: Tuple[int, int],
    background_rgb: Tuple[int, int, int],
) -> bytes:
    canvas_width, canvas_height = canvas_size
    rgba = frame.convert("RGBA")
    frame_width, frame_height = rgba.size

    background = Image.new("RGBA", (canvas_width, canvas_height), (*background_rgb, 255))
    x = (canvas_width - frame_width) // 2
    y = (canvas_height - frame_height) // 2
    background.alpha_composite(rgba, (x, y))

    buffer = io.BytesIO()
    background.convert("RGB").save(buffer, format="PNG", optimize=False)
    return buffer.getvalue()


def iter_video_frames(
    files: Iterable[Path],
    canvas_size: Tuple[int, int],
    fps: float,
    background_rgb: Tuple[int, int, int],
) -> Iterable[bytes]:
    default_duration = max(1, int(round(1000.0 / fps)))

    for path in files:
        logging.info("Reading %s", path.name)
        with Image.open(path) as image:
            for frame in ImageSequence.Iterator(image):
                duration_ms = frame_duration_ms(frame, image, default_duration)
                repeat = frame_repeat_count(duration_ms, fps)
                frame_bytes = frame_to_canvas_png_bytes(frame, canvas_size, background_rgb)
                for _ in range(repeat):
                    yield frame_bytes


def validate_mp4(ffprobe: Optional[str], output_path: Path) -> bool:
    if not ffprobe:
        logging.warning("ffprobe was not found, skipping output validation.")
        return output_path.exists() and output_path.stat().st_size > 0

    result = subprocess.run(
        [
            ffprobe,
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=codec_name,width,height,pix_fmt",
            "-of",
            "default=nw=1",
            str(output_path),
        ],
        text=True,
        capture_output=True,
    )

    if result.returncode != 0:
        logging.error("Output validation failed: %s", result.stderr.strip())
        return False

    info = result.stdout.strip()
    logging.info("Output validation passed:\n%s", info)
    return "codec_name=h264" in info and "pix_fmt=yuv420p" in info


def write_mp4(
    ffmpeg: str,
    ffprobe: Optional[str],
    frames: Iterable[bytes],
    output_path: Path,
    fps: float,
    crf: int,
    preset: str,
    ffmpeg_threads: int,
) -> bool:
    cmd = [
        ffmpeg,
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-f",
        "image2pipe",
        "-framerate",
        f"{fps:.6f}",
        "-vcodec",
        "png",
        "-i",
        "-",
        "-an",
        "-c:v",
        "libx264",
        "-threads",
        str(ffmpeg_threads),
        "-preset",
        preset,
        "-crf",
        str(crf),
        "-pix_fmt",
        "yuv420p",
        "-movflags",
        "+faststart",
        str(output_path),
    ]

    logging.info("Writing %s", output_path)
    process = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE)
    assert process.stdin is not None
    assert process.stderr is not None

    try:
        frame_count = 0
        for frame_bytes in frames:
            process.stdin.write(frame_bytes)
            frame_count += 1
            if frame_count % 300 == 0:
                logging.info("Encoded %s frames...", frame_count)

        process.stdin.close()
        stderr = process.stderr.read().decode("utf-8", errors="replace")
        return_code = process.wait()
    except BrokenPipeError:
        stderr = process.stderr.read().decode("utf-8", errors="replace")
        return_code = process.wait()

    if return_code != 0:
        logging.error(stderr.strip())
        return False

    if not validate_mp4(ffprobe, output_path):
        return False

    logging.info("Done.")
    return True


def make_temp_output_path(output_path: Path) -> Path:
    return output_path.with_name(
        f".{output_path.stem}.{os.getpid()}.{uuid.uuid4().hex}.tmp{output_path.suffix}"
    )


def cleanup_stale_temp_outputs(directory: Path) -> None:
    for path in directory.glob(".*.tmp.mp4"):
        if path.is_file():
            try:
                path.unlink()
                logging.info("Removed stale temp file: %s", path)
            except OSError as exc:
                logging.warning("Could not remove stale temp file %s: %s", path, exc)


def write_mp4_atomically(
    ffmpeg: str,
    ffprobe: Optional[str],
    frames: Iterable[bytes],
    output_path: Path,
    fps: float,
    crf: int,
    preset: str,
    ffmpeg_threads: int,
) -> bool:
    if output_path.exists():
        logging.info("Skipping existing output: %s", output_path)
        return True

    temp_output_path = make_temp_output_path(output_path)
    try:
        ok = write_mp4(
            ffmpeg,
            ffprobe,
            frames,
            temp_output_path,
            fps,
            crf,
            preset,
            ffmpeg_threads,
        )
        if not ok:
            return False

        if output_path.exists():
            logging.info(
                "Skipping rename because output already exists: %s",
                output_path,
            )
            return True

        temp_output_path.replace(output_path)
        logging.info("Saved %s", output_path)
        return True
    finally:
        if temp_output_path.exists():
            try:
                temp_output_path.unlink()
            except OSError:
                pass


def resolve_output_path(output_dir: Path, source_path: Path) -> Path:
    return output_dir / f"{source_path.stem}.mp4"


def convert_one_file(
    ffmpeg: str,
    ffprobe: Optional[str],
    source_path: Path,
    output_dir: Path,
    fps: float,
    crf: int,
    preset: str,
    background_rgb: Tuple[int, int, int],
    ffmpeg_threads: int,
) -> bool:
    output_path = resolve_output_path(output_dir, source_path)
    if output_path.exists():
        logging.info("Skipping existing output: %s", output_path)
        return True

    canvas_size = get_canvas_size([source_path])
    frames = iter_video_frames([source_path], canvas_size, fps, background_rgb)

    logging.info("Converting %s -> %s", source_path.name, output_path.name)
    return write_mp4_atomically(
        ffmpeg,
        ffprobe,
        frames,
        output_path,
        fps,
        crf,
        preset,
        ffmpeg_threads,
    )


def convert_one_file_job(args) -> Tuple[str, bool]:
    (
        ffmpeg,
        ffprobe,
        source_path,
        output_dir,
        fps,
        crf,
        preset,
        background_rgb,
        ffmpeg_threads,
    ) = args

    ok = convert_one_file(
        ffmpeg=ffmpeg,
        ffprobe=ffprobe,
        source_path=source_path,
        output_dir=output_dir,
        fps=fps,
        crf=crf,
        preset=preset,
        background_rgb=background_rgb,
        ffmpeg_threads=ffmpeg_threads,
    )
    return source_path.name, ok


def default_worker_count(file_count: int) -> int:
    cpu_count = os.cpu_count() or 1
    if file_count <= 1:
        return 1
    return max(1, min(file_count, cpu_count))


def ffmpeg_threads_per_worker(worker_count: int, requested_threads: int) -> int:
    if requested_threads > 0:
        return requested_threads

    cpu_count = os.cpu_count() or 1
    return max(1, cpu_count // max(1, worker_count))


def convert_folder(
    folder: Path,
    output_dir: Optional[Path],
    merge: bool,
    merge_output_dir: Optional[Path],
    merge_output_name: str,
    fps: float,
    crf: int,
    preset: str,
    background: str,
    log_to_file: bool,
    workers: int,
    ffmpeg_threads: int,
) -> int:
    configure_logging(log_to_file)

    folder = folder.resolve()
    output_dir = output_dir.resolve() if output_dir else folder
    merge_output_dir = merge_output_dir.resolve() if merge_output_dir else output_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    merge_output_dir.mkdir(parents=True, exist_ok=True)

    cleanup_stale_temp_outputs(output_dir)
    if merge_output_dir != output_dir:
        cleanup_stale_temp_outputs(merge_output_dir)

    script_dir = Path(__file__).resolve().parent
    ffmpeg = find_ffmpeg(script_dir)
    ffprobe = find_ffprobe(script_dir)
    files = list_input_files(folder)

    if not files:
        logging.error("No WEBP or GIF files were found in %s", folder)
        return 1

    logging.info("Input folder: %s", folder)
    logging.info("Files will be processed in this order:")
    for index, path in enumerate(files, 1):
        logging.info("  %s. %s", index, path.name)

    background_rgb = parse_color(background)
    workers = default_worker_count(len(files)) if workers <= 0 else max(1, workers)
    workers = min(workers, len(files))
    ffmpeg_threads = ffmpeg_threads_per_worker(workers, ffmpeg_threads)

    logging.info("Output FPS: %.3f", fps)
    logging.info("Workers: %s", workers)
    logging.info("FFmpeg threads per worker: %s", ffmpeg_threads)

    failed = []
    planned_outputs = set()
    jobs = []
    for path in files:
        output_path = resolve_output_path(output_dir, path)
        if output_path in planned_outputs:
            logging.warning(
                "Skipping %s because another input maps to %s",
                path.name,
                output_path.name,
            )
            continue

        planned_outputs.add(output_path)
        if output_path.exists():
            logging.info("Skipping existing output: %s", output_path)
            continue

        jobs.append(
            (
                ffmpeg,
                ffprobe,
                path,
                output_dir,
                fps,
                crf,
                preset,
                background_rgb,
                ffmpeg_threads,
            )
        )

    skipped_duplicates = len(files) - len(planned_outputs)
    if skipped_duplicates:
        logging.warning(
            "Skipped %s input file(s) because another file maps to the same MP4 name.",
            skipped_duplicates,
        )

    if workers == 1:
        for job in jobs:
            file_name, ok = convert_one_file_job(job)
            if not ok:
                failed.append(file_name)
    else:
        with concurrent.futures.ProcessPoolExecutor(max_workers=workers) as executor:
            futures = [executor.submit(convert_one_file_job, job) for job in jobs]
            for future in concurrent.futures.as_completed(futures):
                file_name, ok = future.result()
                if not ok:
                    failed.append(file_name)

    if merge:
        merge_output_path = merge_output_dir / merge_output_name
        if merge_output_path.suffix.lower() != ".mp4":
            merge_output_path = merge_output_path.with_suffix(".mp4")

        if merge_output_path.exists():
            logging.info("Skipping existing merged output: %s", merge_output_path)
        else:
            logging.info("Creating merged output: %s", merge_output_path)
            canvas_size = get_canvas_size(files)
            frames = iter_video_frames(files, canvas_size, fps, background_rgb)
            ok = write_mp4_atomically(
                ffmpeg,
                ffprobe,
                frames,
                merge_output_path,
                fps,
                crf,
                preset,
                ffmpeg_threads,
            )
            if not ok:
                failed.append(merge_output_path.name)

    if failed:
        logging.error("Failed outputs: %s", ", ".join(failed))
        return 1

    logging.info("All tasks completed.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Convert WEBP/GIF files in a folder to playable MP4 files."
    )
    parser.add_argument(
        "folder",
        nargs="?",
        default=".",
        help="Folder containing .webp and .gif files. Default: current folder.",
    )
    parser.add_argument(
        "--output-dir",
        help="Directory for individual MP4 files. Default: same as input folder.",
    )
    parser.add_argument(
        "--merge",
        action="store_true",
        help="Also create one merged MP4 in natural filename order.",
    )
    parser.add_argument(
        "--merge-output-dir",
        help="Directory for the merged MP4. Default: same as --output-dir.",
    )
    parser.add_argument(
        "--merge-output",
        default="merged.mp4",
        help="Merged MP4 filename. Default: merged.mp4.",
    )
    parser.add_argument(
        "--fps",
        type=float,
        default=30.0,
        help="Output constant FPS. 30 is stable and efficient; use 60 for smoother timing.",
    )
    parser.add_argument("--crf", type=int, default=23, help="Quality. Lower is better.")
    parser.add_argument(
        "--preset",
        default="veryfast",
        help="x264 preset: ultrafast, veryfast, fast, medium...",
    )
    parser.add_argument(
        "--background",
        default="black",
        help="Background for transparent frames: black, white, gray, or #RRGGBB.",
    )
    parser.add_argument("--log", action="store_true", help="Write webp_gif_to_mp4.log.")
    parser.add_argument(
        "--workers",
        type=int,
        default=0,
        help="Parallel conversion workers. Default: auto, using up to the CPU core count.",
    )
    parser.add_argument(
        "--ffmpeg-threads",
        type=int,
        default=0,
        help="FFmpeg threads per worker. Default: auto, CPU cores divided by workers.",
    )

    args = parser.parse_args()

    try:
        return convert_folder(
            folder=Path(args.folder),
            output_dir=Path(args.output_dir) if args.output_dir else None,
            merge=args.merge,
            merge_output_dir=Path(args.merge_output_dir) if args.merge_output_dir else None,
            merge_output_name=args.merge_output,
            fps=args.fps,
            crf=args.crf,
            preset=args.preset,
            background=args.background,
            log_to_file=args.log,
            workers=args.workers,
            ffmpeg_threads=args.ffmpeg_threads,
        )
    except KeyboardInterrupt:
        print("\nCancelled.")
        return 130
    except Exception as exc:
        logging.error("Operation not completed: %s", exc)
        return 1


if __name__ == "__main__":
    sys.exit(main())
