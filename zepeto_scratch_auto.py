#!/usr/bin/env python3
"""
ZEPETO Scratch Card Automation v2.0
=====================================
Automates the ZEPETO scratch card mini-game loop:
  1. Tap "AD One more time!" to start an ad
  2. Wait for ad to finish, then close it
  3. Scratch the card with swipe gestures
  4. Collect reward
  5. Repeat

Features:
  - Live PC monitor window showing phone screen + status
  - Reward counter and session statistics
  - Fully automatic infinite loop

Requirements:
  - Python 3.7+
  - ADB (Android Debug Bridge) installed
  - Phone connected via USB with USB debugging enabled
  - pip install opencv-python numpy

Usage:
  python zepeto_scratch_auto.py              # Run with monitor window
  python zepeto_scratch_auto.py --no-gui     # Run without monitor (terminal only)
  python zepeto_scratch_auto.py --loops 10   # Run 10 cycles then stop
"""

import subprocess
import time
import os
import sys
import argparse
import random
import tempfile
import logging
import threading
from pathlib import Path
from datetime import datetime, timedelta

try:
    import cv2
    import numpy as np
except ImportError:
    print("ERROR: OpenCV and NumPy are required.")
    print("Install them with: pip install opencv-python numpy")
    sys.exit(1)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

SCRIPT_DIR = Path(__file__).parent.resolve()
TEMPLATES_DIR = SCRIPT_DIR / "templates"

# Template matching confidence threshold (0.0 - 1.0)
MATCH_THRESHOLD = 0.65

# Timing (seconds)
SCREENSHOT_INTERVAL = 1.5     # Time between screen checks
AD_WAIT_TIMEOUT = 60          # Max seconds to wait for ad to finish
SCRATCH_DURATION = 3          # Seconds spent scratching
POST_ACTION_DELAY = 2         # Wait after tapping a button

# Scratch area (relative to screen - will be scaled)
SCRATCH_AREA = {
    "left": 0.10,
    "top": 0.25,
    "right": 0.90,
    "bottom": 0.65
}

# Common ad close button locations (relative coordinates)
AD_CLOSE_POSITIONS = [
    (0.93, 0.05),   # Top-right corner
    (0.07, 0.05),   # Top-left corner
    (0.93, 0.08),   # Slightly lower top-right
    (0.07, 0.08),   # Slightly lower top-left
    (0.50, 0.92),   # Bottom center
    (0.93, 0.12),   # Even lower top-right
    (0.07, 0.12),   # Even lower top-left
]

# Monitor window settings
MONITOR_WIDTH = 360           # Width of the phone preview in monitor
MONITOR_BG_COLOR = (30, 30, 30)  # Dark background
STATUS_COLORS = {
    "DETECT":    (200, 200, 200),  # Gray
    "TAP_AD":    (100, 200, 255),  # Orange
    "WATCH_AD":  (0, 165, 255),    # Yellow-orange
    "CLOSE_AD":  (0, 100, 255),    # Red-orange
    "SCRATCH":   (0, 255, 100),    # Green
    "COLLECT":   (0, 255, 255),    # Yellow
}

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S"
)
log = logging.getLogger("zepeto-auto")

# ---------------------------------------------------------------------------
# ADB Helper Functions
# ---------------------------------------------------------------------------

def run_adb(cmd, timeout=10):
    """Run an ADB command and return output."""
    full_cmd = f"adb {cmd}"
    try:
        result = subprocess.run(
            full_cmd, shell=True, capture_output=True, timeout=timeout
        )
        return result.stdout
    except subprocess.TimeoutExpired:
        log.warning(f"ADB command timed out: {full_cmd}")
        return b""

def check_device():
    """Check if an Android device is connected via ADB."""
    output = run_adb("devices").decode()
    lines = [l for l in output.strip().split("\n")[1:] if "device" in l and "offline" not in l]
    if not lines:
        log.error("No Android device found! Make sure:")
        log.error("  1. USB debugging is enabled on your phone")
        log.error("  2. Phone is connected via USB")
        log.error("  3. You authorized the computer on your phone")
        return False
    device_id = lines[0].split("\t")[0]
    log.info(f"Connected device: {device_id}")
    return True

