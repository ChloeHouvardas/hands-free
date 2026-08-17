# Running a live demo

Everything here assumes the Pi is on and reachable. Do the setup **before**
anyone is watching — most of what can go wrong goes wrong at start-up, not
during.

## The short version

```sh
ssh chloewashere@192.168.2.188
cd ~/Code/hands-free && git pull
sudo venv/bin/python -m handsfree run --no-preview
```

That one command advertises, accepts pairing, connects, and starts tracking.
There is no separate pairing step — `run` registers the Bluetooth agent itself,
so if the Mac isn't paired yet you can pair it while `run` is going.

---

## Twenty minutes before

**1. Wake the Pi and confirm it's there.** It has gone missing twice, both
times because it was simply off.

```sh
ping -c1 192.168.2.188 && ssh chloewashere@192.168.2.188 uptime
```

**2. Run the suites.** Ninety seconds, and it catches a bad pull.

```sh
sudo venv/bin/python -m handsfree selftest
```

**3. Check the vision half.** Hold your hand still for sixty seconds.

```sh
sudo venv/bin/python -m handsfree doctor --seconds 60
```

Look at **drops/min**. Under 1 is steady; over 4 and gestures will feel like
they randomly stop working, because every threshold downstream assumes the hand
stays found. If it's high, that's the thing to fix — not the gesture tuning.

Also check `exposure` and `gain` say `locked`. If they say `STILL HUNTING`, the
camera is re-metering mid-session and the model is seeing the brightness change
underneath it.

**4. Pair, if it isn't already.** Start `run`, then System Settings →
Bluetooth → pair `Hands-Free`. It reconnects by itself afterwards, including
after a reboot.

**5. Rehearse the actual sequence** you're going to perform. Once, fully.

---

## The gestures, in the order worth demoing

Point first — that's what takes control, deliberately, so that a hand resting
in view can't do anything.

| Do | Expect |
| --- | --- |
| Hold your hand still in frame | nothing at all — this is the point |
| Point, hold about a second | cursor starts following |
| Move around | cursor follows your palm |
| Open your palm | cursor stops **dead** — the clutch |
| Move your hand somewhere else, point again | cursor resumes where it was, no jump |
| Pinch and release | one click |
| Pinch, move, release | a drag |
| Two fingers, move up and down | scrolls |
| Three fingers, sweep sideways | switches Space |

**Lead with the clutch.** Park, move your hand right across the frame, point
again — the cursor doesn't move until you point. That's the idea the whole
design is built around and it's the one that isn't obvious.

**Finish on resting your hand in frame and doing nothing.** "It ignores me
until I ask" is a stronger claim than any gesture, and it's the hard part.

---

## What will go wrong, and what to do

| Symptom | Cause | Do |
| --- | --- | --- |
| Cursor doesn't move at all | not connected | `run` prints the link state; look for `HOST DISCONNECTED` |
| Cursor moves but wrongly | wrong pointer mode | `[hid] pointer` — try `absolute` |
| Gestures work then stop for a few seconds | detection dropping | run `doctor`; check framing and lighting |
| Swipes don't fire | recall is ~6 of 7 | sweep further and more decisively; repeat it |
| A click you didn't ask for | a closing fist read as a pinch | `[pinch] index_out` — raise it |
| "Device or resource busy" | a previous run still holds the camera | `pgrep -af 'python.*handsfree'` and kill it |
| Nothing arrives after a code change | macOS cached the old HID descriptor | forget the device on the Mac, pair again |
| Pi vanishes from the network | it's off, or Wi-Fi dropped | check the power light; `arp -a \| grep chloespie` |

**The one that needs explaining if it happens:** macOS caches the HID
descriptor at pair time. Any change to `hid.py`'s descriptor means the Mac is
using a stale copy and will silently ignore everything. Forget and re-pair.

---

## Things to say when they happen

- **A dropped gesture** — the vision runs at about 9 fps on a Pi 4; everything
  is time-gated rather than frame-counted so it degrades gracefully, but it
  does degrade.
- **Notchy scrolling** — macOS quantizes wheel input to lines and gives no
  momentum to an external device. Not fixable from this side.
- **Nothing installed on the Mac** — worth saying out loud, because it's the
  actual claim. It pairs as a Bluetooth mouse and keyboard. It would work the
  same on a machine you'd never seen before.

---

## Don't

- Don't run with `--preview` during the demo. Wi-Fi and Bluetooth share one
  antenna and one chip on a Pi 4, and the MJPEG stream competes with the cursor.
- Don't change `config.toml` between the rehearsal and the demo.
- Don't re-pair unless you have to; it's the most fragile step.
- Don't demo in different lighting from the one you ran `doctor` in — exposure
  is locked at start-up, deliberately.
