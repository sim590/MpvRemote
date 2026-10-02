# MpvRemote

Share a URL, tap the buttons, and enjoy your videos on the big screen.

MpvRemote lets you control [MPV](https://mpv.io) on your computer from an Android phone. Share a video URL from any app, and MpvRemote loads it in MPV. The app has a side menu (tap the hamburger icon) with two pages: **Remote** for playback controls and the playlist, and **Settings** for the relay configuration. The interface follows your phone's language and is available in English and French.

## What you can do

<img src="showcase.png" align="right" width="240" alt="MpvRemote app screenshot">

From the Android app:

- **Share a URL** - In any app (browser, YouTube, etc.), choose *Share* -> **MpvRemote**. The video is sent to MPV on your computer.
- **Send clipboard** - Tap the button to send the URL currently on your clipboard.
- **Open links** - Configure MpvRemote to open YouTube and Twitch links directly; see [Open links with MpvRemote](#open-links-with-mpvremote).
- **Play / pause** - Toggle playback.
- **Previous / next** - Skip between playlist items.
- **Stop** - Stop playback and return to MPV's idle screen. The playlist is kept, so Previous/Next resume from where you stopped.
- **Seek** - Drag the position bar above the control buttons to jump to any point in the video.
- **Volume** - When the app is in the foreground, your phone's physical volume buttons control MPV's volume. MPV shows its on-screen display as usual.
- **Mute** - Toggle mute with the dedicated button.
- **Playlist** - View the current queue under the controls. The current item is highlighted. Tap an entry to play it, or tap the X to remove it. Pull down to refresh the list. Titles are resolved automatically when possible; otherwise the URL is shown. MPV's own title takes priority once playback starts.
- **Clear playlist** - Empty the queue.

<br clear="all">

## Components

MpvRemote has three simple pieces:

```
[MpvRemote Android app]
          |
          | HTTP requests
          v
[Python relay]  server/mpvremote_server.py
          |
          | Unix socket
          v
[MPV]  --input-ipc-server=/tmp/mpv.socket
```

- The **Android app** sends plain HTTP requests.
- The **relay** receives them and forwards commands to MPV over its control socket.
- **MPV** plays the video on your computer.

## Requirements

- `python` - for the relay.
- `mpv` - the video player.

## Quick start

1. **Start MPV with a control socket:**

   ```bash
   mpv --idle=yes --force-window=immediate --input-ipc-server=/tmp/mpv.socket
   ```

   `--idle=yes` keeps MPV running even when the playlist is empty, and
   `--force-window=immediate` opens the video window right away, before
   the first URL is sent by the app.

   When the last playlist item finishes, the relay disables MPV's
   `keep-open`, so MPV unloads the file and returns to its idle screen
   ("drop a file or URL") instead of freezing on the last frame.

2. **Start the relay:**

   ```bash
   python server/mpvremote_server.py
   ```

   By default it listens on `127.0.0.1:8765` and talks to `/tmp/mpv.socket`.

## Install the Android app

After building, the debug APK is located at:

```
Android/app/build/outputs/apk/debug/app-debug.apk
```

Install it with `adb`:

```bash
adb install Android/app/build/outputs/apk/debug/app-debug.apk
```

Or copy the APK to your phone and open it with a file manager.

## Configure the app

Open the side menu and tap **Settings**. Set two fields:

- **Relay base URL** - The address where the relay is reachable.
  - Local network example: `http://192.168.1.42:8765`
- **MPV socket path** - Default is `/tmp/mpv.socket`.

There is also an **Append to playlist** checkbox. When checked, shared URLs are appended to the end of the playlist instead of replacing the current item.

Tap **Save** to store the settings.

## Open links with MpvRemote

MpvRemote can open YouTube and Twitch links directly. On Android 12+, you must enable this manually because MpvRemote does not own those domains:

- Go to **Settings > Apps > MpvRemote > Open by default**, turn on "Open supported links", then check the YouTube and Twitch domains.
- Or tap **Open link settings** in the app's Settings page.

Only some paths are intercepted (watch, shorts, live, clips, etc.). Firefox Android has "Open links in apps" disabled by default; enable it if you want links to open in MpvRemote.

## Connect over the local network

The recommended way to use MpvRemote is over your home Wi-Fi.

1. Find your computer's local IP address, for example `192.168.1.42`.
2. Start the relay on all interfaces:

   ```bash
   python server/mpvremote_server.py --host 0.0.0.0
   ```

3. In the app, set the relay URL to:

   ```
   http://192.168.1.42:8765
   ```

Make sure your phone and computer are on the same network.

## Security note

- By default, the relay only listens on `127.0.0.1`. This is intentional: nothing else on the network can reach it.
- With `--host 0.0.0.0`, the relay is exposed to your local network with no encryption or authentication. Only use this on a trusted private network.
- The relay only accepts `http`/`https` URLs and limits request body size.

See `CONTRIBUTING.md` for build and development instructions.
