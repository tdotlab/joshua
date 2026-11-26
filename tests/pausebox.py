"""A harmless stand-in game for the live test: a moving box that P pauses and resumes.

Usage: pythonw pausebox.py STATE_FILE [--paused]
While running it spins a line and counts ticks; arrow keys move the box. It writes
its state to STATE_FILE as JSON on every tick, so the test can see what happened.
"""

import json
import math
import os
import sys
import tkinter as tk

state_path = sys.argv[1]
state = {"paused": "--paused" in sys.argv, "ticks": 0, "x": 40, "toggles": 0}
held = set()

root = tk.Tk()
root.title("Joshua pausebox")
root.resizable(False, False)
canvas = tk.Canvas(root, width=320, height=240, bg="#101018", highlightthickness=0)
canvas.pack()
box = canvas.create_rectangle(0, 100, 40, 140, fill="#e0a030", outline="")
spinner = canvas.create_line(160, 200, 160, 200, fill="#60c0ff", width=3)
label = canvas.create_text(160, 24, fill="white", font=("Segoe UI", 12))


def press(event):
    key = event.keysym.lower()
    if key in held:
        return  # key repeat
    held.add(key)
    if key == "p":
        state["paused"] = not state["paused"]
        state["toggles"] += 1


def release(event):
    held.discard(event.keysym.lower())


def tick():
    root.after(16, tick)
    if not state["paused"]:
        state["ticks"] += 1
        state["x"] += 4 * (("right" in held) - ("left" in held))
        state["x"] = max(0, min(280, state["x"]))
    angle = state["ticks"] / 10
    canvas.coords(spinner, 160, 200, 160 + 30 * math.cos(angle), 200 + 30 * math.sin(angle))
    canvas.coords(box, state["x"], 100, state["x"] + 40, 140)
    canvas.itemconfigure(label, text="PAUSED" if state["paused"] else f"tick {state['ticks']}")
    temporary = state_path + ".tmp"
    try:
        with open(temporary, "w", encoding="utf-8") as handle:
            json.dump(state, handle)
        os.replace(temporary, state_path)
    except OSError:
        pass  # the test is reading it; the next tick writes again


root.bind("<KeyPress>", press)
root.bind("<KeyRelease>", release)
root.after(16, tick)
root.mainloop()
