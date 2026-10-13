# ims-brick-harita

Ankara 2026 brick dağılımını (5 kişi, 83 brick) ve **günün nöbetçi eczanelerini** gösteren tek sayfalık Leaflet haritası (`index.html`). Sol panelde (telefonda alttan açılan çekmecede) her kişinin kendi bricklerindeki bugünün nöbetçileri listelenir; ortak bricklerdeki eczane her iki kişinin altında da görünür. Eczane aranabilir; telefon, not ve yol tarifi bağlantısı gösterilir.

Yayın: https://burhanozguden.github.io/ims-brick-harita/

## Dosyalar
- `index.html` — harita ve panel; nöbet verisi `<script id="nobet-data">` bloğundaki `NOBET` değişkenindedir (betik yazar, birkaç günü birden içerir)
- `update_pharmacies.py` — güncelleyici (Ankara Eczacı Odası API'si → `index.html`, `mahalle_bricks.json`, `unmatched.json`)
- `ph.json` — brick → tüm eczane adları (`ph`) ve brick grupları (`groups`); elle düzenlenir, betik değiştirmez
- `brick_rules.json` — eşleşmeyenler için ipucu kuralları
- `mahalle_bricks.json` — otomatik öğrenilen "ilçe|mahalle → brick" tablosu (bkz. Eşleştirme)
- `unmatched.json` — son çalıştırmada her gün için eşleşmeyen nöbetçiler
- `.github/workflows/update_map.yml` — otomatik güncelleme (günde 3 kez)
- `aeo_api_findings.md` — AEO API'sinin incelenme notları

Kişi–brick dağılımı `index.html` içindeki `OWN` / `PERS` verisindedir.

## Veri kaynağı ve "her zaman güncel" mantığı
Ankara Eczacı Odası'nın kendi sayfasının kullandığı JSON API'si (ayrıntı: `aeo_api_findings.md`):

```
GET https://www.aeo.org.tr/getPharmacies/{YYYY-MM-DD}
```

- API bugün dahil **10 güne kadar** ileri tarihli liste verir. Betik her çalışmada dünden +9 güne kadar tüm günleri çeker ve `index.html`'deki önceki günlerle birleştirir (2 günden eski olanlar silinir).
- Sayfa, açıldığı andaki saate göre geçerli günü **kendisi** seçer. Nöbet günü TR saatiyle **08:30**'da değişir (`index.html` içinde `NOBET_DEGISIM_DK`); 08:30'dan önce dünün listesi gösterilir. Sayfa açık kalırsa 08:30'da ya da sekmeye geri dönüldüğünde kendini yeniler. Bu sayede workflow birkaç gün çalışmasa bile sayfa güncel listeyi gösterir.
- Geçerli günün listesi yoksa en yakın önceki gün sarı uyarıyla gösterilir.
- API "yarın" istendiğinde, o anda geçerli olan (bugünün) listeyi döndürür (*"14.10 saat 08:30'a kadar 13.10 tarihli liste geçerlidir"*). Bu yüzden yanıttaki `date` alanı istenen tarihle aynı değilse o gün atlanır ve eldeki veri korunur.

Site Cloudflare arkasında olduğu için istek sırayla şu yöntemlerle denenir; bir yöntem engellenirse kalan günler sıradakiyle çekilir:
1. düz HTTP isteği (`plain`)
2. Playwright Chromium: sayfa açılır, Cloudflare kontrolü bittikten sonra API aynı oturumdan çağrılır (`playwright`)
3. `curl_cffi` (Chrome TLS parmak izi) (`curl`)

> eczaneler.gen.tr kaynağı denendi ancak Cloudflare tarafından GitHub Actions'tan (ve düz isteklerden) 403 ile engellendiği için bırakıldı; 13 Ekim'deki başarısız çalışmanın nedeni buydu.

## Eşleştirme
Her nöbetçinin `match` alanı nasıl eşleştiğini gösterir:
1. **`ph-name`** — ad `ph.json` → `ph` içinde geçiyor ("ECZANESİ" eki, Türkçe karakterler, parantez içi — ör. "(VERESELİ)" — ve baştaki ilçe adı — ör. "MAMAK GÜRPINAR" → "GÜRPINAR" — yok sayılır). Aday brick ilçeyle uyumsuzsa yalnızca adındaki semt adresinde de geçiyorsa kabul edilir (ör. "YAŞAMKENT FEYZA" — Yaşamkent Mah.).
2. **`ph-name+ipucu`** — ad birden çok brickte geçiyor; ilçe/mahalle ipuçlarıyla seçildi.
3. **`ph-name+mahalle`** — ad var ama ilçe uyumsuz; aynı mahalledeki **başka** bir eczane de `ph.json`'da o brickte olduğu için kabul edildi (ör. Yaşamkent/Alacaatlı eczaneleri `ph.json`'da ERYAMAN'da).
4. **`mahalle`** — ad `ph.json`'da yok; aynı ilçe+mahalledeki bilinen eczanelerin brick'i (en az 2 eczane destekliyorsa kurallardan önce, 1 eczane destekliyorsa kurallardan sonra).
5. **`rule`** — `brick_rules.json` kuralları (`keywords` = adreste geçen mahalle/semt, `districts` = ilçe) ve brick adındaki mahalle ipuçları. Bir brick'in `keywords`'ü adreste geçiyorsa, brick grubunun ilçe filtresi de aşılır (ör. Temelli → POLATLI -2).
6. Eşleşmeyenler nedeniyle birlikte `unmatched.json`'a yazılır: `ambiguous`, `not-in-ph-no-rule`, `district-mismatch`. Çözmek için eczaneyi `ph.json`'da doğru bricke ekleyin.

`mahalle_bricks.json` her çalışmada `ph.json`'daki adlardan öğrenilir: "ilçe|mahalle" → {brick: [eczaneler]}. Eczane adları listelendiği için hiçbir kayıt iki kez sayılmaz; yanlış bir kaydı elle silebilirsiniz. Tabloyu geçmiş verilerle baştan beslemek için: `python update_pharmacies.py --learn-back 60`.

Kırıkkale ilçeleri haritaya alınmaz.

## Kurulum ve kullanım
```bash
pip install -r requirements.txt
python -m playwright install chromium     # yalnızca 2. yöntem için

python update_pharmacies.py                      # dünden +9 güne kadar çek, dosyaları güncelle
python update_pharmacies.py --dry-run            # yazmadan özet
python update_pharmacies.py --date 2026-10-13    # yalnızca bu günü çek (diğer günler korunur)
python update_pharmacies.py --learn-back 60      # mahalle tablosunu son 60 günle de besle
python update_pharmacies.py --fetcher curl       # yalnızca bir yöntemle (plain / playwright / curl)
python update_pharmacies.py --rows-json x.json --date 2026-10-13   # siteye gitmeden hazır listeden (test)
```

Bir günde 20'den az Ankara kaydı gelirse o gün atlanır. Hiçbir gün alınamazsa betik çıkış kodu `1` ile durur ve dosyalara dokunmaz; ayrıntılar `debug/` klasöründedir.

## Otomatik güncelleme (GitHub Actions)
`.github/workflows/update_map.yml` her gün **06:15, 09:00 ve 17:00 (TR)** çalışır: Python 3.12 ve Chromium'u kurar, betiği çalıştırır, değişiklik varsa `index.html`, `unmatched.json` ve `mahalle_bricks.json`'ı commit edip push eder. Betik hata verirse workflow kırmızı olur ve `debug/` artifact olarak yüklenir. Geçerli günün listesi alınamadıysa çalışma sarı uyarı (`::warning::`) verir. Elle çalıştırma: Actions → *Nöbetçi Eczaneleri Güncelle* → *Run workflow*.

Gerekenler: Settings → Actions → General → Workflow permissions: **Read and write permissions** (ek secret gerekmez).

> ⚠️ Workflow dosyası GitHub'ın web arayüzünden yüklenirken dosyanın içine başka metin (ör. yapay zekâ sohbetinden kopyalanan `<uploaded_files>` etiketleri) karışmamalıdır; `.yml` geçerli YAML, `.py` geçerli Python olmalıdır.
