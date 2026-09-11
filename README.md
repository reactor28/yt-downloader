# YouTube Web Downloader

A lightweight, self-hosted web interface built with Python (Flask) and `yt-dlp` for querying, downloading, and managing YouTube videos and subtitles directly on a Linux server.

![UI Theme](https://img.shields.io/badge/UI-YouTube%20Dark%20Theme-red)
![Backend](https://img.shields.io/badge/Backend-Flask%20%7C%20yt--dlp-blue)

---

## 🚀 Features

- **YouTube Dark Theme UI**: Sleek, modern dark-mode interface inspired by YouTube.
- **Download Queue & Multi-User Live Sync**: Add multiple downloads to a sequential processing queue while downloads are actively running. Any connected user can see real-time download progress and the pending queue.
- **Format & Quality Selection**: Query available MP4 video resolutions, Best Quality presets, or Audio-Only modes.
- **Subtitle & Caption Extraction**: Extract and download available manual subtitles or auto-generated captions (`.srt` / `.vtt`).
- **Real-Time Progress Tracking**: Watch live percentage, download speed, and ETA metrics streamed via Server-Sent Events (SSE).
- **Metadata Badges**: View quality and subtitle language tags directly on downloaded server items.
- **Automated Retention**: Built-in 7-day retention policy that automatically purges old downloads and database records.

---

## 🛠️ Dependencies & System Requirements

### Prerequisites
- **Linux OS** (Fedora, RHEL, CentOS Stream, etc.)
- **Python 3.8+**
- **Node.js** or **Deno** (Required JavaScript runtime for YouTube signature & n-token deciphering)
- **FFmpeg** (Recommended for video/audio stream merging and subtitle formatting)

### Python Libraries
- `Flask` (Web framework)
- `yt-dlp` (Video & subtitle extraction engine)

---

## 📥 Installation

### 1. Install System Packages
On Fedora distributions using `dnf`:
```bash
sudo dnf install python3 python3-pip nodejs ffmpeg -y
```

> **Note**: If `ffmpeg` is not available in your standard Fedora repositories, enable RPM Fusion first:
> ```bash
> sudo dnf install https://mirrors.rpmfusion.org/free/fedora/rpmfusion-free-release-$(rpm -E %fedora).noarch.rpm -y
> sudo dnf install ffmpeg -y
> ```

### 2. Install Python Dependencies
Install `Flask` and `yt-dlp` via `pip`:
```bash
pip3 install flask yt-dlp
```
*(Or system-wide if preferred: `sudo pip3 install flask yt-dlp`)*

---

## 🖥️ Running the Application

### Method A: Direct Execution (Manual)
You can run the web server directly using Python:
```bash
python3 app.py
```
The application will start on **`http://0.0.0.0:8180`**. Open your browser and navigate to `http://localhost:8180` or `http://<server-ip>:8180`.

---

### Method B: Running via `yt_downloader.service` (Systemd Service)

Running as a `systemd` service ensures the application runs automatically in the background, starts on system boot, and auto-restarts if an error occurs.

#### Option 1: Automated Setup (Using `install.sh`)
An installer script [`install.sh`](file:///home/bob/Downloads/ytdl_web/install.sh) is provided:

```bash
chmod +x install.sh
./install.sh
```

#### Option 2: Manual Systemd Setup
1. Verify paths in [`yt_downloader.service`](file:///home/bob/Downloads/ytdl_web/yt_downloader.service). Adjust `WorkingDirectory`, `ExecStart`, `User`, and `Group` if your username or project path differs.

2. Copy the service file to the systemd service directory:
   ```bash
   sudo cp yt_downloader.service /etc/systemd/system/
   ```

3. Reload systemd daemon and enable/start the service:
   ```bash
   sudo systemctl daemon-reload
   sudo systemctl enable yt_downloader.service
   sudo systemctl start yt_downloader.service
   ```

#### Managing the Service
- **Check Status**:
  ```bash
  sudo systemctl status yt_downloader.service
  ```
- **Stop Service**:
  ```bash
  sudo systemctl stop yt_downloader.service
  ```
- **Restart Service**:
  ```bash
  sudo systemctl restart yt_downloader.service
  ```
- **View Live Logs**:
  ```bash
  sudo journalctl -u yt_downloader.service -f
  ```

---

## 📂 Project Structure

```
ytdl_web/
├── app.py                  # Flask backend server & yt-dlp integration
├── templates/
│   └── index.html          # YouTube Dark Theme frontend template & SSE client
├── downloads/              # Directory storing downloaded media & subtitle files
├── videos_db.json          # JSON flat-file database storing video records
├── yt_downloader.service   # Systemd unit service configuration
├── install.sh              # Bash installer script for systemd setup
└── README.md               # Project documentation
```
