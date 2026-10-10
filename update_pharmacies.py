#!/usr/bin/env python3
"""
Ankara Eczacı Odası nöbetçi eczanelerini çeker, index.html içindeki PH
(brick -> eczane adları) yapısına göre bricklere eşler ve NOBET verisini
index.html'e AYRI bir <script id="nobet-data"> bloğu olarak yazar.

NOT: PH ve diğer değişkenler tek bir "const GJ=..., PH=..., BRANS=..." zincirinde
olduğu için PH'nin yanına kod eklemek JS'i bozar. Bu yüzden NOBET, ana script'ten
ÖNCE gelen ayrı bir <script> bloğuna yazılır; PH'ye hiç dokunulmaz.

Eşleştirme sırası:
  1) Nöbetçi eczane adı PH içinde BİREBİR (ECZANESİ ekini yok sayarak) geçiyorsa -> o brick
     (aynı ad birden çok brickte varsa ilçe/mahalle ipuçlarıyla seçilir)
  2) PH'de yoksa brick_rules.json + brick adındaki mahalle ipuçları
  3) Eşleşmeyenler unmatched.json'a (nedeniyle birlikte) yazılır

Kullanım:
  python update_pharmacies.py                 # siteden çek, index.html'i güncelle
  python update_pharmacies.py --dry-run       # yazmadan özet göster
  python update_pharmacies.py --rows-json x.json   # scrape etmeden, hazır JSON listesiyle
"""
import argparse
import json
import re
import sys
import unicodedata
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

URL = "https://www.aeo.org.tr/nobetci-eczaneler"
TZ = ZoneInfo("Europe/Istanbul")
ROOT = Path(__file__).resolve().parent
HTML_FILE = ROOT / "index.html"
RULES_FILE = ROOT / "brick_rules.json"
UNMATCHED_FILE = ROOT / "unmatched.json"
DEBUG_DIR = ROOT / "debug"

MIN_EXPECTED = 20  # bunun altında veri gelirse dosyaya DOKUNMA (scrape bozulmuş demektir)

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
KEYMAP = {
    "name": ["eczaneadi", "eczaneismi", "adi", "ad", "name", "title", "eczane", "baslik"],
    "district": ["ilce", "ilceadi", "ilcesi", "district"],
    "address": ["adres", "adresi", "address", "acikadres"],
    "phone": ["telefon", "tel", "phone", "telefonno", "gsm"],
}


def _pick(d: dict, field: str) -> str:
    nk = {re.sub(r"[^a-z0-9]", "", norm(k)): v for k, v in d.items()}
    for cand in KEYMAP[field]:
        v = nk.get(cand)
        if isinstance(v, (str, int)) and str(v).strip():
            return str(v).strip()
    return ""


def _find_lists(obj):
    if isinstance(obj, list):
        if obj and all(isinstance(x, dict) for x in obj):
            yield obj
        for x in obj:
            yield from _find_lists(x)
    elif isinstance(obj, dict):
        for v in obj.values():
            yield from _find_lists(v)


def rows_from_json(payloads):
    best = []
    for data in payloads:
        for lst in _find_lists(data):
            rows = []
            for d in lst:
                name, addr = _pick(d, "name"), _pick(d, "address")
                dist = _pick(d, "district") or detect_district(addr)
                if name and (addr or dist):
                    rows.append({"name": name, "district": dist,
                                 "address": addr, "phone": _pick(d, "phone")})
            if len(rows) > len(best):
                best = rows
    return best


def _row_from_texts(texts, forced_name=None):
    texts = [t for t in texts if t]
    if not texts:
        return None
    name = forced_name or next(
        (t for t in texts if "eczane" in norm(t) and len(t) < 80), texts[0])
    rest = [t for t in texts if t != name]
    address = max(rest, key=len) if rest else ""
    joined = " | ".join(texts)
    m = PHONE_RE.search(joined)
    return {"name": name, "district": detect_district(joined),
            "address": address, "phone": m.group(0).strip() if m else ""}


def rows_from_dom(html):
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "lxml")
    for t in soup(["script", "style", "nav", "footer", "header", "select"]):
        t.decompose()
    rows = []
    for tr in soup.select("table tr"):
        cells = [c.get_text(" ", strip=True) for c in tr.find_all("td")]
        if len(cells) >= 2 and any("eczane" in norm(c) for c in cells):
            r = _row_from_texts(cells)
            if r:
                rows.append(r)
    if rows:
        return rows
    for el in soup.find_all(True):
        if el.find(True) is not None:
            continue
        txt = el.get_text(" ", strip=True)
        n = norm(txt)
        if not txt or len(txt) > 80 or not (n.endswith("eczanesi") or n.endswith("eczane")):
            continue
        box = el
        for _ in range(3):
            if len(box.get_text(" ", strip=True)) >= len(txt) + 25 or box.parent is None:
                break
            box = box.parent
        r = _row_from_texts(list(box.stripped_strings), forced_name=txt)
        if r and r["address"]:
            rows.append(r)
    return rows


