#!/usr/bin/env python3
"""Reproducible tests for the MPV HTTP relay.

Starts fake MPV IPC servers (Unix sockets in a temporary directory) and
the HTTP relay on an ephemeral port, then checks the HTTP responses as
well as the order and arguments of the IPC commands sent. No external
dependency: standard library only.

Run with:

    python3 server/test_mpvremote.py
"""

import http.client
import json
import os
import socket
import tempfile
import threading
import time
import unittest
import uuid

import mpvremote_server as relay

# Legacy "fallback" field from older client versions: the relay must
# ignore it (not reject it).
LEGACY_FALLBACK = "/home/deck/Images/Icônes/mpv.png"


class FakeMpvServer:
    """Fake Unix socket that speaks MPV's JSON newline IPC protocol.

    Records every command received, in order, and echoes the received
    ``request_id`` in its replies (like MPV). Several commands may be
    sent on the same connection, each getting its own reply. ``fail_on``
    (1-based index) simulates rejection of a specific command. ``events``
    sends that many JSON events before each reply (as MPV may do after a
    ``loadfile``) and ``events_together`` sends events and reply in a
    single packet (several lines in one ``recv``). ``other_response_first``
    sends the reply of another request first (different ``request_id``),
    which the relay must ignore. ``no_reply`` keeps the connection open
    without replying and ``close_after_command`` closes the connection
    without replying. ``props`` is a configurable table of property values
    returned by ``get_property`` commands, and ``unavailable`` is a set of
    property names for which ``get_property`` reports "property
    unavailable". ``connection_count`` counts the accepted connections.
    """

    def __init__(self, socket_path, fail_on=None, error="init-failed",
                 events=0, events_together=False, other_response_first=False,
                 no_reply=False, close_after_command=False, props=None,
                 unavailable=None):
        self.socket_path = socket_path
        self.fail_on = fail_on
        self.error = error
        self.events = events
        self.events_together = events_together
        self.other_response_first = other_response_first
        self.no_reply = no_reply
        self.close_after_command = close_after_command
        self.props = props if props is not None else {}
        self.unavailable = set(unavailable) if unavailable else set()
        self.connection_count = 0
        self.received_commands = []
        self._sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._sock.bind(socket_path)
        self._sock.listen(1)
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    def _serve(self):
        while True:
            try:
                conn, _ = self._sock.accept()
            except OSError:
                return  # socket closed
            self.connection_count += 1
            with conn:
                buffer = b""
                while True:
                    while b"\n" not in buffer:
                        chunk = conn.recv(4096)
                        if not chunk:
                            break
                        buffer += chunk
                    if b"\n" not in buffer:
                        break  # client closed the connection
                    raw_line, buffer = buffer.split(b"\n", 1)
                    if not raw_line.strip():
                        continue
                    payload = json.loads(raw_line.decode("utf-8"))
                    command = payload["command"]
                    self.received_commands.append(command)
                    request_id = payload.get("request_id")
                    if self.no_reply:
                        time.sleep(10)
                        break
                    if self.close_after_command:
                        break  # close the connection without replying
                    if (self.fail_on is not None
                            and len(self.received_commands) == self.fail_on):
                        reply = {"error": self.error,
                                 "request_id": request_id}
                    elif command and command[0] == "get_property":
                        name = command[1]
                        if name in self.unavailable:
                            reply = {"error": "property unavailable",
                                     "request_id": request_id}
                        else:
                            reply = {"data": self.props.get(name),
                                     "error": "success",
                                     "request_id": request_id}
                    else:
                        reply = {"error": "success",
                                 "request_id": request_id}

                    lines = []
                    if self.other_response_first:
                        lines.append(json.dumps(
                            {"error": "success",
                             "request_id": (request_id or 0) + 1}))
                    for i in range(self.events):
                        lines.append(json.dumps(
                            {"event": "start-file", "id": i}))
                    lines.append(json.dumps(reply))

                    if self.events_together:
                        conn.sendall(("\n".join(lines) + "\n").encode("utf-8"))
                    else:
                        for line in lines:
                            conn.sendall((line + "\n").encode("utf-8"))

    def close(self):
        self._sock.close()


