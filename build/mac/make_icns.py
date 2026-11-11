"""make_icns.py — macOS 앱 아이콘(.icns)을 만든다. 로고 A(레인 막대) — board-tray.py 의 logo_rgba 와 같은 모양(고칠 땐 둘 다).

  python3 build/mac/make_icns.py <결과.icns> [미리보기.png]

외부 패키지 · iconutil 없이 PNG 를 담은 .icns 를 직접 쓴다(그래서 Windows 에서도 만들어 볼 수 있다). 릴리스 CI 가 앱을 묶기 전에 부른다.
"""
import struct
import sys
import zlib
from pathlib import Path

CORAL, WHITE = (255, 94, 91), (255, 255, 255)
BARS = (12, 19, 10, 16)  # 32 격자 위 막대 높이(board-tray.py BARS)
# .icns 항목: 종류 → 픽셀 크기(PNG). ic10 = 512@2x, ic14 = 256@2x, ic13 = 128@2x, ic12 = 32@2x, ic11 = 16@2x
TYPES = (("icp4", 16), ("icp5", 32), ("icp6", 64), ("ic07", 128), ("ic08", 256), ("ic09", 512), ("ic10", 1024),
         ("ic11", 32), ("ic12", 64), ("ic13", 256), ("ic14", 512))


def _in_rr(x, y, x0, y0, x1, y1, r):
    cx = min(max(x, x0 + r), x1 - r)
    cy = min(max(y, y0 + r), y1 - r)
    return (x - cx) ** 2 + (y - cy) ** 2 <= r * r


def logo_rgba(size: int) -> bytes:
    """둥근 사각형(코랄) 위 흰 막대 4개. macOS 아이콘 격자에 맞춰 바깥에 10% 여백을 둔다."""
    pad = round(size * 0.1)
    inner = size - 2 * pad
    k = inner / 32
    w = max(2, round(4 * k))
    gap = max(1, round(1.75 * k))
    x0 = pad + round((inner - (4 * w + 3 * gap)) / 2)
    base = pad + round(26 * k)
    bars = []
    for i, h in enumerate(BARS):
        x = x0 + i * (w + gap)
        bars.append((x, base - max(w, round(h * k)), x + w, base, w / 2))
    ss = 4 if size <= 64 else (2 if size <= 256 else 1)
    out = bytearray(size * size * 4)
    for py in range(size):
        for px in range(size):
            r = g = b = n = 0
            for sy in range(ss):
                y = py + (sy + .5) / ss
                for sx in range(ss):
                    x = px + (sx + .5) / ss
                    if _in_rr(x, y, pad, pad, pad + inner, pad + inner, 7 * k):
                        col = WHITE if any(_in_rr(x, y, *bb) for bb in bars) else CORAL
                        r += col[0]
                        g += col[1]
                        b += col[2]
                        n += 1
            if n:
                i = (py * size + px) * 4
                out[i:i + 4] = bytes((r // n, g // n, b // n, n * 255 // (ss * ss)))
    return bytes(out)


def png(size: int, rgba: bytes) -> bytes:
    raw = b"".join(b"\x00" + rgba[y * size * 4:(y + 1) * size * 4] for y in range(size))

    def chunk(tag, data):
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))


def main(out: str, preview: str = "") -> None:
    cache = {}
    body = b""
    for kind, size in TYPES:
        if size not in cache:
            cache[size] = png(size, logo_rgba(size))
        data = cache[size]
        body += kind.encode("ascii") + struct.pack(">I", 8 + len(data)) + data
    Path(out).write_bytes(b"icns" + struct.pack(">I", 8 + len(body)) + body)
    print("만들었습니다:", out, f"({len(TYPES)}개 항목)")
    if preview:
        Path(preview).write_bytes(cache[256])


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    main(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else "")
