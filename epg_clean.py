#!/usr/bin/env python3
"""
epg_clean.py - download a fresh XMLTV guide and apply all the fixes made so far.

Usage:
    python3 epg_clean.py SOURCE OUTPUT.xml [SUPPLEMENT ...]
      SOURCE      a URL (http/https, .xml or .xml.gz) or a local file path
      OUTPUT      where to write the cleaned guide; OUTPUT.gz is written too
      SUPPLEMENT  optional extra guides (URL or path). For every channel id that
                  also exists in SOURCE, a supplement replaces that channel's
                  listings when it reaches at least as far into the future.
                  Extra guides can also be given in the EPG_SUPPLEMENT_URLS
                  environment variable (separated by spaces or commas).
                  A supplement that fails to download is skipped with a warning.

Needs only Python 3.8+ (standard library). Run it on a schedule (see README below).

What it does:
  * merges duplicate channel ids (all names kept, first icon kept)
  * repairs garbled text (e.g. "A\u00c2\u00a350,000" -> "A\u00a350,000") and stray whitespace
  * removes duplicate programmes, trims overlaps, drops zero-length programmes
  * drops channels with no listings and programmes with no channel
  * applies the logo overrides in LOGO_OVERRIDES below
  * drops programmes that ended more than PAST_HOURS_KEPT hours ago
  * adds a 'Showtime West' channel = Showtime Showcase West shifted by SHOWTIME_WEST_SHIFT_HOURS
  * writes strict XMLTV (no non-standard attributes), all times in UTC
"""
import unicodedata
import sys, os, ssl, time, re, gzip, io, datetime, calendar, collections, urllib.request
import xml.etree.ElementTree as ET

SHOWTIME_WEST_SHIFT_HOURS = 1
PAST_HOURS_KEPT = 12   # drop programmes that ended more than this many hours ago
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

# Players that read channel logos from the guide can override the playlist logos with
# broken links. True = write no <icon> at all so the playlist (tvg-logo) always wins.
OMIT_CHANNEL_ICONS = True

