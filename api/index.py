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


def source_of(s):
    pr = creq.get(API + "/programs/" + s, headers=hdr({"Referer": "https://pitsport.st/", "Accept": "application/json"}), impersonate="chrome", timeout=25)
    vids = pr.json()["data"].get("videos") or []
    uuid = re.search(r"/embed/([A-Za-z0-9_-]{6,})", vids[0]["embedUrl"]).group(1)
    w = creq.post(EMB, json={"watchId": uuid}, headers=hdr({"Accept": "*/*"}), impersonate="chrome", timeout=25)
    return w.json().get("url", "")


def do_test(s):
    try:
        r = creq.get(source_of(s), headers=hdr(), impersonate="chrome", timeout=25)
        head = r.text[:120].replace('"', "'").replace("\n", " ")
        return Response('{"status":%d,"head":"%s"}' % (r.status_code, head), media_type="application/json")
    except Exception as e:
        return Response('{"errore":"%s"}' % str(e).replace('"', "'"), media_type="application/json")


def do_chain(s):
    out = {}
    try:
        src = source_of(s)
        r = creq.get(src, headers=hdr(), impersonate="chrome", timeout=25)
        out["manifest"] = r.status_code
        lines = [x.strip() for x in r.text.splitlines() if x.strip() and not x.startswith("#")]
        if not lines:
            out["nota"] = "manifest senza righe"
            return Response(json.dumps(out), media_type="application/json")
        first = urljoin(src, lines[0])
        if ".m3u8" in first:
            r2 = creq.get(first, headers=hdr(), impersonate="chrome", timeout=25)
            out["livello2"] = r2.status_code
            l2 = [x.strip() for x in r2.text.splitlines() if x.strip() and not x.startswith("#")]
            if not l2:
                out["nota"] = "livello 2 senza righe"
                return Response(json.dumps(out), media_type="application/json")
            first = urljoin(first, l2[0])
        sg = creq.get(first, headers=hdr(), impersonate="chrome", timeout=25)
        b = sg.content
        out["segmento"] = sg.status_code
        out["len"] = len(b)
        out["head"] = b[:8].hex(" ")
        out["png"] = b[:2] == b"\x89P"
        out["ts"] = b[:1] == b"\x47"
    except Exception as e:
        out["errore"] = str(e)
    return Response(json.dumps(out), media_type="application/json")


def do_raw(request, s):
    try:
        src = source_of(s)
        host = str(request.base_url).rstrip("/") + "/api"
        r = creq.get(src, headers=hdr(), impersonate="chrome", timeout=25)
        out = ["status upstream: %d" % r.status_code, "---", r.text[:800]]
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
        url = b64d(u)
        r = creq.get(url, headers=hdr(), impersonate="chrome", timeout=25)
        if r.status_code != 200:
            return Response(r.text[:400], status_code=r.status_code, media_type="text/plain")
        host = str(request.base_url).rstrip("/") + "/api"
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
                out.append(host + "/m3u8/" + b64e(absu) + ".m3u8")
            else:
                out.append(host + "/seg/" + b64e(absu) + ".ts")
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
    if name == "test":
        return do_test(q.get("s", "103388"))
    if name == "chain":
        return do_chain(q.get("s", "103388"))
    if name == "raw":
        return do_raw(request, q.get("s", "103388"))
    if name == "one":
        return do_one(request, q.get("s", "103388"))
    if name == "playlist.m3u":
        return do_playlist(request)
    if name == "m3u8":
        return do_m3u8(request, arg_u(parts, q))
    if name == "seg":
        return do_seg(arg_u(parts, q))
    return Response("rotta non trovata", status_code=404)
