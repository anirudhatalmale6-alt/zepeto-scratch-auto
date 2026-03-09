#!/usr/bin/env python3
"""
ZEPETO Scratch Card Automation
===============================
Automates the ZEPETO scratch card mini-game loop:
  1. Tap "AD One more time!" to start an ad
  2. Wait for ad to finish, then close it
  3. Scratch the card with swipe gestures
  4. Collect reward
  5. Repeat

Requirements:
  - Python 3.7+
  - ADB (Android Debug Bridge) installed
  - Phone connected via USB with USB debugging enabled
  - OpenCV: pip install opencv-python
  - NumPy: pip install numpy

Usage:
  python zepeto_scratch_auto.py [--loops N] [--delay SECONDS]
"""

import subprocess
import time
import os
import sys
import argparse
import random
import tempfile
import logging
from pathlib import Path

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
AD_CLOSE_DELAY = 2            # Extra wait after ad timer disappears
SCRATCH_DURATION = 3          # Seconds spent scratching
POST_ACTION_DELAY = 2         # Wait after tapping a button

# Scratch area (relative to screen - will be scaled)
# These are approximate percentages of screen width/height
SCRATCH_AREA = {
    "left": 0.10,   # 10% from left
    "top": 0.25,    # 25% from top
    "right": 0.90,  # 90% from left
    "bottom": 0.65  # 65% from top
}

# Common ad close button locations (relative coordinates)
# We try multiple locations since different ad networks place X differently
AD_CLOSE_POSITIONS = [
    (0.93, 0.05),  # Top-right corner
    (0.07, 0.05),  # Top-left corner
    (0.93, 0.08),  # Slightly lower top-right
    (0.07, 0.08),  # Slightly lower top-left
    (0.50, 0.92),  # Bottom center (some ads have "Close" at bottom)
    (0.93, 0.12),  # Even lower top-right
]

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
    # Output: "Physical size: 1080x2400"
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
        # Capture screenshot on device and pull it
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
    """Tap at screen coordinates."""
    # Add slight randomness to avoid detection
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
        """Load all template images from the templates directory."""
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
            else:
                log.warning(f"  Could not load template: {f}")

    def find(self, screen, template_name, threshold=MATCH_THRESHOLD):
        """
        Search for a template in the screen image.
        Returns (x, y, confidence) of the best match center, or None.
        """
        if template_name not in self.templates:
            return None

        template = self.templates[template_name]
        screen_gray = cv2.cvtColor(screen, cv2.COLOR_BGR2GRAY)
        tmpl_gray = cv2.cvtColor(template, cv2.COLOR_BGR2GRAY)

        # Handle scale differences - try multiple scales
        best_match = None
        best_val = 0

        sh, sw = screen_gray.shape[:2]
        th, tw = tmpl_gray.shape[:2]

        # Try original and a few scaled versions
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
                # Center of match
                cx = max_loc[0] + scaled_w // 2
                cy = max_loc[1] + scaled_h // 2
                best_match = (cx, cy, max_val)

        if best_match and best_match[2] >= threshold:
            return best_match
        return None

# ---------------------------------------------------------------------------
# Color-based Detection (backup method)
# ---------------------------------------------------------------------------

def detect_blue_button(screen, y_range=(0.7, 0.95)):
    """
    Detect a blue/purple button in the lower portion of the screen.
    Used as backup for detecting "AD One more time!" button.
    """
    h, w = screen.shape[:2]
    y_start = int(h * y_range[0])
    y_end = int(h * y_range[1])
    roi = screen[y_start:y_end, :]

    # Convert to HSV
    hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)

    # Blue/purple range (the button appears blue-purple)
    lower_blue = np.array([100, 50, 100])
    upper_blue = np.array([140, 255, 255])
    mask = cv2.inRange(hsv, lower_blue, upper_blue)

    # Find largest contour
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if contours:
        largest = max(contours, key=cv2.contourArea)
        area = cv2.contourArea(largest)
        if area > (w * 0.3) * 20:  # Minimum button size
            M = cv2.moments(largest)
            if M["m00"] > 0:
                cx = int(M["m10"] / M["m00"])
                cy = int(M["m01"] / M["m00"]) + y_start
                return (cx, cy)
    return None