EXTRA_CHANNEL_IDS = frozenset(['10BoldAdelaide.au', '10BoldHobart.au', '10BoldMelbourne.au', '10BoldPerth.au', '10BoldSydney.au', '10PeachAdelaide.au', '10PeachBrisbane.au', '10PeachHobart.au', '10PeachMelbourne.au', '10PeachPerth.au', '10PeachSydney.au', '7flixAdelaide.au', '7flixBrisbane.au', '7flixMelbourne.au', '7flixPerth.au', '7flixSydney.au', '7mateAdelaide.au', '7mateBrisbane.au', '7mateHobart.au', '7mateMelbourne.au', '7matePerth.au', '7mateSydney.au', '9Adelaide.au', '9GoAdelaide.au', '9GoBrisbane.au', '9GoHobart.au', '9GoMelbourne.au', '9GoPerth.au', '9GoSydney.au', '9Hobart.au', '9LifeAdelaide.au', '9LifeBrisbane.au', '9LifeHobart.au', '9LifePerth.au', '9Melbourne.au', '9Perth.au', '9RushAdelaide.au', '9RushBrisbane.au', '9RushMelbourne.au', '9RushPerth.au', '9RushSydney.au', 'ABCAdelaide.au', 'ABCHobart.au', 'ABCKidsAdelaide.au', 'ABCKidsBrisbane.au', 'ABCKidsHobart.au', 'ABCKidsMelbourne.au', 'ABCKidsPerth.au', 'ABCNewsAdelaide.au', 'ABCNewsHobart.au', 'ABCNewsMelbourne.au', 'ADDIK.HD.ca', 'ADULTSWIMHD.ca', 'AMItele.ca', 'AXN.br', 'AboriginalPeoplesTelevisionNetwork.ca', 'Adult.Swim.HD.br', 'BBCFIRSTHD.ca', 'BEINSPORTS.ca', 'BMandC.br', 'BNNBLOOMBERGHD.ca', 'Band.News.br', 'Boomerang.br', 'C3TV.br', 'CAAmazingDiscoveries.ca', 'CACRAVE1.ca', 'CACTVDRAMA.ca', 'CACanalSavoir.ca', 'CAGusto.ca', 'CAICIRadioCanadaTeleToronto.ca', 'CAMagnoliaNetwork.ca', 'CARiverMonsters.ca', 'CASA.HD.ca', 'CATheGrahamNortonShow.ca', 'CAThePriceIsRight.ca', 'CAYTV.ca', 'CBCCalgaryAB.ca', 'CBCNEWSNETWORKHD.ca', 'CBCREGINAHD.ca', 'CBCStJohnS.ca', 'CBCVancouverBC.ca', 'CFTMDT.ca', 'CGTN.br', 'CITYTorontoON.ca', 'CMT.ca', 'CNBC.ca', 'CNNHD.ca', 'CP24HD.ca', 'CRAVE4HD.ca', 'CTV2ATLANTIC.ca', 'CTVNEWSHD.ca', 'CTVSCIFIHD.ca', 'Canal.2.de.México.(Canal.Las.Estrellas.-.XEW).mx', 'Canal.22.de.México.mx', 'Canal.Adrenalina.Sports.Network.mx', 'Canal.Antena.3.mx', 'Canal.Bandamax.mx', 'Canal.BitMe.mx', 'Canal.Cartoonito.(México).mx', 'Canal.D.HD.ca', 'Canal.Disney.Channel.(México).mx', 'Canal.Disney.Junior.(México).mx', 'Canal.Distrito.Comedia.mx', 'Canal.ESNE.TV.mx', 'Canal.Elgourmet.mx', 'Canal.Enlace.(TBN).mx', 'Canal.Europa.Europa.mx', 'Canal.FX.(México).mx', 'Canal.Film.and.Arts.mx', 'Canal.Galicia.TV.mx', 'Canal.Golden.Multiplex.mx', 'Canal.HBO.(México).mx', 'Canal.HBO.Mundi.mx', 'Canal.HBO.Plus.mx', 'Canal.HBO.Pop.mx', 'Canal.HBO.Xtreme.mx', 'Canal.MTV.(México).mx', 'Canal.MTV.00s.mx', 'Canal.MTV.80s.mx', 'Canal.MTV.Hits.mx', 'Canal.María.Visión.mx', 'Canal.Mexiquense.TV.mx', 'Canal.Milenio.TV.mx', 'Canal.NFL.Network.mx', 'Canal.NHK.World.mx', 'Canal.NickMusic.mx', 'Canal.PX.Sports.mx', 'Canal.Real.Madrid.TV.mx', 'Canal.Rural.br', 'Canal.TV.Azteca.Corazón.mx', 'Canal.TV.Azteca.Mundo.mx', 'Canal.TV5MONDE.mx', 'Canal.TeenNick.mx', 'Canal.Telehit.Música.Plus.mx', 'Canal.Telehit.Música.mx', 'Canal.Telehit.mx', 'Canal.Tooncast.mx', 'Canal.Unicable.(México).mx', 'Canal.Universal.Cinema.mx', 'Canal.Universal.Comedy.mx', 'Canal.Universal.Crime.mx', 'Canal.Universal.Premiere.mx', 'Canal.Universal.Reality.mx', 'Canal.do.Boi.br', 'Canal.¡HOLA!.TV.mx', 'CanalM.ca', 'CancaoNova.br', 'CartoonNetworkCanada.ca', 'CinePopHD.ca', 'Curta.br', 'DAYSTAR.ca', 'DISCOVERYCHANNEL.ca', 'DTOUR.ca', 'DisneyChannelCanada.ca', 'EMCITV.ca', 'ESPN.br', 'EWTNC.ca', 'FX.ca', 'FXX.ca', 'Film.&.Arts.br', 'FoxNewsChannel.ca', 'FoxSports503.au', 'GNT.br', 'GlobalDurham-CHEXTV2.ca', 'GlobalNewsBC1.ca', 'Globo.News.br', 'Globo.br', 'GloboAmazonas.br', 'GloboNordeste.br', 'GloboRPC.br', 'GloboTVBahia.br', 'HBO1.ca', 'HBO2.ca', 'HGTV.br', 'Historia.ca', 'HollywoodSuite2000s.ca', 'HollywoodSuite70sPlus.ca', 'HollywoodSuite80sAnd90s.ca', 'HomeAndGardenTelevision.ca', 'ICIRDI.ca', 'ICITeleHD.ca', 'LMCPlusHD.br', 'Lifetime.br', 'MLBNetworkCanada.ca', 'MOVIETIME.ca', 'MSNBC.ca', 'MTVBrasil.br', 'MUCHMUSICHD.ca', 'MaisGlobosat.br', 'MakefulTV.ca', 'Max.ca', 'MeteoMedia.ca', 'NBATVCanadaTVHD.ca', 'NFLNetworkHD.ca', 'NHK.br', 'NHL-AnaheimDucks.us', 'NHL-BostonBruins.us', 'NHL-BuffaloSabres.us', 'NHL-CalgaryFlames.us', 'NHL-CarolinaHurricanes.us', 'NHL-ChicagoBlackhawks.us', 'NHL-ColoradoAvalanche.us', 'NHL-ColumbusBlueJackets.us', 'NHL-DallasStars.us', 'NHL-DetroitRedWings.us', 'NHL-EdmontonOilers.us', 'NHL-FloridaPanthers.us', 'NHL-LosAngelesKings.us', 'NHL-MinnesotaWild.us', 'NHL-MontrealCanadiens.us', 'NHL-NashvillePredators.us', 'NHL-NewJerseyDevils.us', 'NHL-NewYorkIslanders.us', 'NHL-NewYorkRangers.us', 'NHL-OttawaSenators.us', 'NHL-PhiladelphiaFlyers.us', 'NHL-PittsburghPenguins.us', 'NHL-SanJoseSharks.us', 'NHL-SeattleKraken.us', 'NHL-St.LouisBlues.us', 'NHL-TampaBayLightning.us', 'NHL-TorontoMapleLeafs.us', 'NHL-UtahMammoth.us', 'NHL-VancouverCanucks.us', 'NHL-VegasGoldenKnights.us', 'NHL-WashingtonCapitals.us', 'NHL-WinnipegJets.us', 'NITVSydney.au', 'NOSSOFUTEBOL.br', 'Noovo.ca', 'OFF.HD.br', 'OMNI1HD.ca', 'OMNI2HD.ca', 'PeachtreeTVCanadaHD.ca', 'Premiere.Clubes.br', 'Premiere2.br', 'Premiere8.br', 'PremiereHD4.br', 'PrimeAsiaTV.ca', 'Record.TV.br', 'RecordTVBahia.br', 'RecordTVBrasilia.br', 'RecordTVRJ.br', 'RecordTVRS.br', 'Rede.Vida.br', 'RedeFamilia.br', 'RedeGospel.br', 'ReseaudesSportsRDS.ca', 'RogersTVBarrie.ca', 'RogersTVBathurst.ca', 'RogersTVCornerBrook.ca', 'RogersTVGuelph.ca', 'RogersTVLondon.ca', 'RogersTVWaterloo.ca', 'SALTLIGHT.ca', 'SBSFoodHobart.au', 'SBSHobart.au', 'SBT.br', 'SLICEHD.ca', 'SPORTSNET360HD.ca', 'SPORTSNETEASTHD.ca', 'SPORTSNETONEHD.ca', 'SPORTSNETONTARIOHD.ca', 'SPORTSNETPACIFICHD.ca', 'SPORTSNETWESTHD.ca', 'SPORTSNETWORLDHD.ca', 'STARZ1.ca', 'STARZ2.ca', 'Sony.br', 'SporTVHD.br', 'SuperChannelFuseHD.ca', 'SuperChannelHeartandHomeHD.ca', 'SuperChannelVaultHD.ca', 'SuperEcran3HD.ca', 'SuperEcran4HD.ca', 'São.Paulo/SP..AMC.HD.br', 'São.Paulo/SP..BIS.HD.³.br', 'São.Paulo/SP..Band.Sports.HD.³.br', 'São.Paulo/SP..Canal.Brasil.HD.³.br', 'São.Paulo/SP..Cinemax.HD.³.br', 'São.Paulo/SP..Discovery.Science.HD.³.br', 'São.Paulo/SP..Discovery.Theater.HD.³.br', 'São.Paulo/SP..Discovery.world.HD.³.br', 'São.Paulo/SP..Gloob.HD.³.br', 'São.Paulo/SP..Gloobinho.HD.br', 'São.Paulo/SP..HBO.MUNDI.HD.br', 'São.Paulo/SP..HBO.POP.HD.br', 'São.Paulo/SP..HBO.Xtreme.HD.br', 'São.Paulo/SP..Multishow.HD.³.br', 'São.Paulo/SP..Music.Box.Brazil.HD.³.br', 'São.Paulo/SP..Prime.Box.Brazil.HD.³.br', 'São.Paulo/SP..Studio.Universal.HD.br', 'São.Paulo/SP..TV.Rá.Tim.Bum.br', 'São.Paulo/SP..ZooMoo.br', 'São.Paulo/SP.Record.News.br', 'TFO.ca', 'TLC.ca', 'TNTNovelas.br', 'TV.Cultura.br', 'TV.Gazeta.br', 'TVACarletonSurMerCHAU.ca', 'TVAGatineauQC.ca', 'TVAPacific.ca', 'TVARiviereDuLoupCIMT.ca', 'TVASPORTS2.ca', 'TVASportsHD.ca', 'TVAparecidaEspelho.br', 'TVNovoTempo.br', 'Telecine.Action.br', 'Telecine.Cult.br', 'Tooncast.br', 'TravelBoxBrazil.br', 'TruTV.br', 'USA.br', 'UnisTV.ca', 'Universal.Channel.br', 'UvagutTV.ca', 'VisionTV.ca', 'WEATHERNETWORK.ca', 'WNetworkHD.ca', 'YESTV.ca', 'a&e.us', 'abcnewslive.us', 'adultswim.ca', 'altitudesports.us', 'animalplanet.us', 'aspire.us', 'bbcamerica.us', 'bbcworldnews.us', 'beinsports.ca', 'bigtennetwork.us', 'bloomberg.us', 'bouncetv.us', 'buzzr.us', 'byu.us', 'cartoonnetworkadultswim.us', 'cbssportsnetwork.us', 'cleotv.us', 'cnbc.us', 'comedycentral.us', 'comedytv.us', 'cookingchannel.us', 'courttv.us', 'crimeplusinvestigation.us', 'cspan.us', 'dablnetworkllc.us', 'discoveryturbo.us', 'dogtv.us', 'e!entertainmenttelevision.us', 'foodnetwork.us', 'foxbusiness.us', 'foxnews.us', 'foxweather.us', 'freeform.us', 'fubosportsnetwork.us', 'fuse.us', 'fyi.us', 'get.us', 'hbo.us', 'hbocomedy.us', 'hbohits.us', 'heroesandiconstv.us', 'history.us', 'ifc.us', 'indieplex.us', 'insp.us', 'ionplus.us', 'justicecentral.us', 'kiahdt3.us', 'lifetimemovienetwork.us', 'mgmplus.us', 'mgmplusdriveminusin.us', 'mgmplushits.us', 'mgmplusmarquee.us', 'mlbnetwork.us', 'movieplex.us', 'msg.us', 'msnbc.us', 'nationalgeographic.us', 'nationalgeographicwild.us', 'nbscahd.us', 'nesn.us', 'nesnplus.us', 'nflredzone.us', 'nicktoons.us', 'paramountpluswithshowtime.us', 'pbskids.us', 'pixl.us', 'pop.us', 'retroplex.us', 'screenpix.us', 'screenpixaction.us', 'screenpixvoices.us', 'showtimeextreme.us', 'showtimefamilyzone.us', 'showtimenext.us', 'showtimewomen.us', 'skysportsf1.uk', 'skysportsmix.uk', 'skysportspremierleague.uk', 'smithsoniannetwork.us', 'sonymoviechannel.us', 'starz.us', 'starzencore.us', 'syfy.us', 'tastemade.us', 'theweatherchannel.us', 'timewarneroutdoorchannel.us', 'tlc(us).us', 'travelchannel.us', 'tvland.us', 'uptv.us', 'usanetwork.us', 'vice.us', 'wabc.us', 'wbtscd.us', 'wbz.us', 'wcvb.us', 'wetv.us', 'wfor.us', 'wftvdt2.us', 'wfxt.us', 'whnhcd.us', 'worldfishingnetwork(us).us', 'wsvn.us', 'wtvj.us', 'wutv.us', 'yesnetwork.us'])

