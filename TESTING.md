# Testing hands-free

How to control your Mac with your hand, start to finish. Ten minutes.

Two things decide whether this works, and neither is obvious:

1. **Light.** The camera must be able to see your hand clearly. In a dim room
   the sensor goes to maximum gain and a long exposure, and the image becomes
   noisy and motion-blurred — the tracker then loses your hand every second or
   so and nothing else you do will fix it. Measured in the current room:
   brightness 31/255, which is too dark.
2. **The Mac caches the Bluetooth descriptor when it pairs.** If anything about
   the device's HID layout changes, you must forget it and pair again or the
   Mac silently ignores every report.

---

## 1. Get set up

**Light the scene first.** A lamp or a bright window in front of you, pointing
at your hand — not behind you. Do this *before* starting, because the camera
locks its exposure at start-up on purpose.

**Turn the Pi on** and check it's there:

```sh
ping -c1 192.168.2.188
```

If that fails the Pi is off or off the network. Nothing else will work.

---

## 2. Check the camera can see you

```sh
ssh chloewashere@192.168.2.188
cd ~/Code/hands-free && git pull

sudo venv/bin/python -m handsfree doctor --seconds 30
```

Hold one hand still in front of the camera for the whole 30 seconds.

Read two lines:

```
brightness    31/255   <- TOO DARK - add light
drops         0  =  0.0/min   <- steady
```

- **brightness** must say `fine`. If it says `TOO DARK`, add light and run it
  again. Don't carry on until this passes — everything downstream assumes the
  tracker can hold on to your hand.
- **drops** should be under 1/min. That's how often it loses your hand and has
  to find it again. Above 4 and gestures will feel like they randomly stop.

---

## 3. Pair with your Mac

On the Pi:

```sh
sudo venv/bin/python -m handsfree run
```

On the Mac: **System Settings → Bluetooth**, find **Hands-Free**, click Connect.

If it's listed already from a previous attempt, click the ⓘ next to it and
**Forget This Device** first, then pair fresh. This matters more than it sounds
— a stale pairing is the single most common reason nothing happens.

The Pi prints `paired and connected` when it's done. After this it reconnects
by itself, including after a reboot.

---

## 4. Use it

Leave `run` going. Now, in front of the camera:

| Do this | What should happen |
| --- | --- |
| Hold your hand still | **nothing** — it ignores you until you ask |
| **Point** your index finger, hold about a second | the cursor starts following your hand |
| Move your hand around | the cursor follows |
| **Open your palm** | the cursor stops dead |
| Move your hand elsewhere, point again | the cursor carries on from where it was |
| **Pinch** thumb to index, release | a click |
| Pinch, move, then release | a drag |
| **Two fingers**, move up and down | scrolls |
| **Three fingers**, sweep sideways | switches Space |

Point first. That's deliberate — a hand resting in view can't do anything until
you point at the camera, which is what stops it clicking while you think.

The terminal prints every gesture as it fires, so you can always see whether
the Pi recognised something and the Mac ignored it, or whether the Pi never saw
it at all. Those need completely different fixes.

**To stop:** Ctrl-C. The mouse button is always released on the way out.

---

## If something's wrong

| What you see | What it means | Do |
| --- | --- | --- |
| Terminal prints gestures, cursor doesn't move | the Mac isn't accepting reports | forget the device on the Mac and pair again |
| Nothing in the terminal either | the camera isn't seeing your hand | run `doctor`; check light and framing |
| Works, then stops for a few seconds, then works | losing your hand | add light; `doctor` will show the drops |
| Cursor moves but drifts away from your hand | expected — see below | open your palm, move your hand back, point again |
| A click you didn't ask for | a closing fist read as a pinch | keep your index finger out when you're not pinching |
| Swipes don't fire | sweep further and more decisively | — |
| `Device or resource busy` | an earlier run still has the camera | `pgrep -af 'python.*handsfree'` then kill it |
| Pi unreachable | it's off, or Wi-Fi dropped | check the power light |

---

## Known, and worth knowing before you judge it

**The cursor drifts away from your hand.** macOS applies its own pointer
acceleration to mouse input, so moving your hand 10 cm doesn't move the cursor
a fixed distance — it depends how fast you moved. Over a minute, your hand and
the cursor stop agreeing about where they are. The palm-open clutch exists
partly for this: open your palm, put your hand back where it's comfortable,
and point again.

There's a fix — `[hid] pointer = "absolute"` in `config.toml` maps your hand
straight onto the screen with no acceleration — but **it is not yet verified on
macOS**, and switching to it requires forgetting and re-pairing the device.
Don't change it before a demo you care about.

**Scrolling is notchy.** macOS quantizes wheel input to lines and gives no
momentum to an external device. Not fixable from this side.

**It runs at about 9 frames per second.** Everything is timed in seconds rather
than frames, so it degrades gracefully, but fast gestures can be missed.

---

## What's actually been proven

So you know what to trust:

- **141 automated tests** pass on the Pi, including our HID bytes checked
  against the Linux kernel's own HID parser, and every gesture replayed through
  nine real recordings.
- **The mouse button can never be left held down** — four independent guards,
  each tested.
- **The Bluetooth link works**: pairs, connects, reconnects by itself after a
  drop, and the cursor has been observed moving from the Pi's reports.
- **Not yet proven:** that hand movement maps *accurately* to cursor movement.
  Delivery is confirmed; precision isn't measured. That's the next piece of
  work, and it's why the drift note above is a known rather than a bug.
