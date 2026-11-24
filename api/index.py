import os, re, base64, zlib, struct
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


def unwrap_png(buf):
    if buf[:2] != b"\x89P":
        return None
    p, parts = 8, []
    while p + 12 <= len(buf):
        (ln,) = struct.unpack(">I", buf[p:p + 4])
        typ = buf[p + 4:p + 8]
        data = buf[p + 8:p + 8 + ln]
        if typ == b"iTXt":
            i = data.find(b"\x00")
            kw = data[:i].decode("latin1")
            if kw.startswith("payload-"):
                n = int(kw[8:] or 0)
                j = data.find(b"\x00", i + 3)
                j = data.find(b"\x00", j + 1)
                raw = base64.b64decode(data[j + 1:].strip())
                if raw[:1] != b"\x47":
                    raw = zlib.decompress(raw)
                parts.append((n, raw))
        p += 12 + ln
    if not parts:
        return None
    parts.sort()
    return b"".join(x for _, x in parts)


@app.get("/")
def index():
    return Response("pitsport proxy", media_type="text/plain")


@app.get("/debug")
def debug():
    return Response('{"ok":true}', media_type="application/json")


@app.get("/test")
def test(s: str = "103388"):
    try:
        pr = creq.get(API + "/programs/" + s, headers=hdr({"Referer": "https://pitsport.st/", "Accept": "application/json"}), impersonate="chrome", timeout=25)
        vids = pr.json()["data"].get("videos") or []
        uuid = re.search(r"/embed/([A-Za-z0-9_-]{6,})", vids[0]["embedUrl"]).group(1)
        w = creq.post(EMB, json={"watchId": uuid}, headers=hdr({"Accept": "*/*"}), impersonate="chrome", timeout=25)
        src = w.json().get("url", "")
        r = creq.get(src, headers=hdr(), impersonate="chrome", timeout=25)
        return Response('{"status":%d,"head":"%s"}' % (r.status_code, r.text[:120].replace('"', "'").replace("\n", " ")), media_type="application/json")
    except Exception as e:
        return Response('{"errore":"%s"}' % str(e).replace('"', "'"), media_type="application/json")


@app.get("/playlist.m3u")
def playlist(request: Request):
    try:
        r = creq.get(API + "/live-now", headers=hdr({"Referer": "https://pitsport.st/", "Accept": "application/json"}), impersonate="chrome", timeout=25)
        data = r.json()["data"]
        items = (data.get("live") or []) + (data.get("upcoming") or [])
        host = str(request.base_url).rstrip("/")
        lines = ["#EXTM3U", "#EXT-X-VERSION:3"]
        for p in items[:80]:
            try:
                pr = creq.get(API + "/programs/" + str(p["programId"]), headers=hdr({"Referer": "https://pitsport.st/", "Accept": "application/json"}), impersonate="chrome", timeout=25)
                vids = pr.json()["data"].get("videos") or []
                if not vids:
                    continue
                uuid = re.search(r"/embed/([A-Za-z0-9_-]{6,})", vids[0]["embedUrl"]).group(1)
                w = creq.post(EMB, json={"watchId": uuid}, headers=hdr({"Accept": "*/*"}), impersonate="chrome", timeout=25)
                src = w.json().get("url")
                if not src:
                    continue
                lines.append('#EXTINF:-1 group-title="%s",%s' % (p.get("footer") or "PitSport", p.get("titleText") or p.get("slug")))
                lines.append(host + "/m3u8?u=" + b64e(src))
            except Exception:
                pass
        return Response("\n".join(lines) + "\n", media_type="audio/x-mpegurl")
    except Exception as e:
        return Response("errore: " + str(e), status_code=502)


@app.get("/m3u8")
def m3u8(request: Request, u: str = ""):
    try:
        url = b64d(u)
        r = creq.get(url, headers=hdr(), impersonate="chrome", timeout=25)
        if r.status_code != 200:
            return Response(r.text[:400], status_code=r.status_code, media_type="text/plain")
        host = str(request.base_url).rstrip("/")
        out = ["#EXTM3U"]
        for line in r.text.splitlines():
            line = line.strip()
            if not line:
                continue
            if line.startswith("#"):
                out.append(line)
                continue
            absu = urljoin(url, line)
            if ".m3u8" in line:
                out.append(host + "/m3u8?u=" + b64e(absu))
            else:
                out.append(host + "/seg?u=" + b64e(absu))
        return Response("\n".join(out) + "\n", media_type="application/vnd.apple.mpegurl")
    except Exception as e:
        return Response("errore: " + str(e), status_code=502)


@app.get("/seg")
def seg(u: str = ""):
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