def scrape(date_iso: str):
    from playwright.sync_api import sync_playwright

    DEBUG_DIR.mkdir(exist_ok=True)
    json_payloads, fragments, net_log = [], [], []

    with sync_playwright() as p:
        browser = p.chromium.launch()
        ctx = browser.new_context(
            locale="tr-TR", timezone_id="Europe/Istanbul",
            user_agent=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"),
        )
        page = ctx.new_page()

        def on_response(resp):
            try:
                ct = resp.headers.get("content-type", "")
                net_log.append({"url": resp.url, "status": resp.status, "type": ct})
                if "json" in ct:
                    json_payloads.append(resp.json())
                elif "html" in ct and resp.url.rstrip("/") != URL.rstrip("/") \
                        and "aeo.org.tr" in resp.url:
                    fragments.append(resp.text())
            except Exception:
                pass

        page.on("response", on_response)
        page.goto(URL, wait_until="networkidle", timeout=60_000)

        if date_iso:
            try:
                page.locator("input[type=date], input[name*=arih i], input[id*=arih i]") \
                    .first.fill(date_iso, timeout=3000)
            except Exception:
                pass

        try:
            page.get_by_role("button", name=re.compile(r"^\s*Ara\s*$", re.I)).first \
                .click(timeout=5000)
        except Exception:
            try:
                page.locator("text=/^\\s*Ara\\s*$/i").first.click(timeout=5000)
            except Exception:
                pass
        try:
            page.wait_for_load_state("networkidle", timeout=20_000)
        except Exception:
            pass
        page.wait_for_timeout(2500)

        html = page.content()
        (DEBUG_DIR / "page.html").write_text(html, encoding="utf-8")
        page.screenshot(path=str(DEBUG_DIR / "screenshot.png"), full_page=True)
        browser.close()

    (DEBUG_DIR / "network.json").write_text(
        json.dumps(net_log, ensure_ascii=False, indent=1), encoding="utf-8")

    rows = rows_from_json(json_payloads)
    source = "json"
    if len(rows) < MIN_EXPECTED:
        for frag in fragments:
            rows = rows_from_dom(frag)
            if len(rows) >= MIN_EXPECTED:
                source = "html-fragment"
                break
    if len(rows) < MIN_EXPECTED:
        rows = rows_from_dom(html)
        source = "dom"

    seen, out = set(), []
    for r in rows:
        k = (core_name(r["name"]), norm(r["district"]))
        if k not in seen and core_name(r["name"]):
            seen.add(k)
            out.append(r)
    print(f"[scrape] kaynak={source} kayıt={len(out)}")
    return out


# ----------------------------------------------------------------- JS NESNE OKUMA
PH_RE = re.compile(r"\bPH\s*=\s*\{")
META_RE = re.compile(r"\bMETA\s*=\s*\{")
IDENT_KEY = re.compile(r"([A-Za-z_$][\w$]*)\s*:")


def scan_object(src: str, start: int):
    """src[start]=='{'. -> (kapanış '}' indeksi, [(key, value_start, value_end), ...])"""
    n, i, depth = len(src), start, 0
    entries, key, vstart = [], None, None

    def close(pos):
        nonlocal key, vstart
        if key is not None:
            entries.append((key, vstart, pos))
            key, vstart = None, None

    while i < n:
        c = src[i]
        if c in "\"'`":
            j = i + 1
            while j < n and src[j] != c:
                j += 2 if src[j] == "\\" else 1
            if depth == 1 and key is None:
                k = j + 1
                while k < n and src[k].isspace():
                    k += 1
                if k < n and src[k] == ":":
                    key, vstart, i = src[i + 1:j], k + 1, k + 1
                    continue
            i = j + 1
            continue
        if src.startswith("//", i):
            j = src.find("\n", i)
            i = n if j < 0 else j
            continue
        if src.startswith("/*", i):
            j = src.find("*/", i + 2)
            i = n if j < 0 else j + 2
            continue
        if depth == 1 and key is None and (i == 0 or not (src[i - 1].isalnum() or src[i - 1] in "_$")):
            m = IDENT_KEY.match(src, i)
            if m:
                key, vstart, i = m.group(1), m.end(), m.end()
                continue
        if c in "{[(":
            depth += 1
        elif c in "}])":
            depth -= 1
            if depth == 0:
                close(i)
                return i, entries
        elif c == "," and depth == 1:
            close(i)
        i += 1
    raise ValueError("JS nesnesinin kapanış parantezi bulunamadı")


