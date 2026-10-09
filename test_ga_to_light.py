"""Fake accounts only. Round-trips through real QR images."""
import base64, hashlib, hmac, os, struct, sys, tempfile
from urllib.parse import quote, unquote, urlparse
import cv2
import ga_to_light as g

def varint(n):
    out = b""
    while True:
        b = n & 0x7F; n >>= 7
        out += bytes([b | (0x80 if n else 0)])
        if not n: return out
def field(num, val):
    if isinstance(val, int): return varint(num << 3) + varint(val)
    return varint(num << 3 | 2) + varint(len(val)) + val
def otp(secret, name, issuer, alg=1, digits=1, typ=2):
    return field(1, secret) + field(2, name.encode()) + field(3, issuer.encode()) + field(4, alg) + field(5, digits) + field(6, typ)
def migration(accts, index, size):
    p = b"".join(field(1, a) for a in accts) + field(2, 1) + field(3, size) + field(4, index) + field(5, 42)
    return "otpauth-migration://offline?data=" + quote(base64.b64encode(p).decode(), safe="")

RFC = b"12345678901234567890"   # RFC 6238 test secret
batch1 = migration([
    otp(RFC, "Example:alice@example.com", "Example", digits=2),       # 8 digits, doubled issuer
    otp(b"\x01\x02\x03\x04\x05\x06\x07\x08\x09\x0a", "bob", "Café & Co", alg=2),
], 0, 2)
batch2 = migration([
    otp(b"hotp-secret-bytes!!!", "counter", "OldBank", typ=1),         # HOTP -> skipped
    otp(b"no-issuer-secret-xx", "plain label", ""),
], 1, 2)

tmp = tempfile.mkdtemp()
paths = []
for n, uri in enumerate([batch1, batch2]):
    p = os.path.join(tmp, f"export{n}.png"); cv2.imwrite(p, g.render_qr(uri, 900)); paths.append(p)

c = g.Collector()
assert g.collect_from_files(c, paths), "no accounts"
assert c.complete and len(c.accounts) == 4, c.status()
accts = sorted(c.accounts.values(), key=lambda a: a.title.lower())
usable = [a for a in accts if not a.unsupported]
assert len(usable) == 3 and [a.name for a in accts if a.unsupported] == ["counter"]

def light_parse(uri):
    """Python mirror of the Light's OtpAuthUriParser."""
    u = urlparse(uri); assert u.scheme == "otpauth" and u.netloc == "totp"
    q = dict(part.split("=", 1) for part in u.query.split("&"))
    q = {k: unquote(v) for k, v in q.items()}
    label = unquote(u.path.lstrip("/"))
    pi, _, lab = label.partition(":") if ":" in label else ("", "", label)
    return dict(issuer=q.get("issuer") or pi, label=lab, secret=q["secret"],
                digits=int(q.get("digits", 6)), algorithm=q.get("algorithm", "SHA1"))

def totp(secret_b32, t, digits, alg):
    key = base64.b32decode(secret_b32 + "=" * (-len(secret_b32) % 8))
    h = hmac.new(key, struct.pack(">Q", t // 30), getattr(hashlib, alg.lower())).digest()
    o = h[-1] & 15
    return str((struct.unpack(">I", h[o:o+4])[0] & 0x7FFFFFFF) % 10**digits).zfill(digits)

for a in usable:
    uri = a.otpauth_uri()
    back = g.decode_qrs(g.render_qr(uri))       # what the Light's camera would read
    assert back == {uri}, f"QR round-trip failed for {a.title}"
    p = light_parse(uri)
    assert base64.b32decode(p["secret"] + "=" * (-len(p["secret"]) % 8)) == a.secret
    print(f"ok  {a.title!r:34} -> issuer={p['issuer']!r} label={p['label']!r} {p['digits']}d {p['algorithm']}")

ex = next(a for a in usable if a.issuer == "Example")
p = light_parse(ex.otpauth_uri())
assert p["label"] == "alice@example.com", p["label"]
assert totp(p["secret"], 59, 8, "SHA1") == "94287082", "RFC 6238 vector mismatch"
print("ok  RFC 6238 vector 94287082 reproduced from the generated QR")
print("ALL PASS")

assert ex.title == "Example: alice@example.com", ex.title
frame = g.frame_for(ex, 0, 3)
assert g.decode_qrs(frame) == {ex.otpauth_uri()}, "display frame doesn't scan"
cv2.imwrite(sys.argv[1] if len(sys.argv) > 1 else os.path.join(tmp, "frame.png"), frame)
print("ok  display frame scans; title de-doubled")