class RelayHttpTests(unittest.TestCase):
    """Check the HTTP responses and the MPV command sequence."""

    @classmethod
    def setUpClass(cls):
        cls.tmpdir = tempfile.TemporaryDirectory()
        cls.shared_socket = os.path.join(cls.tmpdir.name, "mpv-shared.sock")
        cls.shared_mpv = FakeMpvServer(
            cls.shared_socket,
            props={"playlist": [], "playlist-pos": -1, "idle-active": True})
        cls.server = relay.RelayServer(
            ("127.0.0.1", 0),
            relay.RelayHandler,
            default_socket=cls.shared_socket,
            mpv_timeout=2.0,
            max_body=512,
        )
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(
            target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.shared_mpv.close()
        cls.tmpdir.cleanup()

    def fake_mpv(self, fail_on=None, events=0, events_together=False,
                 other_response_first=False, no_reply=False,
                 close_after_command=False, props=None, unavailable=None):
        """Create a fresh fake MPV on a dedicated socket."""
        path = os.path.join(self.tmpdir.name, "mpv-%s.sock" % uuid.uuid4().hex)
        return FakeMpvServer(path, fail_on=fail_on, events=events,
                             events_together=events_together,
                             other_response_first=other_response_first,
                             no_reply=no_reply,
                             close_after_command=close_after_command,
                             props=props, unavailable=unavailable), path

    def request(self, method, path, body=None, headers=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        try:
            conn.request(method, path, body=body, headers=headers or {})
            resp = conn.getresponse()
            return resp.status, resp.read().decode("utf-8")
        finally:
            conn.close()

    # --- /play sequence ------------------------------------------------

    def test_play_sequence_replace_default(self):
        """Without append: loadfile with replace, then release the pause."""
        mpv, sock = self.fake_mpv()
        status, body = self.request(
            "POST", "/play",
            body=json.dumps({"url": "https://example.com/video.mp4",
                             "socket": sock}),
        )
        self.assertEqual(status, 200)
        self.assertIn("success", body)
        self.assertEqual(mpv.received_commands, [
            ["set_property", "keep-open", "no"],
            ["loadfile", "https://example.com/video.mp4", "replace"],
            ["set_property", "pause", "no"],
        ])

    def test_play_sequence_starts_with_keep_open_no(self):
        """The sequence starts with ``set_property keep-open no``, so that
        when the last video ends MPV returns to its idle screen
        (idle-active=True, playlist-pos=-1) instead of staying on the last
        frame -- even if MPV was launched with ``--keep-open=yes``."""
        mpv, sock = self.fake_mpv()
        status, _ = self.request(
            "POST", "/play",
            body=json.dumps({"url": "https://example.com/video.mp4",
                             "socket": sock}),
        )
        self.assertEqual(status, 200)
        self.assertEqual(mpv.received_commands[0],
                         ["set_property", "keep-open", "no"])

    def test_play_sequence_append_play(self):
        """With append: loadfile with append-play, then release."""
        mpv, sock = self.fake_mpv()
        status, _ = self.request(
            "POST", "/play",
            body=json.dumps({"url": "http://example.com/v.mp4",
                             "socket": sock, "append": True}),
        )
        self.assertEqual(status, 200)
        self.assertEqual(mpv.received_commands, [
            ["set_property", "keep-open", "no"],
            ["loadfile", "http://example.com/v.mp4", "append-play"],
            ["set_property", "pause", "no"],
        ])

    def test_play_append_false_explicit(self):
        """Explicit append=false: loadfile with replace."""
        mpv, sock = self.fake_mpv()
        status, _ = self.request(
            "POST", "/play",
            body=json.dumps({"url": "https://example.com/v.mp4",
                             "socket": sock, "append": False}),
        )
        self.assertEqual(status, 200)
        self.assertEqual(mpv.received_commands[1],
                         ["loadfile", "https://example.com/v.mp4",
                          "replace"])

    def test_play_append_play_starts_on_empty_playlist(self):
        """Empty playlist case (``mpv --idle=yes``): the URL is loaded
        with ``append-play``, which both appends the entry AND starts
        playback when nothing is playing. Plain ``append`` would leave the
        playlist empty with nothing playing and no window opened."""
        mpv, sock = self.fake_mpv()
        status, _ = self.request(
            "POST", "/play",
            body=json.dumps({"url": "https://example.com/video.mp4",
                             "socket": sock, "append": True}),
        )
        self.assertEqual(status, 200)
        self.assertEqual(mpv.received_commands, [
            ["set_property", "keep-open", "no"],
            ["loadfile", "https://example.com/video.mp4", "append-play"],
            ["set_property", "pause", "no"],
        ])
        # The loadfile command must use append-play (starts playback when
        # the playlist is empty), not plain append (which does not start
        # it).
        self.assertEqual(mpv.received_commands[1][-1], "append-play")
        self.assertNotEqual(mpv.received_commands[1][-1], "append")

    def test_play_ignores_legacy_fallback_field(self):
        """A legacy "fallback" field is ignored, not rejected: no extra
        loadfile command is sent."""
        mpv, sock = self.fake_mpv()
        status, _ = self.request(
            "POST", "/play",
            body=json.dumps({"url": "https://example.com/video.mp4",
                             "socket": sock, "fallback": LEGACY_FALLBACK}),
        )
        self.assertEqual(status, 200)
        self.assertEqual(mpv.received_commands, [
            ["set_property", "keep-open", "no"],
            ["loadfile", "https://example.com/video.mp4", "replace"],
            ["set_property", "pause", "no"],
        ])

    def test_play_ignores_arbitrary_fallback_value(self):
        """Even an invalid fallback value (relative) is ignored: the field
        is no longer validated at all."""
        mpv, sock = self.fake_mpv()
        status, _ = self.request(
            "POST", "/play",
            body=json.dumps({"url": "https://example.com/video.mp4",
                             "socket": sock, "fallback": "relatif.png"}),
        )
        self.assertEqual(status, 200)
        self.assertEqual(mpv.received_commands, [
            ["set_property", "keep-open", "no"],
            ["loadfile", "https://example.com/video.mp4", "replace"],
            ["set_property", "pause", "no"],
        ])

    def test_play_default_socket(self):
        status, _ = self.request(
            "POST", "/play",
            body=json.dumps({"url": "https://example.com/video.mp4"}),
        )
        self.assertEqual(status, 200)

    # --- MPV errors per step -------------------------------------------

    def test_mpv_error_on_keep_open_step(self):
        """The 1st step (disable keep-open) fails: 502 with the step."""
        _, sock = self.fake_mpv(fail_on=1)
        status, body = self.request(
            "POST", "/play",
            body=json.dumps({"url": "https://example.com/v.mp4",
                             "socket": sock}),
        )
        self.assertEqual(status, 502)
        self.assertIn("désactiver keep-open", body)

    def test_mpv_error_on_loadfile_step(self):
        """The 2nd step (loadfile) fails: 502 with the step."""
        _, sock = self.fake_mpv(fail_on=2)
        status, body = self.request(
            "POST", "/play",
            body=json.dumps({"url": "https://example.com/v.mp4",
                             "socket": sock}),
        )
        self.assertEqual(status, 502)
        self.assertIn("charger la vidéo", body)

    def test_mpv_error_on_unpause_step(self):
        """The 3rd step (release the pause) fails: 502 with the step."""
        _, sock = self.fake_mpv(fail_on=3)
        status, body = self.request(
            "POST", "/play",
            body=json.dumps({"url": "https://example.com/v.mp4",
                             "socket": sock}),
        )
        self.assertEqual(status, 502)
        self.assertIn("reprendre la lecture", body)

    # --- MPV events and connection closure -----------------------------

    def test_play_event_before_response(self):
        """An MPV event before the reply does not break the sequence."""
        mpv, sock = self.fake_mpv(events=1)
        status, _ = self.request(
            "POST", "/play",
            body=json.dumps({"url": "https://example.com/video.mp4",
                             "socket": sock}),
        )
        self.assertEqual(status, 200)
        self.assertEqual(mpv.received_commands, [
            ["set_property", "keep-open", "no"],
            ["loadfile", "https://example.com/video.mp4", "replace"],
            ["set_property", "pause", "no"],
        ])

    def test_play_multiple_lines_in_one_recv(self):
        """Events and reply sent in a single packet (several lines in one
        recv)."""
        mpv, sock = self.fake_mpv(events=2, events_together=True)
        status, _ = self.request(
            "POST", "/play",
            body=json.dumps({"url": "https://example.com/video.mp4",
                             "socket": sock}),
        )
        self.assertEqual(status, 200)
        self.assertEqual(mpv.received_commands, [
            ["set_property", "keep-open", "no"],
            ["loadfile", "https://example.com/video.mp4", "replace"],
            ["set_property", "pause", "no"],
        ])

    def test_play_ignores_other_request_response(self):
        """The reply of another request (different request_id) is ignored
        in favour of the right one."""
        mpv, sock = self.fake_mpv(other_response_first=True)
        status, _ = self.request(
            "POST", "/play",
            body=json.dumps({"url": "https://example.com/video.mp4",
                             "socket": sock}),
        )
        self.assertEqual(status, 200)
        self.assertEqual(mpv.received_commands, [
            ["set_property", "keep-open", "no"],
            ["loadfile", "https://example.com/video.mp4", "replace"],
            ["set_property", "pause", "no"],
        ])

    def test_mpv_closed_before_reply(self):
        """MPV closes the connection without replying: 502 with a clear
        error."""
        _, sock = self.fake_mpv(close_after_command=True)
        status, body = self.request(
            "POST", "/play",
            body=json.dumps({"url": "https://example.com/v.mp4",
                             "socket": sock}),
        )
        self.assertEqual(status, 502)
        self.assertIn("connection closed", body)

    def test_mpv_timeout_returns_502(self):
        """MPV does not reply: 502 after the timeout, with a clear error."""
        _, sock = self.fake_mpv(no_reply=True)
        status, body = self.request(
            "POST", "/play",
            body=json.dumps({"url": "https://example.com/v.mp4",
                             "socket": sock}),
        )
        self.assertEqual(status, 502)
        self.assertIn("timeout", body)

    # --- control (POST /control) ---------------------------------------

    def test_control_toggle_pause(self):
        mpv, sock = self.fake_mpv()
        status, body = self.request(
            "POST", "/control",
            body=json.dumps({"action": "toggle_pause", "socket": sock}),
        )
        self.assertEqual(status, 200)
        self.assertIn("success", body)
        self.assertEqual(mpv.received_commands, [["cycle", "pause"]])

    def test_control_next(self):
        mpv, sock = self.fake_mpv()
        status, _ = self.request(
            "POST", "/control",
            body=json.dumps({"action": "next", "socket": sock}),
        )
        self.assertEqual(status, 200)
        self.assertEqual(mpv.received_commands,
                         [["playlist-next", "weak"]])

    def test_control_previous(self):
        mpv, sock = self.fake_mpv()
        status, _ = self.request(
            "POST", "/control",
            body=json.dumps({"action": "previous", "socket": sock}),
        )
        self.assertEqual(status, 200)
        self.assertEqual(mpv.received_commands,
                         [["playlist-prev", "weak"]])

    def test_control_volume_up(self):
        """volume_up sends a single osd-msg-bar command, MPV's native way
        to change the volume and show the OSD bar."""
        mpv, sock = self.fake_mpv()
        status, _ = self.request(
            "POST", "/control",
            body=json.dumps({"action": "volume_up", "socket": sock}),
        )
        self.assertEqual(status, 200)
        self.assertEqual(mpv.received_commands, [
            ["osd-msg-bar", "add", "volume", 5],
        ])

    def test_control_volume_down(self):
        """volume_down sends a single osd-msg-bar command."""
        mpv, sock = self.fake_mpv()
        status, _ = self.request(
            "POST", "/control",
            body=json.dumps({"action": "volume_down", "socket": sock}),
        )
        self.assertEqual(status, 200)
        self.assertEqual(mpv.received_commands, [
            ["osd-msg-bar", "add", "volume", -5],
        ])

    def test_control_volume_default_socket(self):
        """The volume actions work with the default socket."""
        status, _ = self.request(
            "POST", "/control",
            body=json.dumps({"action": "volume_up"}),
        )
        self.assertEqual(status, 200)

    def test_control_toggle_mute_true(self):
        """toggle_mute cycles mute, reads it back, then shows a literal OSD
        text, in that order."""
        mpv, sock = self.fake_mpv(props={"mute": True})
        status, body = self.request(
            "POST", "/control",
            body=json.dumps({"action": "toggle_mute", "socket": sock}),
        )
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body), {
            "status": "ok", "error": "success", "muted": True})
        self.assertEqual(mpv.received_commands, [
            ["cycle", "mute"],
            ["get_property", "mute"],
            ["show-text", "Mute: on"],
        ])

    def test_control_toggle_mute_false(self):
        """The reported state follows the fake's mute property and the
        literal OSD says off."""
        mpv, sock = self.fake_mpv(props={"mute": False})
        status, body = self.request(
            "POST", "/control",
            body=json.dumps({"action": "toggle_mute", "socket": sock}),
        )
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body), {
            "status": "ok", "error": "success", "muted": False})
        self.assertEqual(mpv.received_commands, [
            ["cycle", "mute"],
            ["get_property", "mute"],
            ["show-text", "Mute: off"],
        ])

    def test_control_toggle_mute_absent(self):
        """A missing mute property falls back to False without failing."""
        mpv, sock = self.fake_mpv(props={})
        status, body = self.request(
            "POST", "/control",
            body=json.dumps({"action": "toggle_mute", "socket": sock}),
        )
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body), {
            "status": "ok", "error": "success", "muted": False})
        self.assertEqual(mpv.received_commands, [
            ["cycle", "mute"],
            ["get_property", "mute"],
            ["show-text", "Mute: off"],
        ])

    def test_control_toggle_mute_show_text_failure_is_best_effort(self):
        """A failing mute OSD is logged but the request still succeeds."""
        mpv, sock = self.fake_mpv(props={"mute": True}, fail_on=3)
        status, body = self.request(
            "POST", "/control",
            body=json.dumps({"action": "toggle_mute", "socket": sock}),
        )
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body), {
            "status": "ok", "error": "success", "muted": True})
        self.assertEqual(mpv.received_commands, [
            ["cycle", "mute"],
            ["get_property", "mute"],
            ["show-text", "Mute: on"],
        ])

    def test_control_toggle_mute_default_socket(self):
        """toggle_mute works with the default socket."""
        status, body = self.request(
            "POST", "/control",
            body=json.dumps({"action": "toggle_mute"}),
        )
        self.assertEqual(status, 200)
        self.assertIs(json.loads(body)["muted"], False)

    def test_control_toggle_mute_cycle_error(self):
        """An MPV rejection of cycle mute surfaces as a 502 with the
        step."""
        _, sock = self.fake_mpv(fail_on=1)
        status, body = self.request(
            "POST", "/control",
            body=json.dumps({"action": "toggle_mute", "socket": sock}),
        )
        self.assertEqual(status, 502)
        self.assertIn("basculer la sourdine", body)

    def test_control_clear_is_stop_only(self):
        """clear only sends stop: no image and no loadfile."""
        mpv, sock = self.fake_mpv()
        status, _ = self.request(
            "POST", "/control",
            body=json.dumps({"action": "clear", "socket": sock}),
        )
        self.assertEqual(status, 200)
        self.assertEqual(mpv.received_commands, [["stop"]])

    def test_control_clear_ignores_legacy_fallback(self):
        """A legacy "fallback" field is ignored: clear stays stop."""
        mpv, sock = self.fake_mpv()
        status, _ = self.request(
            "POST", "/control",
            body=json.dumps({"action": "clear", "socket": sock,
                             "fallback": LEGACY_FALLBACK}),
        )
        self.assertEqual(status, 200)
        self.assertEqual(mpv.received_commands, [["stop"]])

    def test_control_ignores_legacy_fallback_for_other_actions(self):
        """The "fallback" field is ignored for every action."""
        mpv, sock = self.fake_mpv()
        status, _ = self.request(
            "POST", "/control",
            body=json.dumps({"action": "toggle_pause", "socket": sock,
                             "fallback": LEGACY_FALLBACK}),
        )
        self.assertEqual(status, 200)
        self.assertEqual(mpv.received_commands, [["cycle", "pause"]])

    def test_control_default_socket(self):
        status, _ = self.request(
            "POST", "/control",
            body=json.dumps({"action": "toggle_pause"}),
        )
        self.assertEqual(status, 200)

    def test_control_unknown_action(self):
        status, body = self.request(
            "POST", "/control",
            body=json.dumps({"action": "explose-tout",
                             "socket": "/tmp/x.sock"}),
        )
        self.assertEqual(status, 400)
        self.assertIn("action", body)

    def test_control_missing_action(self):
        status, _ = self.request(
            "POST", "/control",
            body=json.dumps({"socket": "/tmp/x.sock"}),
        )
        self.assertEqual(status, 400)

    def test_control_relative_socket(self):
        status, _ = self.request(
            "POST", "/control",
            body=json.dumps({"action": "next", "socket": "relatif.sock"}),
        )
        self.assertEqual(status, 400)

    def test_control_mpv_error_on_clear(self):
        """The clear step (stop) fails: 502 with the step."""
        _, sock = self.fake_mpv(fail_on=1)
        status, body = self.request(
            "POST", "/control",
            body=json.dumps({"action": "clear", "socket": sock}),
        )
        self.assertEqual(status, 502)
        self.assertIn("vider la file de lecture", body)

    def test_control_event_before_response(self):
        """An MPV event before the reply does not break /control."""
        mpv, sock = self.fake_mpv(events=1)
        status, _ = self.request(
            "POST", "/control",
            body=json.dumps({"action": "next", "socket": sock}),
        )
        self.assertEqual(status, 200)
        self.assertEqual(mpv.received_commands, [["playlist-next", "weak"]])

    def test_control_unreachable_socket(self):
        status, body = self.request(
            "POST", "/control",
            body=json.dumps({"action": "next",
                             "socket": "/tmp/prise-inexistante-xyz.sock"}),
        )
        self.assertEqual(status, 502)
        self.assertIn("injoignable", body)

    def test_control_wrong_route_get(self):
        status, _ = self.request("GET", "/control")
        self.assertEqual(status, 404)

    # --- stop and resume position --------------------------------------

    def test_control_stop_reads_pos_then_stops(self):
        """stop reads playlist-pos BEFORE sending stop keep-playlist (the
        property is transiently reset right after the stop)."""
        mpv, sock = self.fake_mpv(props={"playlist-pos": 1})
        status, body = self.request(
            "POST", "/control",
            body=json.dumps({"action": "stop", "socket": sock}),
        )
        self.assertEqual(status, 200)
        self.assertIn("success", body)
        self.assertEqual(mpv.received_commands, [
            ["get_property", "playlist-pos"],
            ["stop", "keep-playlist"],
        ])

    def test_control_stop_with_pos_minus_one_keeps_memory(self):
        """A stop while playlist-pos is -1 does not overwrite the
        remembered index."""
        mpv, sock = self.fake_mpv(props={
            "playlist-pos": 1, "idle-active": True, "playlist-count": 5})
        status, _ = self.request(
            "POST", "/control",
            body=json.dumps({"action": "stop", "socket": sock}),
        )
        self.assertEqual(status, 200)
        # MPV now reports -1: the memory (1) must be kept.
        mpv.props["playlist-pos"] = -1
        status, _ = self.request(
            "POST", "/control",
            body=json.dumps({"action": "stop", "socket": sock}),
        )
        self.assertEqual(status, 200)
        # Resuming next must use the kept memory (1 + 1 = 2).
        status, _ = self.request(
            "POST", "/control",
            body=json.dumps({"action": "next", "socket": sock}),
        )
        self.assertEqual(status, 200)
        self.assertEqual(mpv.received_commands[-1],
                         ["playlist-play-index", 2])

    def test_control_next_resumes_from_memory(self):
        """After a stop, next with idle-active true resumes at memory + 1
        and consumes the memory."""
        mpv, sock = self.fake_mpv(props={
            "playlist-pos": 1, "idle-active": True, "playlist-count": 4})
        self.request(
            "POST", "/control",
            body=json.dumps({"action": "stop", "socket": sock}),
        )
        status, _ = self.request(
            "POST", "/control",
            body=json.dumps({"action": "next", "socket": sock}),
        )
        self.assertEqual(status, 200)
        self.assertEqual(mpv.received_commands[-1],
                         ["playlist-play-index", 2])
        # The memory is consumed: a second next falls back to
        # playlist-next.
        status, _ = self.request(
            "POST", "/control",
            body=json.dumps({"action": "next", "socket": sock}),
        )
        self.assertEqual(status, 200)
        self.assertEqual(mpv.received_commands[-1],
                         ["playlist-next", "weak"])

    def test_control_previous_resumes_from_memory(self):
        """After a stop, previous with idle-active true resumes at
        memory - 1."""
        mpv, sock = self.fake_mpv(props={
            "playlist-pos": 2, "idle-active": True, "playlist-count": 5})
        self.request(
            "POST", "/control",
            body=json.dumps({"action": "stop", "socket": sock}),
        )
        status, _ = self.request(
            "POST", "/control",
            body=json.dumps({"action": "previous", "socket": sock}),
        )
        self.assertEqual(status, 200)
        self.assertEqual(mpv.received_commands[-1],
                         ["playlist-play-index", 1])

    def test_control_resume_out_of_bounds_is_noop(self):
        """A resume target outside [0, playlist-count) sends no
        playlist-play-index command, returns success, and keeps the
        memory."""
        mpv, sock = self.fake_mpv(props={
            "playlist-pos": 3, "idle-active": True, "playlist-count": 4})
        self.request(
            "POST", "/control",
            body=json.dumps({"action": "stop", "socket": sock}),
        )
        before = len(mpv.received_commands)
        status, body = self.request(
            "POST", "/control",
            body=json.dumps({"action": "next", "socket": sock}),
        )
        self.assertEqual(status, 200)
        self.assertIn("success", body)
        # Only the property reads happen: next would target 4, out of
        # range, so no playlist-play-index is sent.
        self.assertEqual(mpv.received_commands[before:], [
            ["get_property", "idle-active"],
            ["get_property", "playlist-count"],
        ])
        # The memory (3) is kept: previous targets 2.
        status, _ = self.request(
            "POST", "/control",
            body=json.dumps({"action": "previous", "socket": sock}),
        )
        self.assertEqual(status, 200)
        self.assertEqual(mpv.received_commands[-1],
                         ["playlist-play-index", 2])

    def test_control_next_with_memory_but_not_idle(self):
        """If playback is still active (not idle), next uses the weak
        command and forgets the memory."""
        mpv, sock = self.fake_mpv(props={
            "playlist-pos": 1, "idle-active": False, "playlist-count": 4})
        self.request(
            "POST", "/control",
            body=json.dumps({"action": "stop", "socket": sock}),
        )
        status, _ = self.request(
            "POST", "/control",
            body=json.dumps({"action": "next", "socket": sock}),
        )
        self.assertEqual(status, 200)
        self.assertEqual(mpv.received_commands[-1],
                         ["playlist-next", "weak"])
        # The memory was consumed: a second next behaves the same way.
        status, _ = self.request(
            "POST", "/control",
            body=json.dumps({"action": "next", "socket": sock}),
        )
        self.assertEqual(status, 200)
        self.assertEqual(mpv.received_commands[-1],
                         ["playlist-next", "weak"])

    def test_play_clears_resume_memory(self):
        """/play forgets any remembered resume position."""
        mpv, sock = self.fake_mpv(props={
            "playlist-pos": 1, "idle-active": True, "playlist-count": 4})
        self.request(
            "POST", "/control",
            body=json.dumps({"action": "stop", "socket": sock}),
        )
        status, _ = self.request(
            "POST", "/play",
            body=json.dumps({"url": "https://example.com/video.mp4",
                             "socket": sock}),
        )
        self.assertEqual(status, 200)
        status, _ = self.request(
            "POST", "/control",
            body=json.dumps({"action": "next", "socket": sock}),
        )
        self.assertEqual(status, 200)
        self.assertEqual(mpv.received_commands[-1],
                         ["playlist-next", "weak"])

    def test_control_clear_clears_resume_memory(self):
        """clear forgets any remembered resume position."""
        mpv, sock = self.fake_mpv(props={
            "playlist-pos": 1, "idle-active": True, "playlist-count": 4})
        self.request(
            "POST", "/control",
            body=json.dumps({"action": "stop", "socket": sock}),
        )
        status, _ = self.request(
            "POST", "/control",
            body=json.dumps({"action": "clear", "socket": sock}),
        )
        self.assertEqual(status, 200)
        status, _ = self.request(
            "POST", "/control",
            body=json.dumps({"action": "next", "socket": sock}),
        )
        self.assertEqual(status, 200)
        self.assertEqual(mpv.received_commands[-1],
                         ["playlist-next", "weak"])

    def test_control_play_index(self):
        """play_index sends playlist-play-index and forgets the resume
        memory."""
        mpv, sock = self.fake_mpv(props={
            "playlist-pos": 1, "idle-active": True, "playlist-count": 4})
        self.request(
            "POST", "/control",
            body=json.dumps({"action": "stop", "socket": sock}),
        )
        status, body = self.request(
            "POST", "/control",
            body=json.dumps({"action": "play_index", "index": 2,
                             "socket": sock}),
        )
        self.assertEqual(status, 200)
        self.assertIn("success", body)
        self.assertEqual(mpv.received_commands[-1],
                         ["playlist-play-index", 2])
        # The resume memory was cleared: next falls back to the weak
        # command.
        status, _ = self.request(
            "POST", "/control",
            body=json.dumps({"action": "next", "socket": sock}),
        )
        self.assertEqual(status, 200)
        self.assertEqual(mpv.received_commands[-1],
                         ["playlist-next", "weak"])

    def test_control_play_index_validation(self):
        """A missing, negative or non-integer index is rejected with 400."""
        bodies = [
            {"action": "play_index", "socket": "/tmp/x.sock"},
            {"action": "play_index", "socket": "/tmp/x.sock", "index": -1},
            {"action": "play_index", "socket": "/tmp/x.sock", "index": "2"},
            {"action": "play_index", "socket": "/tmp/x.sock", "index": 1.5},
            {"action": "play_index", "socket": "/tmp/x.sock", "index": True},
        ]
        for body in bodies:
            with self.subTest(body=body):
                status, _ = self.request(
                    "POST", "/control", body=json.dumps(body))
                self.assertEqual(status, 400)

    def test_control_remove_index(self):
        """remove_index sends playlist-remove and forgets the resume
        memory (the remaining indexes may shift)."""
        mpv, sock = self.fake_mpv(props={
            "playlist-pos": 1, "idle-active": True, "playlist-count": 4})
        self.request(
            "POST", "/control",
            body=json.dumps({"action": "stop", "socket": sock}),
        )
        status, body = self.request(
            "POST", "/control",
            body=json.dumps({"action": "remove_index", "index": 0,
                             "socket": sock}),
        )
        self.assertEqual(status, 200)
        self.assertIn("success", body)
        self.assertEqual(mpv.received_commands[-1],
                         ["playlist-remove", 0])
        # The resume memory was cleared: next falls back to the weak
        # command.
        status, _ = self.request(
            "POST", "/control",
            body=json.dumps({"action": "next", "socket": sock}),
        )
        self.assertEqual(status, 200)
        self.assertEqual(mpv.received_commands[-1],
                         ["playlist-next", "weak"])

    def test_control_remove_index_validation(self):
        """A missing, negative or non-integer index is rejected with 400."""
        bodies = [
            {"action": "remove_index", "socket": "/tmp/x.sock"},
            {"action": "remove_index", "socket": "/tmp/x.sock", "index": -1},
            {"action": "remove_index", "socket": "/tmp/x.sock", "index": "2"},
            {"action": "remove_index", "socket": "/tmp/x.sock", "index": 1.5},
            {"action": "remove_index", "socket": "/tmp/x.sock",
             "index": True},
        ]
        for body in bodies:
            with self.subTest(body=body):
                status, _ = self.request(
                    "POST", "/control", body=json.dumps(body))
                self.assertEqual(status, 400)

    def test_control_remove_index_mpv_rejection(self):
        """An out-of-range index is rejected by MPV and surfaces as a 502
        with the step."""
        _, sock = self.fake_mpv(fail_on=1)
        status, body = self.request(
            "POST", "/control",
            body=json.dumps({"action": "remove_index", "index": 9,
                             "socket": sock}),
        )
        self.assertEqual(status, 502)
        self.assertIn("retirer l'entrée", body)

    def test_control_seek_integer(self):
        """seek sends the absolute position with the osd-msg-bar prefix."""
        mpv, sock = self.fake_mpv()
        status, _ = self.request(
            "POST", "/control",
            body=json.dumps({"action": "seek", "position": 5,
                             "socket": sock}),
        )
        self.assertEqual(status, 200)
        self.assertEqual(mpv.received_commands, [
            ["osd-msg-bar", "seek", 5, "absolute"],
        ])

    def test_control_seek_float(self):
        """A float position is passed through as-is."""
        mpv, sock = self.fake_mpv()
        status, _ = self.request(
            "POST", "/control",
            body=json.dumps({"action": "seek", "position": 12.5,
                             "socket": sock}),
        )
        self.assertEqual(status, 200)
        self.assertEqual(mpv.received_commands, [
            ["osd-msg-bar", "seek", 12.5, "absolute"],
        ])

    def test_control_seek_validation(self):
        """A missing, negative, string or boolean position is rejected with
        400."""
        bodies = [
            {"action": "seek", "socket": "/tmp/x.sock"},
            {"action": "seek", "socket": "/tmp/x.sock", "position": -1},
            {"action": "seek", "socket": "/tmp/x.sock", "position": "5"},
            {"action": "seek", "socket": "/tmp/x.sock", "position": True},
        ]
        for body in bodies:
            with self.subTest(body=body):
                status, _ = self.request(
                    "POST", "/control", body=json.dumps(body))
                self.assertEqual(status, 400)

    def test_control_seek_default_socket(self):
        """seek works with the default socket."""
        status, _ = self.request(
            "POST", "/control",
            body=json.dumps({"action": "seek", "position": 5}),
        )
        self.assertEqual(status, 200)

    def test_control_seek_mpv_rejection(self):
        """An MPV rejection of seek surfaces as a 502 with the step."""
        _, sock = self.fake_mpv(fail_on=1)
        status, body = self.request(
            "POST", "/control",
            body=json.dumps({"action": "seek", "position": 5,
                             "socket": sock}),
        )
        self.assertEqual(status, 502)
        self.assertIn("déplacer la lecture", body)

    def test_control_seek_keeps_resume_memory(self):
        """seek does not modify the resume position."""
        mpv, sock = self.fake_mpv(props={
            "playlist-pos": 1, "idle-active": True, "playlist-count": 4})
        self.request(
            "POST", "/control",
            body=json.dumps({"action": "stop", "socket": sock}),
        )
        status, _ = self.request(
            "POST", "/control",
            body=json.dumps({"action": "seek", "position": 10,
                             "socket": sock}),
        )
        self.assertEqual(status, 200)
        # The remembered index (1) is kept: next resumes at 2.
        status, _ = self.request(
            "POST", "/control",
            body=json.dumps({"action": "next", "socket": sock}),
        )
        self.assertEqual(status, 200)
        self.assertEqual(mpv.received_commands[-1],
                         ["playlist-play-index", 2])

    # --- playlist (POST /playlist) -------------------------------------

    def test_playlist_response_format(self):
        """The playlist is returned with titles, flags and the position,
        using a single connection and the expected command order."""
        mpv, sock = self.fake_mpv(props={
            "playlist": [
                {"filename": "a.mp4", "current": True, "playing": True},
                {"filename": "b.mp4", "current": False},
            ],
            "playlist/0/title": "Alpha",
            "playlist/1/title": "Beta",
            "playlist-pos": 0,
            "idle-active": False,
        })
        status, body = self.request(
            "POST", "/playlist", body=json.dumps({"socket": sock}))
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body), {
            "status": "ok",
            "playlist_pos": 0,
            "idle_active": False,
            "items": [
                {"index": 0, "title": "Alpha", "filename": "a.mp4",
                 "current": True, "playing": True},
                {"index": 1, "title": "Beta", "filename": "b.mp4",
                 "current": False, "playing": False},
            ],
        })
        self.assertEqual(mpv.received_commands, [
            ["get_property", "playlist"],
            ["get_property", "playlist/0/title"],
            ["get_property", "playlist/1/title"],
            ["get_property", "playlist-pos"],
            ["get_property", "idle-active"],
        ])
        self.assertEqual(mpv.connection_count, 1)

    def test_playlist_title_unavailable_falls_back_to_empty(self):
        """A "property unavailable" title does not fail the request; the
        title falls back to an empty string."""
        mpv, sock = self.fake_mpv(
            props={
                "playlist": [{"filename": "a.mp4"},
                             {"filename": "b.mp4"}],
                "playlist/0/title": "Alpha",
                "playlist-pos": 0,
                "idle-active": False,
            },
            unavailable={"playlist/1/title"},
        )
        status, body = self.request(
            "POST", "/playlist", body=json.dumps({"socket": sock}))
        self.assertEqual(status, 200)
        data = json.loads(body)
        self.assertEqual([item["title"] for item in data["items"]],
                         ["Alpha", ""])
        self.assertEqual(mpv.connection_count, 1)

    def test_playlist_empty(self):
        """An empty playlist returns no items and no title queries."""
        mpv, sock = self.fake_mpv(props={
            "playlist": [], "playlist-pos": -1, "idle-active": True})
        status, body = self.request(
            "POST", "/playlist", body=json.dumps({"socket": sock}))
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body), {
            "status": "ok",
            "playlist_pos": -1,
            "idle_active": True,
            "items": [],
        })
        self.assertEqual(mpv.received_commands, [
            ["get_property", "playlist"],
            ["get_property", "playlist-pos"],
            ["get_property", "idle-active"],
        ])

    def test_playlist_default_socket(self):
        status, body = self.request(
            "POST", "/playlist", body=json.dumps({}))
        self.assertEqual(status, 200)
        self.assertIn("items", body)

    def test_playlist_relative_socket(self):
        status, _ = self.request(
            "POST", "/playlist", body=json.dumps({"socket": "relatif.sock"}))
        self.assertEqual(status, 400)

    def test_playlist_invalid_json(self):
        status, _ = self.request("POST", "/playlist", body="pas du json")
        self.assertEqual(status, 400)

    def test_playlist_wrong_method(self):
        status, _ = self.request(
            "PUT", "/playlist", body=json.dumps({}))
        self.assertEqual(status, 405)

    def test_playlist_unreachable_socket(self):
        status, body = self.request(
            "POST", "/playlist",
            body=json.dumps({"socket": "/tmp/prise-inexistante-xyz.sock"}))
        self.assertEqual(status, 502)
        self.assertIn("injoignable", body)

    def test_playlist_timeout(self):
        _, sock = self.fake_mpv(no_reply=True)
        status, body = self.request(
            "POST", "/playlist", body=json.dumps({"socket": sock}))
        self.assertEqual(status, 502)
        self.assertIn("timeout", body)

    def test_playlist_command_error(self):
        _, sock = self.fake_mpv(fail_on=1)
        status, body = self.request(
            "POST", "/playlist", body=json.dumps({"socket": sock}))
        self.assertEqual(status, 502)
        self.assertIn("lire la liste de lecture", body)

    def test_playlist_invalid_response(self):
        _, sock = self.fake_mpv(props={"playlist": "pas une liste"})
        status, body = self.request(
            "POST", "/playlist", body=json.dumps({"socket": sock}))
        self.assertEqual(status, 502)
        self.assertIn("réponse invalide", body)

    # --- state (POST /state) -------------------------------------------

    def test_state_response_format(self):
        """The state is returned with the exact contract, using a single
        connection and the expected command order."""
        mpv, sock = self.fake_mpv(props={
            "time-pos": 12.5, "duration": 120.0, "pause": True,
            "idle-active": False, "playlist-pos": 2})
        status, body = self.request(
            "POST", "/state", body=json.dumps({"socket": sock}))
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body), {
            "status": "ok",
            "idle_active": False,
            "playlist_pos": 2,
            "pause": True,
            "time_pos": 12.5,
            "duration": 120.0,
        })
        self.assertEqual(mpv.received_commands, [
            ["get_property", "time-pos"],
            ["get_property", "duration"],
            ["get_property", "pause"],
            ["get_property", "idle-active"],
            ["get_property", "playlist-pos"],
        ])
        self.assertEqual(mpv.connection_count, 1)

    def test_state_time_pos_and_duration_unavailable(self):
        """"property unavailable" on time-pos/duration yields null without
        failing the request."""
        mpv, sock = self.fake_mpv(
            props={"pause": True, "idle-active": False, "playlist-pos": 1},
            unavailable={"time-pos", "duration"},
        )
        status, body = self.request(
            "POST", "/state", body=json.dumps({"socket": sock}))
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body), {
            "status": "ok",
            "idle_active": False,
            "playlist_pos": 1,
            "pause": True,
            "time_pos": None,
            "duration": None,
        })

    def test_state_defaults_when_absent(self):
        """Missing properties fall back to null/false/-1."""
        mpv, sock = self.fake_mpv(props={})
        status, body = self.request(
            "POST", "/state", body=json.dumps({"socket": sock}))
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body), {
            "status": "ok",
            "idle_active": False,
            "playlist_pos": -1,
            "pause": False,
            "time_pos": None,
            "duration": None,
        })

    def test_state_default_socket(self):
        status, body = self.request(
            "POST", "/state", body=json.dumps({}))
        self.assertEqual(status, 200)
        data = json.loads(body)
        self.assertIs(data["idle_active"], True)
        self.assertEqual(data["playlist_pos"], -1)
        self.assertIs(data["pause"], False)
        self.assertIsNone(data["time_pos"])
        self.assertIsNone(data["duration"])

    def test_state_relative_socket(self):
        status, _ = self.request(
            "POST", "/state", body=json.dumps({"socket": "relatif.sock"}))
        self.assertEqual(status, 400)

    def test_state_invalid_json(self):
        status, _ = self.request("POST", "/state", body="pas du json")
        self.assertEqual(status, 400)

    def test_state_wrong_method(self):
        status, _ = self.request(
            "PUT", "/state", body=json.dumps({}))
        self.assertEqual(status, 405)

    def test_state_unreachable_socket(self):
        status, body = self.request(
            "POST", "/state",
            body=json.dumps({"socket": "/tmp/prise-inexistante-xyz.sock"}))
        self.assertEqual(status, 502)
        self.assertIn("injoignable", body)

    def test_state_property_error_is_502(self):
        """A non-tolerated property error surfaces as a 502 with the
        step."""
        _, sock = self.fake_mpv(props={}, fail_on=2)
        status, body = self.request(
            "POST", "/state", body=json.dumps({"socket": sock}))
        self.assertEqual(status, 502)
        self.assertIn("lire duration", body)

    # --- validations ---------------------------------------------------

    def test_invalid_url(self):
        status, body = self.request(
            "POST", "/play",
            body=json.dumps({"url": "ftp://example.com/x.mp4"}),
        )
        self.assertEqual(status, 400)
        self.assertIn("url", body)

    def test_relative_socket(self):
        status, _ = self.request(
            "POST", "/play",
            body=json.dumps({"url": "https://example.com/x.mp4",
                             "socket": "relatif.sock"}),
        )
        self.assertEqual(status, 400)

    def test_append_invalid(self):
        status, _ = self.request(
            "POST", "/play",
            body=json.dumps({"url": "https://example.com/x.mp4",
                             "append": "oui"}),
        )
        self.assertEqual(status, 400)

    def test_append_invalid_number(self):
        status, _ = self.request(
            "POST", "/play",
            body=json.dumps({"url": "https://example.com/x.mp4",
                             "append": 1}),
        )
        self.assertEqual(status, 400)

    def test_missing_body(self):
        status, _ = self.request("POST", "/play")
        self.assertEqual(status, 400)

    def test_invalid_json(self):
        status, _ = self.request("POST", "/play", body="pas du json")
        self.assertEqual(status, 400)

    def test_oversized_body(self):
        status, _ = self.request(
            "POST", "/play",
            body='{"url":"https://example.com/' + "a" * 800 + '"}',
        )
        self.assertEqual(status, 413)

    def test_wrong_route(self):
        status, _ = self.request("GET", "/bogus")
        self.assertEqual(status, 404)

    def test_wrong_method(self):
        status, _ = self.request(
            "PUT", "/play",
            body=json.dumps({"url": "https://example.com/x.mp4"}),
        )
        self.assertEqual(status, 405)

    def test_unreachable_socket(self):
        status, body = self.request(
            "POST", "/play",
            body=json.dumps({"url": "https://example.com/x.mp4",
                             "socket": "/tmp/prise-inexistante-xyz.sock"}),
        )
        self.assertEqual(status, 502)
        self.assertIn("injoignable", body)

    def test_health(self):
        status, body = self.request("GET", "/health")
        self.assertEqual(status, 200)
        self.assertIn("ok", body)


if __name__ == "__main__":
    unittest.main(verbosity=2)