def load_ph(html: str) -> dict:
    """PH -> {brick: [eczane adı, ...]}"""
    m = PH_RE.search(html)
    if not m:
        raise SystemExit("HATA: index.html içinde 'PH = {' bulunamadı.")
    _, entries = scan_object(html, m.end() - 1)
    out = {}
    for k, s, e in entries:
        txt = html[s:e].strip()
        try:
            arr = json.loads(txt)
        except Exception:
            arr = re.findall(r'"((?:[^"\\]|\\.)*)"', txt)
        out[k] = [a for a in arr if isinstance(a, str)]
    return out


def load_groups(html: str) -> dict:
    """META -> {brick: normalize edilmiş grup adı}  (bulunamazsa boş)"""
    m = META_RE.search(html)
    if not m:
        return {}
    try:
        end, _ = scan_object(html, m.end() - 1)
        meta = json.loads(html[m.end() - 1:end + 1])
        return {b: norm(v.get("g", "")) for b, v in meta.items()}
    except Exception:
        return {}


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

    def compatible(b, dist_n):
        allowed = GROUP_DISTRICTS.get(groups.get(b, ""))
        return allowed is None or not dist_n or dist_n in allowed

    for r in rows:
        key = core_name(r["name"])
        dist_n = norm(r.get("district", ""))
        hay = f" {norm(r.get('address', '') + ' ' + r.get('district', ''))} "
        chosen, how, reason, cands_out = None, None, None, []

        # 1) PH içinde birebir ad eşleşmesi
        cands = sorted(idx.get(key, []))
        if cands:
            pool = [b for b in cands if compatible(b, dist_n)]
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
        if not chosen and not cands:
            pool = [b for b in all_bricks if compatible(b, dist_n)] or all_bricks
            w = pick_unique(pool, hay, dist_n, rules, groups)
            if w:
                chosen, how = w, "rule"
            else:
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


def inject(html: str, payload: dict) -> str:
    n_before = html.count("</script>")
    had_block = bool(DATA_RE.search(html))
    html = clean_html(html)
    m = PH_RE.search(html)
    if not m:
        raise SystemExit("HATA: PH bulunamadı.")
    pos = html.rfind("<script", 0, m.start())
    if pos < 0:
        raise SystemExit("HATA: PH'yi içeren <script> etiketi bulunamadı.")
    js = json.dumps(payload, ensure_ascii=False, indent=1, sort_keys=True).replace("</", "<\\/")
    block = ('<script id="nobet-data">\n/* NOBET:START */\n'
             f"const NOBET = {js};\n/* NOBET:END */\n</script>\n")
    out = html[:pos] + block + html[pos:]
    # Güvenlik: </script> sayısı korunmalı (eski blok varsa aynı, yoksa +1) ve uygulama kodu eksilmemeli.
    expected = n_before if had_block else n_before + 1
    if out.count("</script>") != expected or not PH_RE.search(out):
        raise SystemExit("HATA: çıktı doğrulaması başarısız; index.html DEĞİŞTİRİLMEDİ.")
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", help="YYYY-MM-DD (varsayılan: bugün, Europe/Istanbul)")
    ap.add_argument("--dry-run", action="store_true", help="dosyaya yazma, özet bas")
    ap.add_argument("--rows-json", help="scrape etme; bu JSON listesini kullan (test için)")
    args = ap.parse_args()

    today = args.date or datetime.now(TZ).strftime("%Y-%m-%d")
    if args.rows_json:
        rows = json.loads(Path(args.rows_json).read_text(encoding="utf-8"))
    else:
        rows = scrape(args.date)

    rows = [r for r in rows if is_ankara(r)]
    if len(rows) < MIN_EXPECTED:
        print(f"HATA: yalnızca {len(rows)} Ankara kaydı bulundu (min {MIN_EXPECTED}). "
              f"index.html'e dokunulmadı. debug/ klasörüne bakın.", file=sys.stderr)
        sys.exit(1)

    html = clean_html(HTML_FILE.read_text(encoding="utf-8"))
    ph_items = load_ph(html)
    groups = load_groups(html)
    rules = json.loads(RULES_FILE.read_text(encoding="utf-8")) if RULES_FILE.exists() else {}

    by_brick, unmatched = match_bricks(rows, ph_items, groups, rules)
    matched = sum(len(v) for v in by_brick.values())
    print(f"[match] toplam={len(rows)} eşleşen={matched} eşleşmeyen={len(unmatched)} "
          f"brick={len(by_brick)}")
    for u in unmatched:
        print(f"   - {u['name']} ({u['district']}): {u['reason']} {u['candidates'] or ''}")

    payload = {"date": today, "total": len(rows), "matched": matched, "bricks": by_brick}

    if args.dry_run:
        return

    HTML_FILE.write_text(inject(html, payload), encoding="utf-8")
    UNMATCHED_FILE.write_text(
        json.dumps({"date": today, "items": unmatched}, ensure_ascii=False, indent=1),
        encoding="utf-8")
    print("[ok] index.html güncellendi.")


if __name__ == "__main__":
    main()
