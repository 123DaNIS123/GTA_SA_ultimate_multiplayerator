#!/usr/bin/env python3
"""Ultimate Multiplayerator launcher: a list of favourite servers, their state, and "Connect".

Connect writes the server, your name and the password into UltimateMultiplayerator.ini next to gta_sa.exe and starts
the game; the mod in the game does the rest. Needs Python 3 with tkinter (part of the standard Windows install).
"Play offline" removes the server from the ini and starts the game.
"""
import json
import os
import socket
import struct
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SETTINGS = os.path.join(HERE, "launcher.json")
MAGIC = b"UM"
C_QUERY, S_INFO = 1, 65


def load_settings():
    settings = {"name": "Player", "game": "", "favourites": []}
    try:
        with open(SETTINGS, "r", encoding="utf-8") as f:
            settings.update(json.load(f))
    except (OSError, ValueError):
        pass
    if not settings["game"]:
        for guess in (os.path.normpath(os.path.join(HERE, "..")), os.path.normpath(os.path.join(HERE, "..", "GTAGame"))):
            if os.path.exists(os.path.join(guess, "gta_sa.exe")):   # installed in the game folder, or next to it
                settings["game"] = guess
                break
    return settings


def save_settings(settings):
    with open(SETTINGS, "w", encoding="utf-8") as f:
        json.dump(settings, f, indent=2)


def parse_address(text):
    """'host' or 'host:port' -> (host, port); None when it is not an address."""
    text = text.strip()
    if not text or " " in text:
        return None
    host, _, port = text.partition(":")
    try:
        port = int(port) if port else 7777
    except ValueError:
        return None
    return (host, port) if host and 0 < port < 65536 else None


