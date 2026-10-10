#!/usr/bin/env python3
"""Ultimate Multiplayerator server (phase 2, first stage).

One UDP port. Needs only Python 3.8+ (standard library), runs the same on Windows, Linux and macOS, with or without
a terminal attached. Start it with UMServer.bat / UMServer.sh or:  python3 um_server.py --port 7777 --password secret

What it does now: admits players (optional password, checked by challenge/response so the password never travels),
keeps the list of players, takes each player's state 20 times a second and sends everyone the states of the players
around them, answers server-list queries, and (optionally) checks movement for speed and teleport cheats.
The protocol is described in net/PROTOCOL.md.
"""
import argparse
import zlib
import hashlib
import hmac
import os
import select
import socket
import struct
import sys
import threading
import time
import urllib.request

PROTOCOL = 15
MAGIC = b"UM"

# packet types (first byte after the magic)
C_QUERY, C_HELLO, C_AUTH, C_STATE, C_BYE, C_PING, C_ENTITIES, C_VEHICLES, C_HIT, C_REVIVE, C_ENTITY_HIT, C_MISSION_ASK, C_MISSION_VOTE, C_MISSION_TEXT, C_MISSION_END, C_CUTSCENE, C_CUTSCENE_DONE, C_VEHICLE_HIT, C_MISSION_WORLD, C_PICKUP_TAKEN, C_MISSION_INVITE, C_PLAYER_DATA, C_PLAYER_DATA_GET, C_WORLD_GET, C_WORLD_PUT, C_WORLD_CHUNK, C_SAVE_ASK, C_SAVE_VOTE = 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 24, 25, 26, 27, 28
S_INFO, S_CHALLENGE, S_WELCOME, S_REJECT, S_SNAPSHOT, S_JOINED, S_LEFT, S_PONG, S_CORRECT, S_KICK, S_ENTITIES, S_ROLE, S_VEHICLES, S_TAKEN, S_DAMAGE, S_REVIVED, S_ENTITY_DAMAGE, S_MISSION_ASK, S_MISSION_RESULT, S_MISSION_TEXT, S_MISSION_END, S_CUTSCENE, S_CUTSCENE_FREE, S_VEHICLE_HIT, S_MISSION_WORLD, S_PICKUP_TAKEN, S_PARTY, S_PLAYER_DATA, S_WORLD_INFO, S_WORLD_CHUNK, S_WORLD_PUT, S_SAVE_ASK, S_SAVE_RESULT = 65, 66, 67, 68, 69, 70, 71, 72, 73, 74, 75, 76, 77, 78, 79, 80, 81, 82, 83, 84, 85, 86, 87, 88, 89, 90, 91, 92, 93, 94, 95, 96, 97

# The detailed state of a player (client version 2): what the buttons are doing, where the player aims, what they
# drive. The server does not look inside; it passes it on to the players near the sender (see net/PROTOCOL.md).
C_SYNC, S_SYNC = 29, 98
SYNC_MAX = 240

REJECT_FULL, REJECT_PASSWORD, REJECT_VERSION, REJECT_NAME, REJECT_BUSY = 1, 2, 3, 4, 5

# the place a new single-player game starts at (Grove Street, in front of CJ's house)
SPAWN = (2495.3, -1687.0, 13.5, 0.0)

STATE_FORMAT = "<IfffffHBHBBbIB" # (then: the vehicle this player rides in as a passenger, and the seat)   # sequence, x, y, z, heading, speed, health*10, flags, vehicle model, interior, weapon in hand, aim up/down * 127
STATE_SIZE = struct.calcsize(STATE_FORMAT)
ENTRY_FORMAT = "<HfffffHBHBBbIB" # (then: vehicle and seat; seat + 128 = the vehicle is the receiver's own, named by its key)   # player id, x, y, z, heading, speed, health*10, flags, vehicle model, interior, weapon in hand, aim up/down * 127
ENTRY_SIZE = struct.calcsize(ENTRY_FORMAT)

FLAG_IN_VEHICLE, FLAG_PAUSED, FLAG_DEAD, FLAG_AIMING, FLAG_FIRING, FLAG_DOWN = 1, 2, 4, 8, 16, 32

# The shared world (see net/PROTOCOL.md). Each pedestrian is simulated by one player's game, its "syncer": that game
# reports it, the server relays it to the players nearby, and their games show a copy instead of making pedestrians
# of their own. Where several players are together the server names one of them the area's syncer.
ENT_IN_FORMAT = "<IBHBffffHB"    # the syncer's own key, kind, model, ped type, x, y, z, heading, health*10, interior
ENT_IN_SIZE = struct.calcsize(ENT_IN_FORMAT)
ENT_OUT_FORMAT = "<IHBHBffffHB"  # id, syncer's player id, kind, model, ped type, x, y, z, heading, health*10, interior
ENT_OUT_SIZE = struct.calcsize(ENT_OUT_FORMAT)
KIND_PED, KIND_VEHICLE, KIND_SCRIPT_PED = 1, 2, 3   # 3: a character a mission's script made, in any player's game
VEH_MINE, VEH_PARKED, VEH_SCRIPT = 1, 2, 4            # bits of a vehicle's kind
ENT_PER_PLAYER = 64              # what one game may report
ENT_RANGE = 150.0                # an entity is sent to the players this near to it
ENT_LIFE = 1.5                   # seconds without a report before an entity is forgotten
MISSION_ASK_RANGE = 150.0        # players this near to the one starting a mission are asked to join
MISSION_ASK_TIME = 10.0          # seconds to answer; no answer is a no
SYNC_NEAR, SYNC_FAR = 200.0, 260.0  # another player's world takes over inside the first, is let go beyond the second
GROUP_NEAR, GROUP_FAR = 300.0, 340.0  # ... and a group reaches this far from the player whose game makes its street
# One syncer for each pedestrian and each empty vehicle, as in Multi Theft Auto (CPedSync.cpp, CUnoccupiedVehicleSync.cpp;
# docs/MTA_NOTES.md): the game that has it keeps it while its player is within the distance; beyond it, it is offered
# to the player within the distance whose game has the fewest. That game answers C_ADOPT, the old one is told S_RELEASE.
PED_SYNCER_DISTANCE, VEH_SYNCER_DISTANCE = 100.0, 130.0
# Getting into a vehicle is asked for, as in MTA (CGame.cpp, VEHICLE_REQUEST_IN): the server keeps who has which seat
# and answers. C_VEHICLE_IN: whose game simulates it (0xFFFF: `key` is a street vehicle's id), key, seat (0 driver,
# 1 any passenger seat), passenger seats it has. S_VEHICLE_IN: result (0 no, 1 yes, 2 yes - the driver is pulled out),
# the same owner and key, the seat given, the reason of a no (1 dead, 2 somebody is just getting in, 3 no seat free).
C_VEHICLE_IN, C_VEHICLE_OUT, S_VEHICLE_IN = 31, 32, 101
# What a pedestrian is doing to a player (attacking): from its game (key, target player, weapon), to the others by id.
C_ENTITY_ACTS, S_ENTITY_ACTS = 33, 102
C_ADOPT, S_HANDOVER, S_RELEASE = 30, 99, 100   # (kind 1 pedestrian / 2 vehicle, id, the adopting game's own key) / (kind, id) / (kind, the old game's key, id)
GROUP_MAX = 16                      # players in one group besides that one (the client's list is this long)

# Vehicles. A vehicle a player sits in is simulated by that player's game, whoever the area's syncer is; the traffic
# is simulated by the syncer's game like the pedestrians.
VEH_IN_FORMAT = "<IHfff3b3b3hHBBHBBBIB20sHBHBHB"  # key, model, x, y, z, forward*127, right*127, velocity cm/s, health, colour 1, colour 2,
                                        # driver's model, driver's ped type, 1 = the reporting player is in it, interior,
                                        # the id of another game's vehicle this player has got into (0 = none)
VEH_IN_SIZE = struct.calcsize(VEH_IN_FORMAT)
VEH_OUT_FORMAT = "<IHHfff3b3b3hHBBHBHBBB20sHBHBHB" # id, syncer, model, ..., driver's model, driver's ped type, player in it (0 = none), interior, kind bits
VEH_OUT_SIZE = struct.calcsize(VEH_OUT_FORMAT)
VEH_PER_PLAYER = 40
VEH_RANGE = 200.0


class Vehicle:
    __slots__ = ("id", "owner", "data", "x", "y", "player", "heard", "kind", "key", "offered", "offered_to")


class Entity:
    __slots__ = ("id", "owner", "key", "kind", "model", "pedtype", "x", "y", "z", "heading", "health", "interior", "heard", "offered", "offered_to")


