"""Move Google Authenticator accounts to the Light Phone Authenticator.

Google Authenticator exports accounts as one or more "migration" QR codes,
each packing up to ~10 accounts. The Light Authenticator only scans a plain
otpauth://totp QR, one account at a time. This splits the first into the
second and shows each on screen for the phone to scan.

Everything happens in memory on this Mac. No network, nothing written to
disk, and secrets are never printed -- only account names are.

    ./run                 scan the export QR(s) with this Mac's camera
    ./run export.png ...  read the export QR(s) from screenshots instead
"""
from __future__ import annotations

import base64
import sys
from dataclasses import dataclass
from urllib.parse import parse_qs, quote, unquote, urlparse

import cv2
import numpy as np
import qrcode

WINDOW = "Google Authenticator -> Light"

# Enum values from Google Authenticator's MigrationPayload protobuf.
ALGORITHMS = {0: "SHA1", 1: "SHA1", 2: "SHA256", 3: "SHA512", 4: "MD5"}
DIGITS = {0: 6, 1: 6, 2: 8}
TYPE_HOTP = 1

# The Light Authenticator's own limits (OtpAuthUriParser / TotpAlgorithm).
LIGHT_ALGORITHMS = {"SHA1", "SHA256", "SHA512"}


@dataclass
class Account:
    issuer: str
    name: str
    secret: bytes
    algorithm: str
    digits: int
    hotp: bool

    @property
    def short_name(self) -> str:
        # Google often stores the name as "Issuer:account" as well as setting
        # issuer. Light splits the label on its first colon, so don't double it.
        if self.issuer and self.name.startswith(self.issuer + ":"):
            return self.name[len(self.issuer) + 1:]
        return self.name

    @property
    def title(self) -> str:
        return f"{self.issuer}: {self.short_name}" if self.issuer else self.short_name

    @property
    def unsupported(self) -> str | None:
        if self.hotp:
            return "counter-based (HOTP) -- Light only takes TOTP"
        if self.algorithm not in LIGHT_ALGORITHMS:
            return f"{self.algorithm} -- Light only takes SHA1/256/512"
        return None

    def otpauth_uri(self) -> str:
        label = f"{self.issuer}:{self.short_name}" if self.issuer else self.short_name
        params = {
            "secret": base64.b32encode(self.secret).decode().rstrip("="),
            "algorithm": self.algorithm,
            "digits": self.digits,
            "period": 30,
        }
        if self.issuer:
            params["issuer"] = self.issuer
        query = "&".join(f"{k}={quote(str(v), safe='')}" for k, v in params.items())
        return f"otpauth://totp/{quote(label, safe='')}?{query}"


# --- protobuf, just enough of it -------------------------------------------

def _varint(buf: bytes, i: int) -> tuple[int, int]:
    result = shift = 0
    while True:
        b = buf[i]
        i += 1
        result |= (b & 0x7F) << shift
        if not b & 0x80:
            return result, i
        shift += 7


def _fields(buf: bytes):
    i = 0
    while i < len(buf):
        key, i = _varint(buf, i)
        num, wire = key >> 3, key & 7
        if wire == 0:
            val, i = _varint(buf, i)
        elif wire == 2:
            length, i = _varint(buf, i)
            val, i = buf[i:i + length], i + length
        elif wire == 5:
            val, i = buf[i:i + 4], i + 4
        elif wire == 1:
            val, i = buf[i:i + 8], i + 8
        else:
            raise ValueError(f"unexpected protobuf wire type {wire}")
        yield num, val


@dataclass
class Batch:
    accounts: list[Account]
    index: int
    size: int