def detect_ad_screen(screen):
    """
    Detect if we're on an ad screen by checking for:
    - Mostly dark/black background (video ad)
    - Timer text in top-right
    """
    h, w = screen.shape[:2]

    # Check if top portion is mostly dark (ad playing)
    top_roi = screen[0:int(h*0.15), :]
    gray = cv2.cvtColor(top_roi, cv2.COLOR_BGR2GRAY)
    dark_ratio = np.sum(gray < 50) / gray.size

    return dark_ratio > 0.5

def detect_scratch_screen(screen):
    """
    Detect the scratch card screen by looking for the pink/magenta background.
    """
    h, w = screen.shape[:2]
    hsv = cv2.cvtColor(screen, cv2.COLOR_BGR2HSV)

    # Pink/magenta range (scratch card has bright pink background)
    lower_pink = np.array([140, 50, 150])
    upper_pink = np.array([170, 255, 255])
    mask = cv2.inRange(hsv, lower_pink, upper_pink)

    pink_ratio = np.sum(mask > 0) / mask.size
    return pink_ratio > 0.15  # At least 15% pink

# ---------------------------------------------------------------------------
# Main Automation States
# ---------------------------------------------------------------------------

class ZepetoAutomation:
    """
    State machine for ZEPETO scratch card automation.

    States:
      DETECT    - Analyze screen to determine current state
      TAP_AD    - Tap "AD One more time!" button
      WATCH_AD  - Wait for ad to finish
      CLOSE_AD  - Close the ad
      SCRATCH   - Perform scratch gesture on the card
      COLLECT   - Collect reward (or tap AD again)
    """

    STATE_DETECT = "DETECT"
    STATE_TAP_AD = "TAP_AD"
    STATE_WATCH_AD = "WATCH_AD"
    STATE_CLOSE_AD = "CLOSE_AD"
    STATE_SCRATCH = "SCRATCH"
    STATE_COLLECT = "COLLECT"

    def __init__(self, max_loops=0, extra_delay=0):
        self.matcher = TemplateMatcher(TEMPLATES_DIR)
        self.screen_w, self.screen_h = get_screen_size()
        self.state = self.STATE_DETECT
        self.max_loops = max_loops  # 0 = infinite
        self.loops_done = 0
        self.extra_delay = extra_delay
        self.ad_wait_start = 0
        self.rewards_collected = []
        self.consecutive_detect_fails = 0

    def run(self):
        """Main automation loop."""
        log.info("=" * 50)
        log.info("ZEPETO Scratch Card Automation Started!")
        log.info(f"Screen: {self.screen_w}x{self.screen_h}")
        log.info(f"Max loops: {'infinite' if self.max_loops == 0 else self.max_loops}")
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
                    time.sleep(2)
                    continue

                self.process_state(screen)
                time.sleep(SCREENSHOT_INTERVAL + self.extra_delay)

        except KeyboardInterrupt:
            log.info("\nStopped by user (Ctrl+C)")

        self.print_summary()

    def process_state(self, screen):
        """Process the current state."""
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
        """Detect the current screen and transition to appropriate state."""
        log.info("[DETECT] Analyzing screen...")

        # Check for "AD One more time!" button (reward screen)
        match = self.matcher.find(screen, "ad_one_more_time")
        if match:
            log.info(f"[DETECT] Found 'AD One more time!' button (conf: {match[2]:.2f})")
            self.state = self.STATE_TAP_AD
            self.consecutive_detect_fails = 0
            return

        # Backup: detect blue button in lower area
        blue_btn = detect_blue_button(screen)
        if blue_btn:
            log.info(f"[DETECT] Found blue button at ({blue_btn[0]}, {blue_btn[1]})")
            self.state = self.STATE_TAP_AD
            self.consecutive_detect_fails = 0
            return

        # Check if we're on the scratch card screen (pink background)
        if detect_scratch_screen(screen):
            log.info("[DETECT] Detected scratch card screen")
            self.state = self.STATE_SCRATCH
            self.consecutive_detect_fails = 0
            return

        # Check if we're watching an ad
        if detect_ad_screen(screen):
            log.info("[DETECT] Detected ad screen")
            self.state = self.STATE_WATCH_AD
            self.ad_wait_start = time.time()
            self.consecutive_detect_fails = 0
            return

        # Check for confirmation button
        match = self.matcher.find(screen, "confirmation")
        if match:
            log.info("[DETECT] Found 'confirmation' button - tapping AD instead")
            # Look for the AD button above it
            self.state = self.STATE_TAP_AD
            self.consecutive_detect_fails = 0
            return

        self.consecutive_detect_fails += 1
        log.warning(f"[DETECT] Could not identify screen (attempt {self.consecutive_detect_fails})")

        if self.consecutive_detect_fails >= 5:
            log.warning("[DETECT] Too many failed detections. Trying back button...")
            press_back()
            time.sleep(2)
            self.consecutive_detect_fails = 0

    def do_tap_ad(self, screen):
        """Tap the 'AD One more time!' button."""
        log.info("[TAP_AD] Looking for AD button...")

        # Try template match first
        match = self.matcher.find(screen, "ad_one_more_time")
        if match:
            tap(match[0], match[1])
            log.info(f"[TAP_AD] Tapped AD button at ({match[0]}, {match[1]})")
            self.state = self.STATE_WATCH_AD
            self.ad_wait_start = time.time()
            time.sleep(POST_ACTION_DELAY)
            return

        # Backup: detect blue button
        blue_btn = detect_blue_button(screen)
        if blue_btn:
            tap(blue_btn[0], blue_btn[1])
            log.info(f"[TAP_AD] Tapped blue button at ({blue_btn[0]}, {blue_btn[1]})")
            self.state = self.STATE_WATCH_AD
            self.ad_wait_start = time.time()
            time.sleep(POST_ACTION_DELAY)
            return

        # Fallback: tap where the button typically is
        btn_x = self.screen_w // 2
        btn_y = int(self.screen_h * 0.82)
        tap(btn_x, btn_y)
        log.info(f"[TAP_AD] Tapped estimated button position ({btn_x}, {btn_y})")
        self.state = self.STATE_WATCH_AD
        self.ad_wait_start = time.time()
        time.sleep(POST_ACTION_DELAY)

    def do_watch_ad(self, screen):
        """Wait for the ad to finish playing."""
        elapsed = time.time() - self.ad_wait_start
        log.info(f"[WATCH_AD] Waiting for ad... ({elapsed:.0f}s elapsed)")

        # Check if we're still on the ad screen
        if not detect_ad_screen(screen):
            # Might have auto-closed or we're on a different screen
            # Check if scratch screen appeared
            if detect_scratch_screen(screen):
                log.info("[WATCH_AD] Scratch screen appeared!")
                self.state = self.STATE_SCRATCH
                return

            # Check if reward screen appeared (ad was already watched)
            match = self.matcher.find(screen, "ad_one_more_time")
            if match:
                log.info("[WATCH_AD] Back to reward screen")
                self.state = self.STATE_TAP_AD
                return

        # If ad has been playing too long, try to close it
        if elapsed > AD_WAIT_TIMEOUT:
            log.warning("[WATCH_AD] Ad timeout reached, trying to close...")
            self.state = self.STATE_CLOSE_AD
            return

        # After ~35 seconds, start trying to close (most ads are 30s)
        if elapsed > 35:
            log.info("[WATCH_AD] Ad should be done, attempting to close...")
            self.state = self.STATE_CLOSE_AD

    def do_close_ad(self, screen):
        """Try to close the ad by tapping X buttons."""
        log.info("[CLOSE_AD] Trying to close ad...")

        # First check if we've already left the ad
        if detect_scratch_screen(screen):
            log.info("[CLOSE_AD] Scratch screen detected, ad already closed!")
            self.state = self.STATE_SCRATCH
            return

        match = self.matcher.find(screen, "ad_one_more_time")
        if match:
            log.info("[CLOSE_AD] Reward screen detected, ad already closed!")
            self.state = self.STATE_TAP_AD
            return

        # Try each known close button position
        for i, (rx, ry) in enumerate(AD_CLOSE_POSITIONS):
            x = int(self.screen_w * rx)
            y = int(self.screen_h * ry)
            tap(x, y)
            log.info(f"[CLOSE_AD] Tried position {i+1}: ({x}, {y})")
            time.sleep(0.8)

            # Quick check if it worked
            quick_screen = take_screenshot()
            if quick_screen is not None:
                if detect_scratch_screen(quick_screen):
                    log.info("[CLOSE_AD] Success! Scratch screen appeared")
                    self.state = self.STATE_SCRATCH
                    return
                if not detect_ad_screen(quick_screen):
                    log.info("[CLOSE_AD] Ad seems closed")
                    self.state = self.STATE_DETECT
                    return

        # If nothing worked, try back button
        log.info("[CLOSE_AD] Trying back button...")
        press_back()
        time.sleep(1)
        self.state = self.STATE_DETECT

    def do_scratch(self, screen):
        """Perform scratch gestures on the card."""
        log.info("[SCRATCH] Scratching the card...")

        # Calculate scratch area based on screen size
        left = int(self.screen_w * SCRATCH_AREA["left"])
        top = int(self.screen_h * SCRATCH_AREA["top"])
        right = int(self.screen_w * SCRATCH_AREA["right"])
        bottom = int(self.screen_h * SCRATCH_AREA["bottom"])

        # Perform multiple swipe patterns to scratch thoroughly
        num_swipes = 8
        for i in range(num_swipes):
            # Horizontal swipes at different heights
            y = top + (bottom - top) * i // num_swipes
            y += random.randint(-10, 10)

            # Alternate direction
            if i % 2 == 0:
                swipe(left, y, right, y, 200)
            else:
                swipe(right, y, left, y, 200)
            time.sleep(0.15)

        # Add some vertical swipes too
        for i in range(4):
            x = left + (right - left) * i // 4
            x += random.randint(-10, 10)
            swipe(x, top, x, bottom, 200)
            time.sleep(0.15)

        # Diagonal swipes
        swipe(left, top, right, bottom, 300)
        time.sleep(0.15)
        swipe(right, top, left, bottom, 300)
        time.sleep(0.15)

        log.info("[SCRATCH] Done scratching! Waiting for result...")
        time.sleep(2)

        self.loops_done += 1
        log.info(f"[SCRATCH] Completed loop #{self.loops_done}")
        self.state = self.STATE_DETECT

    def do_collect(self, screen):
        """Handle the reward/collect screen."""
        log.info("[COLLECT] On reward screen")
        # We actually want to tap "AD One more time!" to continue the loop
        self.state = self.STATE_TAP_AD

    def print_summary(self):
        """Print session summary."""
        log.info("=" * 50)
        log.info("SESSION SUMMARY")
        log.info(f"  Loops completed: {self.loops_done}")
        log.info("=" * 50)