class Player:
    __slots__ = ("id", "name", "addr", "joined", "last_heard", "sequence", "state", "state_time", "ping", "violations",
                 "last_correct", "packets", "packet_window", "jump_pos", "jump_since", "populates", "leader", "members", "released", "role_sent", "entities", "vehicles", "taken", "hits", "hit_window", "party", "asked_at", "cut_name", "cut_waiting", "ride", "invited", "account", "data", "data_dirty", "saved_spot")

    def __init__(self, pid, name, addr, now):
        self.id = pid
        self.name = name
        self.addr = addr
        self.joined = now
        self.last_heard = now
        self.sequence = 0
        self.state = (SPAWN[0], SPAWN[1], SPAWN[2], SPAWN[3], 0.0, 1000, 0, 0, 0, 0, 0)  # x y z heading speed health*10 flags vehicle interior weapon aim
        self.jump_pos = None
        self.jump_since = 0.0
        self.populates = True      # this player's game makes the pedestrians around them
        self.leader = None         # ... or this player's game does
        self.released = {}         # (kind, this game's key) -> (when, id): handed over to another game; reports of it are refused
        self.members = []          # the numbers of the players whose street this player's game makes besides its own
        self.role_sent = 0.0
        self.entities = {}         # the syncer's key -> Entity
        self.vehicles = {}         # the game's key -> Vehicle
        self.taken = {}            # the game's key -> when another player took that vehicle over
        self.asked_at = 0.0        # when this player's last mission question was settled
        self.account = name.lower()  # the name the player asked for: what their saved data is filed under
        self.data = {}             # part number -> bytes: items, looks, statistics, as their game reports them
        self.data_dirty = False
        self.saved_spot = None     # (x, y, z, heading) they were at when they last left
        self.invited = {}          # player id -> when this player's mission last invited them
        self.ride = (0, 0)         # passenger: (vehicle id, seat)
        self.cut_name = b""         # the cutscene this player's mission is showing
        self.cut_waiting = set()   # ids of the players who have not watched or skipped it yet
        self.party = set()         # ids of the players who joined the mission this player's game is running
        self.hits = 0              # hits reported in the current second
        self.hit_window = now
        self.state_time = now
        self.ping = 0
        self.violations = 0
        self.last_correct = 0.0
        self.packets = 0
        self.packet_window = now


