# Android apps and phone backup

Two small apps in `android/` (plain Java, no libraries, built in a throwaway Docker container). They are separate apps with separate package names.

- **reclmr** (`android/shell`): a WebView on your server, drawn edge to edge. It adds lock-screen music controls (play, skip, shuffle, repeat, seek) that keep playing with the screen off, keeps the screen on while Now Playing is open, turns off the system's left-edge back swipe while it is open (so the page's own edge swipe works), and adds long-press shortcuts and sharing text into Search. Build with `./scripts/build_android.sh shell`, install with `./scripts/install_app.sh`.
- **reclmr Backup** (`android/pusher`): sends new photos, screenshots, downloads and documents to the server. Build with `./scripts/build_android.sh pusher`, install with `./scripts/install_pusher.sh`.

Both install scripts talk to a shell you already have on the phone (an adb-style sshd, or `adb shell`). Put its address in `.env` as `RECLMR_PHONE=shell@<phone address>`, or pass `PHONE=...`.

### Phone backup

Backup runs through Android's own `JobScheduler`: the system wakes it shortly after new media appears, and the schedule survives reboots. There is no daemon. Uploads resume if the connection drops. You can limit it to Wi-Fi, pause or skip a file from its notification, and on mobile data it sends files up to a size limit (25 MB by default) before asking about larger ones.

The server side is off until you set a token. The phone's token is separate from your login and can only add files: it cannot list, read, overwrite or delete anything.

```bash
# .env
RECLMR_BACKUP_TOKEN=<24+ random characters>     # python3 -c "import secrets; print(secrets.token_urlsafe(36))"
RECLMR_BACKUP_URL=https://your-box.example.ts.net
RECLMR_BACKUP_DIR=/path/to/backups              # Camera/, Screenshots/, Downloads/, Documents/ are created inside
```

Files are never overwritten and are written as `.part` then renamed. The APK has the address and token compiled in, so keep it private; rotate the token by rebuilding and installing again.
