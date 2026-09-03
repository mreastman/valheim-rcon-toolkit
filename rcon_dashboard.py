#!/usr/bin/env python3
"""Live RCON dashboard for the Valheim dedicated server.

Top: scrolling feed of chat/join/leave/death events, polled from the
EventFeed mod's "events_since <id>" console command (RCON has no push
mechanism -- see mods-dev/EventFeed -- so this polls once a second, which
reads as live in practice).

Bottom: a text input. Anything typed is sent as a raw console command over
the same RCON connection -- "broadcast center hello", "kick PlayerName",
"ban PlayerName", or any other command Server devcommands/vanilla exposes.

Protocol note: this is NOT standard Source RCON, despite being modeled on
it -- see rcon_terminal.py's docstring for the reverse-engineered wire
format (AviiNL's rcon.dll has a byte-truncation bug in its own response
writer, so responses are parsed leniently, header-stripped, not strictly).
"""
import json
import re
import socket
import struct
import sys
import threading
import time

from textual.app import App, ComposeResult
from textual.containers import Vertical
from textual.widgets import RichLog, Input, Header, Footer

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 2458

TYPE_STYLES = {
    "join": "bold green",
    "leave": "bold yellow",
    "death": "bold red",
}


def build_packet(request_id: int, packet_type: int, payload: str) -> bytes:
    payload_bytes = payload.encode("ascii", errors="replace")
    size = 10 + len(payload_bytes)
    return (
        struct.pack("<i", size)
        + struct.pack("<i", request_id)
        + struct.pack("<i", packet_type)
        + payload_bytes
    )


_LINE_RE = re.compile(r"^\d{2}/\d{2}/\d{4} \d{2}:\d{2}:\d{2}: (?:Console: )?(.*)$")


def dedupe_console_output(raw: str) -> str:
    """RconCommands' unknown-command bridge Harmony-patches both
    Terminal.AddString and ZLog.Log to capture a command's output -- but
    Terminal.AddString logs through ZLog.Log internally, so every line comes
    back twice: once bare, once prefixed "Console: ". Collapse consecutive
    lines that are the same text once that prefix is stripped."""
    lines = raw.splitlines()
    out = []
    i = 0
    while i < len(lines):
        if i + 1 < len(lines):
            a = _LINE_RE.match(lines[i])
            b = _LINE_RE.match(lines[i + 1])
            a_text = a.group(1) if a else lines[i]
            b_text = b.group(1) if b else lines[i + 1]
            if a_text == b_text:
                out.append(lines[i + 1])  # keep the copy without "Console: "
                i += 2
                continue
        out.append(lines[i])
        i += 1
    return "\n".join(out)


class RconConnection:
    def __init__(self, host: str, port: int, password: str):
        self.sock = socket.create_connection((host, port), timeout=10)
        self.req_id = 1
        self._lock = threading.Lock()
        result = self._send(3, password)  # type 3 = Login
        if "Success" not in result:
            raise RuntimeError(f"Login failed: {result!r}")

    def _send(self, packet_type: int, payload: str) -> str:
        with self._lock:
            self.req_id += 1
            self.sock.sendall(build_packet(self.req_id, packet_type, payload))
            data = self.sock.recv(262144)
        return data[12:].rstrip(b"\x00").decode("ascii", errors="replace")

    def command(self, text: str) -> str:
        return dedupe_console_output(self._send(2, text))  # type 2 = Command

    def fetch_known_commands(self) -> set:
        """Console command names, scraped from `help`'s own output rather than
        hardcoded, so this stays accurate as Server devcommands updates."""
        raw = self.command("help")
        names = set()
        for line in raw.splitlines():
            m = re.match(r"^\d{2}/\d{2}/\d{4} \d{2}:\d{2}:\d{2}: (?:Console: )?(\S+) - ", line.strip())
            if m:
                names.add(m.group(1).lower())
        return names

    def close(self):
        try:
            self.sock.close()
        except OSError:
            pass


