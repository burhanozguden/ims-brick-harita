#!/usr/bin/env python3
"""
Ankara Eczacı Odası nöbetçi eczanelerini çeker, ph.json içindeki PH
(brick -> eczane adları) yapısına göre bricklere eşler ve NOBET verisini
index.html'e AYRI bir <script id="nobet-data"> bloğu olarak yazar.

ÇOK GÜNLÜK VERİ: Her çalışmada dünden itibaren LOOKAHEAD gün sonrasına kadar tüm listeler
çekilir ve index.html'deki önceki günlerle birleştirilir. Sayfa, ziyaret anındaki saate göre
geçerli günü (08:30'da değişir) kendisi seçer; böylece workflow bir gün çalışmasa bile
sayfa güncel listeyi gösterir.

DİKKAT: AEO API'si "yarın" istendiğinde, o anda geçerli olan (bugünün) listeyi döndürür
("14.10 saat 08:30'a kadar 13.10 tarihli liste geçerlidir"). Bu yüzden yanıttaki `date`
alanı istenen tarihle aynı değilse o gün ATLANIR; önceki çalışmalardan kalan veri korunur.

Eşleştirme sırası (match alanı):
  1) Ad PH içinde BİREBİR geçiyorsa -> o brick                              (ph-name)
     - "ECZANESİ" eki, parantez içi ("(VERESELİ)") ve baştaki ilçe adı ("MAMAK ...")
       yok sayılarak da denenir
     - aynı ad birden çok brickte varsa ilçe/mahalle ipuçlarıyla seçilir     (ph-name+ipucu)
     - aday brick ilçeyle uyumsuzsa ama aynı mahalledeki eczaneler de o
       bricktaysa yine kabul edilir                                          (ph-name+mahalle)
  2) PH'de yoksa: aynı ilçe+mahalledeki bilinen eczanelerin brick'i          (mahalle)
  3) O da yoksa: brick_rules.json + brick adındaki mahalle ipuçları          (rule)
  4) Eşleşmeyenler unmatched.json'a (nedeniyle birlikte) yazılır

Mahalle tablosu (mahalle_bricks.json) her çalışmada adıyla eşleşen eczanelerden öğrenilir:
"ilçe|mahalle" -> {brick: [eczane adları]}. Elle düzenlenebilir.

Kullanım:
  python update_pharmacies.py                      # siteden çek, dosyaları güncelle
  python update_pharmacies.py --dry-run            # yazmadan özet göster
  python update_pharmacies.py --date 2026-10-11    # yalnızca bu günü çek (diğer günler korunur)
  python update_pharmacies.py --learn-back 60      # mahalle tablosunu son 60 günle besle
  python update_pharmacies.py --fetcher curl       # yalnızca bir yöntemle (plain/playwright/curl)
  python update_pharmacies.py --rows-json x.json --date 2026-10-11   # hazır listeyle (test)

Veri: https://www.aeo.org.tr/getPharmacies/{YYYY-MM-DD} JSON API'si (bkz. SCRAPING bölümü).
"""
import argparse
import json
import re
import sys
import time
import unicodedata
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

URL = "https://www.aeo.org.tr/nobetci-eczaneler"
TZ = ZoneInfo("Europe/Istanbul")
ROOT = Path(__file__).resolve().parent
HTML_FILE = ROOT / "index.html"
RULES_FILE = ROOT / "brick_rules.json"
PH_FILE = ROOT / "ph.json"
MAHALLE_FILE = ROOT / "mahalle_bricks.json"
UNMATCHED_FILE = ROOT / "unmatched.json"
DEBUG_DIR = ROOT / "debug"

MIN_EXPECTED = 20  # bir günde bunun altında kayıt gelirse o gün ATLANIR (scrape bozulmuş demektir)
NOBET_DEGISIM = (8, 30)  # nöbet günü TR saatiyle 08:30'da değişir (index.html'deki NOBET_DEGISIM_DK ile aynı)
LOOKAHEAD = 9   # AEO bugün dahil en fazla 10 günlük liste veriyor
KEEP_PAST = 2   # index.html'de geçmişe dönük tutulacak gün sayısı