def get_screen_size():
    """Get the device screen resolution."""
    output = run_adb("shell wm size").decode().strip()
    for line in output.split("\n"):
        if "size" in line.lower():
            parts = line.split(":")[-1].strip().split("x")
            if len(parts) == 2:
                w, h = int(parts[0]), int(parts[1])
                log.info(f"Screen size: {w}x{h}")
                return w, h
    log.warning("Could not detect screen size, using default 1080x2400")
    return 1080, 2400

def take_screenshot():
    """Capture the device screen and return as numpy array."""
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
        tmp_path = f.name

    try:
        run_adb("shell screencap -p /sdcard/_zepeto_tmp.png", timeout=5)
        run_adb(f"pull /sdcard/_zepeto_tmp.png {tmp_path}", timeout=5)
        run_adb("shell rm /sdcard/_zepeto_tmp.png", timeout=3)

        if os.path.exists(tmp_path) and os.path.getsize(tmp_path) > 0:
            img = cv2.imread(tmp_path)
            if img is not None:
                return img
    finally:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)

    log.warning("Failed to capture screenshot")
    return None

def tap(x, y):
    """Tap at screen coordinates with slight randomness."""
    rx = x + random.randint(-5, 5)
    ry = y + random.randint(-5, 5)
    log.debug(f"Tap: ({rx}, {ry})")
    run_adb(f"shell input tap {rx} {ry}")

def swipe(x1, y1, x2, y2, duration_ms=300):
    """Swipe from (x1,y1) to (x2,y2)."""
    run_adb(f"shell input swipe {x1} {y1} {x2} {y2} {duration_ms}")

def press_back():
    """Press the Android back button."""
    run_adb("shell input keyevent 4")

# ---------------------------------------------------------------------------
# Template Matching
# ---------------------------------------------------------------------------

class TemplateMatcher:
    def __init__(self, templates_dir):
        self.templates = {}
        self.load_templates(templates_dir)

    def load_templates(self, tdir):
        tdir = Path(tdir)
        if not tdir.exists():
            log.error(f"Templates directory not found: {tdir}")
            return
        for f in tdir.glob("*.png"):
            name = f.stem
            img = cv2.imread(str(f))
            if img is not None:
                self.templates[name] = img
                log.info(f"  Loaded template: {name} ({img.shape[1]}x{img.shape[0]})")

    def find(self, screen, template_name, threshold=MATCH_THRESHOLD):
        if template_name not in self.templates:
            return None

        template = self.templates[template_name]
        screen_gray = cv2.cvtColor(screen, cv2.COLOR_BGR2GRAY)
        tmpl_gray = cv2.cvtColor(template, cv2.COLOR_BGR2GRAY)

        best_match = None
        best_val = 0
        sh, sw = screen_gray.shape[:2]
        th, tw = tmpl_gray.shape[:2]

        for scale in [1.0, 0.8, 1.2, 0.6, 1.5]:
            scaled_w = int(tw * scale)
            scaled_h = int(th * scale)
            if scaled_w >= sw or scaled_h >= sh or scaled_w < 10 or scaled_h < 10:
                continue
            scaled_tmpl = cv2.resize(tmpl_gray, (scaled_w, scaled_h))
            result = cv2.matchTemplate(screen_gray, scaled_tmpl, cv2.TM_CCOEFF_NORMED)
            _, max_val, _, max_loc = cv2.minMaxLoc(result)
            if max_val > best_val:
                best_val = max_val
                cx = max_loc[0] + scaled_w // 2
                cy = max_loc[1] + scaled_h // 2
                best_match = (cx, cy, max_val)

        if best_match and best_match[2] >= threshold:
            return best_match
        return None

# ---------------------------------------------------------------------------
# Color-based Detection (backup)
# ---------------------------------------------------------------------------

