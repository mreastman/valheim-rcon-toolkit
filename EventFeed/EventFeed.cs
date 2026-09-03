using System;
using System.Collections.Generic;
using System.Linq;
using System.Text;
using BepInEx;
using HarmonyLib;
using UnityEngine;

namespace EventFeed
{
    // Records chat (Shout/Ping only -- that's all that reaches the dedicated
    // server, per Valheim's own networking model), joins, leaves, and deaths
    // into a rolling in-memory buffer, and exposes an "events_since <id>"
    // console command to read it. Death detection reuses the same heuristic
    // DiscordConnector uses: ZNet.RPC_CharacterID also fires when a dead
    // player respawns, so a peer re-registering a character while already
    // marked joined (with a real m_characterID) is a death, not a new join --
    // there's no clean standalone OnDeath hook in Valheim's dedicated server
    // code path.
    [BepInPlugin(Guid, Name, Version)]
    public class EventFeedPlugin : BaseUnityPlugin
    {
        public const string Guid = "mods.eventfeed";
        public const string Name = "Event Feed";
        public const string Version = "1.0.0";

        // Hand-picked from the ~160 vanilla console commands -- the rest are
        // emotes, client-only settings, or dev/world-editing commands that
        // don't matter for remote RCON admin use. Text copied verbatim from
        // `help` so it matches the real command syntax.
        private static readonly string[] AdminCheatSheet =
        {
            "-- messaging --",
            "broadcast [center/side] [message] - Broadcasts a message.",
            "message [player] [center/side] [message] - Sends a message to a player.",
            "-- moderation --",
            "kick [name/ip/userID] - kick user",
            "ban [name/ip/userID] - ban user",
            "unban [ip/userID] - unban user",
            "banned - list banned users",
            "permissions [operation] - Manage player permission overrides.",
            "-- players --",
            "playerlist - Prints online players.",
            "pos [name/precision] [precision] - Prints the position of a player. If name is not given, prints the current position.",
            "tp [player1,player2,...] [x,z,y,rot/player] [fast=false] - Teleports the player to coordinates or another player.",
            "recall [*name] - Recalls players to you, optionally matching given name.",
            "-- server --",
            "save - Force saves the world and resets the world save interval.",
            "shutdown - Closes the game.",
            "events_since [id] - Prints buffered chat/join/leave/death events with id > [id] (default 0).",
            "-- full list: help --",
        };

        private void Awake()
        {
            var harmony = new Harmony(Guid);
            try
            {
                harmony.PatchAll();
                Logger.LogInfo($"Patched {harmony.GetPatchedMethods().Count()} methods.");
                foreach (var m in harmony.GetPatchedMethods())
                {
                    Logger.LogInfo($"  patched: {m.DeclaringType?.Name}.{m.Name}");
                }
            }
            catch (Exception ex)
            {
                Logger.LogError($"PatchAll threw: {ex}");
            }
            new Terminal.ConsoleCommand("events_since", "[id] - Prints buffered chat/join/leave/death events with id > [id] (default 0), one JSON object per line.",
                args =>
                {
                    long since = 0;
                    if (args.Length > 1)
                    {
                        long.TryParse(args[1], out since);
                    }
                    foreach (var line in EventBuffer.Since(since))
                    {
                        args.Context.AddString(line);
                    }
                });

            // Vanilla `help` dumps all 160+ registered commands, ~24 of them
            // emotes and most of the rest client-only/dev commands with no
            // use for a remote admin. This is a hand-picked subset instead.
            // (RconCommands' bridge doubles every line of console output --
            // that's a client-side display issue, deduped in
            // rcon_dashboard.py/rcon_terminal.py, not something this command
            // needs to work around.)
            new Terminal.ConsoleCommand("cmds", "Admin command cheat-sheet (short list; use 'help' for the full vanilla command list).",
                args =>
                {
                    foreach (var line in AdminCheatSheet)
                    {
                        args.Context.AddString(line);
                    }
                });

            Logger.LogInfo("Event Feed loaded.");
        }
    }

