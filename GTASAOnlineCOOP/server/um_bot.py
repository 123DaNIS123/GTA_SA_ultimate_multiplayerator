#!/usr/bin/env python3
"""A scripted player for the Ultimate Multiplayerator server: connects like the game does and walks in a circle.
Used by the tests and to see another player in the game without a second PC.
  python um_bot.py [host] [port] [--name Bot] [--password x] [--centre x y z] [--radius 4] [--seconds 60]"""
import argparse
import hashlib
import math
import socket
import struct
import sys
import time

MAGIC = b"UM"
C_QUERY, C_HELLO, C_AUTH, C_STATE, C_BYE, C_PING, C_ENTITIES, C_VEHICLES, C_HIT, C_REVIVE, C_ENTITY_HIT, C_MISSION_ASK, C_MISSION_VOTE, C_MISSION_TEXT, C_MISSION_END, C_CUTSCENE, C_CUTSCENE_DONE, C_VEHICLE_HIT, C_MISSION_WORLD, C_PICKUP_TAKEN, C_MISSION_INVITE, C_PLAYER_DATA, C_PLAYER_DATA_GET, C_WORLD_GET, C_WORLD_PUT, C_WORLD_CHUNK, C_SAVE_ASK, C_SAVE_VOTE = 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 24, 25, 26, 27, 28
S_INFO, S_CHALLENGE, S_WELCOME, S_REJECT, S_SNAPSHOT, S_JOINED, S_LEFT, S_PONG, S_CORRECT, S_KICK, S_ENTITIES, S_ROLE, S_VEHICLES, S_TAKEN, S_DAMAGE, S_REVIVED, S_ENTITY_DAMAGE, S_MISSION_ASK, S_MISSION_RESULT, S_MISSION_TEXT, S_MISSION_END, S_CUTSCENE, S_CUTSCENE_FREE, S_VEHICLE_HIT, S_MISSION_WORLD, S_PICKUP_TAKEN, S_PARTY, S_PLAYER_DATA, S_WORLD_INFO, S_WORLD_CHUNK, S_WORLD_PUT, S_SAVE_ASK, S_SAVE_RESULT = 65, 66, 67, 68, 69, 70, 71, 72, 73, 74, 75, 76, 77, 78, 79, 80, 81, 82, 83, 84, 85, 86, 87, 88, 89, 90, 91, 92, 93, 94, 95, 96, 97
VEH_IN_FORMAT = "<IHfff3b3b3hHBBHBBBIB20sHBHBHB"
VEH_OUT_FORMAT = "<IHHfff3b3b3hHBBHBHBBB20sHBHBHB"
VEH_OUT_SIZE = struct.calcsize(VEH_OUT_FORMAT)
ENT_IN_FORMAT = "<IBHBffffHB"
ENT_OUT_FORMAT = "<IHBHBffffHB"
ENT_OUT_SIZE = struct.calcsize(ENT_OUT_FORMAT)
STATE_FORMAT = "<IfffffHBHBBbIB"
ENTRY_FORMAT = "<HfffffHBHBBbIB"
ENTRY_SIZE = struct.calcsize(ENTRY_FORMAT)


