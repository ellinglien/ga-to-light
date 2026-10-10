# Google Authenticator → Light Phone

Move all your Google Authenticator 2FA codes to the **Light Phone III Authenticator** in a few minutes, without retyping secrets.

Google Authenticator's export packs about 10 accounts into each QR code. The Light Authenticator scans one standard QR per account. This Mac tool reads the export and shows your accounts one at a time, so you can scan each one with the Light.

## Quick start (Mac)

Open **Terminal** (⌘-Space, type "Terminal") and paste:

```
curl -L https://github.com/ellinglien/ga-to-light/archive/refs/heads/main.tar.gz | tar xz && cd ga-to-light-main && ./run
```

The first run sets itself up (a one-time download of about 60 MB). If your Mac says Python or developer tools are missing, run `xcode-select --install`, click Install, then paste the line again.

**Prefer double-clicking?** Click **Code → Download ZIP** above, unzip it, and double-click `Start.command`. The first time, macOS may block it: go to System Settings → Privacy & Security, scroll down, and click **Open Anyway**.

## Using it

1. On your old phone: Google Authenticator → ☰ → **Transfer accounts → Export accounts**. Select everything.
2. Hold the phone up to your Mac's camera. If you have lots of accounts, Google shows several codes; scan each one. The window counts them ("export code 2 of 3").
   - When macOS asks to let Terminal use the camera, click Allow.
   - It moves on by itself once it has every code. Press **Enter** to finish early.
   - No webcam? Take screenshots of the export codes instead and run `./run code1.png code2.png`.
3. Your accounts appear one at a time as QR codes. On the Light Phone, open **Authenticator**, add an account by scanning, then press **Space** on the Mac for the next one (**b** goes back, **q** quits).

Run it again any time with `cd ga-to-light-main && ./run`.

## Good to know

- **It runs offline.** After the first-run setup, you can turn off Wi-Fi. Nothing is saved, and secrets are never shown on screen as text.
- **Keep Google Authenticator** until you've logged in to each account with a code from the Light.
- **Used screenshots?** Delete them afterwards (and empty Recently Deleted), because they contain your secrets.
- It's one short file, [`ga_to_light.py`](ga_to_light.py), if you want to look.

## What won't move

The Light Authenticator only supports time-based codes (TOTP) using SHA1, SHA256 or SHA512. Counter-based (HOTP) accounts, or anything else unusual, are listed at the start as ones to leave in Google Authenticator.

## Troubleshooting

- **"Couldn't open the camera"**: System Settings → Privacy & Security → Camera → turn on Terminal, then run again.
- **Export code won't scan**: hold the phone still, a bit further from the camera, with the screen brightness up. Or use screenshots.
- **Screenshot says "no QR found"**: crop tighter around the code.

## Tests

```
.venv/bin/python test_ga_to_light.py
```

These use fake accounts. They round-trip each account through a real QR image and check the RFC 6238 test vector.

---

Provided as-is; use at your own risk. Not affiliated with Light or Google. MIT licensed.