def detect_blue_button(screen, y_range=(0.7, 0.95)):
    h, w = screen.shape[:2]
    y_start = int(h * y_range[0])
    y_end = int(h * y_range[1])
    roi = screen[y_start:y_end, :]
    hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
    lower_blue = np.array([100, 50, 100])
    upper_blue = np.array([140, 255, 255])
    mask = cv2.inRange(hsv, lower_blue, upper_blue)
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if contours:
        largest = max(contours, key=cv2.contourArea)
        area = cv2.contourArea(largest)
        if area > (w * 0.3) * 20:
            M = cv2.moments(largest)
            if M["m00"] > 0:
                cx = int(M["m10"] / M["m00"])
                cy = int(M["m01"] / M["m00"]) + y_start
                return (cx, cy)
    return None

def detect_ad_screen(screen):
    h, w = screen.shape[:2]
    top_roi = screen[0:int(h*0.15), :]
    gray = cv2.cvtColor(top_roi, cv2.COLOR_BGR2GRAY)
    dark_ratio = np.sum(gray < 50) / gray.size
    return dark_ratio > 0.5

def detect_scratch_screen(screen):
    hsv = cv2.cvtColor(screen, cv2.COLOR_BGR2HSV)
    lower_pink = np.array([140, 50, 150])
    upper_pink = np.array([170, 255, 255])
    mask = cv2.inRange(hsv, lower_pink, upper_pink)
    pink_ratio = np.sum(mask > 0) / mask.size
    return pink_ratio > 0.15

# ---------------------------------------------------------------------------
# Live Monitor Window
# ---------------------------------------------------------------------------