def query(host, port, timeout=1.0):
    """Asks a server for its name and player count. None when it does not answer."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.settimeout(timeout)
    try:
        s.sendto(MAGIC + bytes([C_QUERY]), (host, port))
        data, _ = s.recvfrom(512)
    except OSError:
        return None
    finally:
        s.close()
    if len(data) < 10 or data[:3] != MAGIC + bytes([S_INFO]):
        return None
    version, players, limit, password, n = struct.unpack_from("<BHHBB", data, 3)
    return {"version": version, "players": players, "max": limit, "password": bool(password), "name": data[10:10 + n].decode("utf-8", "replace")}


def write_connection(game, host, port, name, password):
    """The [Connect] section the mod reads. An empty host means single player."""
    path = os.path.join(game, "UltimateMultiplayerator.ini")
    enabled = "1"
    list_key = "118"
    try:  # keep the on/off switch as the player left it
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                if line.strip().lower().startswith("enabled="):
                    enabled = line.strip().split("=", 1)[1].strip() or "1"
                if line.strip().lower().startswith("playerlistkey="):
                    list_key = line.strip().split("=", 1)[1].strip() or "118"
    except OSError:
        pass
    lines = ["; UltimateMultiplayerator.asi - online play. Written by UMLauncher.",
             "[UltimateMultiplayerator]", "; 0 = the mod does nothing at all", "Enabled=" + enabled,
             "; hold this key to see who is online (Windows virtual key code; 118 = F7)", "PlayerListKey=" + list_key, "",
             "[Connect]", "; the server to join when the game starts; empty Host = single player.",
             "; While a server is set here, the split-screen mod switches itself off for that session.",
             "Host=" + host, "Port=%d" % port, "Name=" + name, "Password=" + password, ""]
    with open(path, "w", encoding="utf-8", newline="\r\n") as f:
        f.write("\n".join(lines))
    return path


def start_game(game):
    exe = os.path.join(game, "gta_sa.exe")
    if not os.path.exists(exe):
        return False
    subprocess.Popen([exe], cwd=game)
    return True


def main():
    import tkinter as tk
    from tkinter import filedialog, messagebox, simpledialog, ttk

    settings = load_settings()
    root = tk.Tk()
    root.title("Ultimate Multiplayerator")
    root.geometry("760x420")

    top = ttk.Frame(root, padding=8)
    top.pack(fill="x")
    ttk.Label(top, text="Name:").pack(side="left")
    name = tk.StringVar(value=settings["name"])
    ttk.Entry(top, textvariable=name, width=20).pack(side="left", padx=(4, 16))
    ttk.Label(top, text="Game folder:").pack(side="left")
    game = tk.StringVar(value=settings["game"])
    ttk.Entry(top, textvariable=game, width=44).pack(side="left", padx=4)

    def browse():
        folder = filedialog.askdirectory(title="The folder with gta_sa.exe")
        if folder:
            game.set(os.path.normpath(folder))
    ttk.Button(top, text="...", width=3, command=browse).pack(side="left")

    columns = ("address", "name", "players", "password")
    table = ttk.Treeview(root, columns=columns, show="headings", selectmode="browse")
    for column, title, width in (("address", "Address", 190), ("name", "Server", 300), ("players", "Players", 90), ("password", "Password", 90)):
        table.heading(column, text=title)
        table.column(column, width=width, anchor="w")
    table.pack(fill="both", expand=True, padx=8)

    status = tk.StringVar(value="Add a server by its address (host or host:port), then Connect.")
    ttk.Label(root, textvariable=status, padding=8).pack(fill="x")

    def refresh():
        table.delete(*table.get_children())
        for address in settings["favourites"]:
            parsed = parse_address(address)
            info = query(*parsed, timeout=0.6) if parsed else None
            if info:
                row = (address, info["name"], "%d / %d" % (info["players"], info["max"]), "yes" if info["password"] else "no")
            else:
                row = (address, "(no answer)", "", "")
            table.insert("", "end", iid=address, values=row)

    def remember():
        settings["name"] = name.get().strip()[:24] or "Player"
        settings["game"] = game.get().strip()
        save_settings(settings)

    def add():
        address = simpledialog.askstring("Add a server", "Address (host or host:port):", parent=root)
        if not address:
            return
        if not parse_address(address):
            messagebox.showerror("Add a server", "That is not an address. Examples: 203.0.113.5  or  play.example.org:7777")
            return
        address = address.strip()
        if address not in settings["favourites"]:
            settings["favourites"].append(address)
        remember()
        refresh()

    def remove():
        selected = table.selection()
        if selected:
            settings["favourites"].remove(selected[0])
            remember()
            refresh()

    def connect():
        selected = table.selection()
        if not selected:
            status.set("Pick a server in the list first.")
            return
        host, port = parse_address(selected[0])
        remember()
        if not os.path.exists(os.path.join(settings["game"], "gta_sa.exe")):
            messagebox.showerror("Connect", "gta_sa.exe was not found in the game folder. Set the folder at the top.")
            return
        info = query(host, port)
        if not info:
            if not messagebox.askyesno("Connect", "The server does not answer. Start the game anyway? (It keeps trying to join.)"):
                return
        password = ""
        if info and info["password"]:
            password = simpledialog.askstring("Password", "This server asks for a password:", show="*", parent=root) or ""
        write_connection(settings["game"], host, port, settings["name"], password)
        if start_game(settings["game"]):
            status.set("Game started for %s. Load or start a game; the mod's log is UltimateMultiplayerator.log in the game folder." % selected[0])

    def offline():
        remember()
        if not os.path.exists(os.path.join(settings["game"], "gta_sa.exe")):
            messagebox.showerror("Play offline", "gta_sa.exe was not found in the game folder. Set the folder at the top.")
            return
        write_connection(settings["game"], "", 7777, settings["name"], "")
        start_game(settings["game"])

    buttons = ttk.Frame(root, padding=8)
    buttons.pack(fill="x")
    for text, command in (("Connect", connect), ("Add server", add), ("Remove", remove), ("Refresh", refresh), ("Play offline", offline)):
        ttk.Button(buttons, text=text, command=command).pack(side="left", padx=4)
    table.bind("<Double-1>", lambda event: connect())

    refresh()
    root.mainloop()
    remember()


if __name__ == "__main__":
    main()
