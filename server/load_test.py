#!/usr/bin/env python3
"""How the server holds up with many players: N scripted players for some seconds, spread over the map in groups.
  python load_test.py [players=100] [seconds=15] [group size=10]
Prints the snapshot rate each player got and the processor time the server used."""
import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from um_bot import Client

PORT = 47778


def main():
    count = int(sys.argv[1]) if len(sys.argv) > 1 else 100
    seconds = float(sys.argv[2]) if len(sys.argv) > 2 else 15.0
    group = int(sys.argv[3]) if len(sys.argv) > 3 else 10
    here = os.path.dirname(os.path.abspath(__file__))
    server = subprocess.Popen([sys.executable, os.path.join(here, "um_server.py"), "--port", str(PORT), "--no-public-ip", "--max-players", str(count)],
                              stdin=subprocess.PIPE, stdout=open(os.path.join(here, "load_test_server.log"), "w"), stderr=subprocess.STDOUT, text=True)  # a pipe nobody reads would fill up and block the server
    time.sleep(1.0)
    clients = []
    try:
        for i in range(count):
            c = Client("127.0.0.1", PORT, "Load%03d" % i)
            c.sock.settimeout(0.0)
            if not c.connect(5.0):
                print("player %d could not join (rejected %s)" % (i, c.rejected))
                return 1
            clients.append(c)
        print("%d players joined" % len(clients))
        for c in clients:
            c.snapshots = 0
        start = time.time()
        cpu_start = time.process_time()
        sent = 0
        next_send = start
        while time.time() - start < seconds:
            now = time.time()
            if now >= next_send:
                next_send += 0.05
                t = now - start
                for i, c in enumerate(clients):
                    gx, gy = (i // group) % 10 * 600.0 - 2700.0, (i // group) // 10 * 600.0 - 2700.0  # groups 600 m apart
                    c.state(gx + (i % group) * 3.0 + t, gy, 13.5, heading=1.0, speed=1.0)
                    if i % group == 0:  # each group's syncer reports 30 pedestrians
                        c.report([(k, 7, 4, gx + k, gy + 5.0 + t * 0.1, 13.5, 0.0, 100.0, 0) for k in range(30)])
                    sent += 1
            for c in clients:
                c.pump()
            time.sleep(0.002)
        elapsed = time.time() - start
        rates = sorted(c.snapshots / elapsed for c in clients)
        seen = sorted(len(c.others) for c in clients)
        world = sorted(len(c.entities) for c in clients if c.populates is False)
        print("%.0f s: each player sent %.1f states a second" % (elapsed, sent / len(clients) / elapsed))
        print("snapshots a second per player: lowest %.1f, median %.1f, highest %.1f (20 wanted)" % (rates[0], rates[len(rates) // 2], rates[-1]))
        print("other players in a snapshot: %d to %d (group size %d)" % (seen[0], seen[-1], group))
        if world:
            print("pedestrians shown by the players who are not syncers: %d to %d (30 reported per group)" % (world[0], world[-1]))
        print("this test process used %.1f s of processor time for %d clients" % (time.process_time() - cpu_start, len(clients)))
        server.stdin.write("status\nquit\n")
        server.stdin.flush()
        server.wait(timeout=10)
        out = open(os.path.join(here, "load_test_server.log")).read()
        for line in out.splitlines():
            if "packets in" in line:
                print("server: " + line.strip())
    finally:
        if server.poll() is None:
            server.kill()
    return 0


if __name__ == "__main__":
    sys.exit(main())
