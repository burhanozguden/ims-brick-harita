#!/usr/bin/env python3
"""
Ankara Eczacı Odası nöbetçi eczanelerini çeker ve index.html içindeki
PH (brick -> eczane) yapısına göre bricklere eşleyip NOBET nesnesi olarak yazar.

Eşleştirme sırası:
  1) Nöbetçi eczane adı, PH içindeki brick bloğunda geçiyorsa -> o brick
  2) brick_rules.json içindeki ilçe/mahalle anahtar kelimeleri
  3) Eşleşmeyenler unmatched.json'a yazılır
"""
import argparse
import json
import re
import sys
import unicodedata
from datetime import datetime
from pathlib import Path

from zoneinfo import ZoneInfo
from bs4 import BeautifulSoup

URL = "https://www.aeo.org.tr/nobetci-eczaneler"
TZ = ZoneInfo("Europe/Istanbul")
ROOT = Path(__file__).resolve().parent
HTML_FILE = ROOT / "index.html"
RULES_FILE = ROOT / "brick_rules.json"
UNMATCHED_FILE = ROOT / "unmatched.json"
DEBUG_DIR = ROOT / "debug"

START = "/* NOBET:START */"
END = "/* NOBET:END */"
MIN_EXPECTED = 20  # bunun altında veri gelirse dosyaya DOKUNMA (scrape bozulmuş demektir)

DISTRICTS = [
    "Akyurt", "Altındağ", "Ayaş", "Bahşılı", "Bala", "Balıseyh", "Beypazarı",
    "Çamlıdere", "Çankaya", "Çubuk", "Delice", "Elmadağ", "Etimesgut", "Evren",
    "Gölbaşı", "Güdül", "Haymana", "Kalecik", "Karakeçili", "Kazan", "Keçiören",
    "Keskin", "Kırıkkale", "Kızılcahamam", "Mamak", "Nallıhan", "Polatlı",
    "Pursaklar", "Sincan", "Sulakyurt", "Şereflikoçhisar", "Yahşihan", "Yenimahalle",
]

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


def detect_district(text: str) -> str:
    t = f" {norm(text)} "
    best = ""
    for d in DISTRICTS:
        if f" {norm(d)} " in t and len(d) > len(best):
            best = d
    return best


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
    soup = BeautifulSoup(html, "lxml")
    for t in soup(["script", "style", "nav", "footer", "header", "select"]):
        t.decompose()
    rows = []
    # a) tablo
    for tr in soup.select("table tr"):
        cells = [c.get_text(" ", strip=True) for c in tr.find_all("td")]
        if len(cells) >= 2 and any("eczane" in norm(c) for c in cells):
            r = _row_from_texts(cells)
            if r:
                rows.append(r)
    if rows:
        return rows
    # b) kart yapısı: "... ECZANESİ" yazan yaprak elementler
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

        if date_iso:  # sadece --date verilirse tarih alanını doldurmayı dene
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

    # tekilleştir
    seen, out = set(), []
    for r in rows:
        k = (core_name(r["name"]), norm(r["district"]))
        if k not in seen and core_name(r["name"]):
            seen.add(k)
            out.append(r)
    print(f"[scrape] kaynak={source} kayıt={len(out)}")
    return out


# ----------------------------------------------------------------- PH PARSE
PH_RE = re.compile(r"(?:\b(?:const|let|var)\s+)?\bPH\s*=\s*\{")
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
    raise ValueError("PH nesnesinin kapanış parantezi bulunamadı")


def load_ph(html: str):
    m = PH_RE.search(html)
    if not m:
        raise SystemExit("HATA: index.html içinde 'PH = {' bulunamadı.")
    end, entries = scan_object(html, m.end() - 1)
    bricks = {k: f" {norm(html[s:e])} " for k, s, e in entries}
    return bricks, end


# ----------------------------------------------------------------- EŞLEŞTİRME
def match_bricks(rows, brick_text, rules):
    result, unmatched = {}, []
    for r in rows:
        key = core_name(r["name"])
        addr_tokens = set(norm(r["address"] + " " + r["district"]).split())
        chosen, how = None, None

        # 1) PH içinde eczane adı geçiyor mu?
        if len(key) >= 4:
            cands = [(b, len(addr_tokens & set(t.split())))
                     for b, t in brick_text.items() if f" {key} " in t]
            if cands:
                chosen, how = max(cands, key=lambda x: x[1])[0], "ph-name"

        # 2) brick_rules.json (ilçe / mahalle anahtar kelimeleri)
        if not chosen:
            hay = f" {norm(r['address'] + ' ' + r['district'])} "
            best, best_score = None, 0
            for b, rule in rules.items():
                if b not in brick_text:
                    continue
                score = sum(2 for kw in rule.get("keywords", []) if f" {norm(kw)} " in hay)
                if any(norm(d) == norm(r["district"]) for d in rule.get("districts", [])):
                    score += 1
                if score > best_score:
                    best, best_score = b, score
            if best:
                chosen, how = best, "rule"

        item = {**r, "key": key, "match": how}
        if chosen:
            result.setdefault(chosen, []).append(item)
        else:
            unmatched.append(r)
    return result, unmatched


# ----------------------------------------------------------------- HTML YAZMA
def inject(html: str, payload: dict, ph_end: int) -> str:
    js = json.dumps(payload, ensure_ascii=False, indent=1, sort_keys=True).replace("</", "<\\/")
    block = f"{START}\nconst NOBET = {js};\n{END}"
    if START in html and END in html:
        s, e = html.index(START), html.index(END) + len(END)
        return html[:s] + block + html[e:]
    k = ph_end + 1
    while k < len(html) and html[k] in " \t":
        k += 1
    if k < len(html) and html[k] == ";":
        k += 1
    return html[:k] + "\n" + block + html[k:]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", help="YYYY-MM-DD (varsayılan: bugün, Europe/Istanbul)")
    ap.add_argument("--dry-run", action="store_true", help="dosyaya yazma, sadece özet bas")
    args = ap.parse_args()

    today = args.date or datetime.now(TZ).strftime("%Y-%m-%d")
    rows = scrape(args.date)
    if len(rows) < MIN_EXPECTED:
        print(f"HATA: yalnızca {len(rows)} kayıt bulundu (min {MIN_EXPECTED}). "
              f"index.html'e dokunulmadı. debug/ klasörüne bakın.", file=sys.stderr)
        sys.exit(1)

    html = HTML_FILE.read_text(encoding="utf-8")
    brick_text, ph_end = load_ph(html)
    rules = json.loads(RULES_FILE.read_text(encoding="utf-8")) if RULES_FILE.exists() else {}

    by_brick, unmatched = match_bricks(rows, brick_text, rules)
    matched = sum(len(v) for v in by_brick.values())
    print(f"[match] toplam={len(rows)} eşleşen={matched} eşleşmeyen={len(unmatched)} "
          f"brick={len(by_brick)}")

    payload = {"date": today, "total": len(rows), "matched": matched, "bricks": by_brick}

    if args.dry_run:
        print(json.dumps(payload, ensure_ascii=False, indent=1)[:3000])
        return

    HTML_FILE.write_text(inject(html, payload, ph_end), encoding="utf-8")
    UNMATCHED_FILE.write_text(
        json.dumps({"date": today, "items": unmatched}, ensure_ascii=False, indent=1),
        encoding="utf-8")
    print("[ok] index.html güncellendi.")


if __name__ == "__main__":
    main()