DISTRICTS = [
    "Akyurt", "Altındağ", "Ayaş", "Bahşılı", "Bala", "Balıseyh", "Beypazarı",
    "Çamlıdere", "Çankaya", "Çubuk", "Delice", "Elmadağ", "Etimesgut", "Evren",
    "Gölbaşı", "Güdül", "Haymana", "Kalecik", "Karakeçili", "Kazan", "Keçiören",
    "Keskin", "Kırıkkale", "Kızılcahamam", "Mamak", "Nallıhan", "Polatlı",
    "Pursaklar", "Sincan", "Sulakyurt", "Şereflikoçhisar", "Yahşihan", "Yenimahalle",
]

# AEO sayfası Kırıkkale ilini de listeliyor; Ankara haritasında istemiyoruz.
KIRIKKALE_DISTRICTS = {"kirikkale", "keskin", "delice", "baliseyh", "karakecili",
                       "yahsihan", "sulakyurt", "bahsili", "celebi"}

# Brick grubu -> o gruptaki bricklerde bulunabilecek ilçeler (sadece aday SÜZMEK için).
GROUP_DISTRICTS = {
    "sincan etimesgut": {"sincan", "etimesgut"},
    "yenimahalle batikent": {"yenimahalle"},
    "kecioren pursaklar": {"kecioren", "pursaklar", "altindag"},
    "altindag ulus": {"altindag", "kecioren"},
    "mamak": {"mamak", "cankaya"},
    "cankaya merkez": {"cankaya", "mamak", "altindag"},
    "guneybati koridoru": {"cankaya", "yenimahalle", "etimesgut", "golbasi"},
    "cevre ilceler": {"akyurt", "ayas", "bala", "beypazari", "camlidere", "cubuk", "elmadag",
                      "golbasi", "gudul", "haymana", "kalecik", "kazan", "kizilcahamam",
                      "nallihan", "polatli", "sereflikochisar"},
}

# Beraberlikte: ilçenin "ana" brick grubuna +1 puan
PRIMARY_GROUP = {
    "sincan": "sincan etimesgut", "etimesgut": "sincan etimesgut",
    "yenimahalle": "yenimahalle batikent",
    "kecioren": "kecioren pursaklar", "pursaklar": "kecioren pursaklar",
    "altindag": "altindag ulus", "mamak": "mamak", "cankaya": "cankaya merkez",
}

# Mahalle tablosuna yalnızca PH'deki ada dayanan atamalar öğretilir (mahalle/rule tahminleri öğretilmez)
LEARN_FROM = {"ph-name", "ph-name+ipucu", "ph-name+mahalle"}


# ----------------------------------------------------------------- yardımcılar
_TR = str.maketrans("İIıŞşĞğÜüÖöÇç", "iiissgguuoocc")
STOP = {"eczanesi", "eczane", "ecz", "nobetci", "ve"}