    internal static class EventBuffer
    {
        private static readonly List<(long Id, string Json)> Events = new List<(long, string)>();
        private static long nextId = 1;
        private const int MaxBuffered = 2000;

        public static void Add(string type, string player, string text)
        {
            var id = nextId++;
            var time = DateTimeOffset.UtcNow.ToUnixTimeSeconds();
            var json = new StringBuilder();
            json.Append('{');
            json.Append("\"id\":").Append(id).Append(',');
            json.Append("\"time\":").Append(time).Append(',');
            json.Append("\"type\":\"").Append(Escape(type)).Append("\",");
            json.Append("\"player\":\"").Append(Escape(player)).Append("\",");
            json.Append("\"text\":\"").Append(Escape(text)).Append('"');
            json.Append('}');

            lock (Events)
            {
                Events.Add((id, json.ToString()));
                if (Events.Count > MaxBuffered)
                {
                    Events.RemoveRange(0, Events.Count - MaxBuffered);
                }
            }
        }

        public static List<string> Since(long id)
        {
            lock (Events)
            {
                var result = new List<string>();
                foreach (var e in Events)
                {
                    if (e.Id > id)
                    {
                        result.Add(e.Json);
                    }
                }
                return result;
            }
        }

        private static string Escape(string s)
        {
            if (string.IsNullOrEmpty(s)) return "";
            return s.Replace("\\", "\\\\").Replace("\"", "\\\"").Replace("\n", "\\n").Replace("\r", "");
        }
    }

    // Class-level [HarmonyPatch(type, method)] is what PatchAll() actually
    // scans for -- it was previously (wrongly) placed on the Prefix method
    // itself, which meant PatchAll() found nothing to patch at all and
    // silently did nothing (no exception, just "Patched 0 methods").
    [HarmonyPatch(typeof(Chat), nameof(Chat.OnNewChatMessage))]
    internal static class ChatPatch
    {
        private static void Prefix(ref GameObject go, ref long senderID, ref Vector3 pos, ref Talker.Type type, ref UserInfo sender, ref string text)
        {
            var name = string.IsNullOrEmpty(sender.Name) ? "?" : sender.Name;
            EventBuffer.Add("chat:" + type, name, text);
        }
    }

    // ZNet has two methods patched here, so the class only pins the type and
    // each method carries its own [HarmonyPatch(methodName)] -- Harmony's
    // documented pattern for combining class- and method-level attributes.
    [HarmonyPatch(typeof(ZNet))]
    internal static class ZNetPatch
    {
        private static readonly HashSet<string> JoinedHostNames = new HashSet<string>();

        [HarmonyPatch(nameof(ZNet.RPC_CharacterID))]
        [HarmonyPostfix]
        private static void CharacterIdPostfix(ZRpc rpc, ZDOID characterID)
        {
            var peer = ZNet.instance.GetPeer(rpc);
            if (peer == null) return;

            var hostName = peer.m_socket == null ? peer.m_uid.ToString() : peer.m_socket.GetHostName();
            var playerName = string.IsNullOrEmpty(peer.m_playerName) ? "?" : peer.m_playerName;

            if (!JoinedHostNames.Add(hostName))
            {
                // Already tracked as joined -- a second character registration
                // for the same peer means they died and respawned, unless the
                // character id is 0 (no character yet).
                if (characterID.ID != 0)
                {
                    EventBuffer.Add("death", playerName, "");
                }
                return;
            }

            EventBuffer.Add("join", playerName, "");
        }

        [HarmonyPatch(nameof(ZNet.RPC_Disconnect))]
        [HarmonyPrefix]
        private static void DisconnectPrefix(ZRpc rpc)
        {
            var peer = ZNet.instance.GetPeer(rpc);
            if (peer == null || peer.m_uid == 0) return;

            var hostName = peer.m_socket == null ? peer.m_uid.ToString() : peer.m_socket.GetHostName();
            var playerName = string.IsNullOrEmpty(peer.m_playerName) ? "?" : peer.m_playerName;
            JoinedHostNames.Remove(hostName);
            EventBuffer.Add("leave", playerName, "");
        }
    }
}
