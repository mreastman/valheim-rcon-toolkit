#!/usr/bin/env python3
"""Interactive RCON terminal for the Valheim dedicated server.

Connects and logs in once, then reads commands from stdin in a loop and
prints whatever the server's console sends back. Type "quit" or Ctrl+D/C
to exit.

Protocol note: this is NOT standard Source RCON, despite being modeled on
it -- reverse-engineered from AviiNL's rcon.dll source. The reader expects
a real little-endian int32 at each of the first three 4-byte fields (size,
requestId, type), where `size` must equal 10 + len(payload). The plugin's
own *response* packets have a byte-truncation bug (only byte 0 of each
4-byte field gets written), so responses are read leniently -- we just
strip the 12-byte header and decode the rest as text.
"""
import socket
import struct
import sys

HOST = "127.0.0.1"
PORT = 2458


def build_packet(request_id: int, packet_type: int, payload: str) -> bytes:
    payload_bytes = payload.encode("ascii", errors="replace")
    size = 10 + len(payload_bytes)
    return (
        struct.pack("<i", size)
        + struct.pack("<i", request_id)
        + struct.pack("<i", packet_type)
        + payload_bytes
    )


def send_and_read(sock: socket.socket, request_id: int, packet_type: int, payload: str) -> str:
    sock.sendall(build_packet(request_id, packet_type, payload))
    data = sock.recv(65536)
    return data[12:].rstrip(b"\x00").decode("ascii", errors="replace")


def main():
    # Command-line args still work (host [port]) for scripting/reuse; if not
    # given, prompt interactively so this works from any machine, not just
    # the server box itself.
    if len(sys.argv) > 1:
        host = sys.argv[1]
        port = int(sys.argv[2]) if len(sys.argv) > 2 else PORT
    else:
        host_input = input(f"Server IP [{HOST}]: ").strip()
        host = host_input if host_input else HOST
        port_input = input(f"RCON port [{PORT}]: ").strip()
        port = int(port_input) if port_input else PORT

    password = sys.argv[3] if len(sys.argv) > 3 else input("RCON password: ").strip()

    print(f"Connecting to {host}:{port}...")
    try:
        with socket.create_connection((host, port), timeout=5) as sock:
            _run_session(sock, host, port, password)
    except (ConnectionRefusedError, socket.timeout, OSError) as e:
        print(f"Could not connect to {host}:{port} -- {e}")
        print("If this isn't the server machine, make sure the RCON port is forwarded")
        print("through the server's router/firewall, and double check the IP.")


def _run_session(sock: socket.socket, host: str, port: int, password: str):
    login_result = send_and_read(sock, 1, 3, password)  # type 3 = Login
    if "Success" not in login_result:
        print(f"Login failed: {login_result!r}")
        return
    print(f"Connected to {host}:{port} -- logged in.")
    print("Type a console command and press Enter. 'quit' or Ctrl+D to exit.")

    req_id = 2
    while True:
        try:
            line = input("rcon> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not line:
            continue
        if line.lower() in ("quit", "exit"):
            break
        response = send_and_read(sock, req_id, 2, line)  # type 2 = Command
        req_id += 1
        if response:
            print(response)


if __name__ == "__main__":
    main()
