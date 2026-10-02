#!/usr/bin/env python3
"""HTTP relay to MPV's JSON IPC server.

A small HTTP server written with the Python 3 standard library only. It
accepts ``POST /play`` with a JSON body ``{"url": ..., "socket": ...,
"append": ...}`` (unknown fields, such as the legacy ``fallback``, are
ignored) and sends, in order, the following MPV IPC commands to MPV's
Unix control socket:

1. ``set_property`` to disable keep-open (``keep-open`` to ``no``), so
   MPV returns to its idle screen between videos instead of staying on
   the last frame, regardless of the options MPV was started with;
2. ``loadfile`` of the requested URL with the ``append-play`` flag if the
   ``append`` field is ``true`` (the video is appended without
   interrupting current playback, and playback starts if the playlist is
   empty, e.g. with ``mpv --idle=yes``), otherwise with the ``replace``
   flag;
3. ``set_property`` to release the pause (``pause`` to ``no``).

Each command waits for MPV's reply before the next one. The ``append``
field is optional and defaults to ``false``.

The relay also accepts ``POST /control`` for safe, predefined actions
(no arbitrary IPC command is accepted): ``toggle_pause`` (``cycle
pause``), ``next``/``previous`` (``playlist-next weak`` /
``playlist-prev weak``), ``stop`` (``stop keep-playlist``, which
remembers the stopped index so that ``next``/``previous`` can resume
from it with ``playlist-play-index`` while MPV is idle) and ``clear``
(``stop``, which empties the playlist). A new ``POST /play`` or a
``clear`` forgets the remembered index.

Examples:

    python server/mpvremote_server.py
    curl -X POST http://127.0.0.1:8765/play \\
        -d '{"url": "https://example.com/video.mp4"}'
    curl -X POST http://127.0.0.1:8765/control \\
        -d '{"action": "toggle_pause"}'
"""

import argparse
import json
import logging
import os
import socket
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import cast
from urllib.parse import urlparse

LOG = logging.getLogger("mpvremote")

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765
DEFAULT_SOCKET = "/tmp/mpv.socket"
DEFAULT_MAX_BODY = 8192      # bytes
DEFAULT_MPV_TIMEOUT = 5.0    # seconds
ALLOWED_SCHEMES = ("http", "https")

# Safe POST /control actions: name -> steps (name, command).
# Semantics validated against the MPV documentation (man mpv, section
# "Command Interface"): stop keep-playlist stops playback but keeps the
# playlist, playlist-next/prev with weak do nothing at the end of the
# playlist, cycle pause toggles.
CONTROL_ACTIONS = {
    "toggle_pause": [("basculer la pause", ["cycle", "pause"])],
    "next": [("piste suivante", ["playlist-next", "weak"])],
    "previous": [("piste précédente", ["playlist-prev", "weak"])],
    "stop": [("arrêter la lecture", ["stop", "keep-playlist"])],
    "clear": [("vider la file de lecture", ["stop"])],
}


def is_valid_url(value):
    """Return True if ``value`` is an http/https URL with a host."""
    try:
        parsed = urlparse(value)
    except (TypeError, ValueError):
        return False
    return parsed.scheme in ALLOWED_SCHEMES and bool(parsed.netloc)


def is_valid_socket_path(value):
    """Return True if ``value`` is an absolute path."""
    return isinstance(value, str) and os.path.isabs(value)


def send_mpv_command(socket_path, command, timeout=DEFAULT_MPV_TIMEOUT):
    """Send a JSON newline IPC command to MPV's Unix socket.

    Each command carries a unique ``request_id``. The relay reads the
    JSON lines it receives until it finds the response carrying that
    ``request_id`` (with an ``error`` field): MPV's asynchronous events
    and responses to other requests are ignored. Several lines may
    arrive in a single ``recv``.

    Returns MPV's response dictionary, or an error dictionary
    ``{"error": ...}`` if the socket is closed or the timeout expires.
    Raises ``OSError`` if the socket is unreachable.
    """
    request_id = int(time.time() * 1000)
    payload = {"command": command, "request_id": request_id}
    line = (json.dumps(payload) + "\n").encode("utf-8")

    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        sock.settimeout(timeout)
        sock.connect(socket_path)
        sock.sendall(line)

        deadline = time.monotonic() + timeout
        try:
            buffer = b""
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return {"error": "timeout", "request_id": request_id}
                sock.settimeout(remaining)
                chunk = sock.recv(4096)
                if not chunk:
                    # MPV closed the connection without replying.
                    return {"error": "connection closed",
                            "request_id": request_id}
                buffer += chunk
                # Process every complete JSON line received so far.
                while b"\n" in buffer:
                    raw_line, buffer = buffer.split(b"\n", 1)
                    if not raw_line.strip():
                        continue
                    try:
                        obj = json.loads(raw_line.decode("utf-8"))
                    except (UnicodeDecodeError, json.JSONDecodeError):
                        continue  # unparseable line: keep going
                    # Only the response carrying our request_id and
                    # containing an "error" field is of interest.
                    if (isinstance(obj, dict)
                            and obj.get("request_id") == request_id
                            and "error" in obj):
                        return obj
        except socket.timeout:
            return {"error": "timeout", "request_id": request_id}
        except ConnectionError:
            return {"error": "connection closed",
                    "request_id": request_id}
    finally:
        sock.close()


