# Valheim RCON Toolkit

Remote admin tooling for a Valheim dedicated server: a live terminal
dashboard showing chat/join/leave/death events as they happen, with a
command input for `broadcast`/`kick`/`ban`/anything else your server
supports — connectable from any machine, not just the server box.

Two pieces:

- **EventFeed** — a small BepInEx server-side mod that watches chat
  (shouts/pings), joins, leaves, and deaths, and exposes them through a new
  `events_since <id>` console command.
- **rcon_dashboard.py** / **rcon_terminal.py** — Python clients that poll
  that command over RCON and render it live.

## Just connecting to an existing server?

If someone else already installed and configured this (the sections below
are for them), you don't need to touch any of that. You need:

- Python 3 (already on macOS/Linux; on Windows, install from
  [python.org](https://www.python.org/downloads/) — check "Add python.exe
  to PATH" during install)
- Just three files — `rcon_dashboard.py`, plus a launcher for your OS:
  `launch_dashboard.command` (macOS) or `launch_dashboard.bat` (Windows).
  Either download those directly from this repo, or clone the whole thing;
  nothing else here (EventFeed, `Environment.props`, a C# toolchain) is
  needed on your machine.
- From whoever runs the server: its IP address, the RCON port, and the
  password

**Easiest way in:** double-click `launch_dashboard.command` (macOS) or
`launch_dashboard.bat` (Windows). It checks for the `textual` package,
installs it automatically if missing, and launches the dashboard — no
terminal typing required beyond that.

Prefer the terminal yourself? `python3 rcon_dashboard.py` does the same
thing (you'll need `pip install textual` first), or use
`rcon_terminal.py` for a plain command-response session with no extra
dependency at all.

Either way, it'll prompt for the IP, port, and password. That's the entire
setup — no server access, no build tools, no account on the server machine.

If you downloaded `launch_dashboard.command` individually through a browser
rather than cloning the repo, macOS may not preserve its executable bit and
Gatekeeper may block it as from an "unidentified developer" on first run.
If double-clicking does nothing or shows that warning: right-click it →
Open (bypasses Gatekeeper once), and if it still won't run, open Terminal
and run `chmod +x launch_dashboard.command` in the folder you downloaded it
to, then try again.

## Why polling, not push

Valheim's dedicated server has no remote console of its own, and the RCON
implementation this relies on ([AviiNL/rcon](https://github.com/AviiNL/BepInEx.rcon))
is strictly request/response — there's no mechanism for the server to push
anything to a connected client unprompted. So "live" here means the
dashboard polls `events_since` once a second. In practice it reads as live.

## Setting up the server (admin)

Everything below is for whoever runs the server. This works on **any
platform** BepInEx supports — Windows, Linux, or macOS with a normal,
unpatched BepInEx install. (If this repo sits next to a bunch of Apple
Silicon-specific BepInEx/MonoMod fixes, those are a separate, unrelated
problem — only relevant if you're in that exact situation. EventFeed itself
has no platform-specific code.)

### Prerequisites

On the **server**, install these BepInEx plugins (all server-side only, no
client-side install needed for any of them):

1. [BepInEx 5.4.x](https://github.com/BepInEx/BepInEx) itself
2. [rcon](https://valheim.thunderstore.io/package/AviiNL/rcon/) — the RCON
   transport
3. [Rcon Commands](https://valheim.thunderstore.io/package/JereKuusela/Rcon_Commands/) —
   bridges RCON commands into the server's real console
4. [Server devcommands](https://valheim.thunderstore.io/package/JereKuusela/Server_devcommands/) —
   only needed if you want `broadcast`/`kick`/`ban`; `Rcon Commands` alone
   just gives you vanilla console commands
5. **EventFeed** (this repo) — only needed for the live event feed; the
   dashboard's command input still works without it

To **build** EventFeed, you'll also need the [.NET SDK](https://dotnet.microsoft.com/download)
(any recent version — it targets .NET Framework 4.7.2, which the SDK can
cross-compile for on any OS).

There's deliberately no prebuilt DLL to just download: EventFeed has to be
built against *your* server's exact game assemblies to avoid the kind of
version-mismatch breakage that a one-size-fits-all binary risks. Building
locally is the safer default, even though it raises the bar to entry.

### Installing EventFeed

```sh
cd EventFeed
cp ../Environment.props.example ../Environment.props
# edit Environment.props: point VALHEIM_DEDI_INSTALL at your dedicated
# server's install directory (the one containing Data/ and BepInEx/)
dotnet build EventFeed.csproj -c Release
cp bin/Release/net472/EventFeed.dll "<server>/BepInEx/plugins/"
```

Restart the server. `BepInEx/LogOutput.log` should show:

```
[Info   :Event Feed] Patched 3 methods.
[Info   :Event Feed]   patched: Chat.OnNewChatMessage
[Info   :Event Feed]   patched: ZNet.RPC_CharacterID
[Info   :Event Feed]   patched: ZNet.RPC_Disconnect
```

If it says `Patched 0 methods`, something's wrong with the build against
your server's exact game version — open an issue with your BepInEx log.

### Configuring RCON

After `rcon`'s first run, edit `BepInEx/config/nl.avii.plugins.rcon.cfg`:

```ini
[rcon]
enabled = true
port = 2458
password = <pick something>
```

Restart the server again. If you want to connect from outside your local
network, forward this port through your router/firewall same as the game
ports — RCON traffic here is **unencrypted**, so don't reuse a real password
and don't expose it beyond a trusted network unless you also put it behind
something like a VPN.

## Using it

**One-shot terminal:**

```sh
python3 rcon_terminal.py            # prompts for IP/port
python3 rcon_terminal.py 1.2.3.4 2458
```

Drops you into a `rcon>` prompt — type any console command, get the
response back, `quit` to exit.

**Live dashboard:**

```sh
python3 rcon_dashboard.py           # prompts for IP/port/password
```

Scrolling event feed on top, command input pinned at the bottom. Anything
you type that matches a known console command (fetched live from the
server's own `help` output) runs as that command; anything else is
auto-wrapped as `broadcast center <your text>`.

## Known limitations

- **Only Shouts and Pings reach the server** — that's how Valheim's
  networking model works, not something this toolkit controls. Regular
  nearby chat and whispers never leave the sending client, so they won't
  show up in the feed.
- **Death detection is a heuristic, not a direct hook.** Valheim doesn't
  expose a clean `OnDeath` event on the dedicated server; this reuses the
  same approach as [DiscordConnector](https://github.com/nwesterhausen/valheim-discordconnector):
  a peer re-registering a character (`ZNet.RPC_CharacterID`) while already
  marked as joined is treated as a death/respawn rather than a new join.
- **The RCON protocol here is not standard Source RCON**, despite being
  modeled on it — see the docstrings in `rcon_terminal.py`/`rcon_dashboard.py`
  for the actual (reverse-engineered) wire format. Generic RCON clients are
  unlikely to work; use the scripts in this repo.
