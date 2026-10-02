# MpvRemote

Share an URL, tap the buttons, and enjoy your videos on the big screen.

MpvRemote lets you control [MPV](https://mpv.io) on your computer from an Android phone. Share a video URL from any app, and MpvRemote loads it in MPV. You can also play/pause, skip tracks, stop playback, and clear the playlist from the app. The interface follows your phone's language and is available in English and French.

## What you can do

From the Android app:

- **Share a URL** - In any app (browser, YouTube, etc.), choose *Share* -> **MpvRemote**. The video is sent to MPV on your computer.
- **Play / pause** - Toggle playback.
- **Previous / next** - Skip between playlist items.
- **Stop** - Stop playback and return to MPV's idle screen. The playlist is kept, so Previous/Next resume from where you stopped.
- **Clear playlist** - Empty the queue.

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

When you first open MpvRemote, set two fields:

- **Relay base URL** - The address where the relay is reachable.
  - Local network example: `http://192.168.1.42:8765`
- **MPV socket path** - Default is `/tmp/mpv.socket`.

There is also an **Append to playlist** checkbox. When checked, shared URLs are appended to the end of the playlist instead of replacing the current item.

Tap **Save** to store the settings.

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

