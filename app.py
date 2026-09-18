import os
import json
import time
import threading
import re
from datetime import datetime, timedelta
import yt_dlp
from flask import Flask, render_template, request, redirect, url_for, send_from_directory, flash, jsonify, Response

app = Flask(__name__)
app.secret_key = 'super-secret-key-change-this'

DOWNLOAD_DIR = os.path.join(os.path.dirname(__file__), 'downloads')
DB_FILE = os.path.join(os.path.dirname(__file__), 'videos_db.json')
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

# Global queue & state management
queue_lock = threading.Lock()
download_queue = []
current_download = None
progress_data = {}

def load_db():
    if not os.path.exists(DB_FILE):
        return []
    with open(DB_FILE, 'r') as f:
        return json.load(f)

def save_db(data):
    with open(DB_FILE, 'w') as f:
        json.dump(data, f, indent=4)

def format_bytes(size):
    if not size or size == 0:
        return "0 B"
    for unit in ['B', 'KB', 'MB', 'GB']:
        if size < 1024.0:
            return f"{size:.2f} {unit}"
        size /= 1024.0
    return f"{size:.2f} TB"

app.jinja_env.filters['filesizeformat'] = format_bytes

def clean_ansi(text):
    if not text:
        return ""
    ansi_escape = re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')
    return ansi_escape.sub('', str(text)).strip()

def cleanup_old_videos():
    videos = load_db()
    cutoff = datetime.now() - timedelta(days=7)
    remaining_videos = []

    for video in videos:
        created_at = datetime.fromisoformat(video['created_at'])
        file_path = os.path.join(DOWNLOAD_DIR, video['filename'])

        if created_at < cutoff:
            if os.path.exists(file_path):
                try:
                    os.remove(file_path)
                except OSError:
                    pass
            if video.get('subtitle_filename'):
                sub_path = os.path.join(DOWNLOAD_DIR, video['subtitle_filename'])
                if os.path.exists(sub_path):
                    try:
                        os.remove(sub_path)
                    except OSError:
                        pass
        else:
            remaining_videos.append(video)

    save_db(remaining_videos)

def get_state_snapshot():
    with queue_lock:
        active = dict(current_download) if current_download else None
        q = [dict(t) for t in download_queue]
    videos = load_db()
    return {
        'active': active,
        'queue': q,
        'videos_count': len(videos),
        'latest_video_id': videos[0]['id'] if videos else None
    }

