import os, re, json, base64, zlib, struct
from collections import Counter
from urllib.parse import urljoin
from fastapi import FastAPI, Request
from fastapi.responses import Response
from curl_cffi import requests as creq

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
API = "https://pitsport.st/api/v1"
EMB = "https://api.embdlol.st/watch"
REF = "https://embdlol.st/"

app = FastAPI()


def b64e(s):
    return base64.urlsafe_b64encode(s.encode()).decode().rstrip("=")


def b64d(s):
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4)).decode()


def hdr(extra=None):
    h = {
        "User-Agent": UA,
        "Referer": REF,
        "Accept": "*/*, application/vnd.t1c.int-19621",
        "Accept-Language": "it-IT,it;q=0.9,en-US;q=0.8",
        "Origin": "https://embdlol.st",
    }
    if extra:
        h.update(extra)
    return h


def arg_u(parts, q):
    if len(parts) > 1 and parts[1]:
        return parts[1].split(".")[0]
    return q.get("u", "")


def chunks(buf):
    p = 8
    out = []
    while p + 12 <= len(buf):
        (ln,) = struct.unpack(">I", buf[p:p + 4])
        out.append((buf[p + 4:p + 8], ln, buf[p + 8:p + 8 + ln]))
        p += 12 + ln
    return out


def decomp(buf):
    idat = []
    ihdr = None
    for typ, ln, data in chunks(buf):
        if typ == b"IDAT":
            idat.append(data)
        elif typ == b"IHDR":
            ihdr = data
    if not idat or not ihdr:
        return None, None
    try:
        return zlib.decompress(b"".join(idat)), ihdr
    except Exception:
        return None, ihdr


def packets(seg):
    j = -1
    for i in range(max(0, len(seg) - 376)):
        if seg[i] == 0x47 and seg[i + 188] == 0x47 and seg[i + 376] == 0x47:
            j = i
            break
    if j < 0:
        return b""
    out = []
    while j + 188 <= len(seg) and seg[j] == 0x47:
        out.append(seg[j:j + 188])
        j += 188
    return b"".join(out)


