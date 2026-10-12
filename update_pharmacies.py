#!/usr/bin/env python3
"""
Ankara Eczacı Odası nöbetçi eczanelerini çeker, ph.json içindeki PH
(brick -> eczane adları) yapısına göre bricklere eşler ve NOBET verisini
index.html'e AYRI bir <script id="nobet-data"> bloğu olarak yazar.

NOT: Tüm eczane listesi (PH) sayfaya gömülmez, ph.json'da durur. Sayfada yalnızca günün
nöbetçileri (NOBET) bulunur; ana script'ten ÖNCE gelen ayrı bir <script> bloğuna yazılır.

Eşleştirme sırası:
  1) Nöbetçi eczane adı PH içinde BİREBİR (ECZANESİ ekini yok sayarak) geçiyorsa -> o brick
     (aynı ad birden çok brickte varsa ilçe/mahalle ipuçlarıyla seçilir)
  2) PH'de yoksa brick_rules.json + brick adındaki mahalle ipuçları
  3) Eşleşmeyenler unmatched.json'a (nedeniyle birlikte) yazılır

Kullanım:
  python update_pharmacies.py                 # siteden çek, index.html'i güncelle
  python update_pharmacies.py --dry-run       # yazmadan özet göster
  python update_pharmacies.py --rows-json x.json   # scrape etmeden, hazır JSON listesiyle
  python update_pharmacies.py --date 2026-10-11    # belirli bir günün nöbet listesi
  python update_pharmacies.py --fetcher curl       # Playwright yerine curl_cffi ile

Veri: https://www.aeo.org.tr/getPharmacies/{YYYY-MM-DD} JSON API'si (bkz. SCRAPING bölümü).
"""
import argparse
import json
import os
import re
import sys
import unicodedata
import urllib.request
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

URL = "https://www.aeo.org.tr/nobetci-eczaneler"
TZ = ZoneInfo("Europe/Istanbul")
ROOT = Path(__file__).resolve().parent
HTML_FILE = ROOT / "index.html"
RULES_FILE = ROOT / "brick_rules.json"
PH_FILE = ROOT / "ph.json"
UNMATCHED_FILE = ROOT / "unmatched.json"
DEBUG_DIR = ROOT / "debug"

MIN_EXPECTED = 20  # bunun altında veri gelirse dosyaya DOKUNMA (scrape bozulmuş demektir)
NOBET_SAATI = 9    # nöbet günü TR saatiyle bu saatte değişir. index.html'deki NOBET_SAATI ile AYNI olmalı.


def nobet_gunu(now=None) -> str:
    """Şu an geçerli nöbet gününün tarihi (YYYY-MM-DD). 09:00'dan önce hâlâ dünün listesi geçerlidir."""
    now = now or datetime.now(TZ)
    return (now - timedelta(hours=NOBET_SAATI)).strftime("%Y-%m-%d")

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


PHONE_RE = re.compile(r"(?:\+?90\s*)?0?\(?\d{3}\)?[\s\-]?\d{3}[\s\-]?\d{2}[\s\-]?\d{2}")

# ----------------------------------------------------------------- SCRAPING
# Veri kaynağı: AEO'nun kendi sayfasının da kullandığı JSON API'si.
#   GET https://www.aeo.org.tr/getPharmacies/{YYYY-MM-DD}
#   -> {"status": "success", "html": "<div class='petition-list'> ... </div>"}
# Content-Type yanlış olarak text/html döner; gövde json.loads ile çözülür.
# Site Cloudflare arkasında: çıplak requests/curl 403 alır. Bu yüzden önce
# Playwright ile sayfa açılır (cf_clearance/ci_session çerezleri oluşur) ve istek
# page.evaluate(fetch(...)) ile AYNI tarayıcı oturumundan yapılır.
# Playwright başarısız olursa curl_cffi (Chrome TLS parmak izi taklidi) denenir.
API_URL = "https://www.aeo.org.tr/getPharmacies/{date}"
COORD_RE = re.compile(r"query=(-?\d+(?:\.\d+)?),\s*(-?\d+(?:\.\d+)?)")
_DISTRICT_CANON = {norm(d): d for d in DISTRICTS}

_FETCH_JS = """async (url) => {
    const r = await fetch(url, {method: "GET", credentials: "include",
                                headers: {"Accept": "*/*"}, cache: "no-store"});
    return {status: r.status, text: await r.text()};
}"""


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