class Client:
    """The client side of the protocol, enough for tests."""

    def __init__(self, host, port, name, password=""):
        self.addr = (host, port)
        self.name = name
        self.password = password
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.wait = 0.05
        self.sock.settimeout(0.05)
        self.id = None
        self.spawn = None
        self.rejected = None
        self.kicked = None
        self.sequence = 0
        self.names = {}       # id -> name
        self.others = {}      # id -> (x, y, z, heading, speed, health, flags, vehicle, interior), from the last snapshot
        self.corrections = []
        self.snapshots = 0
        self.populates = None  # what the server last said: this client makes the pedestrians around it
        self.entities = {}     # id -> (owner, kind, model, pedtype, x, y, z, heading, health, interior), from the last world packets
        self.entity_packets = 0
        self.asks = []         # (vote id, host, mission): questions from the server
        self.results = []      # (vote id, host, mission, accepted, own answer)
        self.texts = []        # (host, style, time ms, text) of the mission this client joined
        self.mission_ends = [] # (host, passed)
        self.cutscenes = []    # (host, name): cutscenes of a joined mission
        self.world_info = None # (version, size, crc, script) of the server's world save
        self.world_chunks = {} # offset -> bytes
        self.world_put_replies = []    # (state 0 refused / 1 go on / 2 taken, offset or version)
        self.save_asks = []    # (vote, by)
        self.save_results = [] # (vote, accepted, by)
        self.saved = {}        # part -> bytes: this player's data as the server kept it
        self.saved_all = None  # how many parts the server says it has
        self.party = None      # ids of the players in this client's own mission, as the server says
        self.vehicle_hits = [] # (own key, health lost, damage state, from player)
        self.worlds = []       # (host, blob): a joined mission's blips, markers and pickups
        self.pickups_taken = []  # (pickup, by player)
        self.cutscene_area = 0 # the interior of the last cutscene announced
        self.cutscene_free = 0 # times this client's own mission was told to go on after a cutscene
        self.auto_vote = None  # answer every question with this (0 reject, 1 join and stay, 2 join and come along)
        self.entity_damage = [] # (own key, health points, weapon, from player): what others did to this client's pedestrians
        self.revived_by = []   # players who brought this one back up
        self.damage = []       # (from player, health points, weapon) the server passed on
        self.taken = []        # keys of own vehicles the server said another player took
        self.vehicles = {}     # id -> (owner, model, x, y, z, forward x3, right x3, velocity x3, health, colours x2, driver model, driver type, player in it, interior)

    def send(self, kind, payload=b""):
        self.sock.sendto(MAGIC + bytes([kind]) + payload, self.addr)

    def query(self):
        self.send(C_QUERY)
        try:
            data, _ = self.sock.recvfrom(2048)
        except OSError:
            return None
        if data[:3] != MAGIC + bytes([S_INFO]):
            return None
        version, players, limit, password, n = struct.unpack_from("<BHHBB", data, 3)
        return {"version": version, "players": players, "max": limit, "password": bool(password), "name": data[10:10 + n].decode("utf-8", "replace")}

    def connect(self, timeout=3.0):
        name = self.name.encode("utf-8")
        deadline = time.time() + timeout
        last = 0.0
        while time.time() < deadline and self.id is None and self.rejected is None:
            if time.time() - last > 0.5:
                self.send(C_HELLO, bytes([15, len(name)]) + name)
                last = time.time()
            self.pump()
        return self.id is not None

    def pump(self):
        """Reads what has arrived."""
        began = time.time()
        while True:
            if time.time() - began > 0.1:   # (a steady stream must not keep the caller here: it has its own things to send)
                return
            try:
                data, _ = self.sock.recvfrom(4096)
            except OSError:
                return
            if len(data) < 3 or data[:2] != MAGIC:
                continue
            kind, body = data[2], data[3:]
            if kind == S_CHALLENGE:
                self.send(C_AUTH, hashlib.sha256(body[:16] + self.password.encode("utf-8")).digest())
            elif kind == S_WELCOME:
                self.id, tick, x, y, z, heading, anticheat, n = struct.unpack_from("<HBffffBB", body)
                self.spawn = (x, y, z, heading)
                self.name = body[21:21 + n].decode("utf-8", "replace")
            elif kind == S_REJECT:
                self.rejected = body[0]
            elif kind == S_JOINED:
                pid, n = struct.unpack_from("<HB", body)
                self.names[pid] = body[3:3 + n].decode("utf-8", "replace")
            elif kind == S_LEFT:
                pid = struct.unpack_from("<H", body)[0]
                self.names.pop(pid, None)
                self.others.pop(pid, None)
            elif kind == S_SNAPSHOT:
                _, count = struct.unpack_from("<IB", body)
                self.snapshots += 1
                self.others = {}
                for i in range(count):
                    e = struct.unpack_from(ENTRY_FORMAT, body, 5 + i * ENTRY_SIZE)
                    self.others[e[0]] = e[1:]
            elif kind == S_VEHICLES:
                fresh = {}
                for i in range(body[0]):
                    v = struct.unpack_from(VEH_OUT_FORMAT, body, 1 + i * VEH_OUT_SIZE)
                    fresh[v[0]] = v[1:]
                self.vehicles = fresh if body[0] < 18 else {**self.vehicles, **fresh}
            elif kind == S_MISSION_ASK:
                vote, host, mission = struct.unpack_from("<HHB", body)
                self.asks.append((vote, host, mission))
                if self.auto_vote is not None:
                    self.send(C_MISSION_VOTE, struct.pack("<HB", vote, self.auto_vote))
            elif kind == S_MISSION_RESULT:
                self.results.append(struct.unpack_from("<HHBBB", body))
            elif kind == S_MISSION_TEXT:
                host, style, time_ms, n = struct.unpack_from("<HBHB", body)
                self.texts.append((host, style, time_ms, body[6:6 + n].decode("latin-1")))
            elif kind == S_CUTSCENE:
                host = struct.unpack_from("<H", body)[0]
                self.cutscenes.append((host, bytes(body[2:10]).rstrip(b"\0").decode("latin-1")))
                self.cutscene_area = body[10] if len(body) > 10 else 0
            elif kind == S_WORLD_INFO:
                self.world_info = struct.unpack_from("<IIII", body)
            elif kind == S_WORLD_CHUNK:
                self.world_chunks[struct.unpack_from("<II", body)[1]] = bytes(body[8:])
            elif kind == S_WORLD_PUT:
                self.world_put_replies.append(struct.unpack_from("<BI", body))
            elif kind == S_SAVE_ASK:
                self.save_asks.append(struct.unpack_from("<HH", body))
            elif kind == S_SAVE_RESULT:
                self.save_results.append(struct.unpack_from("<HBH", body))
            elif kind == S_PLAYER_DATA:
                if body[0] == 255:
                    self.saved_all = body[1]
                else:
                    self.saved[body[0]] = bytes(body[1:])
            elif kind == S_PARTY:
                self.party = [struct.unpack_from("<H", body, 1 + i * 2)[0] for i in range(body[0])]
            elif kind == S_VEHICLE_HIT:
                key, loss = struct.unpack_from("<IH", body)
                self.vehicle_hits.append((key, loss, bytes(body[6:26]), struct.unpack_from("<H", body, 26)[0]))
            elif kind == S_MISSION_WORLD:
                self.worlds.append((struct.unpack_from("<H", body)[0], bytes(body[2:])))
            elif kind == S_PICKUP_TAKEN:
                self.pickups_taken.append(struct.unpack_from("<HH", body))
            elif kind == S_CUTSCENE_FREE:
                self.cutscene_free += 1
            elif kind == S_MISSION_END:
                self.mission_ends.append(struct.unpack_from("<HB", body))
            elif kind == S_ENTITY_DAMAGE:
                key, amount, weapon, who = struct.unpack_from("<IHBH", body)
                self.entity_damage.append((key, amount / 10.0, weapon, who))
            elif kind == S_REVIVED:
                self.revived_by.append(struct.unpack_from("<H", body)[0])
            elif kind == S_DAMAGE:
                who, amount, weapon = struct.unpack_from("<HHB", body)
                self.damage.append((who, amount / 10.0, weapon))
            elif kind == S_TAKEN:
                self.taken.append(struct.unpack_from("<I", body)[0])
            elif kind == S_ROLE:
                self.populates = bool(body[0])
            elif kind == S_ENTITIES:
                self.entity_packets += 1
                fresh = {}
                for i in range(body[0]):
                    e = struct.unpack_from(ENT_OUT_FORMAT, body, 1 + i * ENT_OUT_SIZE)
                    fresh[e[0]] = e[1:]
                self.entities = fresh if body[0] < 40 else {**self.entities, **fresh}
            elif kind == S_CORRECT:
                self.corrections.append(struct.unpack_from("<ffff", body))
            elif kind == 98 and len(body) == 137:
                # client version 2: "I have got into your vehicle" (the last two fields of another player's state)
                owner, key = struct.unpack_from("<HI", body, 131)
                if owner == self.id and key == 1 and not getattr(self, "car_taken", False):
                    self.car_taken = True
                    print("player %d took this bot's car: on foot now" % struct.unpack_from("<H", body)[0])
            elif kind == S_KICK:
                self.kicked = body[1:1 + body[0]].decode("utf-8", "replace")

    def state(self, x, y, z, heading=0.0, speed=0.0, health=100.0, flags=0, vehicle=0, interior=0, weapon=0, aim=0, ride=0, seat=0):
        self.sequence += 1
        self.send(C_STATE, struct.pack(STATE_FORMAT, self.sequence, x, y, z, heading, speed, int(health * 10), flags, vehicle, interior, weapon, aim, ride, seat))
        if vehicle and getattr(self, "car_taken", False):   # (its car was taken: it stands beside where it was)
            vehicle, x, speed = 0, x - 3.0, 0.0
        if not vehicle:
            self.sync(x, y, z, heading, speed, health, flags, interior, weapon, aim)
        else:
            self.sync_vehicle(x, y, z, heading, speed, health, vehicle, interior)

    def sync_vehicle(self, x, y, z, heading, speed, health, model, interior):
        """Client version 2: the bot at the wheel of a vehicle its own "game" simulates (handle 1)."""
        import math
        now = time.time()
        before = getattr(self, "sync_before", None)
        self.sync_before = (now, x, y)
        vx = vy = 0.0
        if before and now - before[0] > 0.001 and (abs(x - before[1]) + abs(y - before[2])) > 0.0005:
            vx, vy = (x - before[1]) / (now - before[0]), (y - before[2]) / (now - before[0])
            heading = math.atan2(-vx, vy)
        fx, fy = -math.sin(heading), math.cos(heading)
        turn = 0.0
        last = getattr(self, "sync_heading", None)
        if last is not None and before and now - before[0] > 0.001:
            d = (heading - last + math.pi) % (2 * math.pi) - math.pi
            turn = d / (now - before[0]) / 50.0
        self.sync_heading = heading
        self.sync_sequence = (getattr(self, "sync_sequence", 0) + 1) & 0xFFFF
        self.send(29, struct.pack(self.SYNC_FORMAT, 2, 2, self.sync_sequence, x, y, z, vx / 50.0, vy / 50.0, 0.0, heading,
                                  int(health * 10), 0, 0, 0, interior, 0, 0, 0, 0, 0, 0,
                                  fx, fy, 0.0, x - fx * 6.0, y - fy * 6.0, z + 2.0, math.atan2(fy, fx), 18, 70, getattr(self, "skin", 0),
                                  0, 1, 0, model, fy, -fx, 0.0, fx, fy, 0.0, 0.0, 0.0, turn, 1000, 3, 1, 0, 0))

    # Client version 2: the detailed state the games play each other's characters from (see net/PROTOCOL.md). The
    # bot "holds the stick forward" while it moves, with its camera looking where it walks, and holds aim / fire when
    # its state says so.
    SYNC_FORMAT = "<BBH3f3ffHBBHBB4bI3f3ffBBHHIBH3f3f3fH2BHI"

    def sync(self, x, y, z, heading, speed, health, flags, interior, weapon, aim):
        import math
        now = time.time()
        before = getattr(self, "sync_before", None)
        self.sync_before = (now, x, y)
        if before and now - before[0] > 0.001 and (abs(x - before[1]) + abs(y - before[2])) > 0.0005:
            vx, vy = (x - before[1]) / (now - before[0]), (y - before[2]) / (now - before[0])
            speed = math.hypot(vx, vy)
            heading = math.atan2(-vx, vy)   # it faces the way it goes
        fx, fy = -math.sin(heading), math.cos(heading)
        up = max(-1.0, min(1.0, aim / 127.0))
        flat = math.sqrt(max(0.0, 1.0 - up * up))
        source = (x - fx * 3.0, y - fy * 3.0, z + 1.0)
        target = getattr(self, "aim_at", None)
        if target:   # aims at a place (another player): the camera right behind the gun, looking there
            dx, dy, dz = target[0] - x, target[1] - y, target[2] - (z + 0.5)
            far = math.sqrt(dx * dx + dy * dy + dz * dz) or 1.0
            fx, fy, up = dx / far, dy / far, dz / far
            flat = 1.0
            heading = math.atan2(-fx, fy)
            source = (x - fx * 0.3, y - fy * 0.3, z + 0.5)
        aiming, firing = bool(flags & 8), bool(flags & 16)
        buttons = (4 if aiming or firing else 0) | (1 << 13 if firing else 0)   # R1 = pad slot 6, circle = slot 17
        if speed < 2.5:
            buttons |= 1 << 17                                                  # the "walk" key (pad slot 21): not running
        moving = speed > 0.3 and not (flags & 2)
        self.sync_sequence = (getattr(self, "sync_sequence", 0) + 1) & 0xFFFF
        self.send(29, struct.pack(self.SYNC_FORMAT, 2, 1, self.sync_sequence, x, y, z, fx * speed / 50.0, fy * speed / 50.0, 0.0, heading,
                                  int(health * 10), 0, weapon, 30, interior, (1 if flags & 4 else 0) | (2 if flags & 2 else 0) | (4 if aiming or firing else 0),
                                  0, -127 if moving else 0, 0, 0, buttons,
                                  fx * flat, fy * flat, up, source[0], source[1], source[2], math.atan2(fy, fx), 53 if aiming or firing else 4, 70, getattr(self, "skin", 0),
                                  0, 0, 0, 0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0, 0, 0, 0, 0))

    def ask_mission(self, mission):
        self.send(C_MISSION_ASK, bytes([mission]))

    def mission_text(self, text, style=0, time_ms=4000):
        data = text.encode("latin-1")[:200]
        self.send(C_MISSION_TEXT, struct.pack("<BHB", style, time_ms, len(data)) + data)

    def cutscene(self, name, area=0):
        self.send(C_CUTSCENE, name.encode("latin-1")[:8].ljust(8, b"\0") + bytes([area]))

    def vehicle_hit(self, vehicle_id, loss, damage=bytes(20)):
        self.send(C_VEHICLE_HIT, struct.pack("<IH", vehicle_id, loss) + damage)

    def mission_world(self, blips=(), markers=(), pickups=()):
        """blips: (x, y, z, colour, sprite, size, display); markers: (x, y, z, size, r, g, b, a, type); pickups: (ref, model, x, y, z, type)."""
        body = bytes([len(blips)]) + b"".join(struct.pack("<fffIBBB", *b) for b in blips)
        body += bytes([len(markers)]) + b"".join(struct.pack("<ffffBBBBB", *m) for m in markers)
        body += bytes([len(pickups)]) + b"".join(struct.pack("<HHfffB", *k) for k in pickups)
        self.send(C_MISSION_WORLD, body)

    def world_get(self, version, offset):
        self.send(C_WORLD_GET, struct.pack("<II", version, offset))

    def world_put(self, base, data, script=0, reason=2):
        import zlib
        self.send(C_WORLD_PUT, struct.pack("<IIIIB", base, len(data), zlib.crc32(data) & 0xFFFFFFFF, script, reason))

    def world_chunk(self, offset, data):
        self.send(C_WORLD_CHUNK, struct.pack("<I", offset) + data)

    def save_ask(self):
        self.send(C_SAVE_ASK, b"")

    def save_vote(self, vote, yes):
        self.send(C_SAVE_VOTE, struct.pack("<HB", vote, 1 if yes else 0))

    def player_data(self, part, body):
        self.send(C_PLAYER_DATA, bytes([part]) + body)

    def get_player_data(self):
        self.send(C_PLAYER_DATA_GET, b"")

    def invite(self, player_id):
        self.send(C_MISSION_INVITE, struct.pack("<H", player_id))

    def pickup_taken(self, ref):
        self.send(C_PICKUP_TAKEN, struct.pack("<H", ref))

    def cutscene_done(self):
        self.send(C_CUTSCENE_DONE, b"")

    def mission_end(self, passed):
        self.send(C_MISSION_END, bytes([1 if passed else 0]))

    def hit_entity(self, entity_id, damage, weapon):
        self.send(C_ENTITY_HIT, struct.pack("<IHB", entity_id, int(damage * 10), weapon))

    def revive(self, target):
        self.send(C_REVIVE, struct.pack("<H", target))

    def hit(self, target, damage, weapon):
        """This client's player hurt player `target` by `damage` health points with `weapon`."""
        self.send(C_HIT, struct.pack("<HHB", target, int(damage * 10), weapon))

    def report(self, entities):
        """entities: list of (key, model, pedtype, x, y, z, heading, health, interior): the pedestrians this client simulates."""
        body = bytes([len(entities)])
        for entry in entities:
            key, model, pedtype, x, y, z, heading, health, interior = entry[:9]
            kind = entry[9] if len(entry) > 9 else 1   # 3 = a mission's character
            body += struct.pack(ENT_IN_FORMAT, key, kind, model, pedtype, x, y, z, heading, int(health * 10), interior)
        self.send(C_ENTITIES, body)

    def report_vehicles(self, vehicles):
        """vehicles: list of (key, model, x, y, z, heading, speed m/s, health, mine, driver model, driver type[, took,
        state bits (1 engine, 2 lights, 4 siren), damage (20 bytes), passengers [(model, ped type)] ])."""
        body = bytes([len(vehicles)])
        for entry in vehicles:
            key, model, x, y, z, heading, speed, health, mine, driver_model, driver_type = entry[:11]
            took = entry[11] if len(entry) > 11 else 0
            lights = entry[12] if len(entry) > 12 else 1
            damage = entry[13] if len(entry) > 13 else bytes(20)
            riders = [n for rider in (list(entry[14]) if len(entry) > 14 else []) for n in rider]
            riders = (riders + [0] * 6)[:6]
            fx, fy = -math.sin(heading), math.cos(heading)   # the game's heading: 0 = north (+y), counter-clockwise
            body += struct.pack(VEH_IN_FORMAT, key, model, x, y, z, int(fx * 127), int(fy * 127), 0, int(fy * 127), int(-fx * 127), 0,
                                int(fx * speed * 100), int(fy * speed * 100), 0, int(health), 1, 3, driver_model, driver_type, int(mine), 0, took, lights, damage, *riders)
        self.send(C_VEHICLES, body)

    def bye(self):
        self.send(C_BYE)
        self.sock.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("host", nargs="?", default="127.0.0.1")
    parser.add_argument("port", nargs="?", type=int, default=7777)
    parser.add_argument("--name", default="Bot")
    parser.add_argument("--password", default="")
    parser.add_argument("--centre", nargs=3, type=float, default=None)
    parser.add_argument("--radius", type=float, default=4.0)
    parser.add_argument("--seconds", type=float, default=60.0)
    parser.add_argument("--vote", type=int, default=None, help="answer every mission question: 0 reject, 1 join and stay, 2 join and come along")
    parser.add_argument("--host-mission", type=int, default=None, help="after --host-delay seconds, ask to start this mission")
    parser.add_argument("--host-cutscene", default=None, help="when the mission was accepted, show this cutscene to the players who joined")
    parser.add_argument("--host-world", action="store_true", help="the hosted mission has a blip 30 m east, a red marker 6 m east and a pickup (body armour) 4 m east, 8 m south of the centre")
    parser.add_argument("--host-invite", action="store_true", help="while the hosted mission runs, every player who is not in it is asked to join")
    parser.add_argument("--host-area", type=int, default=0, help="the interior that cutscene plays in")
    parser.add_argument("--host-delay", type=float, default=60.0)
    parser.add_argument("--watch", type=float, default=0.0, help="seconds this player takes to watch a cutscene of a joined mission")
    parser.add_argument("--join-delay", type=float, default=0.0, help="wait this long before joining (to get a higher player number than the game)")
    parser.add_argument("--hurt-peds", action="store_true", help="every two seconds, take 40 health off one of the pedestrians another game simulates")
    parser.add_argument("--mission-peds", type=int, default=0, help="characters 'made by a mission' this bot simulates, standing in a row; it prints the damage others do to them")
    parser.add_argument("--parked", type=int, default=0, help="parked cars this bot simulates, in a row")
    parser.add_argument("--peds", type=int, default=0, help="pedestrians this bot simulates, walking a wider circle")
    parser.add_argument("--cars", type=int, default=0, help="cars with drivers this bot simulates, driving a circle of 18 m")
    parser.add_argument("--at-players", action="store_true", help="with --fire: aims at the first other player it sees (client version 2)")
    parser.add_argument("--skin", type=int, default=0, help="the character model the bot reports (client version 2; 0 = CJ)")
    parser.add_argument("--weapon", type=int, default=0, help="the weapon the bot holds (30 = AK-47)")
    parser.add_argument("--down", type=float, default=0.0, help="the bot stands at the centre and lies knocked down from this second on, until somebody revives it")
    parser.add_argument("--helper", action="store_true", help="the bot stands at the centre and revives any knocked-down player beside it after 7 s")
    parser.add_argument("--stand", action="store_true", help="the bot stands at the centre, facing --heading, aiming (--aim) its weapon")
    parser.add_argument("--heading", type=float, default=0.0, help="radians, 0 = north, counter-clockwise")
    parser.add_argument("--aim", type=int, default=0, help="-127 (straight down) .. 127 (straight up)")
    parser.add_argument("--sweep", action="store_true", help="with --stand: the aim swings between -aim and +aim and the bot turns slowly")
    parser.add_argument("--fire", action="store_true", help="the bot stands still, aims and fires its weapon")
    parser.add_argument("--attack", type=float, default=0.0, help="health points the bot takes off each player it sees, 5 points at a time")
    parser.add_argument("--attack-delay", type=float, default=0.0, help="seconds after a player first stands within 4 m before the attack starts")
    parser.add_argument("--only", default="", help="attack and help only the player with this name")
    parser.add_argument("--sit", action="store_true", help="the bot sits at the wheel of a standing car at the centre (facing --heading)")
    parser.add_argument("--dents", action="store_true", help="that car has a flat tyre, no bonnet, a broken light and bumper, lights and siren on")
    parser.add_argument("--riders", type=int, default=0, help="passengers (not players) in that car, from the back seat on")
    parser.add_argument("--car-model", type=int, default=596, help="its model (596 = police car)")
    parser.add_argument("--drive", action="store_true", help="the bot itself sits in a car (driving a circle of 12 m) instead of walking")
    args = parser.parse_args()

    time.sleep(args.join_delay)
    bot = Client(args.host, args.port, args.name, args.password)
    bot.skin = args.skin
    if not bot.connect(30.0):
        print("could not join: %s" % ("rejected, reason %d" % bot.rejected if bot.rejected else "no answer"))
        return 1
    bot.auto_vote = args.vote
    cx, cy, cz = args.centre if args.centre else bot.spawn[:3]
    print("joined as player %d (%s); walking a circle of %.0f m around %.1f %.1f %.1f" % (bot.id, bot.name, args.radius, cx, cy, cz))
    start = time.time()
    seen = set()
    dealt = {}
    helping = {}
    near_since = {}
    hurt = {}
    watching, watch_until = None, None
    rides = set()
    host_done = 0.0
    world_last, world_taken = 0.0, 0
    invite_last, party_seen = 0.0, None
    host_start, host_state, host_last = time.time(), 0, 0.0   # 0 waiting, 1 asking, 2 showing the cutscene, 3 done
    last_hurt = 0.0
    last_attack = 0.0
    announced = []
    while time.time() - start < args.seconds and bot.kicked is None:
        t = time.time() - start
        if t - getattr(bot, "street_said", -10.0) >= 3.0:   # the shared street, as this player gets it
            bot.street_said = t
            print("street at %3.0f s: this bot makes it: %s; receives %d pedestrians, %d vehicles" % (
                t, bot.populates, len(getattr(bot, "entities", {})), len(getattr(bot, "vehicles", {}))))
        angle = t * 1.4 / args.radius  # walking pace
        x, y = cx + math.cos(angle) * args.radius, cy + math.sin(angle) * args.radius
        cars = []
        if args.sit:
            dents = bytes([0, 1, 0, 0, 0, 4, 0, 0, 0, 0, 0, 0, 1, 0, 0, 0, 0, 0, 0x30, 0]) if args.dents else bytes(20)
            riders = [(0, 0)] + [(105 + i, 8) for i in range(args.riders)]
            bot.state(cx, cy, cz, heading=args.heading, speed=0.0, flags=1, vehicle=args.car_model)
            cars.append((1, args.car_model, cx, cy, cz, args.heading, 0.0, 1000, True, 0, 0, 0, 7 if args.dents else 1, dents, riders))
            for pid, other in bot.others.items():
                if other[11] and (pid, other[11], other[12]) not in rides:
                    rides.add((pid, other[11], other[12]))
                    print("player %d rides as a passenger: vehicle %d, seat %d%s" % (pid, other[11], other[12] & 7, " (this bot's own car)" if other[12] & 128 else ""))
        elif args.drive:
            angle = t * 6.0 / 12.0
            x, y = cx + math.cos(angle) * 12.0, cy + math.sin(angle) * 12.0
            bot.state(x, y, cz, heading=angle, speed=6.0, flags=1, vehicle=410)
            cars.append((1, 410, x, y, cz, angle, 6.0, 1000, True, 0, 0))
        else:
            if args.down or args.helper:
                is_down = bool(args.down) and t >= args.down and not bot.revived_by
                bot.state(cx, cy, cz, heading=0.0, speed=0.0, flags=32 if is_down else 0, health=5.0 if is_down else 100.0)
                if bot.revived_by and not announced:
                    announced.append(1)
                    print("revived by player %d after %.0f s" % (bot.revived_by[0], t))
                if args.helper:
                    for pid, other in bot.others.items():
                        if other[6] & 32 and (other[0] - cx) ** 2 + (other[1] - cy) ** 2 < 9.0 and (not args.only or bot.names.get(pid) == args.only):
                            helping.setdefault(pid, t)
                            if t - helping[pid] >= 7.0:
                                bot.revive(pid)
                        else:
                            helping.pop(pid, None)
            elif args.stand:
                swing = math.sin(t * 0.9) if args.sweep else 1.0
                bot.state(cx, cy, cz, heading=args.heading + (math.sin(t * 0.4) * 0.6 if args.sweep else 0.0), speed=0.0, weapon=args.weapon, flags=8, aim=int(args.aim * swing))
            elif args.fire:
                if args.at_players and bot.others:
                    bot.aim_at = list(bot.others.values())[0][:3]
                bot.state(cx, cy, cz, heading=t * 0.3, speed=0.0, weapon=args.weapon, flags=8 | (16 if int(t) % 3 else 0), aim=10)
            else:
                bot.state(x, y, cz, heading=angle, speed=1.4, weapon=args.weapon)
        if args.cars and bot.populates is not False:
            for i in range(args.cars):
                a = t * 7.0 / 18.0 + i * 6.2832 / args.cars
                cars.append((100 + i, 404 + i % 2 * 6, cx + math.cos(a) * 18.0, cy + math.sin(a) * 18.0, cz, a, 7.0, 1000, False, 7, 4))
        if args.parked and bot.populates is not False:
            for i in range(args.parked):
                cars.append((200 + i, 445 + i * 22, cx + 8.0 + i * 4.0, cy - 6.0, cz, 1.5708, 0.0, 1000, 2, 0, 0))
        if cars:
            bot.report_vehicles(cars)
        if args.mission_peds:
            bot.report([(3000 + i, 105 + i, 8, cx - 3.0 + i * 1.5, cy + 3.0, cz, 3.1416, max(100.0 - hurt.get(3000 + i, 0.0), 0.0), 0, 3) for i in range(args.mission_peds)])
            for key, amount, weapon, who in bot.entity_damage:
                hurt[key] = hurt.get(key, 0.0) + amount
                print("mission character %d was hit by player %d for %.0f with weapon %d (%.0f in all)" % (key, who, amount, weapon, hurt[key]))
            bot.entity_damage = []
        if args.peds and bot.populates is not False:
            peds = []
            for i in range(args.peds):
                a = -t * 0.12 + i * 6.2832 / args.peds
                peds.append((1000 + i, 7 + i % 3 * 2, 4, cx + math.cos(a) * (args.radius + 6.0), cy + math.sin(a) * (args.radius + 6.0), cz, a - 1.5708, 100.0, 0))
            bot.report(peds)
        bot.pump()
        if args.attack and t - last_attack >= 0.4:
            last_attack = t
            for pid, other in bot.others.items():
                if args.only and bot.names.get(pid) != args.only:
                    continue
                if (other[0] - cx) ** 2 + (other[1] - cy) ** 2 < 16.0:
                    near_since.setdefault(pid, t)
                if pid in near_since and t - near_since[pid] >= args.attack_delay and dealt.get(pid, 0.0) < args.attack:
                    dealt[pid] = dealt.get(pid, 0.0) + 5.0
                    bot.hit(pid, 5.0, args.weapon)
        if args.hurt_peds and bot.entities and t - last_hurt >= 2.0:
            last_hurt = t
            victim = sorted(bot.entities)[0]
            bot.hit_entity(victim, 40.0, 30)
            print("hit pedestrian %d of player %d's game (model %d) for 40; it reports %.0f health" % (victim, bot.entities[victim][0], bot.entities[victim][2], bot.entities[victim][7] / 10.0))
        clock = time.time()
        if host_state == 1:
            mine = [r for r in bot.results if r[1] == bot.id]
            if mine:
                host_state = 2 if mine[0][3] and args.host_cutscene else 3
        if args.host_mission is not None and clock - host_last >= 0.5:
            host_last = clock
            if host_state == 0 and clock - host_start >= args.host_delay:
                host_state = 1
                print("asking to start mission %d" % args.host_mission)
            if host_state == 1:
                if clock - host_start > args.host_delay + 14:
                    host_state = 3
                    print("no answer to the mission question")
                else:
                    bot.ask_mission(args.host_mission)
            if host_state == 2:
                if bot.cutscene_free:
                    host_state = 3
                    print("all players have watched cutscene %s after %.1f s" % (args.host_cutscene, clock - host_start - args.host_delay))
                    bot.mission_text("The cutscene is over for everybody.")
                    host_done = clock + (30.0 if args.host_world else 8.0)
                else:
                    bot.cutscene(args.host_cutscene, args.host_area)
        if args.host_world and host_state >= 2 and (host_done or host_state == 2) and clock - world_last >= 0.25:
            world_last = clock
            bot.mission_world(blips=[(cx + 30.0, cy, cz, 0, 0, 3, 2)], markers=[(cx + 6.0, cy, cz - 1.0, 2.0, 255, 0, 0, 228, 1)],
                              pickups=[] if bot.pickups_taken else [(7, 1242, cx + 4.0, cy - 8.0, cz, 3)])
        if args.host_invite and host_state >= 2 and clock - invite_last >= 2.0:
            invite_last = clock
            for pid in bot.others:
                if pid not in (bot.party or []):
                    bot.invite(pid)
            if bot.party and bot.party != party_seen:
                party_seen = list(bot.party)
                print("the mission's party is now %s" % party_seen)
        for taken in bot.pickups_taken[world_taken:]:
            print("player %d took pickup %d of this bot's mission" % (taken[1], taken[0]))
        world_taken = len(bot.pickups_taken)
        for hit in bot.vehicle_hits:
            print("player %d damaged this bot's car (key %d): %d health, state %s" % (hit[3], hit[0], hit[1], hit[2].hex()))
        bot.vehicle_hits = []
        if host_done and clock >= host_done:   # the mission is passed: big text, pay, end
            host_done = 0.0
            bot.mission_text("mission passed!~n~~w~$350", style=1, time_ms=5000)
            bot.mission_text("350", style=250, time_ms=0)
            bot.mission_end(True)
            print("mission passed: text, pay and end sent")
        for ask in bot.asks:
            print("asked to join mission %d of player %d (vote %d)%s" % (ask[2], ask[1], ask[0], "" if args.vote is None else ", answered %d" % args.vote))
        for result in bot.results:
            print("mission %d of player %d: %s" % (result[2], result[1], "starts" if result[3] else "not started"))
        for text in bot.texts:
            print("mission text from player %d: %s" % (text[0], text[3]))
        for cut in bot.cutscenes:
            if cut[1] != watching:
                watching, watch_until = cut[1], t + args.watch
                print("cutscene %s of player %d's mission starts; watching for %.0f s" % (cut[1], cut[0], args.watch))
            elif watch_until is None:
                bot.cutscene_done()   # asked again: the first answer was lost
        bot.cutscenes = []
        if watch_until is not None and t >= watch_until:
            watch_until = None
            bot.cutscene_done()
            print("cutscene %s watched" % watching)
        for end in bot.mission_ends:
            print("the mission of player %d is over" % end[0])
        bot.asks, bot.results, bot.texts, bot.mission_ends = [], [], [], []
        if bot.damage:
            print("hit by player %d for %.1f with weapon %d (%d hits)" % (bot.damage[0] + (len(bot.damage),)))
        bot.damage = []
        for pid in bot.others:
            if pid not in seen:
                seen.add(pid)
                print("sees player %d (%s) at %.1f %.1f %.1f" % (pid, bot.names.get(pid, "?"), *bot.others[pid][:3]))
        time.sleep(0.05)
    bot.bye()
    print("left" if bot.kicked is None else "kicked: " + bot.kicked)
    return 0


if __name__ == "__main__":
    sys.exit(main())