def unwrap_png(buf):
    d, ihdr = decomp(buf)
    if not d or not ihdr:
        return None
    w, h = struct.unpack(">II", ihdr[:8])
    bpp = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}.get(ihdr[9], 3) * max(1, ihdr[8] // 8)
    row = 1 + w * bpp
    rows = len(d) // row
    if row < 2 or rows < 2:
        return None
    first = d[1:row]
    ln = 0
    if first[:4] == b"KWP1":
        ln = struct.unpack(">I", first[4:8])[0]
        first = first[8:]
    parts = [packets(first)]
    for k in range(row, rows * row, row):
        parts.append(packets(d[k + 1:k + row]))
    payload = b"".join(parts)
    if ln and 0 < ln <= len(payload):
        return payload[:ln]
    return payload if payload else None


def follow(url):
    r = creq.get(url, headers=hdr(), impersonate="chrome", timeout=25)
    text = r.text
    if "#EXT-X-STREAM-INF" in text:
        v = [x.strip() for x in text.splitlines() if x.strip() and not x.startswith("#")]
        if v:
            url = urljoin(url, v[0])
            r = creq.get(url, headers=hdr(), impersonate="chrome", timeout=25)
            text = r.text
    return url, text


def source_of(s):
    pr = creq.get(API + "/programs/" + s, headers=hdr({"Referer": "https://pitsport.st/", "Accept": "application/json"}), impersonate="chrome", timeout=25)
    vids = pr.json()["data"].get("videos") or []
    uuid = re.search(r"/embed/([A-Za-z0-9_-]{6,})", vids[0]["embedUrl"]).group(1)
    w = creq.post(EMB, json={"watchId": uuid}, headers=hdr({"Accept": "*/*"}), impersonate="chrome", timeout=25)
    return w.json().get("url", "")


def do_fhex(s):
    try:
        src, text = follow(source_of(s))
        segs = [x.strip() for x in text.splitlines() if x.strip() and not x.startswith("#")]
        sg = creq.get(urljoin(src, segs[0]), headers=hdr(), impersonate="chrome", timeout=25)
        t = unwrap_png(sg.content)
        if not t:
            return Response("estrazione fallita", media_type="text/plain")
        n = len(t) // 188
        cnt = Counter()
        for i in range(0, n * 188, 188):
            cnt[((t[i + 1] & 0x1F) << 8) | t[i + 2]] += 1
        out = ["n: %d" % n, "rari: " + " ".join("%d:%d" % (k, v) for k, v in cnt.items() if v < 8)]
        out.append("pat: %d  pmtCand: %d" % (cnt.get(0, 0), cnt.get(4096, 0)))
        for i in range(0, n * 188, 188):
            pid = ((t[i + 1] & 0x1F) << 8) | t[i + 2]
            if pid == 0:
                st = i + 4 + (1 + t[i + 4])
                out.append("PAT: %s" % t[st:st + 40].hex(" "))
                break
        for i in range(0, n * 188, 188):
            pid = ((t[i + 1] & 0x1F) << 8) | t[i + 2]
            if pid == 4096:
                st = i + 4 + (1 + t[i + 4] if (t[i + 1] & 0x40) else 0)
                out.append("PID4096: %s" % t[st:st + 60].hex(" "))
                break
        return Response("\n".join(out), media_type="text/plain")
    except Exception as e:
        return Response("errore: " + str(e), media_type="text/plain")


def do_one(request, s):
    try:
        host = str(request.base_url).rstrip("/") + "/api"
        body = "#EXTM3U\n#EXTINF:-1,test\n" + host + "/m3u8/" + b64e(source_of(s)) + ".m3u8\n"
        return Response(body, media_type="audio/x-mpegurl")
    except Exception as e:
        return Response("errore: " + str(e), status_code=502)


def do_playlist(request):
    try:
        r = creq.get(API + "/live-now", headers=hdr({"Referer": "https://pitsport.st/", "Accept": "application/json"}), impersonate="chrome", timeout=25)
        data = r.json()["data"]
        items = (data.get("live") or []) + (data.get("upcoming") or [])
        host = str(request.base_url).rstrip("/") + "/api"
        lines = ["#EXTM3U", "#EXT-X-VERSION:3"]
        for p in items[:80]:
            try:
                src = source_of(str(p["programId"]))
                if not src:
                    continue
                lines.append('#EXTINF:-1 group-title="%s",%s' % (p.get("footer") or "PitSport", p.get("titleText") or p.get("slug")))
                lines.append(host + "/m3u8/" + b64e(src) + ".m3u8")
            except Exception:
                pass
        return Response("\n".join(lines) + "\n", media_type="audio/x-mpegurl")
    except Exception as e:
        return Response("errore: " + str(e), status_code=502)


def do_m3u8(request, u):
    try:
        src, text = follow(b64d(u))
        host = str(request.base_url).rstrip("/") + "/api"
        out = []
        for line in text.splitlines():
            line = line.strip()
            if not line or line == "#EXTM3U":
                continue
            if line.startswith("#"):
                out.append(line)
                continue
            out.append(host + "/seg/" + b64e(urljoin(src, line)) + ".ts")
        out.insert(0, "#EXTM3U")
        return Response("\n".join(out) + "\n", media_type="application/vnd.apple.mpegurl")
    except Exception as e:
        return Response("errore: " + str(e), status_code=502)


def do_seg(u):
    try:
        url = b64d(u)
        r = creq.get(url, headers=hdr(), impersonate="chrome", timeout=25)
        if r.status_code != 200:
            return Response(r.text[:200], status_code=r.status_code, media_type="text/plain")
        buf = r.content
        ts = unwrap_png(buf)
        if ts:
            return Response(ts, media_type="video/mp2t")
        return Response(buf, media_type=r.headers.get("content-type", "video/mp2t"))
    except Exception as e:
        return Response("errore: " + str(e), status_code=502)


@app.api_route("/api/{path:path}", methods=["GET", "HEAD"])
def dispatch(request: Request, path: str = ""):
    parts = [x for x in path.split("/") if x]
    name = parts[0] if parts else ""
    q = request.query_params
    if name in ("", "index"):
        return Response("pitsport proxy", media_type="text/plain")
    if name == "debug":
        return Response('{"ok":true}', media_type="application/json")
    if name == "fhex":
        return do_fhex(q.get("s", "103388"))
    if name == "one":
        return do_one(request, q.get("s", "103388"))
    if name == "playlist.m3u":
        return do_playlist(request)
    if name == "m3u8":
        return do_m3u8(request, arg_u(parts, q))
    if name == "seg":
        return do_seg(arg_u(parts, q))
    return Response("rotta non trovata", status_code=404)