class Server:
    def __init__(self, args):
        self.name = args.name[:48]
        self.password = args.password or ""
        self.max_players = max(1, min(args.max_players, 1000))
        self.tick = max(5, min(args.tick, 60))
        self.radius = args.radius
        # The rate every game simulates at (frames a second): 60, 30 or 25, never more than 60. San Andreas' physics
        # depend on the frame step; with SilentPatch and FramerateVigilante 60 behaves like the original 25 / 30.
        self.fps = args.fps if args.fps in (25, 30, 60) else 60
        self.anticheat = args.anticheat and not args.no_anticheat
        self.timeout = 15.0
        self.data_dir = args.data
        # the world: one saved game for everybody (a normal GTA save file), with a version that goes up with every
        # accepted upload. No file = a new game. The server does not look inside.
        self.world_path = args.save or os.path.join(args.data, "world.b")
        self.world = b""
        self.world_version = 0
        self.world_script = 0      # fingerprint of the game script the save was made with (0 = unknown)
        self.upload = None         # {"player", "data", "size", "crc", "script", "at"}: one upload at a time
        self.save_vote = None      # {"id", "by", "yes", "no", "deadline"}
        self.autosave = max(0.0, args.autosave) * 60.0
        self.autosave_at = time.time()
        try:
            with open(self.world_path, "rb") as f:
                self.world = f.read()
            self.world_version = 1
        except OSError:
            pass
        try:
            with open(self.world_path + ".info", "r") as f:
                version, script = f.read().split()[:2]
                self.world_version, self.world_script = max(1, int(version)), int(script)
        except (OSError, ValueError):
            pass
        self.data_written = 0.0
        self.players = {}        # id -> Player
        self.by_addr = {}        # address -> Player
        self.pending = {}        # address -> (nonce, name, time)
        self.failures = {}       # ip -> (count, first time): wrong passwords
        self.unknown = {}        # ip -> (count, window start): packets from addresses that are not players
        self.next_id = 1
        self.next_entity = 1
        self.entity_index = {}   # id -> Entity (pedestrians), for hits
        self.votes = {}          # vote id -> {host, mission, asked, answers, deadline}
        # One mission at a time on a server (user's rule, 2026-10-10): the id of the player whose game runs it, or
        # None. It runs in that game; when that player leaves, the mission has failed for everybody in it.
        self.mission_owner = None
        self.mission_number = 0
        self.next_vote = 1
        self.running = True
        self.started = time.time()
        self.lock = threading.Lock()   # the console thread reads and changes what the network loop uses
        self.sent = self.received = 0
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 1 << 20)
        self.sock.bind((args.bind, args.port))
        self.sock.setblocking(False)
        self.port = self.sock.getsockname()[1]

    # ---------------------------------------------------------------- output
    def say(self, text):
        print(time.strftime("[%H:%M:%S] ") + text, flush=True)

    def send(self, addr, kind, payload=b""):
        try:
            self.sock.sendto(MAGIC + bytes([kind]) + payload, addr)
            self.sent += 1
        except OSError:
            pass

    # ---------------------------------------------------------------- joining
    def info_payload(self):
        name = self.name.encode("utf-8")[:60]
        return struct.pack("<BHHBB", PROTOCOL, len(self.players), self.max_players, 1 if self.password else 0, len(name)) + name

    def throttled(self, ip, now):
        count, since = self.unknown.get(ip, (0, now))
        if now - since > 1.0:
            count, since = 0, now
        self.unknown[ip] = (count + 1, since)
        return count >= 20  # a stranger gets 20 packets a second, the rest is dropped unread

    def on_hello(self, addr, data, now):
        if len(data) < 2:
            return
        version, name_len = data[0], data[1]
        name = data[2:2 + name_len].decode("utf-8", "replace").strip()
        name = "".join(ch for ch in name if ch.isprintable())[:24]
        if version != PROTOCOL:
            return self.send(addr, S_REJECT, bytes([REJECT_VERSION]))
        if len(name) < 1:
            return self.send(addr, S_REJECT, bytes([REJECT_NAME]))
        if addr in self.by_addr:  # the welcome was lost: say it again
            return self.welcome(self.by_addr[addr])
        count, since = self.failures.get(addr[0], (0, now))
        if count >= 5 and now - since < 60.0:
            return self.send(addr, S_REJECT, bytes([REJECT_BUSY]))
        if len(self.players) >= self.max_players:
            return self.send(addr, S_REJECT, bytes([REJECT_FULL]))
        if len(self.pending) > 4096:
            self.pending.clear()
        nonce = os.urandom(16)
        self.pending[addr] = (nonce, name, now)
        self.send(addr, S_CHALLENGE, nonce)

    def on_auth(self, addr, data, now):
        entry = self.pending.pop(addr, None)
        if not entry or len(data) < 32 or now - entry[2] > 10.0:
            return
        nonce, name, _ = entry
        expected = hashlib.sha256(nonce + self.password.encode("utf-8")).digest()
        if not hmac.compare_digest(expected, data[:32]):
            count, since = self.failures.get(addr[0], (0, now))
            if now - since > 60.0:
                count, since = 0, now
            self.failures[addr[0]] = (count + 1, since)
            self.say("refused %s:%d (%s): wrong password" % (addr[0], addr[1], name))
            return self.send(addr, S_REJECT, bytes([REJECT_PASSWORD]))
        if len(self.players) >= self.max_players:
            return self.send(addr, S_REJECT, bytes([REJECT_FULL]))
        taken = {p.name.lower() for p in self.players.values()}
        base, n = name, 2
        while name.lower() in taken:
            name = "%s(%d)" % (base[:20], n)
            n += 1
        while self.next_id in self.players or self.next_id == 0:
            self.next_id = self.next_id % 65000 + 1
        player = Player(self.next_id, name, addr, now)
        player.account = base.lower()
        self.load_player_data(player)
        self.next_id = self.next_id % 65000 + 1
        self.players[player.id] = player
        self.by_addr[addr] = player
        self.welcome(player)
        self.send(addr, S_WORLD_INFO, self.world_info())
        joined = struct.pack("<HB", player.id, len(name.encode("utf-8"))) + name.encode("utf-8")
        for other in self.players.values():
            if other is not player:
                self.send(other.addr, S_JOINED, joined)
                other_name = other.name.encode("utf-8")
                self.send(addr, S_JOINED, struct.pack("<HB", other.id, len(other_name)) + other_name)
        self.say("%s joined from %s:%d as player %d (%d online)" % (name, addr[0], addr[1], player.id, len(self.players)))

    # ---------------------------------------------------------------- what the server keeps for each player
    # One file per player name in <data>/players: the parts their game sent (0 money, health, armour, weapons;
    # 1 clothes, hair, tattoos; 2 and 3 statistics and skills) and where they were when they left. The server does not
    # look inside the parts; it hands them back when a player of that name joins, whoever was online in between.
    def player_file(self, player):
        safe = "".join(c if c.isalnum() or c in "-_" else "_%02x" % ord(c) for c in player.account)[:80] or "_"
        return os.path.join(self.data_dir, "players", safe + ".bin")

    def load_player_data(self, player):
        try:
            with open(self.player_file(player), "rb") as f:
                raw = f.read()
        except OSError:
            return
        try:
            at = 0
            while at + 3 <= len(raw):
                part, size = struct.unpack_from("<BH", raw, at)
                body = raw[at + 3:at + 3 + size]
                at += 3 + size
                if part == 200 and size == 16:
                    player.saved_spot = struct.unpack("<ffff", body)
                elif part < 16:
                    player.data[part] = body
        except struct.error:
            self.say("the saved data of %s is damaged and was not used" % player.name)
            player.data, player.saved_spot = {}, None

    def save_player_data(self, player, leaving=False):
        if leaving and not player.state[6] & 1 and player.state[8] == 0 and player.state_time:   # on foot, outdoors
            player.saved_spot = (player.state[0], player.state[1], player.state[2], player.state[3])
            player.data_dirty = True
        if not player.data_dirty:
            return
        player.data_dirty = False
        raw = b"".join(struct.pack("<BH", part, len(body)) + body for part, body in sorted(player.data.items()))
        if player.saved_spot:
            raw += struct.pack("<BH", 200, 16) + struct.pack("<ffff", *player.saved_spot)
        path = self.player_file(player)
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path + ".tmp", "wb") as f:
                f.write(raw)
            os.replace(path + ".tmp", path)   # never a half-written file
        except OSError as e:
            self.say("could not save the data of %s: %s" % (player.name, e))

    def send_player_data(self, player):
        for part, body in sorted(player.data.items()):
            self.send(player.addr, S_PLAYER_DATA, bytes([part]) + body)
        self.send(player.addr, S_PLAYER_DATA, bytes([255, len(player.data)]))   # "that was all"

    def welcome(self, player):
        name = player.name.encode("utf-8")
        spot = player.saved_spot or SPAWN
        self.send(player.addr, S_WELCOME, struct.pack("<HBffffBB", player.id, self.tick, spot[0], spot[1], spot[2], spot[3],
                                                      1 if self.anticheat else 0, len(name)) + name + bytes([self.fps]))

    def drop(self, player, why, kick_reason=None):
        if player.id not in self.players:
            return
        self.save_player_data(player, leaving=True)
        if kick_reason is not None:
            text = kick_reason.encode("utf-8")[:100]
            self.send(player.addr, S_KICK, bytes([len(text)]) + text)
        del self.players[player.id]
        self.by_addr.pop(player.addr, None)
        for entity in player.entities.values():
            self.entity_index.pop(entity.id, None)
        player.entities.clear()    # what its game simulated is gone with it; the next syncer's game fills the street again
        player.vehicles.clear()
        self.cutscene_watched(player)
        if self.mission_owner == player.id:
            # the game that ran the mission is gone: failed for everybody who was in it (result 2 = its player left)
            for i in player.party:
                other = self.players.get(i)
                if other:
                    self.send(other.addr, S_MISSION_END, struct.pack("<HB", player.id, 2))
            self.say("%s left while their game was running mission %d: the mission has failed for %d more players" % (player.name, self.mission_number, len(player.party)))
            self.mission_owner = None
        for host in self.players.values():
            if player.id in host.party:
                host.party.discard(player.id)
                self.send_party(host)
        left = struct.pack("<H", player.id)
        for other in self.players.values():
            self.send(other.addr, S_LEFT, left)
        self.say("%s left (%s), %d online" % (player.name, why, len(self.players)))

    # ---------------------------------------------------------------- state
    def on_state(self, player, data, now):
        if len(data) < STATE_SIZE:
            return
        sequence, x, y, z, heading, speed, health, flags, vehicle, interior, weapon, aim, ride, seat = struct.unpack_from(STATE_FORMAT, data)
        if sequence <= player.sequence and player.sequence - sequence < 1 << 31:
            return  # late or repeated
        if not all(v == v and abs(v) < 1.0e6 for v in (x, y, z, heading, speed)):
            return  # not numbers
        player.sequence = sequence
        if self.anticheat and not self.plausible(player, x, y, z, flags, interior, now):
            return
        player.state = (x, y, z, heading, speed, min(health, 2000), flags, vehicle, interior, weapon if weapon <= 46 else 0, aim)
        player.ride = (ride, seat & 7) if flags & 1 else (0, 0)
        player.state_time = now

    def plausible(self, player, x, y, z, flags, interior, now):
        """Movement check. Nothing in the game moves a player faster than about 90 m/s (a jet), except the game itself:
        a door (interiors are a kilometre up in the air), a respawn after dying, a mission moving its player. So a
        jump is accepted when the interior changes, the player is dead, was paused, or has just joined. Any other
        jump is answered with "go back" and counts as one violation; if the client stays at the new place for 1.5 s
        (the game put it there, it cannot go back) the place is accepted. Eight violations within a minute: removed."""
        px, py, pz = player.state[0:3]
        dt = max(now - player.state_time, 0.02)
        distance = ((x - px) ** 2 + (y - py) ** 2 + (z - pz) ** 2) ** 0.5
        if (distance <= 100.0 * dt + 15.0 or (flags & FLAG_DEAD) or (player.state[6] & FLAG_DEAD) or interior != player.state[8]
                or now - player.joined < 5.0):
            if player.jump_pos is None and player.violations and now - player.last_correct > 60.0:
                player.violations = 0
            player.jump_pos = None
            return True
        jump = player.jump_pos
        if jump is None or (x - jump[0]) ** 2 + (y - jump[1]) ** 2 + (z - jump[2]) ** 2 > 30.0 ** 2:
            player.jump_pos = (x, y, z)        # a new jump
            player.jump_since = now
            player.violations += 1
            player.last_correct = now
            self.send(player.addr, S_CORRECT, struct.pack("<ffff", px, py, pz, player.state[3]))
            if player.violations >= 8:
                self.say("anti-cheat: %s jumped %.0f m, %d times within a minute: removed" % (player.name, distance, player.violations))
                self.drop(player, "anti-cheat", "movement the game does not allow")
            return False
        if now - player.jump_since > 1.5:      # it stayed there: the game moved it
            player.jump_pos = None
            return True
        return False

    # ---------------------------------------------------------------- damage between players
    def on_hit(self, attacker, data, now):
        """The attacker's game saw its player hurt another player's character (a shot, a punch, a car). The server
        checks that it could have happened and tells the victim's game, which takes the health off."""
        if len(data) < 5:
            return
        target_id, damage, weapon = struct.unpack_from("<HHB", data)
        victim = self.players.get(target_id)
        if not victim or victim is attacker:
            return
        if now - attacker.hit_window > 1.0:
            attacker.hits, attacker.hit_window = 0, now
        attacker.hits += 1
        if self.anticheat:
            a, v = attacker.state, victim.state
            distance = ((a[0] - v[0]) ** 2 + (a[1] - v[1]) ** 2 + (a[2] - v[2]) ** 2) ** 0.5
            in_vehicle = bool(a[6] & FLAG_IN_VEHICLE)
            allowed = {a[9], 49, 50, 51} if in_vehicle else {a[9], 51}   # the weapon in hand; a vehicle; an explosion
            reach = 12.0 if (weapon == a[9] and weapon < 16) or weapon in (49, 50) else 320.0   # fists and blades, a car; a bullet
            if (attacker.hits > 40 or damage > 3000 or weapon not in allowed or distance > reach
                    or a[8] != v[8] or (a[6] & (FLAG_DEAD | FLAG_DOWN)) or (v[6] & (FLAG_DEAD | FLAG_DOWN))):
                return
        self.send(victim.addr, S_DAMAGE, struct.pack("<HHB", attacker.id, damage, weapon))

    # ---------------------------------------------------------------- missions
    def party_of(self, host):
        return [self.players[i] for i in host.party if i in self.players]

    def on_vehicle_hit(self, player, body):
        """A player damaged the copy of a vehicle another game simulates: that game is told, by its own key for it."""
        vehicle_id, loss = struct.unpack_from("<IH", body)
        for owner in self.players.values():
            if owner is player:
                continue
            for key, v in owner.vehicles.items():
                if v.id != vehicle_id:
                    continue
                if self.anticheat and ((v.x - player.state[0]) ** 2 + (v.y - player.state[1]) ** 2 > 320.0 ** 2 or loss > 3000):
                    return
                self.send(owner.addr, S_VEHICLE_HIT, struct.pack("<IH", key, loss) + bytes(body[6:26]) + struct.pack("<H", player.id))
                return

    def on_cutscene(self, host, name, area=0):
        """The game that runs a mission starts a cutscene: the players who joined see it too, and the mission's
        script is held after it until all of them have watched or skipped it. The game repeats the packet until it
        is told that it may go on."""
        if name != host.cut_name:
            host.cut_name = name
            host.cut_waiting = set(p.id for p in self.party_of(host))
            self.say("%s's mission shows cutscene %s to %d more players" % (host.name, name.rstrip(b"\0").decode("latin-1"), len(host.cut_waiting)))
        if not host.cut_waiting:
            self.send(host.addr, S_CUTSCENE_FREE, b"")
            return
        for i in host.cut_waiting:
            if i in self.players:
                self.send(self.players[i].addr, S_CUTSCENE, struct.pack("<H", host.id) + name + bytes([area]))  # (area: the interior the host's script has selected, as of now)

    def cutscene_watched(self, player):
        for host in self.players.values():
            if player.id in host.cut_waiting:
                host.cut_waiting.discard(player.id)
                if not host.cut_waiting:
                    self.send(host.addr, S_CUTSCENE_FREE, b"")

    def on_mission_ask(self, host, mission, now):
        """A player's game is about to start a mission: the players near them are asked whether they join. The game
        holds the mission back until it is told the result. Everybody asked must say yes; no answer in time is a no."""
        for vote in self.votes.values():
            if vote["host"] == host.id and not vote.get("invite"):
                return  # already asking (the game repeats the request until it hears the result)
        if now - host.asked_at < 2.0:
            return  # a late repeat of a request that has just been settled
        if self.mission_owner is not None and self.mission_owner != host.id and self.mission_owner in self.players:
            # another player's mission is running: not started (answer 3 = "a mission is already running")
            host.asked_at = now
            self.send(host.addr, S_MISSION_RESULT, struct.pack("<HHBBB", 0, self.mission_owner, mission, 0, 3))
            self.say("%s wanted to start mission %d, but the mission of %s is running" % (host.name, mission, self.players[self.mission_owner].name))
            return
        asked = [p for p in self.players.values() if p is not host and p.state[8] == host.state[8]
                 and (p.state[0] - host.state[0]) ** 2 + (p.state[1] - host.state[1]) ** 2 <= MISSION_ASK_RANGE ** 2]
        vote_id = self.next_vote
        self.next_vote = self.next_vote % 65000 + 1
        self.votes[vote_id] = {"host": host.id, "mission": mission, "asked": {p.id for p in asked}, "answers": {}, "deadline": now + MISSION_ASK_TIME}
        for p in asked:
            self.send(p.addr, S_MISSION_ASK, struct.pack("<HHB", vote_id, host.id, mission))
        self.say("%s wants to start mission %d; %d players asked" % (host.name, mission, len(asked)))
        self.check_vote(vote_id, now)

    # ---------------------------------------------------------------- the world save
    def world_info(self):
        return struct.pack("<IIII", self.world_version, len(self.world), zlib.crc32(self.world) & 0xFFFFFFFF, self.world_script)

    def on_world_put(self, player, body, now):
        """A game offers a new world save: the version it was made from, its size, checksum, the script fingerprint and
        why (1 mission passed, 2 vote, 3 timer). Taken only when made from the newest version, one upload at a time."""
        base, size, crc, script, reason = struct.unpack_from("<IIIIB", body)
        busy = self.upload and self.upload["player"] != player.id and now - self.upload["at"] < 5.0
        if base != self.world_version or busy or not 1000 <= size <= 4000000:
            self.send(player.addr, S_WORLD_PUT, struct.pack("<BI", 0, self.world_version))
            return
        if not self.upload or self.upload["player"] != player.id or self.upload["crc"] != crc:
            self.upload = {"player": player.id, "data": bytearray(), "size": size, "crc": crc, "script": script, "at": now, "reason": reason}
        self.send(player.addr, S_WORLD_PUT, struct.pack("<BI", 1, len(self.upload["data"])))

    def on_world_chunk(self, player, body, now):
        up = self.upload
        if not up or up["player"] != player.id:
            return
        offset = struct.unpack_from("<I", body)[0]
        if offset == len(up["data"]):
            up["data"] += body[4:]
            up["at"] = now
        if len(up["data"]) < up["size"]:
            self.send(player.addr, S_WORLD_PUT, struct.pack("<BI", 1, len(up["data"])))
            return
        self.upload = None
        data = bytes(up["data"][:up["size"]])
        if zlib.crc32(data) & 0xFFFFFFFF != up["crc"]:
            self.send(player.addr, S_WORLD_PUT, struct.pack("<BI", 0, self.world_version))
            return
        self.world, self.world_script = data, up["script"]
        self.world_version += 1
        self.autosave_at = now
        try:
            os.makedirs(os.path.dirname(os.path.abspath(self.world_path)), exist_ok=True)
            if os.path.exists(self.world_path):
                os.replace(self.world_path, self.world_path + ".bak")   # the one before is kept
            with open(self.world_path + ".tmp", "wb") as f:
                f.write(data)
            os.replace(self.world_path + ".tmp", self.world_path)
            with open(self.world_path + ".info", "w") as f:
                f.write("%d %d\n" % (self.world_version, self.world_script))
        except OSError as e:
            self.say("could not write the world save: %s" % e)
        self.say("world save %d from %s (%d bytes, %s)" % (self.world_version, player.name, len(data), {1: "mission passed", 2: "players' vote", 3: "timer"}.get(up["reason"], "?")))
        self.send(player.addr, S_WORLD_PUT, struct.pack("<BI", 2, self.world_version))   # taken
        info = self.world_info()
        for p in self.players.values():
            self.send(p.addr, S_WORLD_INFO, info)

    def on_save_ask(self, player, now):
        """A player at a save point asks to save: everybody votes; more than half of all players must say yes."""
        if self.save_vote and now < self.save_vote["deadline"]:
            return
        vote_id = self.next_vote
        self.next_vote = self.next_vote % 65000 + 1
        self.save_vote = {"id": vote_id, "by": player.id, "yes": {player.id}, "no": set(), "deadline": now + 15.0}
        for p in self.players.values():
            if p is not player:
                self.send(p.addr, S_SAVE_ASK, struct.pack("<HH", vote_id, player.id))
        self.say("%s asks to save the world; %d players vote" % (player.name, len(self.players)))
        self.check_save_vote(now)

    def check_save_vote(self, now):
        vote = self.save_vote
        if not vote:
            return
        total = len(self.players)
        yes = len([i for i in vote["yes"] if i in self.players])
        no = len([i for i in vote["no"] if i in self.players])
        if yes * 2 > total:
            accepted = True
        elif no * 2 >= total or now >= vote["deadline"]:
            accepted = False
        else:
            return
        self.save_vote = None
        self.say("save vote: %d of %d said yes: %s" % (yes, total, "saving" if accepted else "not saved"))
        for p in self.players.values():
            self.send(p.addr, S_SAVE_RESULT, struct.pack("<HBH", vote["id"], 1 if accepted else 0, vote["by"]))

    def send_party(self, host):
        """The game that runs a mission is told who is in it: only their doings count for its script."""
        ids = [i for i in host.party if i in self.players][:60]
        self.send(host.addr, S_PARTY, bytes([len(ids)]) + b"".join(struct.pack("<H", i) for i in ids))

    def leave_parties(self, player, keep=None):
        for host in self.players.values():
            if host is not keep and player.id in host.party:
                host.party.discard(player.id)
                self.send_party(host)
                self.send(player.addr, S_MISSION_END, struct.pack("<HB", host.id, 0))
        self.cutscene_watched(player)

    def on_mission_invite(self, host, target_id, now):
        """A player who is not in the mission did something that would count for it: they are asked whether they
        join (mission number 255 in the question). Not more often than every 30 seconds."""
        target = self.players.get(target_id)
        if not target or target is host or target_id in host.party or now - host.invited.get(target_id, -100.0) < 30.0:
            return
        host.invited[target_id] = now
        vote_id = self.next_vote
        self.next_vote = self.next_vote % 65000 + 1
        self.votes[vote_id] = {"host": host.id, "mission": 255, "asked": {target_id}, "answers": {}, "deadline": now + MISSION_ASK_TIME, "invite": True}
        self.send(target.addr, S_MISSION_ASK, struct.pack("<HHB", vote_id, host.id, 255))
        self.say("%s is asked to join the mission of %s" % (target.name, host.name))

    def on_mission_vote(self, player, vote_id, answer, now):
        vote = self.votes.get(vote_id)
        if vote and player.id in vote["asked"] and answer in (0, 1, 2):
            if vote.get("invite") and answer == 2:
                answer = 1   # into a running mission: join or not, nobody is brought anywhere
            vote["answers"][player.id] = answer
            self.check_vote(vote_id, now)

    def check_vote(self, vote_id, now):
        vote = self.votes.get(vote_id)
        if not vote:
            return
        host = self.players.get(vote["host"])
        asked = {i for i in vote["asked"] if i in self.players}   # who left does not count
        answers = vote["answers"]
        rejected = any(answers.get(i) == 0 for i in asked)
        if host and not rejected and now < vote["deadline"] and any(i not in answers for i in asked):
            return  # still waiting
        accepted = bool(host) and not rejected and all(answers.get(i, 0) in (1, 2) for i in asked)
        del self.votes[vote_id]
        if not host:
            return
        if vote.get("invite"):   # one more player for a mission that is running
            for i in asked:
                p = self.players[i]
                if accepted:
                    self.leave_parties(p, keep=host)
                    host.party.add(i)
                    self.say("%s joined the mission of %s" % (p.name, host.name))
                self.send(p.addr, S_MISSION_RESULT, struct.pack("<HHBBB", vote_id, host.id, 255, 1 if accepted else 0, answers.get(i, 0)))
            if accepted:
                self.send_party(host)
            return
        host.asked_at = now
        if accepted:
            self.mission_owner, self.mission_number = host.id, vote["mission"]
            host.party = set(asked)
            for i in asked:
                self.leave_parties(self.players[i], keep=host)
            self.send_party(host)
        self.say("mission %d of %s: %s" % (vote["mission"], host.name, "everybody joined" if accepted else "not started (somebody said no or did not answer)"))
        for i in list(asked) + [host.id]:
            p = self.players.get(i)
            if p:
                self.send(p.addr, S_MISSION_RESULT, struct.pack("<HHBBB", vote_id, host.id, vote["mission"], 1 if accepted else 0, answers.get(i, 0)))

    def on_entity_hit(self, attacker, data, now):
        """A player hurt the copy of a pedestrian or mission character that another game simulates: that game is told,
        and does the damage to the real one."""
        if len(data) < 7:
            return
        entity_id, damage, weapon = struct.unpack_from("<IHB", data)
        entity = self.entity_index.get(entity_id)
        owner = self.players.get(entity.owner) if entity else None
        if not owner or owner is attacker:
            return
        if now - attacker.hit_window > 1.0:
            attacker.hits, attacker.hit_window = 0, now
        attacker.hits += 1
        if self.anticheat:
            a = attacker.state
            if attacker.hits > 40 or damage > 3000 or (a[6] & (FLAG_DEAD | FLAG_DOWN)) or (a[0] - entity.x) ** 2 + (a[1] - entity.y) ** 2 > 320.0 ** 2:
                return
        self.send(owner.addr, S_ENTITY_DAMAGE, struct.pack("<IHBH", entity.key, damage, weapon, attacker.id))

    def on_revive(self, helper, data, now):
        """A player who would have died lies knocked down for a while (their game says so with the "down" flag);
        another player who stays beside them brings them back. The helper's game reports that it has done its part."""
        if len(data) < 2:
            return
        target = self.players.get(struct.unpack_from("<H", data)[0])
        if not target or target is helper:
            return
        h, t = helper.state, target.state
        if not (t[6] & FLAG_DOWN) or (h[6] & (FLAG_DEAD | FLAG_DOWN | FLAG_IN_VEHICLE)) or h[8] != t[8]:
            return
        if (h[0] - t[0]) ** 2 + (h[1] - t[1]) ** 2 + (h[2] - t[2]) ** 2 > 4.0 ** 2:
            return
        self.send(target.addr, S_REVIVED, struct.pack("<H", helper.id))

    # ---------------------------------------------------------------- the shared world
    def roles(self, now):
        """Who makes the pedestrians where players are together: the player with the lowest number within reach.
        The others' games are told to stop making their own and show that player's instead."""
        players = sorted(self.players.values(), key=lambda p: p.id)
        # Groups: the lowest number not in a group yet leads one; into it go the players within SYNC_NEAR of ANY of
        # its players (so three in a row are one group, not "the middle one's street comes from the first and the
        # third has none"), as long as they are within GROUP_NEAR of the leader, whose game has to make their street.
        # A player already in the group is let go a little later (SYNC_FAR, GROUP_FAR), so the role does not flicker.
        outdoors = [p for p in players if p.state[8] == 0]         # indoors everybody keeps their own
        leader, groups = {}, {}
        for me in outdoors:
            if me.id in leader:
                continue
            leader[me.id] = me
            group = [me]
            grew = True
            while grew and len(group) <= GROUP_MAX:
                grew = False
                for other in outdoors:
                    if other.id in leader or len(group) > GROUP_MAX:
                        continue
                    was = other.leader is me
                    reach = GROUP_FAR if was else GROUP_NEAR
                    if (other.state[0] - me.state[0]) ** 2 + (other.state[1] - me.state[1]) ** 2 >= reach * reach:
                        continue
                    link = SYNC_FAR if was else SYNC_NEAR
                    if any((other.state[0] - g.state[0]) ** 2 + (other.state[1] - g.state[1]) ** 2 < link * link for g in group):
                        leader[other.id] = me
                        group.append(other)
                        grew = True
            groups[me.id] = group
        for me in players:
            mine = leader.get(me.id, me)
            populates = mine is me
            members = [g.id for g in groups.get(me.id, [me])[1:]] if populates else []
            if members != me.members:
                me.members = members
                me.role_sent = 0.0
            me.leader = None if populates else mine
            if populates != me.populates:
                me.populates = populates
                me.role_sent = 0.0
                # (what its game simulates stays its own until handed over: see handover)
            if now - me.role_sent > 1.0:                           # repeated: a lost packet must not leave a game wrong
                me.role_sent = now
                # (the role; then, for the game that makes a street, the players it makes it for besides its own)
                self.send(me.addr, S_ROLE, bytes([1 if me.populates else 0, len(me.members)]) + struct.pack("<%dH" % len(me.members), *me.members))

    def on_entities(self, player, data, now):
        if not data:
            return
        count = data[0]
        if len(data) < 1 + count * ENT_IN_SIZE:
            return
        for i in range(count):
            key, kind, model, pedtype, x, y, z, heading, health, interior = struct.unpack_from(ENT_IN_FORMAT, data, 1 + i * ENT_IN_SIZE)
            if kind not in (KIND_PED, KIND_SCRIPT_PED) or model > 19999 or not (4 <= pedtype <= 31):
                continue
            if self.was_released(player, 1, key, now):
                continue
            if not all(v == v and abs(v) < 1.0e6 for v in (x, y, z, heading)):
                continue
            # a game reports what is around its own player, nothing else
            if (x - player.state[0]) ** 2 + (y - player.state[1]) ** 2 > 300.0 ** 2:
                continue
            entity = player.entities.get(key)
            if entity is None:
                if len(player.entities) >= ENT_PER_PLAYER:
                    continue
                entity = Entity()
                entity.id = self.next_entity
                self.next_entity = self.next_entity % 0x7FFFFFFF + 1
                entity.owner, entity.key = player.id, key
                entity.offered, entity.offered_to = now, 0   # (not offered in its first second: the others must have been sent it first)
                player.entities[key] = entity
                self.entity_index[entity.id] = entity
            elif self.anticheat and (x - entity.x) ** 2 + (y - entity.y) ** 2 > 60.0 ** 2:
                continue  # a pedestrian does not jump 60 m between two reports
            entity.kind, entity.model, entity.pedtype = kind, model, pedtype
            entity.x, entity.y, entity.z, entity.heading, entity.health, entity.interior = x, y, z, heading, min(health, 2000), interior
            entity.heard = now

    def on_vehicles(self, player, data, now):
        if not data:
            return
        count = data[0]
        if len(data) < 1 + count * VEH_IN_SIZE:
            return
        for i in range(count):
            fields = struct.unpack_from(VEH_IN_FORMAT, data, 1 + i * VEH_IN_SIZE)
            key, model, x, y, z = fields[0:5]
            driver_model, driver_type, mine, interior, took = fields[17:22]
            lights, damage = fields[22:24]
            riders = tuple(v if (i % 2 == 0 and v <= 19999) or (i % 2 == 1 and 4 <= v <= 31) else 0 for i, v in enumerate(fields[24:30]))
            if key in player.taken:
                # another player sits in this one now and their game simulates it: this game is told to let go of it
                if now - player.taken[key] < 20.0:
                    self.send(player.addr, S_TAKEN, struct.pack("<I", key))
                    continue
                del player.taken[key]
            if mine and took:
                self.take_over(player, took, now)
            kind = mine & (VEH_MINE | VEH_PARKED | VEH_SCRIPT)
            mine = kind & VEH_MINE
            if not mine and self.was_released(player, 2, key, now):
                continue
            if not (400 <= model <= 611) or driver_model > 19999:
                continue
            if not all(v == v and abs(v) < 1.0e6 for v in (x, y, z)):
                continue
            if (x - player.state[0]) ** 2 + (y - player.state[1]) ** 2 > 300.0 ** 2:
                continue
            vehicle = player.vehicles.get(key)
            if vehicle is None:
                if len(player.vehicles) >= VEH_PER_PLAYER:
                    continue
                vehicle = Vehicle()
                vehicle.id = self.next_entity
                self.next_entity = self.next_entity % 0x7FFFFFFF + 1
                vehicle.owner, vehicle.key = player.id, key
                vehicle.offered, vehicle.offered_to = now, 0   # (not offered in its first second: the others must have been sent it first)
                player.vehicles[key] = vehicle
            elif self.anticheat and (x - vehicle.x) ** 2 + (y - vehicle.y) ** 2 > 200.0 ** 2:
                continue  # no vehicle jumps 200 m between two reports
            vehicle.x, vehicle.y = x, y
            vehicle.player = player.id if mine else 0
            vehicle.kind = kind
            vehicle.data = fields[1:17] + (driver_model, driver_type if 4 <= driver_type <= 31 else 0, vehicle.player, interior, kind, lights, damage) + riders
            vehicle.heard = now

    def was_released(self, player, kind, key, now):
        """This game still reports something it has handed over: it is told again (the first word may have been lost)."""
        gone = player.released.get((kind, key))
        if gone is None:
            return False
        if now - gone[0] > 10.0:
            del player.released[(kind, key)]
            return False
        self.send(player.addr, S_RELEASE, struct.pack("<BII", kind, key, gone[1]))
        return True

    def handover(self, now):
        """MTA's rule: a syncer is kept while its player is within the distance; otherwise the player within the
        distance whose game simulates the fewest is offered it (once a second until one answers)."""
        players = [p for p in self.players.values()]
        if len(players) < 2:
            return
        load = {p.id: len(p.entities) + len(p.vehicles) for p in players}
        for owner in players:
            ox, oy, interior = owner.state[0], owner.state[1], owner.state[8]
            things = [(1, e, e.x, e.y, PED_SYNCER_DISTANCE) for e in owner.entities.values() if e.kind == KIND_PED]
            things += [(2, v, v.x, v.y, VEH_SYNCER_DISTANCE) for v in owner.vehicles.values() if not v.kind & (VEH_MINE | VEH_SCRIPT) and not v.player]
            for kind, thing, x, y, limit in things:
                if now - thing.offered < 1.0:
                    continue
                paused = now - owner.state_time > 1.0   # its game draws no frames: it cannot simulate anything
                if paused:
                    limit = ENT_RANGE
                elif (x - ox) ** 2 + (y - oy) ** 2 <= limit * limit:
                    continue
                best = None
                for p in players:
                    if p is owner or p.state[8] != interior or (p.state[6] & FLAG_DEAD):
                        continue
                    if (x - p.state[0]) ** 2 + (y - p.state[1]) ** 2 <= limit * limit and (best is None or load[p.id] < load[best.id]):
                        best = p
                if best is None:
                    continue
                thing.offered, thing.offered_to = now, best.id
                self.send(best.addr, S_HANDOVER, struct.pack("<BI", kind, thing.id))

    def on_adopt(self, player, data, now):
        if len(data) < 9:
            return
        kind, thing_id, key = struct.unpack_from("<BII", data)
        for owner in self.players.values():
            if owner is player:
                continue
            table = owner.entities if kind == 1 else owner.vehicles if kind == 2 else {}
            for old_key, thing in table.items():
                if thing.id != thing_id:
                    continue
                if thing.offered_to != player.id or now - thing.offered > 5.0:
                    return                      # not offered to this player (or too long ago)
                mine = player.entities if kind == 1 else player.vehicles
                if key in mine or len(mine) >= (ENT_PER_PLAYER if kind == 1 else VEH_PER_PLAYER):
                    return
                del table[old_key]
                owner.released[(kind, old_key)] = (now, thing_id)
                thing.owner, thing.key, thing.heard, thing.offered_to = player.id, key, now, 0
                mine[key] = thing
                player.released.pop((kind, key), None)
                self.handovers = getattr(self, "handovers", 0) + 1
                self.send(owner.addr, S_RELEASE, struct.pack("<BII", kind, old_key, thing_id))
                return

    def vehicle_name(self, owner, key):
        """One name for a vehicle whichever game asks: (the id of the player whose game simulates it, that game's key)."""
        if owner != 0xFFFF:
            return (owner, key)
        for p in self.players.values():
            for k, v in p.vehicles.items():
                if v.id == key:
                    return (p.id, k)
        return ("street", key)

    def on_vehicle_in(self, player, data, now):
        if len(data) < 8:
            return
        owner, key, seat, seats = struct.unpack_from("<HIBB", data)
        riding = self.__dict__.setdefault("riding", {})   # player id -> (vehicle name, seat, since)
        def answer(result, got=0, reason=0):
            self.send(player.addr, S_VEHICLE_IN, struct.pack("<BHIBB", result, owner, key, got, reason))
        riding.pop(player.id, None)
        if player.state[6] & (FLAG_DEAD | FLAG_DOWN):
            return answer(0, reason=1)
        name = self.vehicle_name(owner, key)
        taken = {}
        for pid, (vehicle, s, since) in list(riding.items()):
            other = self.players.get(pid)
            if other is None or (now - since > 10.0 and not other.state[6] & FLAG_IN_VEHICLE):
                del riding[pid]      # gone, or never got in
            elif vehicle == name:
                taken[s] = (pid, since)
        if seat == 0:
            if 0 in taken and now - taken[0][1] < 3.0:
                return answer(0, reason=2)   # two players at one door: the first has it
            result = 2 if 0 in taken else 1
            if 0 in taken:
                del riding[taken[0][0]]
            got = 0
        else:
            free = [s for s in range(1, max(1, min(seats, 8)) + 1) if s not in taken]
            if not free:
                return answer(0, reason=3)
            got, result = free[0], 1
        riding[player.id] = (name, got, now)
        answer(result, got)

    def on_entity_acts(self, player, data):
        if not data or len(data) < 1 + data[0] * 7:
            return
        out = []
        for i in range(min(data[0], 16)):
            key, target, weapon = struct.unpack_from("<IHB", data, 1 + i * 7)
            entity = player.entities.get(key)
            if entity is not None and target in self.players:
                out.append(struct.pack("<IHB", entity.id, target, weapon))
        if not out:
            return
        body = bytes([len(out)]) + b"".join(out)
        px, py = player.state[0], player.state[1]
        for other in self.players.values():
            if other is not player and (other.state[0] - px) ** 2 + (other.state[1] - py) ** 2 <= 250.0 ** 2:
                self.send(other.addr, S_ENTITY_ACTS, body)

    def on_vehicle_out(self, player):
        self.__dict__.setdefault("riding", {}).pop(player.id, None)

    def take_over(self, player, vehicle_id, now):
        """A player got into a vehicle another game simulates (the copy of it, in their own game). From now on it is
        that player's: the game that had it is told to remove its original."""
        for owner in self.players.values():
            if owner is player:
                continue
            for key, vehicle in list(owner.vehicles.items()):
                if vehicle.id == vehicle_id and not vehicle.player:
                    del owner.vehicles[key]
                    owner.taken[key] = now
                    self.send(owner.addr, S_TAKEN, struct.pack("<I", key))
                    self.say("%s took a vehicle that %s's game was simulating" % (player.name, owner.name))
                    return

    def vehicle_snapshots(self, now):
        players = list(self.players.values())
        for owner in players:
            life = 120.0 if now - owner.state_time > 1.0 else ENT_LIFE   # (a paused game's things wait for it, or for another game to take them)
            for key in [k for k, v in owner.vehicles.items() if now - v.heard > life]:
                del owner.vehicles[key]
        r2 = VEH_RANGE * VEH_RANGE
        for me in players:
            parts = []
            for owner in players:
                if owner is me:
                    continue
                for v in owner.vehicles.values():
                    if (v.x - me.state[0]) ** 2 + (v.y - me.state[1]) ** 2 <= r2:
                        parts.append(struct.pack(VEH_OUT_FORMAT, v.id, v.owner, *v.data))
            for start in range(0, len(parts), 18):
                chunk = parts[start:start + 18]
                self.send(me.addr, S_VEHICLES, bytes([len(chunk)]) + b"".join(chunk))
            if not parts:
                self.send(me.addr, S_VEHICLES, bytes([0]))  # "none": what a game still shows must go

    def entity_snapshots(self, now):
        players = list(self.players.values())
        for owner in players:
            life = 120.0 if now - owner.state_time > 1.0 else ENT_LIFE
            for key in [k for k, e in owner.entities.items() if now - e.heard > life]:
                self.entity_index.pop(owner.entities[key].id, None)
                del owner.entities[key]
        r2 = ENT_RANGE * ENT_RANGE
        for me in players:
            parts = []
            for owner in players:
                if owner is me:
                    continue
                for e in owner.entities.values():
                    if (e.x - me.state[0]) ** 2 + (e.y - me.state[1]) ** 2 <= r2:
                        parts.append(struct.pack(ENT_OUT_FORMAT, e.id, e.owner, e.kind, e.model, e.pedtype, e.x, e.y, e.z, e.heading, e.health, e.interior))
            for start in range(0, len(parts), 40):  # 40 to a datagram; an empty one still says "nothing here"
                chunk = parts[start:start + 40]
                self.send(me.addr, S_ENTITIES, bytes([len(chunk)]) + b"".join(chunk))
            if not parts:
                self.send(me.addr, S_ENTITIES, bytes([0]))

    def on_sync(self, player, body):
        if not 1 <= len(body) <= SYNC_MAX:
            return
        out = struct.pack("<H", player.id) + body
        px, py = player.state[0], player.state[1]
        r2 = self.radius * self.radius
        for other in self.players.values():
            if other is not player and (other.state[0] - px) ** 2 + (other.state[1] - py) ** 2 <= r2:
                self.send(other.addr, S_SYNC, out)

    def snapshots(self, now):
        players = list(self.players.values())
        r2 = self.radius * self.radius
        rides = {}  # vehicle id -> (owner's player id, the owner's own key for it), when somebody rides as a passenger
        if any(p.ride[0] for p in players):
            rides = {v.id: (owner.id, key) for owner in players for key, v in owner.vehicles.items()}
        for me in players:
            mx, my = me.state[0], me.state[1]
            parts = []
            for other in players:
                if other is me:
                    continue
                ox, oy = other.state[0], other.state[1]
                if (ox - mx) ** 2 + (oy - my) ** 2 > r2:
                    continue
                s = other.state
                flags = s[6] | (FLAG_PAUSED if now - other.state_time > 1.0 else 0)
                ride, seat = other.ride
                if ride:
                    owner = rides.get(ride)
                    if owner is None:
                        ride, seat = 0, 0
                    elif owner[0] == me.id:
                        ride, seat = owner[1], seat | 128   # the receiver's own vehicle: by the key its game knows it by
                parts.append(struct.pack(ENTRY_FORMAT, other.id, s[0], s[1], s[2], s[3], s[4], s[5], flags, s[7], s[8], s[9], s[10], ride, seat))
                if len(parts) == 38:  # one datagram stays under the usual MTU; the nearest would be better, later
                    break
            self.send(me.addr, S_SNAPSHOT, struct.pack("<IB", int(now * 1000) & 0xFFFFFFFF, len(parts)) + b"".join(parts))

    # ---------------------------------------------------------------- the loop
    def handle(self, data, addr, now):
        if len(data) < 3 or data[:2] != MAGIC:
            return
        kind, body = data[2], data[3:]
        player = self.by_addr.get(addr)
        if player:
            if now - player.packet_window > 1.0:
                player.packets, player.packet_window = 0, now
            player.packets += 1
            if player.packets > 200:  # ten times what a client sends
                return
            player.last_heard = now
            if kind == C_STATE:
                self.on_state(player, body, now)
            elif kind == C_SYNC:
                self.on_sync(player, body)
            elif kind == C_ENTITY_ACTS:
                self.on_entity_acts(player, body)
            elif kind == C_VEHICLE_IN:
                self.on_vehicle_in(player, body, now)
            elif kind == C_VEHICLE_OUT:
                self.on_vehicle_out(player)
            elif kind == C_ADOPT:
                self.on_adopt(player, body, now)
            elif kind == C_ENTITIES:
                self.on_entities(player, body, now)
            elif kind == C_VEHICLES:
                self.on_vehicles(player, body, now)
            elif kind == C_HIT:
                self.on_hit(player, body, now)
            elif kind == C_REVIVE:
                self.on_revive(player, body, now)
            elif kind == C_ENTITY_HIT:
                self.on_entity_hit(player, body, now)
            elif kind == C_MISSION_ASK and len(body) >= 1:
                self.on_mission_ask(player, body[0], now)
            elif kind == C_MISSION_VOTE and len(body) >= 3:
                self.on_mission_vote(player, struct.unpack_from("<H", body)[0], body[2], now)
            elif kind == C_MISSION_TEXT and len(body) >= 4:
                for other in self.party_of(player):
                    self.send(other.addr, S_MISSION_TEXT, struct.pack("<H", player.id) + body[:4 + body[3]])
            elif kind == C_MISSION_END:
                for other in self.party_of(player):
                    self.send(other.addr, S_MISSION_END, struct.pack("<HB", player.id, body[0] if body else 0))
                player.party = set()
                player.cut_name, player.cut_waiting = b"", set()
                if self.mission_owner == player.id:
                    self.mission_owner = None
            elif kind == C_CUTSCENE and len(body) >= 8:
                self.on_cutscene(player, bytes(body[:8]), body[8] if len(body) > 8 else 0)
            elif kind == C_CUTSCENE_DONE:
                self.cutscene_watched(player)
            elif kind == C_VEHICLE_HIT and len(body) >= 26:
                self.on_vehicle_hit(player, body)
            elif kind == C_MISSION_WORLD and len(body) <= 1300:
                # the mission's blips, markers and pickups, as its game lists them: to the players who joined
                for other in self.party_of(player):
                    self.send(other.addr, S_MISSION_WORLD, struct.pack("<H", player.id) + bytes(body))
            elif kind == C_PLAYER_DATA and 2 <= len(body) <= 1301 and body[0] < 16:
                if player.data.get(body[0]) != bytes(body[1:]):
                    player.data[body[0]] = bytes(body[1:])
                    player.data_dirty = True
            elif kind == C_WORLD_GET and len(body) >= 8:
                version, offset = struct.unpack_from("<II", body)
                if version != self.world_version or offset == 0xFFFFFFFF:
                    self.send(addr, S_WORLD_INFO, self.world_info())
                else:
                    self.send(addr, S_WORLD_CHUNK, struct.pack("<II", version, offset) + self.world[offset:offset + 1200])
            elif kind == C_WORLD_PUT and len(body) >= 17:
                self.on_world_put(player, body, now)
            elif kind == C_WORLD_CHUNK and 4 < len(body) <= 1300:
                self.on_world_chunk(player, body, now)
            elif kind == C_SAVE_ASK:
                self.on_save_ask(player, now)
            elif kind == C_SAVE_VOTE and len(body) >= 3:
                vote = self.save_vote
                if vote and struct.unpack_from("<H", body)[0] == vote["id"]:
                    (vote["yes"] if body[2] else vote["no"]).add(player.id)
                    (vote["no"] if body[2] else vote["yes"]).discard(player.id)
                    self.check_save_vote(now)
            elif kind == C_PLAYER_DATA_GET:
                self.send_player_data(player)
            elif kind == C_MISSION_INVITE and len(body) >= 2:
                self.on_mission_invite(player, struct.unpack_from("<H", body)[0], now)
            elif kind == C_PICKUP_TAKEN and len(body) >= 2:
                for host in self.players.values():  # a player who joined took one of the mission's pickups: its game is told
                    if player.id in host.party:
                        self.send(host.addr, S_PICKUP_TAKEN, bytes(body[:2]) + struct.pack("<H", player.id))
            elif kind == C_PING and len(body) >= 4:
                self.send(addr, S_PONG, body[:4])
            elif kind == C_BYE:
                self.drop(player, "quit")
            elif kind == C_HELLO:
                self.welcome(player)
            elif kind == C_QUERY:
                self.send(addr, S_INFO, self.info_payload())
            return
        if self.throttled(addr[0], now):
            return
        if kind == C_QUERY:
            self.send(addr, S_INFO, self.info_payload())
        elif kind == C_HELLO:
            self.on_hello(addr, body, now)
        elif kind == C_AUTH:
            self.on_auth(addr, body, now)

    def run(self):
        interval = 1.0 / self.tick
        next_tick = time.time() + interval
        while self.running:
            now = time.time()
            wait = max(0.0, min(next_tick - now, 0.05))
            try:
                ready, _, _ = select.select([self.sock], [], [], wait)
            except (OSError, ValueError):
                break
            now = time.time()
            if ready:
                with self.lock:
                    for _ in range(512):
                        try:
                            data, addr = self.sock.recvfrom(2048)
                        except (BlockingIOError, InterruptedError):
                            break
                        except ConnectionResetError:
                            continue  # Windows reports a closed port of some client here
                        except OSError:
                            break
                        self.received += 1
                        self.handle(data, addr, now)
            if now >= next_tick:
                next_tick += interval
                if next_tick < now:
                    next_tick = now + interval
                with self.lock:
                    write = now - self.data_written > 10.0   # what has changed goes to disk every ten seconds
                    if write:
                        self.data_written = now
                    for player in list(self.players.values()):
                        if write:
                            self.save_player_data(player)
                        if now - player.last_heard > self.timeout:
                            self.drop(player, "timed out")
                    self.snapshots(now)
                    self.check_save_vote(now)
                    if self.autosave and self.players and now - self.autosave_at > self.autosave:
                        self.autosave_at = now   # "whoever is free: send the world as it is now" (3 = timer; no vote)
                        for p in self.players.values():
                            self.send(p.addr, S_SAVE_RESULT, struct.pack("<HBH", 0, 3, 0))
                    for vote_id in [v for v, vote in self.votes.items() if now >= vote["deadline"]]:
                        self.check_vote(vote_id, now)
                    self.roles(now)
                    self.tick_count = getattr(self, "tick_count", 0) + 1
                    if self.tick_count % 2 == 0:  # the world goes out 10 times a second
                        self.handover(now)
                        self.entity_snapshots(now)
                        self.vehicle_snapshots(now)
                    if len(self.unknown) > 10000:
                        self.unknown.clear()
        with self.lock:
            for player in list(self.players.values()):
                self.drop(player, "server stopped", "the server was stopped")
        self.sock.close()

    # ---------------------------------------------------------------- the console
    def command(self, line):
        parts = line.strip().split(None, 1)
        if not parts:
            return
        cmd, arg = parts[0].lower(), parts[1].strip() if len(parts) > 1 else ""
        with self.lock:
            if cmd in ("players", "list", "who"):
                if not self.players:
                    print("nobody is connected")
                now = time.time()
                for p in sorted(self.players.values(), key=lambda p: p.id):
                    s = p.state
                    print("%4d  %-24s %-21s at %8.1f %8.1f %6.1f  health %3d  %s  online %s  %s" % (
                        p.id, p.name, "%s:%d" % p.addr, s[0], s[1], s[2], s[5] // 10,
                        "in a vehicle" if s[6] & FLAG_IN_VEHICLE else "on foot     ", duration(now - p.joined),
                        "simulates %d pedestrians, %d vehicles" % (len(p.entities), len(p.vehicles)) if p.populates else "shows another player's world"))
            elif cmd == "status":
                print("%s: %d of %d players, port %d, %d ticks a second, games at %d frames a second, password %s, anti-cheat %s, up %s, %d packets in, %d out" % (
                    self.name, len(self.players), self.max_players, self.port, self.tick, self.fps, "set" if self.password else "none",
                    "on" if self.anticheat else "off", duration(time.time() - self.started), self.received, self.sent))
                owner = self.players.get(self.mission_owner) if self.mission_owner is not None else None
                print("mission: %s" % ("number %d, run by %s's game, %d more players in it" % (self.mission_number, owner.name, len(owner.party)) if owner else "none running"))
            elif cmd == "kick":
                target = self.find(arg)
                if target:
                    self.drop(target, "kicked", "kicked by the server")
                else:
                    print("no such player: give the number or the name from 'players'")
            elif cmd == "anticheat":
                if arg.lower() in ("on", "off"):
                    self.anticheat = arg.lower() == "on"
                print("anti-cheat is %s" % ("on" if self.anticheat else "off"))
            elif cmd == "password":
                self.password = "" if arg in ("", "none", "off") else arg
                print("password %s (players already connected stay)" % ("set" if self.password else "removed"))
            elif cmd in ("quit", "stop", "exit"):
                self.running = False
            elif cmd in ("help", "?"):
                print("players | status | kick <number or name> | anticheat on|off | password <text>|none | quit")
            else:
                print("unknown command, try 'help'")
        sys.stdout.flush()

    def find(self, text):
        if text.isdigit() and int(text) in self.players:
            return self.players[int(text)]
        for p in self.players.values():
            if p.name.lower() == text.lower():
                return p
        return None

    def console(self):
        """Reads commands while a terminal is attached; without one (nohup, a service) the server just runs."""
        try:
            for line in sys.stdin:
                self.command(line)
                if not self.running:
                    break
        except (OSError, ValueError):
            pass