PLUTO_ID = re.compile(r"^[0-9a-f]{24}$")  # Pluto TV channel ids (i.mjh.nz guides)


def is_extra(c):
    return c in EXTRA_CHANNEL_IDS or bool(c and PLUTO_ID.match(c))


def safe_id(c):
    """ASCII-only version of an id (accents/brackets/& confuse some players)."""
    if not is_extra(c):
        return c
    a = "".join(ch for ch in unicodedata.normalize("NFD", c) if unicodedata.category(ch) != "Mn")
    return re.sub(r"[^A-Za-z0-9._-]", "_", a.encode("ascii", "ignore").decode())


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
    """Open a URL or local path as a binary stream, un-gzipping on the fly (low memory)."""
    if re.match(r"https?://", src):
        req = urllib.request.Request(src, headers={"User-Agent": "epg-clean/1.0"})
        ctx = None
        if os.environ.get("EPG_INSECURE_TLS") == "1":
            # opt-in only: for sources whose certificate is self-signed
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            print("WARNING: TLS certificate checking is OFF for this download")
        raw = urllib.request.urlopen(req, timeout=300, context=ctx)
    else:
        raw = open(src, "rb")
    br = io.BufferedReader(raw, buffer_size=1 << 20)
    if br.peek(2)[:2] == b"\x1f\x8b":
        return gzip.GzipFile(fileobj=br)
    return br

