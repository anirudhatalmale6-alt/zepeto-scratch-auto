# ZEPETO Scratch Card Automation - Setup Guide
# ZEPETOスクラッチカード自動化 - セットアップガイド

## What You Need / 必要なもの

1. **A Windows/Mac computer** (コンピュータ)
2. **Your Android phone** (Androidスマホ)
3. **A USB cable** (USBケーブル)

---

## Step 1: Install Python (Pythonのインストール)

### Windows:
1. Go to https://www.python.org/downloads/
2. Download and run the installer
3. **IMPORTANT**: Check the box "Add Python to PATH" during installation
4. Click "Install Now"

### Mac:
1. Open Terminal
2. Run: `brew install python3` (or download from python.org)

---

## Step 2: Install ADB (ADBのインストール)

### Windows:
1. Download "SDK Platform Tools" from:
   https://developer.android.com/tools/releases/platform-tools
2. Extract the zip file to `C:\platform-tools`
3. Add `C:\platform-tools` to your system PATH:
   - Search "Environment Variables" in Windows
   - Edit "Path" → Add `C:\platform-tools`

### Mac:
1. Open Terminal
2. Run: `brew install android-platform-tools`

---

## Step 3: Enable USB Debugging on Your Phone (USBデバッグを有効にする)

1. Open **Settings** (設定)
2. Go to **About Phone** (端末情報)
3. Tap **Build Number** (ビルド番号) **7 times** → Developer Mode enabled!
4. Go back to **Settings** → **Developer Options** (開発者オプション)
5. Enable **USB Debugging** (USBデバッグ)

---

## Step 4: Connect Phone to Computer (スマホをPCに接続)

1. Connect your phone to the computer with a USB cable
2. On your phone, tap **"Allow"** when asked "Allow USB debugging?"
3. Check the box **"Always allow from this computer"**
4. Open a terminal/command prompt and type: `adb devices`
5. You should see your device listed!

---

## Step 5: Install Python Libraries (Pythonライブラリのインストール)

Open terminal/command prompt and run:

```
pip install opencv-python numpy
```

---

## Step 6: Run the Automation! (自動化を実行！)

1. Open ZEPETO on your phone
2. Navigate to the scratch card game
3. Open terminal/command prompt on your computer
4. Navigate to the script folder:
   ```
   cd path/to/zepeto-auto
   ```
5. Run the script:
   ```
   python zepeto_scratch_auto.py
   ```

### Options / オプション:

```
# Run 10 cycles then stop (10回で停止)
python zepeto_scratch_auto.py --loops 10

# Add extra delay between actions (アクション間に追加待機)
python zepeto_scratch_auto.py --delay 1

# Verbose logging (詳細ログ)
python zepeto_scratch_auto.py -v
```

### To stop: Press **Ctrl+C** (停止するには Ctrl+C を押してください)

---

## Troubleshooting / トラブルシューティング

### "No device found" (デバイスが見つからない)
- Make sure USB debugging is enabled
- Try a different USB cable
- On your phone, check for "Allow USB debugging" popup

### Script doesn't detect buttons (ボタンが検出されない)
- Make sure ZEPETO is on the scratch card screen when you start
- Try running with `--delay 2` for slower operation
- The `templates/` folder must be next to the script

### Ad doesn't close (広告が閉じない)
- Different ads have different close button positions
- The script tries multiple positions automatically
- You can edit `AD_CLOSE_POSITIONS` in the script to add more

---

## How Ad-Skipping Works / 広告スキップの仕組み

The script does NOT block or remove ads. Instead, it:

1. **Waits** for the ad timer to count down (typically 30-37 seconds)
2. **Detects** when the close button (X) appears
3. **Taps** the close button automatically
4. If the X button location varies, it tries multiple common positions

The script uses **image recognition** (template matching) and **color detection** to identify which screen is currently showing. You can update the template images in the `templates/` folder if ZEPETO changes its design.

### To update templates:
1. Take a screenshot of the new screen
2. Crop the button/element you want to detect
3. Save it as a PNG in the `templates/` folder with the same filename
