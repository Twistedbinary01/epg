#!/usr/bin/env python3
"""
epg_clean.py - download a fresh XMLTV guide and apply all the fixes made so far.

Usage:
    python3 epg_clean.py SOURCE OUTPUT.xml
      SOURCE   a URL (http/https, .xml or .xml.gz) or a local file path
      OUTPUT   where to write the cleaned guide; OUTPUT.gz is written too

Needs only Python 3.8+ (standard library). Run it on a schedule (see README below).

What it does:
  * merges duplicate channel ids (all names kept, first icon kept)
  * repairs garbled text (e.g. "A\u00c2\u00a350,000" -> "A\u00a350,000") and stray whitespace
  * removes duplicate programmes, trims overlaps, drops zero-length programmes
  * drops channels with no listings and programmes with no channel
  * applies the logo overrides in LOGO_OVERRIDES below
  * adds a 'Showtime West' channel = Showtime Showcase West shifted by SHOWTIME_WEST_SHIFT_HOURS
  * writes strict XMLTV (no non-standard attributes), all times in UTC
"""
import sys, re, gzip, io, datetime, calendar, collections, urllib.request
import xml.etree.ElementTree as ET

SHOWTIME_WEST_SHIFT_HOURS = 1
SHOWTIME_WEST_SOURCE_ID = "showtimeshowcasepacific.us"
SHOWTIME_WEST_ID = "showtimewest.us"
SHOWTIME_WEST_ICON = "https://purecast.us/logos/united-states/showtime-us.png"

LOGO_OVERRIDES = {
    '7Melbourne.au': 'https://purecast.us/logos/australia/seven-au.png',
    'ADN40ID': 'http://www.tmsimg.com/assets/s15384_ll_h3_ab.png',
    'AMC.Canada.HD.ca2': 'https://purecast.us/logos/canada/amc-ca.png',
    'CBC.Halifax.HD.ca2': 'https://purecast.us/logos/canada/cbc-ca.png',
    'CBC.Halifax.ca2': 'https://purecast.us/logos/canada/cbc-ca.png',
    'CBC.Toronto.HD.ca2': 'https://purecast.us/logos/canada/cbc-ca.png',
    'CHCH-DT.ca2': 'https://purecast.us/logos/canada/chch-ca.png',
    'CTV.COMEDY.CHANNEL.ca2': 'https://purecast.us/logos/canada/ctv-ca.png',
    'CTV.SCI-FI.CHANNEL.HD.ca2': 'https://purecast.us/logos/canada/ctv-ca.png',
    'CTV.Toronto.HD.ca2': 'https://purecast.us/logos/canada/ctv-ca.png',
    'CTV.Two.-.Atlantic.ca2': 'https://purecast.us/logos/canada/ctv-ca.png',
    'CTV.Two.-.Toronto.ca2': 'https://purecast.us/logos/canada/ctv-ca.png',
    'Citytv.Toronto.HD.ca2': 'https://purecast.us/logos/canada/city-tv-ca.png',
    'Discovery.Channel.ca2': 'https://purecast.us/logos/canada/discovery-velocity-ca.png',
    'FUBOSN': 'http://purecast.net/Ic212/fubo.png',
    'Global.BC.ca2': 'https://purecast.us/logos/canada/global-ca.png',
    'Global.Calgary.ca2': 'https://purecast.us/logos/canada/global-ca.png',
    'Global.Durham.ca2': 'https://purecast.us/logos/canada/global-ca.png',
    'Global.Maritimes.ca2': 'https://purecast.us/logos/canada/global-ca.png',
    'Global.Toronto.HD.ca2': 'https://purecast.us/logos/canada/global-ca.png',
    'Global.Toronto.ca2': 'https://purecast.us/logos/canada/global-ca.png',
    'MuchMusic.Network.ca2': 'https://purecast.us/logos/canada/much-ca.png',
    'PK24': 'http://www.tmsimg.com/assets/s113367_ll_h3_aa.png',
    'PTVCARS': 'https://images.pluto.tv/channels/5c12ba66eae03059cbdc77f2/colorLogoPNG.png',
    'RTP3.pt': 'https://purecast.us/logos/portugal/rtp-3-pt.png',
    'Racingcom.au': 'https://purecast.us/logos/australia/racing-com-au.png',
    'Real.Madrid.TV.es': 'http://www.tmsimg.com/assets/s109784_ll_h3_ab.png',
    'SLICE.ca2': 'https://purecast.us/logos/canada/slice-ca.png',
    'SPORTINGTV.pt': 'https://purecast.us/logos/portugal/sporting-tv-pt.png',
    'Showcase.Canada.HD.ca2': 'https://purecast.us/logos/canada/showcase-ca.png',
    'SkyRacing1.au': 'https://purecast.us/logos/australia/sky-racing-1-au.png',
    'SkyRacing2.au': 'https://purecast.us/logos/australia/sky-racing-2-au.png',
    'SportTV6.pt': 'https://purecast.us/logos/portugal/sport-tv-6-pt.png',
    'SportTV7.pt': 'https://purecast.us/logos/portugal/sport-tv-7-pt.png',
    'TVI.pt': 'https://purecast.us/logos/portugal/tvi-pt.png',
    'TVO.ca2': 'https://purecast.us/logos/canada/tvo-ca.png',
    'discoveryfamily.us': 'https://purecast.us/logos/canada/family-channel-ca.png',
    'magnolianetwork.us': 'https://logos-world.net/wp-content/uploads/2022/01/Magnolia-Network-Logo-700x394.png',
    'rtp1.pt': 'https://purecast.us/logos/portugal/rtp-1-pt.png',
    'rtp2.pt': 'https://purecast.us/logos/portugal/rtp-2-pt.png',
    'sic.pt': 'https://purecast.us/logos/portugal/sic-pt.png',
    'sporttv1.pt': 'https://purecast.us/logos/portugal/sport-tv-1-pt.png',
    'sporttv2.pt': 'https://purecast.us/logos/portugal/sport-tv-2-pt.png',
    'sporttv3.pt': 'https://purecast.us/logos/portugal/sport-tv-3-pt.png',
    'sporttv4.pt': 'https://purecast.us/logos/portugal/sport-tv-4-pt.png',
    'sporttv5.pt': 'https://purecast.us/logos/portugal/sport-tv-5-pt.png'
}