def norm(s: str) -> str:
    s = (s or "").translate(_TR).lower()
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = re.sub(r"[^a-z0-9 ]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def core_name(name: str) -> str:
    return " ".join(t for t in norm(name).split() if t not in STOP)


def name_keys(name: str, district: str):
    """PH'de aranacak ad anahtarları, en kesinden en gevşeğe:
    'MAMAK GÜRPINAR ECZANESİ' -> ['mamak gurpinar', 'gurpinar'];
    'YENİ (VERESELİ) ECZANESİ' -> ['yeni vereseli', 'yeni']."""
    keys = [core_name(name)]
    base = core_name(re.sub(r"\(.*?\)", " ", name))
    keys.append(base)
    d = norm(district)
    if d and base.startswith(d + " "):
        keys.append(base[len(d) + 1:])
    return [k for i, k in enumerate(keys) if k and k not in keys[:i]]


# Adreste farklı yazılan ilçe adları -> AEO'daki ilçe adı
DISTRICT_ALIASES = {"Kahramankazan": "Kazan"}


def detect_district(text: str) -> str:
    t = f" {norm(text)} "
    best, best_len = "", 0
    for name, canon in [(d, d) for d in DISTRICTS] + list(DISTRICT_ALIASES.items()):
        if f" {norm(name)} " in t and len(name) > best_len:
            best, best_len = canon, len(name)
    return best


def is_ankara(r) -> bool:
    if "kirikkale" in norm(r.get("address", "")):
        return False
    return norm(r.get("district", "")) not in KIRIKKALE_DISTRICTS


_MAH_RE = re.compile(r"([^\-/,()]+?)\s+(?:Mahallesi|Mah\b\.?|Mh\b\.?)", re.I)


def mahalle_key(r) -> str:
    """'Başak Mah. Başak Cad. ...' (Mamak) -> 'mamak|basak'; mahalle yazmıyorsa ''."""
    m = _MAH_RE.search(r.get("address", ""))
    dist, mah = norm(r.get("district", "")), norm(m.group(1)) if m else ""
    return f"{dist}|{mah}" if dist and mah else ""


def duty_date(now=None) -> date:
    """Şu an geçerli nöbet günü: 08:30'dan önce hâlâ dünün listesi geçerlidir."""
    now = now or datetime.now(TZ)
    return (now - timedelta(hours=NOBET_DEGISIM[0], minutes=NOBET_DEGISIM[1])).date()


PHONE_RE = re.compile(r"(?:\+?90\s*)?0?\(?\d{3}\)?[\s\-]?\d{3}[\s\-]?\d{2}[\s\-]?\d{2}")

# ----------------------------------------------------------------- SCRAPING
# Veri kaynağı: AEO'nun kendi sayfasının da kullandığı JSON API'si.
#   GET https://www.aeo.org.tr/getPharmacies/{YYYY-MM-DD}
#   -> {"status": "success", "date": "YYYY-MM-DD", "html": "<div class='petition-list'> ... </div>"}
#   -> {"status": "error", "message": "Maksimum bugün dahil 10 günlük nöbet verisi ..."}
# Content-Type yanlış olarak text/html döner; gövde json.loads ile çözülür.
# Site Cloudflare arkasında: önce düz HTTP denenir; 403 alınırsa Playwright ile sayfa açılır
# (cf_clearance/ci_session çerezleri oluşur) ve istekler AYNI tarayıcı oturumundan yapılır.
# O da olmazsa curl_cffi (Chrome TLS parmak izi taklidi) denenir.
API_URL = "https://www.aeo.org.tr/getPharmacies/{date}"
COORD_RE = re.compile(r"query=(-?\d+(?:\.\d+)?),\s*(-?\d+(?:\.\d+)?)")
_DISTRICT_CANON = {norm(d): d for d in DISTRICTS}
_UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/{major}.0.0.0 Safari/537.36")

_FETCH_JS = """async (url) => {
    const r = await fetch(url, {method: "GET", credentials: "include",
                                headers: {"Accept": "*/*"}, cache: "no-store"});
    return {status: r.status, text: await r.text()};
}"""


class Blocked(RuntimeError):
    """Erişim yöntemi engellendi (403 / JSON olmayan yanıt): sıradaki yönteme geçilir."""


class DayUnavailable(ValueError):
    """Yanıt geldi ama istenen günün listesi yok (10 gün sınırı, tarih uyuşmazlığı...)."""


def _clean(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip()


def parse_pharmacies_html(html: str):
    """getPharmacies yanıtındaki `html` alanını eczane satırlarına çevirir.

    Kart yapısı:
      <div class="inline-box" data-name="ÇİĞDEM" data-district="Çankaya">
        <h4>ÇİĞDEM ECZANESİ</h4>
        <p>ADRES<br>TARİF/NOT<span>TELEFON</span></p>
        <a href="https://www.google.com/maps/search/?api=1&query=LAT,LNG">
        <a href="tel:03122860385">
    """
    from bs4 import BeautifulSoup, NavigableString, Tag

    soup = BeautifulSoup(html or "", "lxml")
    rows = []
    for box in soup.select("div.inline-box"):
        h4 = box.find("h4")
        name = _clean(h4.get_text(" ", strip=True) if h4 else box.get("data-name", ""))
        if not name:
            continue

        address, note, phone = "", "", ""
        p = box.select_one(".text p") or box.find("p")
        if p is not None:
            before, after, seen_br = [], [], False
            for node in p.children:
                if isinstance(node, Tag) and node.name == "br":
                    seen_br = True
                elif isinstance(node, Tag) and node.name == "span":
                    phone = phone or _clean(node.get_text(" ", strip=True))
                elif isinstance(node, (NavigableString, Tag)):
                    txt = node.get_text(" ", strip=True) if isinstance(node, Tag) else str(node)
                    (after if seen_br else before).append(txt)
            address, note = _clean(" ".join(before)), _clean(" ".join(after))

        tel = box.select_one('a[href^="tel:"]')
        if tel is not None:
            phone = _clean(tel["href"][4:]) or phone
        if not phone:
            m = PHONE_RE.search(box.get_text(" ", strip=True))
            phone = m.group(0).strip() if m else ""

        # data-district geçerli bir ilçe değilse (ör. "Ilcesi Belirlenmemis") adresten bul
        raw_dist = _clean(box.get("data-district", ""))
        district = _DISTRICT_CANON.get(norm(raw_dist)) or detect_district(f"{address} {note}")

        row = {"name": name, "district": district, "address": address, "phone": phone}
        if note:
            row["note"] = note
        maps = box.select_one('a[href*="google.com/maps"]')
        m = COORD_RE.search(maps["href"]) if maps is not None else None
        if m:
            row["lat"], row["lng"] = float(m.group(1)), float(m.group(2))
        rows.append(row)
    return rows


def _decode_api(status: int, text: str, date_iso: str) -> str:
    """API yanıtını çöz; istenen günün `html` alanını döndür.
    Erişim engeli -> Blocked, o günün verisi yok -> DayUnavailable."""
    if status != 200:
        raise Blocked(f"HTTP {status}")
    try:
        data = json.loads(text)
    except ValueError:
        raise Blocked(f"JSON değil (Cloudflare engeli olabilir): {_clean(text[:120])!r}")
    if not isinstance(data, dict) or data.get("status") != "success" or not data.get("html"):
        msg = data.get("message") if isinstance(data, dict) else type(data).__name__
        raise DayUnavailable(f"yanıt başarısız: {msg}")
    if data.get("date") and data["date"] != date_iso:
        raise DayUnavailable(f"API {data['date']} tarihli listeyi döndürdü (henüz yayında değil)")
    return data["html"]


@contextmanager
def _plain_session():
    """Tarayıcısız düz HTTP isteği. Cloudflare izin verirse en hızlı yol."""
    import urllib.error
    import urllib.request

    def get(url):
        req = urllib.request.Request(url, headers={
            "User-Agent": _UA.format(major=140), "Accept": "*/*",
            "Accept-Language": "tr-TR,tr;q=0.9", "Referer": URL})
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return r.status, r.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode("utf-8", "replace")
    yield get


@contextmanager
def _playwright_session():
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(args=["--disable-blink-features=AutomationControlled"])
        try:
            # Varsayılan UA "HeadlessChrome" içerir ve Cloudflare tarafından engellenir;
            # tarayıcının gerçek sürümüyle normal bir Chrome UA'sı kullanılır.
            ctx = browser.new_context(locale="tr-TR", timezone_id="Europe/Istanbul",
                                      user_agent=_UA.format(major=browser.version.split(".")[0]))
            ctx.add_init_script(
                "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
            page = ctx.new_page()
            page.goto(URL, wait_until="domcontentloaded", timeout=60_000)
            # Cloudflare ara sayfası ("Just a moment..." / "Bir dakika lütfen...") geçene kadar bekle
            for _ in range(30):
                title = norm(page.title())
                if "moment" not in title and "dakika" not in title:
                    break
                page.wait_for_timeout(1000)

            def get(url):
                res = page.evaluate(_FETCH_JS, url)
                return res["status"], res["text"]
            try:
                yield get
            except Blocked:
                try:
                    page.screenshot(path=str(DEBUG_DIR / "screenshot.png"), full_page=True)
                except Exception:
                    pass
                raise
        finally:
            browser.close()


@contextmanager
def _curl_session():
    from curl_cffi import requests as creq

    s = creq.Session(impersonate="chrome")
    s.get(URL, timeout=30)  # oturum çerezleri (ci_session vb.)

    def get(url):
        r = s.get(url, timeout=30, headers={"Accept": "*/*", "Referer": URL})
        return r.status_code, r.text
    yield get


SESSIONS = {"plain": _plain_session, "playwright": _playwright_session, "curl": _curl_session}


def fetch_days(dates, fetcher: str = "auto") -> dict:
    """{tarih: html} döndürür. Bir yöntem engellenirse kalan günler sıradaki yöntemle denenir.
    Listesi henüz yayında olmayan günler atlanır."""
    DEBUG_DIR.mkdir(exist_ok=True)
    order = list(SESSIONS) if fetcher == "auto" else [fetcher]
    out, todo, errors = {}, list(dates), []
    for name in order:
        if not todo:
            break
        try:
            with SESSIONS[name]() as get:
                while todo:
                    d = todo[0]
                    for attempt in range(1, 3):
                        status, text = get(API_URL.format(date=d))
                        try:
                            out[d] = _decode_api(status, text, d)
                            break
                        except Blocked:
                            (DEBUG_DIR / f"api_response_{name}.txt").write_text(text[:20000], encoding="utf-8")
                            if attempt == 2:
                                raise
                            time.sleep(3)
                        except DayUnavailable as e:
                            print(f"[scrape] {d}: atlandı ({e})")
                            break
                    todo.pop(0)
            print(f"[scrape] yöntem={name} alınan gün={len(out)}")
        except Exception as e:
            print(f"[scrape] {name} başarısız: {e}", file=sys.stderr)
            errors.append(f"{name}: {e}")
    if errors and not out:
        (DEBUG_DIR / "errors.txt").write_text("\n".join(errors), encoding="utf-8")
    return out


def rows_from_html(html: str):
    seen, out = set(), []
    for r in parse_pharmacies_html(html):
        k = (core_name(r["name"]), norm(r["district"]))
        if k not in seen and core_name(r["name"]):
            seen.add(k)
            out.append(r)
    return [r for r in out if is_ankara(r)]


# ----------------------------------------------------------------- PH VERİSİ
def load_ph_file():
    """ph.json -> (PH: {brick: [eczane adı]}, groups: {brick: normalize edilmiş grup adı})"""
    if not PH_FILE.exists():
        raise SystemExit(f"HATA: {PH_FILE.name} bulunamadı.")
    d = json.loads(PH_FILE.read_text(encoding="utf-8"))
    if "ph" not in d:
        raise SystemExit(f"HATA: {PH_FILE.name} beklenen biçimde değil ('ph' anahtarı yok). "
                         f"Bu dosya brick -> eczane adları listesini içermeli.")
    return d["ph"], d.get("groups", {})


def load_json(path: Path, default):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default


# ----------------------------------------------------------------- EŞLEŞTİRME
def brick_score(brick, hay, dist_n, rules, groups):
    rule = rules.get(brick, {})
    s = sum(4 for kw in rule.get("keywords", []) if f" {norm(kw)} " in hay)
    if dist_n and any(norm(d) == dist_n for d in rule.get("districts", [])):
        s += 1
    if dist_n and PRIMARY_GROUP.get(dist_n) and groups.get(brick) == PRIMARY_GROUP[dist_n]:
        s += 1
    toks = [t for t in norm(brick).split()
            if t not in ("ank", "ankara", "sitesi") and len(t) > 2 and not t.isdigit()]
    s += sum(2 for t in toks if f" {t} " in hay)
    return s


def pick_unique(cands, hay, dist_n, rules, groups):
    """Skora göre tek bir kazanan döndür; beraberlikte None."""
    scored = sorted(((brick_score(b, hay, dist_n, rules, groups), b) for b in cands), reverse=True)
    if not scored or scored[0][0] <= 0:
        return None
    if len(scored) > 1 and scored[1][0] == scored[0][0]:
        return None
    return scored[0][1]


def mahalle_pick(key, pool, mahalle, self_name="", min_votes=1):
    """Aynı ilçe+mahalledeki BAŞKA eczanelerin brick'i (oy = farklı eczane sayısı);
    en az min_votes oy alan tek ve açık bir kazanan yoksa None."""
    votes = sorted(((len(set(names) - {self_name}), b) for b, names in mahalle.get(key, {}).items()
                    if b in pool), reverse=True)
    if not votes or votes[0][0] < min_votes or (len(votes) > 1 and votes[1][0] == votes[0][0]):
        return None
    return votes[0][1]


def name_index(ph_items) -> dict:
    """{çekirdek ad: {brick, ...}}"""
    idx = {}
    for b, names in ph_items.items():
        for n in names:
            c = core_name(n)
            if c:
                idx.setdefault(c, set()).add(b)
    return idx


def ph_candidates(r, idx):
    """(eşleşen ad anahtarı, PH'de o adın geçtiği bricklerin sıralı listesi)"""
    key = next((k for k in name_keys(r["name"], r.get("district", "")) if k in idx), None)
    return key, sorted(idx.get(key, []))


def match_bricks(rows, ph_items, groups, rules, mahalle):
    idx = name_index(ph_items)
    all_bricks = list(ph_items.keys())
    result, unmatched = {}, []

    GENERIC = {"ilceler", "guney", "kuzey", "dogu", "bati", "merkez"}

    def compatible(b, dists, hay):
        """Brick grubu ilçeyle uyumlu mu? API'deki ilçe VE adreste geçen ilçe dikkate alınır;
        ayrıca adreste brick adındaki mahalle/semt (ör. "(Eryaman)") ya da brick_rules.json'daki
        bir anahtar kelime (ör. Temelli -> POLATLI -2) geçiyorsa uyumlu sayılır."""
        allowed = GROUP_DISTRICTS.get(groups.get(b, ""))
        if allowed is None or not dists or dists & allowed:
            return True
        toks = [t for t in re.findall(r"[a-z0-9]+", norm(b)) if len(t) >= 5 and t not in GENERIC]
        toks += [norm(kw) for kw in rules.get(b, {}).get("keywords", [])]
        return any(f" {t} " in hay for t in toks)

    for r in rows:
        dist_n = norm(r.get("district", ""))
        hay = f" {norm(r.get('address', '') + ' ' + r.get('district', ''))} "
        dists = {d for d in (dist_n, norm(detect_district(r.get("address", "")))) if d}
        mkey = mahalle_key(r)
        chosen, how, reason, cands_out = None, None, None, []

        # 1) PH içinde ad eşleşmesi (önce tam ad, sonra parantezsiz / ilçe öneksiz ad)
        key, cands = ph_candidates(r, idx)
        if cands:
            pool = [b for b in cands if compatible(b, dists, hay)]
            if not pool:
                # Ad PH'de var ama hiçbir aday brick eczanenin ilçesiyle uyumlu değil: aynı isimli
                # başka bir eczane olabilir. Şu durumlarda yine de aynı eczane sayılır:
                #  - ad tek brickte geçiyor ve adındaki semt adresinde de geçiyor ("YAŞAMKENT FEYZA")
                #  - aynı mahalledeki BAŞKA bir eczane de PH'de o brickte (Yaşamkent -> ERYAMAN)
                own = [t for t in key.split() if len(t) >= 5 and f" {t} " in hay]
                w = mahalle_pick(mkey, cands, mahalle, r["name"])
                if len(cands) == 1 and own:
                    chosen, how = cands[0], "ph-name"
                elif w:
                    chosen, how = w, "ph-name+mahalle"
                else:
                    reason, cands_out = "district-mismatch", cands
            elif len(pool) == 1:
                chosen, how = pool[0], "ph-name"
            else:
                w = pick_unique(pool, hay, dist_n, rules, groups) or mahalle_pick(mkey, pool, mahalle, r["name"])
                if w:
                    chosen, how = w, "ph-name+ipucu"
                else:
                    reason, cands_out = "ambiguous", pool

        # 2-3) PH'de yok (ya da ad başka bir eczaneye ait): en az 2 eczaneyle desteklenen mahalle,
        #      sonra kurallar, en son tek eczaneyle desteklenen mahalle
        if not chosen and reason != "ambiguous":
            pool = [b for b in all_bricks if compatible(b, dists, hay)] or all_bricks
            chosen = mahalle_pick(mkey, pool, mahalle, r["name"], 2)
            how = "mahalle"
            if not chosen:
                chosen, how = pick_unique(pool, hay, dist_n, rules, groups), "rule"
            if not chosen:
                chosen, how = mahalle_pick(mkey, pool, mahalle, r["name"], 1), "mahalle"
            if not chosen and not cands:
                reason = "not-in-ph-no-rule"

        if chosen:
            result.setdefault(chosen, []).append({**r, "key": key or core_name(r["name"]), "match": how})
        else:
            unmatched.append({**r, "reason": reason, "candidates": cands_out})
    return result, unmatched


def learn_mahalle(mahalle: dict, rows: list, by_brick: dict, idx: dict) -> int:
    """'ilçe|mahalle' -> brick tablosunu PH'deki adlardan besler: adıyla eşleşenler + adı PH'de tek
    bir brickte geçen tüm eczaneler (PH'nin iddiası; ilçe filtresine takılsa bile tek başına kabul
    edilmez, ancak aynı mahalledeki başka eczaneleri destekler). Eklenen kayıt sayısını döndürür."""
    claims = [(b, it) for b, items in by_brick.items() for it in items if it["match"] in LEARN_FROM]
    claims += [(c[0], r) for r in rows for c in [ph_candidates(r, idx)[1]] if len(c) == 1]
    added = 0
    for b, it in claims:
        k = mahalle_key(it)
        names = mahalle.setdefault(k, {}).setdefault(b, []) if k else None
        if names is not None and it["name"] not in names:
            names.append(it["name"])
            names.sort()
            added += 1
    return added


def dump_mahalle(mahalle: dict) -> str:
    """Satır başına bir mahalle: elle okunup düzenlenebilsin, git diff'i küçük kalsın."""
    head = {"_aciklama": "Otomatik öğrenilir: 'ilçe|mahalle' -> {brick: [o mahallede adıyla eşleşmiş "
                         "eczaneler]}. Adı ph.json'da olmayan nöbetçi, bu tabloda tek bir brick öne "
                         "çıkıyorsa o bricke atanır. Yanlış bir kaydı elle silebilirsiniz."}
    lines = [f"{json.dumps(k, ensure_ascii=False)}: {json.dumps(head[k], ensure_ascii=False)}" for k in head]
    lines += [f"{json.dumps(k, ensure_ascii=False)}: {json.dumps(dict(sorted(v.items())), ensure_ascii=False)}"
              for k, v in sorted(mahalle.items()) if k not in head]
    return "{\n" + ",\n".join(lines) + "\n}\n"


# ----------------------------------------------------------------- HTML YAZMA
# Etiketin hemen ardından NOBET:START, kapanıştan hemen önce NOBET:END gelmek ZORUNDA.
# (Sadece etiket metnine bakmak, başka yerde geçen aynı metni de yakalayıp kodu silebilirdi.)
DATA_RE = re.compile(
    r'<script id="nobet-data">\s*/\* NOBET:START \*/(?:(?!</script>).)*?/\* NOBET:END \*/\s*</script>\s*',
    re.S)
LEGACY_RE = re.compile(r"\n?/\* NOBET:START \*/(?:(?!</script>).)*?/\* NOBET:END \*/", re.S)
PAYLOAD_RE = re.compile(r"const NOBET = (.*?);\s*/\* NOBET:END \*/", re.S)


def clean_html(html: str) -> str:
    """Eski (bozuk) satır içi NOBET bloklarını ve eski veri script'ini temizle."""
    for rx in (DATA_RE, LEGACY_RE):
        for m in rx.finditer(html):
            blk = m.group(0)
            if len(blk) > 1_500_000 or "BRANS" in blk or "function" in blk:
                raise SystemExit("HATA: temizlenecek blok beklenenden büyük/şüpheli; dosya DEĞİŞTİRİLMEDİ.")
        html = rx.sub("", html)
    return html


def read_days(html: str) -> dict:
    """index.html'deki mevcut NOBET verisinden {tarih: gün verisi}. Eski tek günlük biçimi de okur."""
    m = DATA_RE.search(html)
    pm = PAYLOAD_RE.search(m.group(0)) if m else None
    if not pm:
        return {}
    try:
        data = json.loads(pm.group(1))
    except ValueError:
        print("[uyarı] mevcut NOBET verisi okunamadı; yalnızca yeni veri yazılacak.", file=sys.stderr)
        return {}
    if "days" in data:
        return data["days"]
    if data.get("date") and "bricks" in data:
        return {data["date"]: {k: data[k] for k in ("total", "matched", "bricks") if k in data}}
    return {}


MAIN_SCRIPT = "<script>"  # özniteliksiz tek ana script; NOBET bloğu bunun ÖNÜNE konur


def render_payload(days: dict) -> str:
    """Satır başına bir gün: dosya küçük kalır, git diff'inde hangi günün değiştiği görünür."""
    lines = [f" {json.dumps(d)}: {json.dumps(days[d], ensure_ascii=False, sort_keys=True, separators=(',', ':'))}"
             for d in sorted(days)]
    return '{"source": "aeo", "days": {\n' + ",\n".join(lines) + "\n}}"


def inject(html: str, days: dict) -> str:
    n_before = html.count("</script>")
    had_block = bool(DATA_RE.search(html))
    html = clean_html(html)
    pos = html.rfind(MAIN_SCRIPT)
    if pos < 0:
        raise SystemExit("HATA: ana <script> etiketi bulunamadı.")
    js = render_payload(days).replace("</", "<\\/")
    block = ('<script id="nobet-data">\n/* NOBET:START */\n'
             f"const NOBET = {js};\n/* NOBET:END */\n</script>\n")
    out = html[:pos] + block + html[pos:]
    # Güvenlik: </script> sayısı korunmalı (eski blok varsa aynı, yoksa +1) ve ana kod eksilmemeli.
    expected = n_before if had_block else n_before + 1
    if out.count("</script>") != expected or MAIN_SCRIPT not in out:
        raise SystemExit("HATA: çıktı doğrulaması başarısız; index.html DEĞİŞTİRİLMEDİ.")
    return out


def write_if_changed(path: Path, text: str) -> bool:
    if path.exists() and path.read_text(encoding="utf-8") == text:
        return False
    path.write_text(text, encoding="utf-8")
    return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", help="yalnızca bu günü çek: YYYY-MM-DD (varsayılan: dünden +%d güne)" % LOOKAHEAD)
    ap.add_argument("--dry-run", action="store_true", help="dosyaya yazma, özet bas")
    ap.add_argument("--rows-json", help="scrape etme; bu JSON listesini --date günü için kullan (test)")
    ap.add_argument("--learn-back", type=int, default=0, metavar="N",
                    help="mahalle tablosunu beslemek için ayrıca son N günü çek (haritaya yazılmaz)")
    ap.add_argument("--fetcher", choices=["auto", *SESSIONS], default="auto",
                    help="API'ye erişim yöntemi (auto: düz HTTP -> playwright -> curl_cffi)")
    args = ap.parse_args()

    today = duty_date()
    if args.date:
        dates = [args.date]
    else:
        dates = [(today + timedelta(days=i)).isoformat() for i in range(-1, LOOKAHEAD + 1)]
    learn_only = [(today - timedelta(days=i)).isoformat() for i in range(args.learn_back, 1, -1)]

    if args.rows_json:
        if not args.date:
            raise SystemExit("HATA: --rows-json ile --date de verilmeli.")
        fetched = {args.date: json.loads(Path(args.rows_json).read_text(encoding="utf-8"))}
    else:
        fetched = {d: rows_from_html(h) for d, h in fetch_days(learn_only + dates, args.fetcher).items()}

    ph_items, groups = load_ph_file()
    rules = {k: v for k, v in load_json(RULES_FILE, {}).items() if isinstance(v, dict)}
    mahalle = {k: v for k, v in load_json(MAHALLE_FILE, {}).items() if isinstance(v, dict)}

    # Önce tüm günlerden mahalle tablosunu öğren, sonra bütün günleri güncel tabloyla eşle.
    learned, idx = 0, name_index(ph_items)
    for d, rows in fetched.items():
        learned += learn_mahalle(mahalle, rows, match_bricks(rows, ph_items, groups, rules, mahalle)[0], idx)

    new_days, unmatched_by_day = {}, {}
    for d in dates:
        rows = fetched.get(d)
        if rows is None:
            continue
        if len(rows) < MIN_EXPECTED:
            print(f"[uyarı] {d}: yalnızca {len(rows)} Ankara kaydı (min {MIN_EXPECTED}); gün atlandı.",
                  file=sys.stderr)
            continue
        by_brick, unmatched = match_bricks(rows, ph_items, groups, rules, mahalle)
        matched = sum(len(v) for v in by_brick.values())
        new_days[d] = {"total": len(rows), "matched": matched, "bricks": by_brick}
        unmatched_by_day[d] = unmatched
        print(f"[match] {d} toplam={len(rows)} eşleşen={matched} eşleşmeyen={len(unmatched)} "
              f"brick={len(by_brick)}")
        for u in unmatched:
            print(f"   - {u['name']} ({u['district']}): {u['reason']} {u['candidates'] or ''}")
    print(f"[mahalle] yeni öğrenilen kayıt={learned} toplam mahalle={len(mahalle)}")

    if not new_days:
        print("HATA: hiçbir günün verisi alınamadı; dosyalara dokunulmadı. debug/ klasörüne bakın.",
              file=sys.stderr)
        sys.exit(1)

    html = HTML_FILE.read_text(encoding="utf-8")
    keep_from = (today - timedelta(days=KEEP_PAST)).isoformat()
    days = {d: v for d, v in {**read_days(html), **new_days}.items() if d >= keep_from}
    if today.isoformat() not in days:
        # GitHub Actions'ta sarı uyarı olarak görünür; sayfa bu durumda eski listeyi uyarıyla gösterir.
        print(f"::warning::Geçerli nöbet günü ({today}) için liste yok; sayfa bir önceki günü gösterecek.")
    print(f"[ok] index.html'deki günler: {', '.join(sorted(days))}")

    if args.dry_run:
        return

    changed = [p.name for p, text in (
        (HTML_FILE, inject(html, days)),
        (MAHALLE_FILE, dump_mahalle(mahalle)),
        (UNMATCHED_FILE, json.dumps({"days": unmatched_by_day}, ensure_ascii=False, indent=1) + "\n"),
    ) if write_if_changed(p, text)]
    print(f"[ok] güncellenen dosyalar: {', '.join(changed) or 'yok (değişiklik yok)'}")


if __name__ == "__main__":
    main()