def parse_migration(uri: str) -> Batch:
    data = parse_qs(urlparse(uri).query)["data"][0]
    payload = base64.b64decode(data + "=" * (-len(data) % 4))
    accounts, index, size = [], 0, 1
    for num, val in _fields(payload):
        if num == 1:
            f = dict(_fields(val))
            accounts.append(Account(
                issuer=f.get(3, b"").decode("utf-8", "replace"),
                name=f.get(2, b"").decode("utf-8", "replace"),
                secret=f.get(1, b""),
                algorithm=ALGORITHMS.get(f.get(4, 0), "UNKNOWN"),
                digits=DIGITS.get(f.get(5, 0), 6),
                hotp=f.get(6, 2) == TYPE_HOTP,
            ))
        elif num == 3:
            size = val or 1
        elif num == 4:
            index = val
    return Batch(accounts, index, size)


def parse_plain(uri: str) -> Account:
    """An ordinary otpauth://totp QR -- passed through so mixed input works."""
    u = urlparse(uri)
    q = {k: v[0] for k, v in parse_qs(u.query).items()}
    label = unquote(u.path.lstrip("/"))
    issuer, _, name = label.rpartition(":")
    secret = q.get("secret", "").upper()
    return Account(
        issuer=q.get("issuer", issuer),
        name=name,
        secret=base64.b32decode(secret + "=" * (-len(secret) % 8)),
        algorithm=q.get("algorithm", "SHA1").upper(),
        digits=int(q.get("digits", 6)),
        hotp=u.netloc.lower() == "hotp",
    )


# --- QR in and out ---------------------------------------------------------

_DETECTORS = [cv2.QRCodeDetectorAruco(), cv2.QRCodeDetector()]


def decode_qrs(img) -> set[str]:
    found = set()
    for detector in _DETECTORS:
        try:
            ok, texts, _, _ = detector.detectAndDecodeMulti(img)
            if ok:
                found.update(t for t in texts if t)
        except cv2.error:
            pass
    return found


