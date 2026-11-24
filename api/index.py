import os, re, json, base64, zlib, struct
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
        d, ihdr = decomp(sg.content)
        w, h = struct.unpack(">II", ihdr[:8])
        row = 1 + w * 3
        rows = len(d) // row
        flat = b"".join(d[k + 1:k + row] for k in range(0, rows * row, row))
        starts = [flat[k] for k in range(0, min(rows, 200) * row, row)]
        out = ["row: %d rows: %d" % (row, rows), "flatLen: %d" % len(flat)]
        out.append("inizi47: %d su %d" % (sum(1 for x in starts if x == 0x47), len(starts)))
        out.append("primi30inizi: %s" % "".join("%02x" % x for x in starts[:30]))
        pair = 0
        for k in range(0, min(rows, 200) * row, row):
            if flat[k] == 0x47 and flat[k + 188] == 0x47:
                pair += 1
        out.append("coppie47: %d" % pair)
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
        ts = None
        d, ihdr = decomp(buf)
        if d:
            return Response(d[:64], media_type="application/octet-stream")
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
