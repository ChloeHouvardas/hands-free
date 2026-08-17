# Where we are — Sun 16 Aug

A plug-in device: Pi 4 watches your hand and pretends to be a USB mouse for the
Mac. Nothing installed on the Mac.

Phases A and B are done. C and D are not started.

---

## Picking this up cold

**This file is the entry point.** It's the current state of the project; the
other docs are reference.

### Where things are

| | |
| --- | --- |
| Repo | `~/Documents/CODE/hands-free` on the Mac, branch `main` |
| GitHub | `ChloeHouvardas/hands-free` (public) |
| Pi | `ssh chloewashere@chloespie.local` (keys are set up, no password) |
| On the Pi | `~/Code/hands-free`, venv at `venv/` — `source venv/bin/activate` |
| Recordings | `recordings/*.jsonl`, on both machines, **gitignored** — move with `scp` |

### Read in this order

1. This file — state, open problems, what's next
2. `gestures.py` module docstring — the state machine and the three rules
3. `config.toml` — every threshold, each with a comment on why it's that value
4. `docs/field-notes/decisions.md` — settled tech choices
5. `docs/hardware-setup.md` — only if touching the Pi's setup

### The file map

```
config.py       loads config.toml            hand.py     pure geometry, no imports
capture.py      camera only, no ML           filters.py  one euro filter
landmarks.py    camera + MediaPipe           gestures.py the state machine
preview.py      browser live view            synth.py    generated hands for tests
record.py       capture to JSONL             replay.py   JSONL back through gestures
bench.py        FPS / latency                test_gestures.py
```

Dependency shape — `hand.py`, `filters.py`, `gestures.py`, `synth.py` and the
tests import **nothing hardware**, which is what lets the whole gesture layer be
worked on from a laptop:

```
config.py ← everything
capture.py ← landmarks.py ← record.py, bench.py
hand.py, filters.py ← gestures.py ← replay.py, test_gestures.py
```

### Working style (also in CLAUDE.md, which is gitignored)

- Answer first in a sentence or two. No preamble, no recapping.
- **Check before architectural or hard-to-reverse decisions** — inference
  runtime, model, HID approach, language. Small libraries don't need a
  check-in. When a tech decision comes up, name the alternatives and why they
  lost, then recommend one.
- When a choice is settled, add a row to `docs/field-notes/decisions.md`.
  Leave other docs alone unless asked.
- Docs record what we decided or built, not what we considered.
- Conventional commits: `type: subject`, imperative, lowercase.
  `feat` `fix` `docs` `refactor` `chore`. No co-author trailers.

### The loop that makes this fast

Both run on the Mac with no hardware attached. Use them after **any** threshold
change:

```sh
python3 test_gestures.py                          # 36 synthetic
python3 replay.py 'recordings/*.jsonl' --check    # 9 real clips
python3 replay.py recordings/pinch.jsonl -v       # every event, timestamped
python3 replay.py 'recordings/*.jsonl' --check --drop 1   # half frame rate
```

Recordings keep it honest about real sloppy input; `synth.py` stops it
overfitting to one afternoon's hands. **Don't tune against only one of them** —
that's how the first pass produced a palm that couldn't park.

### Gotchas that cost time already

- **Only one process can hold the camera.** "Device or resource busy" means a
  previous run is alive: `pgrep -af 'python.*(gestures|record|landmarks)'`.
- **`pkill -f gestures.py` over SSH kills its own shell**, because the remote
  command line contains that string. Use `pkill -f "[g]estures.py"`, and don't
  put it on the same line as the thing you're starting.
- **Backgrounding over SSH needs `ssh -f` and `< /dev/null`**, or the process
  dies when the session closes.
- **`mediapipe==0.10.18` is pinned** — the last 0.10.x with an aarch64 wheel.
  0.10.20–0.10.35 are x86_64 only and will not install on the Pi.
- **Bookworm, not Trixie.** Trixie ships Python 3.13 and MediaPipe has no 3.13
  wheel. The Pi was reflashed once over this.
- **picamera2's `"RGB888"` returns BGR-ordered arrays.** If the preview looks
  right but tracking is bad, check this first.
- **Ask before pulling on the Pi.** Doing it silently makes the user's own
  `git pull` say "already up to date", which reads like nothing was committed.

### Asking the user to run something

Suggest they type `! <command>` in the prompt so the output lands in the
conversation. They test live; the agent can SSH for diagnosis.

---

## The shape of it

```
camera -> landmarks -> gestures -> [ transport ] -> Mac
 done      done         done         not built
```

Everything up to "what gesture is this" works and is tested. Nothing yet
touches the Mac — the gesture layer prints what it *would* do.

---

## Phase A — the recording rig (done)

The idea that made everything after it fast: **record hand landmarks to a file
and replay them offline.** Tuning is most of the work, and every threshold
change otherwise needs a human, a camera and a hand. Now it's a test suite.

