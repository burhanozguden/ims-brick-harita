<uploaded_files github_repo=burhanozguden/ims-brick-harita>
<file=update_pharmacies_new.py>
#!/usr/bin/env python3
"""
Ankara nöbetçi eczanelerini https://www.eczaneler.gen.tr/nobetci-ankara sayfasından çeker,
ph.json'daki brick listeleriyle (brick_rules.json ipuçlarıyla) eşleştirir ve şunları günceller:

  * index.html     -> <script id="nobet-data"> içindeki NOBET verisi (haritanın okuduğu blok)
  * ph.json        -> "ph"/"groups" AYNEN korunur; "pharmacies" listesi (br_id, name, district,
                      address, phone, on_duty) ve "on_duty_date" yazılır
  * unmatched.json -> eşleşmeyen nöbetçiler (nedeniyle birlikte)

Tarih: Sayfanın adresi sabittir; sayfa dün/bugün/yarın için üç sekme içerir ve her sekme
"12 Ekim P.tesi akşamından 13 Ekim Salı sabahına kadar." gibi bir başlıkla başlar. Betik
bugünün tarihini (Europe/Istanbul) hesaplar ve bu başlıklara göre doğru listeyi seçer.
İsteğe bağlı olarak GENTR_URL ortam değişkeninde {date} / {date_tr} yer tutucusu kullanılabilir.

Kullanım:
  python update_pharmacies_new.py                    # bugünün listesi, dosyaları güncelle
  python update_pharmacies_new.py --dry-run          # yazmadan özet
  python update_pharmacies_new.py --date 2026-10-13  # belirli gün (sayfada varsa: dün/bugün/yarın)
  python update_pharmacies_new.py --html-file x.html # siteye gitmeden, kaydedilmiş sayfayla
  python update_pharmacies_new.py --rows-json x.json # hazır eczane listesiyle (test)

Çıkış kodları: 0 = başarılı (değişiklik olmasa da), 1 = veri alınamadı/yetersiz,
2 = yapılandırma/dosya hatası. Hata durumunda hiçbir dosyaya dokunulmaz.
"""
import argparse
import json
import os
import re
import sys
import time
import unicodedata
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Europe/Istanbul")
ROOT = Path(os.environ.get("NOBET_ROOT") or Path(__file__).resolve().parent)
HTML_FILE = ROOT / "index.html"
PH_FILE = ROOT / "ph.json"
RULES_FILE = ROOT / "brick_rules.json"
UNMATCHED_FILE = ROOT / "unmatched.json"
DEBUG_DIR = ROOT / "debug"

GENTR_URL = os.environ.get("GENTR_URL", "https://www.eczaneler.gen.tr/nobetci-ankara")
MIN_EXPECTED = int(os.environ.get("MIN_EXPECTED", "20"))  # altında veri gelirse dosyalara DOKUNMA
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")

DISTRICTS = [
    "Akyurt", "Altındağ", "Ayaş", "Bahşılı", "Bala", "Balışeyh", "Beypazarı",
    "Çamlıdere", "Çankaya", "Çubuk", "Delice", "Elmadağ", "Etimesgut", "Evren",
    "Gölbaşı", "Güdül", "Haymana", "Kalecik", "Karakeçili", "Kazan", "Keçiören",
    "Keskin", "Kırıkkale", "Kızılcahamam", "Mamak", "Nallıhan", "Polatlı",
    "Pursaklar", "Sincan", "Sulakyurt", "Şereflikoçhisar", "Yahşihan", "Yenimahalle",
]
DISTRICT_ALIASES = {"Kahramankazan": "Kazan"}
KIRIKKALE_DISTRICTS = {"kirikkale", "keskin", "delice", "baliseyh", "karakecili",
                       "yahsihan", "sulakyurt", "bahsili", "celebi"}

# Brick grubu -> o gruptaki bricklerde bulunabilecek ilçeler (yalnızca aday süzmek için)
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
# Beraberlikte ilçenin "ana" brick grubuna +1 puan
PRIMARY_GROUP = {
    "sincan": "sincan etimesgut", "etimesgut": "sincan etimesgut",
    "yenimahalle": "yenimahalle batikent",
    "kecioren": "kecioren pursaklar", "pursaklar": "kecioren pursaklar",
    "altindag": "altindag ulus", "mamak": "mamak", "cankaya": "cankaya merkez",
}