class MonitorWindow:
    """Shows a live status dashboard on the PC screen."""

    STATE_LABELS = {
        "DETECT":    "Detecting...",
        "TAP_AD":    "Tapping AD Button",
        "WATCH_AD":  "Watching Ad",
        "CLOSE_AD":  "Closing Ad",
        "SCRATCH":   "Scratching Card!",
        "COLLECT":   "Collecting Reward",
    }

    STATE_LABELS_JP = {
        "DETECT":    "検出中...",
        "TAP_AD":    "広告ボタンをタップ",
        "WATCH_AD":  "広告視聴中",
        "CLOSE_AD":  "広告を閉じる",
        "SCRATCH":   "スクラッチ中！",
        "COLLECT":   "報酬獲得",
    }

    def __init__(self):
        self.enabled = True
        self.window_name = "ZEPETO Auto Monitor"
        self.last_screen = None
        self.state = "DETECT"
        self.loops_done = 0
        self.start_time = datetime.now()
        self.ad_elapsed = 0
        self.log_lines = []
        self.max_log_lines = 12

    def add_log(self, msg):
        timestamp = datetime.now().strftime("%H:%M:%S")
        self.log_lines.append(f"[{timestamp}] {msg}")
        if len(self.log_lines) > self.max_log_lines:
            self.log_lines = self.log_lines[-self.max_log_lines:]

    def update(self, screen, state, loops_done, ad_elapsed=0):
        if not self.enabled:
            return
        self.last_screen = screen
        self.state = state
        self.loops_done = loops_done
        self.ad_elapsed = ad_elapsed
        self._render()

    def _render(self):
        try:
            # Phone preview dimensions
            phone_w = MONITOR_WIDTH
            if self.last_screen is not None:
                h, w = self.last_screen.shape[:2]
                phone_h = int(phone_w * h / w)
                phone_img = cv2.resize(self.last_screen, (phone_w, phone_h))
            else:
                phone_h = int(phone_w * 2.1)
                phone_img = np.zeros((phone_h, phone_w, 3), dtype=np.uint8)

            # Status panel width
            panel_w = 340
            total_w = phone_w + panel_w + 20  # 20px gap
            total_h = max(phone_h + 20, 600)

            # Create canvas
            canvas = np.full((total_h, total_w, 3), MONITOR_BG_COLOR, dtype=np.uint8)

            # Draw phone preview (left side)
            y_offset = 10
            canvas[y_offset:y_offset+phone_h, 10:10+phone_w] = phone_img

            # Draw border around phone
            cv2.rectangle(canvas, (9, y_offset-1), (11+phone_w, y_offset+phone_h+1),
                         (100, 100, 100), 1)

            # ---- Status Panel (right side) ----
            px = phone_w + 25  # Panel x start
            py = 20            # Panel y start

            # Title
            cv2.putText(canvas, "ZEPETO Auto", (px, py),
                       cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255, 255, 255), 2)
            py += 15
            cv2.putText(canvas, "Scratch Monitor", (px, py + 20),
                       cv2.FONT_HERSHEY_SIMPLEX, 0.6, (180, 180, 180), 1)
            py += 50

            # Divider
            cv2.line(canvas, (px, py), (px + panel_w - 30, py), (80, 80, 80), 1)
            py += 20

            # Current State
            state_color = STATUS_COLORS.get(self.state, (200, 200, 200))
            state_label = self.STATE_LABELS.get(self.state, self.state)
            state_label_jp = self.STATE_LABELS_JP.get(self.state, "")

            cv2.putText(canvas, "STATUS:", (px, py),
                       cv2.FONT_HERSHEY_SIMPLEX, 0.45, (150, 150, 150), 1)
            py += 25
            cv2.putText(canvas, state_label, (px + 5, py),
                       cv2.FONT_HERSHEY_SIMPLEX, 0.6, state_color, 2)
            py += 22
            cv2.putText(canvas, state_label_jp, (px + 5, py),
                       cv2.FONT_HERSHEY_SIMPLEX, 0.45, state_color, 1)
            py += 35

            # State indicator circle
            circle_x = px + panel_w - 50
            circle_y = py - 55
            cv2.circle(canvas, (circle_x, circle_y), 12, state_color, -1)

            # Scratches completed
            cv2.putText(canvas, "SCRATCHES:", (px, py),
                       cv2.FONT_HERSHEY_SIMPLEX, 0.45, (150, 150, 150), 1)
            py += 30
            cv2.putText(canvas, str(self.loops_done), (px + 5, py),
                       cv2.FONT_HERSHEY_SIMPLEX, 1.2, (0, 255, 200), 2)
            py += 40

            # Runtime
            runtime = datetime.now() - self.start_time
            hours, remainder = divmod(int(runtime.total_seconds()), 3600)
            minutes, seconds = divmod(remainder, 60)
            runtime_str = f"{hours:02d}:{minutes:02d}:{seconds:02d}"

            cv2.putText(canvas, "RUNTIME:", (px, py),
                       cv2.FONT_HERSHEY_SIMPLEX, 0.45, (150, 150, 150), 1)
            py += 25
            cv2.putText(canvas, runtime_str, (px + 5, py),
                       cv2.FONT_HERSHEY_SIMPLEX, 0.6, (200, 200, 200), 1)
            py += 30

            # Ad timer (if watching ad)
            if self.state in ("WATCH_AD", "CLOSE_AD") and self.ad_elapsed > 0:
                cv2.putText(canvas, "AD TIMER:", (px, py),
                           cv2.FONT_HERSHEY_SIMPLEX, 0.45, (150, 150, 150), 1)
                py += 25
                ad_str = f"{self.ad_elapsed:.0f}s"
                cv2.putText(canvas, ad_str, (px + 5, py),
                           cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 165, 255), 1)
                py += 30

            # Divider
            cv2.line(canvas, (px, py), (px + panel_w - 30, py), (80, 80, 80), 1)
            py += 15

            # Activity log
            cv2.putText(canvas, "ACTIVITY LOG:", (px, py),
                       cv2.FONT_HERSHEY_SIMPLEX, 0.4, (150, 150, 150), 1)
            py += 20

            for line in self.log_lines[-8:]:
                # Truncate long lines
                display = line[:45] + "..." if len(line) > 48 else line
                cv2.putText(canvas, display, (px, py),
                           cv2.FONT_HERSHEY_SIMPLEX, 0.32, (140, 140, 140), 1)
                py += 16

            # Bottom: instructions
            bottom_y = total_h - 15
            cv2.putText(canvas, "Press 'Q' to quit | Ctrl+C in terminal",
                       (10, bottom_y), cv2.FONT_HERSHEY_SIMPLEX, 0.35,
                       (100, 100, 100), 1)

            # Show window
            cv2.imshow(self.window_name, canvas)
            key = cv2.waitKey(1) & 0xFF
            if key == ord('q') or key == ord('Q'):
                raise KeyboardInterrupt("User pressed Q")

        except cv2.error:
            # GUI not available (headless mode)
            self.enabled = False

    def close(self):
        if self.enabled:
            try:
                cv2.destroyAllWindows()
            except cv2.error:
                pass