def duration(seconds):
    seconds = int(seconds)
    return "%d:%02d:%02d" % (seconds // 3600, seconds // 60 % 60, seconds % 60)


def local_addresses():
    found = []
    try:  # the address this machine uses to reach the outside (no packet is sent)
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("192.0.2.1", 9))
        found.append(s.getsockname()[0])
        s.close()
    except OSError:
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            if info[4][0] not in found and not info[4][0].startswith("127."):
                found.append(info[4][0])
    except OSError:
        pass
    return found or ["127.0.0.1"]


def public_address():
    for url in ("https://api.ipify.org", "https://checkip.amazonaws.com"):
        try:
            with urllib.request.urlopen(url, timeout=3) as response:
                text = response.read(64).decode("ascii", "replace").strip()
                socket.inet_aton(text)
                return text
        except Exception:
            continue
    return None


def self_check(port):
    """Asks the server over the loopback, like a client's server list would: proves the port is bound and answering."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.settimeout(2.0)
    try:
        s.sendto(MAGIC + bytes([C_QUERY]), ("127.0.0.1", port))
        data, _ = s.recvfrom(256)
        return data[:3] == MAGIC + bytes([S_INFO])
    except OSError:
        return False
    finally:
        s.close()


def main():
    parser = argparse.ArgumentParser(description="Ultimate Multiplayerator server")
    parser.add_argument("--port", type=int, default=7777, help="UDP port (default 7777)")
    parser.add_argument("--bind", default="0.0.0.0", help="address to listen on (default: all)")
    parser.add_argument("--name", default="Ultimate Multiplayerator server")
    parser.add_argument("--save", default="", help="the saved game that is the world of this server (a GTA San Andreas save file, e.g. GTASAsf1.b); it is read at the start and replaced when the players save. Not given or not there: a new game, kept in the data folder")
    parser.add_argument("--autosave", type=float, default=10.0, help="minutes between automatic saves (0 = never)")
    parser.add_argument("--data", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "server_data"), help="folder for what the server keeps between sessions (players' items, looks, statistics)")
    parser.add_argument("--password", default=os.environ.get("UM_PASSWORD", ""), help="players must give this to join (or set UM_PASSWORD)")
    parser.add_argument("--max-players", type=int, default=100)
    parser.add_argument("--tick", type=int, default=20, help="snapshots a second (default 20)")
    parser.add_argument("--fps", type=int, default=60, choices=(25, 30, 60), help="frames a second every player's game simulates at (default 60; needs SilentPatch and FramerateVigilante in the game, as in single player)")
    parser.add_argument("--radius", type=float, default=300.0, help="players farther apart than this are not sent to each other")
    parser.add_argument("--anticheat", action="store_true", help="start with the checks on (movement, hits). Off by default: story missions move players in ways the movement check counts as cheating")
    parser.add_argument("--no-anticheat", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--no-public-ip", action="store_true", help="do not ask an outside service for this machine's public address")
    args = parser.parse_args()

    try:
        server = Server(args)
    except OSError as error:
        print("Cannot open UDP port %d: %s" % (args.port, error))
        print("Another program (or another copy of this server) is using it, or the address is not this machine's.")
        return 1
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    print("Ultimate Multiplayerator server, protocol %d" % PROTOCOL)
    print("  name:        %s" % server.name)
    print("  UDP port:    %d  %s" % (server.port, "open and answering" if self_check(server.port) else "BOUND BUT NOT ANSWERING (a local firewall?)"))
    print("  password:    %s" % ("required" if server.password else "none"))
    print("  anti-cheat:  %s" % ("on" if server.anticheat else "off"))
    print("  players:     up to %d, %d snapshots a second, %d m around each player" % (server.max_players, server.tick, server.radius))
    for address in local_addresses():
        print("  this machine: %s:%d" % (address, server.port))
    if not args.no_public_ip:
        public = public_address()
        print("  public address: %s" % ("%s:%d  (give this to the players)" % (public, server.port) if public else "could not be found out from here"))
    print("  From outside, UDP port %d must also be allowed in the firewall / the VPS panel (Ubuntu: sudo ufw allow %d/udp)." % (server.port, server.port))
    print("  Commands: players, status, kick, anticheat on|off, password, quit")
    sys.stdout.flush()

    try:
        server.console()
        while server.running and thread.is_alive():  # no terminal: run until stopped
            time.sleep(0.5)
    except KeyboardInterrupt:
        pass
    server.running = False
    thread.join(3.0)
    print("server stopped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