def main(src, out, extra=()):
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

    sup_urls = list(extra) + [u for u in re.split(r"[\s,]+", os.environ.get("EPG_SUPPLEMENT_URLS", "")) if u]
    for url in sup_urls:
        sup = collections.defaultdict(list)
        sup_meta = {}
        sup_icons = {}
        try:
            for ev, el in ET.iterparse(open_source(url), events=("end",)):
                if el.tag == "programme":
                    cid = el.get("channel")
                    if cid in channels or is_extra(cid):
                        try:
                            s_, e_ = to_utc(el.get("start")), to_utc(el.get("stop"))
                            t_ = clean(el.findtext("title"))
                            d_ = clean(el.findtext("desc"))
                        except Exception:
                            t_ = None
                        if t_:
                            sup[cid].append([s_, e_, t_, d_ or None])
                    el.clear()
                elif el.tag == "channel":
                    if is_extra(el.get("id")):
                        sup_meta[el.get("id")] = [clean(d.text) for d in el.findall("display-name") if clean(d.text)]
                        _ic = el.find("icon")
                        if _ic is not None and _ic.get("src") and PLUTO_ID.match(el.get("id") or ""):
                            sup_icons[el.get("id")] = _ic.get("src")
                    el.clear()
        except Exception as ex:
            print("WARNING: supplementary guide skipped (%s): %s" % (type(ex).__name__, ex))
            stats["supplementary guides skipped"] += 1
            continue
        for cid0, lst in sup.items():
            cid = safe_id(cid0)
            if cid not in channels:
                channels[cid] = {"names": sup_meta.get(cid0) or [cid], "icon": sup_icons.get(cid0)}; order.append(cid)
                stats["extra channels added"] += 1
            base_end = max((x[1] for x in progs.get(cid, [])), default=0)
            if max(x[1] for x in lst) >= base_end:
                progs[cid] = lst
                stats["channels updated from supplementary guides"] += 1
            else:
                stats["supplement not newer, kept original"] += 1

    cutoff = int(time.time()) - PAST_HOURS_KEPT * 3600
    for cid in list(progs):
        before = len(progs[cid])
        progs[cid] = [x for x in progs[cid] if x[1] >= cutoff]
        stats["old programmes dropped"] += before - len(progs[cid])

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
    # channels from the playlist (playlist_logos.json) are kept even without listings,
    # so players that take logos from the guide (Purecast) can still show them
    try:
        import json as _json0
        _pl0 = _json0.load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "playlist_logos.json"), encoding="utf-8"))
    except (OSError, ValueError):
        _pl0 = {}
    for _cid in _pl0:
        if _cid not in channels:
            channels[_cid] = {"names": [_cid], "icon": None}; order.append(_cid)
            stats["logo-only channels added"] += 1
    final = [c for c in order if c in kept or c in _pl0]
    stats["channels without listings removed"] = len(order) - len(final)

    if SHOWTIME_WEST_SOURCE_ID in kept:
        sh = SHOWTIME_WEST_SHIFT_HOURS * 3600
        kept[SHOWTIME_WEST_ID] = [[s + sh, e + sh, t, d] for s, e, t, d in kept[SHOWTIME_WEST_SOURCE_ID]]
        channels[SHOWTIME_WEST_ID] = {"names": ["Showtime West"], "icon": SHOWTIME_WEST_ICON}
        if SHOWTIME_WEST_ID not in final:
            final.append(SHOWTIME_WEST_ID)
    for cid, url in LOGO_OVERRIDES.items():
        if cid in channels:
            channels[cid]["icon"] = url
    playlist_icon_ids = set()
    # optional: icons taken from the playlist (playlist_logos.json next to this script)
    try:
        import json as _json
        _pl = _json.load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "playlist_logos.json"), encoding="utf-8"))
        for _cid, _url in _pl.items():
            if _cid in channels:
                channels[_cid]["icon"] = _url
                playlist_icon_ids.add(_cid)
    except (OSError, ValueError):
        pass

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
        if c["icon"] and (not OMIT_CHANNEL_ICONS or PLUTO_ID.match(cid) or cid in playlist_icon_ids):
            buf.write('    <icon src="%s"/>\n' % esc(c["icon"]))
        buf.write("  </channel>\n")
    n = 0
    for cid in final:
        for s, e, t, d in kept.get(cid, []):
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
    if len(sys.argv) < 3:
        print(__doc__); sys.exit(1)
    main(sys.argv[1], sys.argv[2], sys.argv[3:])