# ---------------------------------------------------------------------------
# Main Automation
# ---------------------------------------------------------------------------

class ZepetoAutomation:
    """State machine for ZEPETO scratch card automation."""

    STATE_DETECT = "DETECT"
    STATE_TAP_AD = "TAP_AD"
    STATE_WATCH_AD = "WATCH_AD"
    STATE_CLOSE_AD = "CLOSE_AD"
    STATE_SCRATCH = "SCRATCH"
    STATE_COLLECT = "COLLECT"

    def __init__(self, max_loops=0, extra_delay=0, use_gui=True):
        self.matcher = TemplateMatcher(TEMPLATES_DIR)
        self.screen_w, self.screen_h = get_screen_size()
        self.state = self.STATE_DETECT
        self.max_loops = max_loops
        self.loops_done = 0
        self.extra_delay = extra_delay
        self.ad_wait_start = 0
        self.consecutive_detect_fails = 0
        self.start_time = datetime.now()

        # Monitor window
        if use_gui:
            self.monitor = MonitorWindow()
        else:
            self.monitor = None

    def run(self):
        log.info("=" * 50)
        log.info("ZEPETO Scratch Card Automation v2.0")
        log.info(f"Screen: {self.screen_w}x{self.screen_h}")
        log.info(f"Max loops: {'infinite' if self.max_loops == 0 else self.max_loops}")
        if self.monitor:
            log.info("Monitor window: ENABLED (press Q to quit)")
        log.info("Press Ctrl+C to stop at any time")
        log.info("=" * 50)

        try:
            while True:
                if self.max_loops > 0 and self.loops_done >= self.max_loops:
                    log.info(f"Completed {self.loops_done} loops. Stopping.")
                    break

                screen = take_screenshot()
                if screen is None:
                    log.warning("Screenshot failed, retrying...")
                    self._update_monitor(None)
                    time.sleep(2)
                    continue

                self.process_state(screen)
                self._update_monitor(screen)
                time.sleep(SCREENSHOT_INTERVAL + self.extra_delay)

        except KeyboardInterrupt:
            log.info("\nStopped by user")

        self.print_summary()
        if self.monitor:
            self.monitor.close()

    def _update_monitor(self, screen):
        if self.monitor:
            ad_elapsed = 0
            if self.state in (self.STATE_WATCH_AD, self.STATE_CLOSE_AD) and self.ad_wait_start > 0:
                ad_elapsed = time.time() - self.ad_wait_start
            self.monitor.update(screen, self.state, self.loops_done, ad_elapsed)

    def _log_and_monitor(self, msg):
        log.info(msg)
        if self.monitor:
            # Strip the [STATE] prefix for cleaner monitor display
            clean = msg.split("] ", 1)[-1] if "] " in msg else msg
            self.monitor.add_log(clean)

    def process_state(self, screen):
        if self.state == self.STATE_DETECT:
            self.do_detect(screen)
        elif self.state == self.STATE_TAP_AD:
            self.do_tap_ad(screen)
        elif self.state == self.STATE_WATCH_AD:
            self.do_watch_ad(screen)
        elif self.state == self.STATE_CLOSE_AD:
            self.do_close_ad(screen)
        elif self.state == self.STATE_SCRATCH:
            self.do_scratch(screen)
        elif self.state == self.STATE_COLLECT:
            self.do_collect(screen)

    def do_detect(self, screen):
        self._log_and_monitor("[DETECT] Analyzing screen...")

        # Check for "AD One more time!" button (reward screen)
        match = self.matcher.find(screen, "ad_one_more_time")
        if match:
            self._log_and_monitor(f"[DETECT] Found AD button (conf: {match[2]:.2f})")
            self.state = self.STATE_TAP_AD
            self.consecutive_detect_fails = 0
            return

        # Backup: detect blue button
        blue_btn = detect_blue_button(screen)
        if blue_btn:
            self._log_and_monitor(f"[DETECT] Found blue button")
            self.state = self.STATE_TAP_AD
            self.consecutive_detect_fails = 0
            return

        # Check scratch card screen (pink background)
        if detect_scratch_screen(screen):
            self._log_and_monitor("[DETECT] Scratch card screen!")
            self.state = self.STATE_SCRATCH
            self.consecutive_detect_fails = 0
            return

        # Check ad screen
        if detect_ad_screen(screen):
            self._log_and_monitor("[DETECT] Ad screen detected")
            self.state = self.STATE_WATCH_AD
            self.ad_wait_start = time.time()
            self.consecutive_detect_fails = 0
            return

        # Check confirmation button
        match = self.matcher.find(screen, "confirmation")
        if match:
            self._log_and_monitor("[DETECT] Found confirmation - looking for AD btn")
            self.state = self.STATE_TAP_AD
            self.consecutive_detect_fails = 0
            return

        self.consecutive_detect_fails += 1
        log.warning(f"[DETECT] Unknown screen (attempt {self.consecutive_detect_fails})")

        if self.consecutive_detect_fails >= 5:
            self._log_and_monitor("[DETECT] Trying back button...")
            press_back()
            time.sleep(2)
            self.consecutive_detect_fails = 0

    def do_tap_ad(self, screen):
        self._log_and_monitor("[TAP_AD] Tapping AD button...")

        match = self.matcher.find(screen, "ad_one_more_time")
        if match:
            tap(match[0], match[1])
            self._log_and_monitor(f"[TAP_AD] Tapped at ({match[0]}, {match[1]})")
            self.state = self.STATE_WATCH_AD
            self.ad_wait_start = time.time()
            time.sleep(POST_ACTION_DELAY)
            return

        blue_btn = detect_blue_button(screen)
        if blue_btn:
            tap(blue_btn[0], blue_btn[1])
            self._log_and_monitor(f"[TAP_AD] Tapped blue button")
            self.state = self.STATE_WATCH_AD
            self.ad_wait_start = time.time()
            time.sleep(POST_ACTION_DELAY)
            return

        # Fallback position
        btn_x = self.screen_w // 2
        btn_y = int(self.screen_h * 0.82)
        tap(btn_x, btn_y)
        self._log_and_monitor(f"[TAP_AD] Tapped estimated position")
        self.state = self.STATE_WATCH_AD
        self.ad_wait_start = time.time()
        time.sleep(POST_ACTION_DELAY)

    def do_watch_ad(self, screen):
        elapsed = time.time() - self.ad_wait_start
        self._log_and_monitor(f"[WATCH_AD] Ad playing... {elapsed:.0f}s")

        if not detect_ad_screen(screen):
            if detect_scratch_screen(screen):
                self._log_and_monitor("[WATCH_AD] Scratch screen appeared!")
                self.state = self.STATE_SCRATCH
                return

            match = self.matcher.find(screen, "ad_one_more_time")
            if match:
                self._log_and_monitor("[WATCH_AD] Back to reward screen")
                self.state = self.STATE_TAP_AD
                return

        if elapsed > AD_WAIT_TIMEOUT:
            self._log_and_monitor("[WATCH_AD] Timeout! Trying to close...")
            self.state = self.STATE_CLOSE_AD
            return

        if elapsed > 35:
            self._log_and_monitor("[WATCH_AD] Timer should be done, closing...")
            self.state = self.STATE_CLOSE_AD

    def do_close_ad(self, screen):
        self._log_and_monitor("[CLOSE_AD] Closing ad...")

        if detect_scratch_screen(screen):
            self._log_and_monitor("[CLOSE_AD] Scratch screen found!")
            self.state = self.STATE_SCRATCH
            return

        match = self.matcher.find(screen, "ad_one_more_time")
        if match:
            self._log_and_monitor("[CLOSE_AD] Reward screen found!")
            self.state = self.STATE_TAP_AD
            return

        for i, (rx, ry) in enumerate(AD_CLOSE_POSITIONS):
            x = int(self.screen_w * rx)
            y = int(self.screen_h * ry)
            tap(x, y)
            self._log_and_monitor(f"[CLOSE_AD] Try X position {i+1}")
            time.sleep(0.8)

            quick_screen = take_screenshot()
            if quick_screen is not None:
                if detect_scratch_screen(quick_screen):
                    self._log_and_monitor("[CLOSE_AD] Ad closed! Scratch time!")
                    self.state = self.STATE_SCRATCH
                    return
                if not detect_ad_screen(quick_screen):
                    self._log_and_monitor("[CLOSE_AD] Ad closed")
                    self.state = self.STATE_DETECT
                    return

        self._log_and_monitor("[CLOSE_AD] Trying back button...")
        press_back()
        time.sleep(1)
        self.state = self.STATE_DETECT

    def do_scratch(self, screen):
        self._log_and_monitor("[SCRATCH] Scratching the card...")

        left = int(self.screen_w * SCRATCH_AREA["left"])
        top = int(self.screen_h * SCRATCH_AREA["top"])
        right = int(self.screen_w * SCRATCH_AREA["right"])
        bottom = int(self.screen_h * SCRATCH_AREA["bottom"])

        # Horizontal swipes
        num_swipes = 8
        for i in range(num_swipes):
            y = top + (bottom - top) * i // num_swipes + random.randint(-10, 10)
            if i % 2 == 0:
                swipe(left, y, right, y, 200)
            else:
                swipe(right, y, left, y, 200)
            time.sleep(0.15)

        # Vertical swipes
        for i in range(4):
            x = left + (right - left) * i // 4 + random.randint(-10, 10)
            swipe(x, top, x, bottom, 200)
            time.sleep(0.15)

        # Diagonal swipes
        swipe(left, top, right, bottom, 300)
        time.sleep(0.15)
        swipe(right, top, left, bottom, 300)
        time.sleep(0.15)

        time.sleep(2)
        self.loops_done += 1
        self._log_and_monitor(f"[SCRATCH] Done! Card #{self.loops_done} completed!")
        self.state = self.STATE_DETECT

    def do_collect(self, screen):
        self._log_and_monitor("[COLLECT] Reward screen - continuing loop")
        self.state = self.STATE_TAP_AD

    def print_summary(self):
        runtime = datetime.now() - self.start_time
        hours, remainder = divmod(int(runtime.total_seconds()), 3600)
        minutes, seconds = divmod(remainder, 60)

        log.info("")
        log.info("=" * 50)
        log.info("       SESSION SUMMARY / セッション結果")
        log.info("=" * 50)
        log.info(f"  Scratch cards completed: {self.loops_done}")
        log.info(f"  Total runtime: {hours:02d}h {minutes:02d}m {seconds:02d}s")
        if self.loops_done > 0 and runtime.total_seconds() > 0:
            avg = runtime.total_seconds() / self.loops_done
            log.info(f"  Average per card: {avg:.1f}s")
        log.info("=" * 50)

