# canon-autosync

[简体中文](README.md) | English

Automatically back up photos and videos from a Canon camera with CCAPI to a NAS or local folder, preserving the camera's directory structure. Canon AutoSync uses a FastAPI backend and a React frontend. Run it locally, as a macOS or Windows desktop app, in Docker, or as a fnOS app.

- **Automatic sync** listens for new captures and falls back to scheduled scans when camera events are unavailable. The default scan interval is 60 seconds.
- **Transfer safeguards** pause sync if the camera overheats or its battery is empty, flag slow Wi-Fi transfers, and limit and cache thumbnail requests to reduce load on the camera.
- **Camera dashboard** shows battery level, temperature, card capacity, serial number, MAC address, and sync progress.
- **File browser** shows backed-up and pending files in grid or list view with search, RAW and video thumbnails, and full-size previews. Use the arrow keys to browse previews and Esc to close them.
- **Backup management** lets you delete NAS backups and exclude the corresponding camera originals from automatic backup. Use **Restore backup** to include a file again. You can also enable automatic deletion of originals after backup.

Use **Settings → About → Interface language** to switch between English and Simplified Chinese. The web app remembers your choice in the current browser. On first use it selects Simplified Chinese for a Chinese browser locale and English otherwise.

## Camera setup

1. Update the camera to firmware that supports CCAPI.
2. If CCAPI has yet to be activated, extract the Windows Activation Tool from [`ccapitool/`](ccapitool/) and activate CCAPI over USB. This is a one-time step.
3. On the camera, choose **Wi-Fi settings → Camera Control API → Connect** and select your Wi-Fi network. The camera displays an address such as `http://<camera-ip>:8080/ccapi`.
4. Open the displayed address in a browser on the same network and check that it returns JSON.

Use the **Camera Control API** connection option to start the CCAPI HTTP service. Canon AutoSync uses `/ccapi/ver130/contents` to browse camera files.

## Run locally

From the repository root, start the backend in one terminal.

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --port 8000
```

From the repository root, start the frontend in a second terminal.

```bash
cd frontend
npm ci
npm run dev
```

Open [the local app](http://localhost:5173) and set the **Camera IP**, **Port**, and **NAS backup folder** under **Settings**. You can also set `CANON_IP` and `NAS_PATH` in the backend's environment. Configuration and sync state live in `backend/data/` by default.

## Desktop app for macOS and Windows

Download a build from [GitHub Releases](https://github.com/xiaokaiwangcc/canon-autosync/releases). The desktop app launches FastAPI on an available loopback port and opens the same interface in a native window.

To run the desktop app from source, use the following commands from the repository root.

```bash
(cd frontend && npm ci && npm run build)
pip install -r backend/requirements.txt -r desktop/requirements.txt
python desktop/entry.py
```

To build the installer or executable locally, run the following commands after building the frontend.

```bash
python desktop/make_icons.py
pyinstaller desktop/CanonAutoSync.spec --noconfirm
```

Builds are written to `dist/CanonAutoSync.app` on macOS or `dist/CanonAutoSync.exe` on Windows. The Windows build uses WebView2.

The desktop app defaults to `~/Pictures/canon-backup` for backups. It stores configuration and sync state in these directories.

- macOS uses `~/Library/Application Support/canon-autosync/`.
- Windows uses `%APPDATA%\canon-autosync\`.

If macOS blocks the unsigned app when you first open it, look for **Open Anyway** under **System Settings → Privacy & Security**.

## Docker

```bash
docker compose up -d --build
```

Open port `8315` on the NAS or server. The Compose file uses host networking so the backend can reach the camera on the local network. Set the following values in a `.env` file to customize the deployment.

```dotenv
CANON_IP=192.168.1.50
BACKUP_DIR=/vol1/photos/canon-backup
DATA_DIR=./data
```

`BACKUP_DIR` is the host folder for backed-up media. `DATA_DIR` holds configuration and sync history. Both are mounted into the container.

## fnOS package

Run `./fpk/build.sh` with `fnpack` available to build the frontend and package the app. Upload `fpk/canon-autosync/canon-autosync.fpk` through **App Center → Manual Install**.

- The package uses the fnOS Python 3.12 runtime and installs its Python dependencies from PyPI on first start.
- Enter the camera IP and backup folder in the fnOS installer, which is in Simplified Chinese. Grant the app read and write access to the backup folder in fnOS app settings.
- The app serves its bilingual interface on port `8315`. Configuration, sync state, and `app.log` live in the fnOS package data directory (`TRIM_PKGVAR`).

## API

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/api/status` | Camera status, sync progress, and counts |
| GET / POST | `/api/config` | Read or save configuration |
| POST | `/api/sync` | Start a sync |
| POST | `/api/sync/stop` | Request a stop for the current sync |
| POST | `/api/reconnect` | Retry the camera connection |
| GET | `/api/files` | List backups with `offset` and `limit` |
| DELETE | `/api/files?path=` | Delete a backup record and its file on the NAS |
| POST | `/api/files/restore?path=` | Include an ignored camera original in automatic backups again |
| GET | `/api/pending` | List camera files that are pending backup |
| GET | `/api/thumb?path=` | Fetch a camera thumbnail |
| GET | `/api/preview?path=&size=` | Preview a file inside the backup folder |