def render_qr(text: str, size: int = 640) -> np.ndarray:
    qr = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_M, border=4)
    qr.add_data(text)
    qr.make(fit=True)
    matrix = np.array(qr.get_matrix(), dtype=bool)
    img = np.where(matrix, 0, 255).astype(np.uint8)
    scale = max(1, size // img.shape[0])
    return cv2.resize(img, None, fx=scale, fy=scale, interpolation=cv2.INTER_NEAREST)


def _ascii(s: str) -> str:
    return s.encode("ascii", "replace").decode()


# --- collecting accounts ---------------------------------------------------

class Collector:
    def __init__(self):
        self.accounts: dict[bytes, Account] = {}
        self.batches_seen: set[int] = set()
        self.batch_size = 0

    def add(self, text: str) -> bool:
        """Returns True if this QR added anything new."""
        before = (len(self.accounts), len(self.batches_seen))
        if text.startswith("otpauth-migration://"):
            batch = parse_migration(text)
            self.batches_seen.add(batch.index)
            self.batch_size = max(self.batch_size, batch.size)
            new = batch.accounts
        elif text.lower().startswith("otpauth://"):
            new = [parse_plain(text)]
        else:
            return False
        for acct in new:
            self.accounts.setdefault(acct.secret, acct)
        return (len(self.accounts), len(self.batches_seen)) != before

    @property
    def complete(self) -> bool:
        return self.batch_size > 0 and len(self.batches_seen) >= self.batch_size

    def status(self) -> str:
        codes = (f"export code {len(self.batches_seen)} of {self.batch_size}"
                 if self.batch_size else "no export code yet")
        return f"{len(self.accounts)} accounts  |  {codes}"


def collect_from_camera(c: Collector) -> bool:
    cam = cv2.VideoCapture(0)
    if not cam.isOpened():
        print("Couldn't open the camera. If macOS asked for camera access, allow it for\n"
              "Terminal (System Settings > Privacy & Security > Camera) and run again.\n"
              "Or pass screenshots of the export codes instead: ./run code1.png code2.png")
        return False
    print("Hold the Google Authenticator export code up to the camera.")
    try:
        while True:
            ok, frame = cam.read()
            if not ok:
                continue
            for text in decode_qrs(frame):
                if c.add(text):
                    print(f"  {c.status()}")
            if c.complete:
                return True
            view = frame.copy()
            h = view.shape[0]
            cv2.rectangle(view, (0, h - 90), (view.shape[1], h), (0, 0, 0), -1)
            cv2.putText(view, c.status(), (20, h - 52),
                        cv2.FONT_HERSHEY_SIMPLEX, 1.0, (255, 255, 255), 2)
            cv2.putText(view, "Enter: done scanning    q: quit", (20, h - 16),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.8, (180, 180, 180), 2)
            cv2.imshow(WINDOW, view)
            key = cv2.waitKey(1) & 0xFF
            if key in (13, 10) and c.accounts:
                return True
            if key in (ord("q"), 27):
                return False
    finally:
        cam.release()


def collect_from_files(c: Collector, paths: list[str]) -> bool:
    for path in paths:
        img = cv2.imread(path)
        if img is None:
            print(f"  can't read {path}")
            continue
        texts = decode_qrs(img)
        if not texts:
            print(f"  no QR found in {path} -- try a tighter crop")
        for text in texts:
            c.add(text)
    print(f"  {c.status()}")
    if c.batch_size and not c.complete:
        missing = sorted(set(range(c.batch_size)) - c.batches_seen)
        print(f"  note: export code(s) {[m + 1 for m in missing]} of {c.batch_size} not found")
    return bool(c.accounts)


# --- showing them one at a time --------------------------------------------

def frame_for(acct: Account, i: int, total: int) -> np.ndarray:
    qr = render_qr(acct.otpauth_uri())
    pad = 60
    canvas = np.full((qr.shape[0] + 230, qr.shape[1] + pad * 2), 255, dtype=np.uint8)
    cv2.putText(canvas, f"{i + 1} / {total}", (pad, 50),
                cv2.FONT_HERSHEY_SIMPLEX, 1.1, 90, 2)
    canvas[80:80 + qr.shape[0], pad:pad + qr.shape[1]] = qr
    y = 80 + qr.shape[0] + 55
    cv2.putText(canvas, _ascii(acct.title)[:40], (pad, y),
                cv2.FONT_HERSHEY_SIMPLEX, 1.1, 0, 2)
    cv2.putText(canvas, "space: next    b: back    q: quit", (pad, y + 60),
                cv2.FONT_HERSHEY_SIMPLEX, 0.8, 120, 2)
    return canvas


def show(accounts: list[Account]) -> None:
    i = 0
    while True:
        cv2.imshow(WINDOW, frame_for(accounts[i], i, len(accounts)))

        key = cv2.waitKeyEx(0)
        if key in (ord(" "), ord("n"), 13, 3, 63235):          # next / right arrow
            if i == len(accounts) - 1:
                return
            i += 1
        elif key in (ord("b"), 8, 127, 2, 63234):               # back / left arrow
            i = max(0, i - 1)
        elif key in (ord("q"), 27):
            return


def main(argv: list[str]) -> int:
    c = Collector()
    got = collect_from_files(c, argv) if argv else collect_from_camera(c)
    if not got:
        cv2.destroyAllWindows()
        return 1

    accounts = sorted(c.accounts.values(), key=lambda a: a.title.lower())
    usable = [a for a in accounts if not a.unsupported]
    skipped = [a for a in accounts if a.unsupported]

    print(f"\n{len(usable)} to move:")
    for a in usable:
        print(f"  {a.title}")
    if skipped:
        print(f"\n{len(skipped)} the Light can't take -- leave these in Google Authenticator:")
        for a in skipped:
            print(f"  {a.title}  ({a.unsupported})")

    if usable:
        print("\nClick the window, then scan each code with Authenticator on the Light."
              "\nSpace for the next one, b to go back, q to stop.")
        show(usable)
    cv2.destroyAllWindows()
    print("\nDone. Nothing was saved to disk.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