| File | What it does |
| --- | --- |
| `capture.py` | Opens the camera. No ML, so when things break it's obvious which half is at fault. |
| `landmarks.py` | Camera + MediaPipe → 21 hand landmarks per frame. |
| `preview.py` | Streams the annotated camera view to a browser (the Pi is headless). |
| `record.py` | Guided recording session → `recordings/*.jsonl`. |
| `replay.py` | Feeds a recording back through the gesture layer. |
| `bench.py` | FPS / latency measurement. |

**What we measured.** ~9.5 FPS with a hand in frame, 104 ms/frame. Camera rate
and resolution make no difference — MediaPipe is the entire cost. The hoped-for
~14 FPS from VIDEO mode skipping palm detection never materialised; a hand
present buys about 1.5 FPS over an empty frame.

**Recordings.** Nine clips, ~205 s: `baseline`, `park`, `move`, `pinch`,
`drag`, `scroll`, `swipe`, `nothing` (the control), `lost`. They live on the Pi
and on this Mac in `recordings/`, and are gitignored.

---

## Phase B — the gesture layer (done)

| File | What it does |
| --- | --- |
| `hand.py` | Pure geometry — joint angles, pinch ratio, hand size. No camera, no imports. |
| `filters.py` | One Euro filter: steady when your hand is still, no lag when it moves. |
| `gestures.py` | The state machine. |
| `config.toml` | Every threshold, with a comment saying what it does. |
| `config.py` | Shared config loader. |
| `synth.py` | Builds hands from scratch, so tuning isn't fitted to one recording session. |
| `test_gestures.py` | 36 tests over generated hands. |

### The state machine

```
NO_HAND ──hand appears──> PARKED        cursor frozen, nothing fires
PARKED  ──point 0.45s───> MOVE          cursor follows your palm
MOVE    ──pinch─────────> DRAG          button held down
        ──two fingers───> SCROLL        palm offset sets scroll speed
        ──three fingers─> SWIPE         sideways travel fires once
        ──open palm─────> PARKED
```

### The three rules everything hangs off

All three came from measuring the recordings, not from what seemed sensible.

1. **Pointing is the only way out of PARKED.** An idle hand sits with two or
   three fingers half out. Letting those poses take control caused nearly every
   false click. Point to take over, like putting your hand back on a mouse.
2. **Pinch beats pose.** When you pinch, your index and middle read as
   extended — identical to the two-finger scroll pose. So pinch is checked
   first and wins. Except from PARKED, where a resting thumb sits near the
   index often enough to matter.
3. **Parking wins ties.** It's the clutch, the way you stop. A park that
   doesn't take is much worse than a swipe you have to repeat.

### What the recordings actually taught us

The guessed thresholds in the original code were badly wrong:

- An open hand reads **1.5** on the pinch ratio. The committed threshold was
  0.45, which would have counted almost everything as a pinch.
- **Lazy pointing is index + pinky**, 64% of frames — not index alone. So POINT
  ignores the pinky entirely.
- **An open palm and a three-finger swipe differ only in the pinky**, and the
  pinky is the least reliable landmark on the hand. It now gets a stricter
  angle than the other fingers (166° vs 155°): a swiping pinky drifts to ~159°,
  a spread palm sits at ~174°. The thumb only breaks ties inside that band.
- **A real pinch curls the ring and pinky.** That's what stops a hand resting
  on the desk from clicking.

### Everything is time-gated and scale-free

No frame counts (the rate wanders 8–12 FPS), no pixel distances (your hand
moves closer and further). Seconds, degrees, and multiples of hand size only.
That's also what makes it survive the camera being mounted at an angle.

### Where it stands

- **9/9 recorded clips pass**, and still pass at half the frame rate
- **36/36 synthetic tests pass** — every pose at six rotations and three hand
  sizes, frame rates 7→30, landmark noise well past what the recordings show,
  and 12 seeds of random poses asserting the button is never left held down

Both suites run on the Mac with no hardware:

```sh
python3 test_gestures.py
python3 replay.py 'recordings/*.jsonl' --check
```

---

## Open problems

Roughly in the order they'd hurt.

### 1. Detection flickers — `hand_found` / `hand_lost` about once a second

Seen repeatedly in live runs with nothing deliberately happening. **Every
threshold below assumes a stable hand**, so this makes everything feel
unreliable no matter how it's tuned. Worth fixing before any more tuning.

Not yet investigated: whether it's framing, lighting, the mounting angle, or
something in view being read as a hand.

### 2. The feed is laggy and freezes

Ruled out so far:

- **Not the preview.** JPEG encoding measured at 4.2 ms/frame; a viewer costs
  7.8 → 7.4 FPS.
- **Not thermal.** 51°C, `throttled=0x0`, full 1.8 GHz, load 0.9, RAM fine.