MOJI = re.compile("\u00c3.|\u00c2.|\u00e2\u20ac|\u00c5.|\u00c4.")

def demoji(s):
    if not s or not MOJI.search(s):
        return s
    cur = s
    for _ in range(3):
        for enc in ("cp1252", "latin-1"):
            try:
                nxt = cur.encode(enc).decode("utf-8")
                break
            except UnicodeError:
                nxt = None
        if nxt is None or nxt == cur:
            break
        cur = nxt
        if not MOJI.search(cur):
            break
    return cur

def clean(s):
    return re.sub(r"\s+", " ", demoji(s)).strip() if s else s

def to_utc(s):
    dt = datetime.datetime.strptime(s[:14], "%Y%m%d%H%M%S")
    off = 0
    m = re.search(r"([+-])(\d{2})(\d{2})\s*$", s[14:])
    if m:
        off = (1 if m.group(1) == "+" else -1) * (int(m.group(2)) * 3600 + int(m.group(3)) * 60)
    return calendar.timegm(dt.timetuple()) - off

def fmt(u):
    return datetime.datetime.fromtimestamp(u, datetime.timezone.utc).strftime("%Y%m%d%H%M%S") + " +0000"

def open_source(src):
    if re.match(r"https?://", src):
        req = urllib.request.Request(src, headers={"User-Agent": "epg-clean/1.0"})
        data = urllib.request.urlopen(req, timeout=300).read()
    else:
        data = open(src, "rb").read()
    if data[:2] == b"\x1f\x8b":
        data = gzip.decompress(data)
    return io.BytesIO(data)

