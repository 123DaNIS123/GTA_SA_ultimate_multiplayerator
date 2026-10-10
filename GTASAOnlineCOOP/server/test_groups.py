"""Who makes the street for whom (Server.roles), checked without a network: python GTAGame\\server\\test_groups.py"""
import struct
import sys

import um_server

failed = []


def check(what, ok):
    print(("ok    " if ok else "FAILED") + " " + what)
    if not ok:
        failed.append(what)


class Fake:
    """Just what Server.roles uses of a server."""
    def __init__(self):
        self.players, self.entity_index, self.sent, self.other = {}, {}, {}, []

    def send(self, addr, kind, body=b""):
        if kind == um_server.S_ROLE:
            self.sent[addr] = body
        else:
            self.other.append((addr, kind, body))

    was_released = um_server.Server.was_released
    handover = um_server.Server.handover
    on_adopt = um_server.Server.on_adopt
    vehicle_name = um_server.Server.vehicle_name
    on_vehicle_in = um_server.Server.on_vehicle_in
    on_vehicle_out = um_server.Server.on_vehicle_out

    def add(self, pid, x, y, interior=0):
        p = um_server.Player(pid, "p%d" % pid, pid, 0.0)
        p.state = (x, y, 13.0, 0.0, 0.0, 1000, 0, 0, interior, 0, 0)
        self.players[pid] = p
        return p

    def move(self, pid, x, y):
        s = self.players[pid].state
        self.players[pid].state = (x, y) + tuple(s[2:])

    def roles(self, now):
        um_server.Server.roles(self, now)
        return {pid: (p.populates, list(p.members)) for pid, p in self.players.items()}


def main():
    f = Fake()
    f.add(1, 0.0, 0.0)
    f.add(2, 150.0, 0.0)
    f.add(3, 290.0, 0.0)     # near player 2, 290 m from player 1
    f.add(4, 470.0, 0.0)     # near player 3, too far from player 1
    f.add(5, 10.0, 10.0, interior=3)
    r = f.roles(10.0)
    check("three players in a row are one group, made by the lowest number", r[1] == (True, [2, 3]) and r[2] == (False, []) and r[3] == (False, []))
    check("a player beyond the group's reach makes their own street", r[4] == (True, []))
    check("indoors a player keeps their own", r[5] == (True, []))
    body = f.sent[1]
    check("the role packet lists the group for the game that makes its street", body[0] == 1 and body[1] == 2 and struct.unpack_from("<2H", body, 2) == (2, 3))
    check("... and is a plain 'no' with an empty list for the others", f.sent[2] == bytes([0, 0]))

    f.move(3, 330.0, 0.0)    # 180 m from player 2, 330 m from player 1: stays (let go beyond 340 m)
    r = f.roles(11.0)
    check("a member is kept a little beyond the distance at which it would be taken in", r[1] == (True, [2, 3]))
    f.move(3, 345.0, 0.0)
    r = f.roles(12.0)
    check("beyond that it makes its own street, and the player near it joins it", r[1] == (True, [2]) and r[3] == (True, [4]) and r[4] == (False, []))

    f.move(1, 2000.0, 0.0)   # the leader leaves the group
    r = f.roles(13.0)
    check("the leader gone: the next lowest number takes over those in reach", r[1] == (True, []) and r[2] == (True, [3, 4]) or r[2] == (True, [3]))

    g = Fake()               # many players in one place: no more than the client's list holds
    for i in range(1, 25):
        g.add(i, i * 3.0, 0.0)
    r = g.roles(1.0)
    check("a group holds at most %d players besides its leader; the rest form the next group" % um_server.GROUP_MAX,
          len(r[1][1]) == um_server.GROUP_MAX and sum(1 for v in r.values() if v[0]) == 2)
    # one syncer for each pedestrian, handed over as in MTA
    h = Fake()
    a, b = h.add(1, 0.0, 0.0), h.add(2, 150.0, 0.0)
    e = um_server.Entity()
    e.id, e.owner, e.key, e.kind, e.x, e.y, e.heard, e.offered, e.offered_to = 500, 1, 77, um_server.KIND_PED, 60.0, 0.0, 0.0, 0.0, 0
    a.entities[77] = e
    h.handover(10.0)
    check("a pedestrian within 100 m of its game's player stays with that game", not h.other)
    e.x = 120.0   # 120 m from player 1, 30 m from player 2
    h.handover(11.5)
    check("beyond 100 m it is offered to the player near it", h.other == [(2, um_server.S_HANDOVER, struct.pack("<BI", 1, 500))])
    h.handover(11.8)
    check("... not again within a second", len(h.other) == 1)
    h.on_adopt(a, struct.pack("<BII", 1, 500, 9), 12.0)
    check("only the player it was offered to can adopt it", 77 in a.entities and not b.entities)
    h.on_adopt(b, struct.pack("<BII", 1, 500, 9), 12.0)
    check("adopted: it is the new game's under that game's key, with the same id",
          77 not in a.entities and b.entities.get(9) is e and e.owner == 2 and e.id == 500)
    check("the old game is told to let go of it", h.other[-1] == (1, um_server.S_RELEASE, struct.pack("<BII", 1, 77, 500)))
    n = len(h.other)
    check("a report of it from the old game is refused and the word repeated", h.was_released(a, 1, 77, 12.5) and len(h.other) == n + 1)
    check("other keys of the old game are not touched", not h.was_released(a, 1, 78, 12.5))
    # a seat is asked for
    v = Fake()
    a, b, c = v.add(1, 0.0, 0.0), v.add(2, 2.0, 0.0), v.add(3, 4.0, 0.0)
    car = um_server.Vehicle()
    car.id, car.owner, car.key, car.kind, car.player, car.x, car.y = 900, 1, 55, 0, 0, 1.0, 0.0
    a.vehicles[55] = car
    def ask(player, owner, key, seat, now, seats=3):
        v.other.clear()
        v.on_vehicle_in(player, struct.pack("<HIBB", owner, key, seat, seats), now)
        return struct.unpack("<BHIBB", v.other[-1][2])
    r = ask(a, 1, 55, 0, 1.0)
    check("the wheel of an empty car is given to the first who asks", r[0] == 1 and r[3] == 0)
    r = ask(b, 0xFFFF, 900, 0, 1.5)
    check("another player at the same door a moment later is refused (asked by the street id: the same car)", r[0] == 0 and r[4] == 2)
    r = ask(b, 0xFFFF, 900, 1, 2.0)
    check("a passenger seat is given: the first free one", r[0] == 1 and r[3] == 1)
    r = ask(c, 0xFFFF, 900, 1, 2.5)
    check("the next passenger gets the next seat", r[0] == 1 and r[3] == 2)
    r = ask(c, 0xFFFF, 900, 1, 2.6, seats=1)
    check("no seat free: refused", r[0] == 0 and r[4] == 3)
    a.state = a.state[:6] + (um_server.FLAG_IN_VEHICLE,) + a.state[7:]
    r = ask(c, 0xFFFF, 900, 0, 9.0)
    check("the wheel of a car somebody sits in: yes, the driver is pulled out", r[0] == 2)
    r = ask(a, 1, 55, 0, 13.0)
    check("... and the first driver can take it back later", r[0] == 2)
    v.on_vehicle_out(a)
    r = ask(c, 0xFFFF, 900, 0, 20.0)
    check("after the driver left, the wheel is free again", r[0] == 1)
    print("FAILED: %d" % len(failed) if failed else "all passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