# ---------------------------------------------------------------------------
# Main Entry Point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="ZEPETO Scratch Card Automation",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python zepeto_scratch_auto.py              # Run forever
  python zepeto_scratch_auto.py --loops 10   # Run 10 scratch cycles
  python zepeto_scratch_auto.py --delay 1    # Add 1s extra delay between actions
        """
    )
    parser.add_argument(
        "--loops", type=int, default=0,
        help="Number of scratch cycles (0 = infinite, default: 0)"
    )
    parser.add_argument(
        "--delay", type=float, default=0,
        help="Extra delay in seconds between actions (default: 0)"
    )
    parser.add_argument(
        "--verbose", "-v", action="store_true",
        help="Enable verbose/debug logging"
    )
    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    # Pre-flight checks
    log.info("ZEPETO Scratch Card Automation v1.0")
    log.info("-" * 40)

    if not check_device():
        sys.exit(1)

    if not TEMPLATES_DIR.exists():
        log.error(f"Templates directory not found: {TEMPLATES_DIR}")
        log.error("Make sure the 'templates' folder is next to this script.")
        sys.exit(1)

    log.info("Loading templates...")

    # Start automation
    auto = ZepetoAutomation(max_loops=args.loops, extra_delay=args.delay)
    auto.run()

if __name__ == "__main__":
    main()
