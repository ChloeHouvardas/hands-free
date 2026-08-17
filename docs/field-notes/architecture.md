# Architecture

```mermaid
flowchart LR
    subgraph pi["Raspberry Pi 4 · 2GB"]
        direction TB
        cam["Camera Module 3<br/><i>CSI ribbon</i>"]
        pc["picamera2<br/><i>frame capture</i>"]
        mp["MediaPipe Hand Landmarker<br/><i>21 landmarks</i>"]
        gs["handsfree.gestures<br/><i>landmark geometry</i>"]
        hid["USB HID gadget<br/><i>dwc2 + ConfigFS</i>"]
        cam --> pc --> mp --> gs --> hid
    end
    hid -->|"USB-C · mouse reports"| host["Laptop<br/><i>no driver, no app</i>"]
```

| | |
| --- | --- |
| Hardware | Pi 4 (2GB), Camera Module 3 |
| Capture | picamera2 |
| Inference | MediaPipe Hand Landmarker + `hand_landmarker.task` |
| Gestures | numpy geometry over landmarks |
| Output | Linux USB HID gadget |

# Implementation flow

```mermaid
flowchart TD
    A["0 · git init, first commit"] --> B["1 · SSH into the Pi"]
    B --> C["2 · venv + pinned deps"]
    C --> D["3 · capture.py<br/>frames only, no ML"]
    D --> E["4 · landmarks.py<br/>21 points drawn"]
    E --> F["5 · bench.py<br/>FPS, latency, CPU"]
    F --> G["6 · gestures.py<br/>pinch fires"]
    G --> H{"fast enough?"}
    H -->|yes| I["7 · USB HID gadget"]
    H -->|no| J["optimize<br/>resolution · 1 hand · ncnn"]
    J --> F
    I --> K["v1 · cursor follows hand"]
```

Start at the Pi, not the code — the Python environment is what broke the
official sample, and everything else depends on it.