def run_download_task(task):
    task_id = task['task_id']
    url = task['url']
    format_id = task['format_id']
    quality_label = task.get('quality_label', '')
    subtitle_code = task.get('subtitle_code', 'none')
    subtitle_label = task.get('subtitle_label')
    playlist_title = task.get('playlist_title')

    def progress_hook(d):
        if d['status'] == 'downloading':
            downloaded = d.get('downloaded_bytes', 0)
            total = d.get('total_bytes') or d.get('total_bytes_estimate', 0)
            
            if total > 0:
                percent_val = round((downloaded / total) * 100, 1)
                percent_str = f"{percent_val}%"
            else:
                percent_str = clean_ansi(d.get('_percent_str', '0%'))

            speed_str = clean_ansi(d.get('_speed_str', '0 KiB/s'))
            eta_str = clean_ansi(d.get('_eta_str', 'Unknown'))

            with queue_lock:
                if current_download and current_download['task_id'] == task_id:
                    current_download['status'] = 'downloading'
                    current_download['percent'] = percent_str
                    current_download['speed'] = speed_str
                    current_download['eta'] = eta_str

            progress_data[task_id] = {
                'status': 'downloading',
                'percent': percent_str,
                'speed': speed_str,
                'eta': eta_str
            }
        elif d['status'] == 'finished':
            with queue_lock:
                if current_download and current_download['task_id'] == task_id:
                    current_download['status'] = 'processing'
                    current_download['percent'] = '100%'
                    current_download['speed'] = ''
                    current_download['eta'] = ''

            progress_data[task_id] = {
                'status': 'processing',
                'percent': '100%',
                'speed': '',
                'eta': ''
            }

    js_runtimes = {'node': {'path': '/usr/bin/node'},
                   'deno': {'path': '/home/bob/.deno/bin/deno'}}

    # Configure format and format_sort for universal MP4 / H.264 compatibility
    if format_id in ['bestaudio/best', 'audio'] or (format_id and format_id.startswith('bestaudio')):
        actual_format = 'bestaudio[ext=m4a]/bestaudio/best'
        format_sort = ['aext:m4a']
        merge_fmt = None
    elif format_id and format_id.startswith('res:'):
        actual_format = 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/bestvideo+bestaudio/best'
        format_sort = [format_id, 'vcodec:avc', 'ext:mp4:m4a']
        merge_fmt = 'mp4'
    elif format_id in ['best', 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best'] or (format_id and 'bestvideo' in format_id):
        actual_format = 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/bestvideo+bestaudio/best'
        format_sort = ['res', 'vcodec:avc', 'ext:mp4:m4a']
        merge_fmt = 'mp4'
    else:
        actual_format = format_id
        format_sort = ['vcodec:avc', 'ext:mp4:m4a']
        merge_fmt = 'mp4'

    ydl_opts = {
        'format': actual_format,
        'format_sort': format_sort,
        'outtmpl': os.path.join(DOWNLOAD_DIR, '%(title)s.%(ext)s'),
        'noplaylist': True,
        'no_color': True,
        'progress_hooks': [progress_hook],
        'quiet': True,
        'js_runtimes': js_runtimes,
        'remote_components': ['ejs:github'],
        'extractor_args': {
            'youtube': {
                'player_client': ['android', 'web_embedded']
            }
        },
    }
    if merge_fmt:
        ydl_opts['merge_output_format'] = merge_fmt

    if subtitle_code and subtitle_code != 'none':
        is_auto = subtitle_code.startswith('auto:')
        clean_lang = subtitle_code.replace('auto:', '')
        ydl_opts['writesubtitles'] = True
        ydl_opts['writeautomaticsub'] = is_auto
        if clean_lang == 'all':
            ydl_opts['allsubtitles'] = True
        else:
            ydl_opts['subtitleslangs'] = [clean_lang]
        ydl_opts['subtitlesformat'] = 'srt/vtt/best'

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            full_path = ydl.prepare_filename(info)
            filename = os.path.basename(full_path)
            file_size = os.path.getsize(full_path) if os.path.exists(full_path) else 0

            # Update resolved video title & thumbnail
            resolved_title = info.get('title') or task.get('title') or 'Downloaded Video'
            resolved_thumb = info.get('thumbnail') or task.get('thumbnail') or ''
            with queue_lock:
                if current_download and current_download['task_id'] == task_id:
                    current_download['title'] = resolved_title
                    if resolved_thumb:
                        current_download['thumbnail'] = resolved_thumb

            # Detect downloaded subtitle file
            sub_filename = None
            if info.get('requested_subtitles'):
                for lang, sub_info in info['requested_subtitles'].items():
                    if sub_info.get('filepath') and os.path.exists(sub_info['filepath']):
                        sub_filename = os.path.basename(sub_info['filepath'])
                        break

            if not sub_filename and subtitle_code != 'none':
                base_name = os.path.splitext(filename)[0]
                for f in os.listdir(DOWNLOAD_DIR):
                    if f.startswith(base_name) and (f.endswith('.srt') or f.endswith('.vtt')):
                        sub_filename = f
                        break

            # Determine Quality Tag
            if quality_label and quality_label not in ['Best Available Quality', 'Best Available Quality (MP4)']:
                quality_tag = quality_label
            else:
                if info.get('height'):
                    quality_tag = f"{info.get('height')}p (Best)"
                elif format_id in ['bestaudio/best', 'audio']:
                    quality_tag = "Audio Only"
                else:
                    quality_tag = "Best Quality"

            sub_tag = subtitle_label if sub_filename else None

            videos = load_db()
            video_record = {
                'id': task_id,
                'title': resolved_title,
                'filename': filename,
                'subtitle_filename': sub_filename,
                'quality': quality_tag,
                'subtitle_lang': sub_tag,
                'playlist_title': playlist_title,
                'file_size': file_size,
                'created_at': datetime.now().isoformat()
            }
            videos.insert(0, video_record)
            save_db(videos)

            with queue_lock:
                if current_download and current_download['task_id'] == task_id:
                    current_download['status'] = 'complete'
                    current_download['percent'] = '100%'

            progress_data[task_id] = {'status': 'complete'}
            time.sleep(1.0)
    except Exception as e:
        with queue_lock:
            if current_download and current_download['task_id'] == task_id:
                current_download['status'] = 'error'
                current_download['error'] = str(e)
        progress_data[task_id] = {'status': 'error', 'error': str(e)}
        time.sleep(3.0)

def queue_worker():
    global current_download
    while True:
        task = None
        with queue_lock:
            if download_queue and current_download is None:
                task = download_queue.pop(0)
                current_download = {
                    'task_id': task['task_id'],
                    'url': task['url'],
                    'title': task.get('title', 'Video'),
                    'thumbnail': task.get('thumbnail', ''),
                    'format_id': task['format_id'],
                    'quality_label': task.get('quality_label', ''),
                    'subtitle_code': task.get('subtitle_code', 'none'),
                    'subtitle_label': task.get('subtitle_label'),
                    'playlist_title': task.get('playlist_title'),
                    'playlist_index': task.get('playlist_index'),
                    'playlist_total': task.get('playlist_total'),
                    'status': 'downloading',
                    'percent': '0%',
                    'speed': '0 KiB/s',
                    'eta': 'Starting...',
                    'error': None
                }
                progress_data[task['task_id']] = current_download

        if task:
            try:
                run_download_task(task)
            except Exception as e:
                print(f"Error executing download task {task['task_id']}: {e}")
            finally:
                with queue_lock:
                    current_download = None
        else:
            time.sleep(0.5)

# Start background queue processor
worker_thread = threading.Thread(target=queue_worker, daemon=True)
worker_thread.start()


@app.route('/')
def index():
    cleanup_old_videos()
    videos = load_db()
    state = get_state_snapshot()
    return render_template(
        'index.html',
        videos=videos,
        active=state['active'],
        queue=state['queue']
    )


@app.route('/get-formats', methods=['POST'])
def get_formats():
    payload = request.json or {}
    url = payload.get('url', '').strip()
    single_video_only = payload.get('single_video', False)

    if not url:
        return jsonify({'error': 'Please provide a valid URL'}), 400

    js_runtimes = {'node': {'path': '/usr/bin/node'},
                   'deno': {'path': '/home/bob/.deno/bin/deno'}}

    # Check playlist first (unless single video specifically forced)
    if not single_video_only:
        ydl_opts_check = {
            'quiet': True,
            'extract_flat': 'in_playlist',
            'noplaylist': False,
            'js_runtimes': js_runtimes
        }
        try:
            with yt_dlp.YoutubeDL(ydl_opts_check) as ydl:
                info = ydl.extract_info(url, download=False)
                is_playlist = (info.get('_type') == 'playlist' or bool(info.get('entries')))

                if is_playlist:
                    raw_entries = list(info.get('entries', []))
                    entries = []
                    for idx, e in enumerate(raw_entries):
                        if not e:
                            continue
                        v_url = e.get('url')
                        if v_url and not v_url.startswith('http'):
                            v_url = f"https://www.youtube.com/watch?v={v_url}"
                        elif not v_url and e.get('id'):
                            v_url = f"https://www.youtube.com/watch?v={e['id']}"

                        thumb = ''
                        if e.get('thumbnails') and len(e['thumbnails']) > 0:
                            thumb = e['thumbnails'][-1].get('url', '')
                        elif e.get('thumbnail'):
                            thumb = e.get('thumbnail', '')

                        dur = e.get('duration_string')
                        if not dur and e.get('duration'):
                            dur = f"{int(e['duration'] // 60)}:{int(e['duration'] % 60):02d}"

                        entries.append({
                            'id': e.get('id', str(idx)),
                            'title': e.get('title', f"Video {idx + 1}"),
                            'url': v_url,
                            'thumbnail': thumb,
                            'duration': dur or ''
                        })

                    playlist_formats = [
                        {'format_id': 'best', 'resolution': 'Best Available Quality (MP4)'},
                        {'format_id': 'res:1440', 'resolution': '1440p 2K or lower (MP4)'},
                        {'format_id': 'res:1080', 'resolution': '1080p Full HD or lower (MP4)'},
                        {'format_id': 'res:720', 'resolution': '720p HD or lower (MP4)'},
                        {'format_id': 'res:480', 'resolution': '480p SD or lower (MP4)'},
                        {'format_id': 'bestaudio/best', 'resolution': 'Audio Only (MP3/M4A)'}
                    ]

                    playlist_subtitles = [
                        {'code': 'none', 'name': 'No Subtitles'},
                        {'code': 'en', 'name': 'English (en)'},
                        {'code': 'auto:en', 'name': 'English [Auto-generated]'},
                        {'code': 'es', 'name': 'Spanish (es)'},
                        {'code': 'auto:es', 'name': 'Spanish [Auto-generated]'},
                        {'code': 'fr', 'name': 'French (fr)'},
                        {'code': 'de', 'name': 'German (de)'},
                        {'code': 'all', 'name': 'All Available Subtitles'}
                    ]

                    first_thumb = ''
                    for item in entries:
                        if item.get('thumbnail'):
                            first_thumb = item['thumbnail']
                            break
                    if not first_thumb:
                        first_thumb = info.get('thumbnail', '')

                    return jsonify({
                        'is_playlist': True,
                        'has_single_video': ('watch?v=' in url or 'youtu.be/' in url),
                        'title': info.get('title') or 'Playlist',
                        'thumbnail': first_thumb,
                        'duration': f"Playlist • {len(entries)} videos",
                        'video_count': len(entries),
                        'formats': playlist_formats,
                        'subtitles': playlist_subtitles,
                        'entries': entries
                    })
        except Exception:
            pass  # Fall through to single video extraction

    # Single video extraction
    ydl_opts_single = {
        'quiet': True,
        'noplaylist': True,
        'js_runtimes': js_runtimes
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts_single) as ydl:
            info = ydl.extract_info(url, download=False)
            
            formats = []
            seen_heights = set()
            
            # Prioritize AVC / H.264 formats so standard media players decode video without issue
            raw_formats = list(info.get('formats', []))
            raw_formats.sort(key=lambda f: (1 if (f.get('vcodec') or '').startswith('avc') else 0), reverse=True)

            for f in raw_formats:
                height = f.get('height')
                ext = f.get('ext', 'mp4')
                format_id = f.get('format_id')
                vcodec = f.get('vcodec', '')
                
                if height and height not in seen_heights and ext == 'mp4' and vcodec != 'none':
                    seen_heights.add(height)
                    formats.append({
                        'format_id': f"{format_id}+bestaudio/best",
                        'resolution': f"{height}p ({ext})",
                        'height': height
                    })
            
            formats.sort(key=lambda x: x['height'], reverse=True)
            
            options = [
                {'format_id': 'best', 'resolution': 'Best Available Quality (MP4)'},
            ] + formats + [
                {'format_id': 'bestaudio/best', 'resolution': 'Audio Only (MP3/M4A)'}
            ]

            # Parse subtitle options (manual and automatic)
            subtitles = [{'code': 'none', 'name': 'No Subtitles'}]
            raw_subs = info.get('subtitles') or {}
            for lang_code, sub_list in raw_subs.items():
                lang_name = lang_code
                if sub_list and isinstance(sub_list, list) and len(sub_list) > 0:
                    lang_name = sub_list[0].get('name') or lang_code
                subtitles.append({
                    'code': lang_code,
                    'name': f"{lang_name} ({lang_code})",
                    'is_auto': False
                })

            raw_auto = info.get('automatic_captions') or {}
            for lang_code, sub_list in raw_auto.items():
                if lang_code not in raw_subs:
                    lang_name = lang_code
                    if sub_list and isinstance(sub_list, list) and len(sub_list) > 0:
                        lang_name = sub_list[0].get('name') or lang_code
                    subtitles.append({
                        'code': f"auto:{lang_code}",
                        'name': f"{lang_name} ({lang_code}) [Auto-generated]",
                        'is_auto': True
                    })

            return jsonify({
                'is_playlist': False,
                'title': info.get('title', 'Video'),
                'thumbnail': info.get('thumbnail', ''),
                'duration': info.get('duration_string', ''),
                'formats': options,
                'subtitles': subtitles
            })
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/start-download', methods=['POST'])
def start_download():
    data = request.json or {}
    url = data.get('url', '').strip()
    if not url:
        return jsonify({'error': 'Please provide a valid URL'}), 400

    format_id = data.get('format_id')
    title = data.get('title', 'Video')
    thumbnail = data.get('thumbnail', '')
    quality_label = data.get('quality_label', '')
    subtitle_code = data.get('subtitle_code', 'none')
    subtitle_label = data.get('subtitle_label')
    is_playlist = data.get('is_playlist', False)
    entries = data.get('entries', [])

    if is_playlist:
        # If entries were not provided in payload, extract them on the fly
        if not entries:
            js_runtimes = {'node': {'path': '/usr/bin/node'},
                           'deno': {'path': '/home/bob/.deno/bin/deno'}}
            ydl_opts = {
                'quiet': True,
                'extract_flat': 'in_playlist',
                'noplaylist': False,
                'js_runtimes': js_runtimes
            }
            try:
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    pl_info = ydl.extract_info(url, download=False)
                    raw_entries = list(pl_info.get('entries', []))
                    for idx, e in enumerate(raw_entries):
                        if not e:
                            continue
                        v_url = e.get('url')
                        if v_url and not v_url.startswith('http'):
                            v_url = f"https://www.youtube.com/watch?v={v_url}"
                        elif not v_url and e.get('id'):
                            v_url = f"https://www.youtube.com/watch?v={e['id']}"

                        thumb = ''
                        if e.get('thumbnails') and len(e['thumbnails']) > 0:
                            thumb = e['thumbnails'][-1].get('url', '')
                        elif e.get('thumbnail'):
                            thumb = e.get('thumbnail', '')

                        entries.append({
                            'id': e.get('id', str(idx)),
                            'title': e.get('title', f"Video {idx + 1}"),
                            'url': v_url,
                            'thumbnail': thumb
                        })
            except Exception as e:
                return jsonify({'error': f"Failed to extract playlist videos: {str(e)}"}), 500

        if not entries:
            return jsonify({'error': 'No downloadable videos found in playlist'}), 400

        added_count = 0
        now_ts = int(time.time() * 1000)
        with queue_lock:
            for idx, entry in enumerate(entries):
                task_id = f"{now_ts}_{idx}"
                task = {
                    'task_id': task_id,
                    'url': entry['url'],
                    'title': entry.get('title', f"Video {idx + 1}"),
                    'thumbnail': entry.get('thumbnail', ''),
                    'format_id': format_id,
                    'quality_label': quality_label,
                    'subtitle_code': subtitle_code,
                    'subtitle_label': subtitle_label,
                    'playlist_title': title,
                    'playlist_index': idx + 1,
                    'playlist_total': len(entries),
                    'added_at': datetime.now().isoformat()
                }
                download_queue.append(task)
                added_count += 1
            
            queue_pos = len(download_queue)
            is_active_immediately = (current_download is None and queue_pos == added_count)

        return jsonify({
            'status': 'starting' if is_active_immediately else 'queued',
            'is_playlist': True,
            'playlist_title': title,
            'added_count': added_count,
            'queue_position': queue_pos
        })
    else:
        # Single video download
        task_id = str(int(time.time() * 1000))
        task = {
            'task_id': task_id,
            'url': url,
            'title': title,
            'thumbnail': thumbnail,
            'format_id': format_id,
            'quality_label': quality_label,
            'subtitle_code': subtitle_code,
            'subtitle_label': subtitle_label,
            'added_at': datetime.now().isoformat()
        }

        with queue_lock:
            download_queue.append(task)
            queue_pos = len(download_queue)
            is_active_immediately = (current_download is None and queue_pos == 1)

        return jsonify({
            'task_id': task_id,
            'queue_position': queue_pos,
            'status': 'starting' if is_active_immediately else 'queued',
            'title': title
        })


@app.route('/cancel-queue/<task_id>', methods=['POST'])
def cancel_queue(task_id):
    global download_queue
    with queue_lock:
        initial_len = len(download_queue)
        download_queue = [t for t in download_queue if t['task_id'] != task_id]
        removed = len(download_queue) < initial_len

    if removed:
        return jsonify({'success': True, 'message': 'Removed from queue'})
    return jsonify({'error': 'Item not found in waiting queue'}), 404


@app.route('/clear-queue', methods=['POST'])
def clear_queue():
    global download_queue
    with queue_lock:
        cleared_count = len(download_queue)
        download_queue.clear()
    return jsonify({'success': True, 'cleared_count': cleared_count})


@app.route('/stream')
def global_stream():
    def generate():
        while True:
            state = get_state_snapshot()
            yield f"data: {json.dumps(state)}\n\n"
            time.sleep(0.5)

    return Response(generate(), mimetype='text/event-stream')


@app.route('/api/status')
def api_status():
    return jsonify(get_state_snapshot())


@app.route('/api/videos')
def api_videos():
    return jsonify(load_db())


@app.route('/progress/<task_id>')
def progress_stream(task_id):
    def generate():
        while True:
            data = progress_data.get(task_id)
            if not data:
                with queue_lock:
                    if current_download and current_download['task_id'] == task_id:
                        data = current_download
                    else:
                        in_q = any(t['task_id'] == task_id for t in download_queue)
                        if in_q:
                            data = {'status': 'queued', 'percent': '0%', 'speed': 'Queued', 'eta': 'Waiting'}
                        else:
                            data = {'status': 'waiting'}
            yield f"data: {json.dumps(data)}\n\n"
            if data.get('status') in ['complete', 'error']:
                break
            time.sleep(0.5)

    return Response(generate(), mimetype='text/event-stream')


@app.route('/download/<filename>')
def download_file(filename):
    return send_from_directory(DOWNLOAD_DIR, filename, as_attachment=True)


# Manual Video Deletion Route
@app.route('/delete-video/<video_id>', methods=['POST'])
def delete_video(video_id):
    """Deletes a video file (and associated subtitle) from disk and removes metadata from JSON store."""
    videos = load_db()
    video_to_delete = None
    remaining_videos = []

    for video in videos:
        if video['id'] == video_id:
            video_to_delete = video
        else:
            remaining_videos.append(video)

    if video_to_delete:
        file_path = os.path.join(DOWNLOAD_DIR, video_to_delete['filename'])
        if os.path.exists(file_path):
            try:
                os.remove(file_path)
            except OSError as e:
                return jsonify({'error': f"Failed to delete video file from disk: {str(e)}"}), 500

        if video_to_delete.get('subtitle_filename'):
            sub_path = os.path.join(DOWNLOAD_DIR, video_to_delete['subtitle_filename'])
            if os.path.exists(sub_path):
                try:
                    os.remove(sub_path)
                except OSError:
                    pass

        save_db(remaining_videos)
        return jsonify({'success': True, 'message': 'Video deleted successfully'})

    return jsonify({'error': 'Video record not found'}), 404


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8180, debug=True)