class RelayServer(ThreadingHTTPServer):
    """HTTP server that carries the relay configuration."""

    allow_reuse_address = True

    def __init__(self, addr, handler, *, default_socket,
                 mpv_timeout, max_body):
        super().__init__(addr, handler)
        self.default_socket = default_socket
        self.mpv_timeout = mpv_timeout
        self.max_body = max_body
        # Resume positions, keyed by socket path (socket path -> playlist
        # index). Shared across the ThreadingHTTPServer request threads,
        # hence the lock.
        self._resume_lock = threading.Lock()
        self._resume_index = {}

    def remember_resume(self, socket_path, index):
        """Remember ``index`` as the resume position for ``socket_path``."""
        with self._resume_lock:
            self._resume_index[socket_path] = index

    def get_resume(self, socket_path):
        """Return the remembered resume index for ``socket_path``, or None."""
        with self._resume_lock:
            return self._resume_index.get(socket_path)

    def clear_resume(self, socket_path):
        """Forget any resume position for ``socket_path``."""
        with self._resume_lock:
            self._resume_index.pop(socket_path, None)


class RelayHandler(BaseHTTPRequestHandler):
    """Handle HTTP requests and translate them into MPV IPC."""

    server_version = "mpvremote/1.0"
    sys_version = ""

    @property
    def _server(self) -> RelayServer:
        """The server, typed as ``RelayServer``."""
        return cast(RelayServer, self.server)

    # --- JSON responses -----------------------------------------------

    def _send_json(self, status, obj, extra_headers=None):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        for key, value in extra_headers or ():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)

    def _method_not_allowed(self):
        self._send_json(
            405,
            {"error": "méthode non permise"},
            extra_headers=[("Allow", "GET, POST")],
        )

    # Methods other than GET and POST are refused.
    do_PUT = do_DELETE = do_PATCH = do_OPTIONS = do_HEAD = _method_not_allowed

    def _route(self):
        return urlparse(self.path).path

    # --- reading the request body -------------------------------------

    def _read_body(self):
        """Read the JSON body, enforcing the size limit.

        Returns ``(body, error)``.
        """
        raw_length = self.headers.get("Content-Length")
        if raw_length is None:
            length = 0
        else:
            try:
                length = int(raw_length)
            except ValueError:
                return None, "Content-Length invalide"
        if length < 0:
            return None, "Content-Length négatif"
        if length > self._server.max_body:
            # Partially drain the socket to avoid connection resets on
            # the client side, then reject the request.
            self.rfile.read(self._server.max_body + 1)
            return None, "corps de requête trop volumineux"
        return self.rfile.read(length) if length else b"", None

    # --- routes -------------------------------------------------------

    def do_GET(self):
        route = self._route()
        if route == "/health":
            self._send_json(200, {"status": "ok"})
        elif route == "/":
            self._send_json(
                200,
                {
                    "service": "mpvremote",
                    "usage": "POST /play  {\"url\": \"http(s)://...\", "
                             "\"socket\": \"/chemin/optionnel\", "
                             "\"append\": false} ; POST /control  "
                             "{\"action\": \"toggle_pause|next|previous|"
                             "stop|clear\", \"socket\": \"/chemin/optionnel\"}",
                },
            )
        else:
            self._send_json(404, {"error": "route inconnue"})

    def do_POST(self):
        route = self._route()
        if route not in ("/play", "/control"):
            self._send_json(404, {"error": "route inconnue"})
            return

        body, err = self._read_body()
        if err is not None:
            status = 413 if "volumineux" in err else 400
            self._send_json(status, {"error": err})
            return
        if not body:
            self._send_json(400, {"error": "corps JSON requis"})
            return

        try:
            data = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            self._send_json(400, {"error": "JSON invalide"})
            return
        if not isinstance(data, dict):
            self._send_json(400, {"error": "le corps doit être un objet JSON"})
            return

        if route == "/play":
            self._handle_play(data)
        else:
            self._handle_control(data)

    def _call_mpv(self, socket_path, step, command):
        """Run a single MPV command (correlated by ``request_id``) and
        return its reply, or None after sending the appropriate HTTP 502
        response on failure."""
        try:
            reply = send_mpv_command(
                socket_path, command, self._server.mpv_timeout)
        except OSError as exc:
            LOG.warning("étape \"%s\" : prise MPV injoignable (%s) : %s",
                        step, socket_path, exc)
            self._send_json(
                502,
                {"error": "prise MPV injoignable",
                 "step": step,
                 "detail": str(exc)},
            )
            return None
        if reply.get("error") != "success":
            LOG.warning("étape \"%s\" : MPV a rejeté la commande : %s",
                        step, reply.get("error"))
            self._send_json(
                502,
                {"error": "MPV a rejeté la commande",
                 "step": step,
                 "detail": reply.get("error", "inconnu")},
            )
            return None
        return reply

    def _run_mpv_steps(self, socket_path, steps):
        """Run MPV steps sequentially; each command waits for its reply,
        correlated by ``request_id`` (asynchronous events are ignored).
        On failure, the HTTP 502 response has already been sent and
        False is returned."""
        for step, command in steps:
            if self._call_mpv(socket_path, step, command) is None:
                return False
        return True

    def _handle_play(self, data):
        url = data.get("url")
        socket_path = data.get("socket") or self._server.default_socket
        append = data.get("append")
        if not isinstance(url, str) or not is_valid_url(url):
            self._send_json(
                400, {"error": "\"url\" doit être une URL http(s) valide"})
            return
        if not is_valid_socket_path(socket_path):
            self._send_json(
                400, {"error": "\"socket\" doit être un chemin absolu"})
            return
        if append is None:
            append = False
        elif not isinstance(append, bool):
            self._send_json(
                400, {"error": "\"append\" doit être un booléen"})
            return

        # A new playback request invalidates any pending resume position.
        self._server.clear_resume(socket_path)

        # Sequence: first disable keep-open, so that when the last video
        # ends MPV returns to its idle screen (idle-active=True,
        # playlist-pos=-1) instead of staying on the last frame -- this
        # holds regardless of how MPV was launched (e.g. --keep-open=yes),
        # and being first also covers very short files. Then load the
        # video. If the app asks to append ("append" true), use
        # "append-play": the video is appended without interrupting
        # current playback, and playback starts if the playlist is empty
        # (e.g. mpv --idle=yes), unlike "append" which would leave the
        # playlist empty with nothing playing. Otherwise replace current
        # playback with "replace". Finally, release the pause. Unknown
        # fields (legacy "fallback") are ignored.
        mode = "append-play" if append else "replace"
        steps = [
            ("désactiver keep-open",
             ["set_property", "keep-open", "no"]),
            ("charger la vidéo", ["loadfile", url, mode]),
            ("reprendre la lecture", ["set_property", "pause", "no"]),
        ]
        if not self._run_mpv_steps(socket_path, steps):
            return
        self._send_json(200, {"status": "ok", "error": "success"})

    def _handle_control(self, data):
        action = data.get("action")
        socket_path = data.get("socket") or self._server.default_socket
        if not isinstance(action, str) or action not in CONTROL_ACTIONS:
            self._send_json(
                400,
                {"error": "\"action\" inconnue ou manquante",
                 "allowed": sorted(CONTROL_ACTIONS)},
            )
            return
        if not is_valid_socket_path(socket_path):
            self._send_json(
                400, {"error": "\"socket\" doit être un chemin absolu"})
            return

        if action == "stop":
            self._control_stop(socket_path)
        elif action in ("next", "previous"):
            self._control_skip(socket_path, action)
        elif action == "clear":
            # Empty the playlist and forget any pending resume position.
            if not self._run_mpv_steps(
                    socket_path, list(CONTROL_ACTIONS["clear"])):
                return
            self._server.clear_resume(socket_path)
            self._send_json(200, {"status": "ok", "error": "success"})
        else:
            # toggle_pause is unconditional.
            if not self._run_mpv_steps(
                    socket_path, list(CONTROL_ACTIONS[action])):
                return
            self._send_json(200, {"status": "ok", "error": "success"})

    def _control_stop(self, socket_path):
        """Stop playback but keep the playlist, remembering the current
        index for next/previous. The index must be read BEFORE the stop:
        right after ``stop``, playlist-pos is transiently reset and would
        report the wrong value."""
        reply = self._call_mpv(socket_path, "lire la position",
                               ["get_property", "playlist-pos"])
        if reply is None:
            return
        pos = reply.get("data")
        if isinstance(pos, int) and pos >= 0:
            self._server.remember_resume(socket_path, pos)
        # A value of -1 leaves any existing resume memory untouched.
        if not self._run_mpv_steps(
                socket_path, list(CONTROL_ACTIONS["stop"])):
            return
        self._send_json(200, {"status": "ok", "error": "success"})

    def _control_skip(self, socket_path, action):
        """Go to the next/previous entry. If playback was stopped (idle)
        and an index was remembered, jump straight to memory +/- 1 with
        playlist-play-index; otherwise fall back to the weak
        playlist-next/prev command."""
        resume = self._server.get_resume(socket_path)
        if resume is not None:
            reply = self._call_mpv(socket_path, "lire idle-active",
                                   ["get_property", "idle-active"])
            if reply is None:
                return
            if reply.get("data") is True:
                target = resume + 1 if action == "next" else resume - 1
                count_reply = self._call_mpv(
                    socket_path, "lire playlist-count",
                    ["get_property", "playlist-count"])
                if count_reply is None:
                    return
                total = count_reply.get("data")
                if isinstance(total, int) and 0 <= target < total:
                    if not self._run_mpv_steps(
                            socket_path,
                            [("reprendre à la position mémorisée",
                              ["playlist-play-index", target])]):
                        return
                    self._server.clear_resume(socket_path)
                # Out of bounds: no command, keep the memory.
                self._send_json(200, {"status": "ok", "error": "success"})
                return
            # Not idle: forget the stale memory and fall back below.
        if not self._run_mpv_steps(
                socket_path, list(CONTROL_ACTIONS[action])):
            return
        self._server.clear_resume(socket_path)
        self._send_json(200, {"status": "ok", "error": "success"})

    # --- logging --------------------------------------------------------

    def log_message(self, format, *args):
        LOG.info("%s %s", self.address_string(), format % args)


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        prog="mpvremote_server",
        description="Relais HTTP vers l'IPC JSON de MPV "
                    "(bibliothèque standard uniquement).",
        epilog="Sécurité : le relais écoute sur 127.0.0.1 par défaut. "
               "Ne pas l'exposer sur 0.0.0.0 sans protection.",
    )
    parser.add_argument("--host", default=DEFAULT_HOST,
                        help="adresse d'écoute (défaut : %(default)s)")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT,
                        help="port d'écoute (défaut : %(default)s)")
    parser.add_argument("--socket", default=DEFAULT_SOCKET,
                        help="prise de contrôle MPV par défaut "
                             "(défaut : %(default)s)")
    parser.add_argument("--max-body", type=int, default=DEFAULT_MAX_BODY,
                        help="taille maximale du corps de requête, en octets "
                             "(défaut : %(default)s)")
    parser.add_argument("--mpv-timeout", type=float, default=DEFAULT_MPV_TIMEOUT,
                        help="délai maximal en secondes pour la réponse de MPV "
                             "(défaut : %(default)s)")
    parser.add_argument("--verbose", action="store_true",
                        help="journalisation détaillée")
    return parser, parser.parse_args(argv)


def main(argv=None):
    parser, args = parse_args(argv)
    if not os.path.isabs(args.socket):
        parser.error("--socket doit être un chemin absolu")
    if args.max_body <= 0:
        parser.error("--max-body doit être un entier positif")
    if args.mpv_timeout <= 0:
        parser.error("--mpv-timeout doit être un nombre positif")

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s : %(message)s",
    )

    server = RelayServer(
        (args.host, args.port),
        RelayHandler,
        default_socket=args.socket,
        mpv_timeout=args.mpv_timeout,
        max_body=args.max_body,
    )
    LOG.info("relais en écoute sur http://%s:%s (prise MPV par défaut : %s)",
             args.host, args.port, args.socket)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        LOG.info("arrêt demandé")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
