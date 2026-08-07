import os
import json
import time
import threading
from datetime import datetime, timedelta
import yt_dlp
from flask import Flask, render_template, request, redirect, url_for, send_from_directory, flash, jsonify, Response

app = Flask(__name__)
app.secret_key = 'super-secret-key-change-this'

DOWNLOAD_DIR = os.path.join(os.path.dirname(__file__), 'downloads')
DB_FILE = os.path.join(os.path.dirname(__file__), 'videos_db.json')
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

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
        else:
            remaining_videos.append(video)

    save_db(remaining_videos)


@app.route('/')
def index():
    cleanup_old_videos()
    videos = load_db()
    return render_template('index.html', videos=videos)


@app.route('/get-formats', methods=['POST'])
def get_formats():
    url = request.json.get('url', '').strip()
    if not url:
        return jsonify({'error': 'Please provide a valid URL'}), 400

    ydl_opts = {'quiet': True, 'noplaylist': True}
    
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            
            formats = []
            seen_heights = set()
            
            for f in info.get('formats', []):
                height = f.get('height')
                ext = f.get('ext', 'mp4')
                format_id = f.get('format_id')
                
                if height and height not in seen_heights and ext == 'mp4':
                    seen_heights.add(height)
                    formats.append({
                        'format_id': f"{format_id}+bestaudio/best",
                        'resolution': f"{height}p ({ext})",
                        'height': height
                    })
            
            formats.sort(key=lambda x: x['height'], reverse=True)
            
            options = [
                {'format_id': 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best', 'resolution': 'Best Available Quality'},
            ] + formats + [
                {'format_id': 'bestaudio/best', 'resolution': 'Audio Only (MP3/M4A)'}
            ]

            return jsonify({
                'title': info.get('title', 'Video'),
                'thumbnail': info.get('thumbnail', ''),
                'duration': info.get('duration_string', ''),
                'formats': options
            })
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/start-download', methods=['POST'])
def start_download():
    data = request.json
    url = data.get('url')
    format_id = data.get('format_id')
    
    task_id = str(int(time.time() * 1000))
    progress_data[task_id] = {
        'status': 'downloading',
        'percent': '0%',
        'speed': '0 KiB/s',
        'eta': 'Unknown'
    }

    thread = threading.Thread(target=run_yt_dlp, args=(task_id, url, format_id))
    thread.start()

    return jsonify({'task_id': task_id})

import re

# Add helper function to strip ANSI codes just in case
def clean_ansi(text):
    if not text:
        return ""
    ansi_escape = re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')
    return ansi_escape.sub('', str(text)).strip()

def run_yt_dlp(task_id, url, format_id):
    def progress_hook(d):
        if d['status'] == 'downloading':
            # Extract raw percentages for reliable progress bar filling
            downloaded = d.get('downloaded_bytes', 0)
            total = d.get('total_bytes') or d.get('total_bytes_estimate', 0)
            
            if total > 0:
                percent_val = round((downloaded / total) * 100, 1)
                percent_str = f"{percent_val}%"
            else:
                percent_str = clean_ansi(d.get('_percent_str', '0%'))

            progress_data[task_id] = {
                'status': 'downloading',
                'percent': percent_str,
                'speed': clean_ansi(d.get('_speed_str', '0 KiB/s')),
                'eta': clean_ansi(d.get('_eta_str', 'Unknown'))
            }
        elif d['status'] == 'finished':
            progress_data[task_id]['status'] = 'processing'
            progress_data[task_id]['percent'] = '100%'

    ydl_opts = {
        'format': format_id,
        'outtmpl': os.path.join(DOWNLOAD_DIR, '%(title)s.%(ext)s'),
        'noplaylist': True,
        'no_color': True,  # <--- DISABLES ANSI ESCAPE CODES
        'progress_hooks': [progress_hook],
        'quiet': True
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            full_path = ydl.prepare_filename(info)
            filename = os.path.basename(full_path)
            file_size = os.path.getsize(full_path) if os.path.exists(full_path) else 0

            videos = load_db()
            video_record = {
                'id': task_id,
                'title': info.get('title', 'Downloaded Video'),
                'filename': filename,
                'file_size': file_size,
                'created_at': datetime.now().isoformat()
            }
            videos.insert(0, video_record)
            save_db(videos)

            progress_data[task_id]['status'] = 'complete'
    except Exception as e:
        progress_data[task_id] = {'status': 'error', 'error': str(e)}


@app.route('/progress/<task_id>')
def progress_stream(task_id):
    def generate():
        while True:
            data = progress_data.get(task_id, {'status': 'waiting'})
            yield f"data: {json.dumps(data)}\n\n"
            
            if data.get('status') in ['complete', 'error']:
                progress_data.pop(task_id, None)
                break
            time.sleep(0.5)

    return Response(generate(), mimetype='text/event-stream')


@app.route('/download/<filename>')
def download_file(filename):
    return send_from_directory(DOWNLOAD_DIR, filename, as_attachment=True)


# NEW: Manual Video Deletion Route
@app.route('/delete-video/<video_id>', methods=['POST'])
def delete_video(video_id):
    """Deletes a video file from disk and removes its metadata from the JSON store."""
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
                return jsonify({'error': f"Failed to delete file from disk: {str(e)}"}), 500

        save_db(remaining_videos)
        return jsonify({'success': True, 'message': 'Video deleted successfully'})

    return jsonify({'error': 'Video record not found'}), 404


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8180, debug=True)