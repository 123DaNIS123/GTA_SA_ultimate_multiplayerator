#!/usr/bin/env python3
"""Checks of the server with scripted players, on this machine. Run: python test_server.py   (exit code 0 = passed)"""
import os
import struct
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from um_bot import Client

PORT = 47777
failed = 0


def check(what, ok):
    global failed
    print(("ok      " if ok else "FAILED  ") + what)
    if not ok:
        failed += 1


def run_for(clients, seconds, step=None):
    end = time.time() + seconds
    while time.time() < end:
        if step:
            step()
        for c in clients:
            c.pump()
        time.sleep(0.02)


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    import tempfile
    data_dir = tempfile.mkdtemp(prefix="um_test_", dir=os.path.join(here, "..", "build") if os.path.isdir(os.path.join(here, "..", "build")) else None)
    server = subprocess.Popen([sys.executable, os.path.join(here, "um_server.py"), "--port", str(PORT), "--password", "letmein", "--no-public-ip",
                               "--name", "test server", "--max-players", "3", "--anticheat", "--data", data_dir], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    time.sleep(1.0)
    try:
        info = Client("127.0.0.1", PORT, "q").query()
        check("the server answers a query with its name, player count and that it wants a password",
              info is not None and info["name"] == "test server" and info["players"] == 0 and info["max"] == 3 and info["password"])

        wrong = Client("127.0.0.1", PORT, "Mallory", "guess")
        check("a wrong password is refused", not wrong.connect(1.5) and wrong.rejected == 2)

        a = Client("127.0.0.1", PORT, "Alice", "letmein")
        b = Client("127.0.0.1", PORT, "Bob", "letmein")
        check("the right password gets in", a.connect() and b.connect())
        check("new players are given the single-player start as their spawn", a.spawn is not None and abs(a.spawn[0] - 2495.3) < 1 and abs(a.spawn[1] + 1687.0) < 1)

        # what the server keeps for a player: handed back when they join again, whoever was online in between
        c = Client("127.0.0.1", PORT, "Carol", "letmein")
        c.connect()
        c.get_player_data()
        run_for([a, b, c], 0.3, lambda: c.state(2400.0, -1650.0, 13.5, heading=1.0))
        check("a player the server has nothing for is told so", c.saved_all == 0 and not c.saved)
        stats = bytes(range(256)) * 3
        c.player_data(0, b"money and weapons")
        c.player_data(2, stats)
        run_for([a, b, c], 0.3, lambda: c.state(2400.0, -1650.0, 13.5, heading=1.0))
        c.bye()
        run_for([a, b], 0.5)
        c = Client("127.0.0.1", PORT, "carol", "letmein")
        c.connect()
        c.get_player_data()
        run_for([a, b, c], 0.4, lambda: c.state(c.spawn[0], c.spawn[1], c.spawn[2]))
        check("a player who comes back gets their items, looks and statistics and starts where they left",
              c.saved == {0: b"money and weapons", 2: stats} and c.saved_all == 2 and abs(c.spawn[0] - 2400.0) < 0.1 and abs(c.spawn[1] + 1650.0) < 0.1 and abs(c.spawn[3] - 1.0) < 0.01)
        c.bye()
        run_for([a, b], 0.5)

        # the world save: none at first; a vote; an upload in pieces; everybody is told the new version; download
        check("a new server has no world save (a new game)", a.world_info is not None and a.world_info[:2] == (0, 0))
        a.save_ask()
        run_for([a, b], 0.3)
        check("a player asks to save: the others are asked, nothing is decided yet", len(b.save_asks) == 1 and b.save_asks[0][1] == a.id and not a.save_results)
        b.save_vote(b.save_asks[0][0], True)
        run_for([a, b], 0.3)
        check("more than half said yes: everybody is told, with who makes the save", [r[1:] for r in a.save_results] == [(1, a.id)] and [r[1:] for r in b.save_results] == [(1, a.id)])
        save = bytes((i * 7) & 255 for i in range(3000))
        a.world_put(5, save)
        run_for([a, b], 0.2)
        check("an upload made from another version than the server's is refused", a.world_put_replies and a.world_put_replies[-1][0] == 0)
        a.world_put(0, save, script=1234)
        run_for([a, b], 0.2)
        a.world_chunk(0, save[:1200])
        a.world_chunk(0, save[:1200])       # a repeat is ignored
        a.world_chunk(1200, save[1200:2400])
        a.world_chunk(2400, save[2400:])
        run_for([a, b], 0.3)
        check("an upload in pieces is taken, and everybody hears of the new version",
              a.world_put_replies[-1] == (2, 1) and b.world_info[0] == 1 and b.world_info[1] == 3000 and b.world_info[3] == 1234)
        for offset in (0, 1200, 2400):
            b.world_get(1, offset)
        run_for([a, b], 0.3)
        check("the save can be fetched piece by piece", b"".join(b.world_chunks.get(o, b"") for o in (0, 1200, 2400)) == save)
        a.save_results, b.save_results, b.save_asks = [], [], []
        a.save_ask()
        run_for([a, b], 0.2)
        b.save_vote(b.save_asks[0][0], False)
        run_for([a, b], 0.3)
        check("without more than half there is no save", [r[1] for r in a.save_results] == [0])
        check("players get different numbers", a.id != b.id)

        same = Client("127.0.0.1", PORT, "alice", "letmein")
        check("a name already in use is given a suffix", same.connect() and same.name.lower() != "alice")
        full = Client("127.0.0.1", PORT, "Dave", "letmein")
        check("a full server refuses", not full.connect(1.5) and full.rejected == 1)
        same.bye()
        run_for([a, b], 0.3)

        # both walk near the spawn and must see each other
        t = [0.0]
        def walk():
            t[0] += 0.02
            a.state(2495.0 + t[0], -1687.0, 13.5, heading=1.0, speed=1.0)
            b.state(2500.0, -1680.0 + t[0], 13.5, heading=2.0, speed=1.0, health=55.0)
        before, began = a.snapshots, time.time()
        run_for([a, b], 1.5, walk)
        rate = (a.snapshots - before) / (time.time() - began)
        seen = a.others.get(b.id)
        check("each player knows the other's name", a.names.get(b.id) == "Bob" and b.names.get(a.id) == "Alice")
        check("each player receives the other's position, heading and health",
              seen is not None and abs(seen[0] - 2500.0) < 0.1 and abs(seen[3] - 2.0) < 0.01 and seen[5] == 550 and a.id in b.others)
        check("snapshots arrive about 20 times a second (%.1f)" % rate, 16.0 <= rate <= 24.0)
        check("a player is not sent to themselves", a.id not in a.others)

        # the shared world: where players are together, the lowest number's game makes the pedestrians
        def together():
            t[0] += 0.02
            a.state(2495.0, -1687.0, 13.5)
            b.state(2500.0, -1680.0, 13.5)
            a.report([(11, 7, 4, 2490.0 + t[0], -1690.0, 13.5, 0.5, 100.0, 0), (12, 9, 5, 2480.0, -1700.0, 13.5, 1.0, 80.0, 0)])
            b.report([(77, 20, 4, 2501.0, -1681.0, 13.5, 0.0, 100.0, 0)])   # refused: b is not the syncer here
        run_for([a, b], 2.5, together)
        first, second = (a, b) if a.id < b.id else (b, a)
        check("with two players together the lower number is told to make the pedestrians, the other not", first.populates is True and second.populates is False)
        mine = [e for e in second.entities.values() if e[0] == first.id]
        check("the other player receives the syncer's pedestrians with model, type, place and health",
              len(mine) == 2 and sorted(e[2] for e in mine) == [7, 9] and any(abs(e[5] + 1700.0) < 0.1 and e[8] == 800 for e in mine))
        check("a game is not sent its own pedestrians; it is sent the one the other game simulates (one syncer for each, whoever makes the street)",
              [e[2] for e in first.entities.values()] == [20] and all(e[0] != first.id for e in first.entities.values()))
        ids_before = set(second.entities)
        def one_left():
            a.state(2495.0, -1687.0, 13.5)
            b.state(2500.0, -1680.0, 13.5)
            a.report([(11, 7, 4, 2492.0, -1690.0, 13.5, 0.5, 100.0, 0)])
        run_for([a, b], 2.5, one_left)
        check("a pedestrian that is no longer reported disappears for the others; the rest keeps its number",
              len(second.entities) == 1 and set(second.entities) <= ids_before)
        def cheat():
            a.state(2495.0, -1687.0, 13.5)
            b.state(2500.0, -1680.0, 13.5)
            a.report([(11, 7, 4, 2492.0, -1690.0, 13.5, 0.5, 100.0, 0), (500, 7, 4, 900.0, 900.0, 13.5, 0.0, 100.0, 0), (501, 30000, 4, 2492.0, -1690.0, 13.5, 0.0, 100.0, 0),
                      (502, 7, 99, 2492.0, -1690.0, 13.5, 0.0, 100.0, 0)])
        run_for([a, b], 1.0, cheat)
        check("pedestrians reported far from the reporting player, with an impossible model or type, are not passed on", len(second.entities) == 1)

        # weapons and damage between players
        def armed():
            a.state(2495.0, -1687.0, 13.5, weapon=30)
            b.state(2500.0, -1680.0, 13.5, health=80.0)
        run_for([a, b], 0.5, armed)
        check("the weapon a player holds is sent to the others", b.others.get(a.id, (0,) * 10)[9] == 30)
        def firing():
            a.state(2495.0, -1687.0, 13.5, weapon=30, flags=8 | 16, aim=-40)
            b.state(2500.0, -1680.0, 13.5, health=80.0)
        run_for([a, b], 0.4, firing)
        seen = b.others.get(a.id, (0,) * 11)
        check("that a player aims and fires, and how far up or down, is sent to the others", seen[6] & 24 == 24 and seen[10] == -40)
        a.hit(b.id, 30.0, 30)
        run_for([a, b], 0.4, armed)
        check("a hit is passed on to the victim with the attacker, the damage and the weapon", b.damage == [(a.id, 30.0, 30)] and not a.damage)
        b.damage = []
        a.hit(b.id, 30.0, 38)       # a minigun the attacker does not hold
        a.hit(b.id, 500.0, 30)      # more than any single hit does
        a.hit(a.id, 30.0, 30)       # oneself
        a.hit(9999, 30.0, 30)       # nobody
        b.hit(a.id, 10.0, 0)        # fists from 8.6 m: in reach (12 m)
        run_for([a, b], 0.4, armed)
        check("hits with a weapon the attacker does not hold, with impossible damage, on oneself or on nobody are dropped", not b.damage)
        check("a punch from within reach is passed on", a.damage == [(b.id, 10.0, 0)])
        a.damage = []
        def far_fists():
            a.state(2495.0, -1687.0, 13.5, weapon=30)
            b.state(2540.0, -1680.0, 13.5)
        run_for([a, b], 0.4, far_fists)
        b.hit(a.id, 10.0, 0)
        for i in range(60):
            a.hit(b.id, 1.0, 30)
        run_for([a, b], 0.5, far_fists)
        check("a punch from 45 m away is dropped", not a.damage)
        check("more than 40 hits a second from one player are cut off (%d passed)" % len(b.damage), 30 <= len(b.damage) <= 40)
        b.damage = []
        run_for([a, b], 1.0, together)

        # knocked down and brought back
        def lying():
            a.state(2495.0, -1687.0, 13.5)
            b.state(2496.5, -1687.0, 13.5, health=5.0, flags=32)
        run_for([a, b], 0.5, lying)
        check("that a player lies knocked down is sent to the others", a.others.get(b.id, (0,) * 11)[6] & 32)
        a.hit(b.id, 30.0, 0)
        run_for([a, b], 0.3, lying)
        check("a knocked-down player cannot be hurt further", not b.damage)
        a.revive(b.id)
        run_for([a, b], 0.3, lying)
        check("a player beside a knocked-down one can bring them back: the downed player's game is told by whom", b.revived_by == [a.id])
        b.revived_by = []
        def lying_far():
            a.state(2480.0, -1687.0, 13.5)
            b.state(2496.5, -1687.0, 13.5, health=5.0, flags=32)
        run_for([a, b], 0.4, lying_far)
        a.revive(b.id)
        b.revive(b.id)
        run_for([a, b], 0.3, lying_far)
        check("not from 16 m away, and not by oneself", not b.revived_by)
        run_for([a, b], 0.4, together)
        a.revive(b.id)
        run_for([a, b], 0.3, together)
        check("a player who is not down is not 'revived'", not b.revived_by)

        # vehicles: the traffic from the syncer, a player's own vehicle from that player
        def traffic():
            a.state(2495.0, -1687.0, 13.5)
            b.state(2500.0, -1680.0, 13.5, flags=1, vehicle=410)
            first.report_vehicles([(5, 404, 2480.0, -1670.0, 13.5, 0.0, 8.0, 1000, False, 7, 4), (6, 9999, 2480.0, -1670.0, 13.5, 0.0, 8.0, 1000, False, 7, 4)])
            second.report_vehicles([(1, 410, 2500.0, -1680.0, 13.5, 1.0, 5.0, 900, True, 0, 0), (2, 404, 2510.0, -1680.0, 13.5, 0.0, 0.0, 1000, False, 7, 4)])
        run_for([a, b], 1.5, traffic)
        got = list(second.vehicles.values())
        check("the other player receives the syncer's traffic (a car with its driver's model), not a vehicle with an impossible model",
              len(got) == 1 and got[0][1] == 404 and got[0][17] == 7 and got[0][19] == 0 and got[0][21] == 0 and abs(got[0][11] - 0) <= 1 and abs(got[0][12] - 800) <= 8)
        got = list(first.vehicles.values())
        got = [v for v in got if v[1] == 410]
        check("the syncer receives the vehicle the other player sits in, marked with that player",
              len(got) == 1 and got[0][19] == second.id and got[0][14] == 900)
        # damage, lights and passengers travel with a vehicle; a player riding as a passenger names the vehicle and the seat
        car_id = [i for i, v in second.vehicles.items() if v[1] == 404][0]
        dents = bytes(range(1, 21))
        def riding():
            a.state(2495.0, -1687.0, 13.5)
            b.state(2480.0, -1670.0, 13.5, flags=1, vehicle=404, ride=car_id, seat=1)
            first.report_vehicles([(5, 404, 2480.0, -1670.0, 13.5, 0.0, 8.0, 1000, False, 7, 4, 0, 5, dents, [(105, 8), (40000, 99)])])
        run_for([a, b], 1.2, riding)
        got = second.vehicles.get(car_id)
        check("a vehicle's engine/lights/siren bits, its damage and its passengers reach the other players; impossible passengers do not",
              got is not None and got[22] == 5 and got[23] == dents and got[24:30] == (105, 8, 0, 0, 0, 0))
        got = first.others.get(second.id)
        check("a passenger is passed on with the vehicle and the seat; to the vehicle's own game by that game's key for it",
              got is not None and got[11] == 5 and got[12] == 129)
        second.vehicle_hit(car_id, 120, dents)
        second.vehicle_hit(999999, 50)
        run_for([a, b], 0.4, riding)
        check("damage a player does to another game's vehicle goes to that game, with its own key for the vehicle",
              first.vehicle_hits == [(5, 120, dents, second.id)] and not second.vehicle_hits)
        # the other player gets into the syncer's car: it becomes theirs, the syncer's game is told to drop its own
        def stolen():
            a.state(2495.0, -1687.0, 13.5)
            b.state(2480.0, -1670.0, 13.5, flags=1, vehicle=404)
            first.report_vehicles([(5, 404, 2480.0, -1670.0, 13.5, 0.0, 8.0, 1000, False, 7, 4)])   # its game has not removed it yet
            second.report_vehicles([(9, 404, 2480.0, -1670.0, 13.5, 0.0, 2.0, 1000, True, 0, 0, car_id)])
        run_for([a, b], 1.5, stolen)
        check("a player who gets into another game's car takes it over: its former game is told which one to remove", 5 in first.taken)
        check("that car is then sent as the player's own, once, and no longer as traffic",
              [v[19] for v in first.vehicles.values()] == [second.id] and not second.vehicles)
        run_for([a, b], 2.0, together)
        check("vehicles that are no longer reported disappear", not first.vehicles and not second.vehicles)

        # a mission's characters and vehicles come from the game that runs the mission, syncer or not; parked cars from the syncer
        def mission():
            a.state(2495.0, -1687.0, 13.5)
            b.state(2500.0, -1680.0, 13.5)
            second.report([(900, 105, 8, 2501.0, -1681.0, 13.5, 0.0, 100.0, 0, 3), (901, 106, 8, 2502.0, -1681.0, 13.5, 0.0, 100.0, 0)])
            second.report_vehicles([(40, 420, 2505.0, -1680.0, 13.5, 0.0, 0.0, 1000, 4, 0, 0), (41, 421, 2507.0, -1680.0, 13.5, 0.0, 0.0, 1000, 2, 0, 0)])
            first.report_vehicles([(42, 445, 2490.0, -1695.0, 13.5, 0.0, 0.0, 1000, 2, 0, 0)])
        run_for([a, b], 1.5, mission)
        got = [e for e in first.entities.values()]
        check("a mission character reported by a player who is not the syncer reaches the others, marked as such",
              [e[1] for e in got if e[2] == 105] == [3])
        got = sorted((v[1], v[21]) for v in first.vehicles.values())
        check("so does a mission's vehicle, and a parked car that game simulates", (420, 4) in got and (421, 2) in got)
        got = sorted((v[1], v[21]) for v in second.vehicles.values())
        check("the syncer's parked cars reach the others, marked as parked", got == [(445, 2)])
        target = [i for i, e in first.entities.items() if e[2] == 105][0]
        first.hit_entity(target, 40.0, 30)
        run_for([a, b], 0.4, mission)
        check("a hit on another game's character goes to that game with its own key, the damage, the weapon and the attacker",
              second.entity_damage == [(900, 40.0, 30, first.id)])
        run_for([a, b], 2.0, together)

        # a mission: the players nearby are asked, all must join
        run_for([a, b], 0.3, together)
        a.ask_mission(12)
        run_for([a, b], 0.4, together)
        check("a player about to start a mission makes the server ask the players nearby", len(b.asks) == 1 and b.asks[0][1:] == (a.id, 12) and not a.asks)
        b.send(13, struct.pack("<HB", b.asks[0][0], 0))
        run_for([a, b], 0.4, together)
        check("one 'no' and the mission is not started: both are told", [r[3] for r in a.results] == [0] and [r[3] for r in b.results] == [0])
        a.results, b.results, b.asks = [], [], []
        run_for([a, b], 2.2, together)
        b.auto_vote = 2
        a.ask_mission(12)
        a.ask_mission(12)   # the game repeats its request while it waits
        run_for([a, b], 1.0, together)
        check("everybody says yes: the mission starts, and who asked to be brought along is told so",
              len(b.asks) == 1 and [r[3] for r in a.results] == [1] and [(r[3], r[4]) for r in b.results] == [(1, 2)])
        a.cutscene("INTRO2A", 3)
        a.cutscene("INTRO2A", 3)   # repeated until the server says "go on"
        run_for([a, b], 0.4, together)
        check("a cutscene of the mission goes to the players who joined, and the mission's game is not told to go on yet",
              b.cutscenes and all(c == (a.id, "INTRO2A") for c in b.cutscenes) and b.cutscene_area == 3 and not a.cutscenes and a.cutscene_free == 0)
        b.cutscene_done()
        run_for([a, b], 0.4, together)
        check("when all have watched or skipped it, the mission's game is told to go on", a.cutscene_free == 1)
        a.cutscene("INTRO2A")
        run_for([a, b], 0.3, together)
        check("a late repeat is answered with 'go on' again and not shown twice", a.cutscene_free == 2 and len(b.cutscenes) == 2)
        b.cutscenes = []
        a.mission_world(blips=[(1.0, 2.0, 3.0, 0, 0, 3, 2)], pickups=[(7, 1240, 4.0, 5.0, 6.0, 3)])
        b.mission_world()
        b.pickup_taken(7)
        a.pickup_taken(8)
        run_for([a, b], 0.4, together)
        check("the mission's blips, markers and pickups go to the players who joined; a pickup one of them takes is reported to the mission's game",
              len(b.worlds) == 1 and b.worlds[0][0] == a.id and len(b.worlds[0][1]) == 39 and not a.worlds and a.pickups_taken == [(7, b.id)] and not b.pickups_taken)
        check("the mission's game is told who is in it", a.party == [b.id])
        a.invite(b.id)
        run_for([a, b], 0.3, together)
        check("a player who is in the mission already is not asked again", len(b.asks) == 1)
        a.mission_text("Get in the car!")
        b.mission_text("I am not the host")
        a.mission_end(True)
        run_for([a, b], 0.4, together)
        check("the mission's texts and its end go to the players who joined, and only from the game that runs it",
              [t[3] for t in b.texts] == ["Get in the car!"] and not a.texts and b.mission_ends == [(a.id, 1)])
        b.texts, b.auto_vote, b.asks, a.results, b.results = [], None, [], [], []
        a.mission_text("after the end")
        run_for([a, b], 0.3, together)
        check("after the end nothing more is passed on", not b.texts)
        def alone():
            a.state(2495.0, -1687.0, 13.5)
            b.state(2900.0, -1680.0, 13.5)
        run_for([a, b], 6.0, alone)
        a.ask_mission(5)
        run_for([a, b], 0.4, alone)
        check("with nobody nearby the mission starts at once and nobody is asked", [r[3] for r in a.results] == [1] and not b.asks)
        a.results = []
        # one mission at a time: while Alice's runs, Bob cannot start another
        b.results = []
        b.ask_mission(7)
        run_for([a, b], 0.4, alone)
        check("while one player's mission runs, another player's mission is not started and they are told why",
              [(r[1], r[3], r[4]) for r in b.results] == [(a.id, 0, 3)] and not a.asks)
        a.mission_end(True)
        run_for([a, b], 2.5, alone)
        b.results = []
        b.ask_mission(7)
        run_for([a, b], 0.4, alone)
        check("when it has ended, the next mission can start", [r[3] for r in b.results] == [1])
        b.mission_end(False)
        run_for([a, b], 0.3, alone)
        b.results = []
        run_for([a, b], 6.0, together)
        b.corrections = []   # (this test's own jumps away and back)

        # far apart: no longer sent to each other
        def apart():
            t[0] += 0.02
            a.state(2495.0 + t[0], -1687.0, 13.5)
            b.state(2500.0 + (time.time() - began) * 60.0, -1680.0, 13.5, speed=60.0)  # drives off, fast but possible
        began = time.time()
        run_for([a, b], 7.0, apart)
        check("players far apart are not sent to each other", b.id not in a.others and a.id not in b.others)
        check("apart again, each player's game makes its own pedestrians", a.populates is True and b.populates is True)
        check("driving off at 60 m/s is not treated as cheating", not b.corrections and b.kicked is None)

        # what the game itself does is not cheating: a door (interiors are a kilometre up), and a move by a mission
        b.state(2500.0, -1680.0, 13.5)
        run_for([a, b], 0.3)
        before = len(b.corrections)
        b.state(2496.0, -1707.0, 1014.7, interior=3)   # through a door
        run_for([a, b], 0.3)
        def indoors():
            a.state(2495.0, -1687.0, 13.5)
            b.state(2496.0, -1707.0, 1014.7, interior=3)
        run_for([a, b], 0.5, indoors)
        check("a jump that comes with a change of interior (a door) is accepted at once", len(b.corrections) == before and a.others.get(b.id, (0,) * 9)[8] == 3)
        def moved():
            a.state(2495.0, -1687.0, 13.5)
            b.state(1000.0, 1000.0, 1014.7, interior=3)
        run_for([a, b], 0.6, moved)
        check("a jump without a reason is answered with a correction", len(b.corrections) == before + 1)
        run_for([a, b], 2.0, moved)
        check("a player who stays at the new place (the game moved them) is accepted there, once, and not removed", len(b.corrections) == before + 1 and b.kicked is None)

        # a teleport is corrected, and repeated ones get the player removed
        for i in range(12):
            a.state(2495.0 + (3000.0 if i % 2 == 0 else -3000.0), 500.0, 13.5)
            run_for([a, b], 0.12)
        check("teleporting is answered with a correction back to the last good place", len(a.corrections) >= 1 and abs(a.corrections[0][1] + 1687.0) < 5.0)
        check("repeated teleporting gets the player removed", a.kicked is not None)
        run_for([b], 0.3)
        check("the others are told that a player left", a.id not in b.names)

        server.stdin.write("players\nanticheat off\nstatus\n")
        server.stdin.flush()
        c = Client("127.0.0.1", PORT, "Carol", "letmein")
        check("a new player can join after the kick", c.connect())
        for i in range(6):
            c.state(2495.0 + (3000.0 if i % 2 == 0 else -3000.0), 500.0, 13.5)
            run_for([c], 0.12)
        check("with the anti-cheat switched off from the console, teleporting is accepted", not c.corrections and c.kicked is None)

        # the game that runs a mission leaves: the mission has failed for the players in it, and another can start
        def side_by_side():
            b.state(2500.0, -1680.0, 13.5)
            c.state(2503.0, -1680.0, 13.5)
        run_for([b, c], 6.5, side_by_side)
        b.auto_vote = 1
        b.mission_ends, c.results = [], []
        c.ask_mission(9)
        run_for([b, c], 1.0, side_by_side)
        check("a mission with one more player in it is running", [r[3] for r in c.results] == [1] and c.party == [b.id])
        carol = c.id

        # silence: a player who stops talking is dropped after the timeout (15 s is too long for a test; BYE instead)
        c.bye()
        run_for([b], 0.5)
        check("a player who quits is removed at once", c.id not in b.names)
        check("when the player whose game runs the mission leaves, the players in it are told it has failed", b.mission_ends == [(carol, 2)])
        b.results = []
        b.ask_mission(10)
        run_for([b], 2.5)
        check("and the next mission can start", [r[3] for r in b.results] == [1])
        b.mission_end(False)
        run_for([b], 0.3)

        # junk does not disturb the server
        import socket
        junk = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        for payload in (b"", b"UM", b"XX" + bytes(40), b"UM" + bytes([4]) + b"\xff" * 5, b"UM" + bytes([2, 1, 200]) + b"a" * 10, os.urandom(900)):
            junk.sendto(payload, ("127.0.0.1", PORT))
        for i in range(200):
            junk.sendto(b"UM" + bytes([1]), ("127.0.0.1", PORT))
        junk.close()
        check("a burst from an address that is not a player is cut off", Client("127.0.0.1", PORT, "q").query() is None)
        time.sleep(1.2)
        check("the server still answers after malformed packets and the burst", Client("127.0.0.1", PORT, "q").query() is not None)
        b.state(2500.0, -1680.0, 13.5)
        run_for([b], 0.3)
        check("and the connected player is still served", b.kicked is None and b.snapshots > 0)
        b.bye()
        time.sleep(0.2)
        server.stdin.write("quit\n")
        server.stdin.flush()
        output = server.communicate(timeout=5)[0]
        check("the console lists players and reports status", "Bob" in output and "anti-cheat off" in output.replace("is off", "off") or "anti-cheat is off" in output)
        check("the start-up text names the port as open and the addresses to give to players", "open and answering" in output and "this machine:" in output)
        check("the server stops on 'quit'", server.returncode == 0 and "server stopped" in output)
    finally:
        if server.poll() is None:
            server.kill()
    print("server test: " + ("%d FAILED" % failed if failed else "all passed"))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