class RconDashboard(App):
    CSS = """
    Screen { layout: vertical; }
    RichLog { height: 1fr; border: solid $accent; }
    Input { dock: bottom; }
    """
    BINDINGS = [("ctrl+c", "quit", "Quit")]

    def __init__(self, conn: RconConnection, known_commands: set):
        super().__init__()
        self.conn = conn
        self.known_commands = known_commands
        self.last_event_id = 0
        self._stop = threading.Event()

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        yield RichLog(id="feed", wrap=True, markup=True, highlight=False)
        yield Input(placeholder="Type a command (broadcast/kick/ban/...) and press Enter", id="cmd")
        yield Footer()

    def on_mount(self) -> None:
        feed = self.query_one("#feed", RichLog)
        feed.write("[bold cyan]Connected. Watching for events...[/bold cyan]")
        feed.write(f"[dim]{len(self.known_commands)} known commands loaded. Type a command name to run it, or anything else to broadcast it.[/dim]")
        self.query_one(Input).focus()
        threading.Thread(target=self._poll_loop, daemon=True).start()

    def _poll_loop(self) -> None:
        while not self._stop.is_set():
            try:
                raw = self.conn.command(f"events_since {self.last_event_id}")
            except OSError as e:
                self.call_from_thread(self._log, f"[bold red]Connection error: {e}[/bold red]")
                time.sleep(3)
                continue

            for line in raw.splitlines():
                line = line.strip()
                if not line:
                    continue
                # Console output lines look like "MM/DD/YYYY HH:MM:SS: {json}"
                # -- the JSON payload is whatever comes after the last ": ".
                idx = line.find('{"id"')
                if idx == -1:
                    continue
                try:
                    event = json.loads(line[idx:])
                except json.JSONDecodeError:
                    continue
                self.last_event_id = max(self.last_event_id, event.get("id", 0))
                self.call_from_thread(self._render_event, event)

            time.sleep(1)

    def _render_event(self, event: dict) -> None:
        etype = event.get("type", "?")
        player = event.get("player", "?")
        text = event.get("text", "")
        ts = time.strftime("%H:%M:%S", time.localtime(event.get("time", time.time())))
        style = TYPE_STYLES.get(etype.split(":")[0], "white")
        if etype.startswith("chat:"):
            self._log(f"[dim]{ts}[/dim] [{style}]{etype.split(':', 1)[1]}[/{style}] [bold]{player}[/bold]: {text}")
        elif etype == "join":
            self._log(f"[dim]{ts}[/dim] [{style}]>> {player} joined[/{style}]")
        elif etype == "leave":
            self._log(f"[dim]{ts}[/dim] [{style}]<< {player} left[/{style}]")
        elif etype == "death":
            self._log(f"[dim]{ts}[/dim] [{style}]{player} died[/{style}]")
        else:
            self._log(f"[dim]{ts}[/dim] {etype} {player} {text}")

    def _log(self, text: str) -> None:
        self.query_one("#feed", RichLog).write(text)

    def on_input_submitted(self, message: Input.Submitted) -> None:
        text = message.value.strip()
        message.input.value = ""
        if not text:
            return

        first_word = text.split(" ", 1)[0].lower()
        if first_word in self.known_commands:
            command = text
            self._log(f"[bold blue]> {command}[/bold blue]")
        else:
            command = f"broadcast center {text}"
            self._log(f"[bold blue]> (broadcast) {text}[/bold blue]")

        threading.Thread(target=self._run_command, args=(command,), daemon=True).start()

    def _run_command(self, text: str) -> None:
        try:
            response = self.conn.command(text)
        except OSError as e:
            self.call_from_thread(self._log, f"[bold red]Error: {e}[/bold red]")
            return
        if response:
            self.call_from_thread(self._log, response)

    def on_unmount(self) -> None:
        self._stop.set()
        self.conn.close()


def main():
    if len(sys.argv) > 1:
        host = sys.argv[1]
        port = int(sys.argv[2]) if len(sys.argv) > 2 else DEFAULT_PORT
    else:
        host_input = input(f"Server IP [{DEFAULT_HOST}]: ").strip()
        host = host_input or DEFAULT_HOST
        port_input = input(f"RCON port [{DEFAULT_PORT}]: ").strip()
        port = int(port_input) if port_input else DEFAULT_PORT

    password = sys.argv[3] if len(sys.argv) > 3 else input("RCON password: ").strip()

    print(f"Connecting to {host}:{port}...")
    try:
        conn = RconConnection(host, port, password)
        known_commands = conn.fetch_known_commands()
    except (OSError, RuntimeError) as e:
        print(f"Could not connect: {e}")
        sys.exit(1)

    RconDashboard(conn, known_commands).run()


if __name__ == "__main__":
    main()
