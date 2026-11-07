
    ffmpeg_loc = get_ffmpeg_path()
    if ffmpeg_loc:
        base_ydl_opts['ffmpeg_location'] = ffmpeg_loc

    if is_cancelled:
        def hook(d):
            if is_cancelled():
                raise DownloadCancelledError("Download cancelled by user")
        base_ydl_opts['progress_hooks'] = [hook]

    # yt-dlp snapshots sys.stdout/stderr at construction and writes to them
    # directly for some messages (e.g. deprecation notices), bypassing the
    # logger. On a broken Windows child-process stream that flush raises
    # OSError [Errno 22], so we redirect both streams to an in-memory sink for
    # the whole call.
    max_retries = 3
    clients_to_try = ['ios', 'tv', 'web_creator', 'android']

    success = False
    last_error = None

    for client in clients_to_try:
        ydl_opts = dict(base_ydl_opts)
        ydl_opts['extractor_args'] = {
            'youtube': [f'player_client={client}', 'player_skip=web']
        }

        for attempt in range(max_retries):
            try:
                sink = io.StringIO()
                with contextlib.redirect_stdout(sink), contextlib.redirect_stderr(sink):
                    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                        ydl.download([url])
                success = True
                break
            except Exception as e:
                last_error = e

                # If cancelled during download, don't retry or try other clients.
                if is_cancelled and is_cancelled():
                    raise DownloadCancelledError("Download cancelled by user")

                err_str = str(e).lower()
                if (
                    "sign in to confirm" in err_str
                    or "bot" in err_str
                    or "unsupported browser" in err_str
                ):
                    break

                # Wait a bit before retrying, especially useful for 403 blocks.
                if attempt < max_retries - 1:
                    wait_secs = 2 ** attempt
                    end_t = time.time() + wait_secs
                    while time.time() < end_t:
                        if is_cancelled and is_cancelled():
                            raise DownloadCancelledError("Download cancelled by user")
                        time.sleep(min(0.2, max(0.0, end_t - time.time())))

        if success:
            break

    if not success and last_error:
        raise last_error

    return Path(output_path)


def extract_audio(video_path: str, audio_path: str, register_proc: callable = None) -> str:
    """Extract a compact mono 16kHz audio track for speech-to-text.

    Whisper only accepts audio and caps uploads at 25MB, so sending the raw
    (often multi-GB) video fails on real long-form content. A mono 16kHz MP3
    stays tiny even for hour-long videos while keeping speech intelligible.
    """
    if os.path.exists(audio_path) and os.path.getsize(audio_path) > 0:
        return audio_path

    cmd = [
        "ffmpeg", "-y",
        "-i", video_path,
        "-vn",
        "-ac", "1",
        "-ar", "16000",
        "-b:a", "64k",
        audio_path,
    ]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if register_proc:
        register_proc(proc)
    stdout, stderr = proc.communicate()
    if proc.returncode != 0:
        raise subprocess.CalledProcessError(proc.returncode, cmd, stdout, stderr)
    return audio_path


def get_video_duration(video_path: str) -> float:
    """Get video duration in seconds using ffmpeg (bundled), parsing stderr."""
    import re
    cmd = ["ffmpeg", "-i", video_path]
    try:
        res = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace"
        )
        match = re.search(r"Duration:\s*(\d{2}):(\d{2}):(\d{2}\.\d+)", res.stderr)
        if match:
            h, m, s = match.groups()
            return int(h) * 3600 + int(m) * 60 + float(s)
    except Exception as e:
        log_error("video_utils.get_video_duration", e)
    return 0.0
