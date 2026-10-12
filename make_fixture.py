"""Gözlenen eczaneler.gen.tr yapısını taklit eden test sayfası üretir (dün/bugün/yarın sekmeleri)."""
import json, sys
from datetime import date, timedelta
from pathlib import Path

AY = ["", "Ocak", "Şubat", "Mart", "Nisan", "Mayıs", "Haziran", "Temmuz", "Ağustos", "Eylül", "Ekim", "Kasım", "Aralık"]
GUN = ["P.tesi", "Salı", "Çarşamba", "Perşembe", "Cuma", "Cumartesi", "Pazar"]
ILCE = {"sincan etimesgut": "Etimesgut", "yenimahalle batikent": "Yenimahalle", "kecioren pursaklar": "Keçiören",
        "altindag ulus": "Altındağ", "mamak": "Mamak", "cankaya merkez": "Çankaya"}

def head(d):
    n = d + timedelta(days=1)
    return f"{d.day} {AY[d.month]} {GUN[d.weekday()]} akşamından {n.day} {AY[n.month]} {GUN[n.weekday()]} sabahına kadar."

def row(name, addr, dist, phone, note=None, extra=None):
    slug = name.lower().replace(" ", "-")
    n = f'<div class="text-muted"><i>→ {note}</i></div>' if note else ""
    badges = f'<span class="badge">{dist}</span>' + (f' <span class="badge bg-secondary">{extra}</span>' if extra else "")
    return (f'<tr><td><a href="/eczane/ankara-{slug}">{name.title()}</a><br>'
            f'<a href="/eczane/ankara-{slug}"><img src="/img/map.png"></a></td>'
            f'<td>{addr}{n}<div>{badges}</div></td><td>{phone}</td></tr>\n'
            '<tr><td colspan="3">Daha fazlasını keşfedin <a href="/x">Nöbetçi eczane listesi</a></td></tr>\n')

def pane(pid, d, rows):
    return (f'<div class="tab-pane" id="{pid}"><div class="alert">{head(d)}</div>'
            f'<table><thead><tr><th>Eczane</th><th>Adres</th><th>Telefon</th></tr></thead><tbody>{"".join(rows)}</tbody></table></div>')

def build(ph_path, today, n=30):
    ph = json.loads(Path(ph_path).read_text(encoding="utf-8"))
    picks = []
    for b, names in ph["ph"].items():
        dist = ILCE.get(ph["groups"].get(b))
        if dist and names:
            picks.append((names[0], dist))
        if len(picks) >= n:
            break
    rows = [row(nm, f"Örnek Mahallesi, Test Sokak No:{i}/A {dist} / Ankara", dist, f"0 (312) 3{i:02d}-11-22",
                note="Sağlık Ocağı karşısı" if i % 3 == 0 else None, extra="Ulucanlar" if i % 5 == 0 else None)
            for i, (nm, dist) in enumerate(picks, 1)]
    rows.append(row("YOKBÖYLE ECZANESİ", "Eryaman Mah. 1. Cad. No:3 Etimesgut / Ankara", "Etimesgut", "0 (312) 999-00-00"))
    rows.append(row("KIRIKKALE TEST ECZANESİ", "Merkez Kırıkkale", "Kırıkkale", "0 (318) 111-22-33"))
    y, t = today - timedelta(days=1), today + timedelta(days=1)
    other = [row("DÜN ECZANESİ", "X Mah. Çankaya / Ankara", "Çankaya", "0 (312) 111-11-11")]
    return ("<html><body><ul class='nav'><a href='#nav-dun'>Pazar</a><a href='#nav-bugun'>P.tesi</a><a href='#nav-yarin'>Salı</a></ul>"
            + pane("nav-dun", y, other) + pane("nav-bugun", today, rows) + pane("nav-yarin", t, other) + "</body></html>")

if __name__ == "__main__":
    print(build(sys.argv[1], date.fromisoformat(sys.argv[2])))