def main(src, out):
    stats = collections.Counter()
    channels, order = {}, []
    progs = collections.defaultdict(list)
    generator = ""
    for ev, el in ET.iterparse(open_source(src), events=("start", "end")):
        if ev == "start" and el.tag == "tv":
            generator = el.get("generator-info-name", "")
        if ev != "end":
            continue
        if el.tag == "channel":
            cid = (el.get("id") or "").strip()
            if not cid:
                el.clear(); continue
            ent = channels.get(cid)
            if ent is None:
                ent = channels[cid] = {"names": [], "icon": None}; order.append(cid)
            else:
                stats["duplicate channel entries merged"] += 1
            for d in el.findall("display-name"):
                n = clean(d.text)
                if n and n not in ent["names"]:
                    ent["names"].append(n)
            ic = el.find("icon")
            if ic is not None and ic.get("src") and not ent["icon"]:
                ent["icon"] = ic.get("src")
            el.clear()
        elif el.tag == "programme":
            try:
                s, e = to_utc(el.get("start")), to_utc(el.get("stop"))
            except Exception:
                stats["bad time dropped"] += 1; el.clear(); continue
            t = el.findtext("title")
            d = el.findtext("desc")
            t, d = clean(t), clean(d)
            if not t:
                stats["no title dropped"] += 1; el.clear(); continue
            progs[el.get("channel")].append([s, e, t, d or None])
            el.clear()

    kept = {}
    for cid, lst in progs.items():
        lst.sort(key=lambda x: (x[0], x[1]))
        outl, seen = [], set()
        for it in lst:
            key = (it[0], it[2])
            if key in seen:
                stats["duplicate programmes removed"] += 1; continue
            seen.add(key)
            if outl and outl[-1][1] > it[0]:
                outl[-1][1] = it[0]; stats["overlaps trimmed"] += 1
            outl.append(it)
        outl = [x for x in outl if x[1] > x[0]]
        if outl:
            kept[cid] = outl

    for cid in kept:
        if cid not in channels:
            channels[cid] = {"names": [cid], "icon": None}; order.append(cid)
            stats["channel stubs added for orphan listings"] += 1
    final = [c for c in order if c in kept]
    stats["channels without listings removed"] = len(order) - len(final)

    if SHOWTIME_WEST_SOURCE_ID in kept:
        sh = SHOWTIME_WEST_SHIFT_HOURS * 3600
        kept[SHOWTIME_WEST_ID] = [[s + sh, e + sh, t, d] for s, e, t, d in kept[SHOWTIME_WEST_SOURCE_ID]]
        channels[SHOWTIME_WEST_ID] = {"names": ["Showtime West"], "icon": SHOWTIME_WEST_ICON}
        final.append(SHOWTIME_WEST_ID)
    for cid, url in LOGO_OVERRIDES.items():
        if cid in channels:
            channels[cid]["icon"] = url

    def esc(x):
        return (x.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;"))
    buf = io.StringIO()
    buf.write('<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE tv SYSTEM "xmltv.dtd">\n')
    buf.write('<tv generator-info-name="%s">\n' % esc(generator))
    for cid in final:
        c = channels[cid]
        buf.write('  <channel id="%s">\n' % esc(cid))
        for n in (c["names"] or [cid]):
            buf.write("    <display-name>%s</display-name>\n" % esc(n))
        if c["icon"]:
            buf.write('    <icon src="%s"/>\n' % esc(c["icon"]))
        buf.write("  </channel>\n")
    n = 0
    for cid in final:
        for s, e, t, d in kept[cid]:
            buf.write('  <programme start="%s" stop="%s" channel="%s">\n    <title>%s</title>\n' % (fmt(s), fmt(e), esc(cid), esc(t)))
            if d:
                buf.write("    <desc>%s</desc>\n" % esc(d))
            buf.write("  </programme>\n"); n += 1
    buf.write("</tv>\n")
    data = buf.getvalue().encode("utf-8")
    with open(out, "wb") as f:
        f.write(data)
    with gzip.open(out + ".gz", "wb", compresslevel=9) as f:
        f.write(data)
    stats["channels written"] = len(final); stats["programmes written"] = n
    for k, v in stats.items():
        print("%-45s %s" % (k, v))
    if n:
        latest = max(x[1] for c in kept.values() for x in c)
        print("guide runs until (UTC)", fmt(latest))

if __name__ == "__main__":
    if len(sys.argv) != 3:
        print(__doc__); sys.exit(1)
    main(sys.argv[1], sys.argv[2])