# ---------------------------------------------------------------------------
# Main Entry Point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="ZEPETO Scratch Card Automation v2.0",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python zepeto_scratch_auto.py              # Run with monitor window
  python zepeto_scratch_auto.py --no-gui     # Terminal only (no window)
  python zepeto_scratch_auto.py --loops 10   # Run 10 cycles then stop
  python zepeto_scratch_auto.py --delay 1    # Extra 1s delay between actions
        """
    )
    parser.add_argument("--loops", type=int, default=0,
                       help="Number of cycles (0 = infinite)")
    parser.add_argument("--delay", type=float, default=0,
                       help="Extra delay between actions (seconds)")
    parser.add_argument("--no-gui", action="store_true",
                       help="Disable monitor window (terminal only)")
    parser.add_argument("-v", "--verbose", action="store_true",
                       help="Verbose logging")
    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    log.info("ZEPETO Scratch Card Automation v2.0")
    log.info("-" * 40)

    if not check_device():
        sys.exit(1)

    if not TEMPLATES_DIR.exists():
        log.error(f"Templates directory not found: {TEMPLATES_DIR}")
        sys.exit(1)

    log.info("Loading templates...")

    auto = ZepetoAutomation(
        max_loops=args.loops,
        extra_delay=args.delay,
        use_gui=not args.no_gui
    )
    auto.run()

if __name__ == "__main__":
    main()