class FetchError(RuntimeError):
    pass


# ---------------------------------------------------------------- yardımcılar
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


def tr_upper(s: str) -> str:
    """Türkçe büyük harf: 'Esra Eczanesi' -> 'ESRA ECZANESİ'."""
    s = unicodedata.normalize("NFC", s or "").replace("i\u0307", "i")
    return s.replace("i", "İ").replace("ı", "I").upper()


def clean(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip()


_DISTRICT_CANON = {norm(d): d for d in DISTRICTS}
_DISTRICT_PAIRS = [(d, d) for d in DISTRICTS] + list(DISTRICT_ALIASES.items())


def detect_district(text: str) -> str:
    t = f" {norm(text)} "
    best = ""
    for name, canon in _DISTRICT_PAIRS:
        if f" {norm(name)} " in t and len(name) > len(best):
            best, found = name, canon
    return found if best else ""


def district_from_piece(piece: str) -> str:
    n, best, found = norm(piece), "", ""
    for name, canon in _DISTRICT_PAIRS:
        if n.startswith(norm(name)) and len(name) > len(best):
            best, found = name, canon
    return found


def is_ankara(r) -> bool:
    if "kirikkale" in norm(r.get("address", "")):
        return False
    return norm(r.get("district", "")) not in KIRIKKALE_DISTRICTS


def today_tr() -> str:
    """Bugünün tarihi (Europe/Istanbul). GitHub Actions 01:00 UTC = 04:00 TR'de çalışır;
    bu saatte sayfada bugünün (akşamdan ertesi sabaha) listesi zaten bulunur."""
    return datetime.now(TZ).date().isoformat()


def build_url(date_iso: str) -> str:
    """GENTR_URL içinde {date} (YYYY-MM-DD) / {date_tr} (GG.AA.YYYY) varsa doldurur."""
    d = date.fromisoformat(date_iso)
    return GENTR_URL.format(date=date_iso, date_tr=d.strftime("%d.%m.%Y"))


# ---------------------------------------------------------------- İNDİRME
def _looks_ok(html: str) -> bool:
    return bool(html) and "/eczane/" in html and "akşamından" in html


def _save_debug(name: str, text: str):
    try:
        DEBUG_DIR.mkdir(exist_ok=True)
        (DEBUG_DIR / name).write_text(text or "", encoding="utf-8")
    except OSError:
        pass


def _fetch_requests(url: str) -> str:
    import requests

    s = requests.Session()
    s.headers.update({
        "User-Agent": UA,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "tr-TR,tr;q=0.9,en;q=0.8",
        "Referer": "https://www.eczaneler.gen.tr/",
    })
    last = None
    for attempt in range(1, 4):
        try:
            r = s.get(url, timeout=30)
            r.encoding = r.encoding if r.encoding and r.encoding.lower() != "iso-8859-1" else "utf-8"
            print(f"[fetch] requests deneme={attempt} HTTP {r.status_code} {len(r.text)} bayt")
            if r.status_code == 200 and _looks_ok(r.text):
                return r.text
            _save_debug("requests.html", r.text)
            last = f"HTTP {r.status_code}" + (" (Cloudflare)" if "cloudflare" in r.text.lower() else "")
            if r.status_code in (403, 503) and "cloudflare" in r.text.lower():
                break  # Cloudflare engeli: tekrar denemek işe yaramaz, sıradaki yönteme geç
        except requests.RequestException as e:
            last = str(e)
        time.sleep(3 * attempt)
    raise FetchError(f"requests: {last}")


def _fetch_curl_cffi(url: str) -> str:
    try:
        from curl_cffi import requests as creq
    except ImportError:
        raise FetchError("curl_cffi kurulu değil")
    last = None
    for imp in ("chrome", "chrome131", "safari", "edge"):
        try:
            r = creq.get(url, impersonate=imp, timeout=30,
                         headers={"Accept-Language": "tr-TR,tr;q=0.9"})
            print(f"[fetch] curl_cffi({imp}) HTTP {r.status_code} {len(r.text)} bayt")
            if r.status_code == 200 and _looks_ok(r.text):
                return r.text
            _save_debug(f"curl_{imp}.html", r.text)
            last = f"HTTP {r.status_code}"
        except Exception as e:  # desteklenmeyen impersonate adı vb.
            last = str(e)
    raise FetchError(f"curl_cffi: {last}")


def _fetch_playwright(url: str) -> str:
    """Gerçek Chromium ile sayfayı açar; Cloudflare'ın otomatik (etkileşimsiz) kontrolünün
    bitmesini bekler. DISPLAY varsa (ör. xvfb-run) görünür modda açılır."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        raise FetchError("playwright kurulu değil")
    headless = not os.environ.get("DISPLAY")
    with sync_playwright() as p:
        br = p.chromium.launch(headless=headless,
                               args=["--disable-blink-features=AutomationControlled"])
        try:
            ctx = br.new_context(locale="tr-TR", timezone_id="Europe/Istanbul", user_agent=UA,
                                 viewport={"width": 1280, "height": 900})
            ctx.add_init_script("Object.defineProperty(navigator,'webdriver',{get:()=>undefined})")
            pg = ctx.new_page()
            resp = pg.goto(url, wait_until="domcontentloaded", timeout=60_000)
            print(f"[fetch] playwright(headless={headless}) ilk yanıt HTTP {resp.status if resp else '?'}")
            html = ""
            for _ in range(40):
                html = pg.content()
                if _looks_ok(html):
                    return html
                pg.wait_for_timeout(1000)
            _save_debug("playwright.html", html)
            try:
                pg.screenshot(path=str(DEBUG_DIR / "playwright.png"), full_page=True)
            except Exception:
                pass
            raise FetchError(f"playwright: içerik gelmedi (başlık: {pg.title()!r})")
        finally:
            br.close()


FETCHERS = [("requests", _fetch_requests), ("curl_cffi", _fetch_curl_cffi),
            ("playwright", _fetch_playwright)]


def fetch_html(url: str) -> str:
    errors = []
    for name, fn in FETCHERS:
        try:
            html = fn(url)
            print(f"[fetch] başarılı: {name}")
            return html
        except Exception as e:
            print(f"[fetch] {name} başarısız: {e}", file=sys.stderr)
            errors.append(str(e))
    _save_debug("errors.txt", "\n".join(errors))
    raise FetchError("; ".join(errors))


# ---------------------------------------------------------------- AYRIŞTIRMA
_MONTHS = {"ocak": 1, "subat": 2, "mart": 3, "nisan": 4, "mayis": 5, "haziran": 6,
           "temmuz": 7, "agustos": 8, "eylul": 9, "ekim": 10, "kasim": 11, "aralik": 12}
_DATE_HEAD_RE = re.compile(r"^\s*(\d{1,2}) (" + "|".join(_MONTHS) + r")\b")
_PHONE_PIECE_RE = re.compile(r"^\+?[\d\s()\-]{10,22}$")
_PHONE_IN_TEXT_RE = re.compile(r"0?\s*\(?\s*\d{3}\s*\)?\s*\d{3}[\s\-]*\d{2}[\s\-]*\d{2}")
_ARROWS = "→»"
_ELINK = 'a[href*="/eczane/"]'


def _head_date(day: int, month: int, ref: date) -> str:
    """Başlıkta yıl yok: referans tarihe en yakın yılı seç (yılbaşı geçişleri için)."""
    best = min((date(y, month, day) for y in (ref.year - 1, ref.year, ref.year + 1)),
               key=lambda d: abs((d - ref).days))
    return best.isoformat()


def _digits_to_phone(s: str) -> str:
    d = re.sub(r"\D", "", s)
    if len(d) == 12 and d.startswith("90"):
        d = "0" + d[2:]
    if len(d) == 10:
        d = "0" + d
    return d


def _container(a):
    """Bu eczane linkini (ve yalnızca onu) içeren en geniş ata öğe = eczane satırı (<tr>/kart)."""
    cur = a
    while cur.parent is not None and getattr(cur.parent, "name", None) not in (None, "body", "html", "[document]"):
        par = cur.parent
        if par.name in ("tbody", "table") or len({x.get("href") for x in par.select(_ELINK)}) > 1:
            break
        cur = par
    return cur


def _parse_row(a):
    cont = _container(a)
    name = clean(a.get_text(" ", strip=True))
    pieces = [clean(s) for s in cont.stripped_strings]
    pieces = [s for s in pieces if s]
    if name in pieces:
        pieces.remove(name)

    phone = ""
    tel = cont.select_one('a[href^="tel:"]')
    if tel is not None:
        phone = _digits_to_phone(tel["href"][4:])
        txt = clean(tel.get_text(" ", strip=True))
        if txt in pieces:
            pieces.remove(txt)
    if not phone:
        for i in range(len(pieces) - 1, -1, -1):
            if _PHONE_PIECE_RE.match(pieces[i]):
                phone = _digits_to_phone(pieces.pop(i))
                break
    if not phone:
        for i, s in enumerate(pieces):
            m = _PHONE_IN_TEXT_RE.search(s)
            if m:
                phone = _digits_to_phone(m.group(0))
                pieces[i] = clean(s.replace(m.group(0), ""))
                break
        pieces = [s for s in pieces if s]

    # sondaki "İlçe [Mahalle]" rozetlerini ayır
    def short(s):
        return len(s) <= 28 and not re.search(r"\d", s) and s[0] not in _ARROWS

    def dlike(s):
        return short(s) and bool(district_from_piece(s))

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
    main_txt = clean(" ".join(pieces[:i]))

    # adres | → konum notu » icap notu
    m = re.search(f"[{_ARROWS}]", main_txt)
    address = clean(main_txt[:m.start()] if m else main_txt)
    notes = [clean(x) for x in re.split(rf"\s*[{_ARROWS}]\s*", main_txt[m.start():])] if m else []
    note = " • ".join(x for x in notes if x)

    # ilçe: rozet > "... X / Ankara" kalıbı > adres taraması > link adresi
    district = ""
    for t in tail:
        district = district_from_piece(t) or district
    if not district:
        mm = re.search(r"([^\s/,\-]+)\s*/\s*(?:Ankara|ANKARA)\b", address)
        if mm:
            district = _DISTRICT_CANON.get(norm(mm.group(1))) or DISTRICT_ALIASES.get(mm.group(1), "")
    if not district:
        district = detect_district(address)
    if not district:
        slug = (a.get("href") or "").rsplit("/", 1)[-1].replace("ankara-", "", 1).split("-")[0]
        district = _DISTRICT_CANON.get(norm(slug), "")

    row = {"name": name, "district": district, "address": address, "phone": phone}
    if note:
        row["note"] = note
    return row


def parse_page(html: str, ref: date = None) -> dict:
    """Sayfayı {YYYY-MM-DD: [eczane satırları]} sözlüğüne çevirir. Her eczane linki, kendinden
    önce gelen en yakın "GG Ay ... akşamından ... sabahına kadar" başlığının gününe atanır."""
    from bs4 import BeautifulSoup

    ref = ref or datetime.now(TZ).date()
    soup = BeautifulSoup(html or "", "lxml")
    out, seen = {}, set()
    for a in soup.select(_ELINK):
        href = a.get("href")
        if not clean(a.get_text(" ", strip=True)):
            continue  # yalnızca ikon içeren link
        head = a.find_previous(
            string=lambda s: bool(s) and bool(_DATE_HEAD_RE.match(norm(s))) and "kadar" in norm(s))
        if head is None:
            continue
        m = _DATE_HEAD_RE.match(norm(head))
        key = _head_date(int(m.group(1)), _MONTHS[m.group(2)], ref)
        if (key, href) in seen:
            continue
        seen.add((key, href))
        out.setdefault(key, []).append(_parse_row(a))
    return out


def dedupe(rows):
    seen, out = set(), []
    for r in rows:
        k = (core_name(r["name"]), norm(r.get("district", "")))
        if core_name(r["name"]) and k not in seen:
            seen.add(k)
            out.append(r)
    return out


# ---------------------------------------------------------------- EŞLEŞTİRME
def brick_score(brick, hay, dist_n, rules, groups):
    rule = rules.get(brick, {}) if isinstance(rules.get(brick), dict) else {}
    s = sum(4 for kw in rule.get("keywords", []) if f" {norm(kw)} " in hay)
    if dist_n and any(norm(d) == dist_n for d in rule.get("districts", [])):
        s += 1
    if dist_n and PRIMARY_GROUP.get(dist_n) and groups.get(brick) == PRIMARY_GROUP[dist_n]:
        s += 1
    toks = [t for t in norm(brick).split() if t not in ("ank", "ankara") and len(t) > 2 and not t.isdigit()]
    s += sum(2 for t in toks if f" {t} " in hay)
    return s


def pick_unique(cands, hay, dist_n, rules, groups):
    scored = sorted(((brick_score(b, hay, dist_n, rules, groups), b) for b in cands), reverse=True)
    if not scored or scored[0][0] <= 0:
        return None
    if len(scored) > 1 and scored[1][0] == scored[0][0]:
        return None
    return scored[0][1]


def match_bricks(rows, ph_items, groups, rules):
    """-> ({brick: [eczane]}, [eşleşmeyen])"""
    idx = {}
    for b, names in ph_items.items():
        for n in names:
            if core_name(n):
                idx.setdefault(core_name(n), set()).add(b)
    all_bricks = list(ph_items)
    generic = {"ilceler", "guney", "kuzey", "dogu", "bati", "merkez"}

    def compatible(b, dists, hay):
        allowed = GROUP_DISTRICTS.get(groups.get(b, ""))
        if allowed is None or not dists or dists & allowed:
            return True
        toks = [t for t in re.findall(r"[a-z0-9]+", norm(b)) if len(t) >= 5 and t not in generic]
        return any(f" {t} " in hay for t in toks)

    result, unmatched = {}, []
    for r in rows:
        key = core_name(r["name"])
        dist_n = norm(r.get("district", ""))
        hay = f" {norm(r.get('address', '') + ' ' + r.get('district', ''))} "
        dists = {d for d in (dist_n, norm(detect_district(r.get("address", "")))) if d}
        chosen, how, reason, cands_out = None, None, None, []

        cands = sorted(idx.get(key, []))
        if cands:  # 1) ph.json'da birebir ad
            pool = [b for b in cands if compatible(b, dists, hay)]
            if not pool:
                reason, cands_out = "district-mismatch", cands
            elif len(pool) == 1:
                chosen, how = pool[0], "ph-name"
            else:
                w = pick_unique(pool, hay, dist_n, rules, groups)
                if w:
                    chosen, how = w, "ph-name+ipucu"
                else:
                    reason, cands_out = "ambiguous", pool

        if not chosen and (not cands or reason == "district-mismatch"):  # 2) kurallar
            pool = [b for b in all_bricks if compatible(b, dists, hay)] or all_bricks
            w = pick_unique(pool, hay, dist_n, rules, groups)
            if w:
                chosen, how, reason, cands_out = w, "rule", None, []
            elif not cands:
                reason = "not-in-ph-no-rule"

        # Görünen ad: ph.json'daki yazım (eşleştiyse), değilse Türkçe büyük harf
        canon = next((n for n in ph_items.get(chosen, []) if core_name(n) == key), None) if chosen else None
        r = {**r, "name": canon or tr_upper(r["name"])}
        if chosen:
            result.setdefault(chosen, []).append({**r, "key": key, "match": how})
        else:
            unmatched.append({**r, "reason": reason, "candidates": cands_out})
    return result, unmatched


# ---------------------------------------------------------------- ph.json
def load_ph():
    if not PH_FILE.exists():
        raise SystemExit(2)
    d = json.loads(PH_FILE.read_text(encoding="utf-8"))
    if not isinstance(d.get("ph"), dict):
        print(f"HATA: {PH_FILE.name} içinde 'ph' (brick -> eczane adları) yok.", file=sys.stderr)
        raise SystemExit(2)
    return d


def update_ph_status(ph_doc: dict, by_brick: dict, date_iso: str) -> dict:
    """ph.json'a nöbet durumunu yazar. 'ph' ve 'groups' DEĞİŞMEZ.
    pharmacies: ph listesindeki her (brick, eczane) için bir kayıt; nöbetçi olanlar on_duty=true.
    Nöbetçi olmayanların adres/telefonu, daha önce öğrenildiyse korunur."""
    prev = {(p.get("br_id"), core_name(p.get("name", ""))): p
            for p in ph_doc.get("pharmacies", []) if isinstance(p, dict)}
    duty = {(b, core_name(it["name"])): it for b, items in by_brick.items() for it in items}

    out, used = [], set()
    for brick, names in ph_doc["ph"].items():
        for name in names:
            k = (brick, core_name(name))
            old, cur = prev.get(k, {}), duty.get(k)
            src = cur or old
            out.append({"br_id": brick, "name": name,
                        "district": src.get("district", ""), "address": src.get("address", ""),
                        "phone": src.get("phone", ""), "on_duty": cur is not None})
            if cur:
                used.add(k)
    # ph listesinde olmayan ama kuralla bir bricke atanan nöbetçiler (yalnızca nöbet günü listelenir)
    for k, it in duty.items():
        if k not in used:
            out.append({"br_id": k[0], "name": it["name"], "district": it.get("district", ""),
                        "address": it.get("address", ""), "phone": it.get("phone", ""),
                        "on_duty": True, "in_ph": False})

    doc = {k: v for k, v in ph_doc.items() if k not in ("pharmacies", "on_duty_date")}
    doc["on_duty_date"] = date_iso
    doc["pharmacies"] = out
    return doc


def dump_ph(doc: dict) -> str:
    """Mevcut ph.json biçimini (indent=0) korur; pharmacies kayıtları satır başına bir eczane."""
    pharm = doc.get("pharmacies")
    base = {k: v for k, v in doc.items() if k != "pharmacies"}
    text = json.dumps(base, ensure_ascii=False, indent=0)
    if pharm is None:
        return text + "\n"
    lines = ",\n".join(json.dumps(p, ensure_ascii=False) for p in pharm)
    return text[:-2] + ',\n"pharmacies": [\n' + lines + "\n]\n}\n"


# ---------------------------------------------------------------- index.html
DATA_RE = re.compile(
    r'<script id="nobet-data">\s*/\* NOBET:START \*/(?:(?!</script>).)*?/\* NOBET:END \*/\s*</script>\s*', re.S)
MAIN_SCRIPT = "<script>"


def inject(html: str, payload: dict) -> str:
    n_before = html.count("</script>")
    m = DATA_RE.search(html)
    if m and (len(m.group(0)) > 600_000 or "function" in m.group(0)):
        raise ValueError("mevcut NOBET bloğu şüpheli; index.html değiştirilmedi")
    html = DATA_RE.sub("", html)
    pos = html.rfind(MAIN_SCRIPT)
    if pos < 0:
        raise ValueError("index.html içinde ana <script> etiketi yok")
    js = json.dumps(payload, ensure_ascii=False, indent=1, sort_keys=True).replace("</", "<\\/")
    block = f'<script id="nobet-data">\n/* NOBET:START */\nconst NOBET = {js};\n/* NOBET:END */\n</script>\n'
    out = html[:pos] + block + html[pos:]
    if out.count("</script>") != (n_before if m else n_before + 1):
        raise ValueError("çıktı doğrulaması başarısız; index.html değiştirilmedi")
    return out


def write_if_changed(path: Path, text: str) -> bool:
    if path.exists() and path.read_text(encoding="utf-8") == text:
        return False
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)
    return True


# ---------------------------------------------------------------- ANA AKIŞ
def get_rows(args, date_iso: str):
    if args.rows_json:
        return json.loads(Path(args.rows_json).read_text(encoding="utf-8")), "rows-json"
    if args.html_file:
        html = Path(args.html_file).read_text(encoding="utf-8")
        src = "html-file"
    else:
        url = build_url(date_iso)
        print(f"[fetch] {url}")
        html = fetch_html(url)
        src = "eczaneler"
        _save_debug("gentr.html", html)
    days = parse_page(html, ref=date.fromisoformat(date_iso))
    print("[parse] sayfadaki günler: " + (", ".join(f"{k}={len(v)}" for k, v in sorted(days.items())) or "yok"))
    rows = days.get(date_iso, [])
    if not rows:
        print(f"HATA: sayfada {date_iso} tarihli liste yok.", file=sys.stderr)
    return dedupe(rows), src


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Ankara nöbetçi eczane güncelleyici (eczaneler.gen.tr)")
    ap.add_argument("--date", help="YYYY-MM-DD (varsayılan: bugün, Europe/Istanbul)")
    ap.add_argument("--dry-run", action="store_true", help="dosyalara yazma, özet göster")
    ap.add_argument("--html-file", help="siteye gitme; kaydedilmiş sayfa HTML'ini ayrıştır")
    ap.add_argument("--rows-json", help="siteye gitme; hazır eczane listesi (JSON) kullan")
    ap.add_argument("--no-index", action="store_true", help="index.html'i güncelleme")
    args = ap.parse_args(argv)

    date_iso = args.date or today_tr()
    try:
        date.fromisoformat(date_iso)
    except ValueError:
        print(f"HATA: geçersiz tarih {date_iso!r} (YYYY-MM-DD olmalı)", file=sys.stderr)
        return 2
    print(f"[info] nöbet tarihi: {date_iso}")

    for f in [PH_FILE] + ([] if args.no_index else [HTML_FILE]):
        if not f.exists():
            print(f"HATA: {f} bulunamadı.", file=sys.stderr)
            return 2

    try:
        rows, source = get_rows(args, date_iso)
    except FetchError as e:
        print(f"HATA: sayfa indirilemedi: {e}. Ayrıntı: debug/ klasörü.", file=sys.stderr)
        return 1

    rows = [r for r in rows if is_ankara(r)]
    if len(rows) < MIN_EXPECTED:
        print(f"HATA: yalnızca {len(rows)} Ankara kaydı (min {MIN_EXPECTED}); dosyalara dokunulmadı.",
              file=sys.stderr)
        return 1

    ph_doc = load_ph()
    rules = json.loads(RULES_FILE.read_text(encoding="utf-8")) if RULES_FILE.exists() else {}
    rules = {k: v for k, v in rules.items() if isinstance(v, dict)}
    by_brick, unmatched = match_bricks(rows, ph_doc["ph"], ph_doc.get("groups", {}), rules)
    matched = sum(len(v) for v in by_brick.values())
    print(f"[match] toplam={len(rows)} eşleşen={matched} eşleşmeyen={len(unmatched)} brick={len(by_brick)}")
    for u in unmatched:
        print(f"   - {u['name']} ({u['district']}): {u['reason']} {u['candidates'] or ''}")

    payload = {"date": date_iso, "source": source, "total": len(rows), "matched": matched,
               "bricks": by_brick}
    new_ph = dump_ph(update_ph_status(ph_doc, by_brick, date_iso))
    new_unm = json.dumps({"date": date_iso, "items": unmatched}, ensure_ascii=False, indent=1)
    try:
        new_html = None if args.no_index else inject(HTML_FILE.read_text(encoding="utf-8"), payload)
    except ValueError as e:
        print(f"HATA: {e}", file=sys.stderr)
        return 2

    if args.dry_run:
        print("[dry-run] dosyalar yazılmadı.")
        return 0
    changed = [p.name for p, t in ((PH_FILE, new_ph), (UNMATCHED_FILE, new_unm), (HTML_FILE, new_html))
               if t is not None and write_if_changed(p, t)]
    print(f"[ok] güncellenen dosyalar: {', '.join(changed) or 'yok (değişiklik yok)'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

</file>

</uploaded_files>