def _decode_api(text: str) -> str:
    """API gövdesini çöz; başarılıysa `html` alanını döndür, değilse ValueError."""
    try:
        data = json.loads(text)
    except Exception:
        snippet = _clean(text[:120])
        raise ValueError(f"JSON değil (Cloudflare engeli olabilir): {snippet!r}")
    if not isinstance(data, dict) or data.get("status") != "success" or not data.get("html"):
        raise ValueError(f"beklenmeyen yanıt: status={data.get('status') if isinstance(data, dict) else type(data)}")
    return data["html"]


def _fetch_with_playwright(date_iso: str) -> str:
    from playwright.sync_api import sync_playwright

    url = API_URL.format(date=date_iso)
    with sync_playwright() as p:
        browser = p.chromium.launch(args=["--disable-blink-features=AutomationControlled"])
        try:
            # Varsayılan UA "HeadlessChrome" içerir ve Cloudflare tarafından engellenir;
            # tarayıcının gerçek sürümüyle normal bir Chrome UA'sı kullanılır.
            major = browser.version.split(".")[0]
            ctx = browser.new_context(
                locale="tr-TR", timezone_id="Europe/Istanbul",
                user_agent=(f"Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                            f"(KHTML, like Gecko) Chrome/{major}.0.0.0 Safari/537.36"),
            )
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

            last_err = None
            for attempt in range(1, 4):
                res = page.evaluate(_FETCH_JS, url)
                print(f"[scrape] playwright deneme={attempt} HTTP {res['status']} "
                      f"{len(res['text'])} bayt")
                try:
                    if res["status"] != 200:
                        raise ValueError(f"HTTP {res['status']}")
                    return _decode_api(res["text"])
                except ValueError as e:
                    last_err = e
                    (DEBUG_DIR / "api_response_playwright.txt").write_text(
                        res["text"][:20000], encoding="utf-8")
                    page.wait_for_timeout(3000 * attempt)
            try:
                page.screenshot(path=str(DEBUG_DIR / "screenshot.png"), full_page=True)
            except Exception:
                pass
            raise RuntimeError(f"Playwright ile API okunamadı: {last_err}")
        finally:
            browser.close()


def _fetch_with_curl_cffi(date_iso: str) -> str:
    from curl_cffi import requests as creq

    last_err = None
    for imp in ("chrome", "safari"):
        try:
            s = creq.Session(impersonate=imp)
            s.get(URL, timeout=30)  # oturum çerezleri (ci_session vb.)
            r = s.get(API_URL.format(date=date_iso), timeout=30,
                      headers={"Accept": "*/*", "Referer": URL})
            print(f"[scrape] curl_cffi({imp}) HTTP {r.status_code} {len(r.text)} bayt")
            if r.status_code != 200:
                raise ValueError(f"HTTP {r.status_code}")
            return _decode_api(r.text)
        except Exception as e:
            last_err = e
            try:
                (DEBUG_DIR / f"api_response_curl_{imp}.txt").write_text(
                    r.text[:20000], encoding="utf-8")
            except Exception:
                pass
    raise RuntimeError(f"curl_cffi ile API okunamadı: {last_err}")


FETCHERS = {"playwright": _fetch_with_playwright, "curl": _fetch_with_curl_cffi}


def _scrape_aeo(date_iso: str, fetcher: str = "auto"):
    date_iso = date_iso or nobet_gunu()
    DEBUG_DIR.mkdir(exist_ok=True)
    order = ["playwright", "curl"] if fetcher == "auto" else [fetcher]

    html, source, errors = None, None, []
    for name in order:
        try:
            html, source = FETCHERS[name](date_iso), name
            break
        except Exception as e:
            print(f"[scrape] {name} başarısız: {e}", file=sys.stderr)
            errors.append(f"{name}: {e}")
    if html is None:
        (DEBUG_DIR / "errors.txt").write_text("\n".join(errors), encoding="utf-8")
        print("HATA: API verisi alınamadı.", file=sys.stderr)
        return []

    (DEBUG_DIR / "api.html").write_text(html, encoding="utf-8")
    rows = parse_pharmacies_html(html)

    seen, out = set(), []
    for r in rows:
        k = (core_name(r["name"]), norm(r["district"]))
        if k not in seen and core_name(r["name"]):
            seen.add(k)
            out.append(r)
    print(f"[scrape] tarih={date_iso} kaynak=api/{source} kayıt={len(out)}")
    return out


# ----------------------------------------------------------------- YEDEK KAYNAK: eczaneler.gen.tr
# Varsayılan veri kaynağı (AEO sitesi Cloudflare nedeniyle güvenilir çekilemediği için). Sayfa üç günlük liste içerir
# (dün/bugün/yarın); her blok "12 Ekim P.tesi akşamından 13 Ekim ... sabahına kadar." gibi
# bir başlıkla başlar. Başlıktaki tarih, istenen güne (date_iso) eşlenir.
# NOT: Bu site Ankara Eczacı Odası'nın resmi sitesi değil, listeleri derleyen üçüncü taraf bir
# portaldır (kullanım koşullarına bakınız); listeleri Ankara Eczacı Odası'ndan derler.
GENTR_URL = os.environ.get("GENTR_URL", "https://www.eczaneler.gen.tr/nobetci-ankara")
_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
_MONTHS = {"ocak": 1, "subat": 2, "mart": 3, "nisan": 4, "mayis": 5, "haziran": 6,
           "temmuz": 7, "agustos": 8, "eylul": 9, "ekim": 10, "kasim": 11, "aralik": 12}
_DATE_HEAD_RE = re.compile(r"^\s*(\d{1,2}) (" + "|".join(_MONTHS) + r")\b")
_PHONE_PIECE_RE = re.compile(r"^\+?[\d\s()\-]{10,22}$")
_PHONE_IN_TEXT_RE = re.compile(r"0?\s*\(\s*\d{3}\s*\)\s*\d{3}[\s\-]*\d{2}[\s\-]*\d{2}")
_ARROWS = "→»"
_ELINK = 'a[href*="/eczane/ankara-"]'


def _gentr_date(day: int, month: int, today: date) -> str:
    best = min((date(y, month, day) for y in (today.year - 1, today.year, today.year + 1)),
               key=lambda d: abs((d - today).days))
    return best.isoformat()


def _digits_to_phone(s: str) -> str:
    d = re.sub(r"\D", "", s)
    if len(d) == 12 and d.startswith("90"):
        d = "0" + d[2:]
    if len(d) == 10:
        d = "0" + d
    return d


def _district_from_piece(piece: str) -> str:
    n, best, found = norm(piece), "", ""
    for name, canon in [(d, d) for d in DISTRICTS] + list(DISTRICT_ALIASES.items()):
        if n.startswith(norm(name)) and len(name) > len(best):
            best, found = name, canon
    return found


def _gentr_container(a):
    """Bu eczane linkini (ve yalnızca onu) içeren en geniş ata öğe = eczane satırı/kartı."""
    cur = a
    while cur.parent is not None and getattr(cur.parent, "name", None) not in (None, "body", "html", "[document]"):
        par = cur.parent
        if len({x.get("href") for x in par.select(_ELINK)}) > 1:
            break
        cur = par
    return cur


def _gentr_row(a):
    cont = _gentr_container(a)
    name = _clean(a.get_text(" ", strip=True))
    pieces = [_clean(s) for s in cont.stripped_strings]
    pieces = [s for s in pieces if s]
    if name in pieces:
        pieces.remove(name)

    # telefon
    phone = ""
    for i in range(len(pieces) - 1, -1, -1):
        if _PHONE_PIECE_RE.match(pieces[i]):
            phone = _digits_to_phone(pieces.pop(i))
            break
    if not phone:
        for i, s in enumerate(pieces):
            m = _PHONE_IN_TEXT_RE.search(s)
            if m:
                phone = _digits_to_phone(m.group(0))
                pieces[i] = _clean(s.replace(m.group(0), ""))
                break
        pieces = [s for s in pieces if s]

    # sondaki "İlçe [Mahalle]" parçalarını ayır
    def short(s):
        return len(s) <= 28 and not re.search(r"\d", s) and s[0] not in _ARROWS

    def dlike(s):
        return short(s) and bool(_district_from_piece(s))

    tail, i = [], len(pieces)
    while i > 1:
        s = pieces[i - 1]
        if dlike(s):
            tail.insert(0, s)
            i -= 1
            break
        if short(s) and any(dlike(q) for q in pieces[max(0, i - 3):i - 1]):
            tail.insert(0, s)
            i -= 1
            continue
        break
    main_txt = _clean(" ".join(pieces[:i]))

    # adres | → konum notu » icap notu
    m = re.search(f"[{_ARROWS}]", main_txt)
    address = _clean(main_txt[:m.start()] if m else main_txt)
    notes = [_clean(x) for x in re.split(rf"\s*[{_ARROWS}]\s*", main_txt[m.start():])] if m else []
    note = " • ".join(x for x in notes if x)

    # ilçe: sondaki parça > "... X / Ankara" kalıbı > adres taraması > link adresi
    district = ""
    for t in tail:
        district = _district_from_piece(t) or district
    if not district:
        mm = re.search(r"([^\s/,\-]+)\s*/\s*(?:Ankara|ANKARA|Nkara)\b", address)
        if mm:
            district = _DISTRICT_CANON.get(norm(mm.group(1))) or DISTRICT_ALIASES.get(mm.group(1), "")
    if not district:
        district = detect_district(address)
    if not district:
        slug = (a.get("href") or "").split("/eczane/ankara-")[-1].split("-")[0]
        district = _DISTRICT_CANON.get(norm(slug), "")

    row = {"name": name, "district": district, "address": address, "phone": phone}
    if note:
        row["note"] = note
    return row


def parse_gentr_html(html: str, today: date = None):
    """Sayfayı {YYYY-MM-DD: [eczane satırları]} sözlüğüne çevirir."""
    from bs4 import BeautifulSoup

    today = today or datetime.now(TZ).date()
    soup = BeautifulSoup(html or "", "lxml")
    out, seen = {}, set()
    for a in soup.select(_ELINK):
        href = a.get("href")
        if not _clean(a.get_text(" ", strip=True)):
            continue  # yalnızca ikon içeren (ör. "yol tarifi") link
        head = a.find_previous(
            string=lambda s: bool(s) and bool(_DATE_HEAD_RE.match(norm(s))) and "kadar" in norm(s))
        if head is None:
            continue
        m = _DATE_HEAD_RE.match(norm(head))
        key = _gentr_date(int(m.group(1)), _MONTHS[m.group(2)], today)
        if (key, href) in seen:
            continue
        seen.add((key, href))
        out.setdefault(key, []).append(_gentr_row(a))
    return out


def _fetch_gentr_html() -> str:
    errors = []
    try:  # 1) standart kütüphane
        req = urllib.request.Request(GENTR_URL, headers={
            "User-Agent": _UA, "Accept-Language": "tr-TR,tr;q=0.9",
            "Accept": "text/html,application/xhtml+xml"})
        with urllib.request.urlopen(req, timeout=30) as r:
            html = r.read().decode("utf-8", "replace")
        if "/eczane/ankara-" in html:
            return html
        errors.append("urllib: beklenen içerik yok")
    except Exception as e:
        errors.append(f"urllib: {e}")
    try:  # 2) curl_cffi
        from curl_cffi import requests as cr
        r = cr.get(GENTR_URL, impersonate="chrome", timeout=30)
        if r.status_code == 200 and "/eczane/ankara-" in r.text:
            return r.text
        errors.append(f"curl_cffi: HTTP {r.status_code}")
    except Exception as e:
        errors.append(f"curl_cffi: {e}")
    try:  # 3) tarayıcı
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            br = p.chromium.launch()
            try:
                pg = br.new_context(locale="tr-TR", user_agent=_UA).new_page()
                pg.goto(GENTR_URL, wait_until="domcontentloaded", timeout=45_000)
                pg.wait_for_timeout(2500)
                html = pg.content()
            finally:
                br.close()
        if "/eczane/ankara-" in html:
            return html
        errors.append("playwright: beklenen içerik yok")
    except Exception as e:
        errors.append(f"playwright: {e}")
    raise RuntimeError("; ".join(errors))


def _scrape_gentr(date_iso: str):
    DEBUG_DIR.mkdir(exist_ok=True)
    try:
        html = _fetch_gentr_html()
    except Exception as e:
        print(f"[scrape] eczaneler.gen.tr başarısız: {e}", file=sys.stderr)
        return []
    (DEBUG_DIR / "gentr.html").write_text(html, encoding="utf-8")
    groups = parse_gentr_html(html)
    print("[scrape] eczaneler.gen.tr günler: " +
          ", ".join(f"{k}={len(v)}" for k, v in sorted(groups.items())))
    rows = groups.get(date_iso, [])
    if not rows:
        print(f"[scrape] eczaneler.gen.tr: {date_iso} için liste yok", file=sys.stderr)
        return []
    seen, out = set(), []
    for r in rows:
        k = (core_name(r["name"]), norm(r["district"]))
        if k not in seen and core_name(r["name"]):
            seen.add(k)
            out.append(r)
    print(f"[scrape] tarih={date_iso} kaynak=eczaneler.gen.tr kayıt={len(out)}")
    return out


STATE = {}


def scrape(date_iso: str, fetcher: str = "auto", source: str = "eczaneler"):
    """Varsayılan kaynak eczaneler.gen.tr. source=aeo: yalnızca AEO API'si; source=auto: önce AEO, olmazsa eczaneler.gen.tr."""
    date_iso = date_iso or nobet_gunu()
    order = ["aeo", "eczaneler"] if source == "auto" else [source]
    rows = []
    for src in order:
        rows = _scrape_aeo(date_iso, fetcher) if src == "aeo" else _scrape_gentr(date_iso)
        if len(rows) >= MIN_EXPECTED:
            STATE["source"] = src
            return rows
        print(f"[scrape] {src}: yeterli veri yok ({len(rows)} kayıt)", file=sys.stderr)
    return rows


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


# ----------------------------------------------------------------- EŞLEŞTİRME
def brick_score(brick, hay, dist_n, rules, groups):
    rule = rules.get(brick, {})
    s = sum(4 for kw in rule.get("keywords", []) if f" {norm(kw)} " in hay)
    if dist_n and any(norm(d) == dist_n for d in rule.get("districts", [])):
        s += 1
    if dist_n and PRIMARY_GROUP.get(dist_n) and groups.get(brick) == PRIMARY_GROUP[dist_n]:
        s += 1
    toks = [t for t in norm(brick).split()
            if t not in ("ank", "ankara") and len(t) > 2 and not t.isdigit()]
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


def match_bricks(rows, ph_items, groups, rules):
    idx = {}
    for b, names in ph_items.items():
        for n in names:
            c = core_name(n)
            if c:
                idx.setdefault(c, set()).add(b)

    all_bricks = list(ph_items.keys())
    result, unmatched = {}, []

    GENERIC = {"ilceler", "guney", "kuzey", "dogu", "bati", "merkez"}

    def compatible(b, dists, hay):
        """Brick grubu ilçeyle uyumlu mu? API'deki ilçe VE adreste geçen ilçe dikkate alınır;
        ayrıca adreste brick adındaki mahalle/semt geçiyorsa (ör. "(Eryaman)") uyumlu sayılır."""
        allowed = GROUP_DISTRICTS.get(groups.get(b, ""))
        if allowed is None or not dists or dists & allowed:
            return True
        toks = [t for t in re.findall(r"[a-z0-9]+", norm(b)) if len(t) >= 5 and t not in GENERIC]
        return any(f" {t} " in hay for t in toks)

    for r in rows:
        key = core_name(r["name"])
        dist_n = norm(r.get("district", ""))
        hay = f" {norm(r.get('address', '') + ' ' + r.get('district', ''))} "
        dists = {d for d in (dist_n, norm(detect_district(r.get("address", "")))) if d}
        chosen, how, reason, cands_out = None, None, None, []

        # 1) PH içinde birebir ad eşleşmesi
        cands = sorted(idx.get(key, []))
        if cands:
            pool = [b for b in cands if compatible(b, dists, hay)]
            if not pool:
                # Ad PH'de var ama hiçbir aday brick eczanenin ilçesiyle uyumlu değil:
                # aynı isimli başka bir eczane olabilir -> sessizce atama, raporla.
                reason, cands_out = "district-mismatch", cands
            elif len(pool) == 1:
                chosen, how = pool[0], "ph-name"
            else:
                w = pick_unique(pool, hay, dist_n, rules, groups)
                if w:
                    chosen, how = w, "ph-name+ipucu"
                else:
                    reason, cands_out = "ambiguous", pool

        # 2) PH'de yok -> kurallar / mahalle ipuçları
        if not chosen and (not cands or reason == "district-mismatch"):
            pool = [b for b in all_bricks if compatible(b, dists, hay)] or all_bricks
            w = pick_unique(pool, hay, dist_n, rules, groups)
            if w:
                chosen, how, reason, cands_out = w, "rule", None, []
            elif not cands:
                reason = "not-in-ph-no-rule"

        item = {**r, "key": key, "match": how}
        if chosen:
            result.setdefault(chosen, []).append(item)
        else:
            unmatched.append({**r, "reason": reason, "candidates": cands_out})
    return result, unmatched


# ----------------------------------------------------------------- HTML YAZMA
# Etiketin hemen ardından NOBET:START, kapanıştan hemen önce NOBET:END gelmek ZORUNDA.
# (Sadece etiket metnine bakmak, başka yerde geçen aynı metni de yakalayıp kodu silebilirdi.)
DATA_RE = re.compile(
    r'<script id="nobet-data">\s*/\* NOBET:START \*/(?:(?!</script>).)*?/\* NOBET:END \*/\s*</script>\s*',
    re.S)
LEGACY_RE = re.compile(r"\n?/\* NOBET:START \*/(?:(?!</script>).)*?/\* NOBET:END \*/", re.S)


def clean_html(html: str) -> str:
    """Eski (bozuk) satır içi NOBET bloklarını ve eski veri script'ini temizle."""
    for rx in (DATA_RE, LEGACY_RE):
        for m in rx.finditer(html):
            blk = m.group(0)
            if len(blk) > 600_000 or "BRANS" in blk or "function" in blk:
                raise SystemExit("HATA: temizlenecek blok beklenenden büyük/şüpheli; dosya DEĞİŞTİRİLMEDİ.")
        html = rx.sub("", html)
    return html


MAIN_SCRIPT = "<script>"  # özniteliksiz tek ana script; NOBET bloğu bunun ÖNÜNE konur


def inject(html: str, payload: dict) -> str:
    n_before = html.count("</script>")
    had_block = bool(DATA_RE.search(html))
    html = clean_html(html)
    pos = html.rfind(MAIN_SCRIPT)
    if pos < 0:
        raise SystemExit("HATA: ana <script> etiketi bulunamadı.")
    js = json.dumps(payload, ensure_ascii=False, indent=1, sort_keys=True).replace("</", "<\\/")
    block = ('<script id="nobet-data">\n/* NOBET:START */\n'
             f"const NOBET = {js};\n/* NOBET:END */\n</script>\n")
    out = html[:pos] + block + html[pos:]
    # Güvenlik: </script> sayısı korunmalı (eski blok varsa aynı, yoksa +1) ve ana kod eksilmemeli.
    expected = n_before if had_block else n_before + 1
    if out.count("</script>") != expected or MAIN_SCRIPT not in out:
        raise SystemExit("HATA: çıktı doğrulaması başarısız; index.html DEĞİŞTİRİLMEDİ.")
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", help="YYYY-MM-DD (varsayılan: bugün, Europe/Istanbul)")
    ap.add_argument("--dry-run", action="store_true", help="dosyaya yazma, özet bas")
    ap.add_argument("--rows-json", help="scrape etme; bu JSON listesini kullan (test için)")
    ap.add_argument("--fetcher", choices=["auto", "playwright", "curl"], default="auto",
                    help="API'ye erişim yöntemi (auto: önce playwright, olmazsa curl_cffi)")
    ap.add_argument("--source", choices=["eczaneler", "aeo", "auto"], default="eczaneler",
                    help="veri kaynağı (varsayılan: eczaneler.gen.tr; aeo: Eczacı Odası API'si; auto: önce AEO, olmazsa eczaneler.gen.tr)")
    args = ap.parse_args()

    today = args.date or nobet_gunu()
    if args.rows_json:
        rows = json.loads(Path(args.rows_json).read_text(encoding="utf-8"))
    else:
        rows = scrape(today, args.fetcher, args.source)

    rows = [r for r in rows if is_ankara(r)]
    if len(rows) < MIN_EXPECTED:
        print(f"HATA: yalnızca {len(rows)} Ankara kaydı bulundu (min {MIN_EXPECTED}). "
              f"index.html'e dokunulmadı. debug/ klasörüne bakın.", file=sys.stderr)
        sys.exit(1)

    html = clean_html(HTML_FILE.read_text(encoding="utf-8"))
    ph_items, groups = load_ph_file()
    rules = json.loads(RULES_FILE.read_text(encoding="utf-8")) if RULES_FILE.exists() else {}

    by_brick, unmatched = match_bricks(rows, ph_items, groups, rules)
    matched = sum(len(v) for v in by_brick.values())
    print(f"[match] toplam={len(rows)} eşleşen={matched} eşleşmeyen={len(unmatched)} "
          f"brick={len(by_brick)}")
    for u in unmatched:
        print(f"   - {u['name']} ({u['district']}): {u['reason']} {u['candidates'] or ''}")

    payload = {"date": today, "source": STATE.get("source", "rows-json"),
               "total": len(rows), "matched": matched, "bricks": by_brick}

    if args.dry_run:
        return

    HTML_FILE.write_text(inject(html, payload), encoding="utf-8")
    UNMATCHED_FILE.write_text(
        json.dumps({"date": today, "items": unmatched}, ensure_ascii=False, indent=1),
        encoding="utf-8")
    print("[ok] index.html güncellendi.")


if __name__ == "__main__":
    main()