Unexplained: the loop measured **125 ms/frame now vs 104 ms during the
recording session**, with spikes to 219 ms. Needs a clean re-measure with
nothing else running and no SSH sessions attached — several of mine were open
during that measurement, which may have skewed it.

Probably related to #1.

### 3. Releasing a pinch

In the `drag` recording the thumb never fully lets go — the ratio sits around
0.3 for twenty seconds instead of returning to 1.5. Right now that just means
fewer drags detected. Once the HID transport exists **it means a stuck mouse
button**, which makes a Mac unusable. Watch this specifically in Phase C.

The safety nets that exist: hand lost → button released; every state transition
goes through one place that releases it; a test asserts no random pose sequence
can leave it down.

### 4. One false click in 30 s of idle

Down from five. The remaining one is a genuine point-then-thumb-closes, near
indistinguishable from a real click. Acceptable for now, worth revisiting.

### 5. Swipe recall

Detects 5 of ~7 real swipes. Deliberately traded away to make parking reliable.

---

## Phase C — USB HID transport (not started)

Make the Pi enumerate as a **composite keyboard + mouse**, and turn the events
into real input.

### Blocked on hardware — this is the bit to order

The Pi 4's USB-C port is both power input and gadget port, and has no USB-PD
negotiation. Under CV load it draws ~7–8 W against a port advertising 7.5 W.
The failure mode is undervoltage throttling that looks exactly like a
MediaPipe bug. Separately, C-to-C reportedly doesn't enumerate on Macs at all.

Working topology:

- **5 V into the GPIO pins** for power — no protection circuitry on that path,
  so use a good supply
- **USB-A→C data cable with VBUS cut**, Mac end via USB-A
- **Not a Y-splitter** — two 5 V sources back-feed each other

**Validate the physical layer before writing any transport logic.** A marginal
power path shows up as intermittent throttling that destroys tracking and looks
like a software bug.

### Then

- `transport.py` — composite gadget via ConfigFS, writing `/dev/hidg0` and
  `/dev/hidg1`. Handle `BlockingIOError`/EAGAIN, which is normal when no host
  is enumerated.
- Cursor as relative deltas.
- Scroll via Wheel + AC Pan. It will feel notchy — macOS wheel scroll is
  line-quantized and momentum isn't available to a HID mouse. Accept it.
- **Swipe becomes keyboard shortcuts**, because macOS multi-touch gestures are
  impossible from any external device: `Ctrl+←/→` for spaces, `Cmd+Shift+[/]`
  for tabs.

### Things that will surprise you

- Keyboard Setup Assistant pops up on every re-enumeration. Dismissable, or
  pre-populate `com.apple.keyboardtype.plist`.
- Check `Ctrl+←/→` is actually enabled in Keyboard Shortcuts on the target Mac.
- HID latency is ~12 ms against a 100–200 ms vision budget. Ignore it.

---

## Phase D — integration (not started)

- One process on the Pi: capture → landmarks → gestures → transport.
- **The interpolation thread.** Highest-leverage remaining change: a 60–120 Hz
  thread easing the cursor toward the target the vision loop sets at ~9 FPS.
  Hides latency that can't be removed. The engine already keeps the cursor in
  normalized screen space specifically so this can sit in front of it.
- Re-measure FPS and latency to see what the gesture layer cost.
- Tune against real use.
- systemd unit only if it earns its place.

---

## How to run it

On the Pi:

```sh
cd ~/Code/hands-free && git pull
source venv/bin/activate
python gestures.py
```

It prints the live-view URL. The page reconnects itself when you restart the
script. Camera angle lives in `config.toml` (`[camera] rotate = 180`) — change
it there if you remount, `--rotate` overrides for a one-off.

Only one process can hold the camera. If it says busy:

```sh
pgrep -af 'python.*(gestures|record|landmarks)'
```

### A test pass

Point first — that's what takes control.

| Do | Expect |
| --- | --- |
| Hand still in frame | `PARKED`, silence |
| Point, hold ~1 s | `MOVE`, dot follows |
| Open palm, move, point again | dot stays put (the clutch) |
| Pinch and release | `click_down`, `click_up` |
| Pinch, hold, move, release | one down, movement, one up |
| Two fingers, move up/down | `scroll(+n)` / `scroll(-n)` |
| Three fingers, sweep sideways | one `swipe(left/right)` |
| Rest your hand, potter, type | **nothing at all** |
| Pinch, then yank hand out of frame | `click_up` *then* `hand_lost` |

The last two matter most: the first is what the whole design is tuned around,
the second is the stuck-button safety case.

---

## Decisions so far

`docs/field-notes/decisions.md` — one row per settled choice.

## Hardware

`docs/hardware-setup.md` — flashing, camera, SSH, Pi Connect.
