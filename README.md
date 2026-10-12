# ims-brick-harita

Ankara 2026 brick dağılımını (5 kişi, 83 brick) ve **günün nöbetçi eczanelerini** gösteren tek sayfalık Leaflet haritası (`index.html`). Sol panelde (telefonda alttan açılan çekmecede) her kişinin kendi bricklerindeki bugünün nöbetçileri listelenir; ortak bricklerdeki eczane her iki kişinin altında da görünür. Eczane aranabilir; telefon, not ve yol tarifi bağlantısı gösterilir.

Yayın: https://burhanozguden.github.io/ims-brick-harita/

## Dosyalar
- `index.html` — harita ve panel; nöbet verisi `<script id="nobet-data">` bloğundaki `NOBET` değişkenindedir (betik yazar)
- `update_pharmacies_new.py` — **yeni** günlük güncelleyici (eczaneler.gen.tr → `index.html`, `ph.json`, `unmatched.json`)
- `update_pharmacies.py` — eski betik (AEO API + eczaneler.gen.tr); artık workflow'da kullanılmıyor, istenirse silinebilir
- `ph.json` — brick → tüm eczane adları (`ph`), brick grupları (`groups`) ve günlük nöbet durumu (`pharmacies`, `on_duty_date`)
- `brick_rules.json` — eşleşmeyenler için ipucu kuralları
- `unmatched.json` — son çalıştırmada eşleşmeyen nöbetçiler
- `.github/workflows/nobet.yml` — günlük otomatik güncelleme (01:00 UTC = 04:00 TR)
- `tests/make_fixture.py` — internete çıkmadan test için örnek sayfa üretir

Kişi–brick dağılımı `index.html` içindeki `OWN` / `PERS` verisindedir.

## Veri kaynağı ve tarih
`https://www.eczaneler.gen.tr/nobetci-ankara` — sayfa adresi sabittir; içinde **dün / bugün / yarın** sekmeleri (`#nav-...`) vardır ve her sekme şu tür bir başlıkla başlar:

```
12 Ekim P.tesi akşamından 13 Ekim Salı sabahına kadar.
```

Betik bugünün tarihini **Europe/Istanbul** saat dilimine göre hesaplar ve bu başlıklardaki tarihe göre doğru listeyi seçer (yıl başlıkta yazmaz; yılbaşı geçişi otomatik çözülür). Site ileride tarihi adrese taşırsa `GENTR_URL` ortam değişkeniyle şablon verilebilir: `{date}` = `YYYY-MM-DD`, `{date_tr}` = `GG.AA.YYYY` (ör. `GENTR_URL="https://.../nobetci-ankara?tarih={date}"`).

Sayfa sırasıyla şu yöntemlerle indirilir; ilk başarılı olan kullanılır:
1. `requests` (normal tarayıcı başlıklarıyla, 3 deneme)
2. `curl_cffi` (Chrome/Safari/Edge TLS parmak izi)
3. `playwright` Chromium (workflow'da `xvfb-run` ile görünür modda; Cloudflare'ın otomatik kontrolünün bitmesini bekler, CAPTCHA çözmeye çalışmaz)

Bu site resmi oda sitesi değil, Ankara Eczacı Odası listelerini derleyen üçüncü taraf bir portaldır; betik günde bir istek atar.

> ⚠️ **Cloudflare uyarısı:** Site Cloudflare korumalıdır. Bazı sunucu IP'lerinden (bulut/veri merkezi) gelen isteklere "Bir dakika lütfen…" doğrulama sayfası döner. GitHub Actions'ta da engellenirse workflow **kırmızı (fail)** olur, dosyalara dokunulmaz ve `debug` artifact'ında dönen sayfa/ekran görüntüsü bulunur. Bu durumda: sayfayı kendi tarayıcınızda açıp *Farklı Kaydet → Yalnızca HTML* ile kaydedin ve `python update_pharmacies_new.py --html-file sayfa.html` çalıştırıp sonucu commit edin.

## Eşleştirme
1. Eczane adı `ph.json` → `ph` içinde birebir geçiyorsa o brick ("ECZANESİ" eki ve Türkçe karakter farkları yok sayılır; aynı ad birden çok brickte varsa ilçe/mahalle ipuçlarıyla seçilir).
2. `ph`'de yoksa `brick_rules.json` kuralları (`keywords` = adreste geçen mahalle/semt, `districts` = ilçe) ve brick adındaki mahalle ipuçları.
3. Eşleşmeyenler nedeniyle birlikte `unmatched.json`'a yazılır: `ambiguous`, `not-in-ph-no-rule`, `district-mismatch`.

Kırıkkale ilçeleri haritaya alınmaz.

## ph.json biçimi
`ph` ve `groups` **değiştirilmez** (elle düzenlemeye devam edebilirsiniz). Betik her çalıştırmada şunları yeniden yazar:

```json
"on_duty_date": "2026-10-12",
"pharmacies": [
{"br_id": "ANK AKKOPRU", "name": "AHİ EVRAN ECZANESİ", "district": "Altındağ", "address": "...", "phone": "03123011122", "on_duty": true},
{"br_id": "ANK AKKOPRU", "name": "CELAL ECZANESİ", "district": "", "address": "", "phone": "", "on_duty": false}
]
```

- `ph` listesindeki her (brick, eczane) için bir kayıt vardır; o gün nöbetçi olanlar `on_duty: true`, diğerleri `false`.
- Nöbetçi olmayan eczanenin adres/telefonu, önceki günlerde öğrenildiyse korunur (zamanla dolar).
- `ph` listesinde olmayıp kuralla bir bricke atanan nöbetçiler yalnızca o gün `"in_ph": false` ile eklenir.

## Kurulum ve kullanım
```bash
pip install -r requirements.txt
python -m playwright install chromium     # yalnızca 3. yöntem için

python update_pharmacies_new.py                      # bugünün listesi, dosyaları güncelle
python update_pharmacies_new.py --dry-run            # yazmadan özet
python update_pharmacies_new.py --date 2026-10-13    # sayfadaki başka bir gün (dün/yarın)
python update_pharmacies_new.py --html-file x.html   # kaydedilmiş sayfadan
python update_pharmacies_new.py --rows-json x.json   # hazır listeden (test)
python update_pharmacies_new.py --no-index           # index.html'e dokunma

# İnternetsiz test:
python tests/make_fixture.py ph.json 2026-10-12 > /tmp/fx.html
python update_pharmacies_new.py --html-file /tmp/fx.html --date 2026-10-12 --dry-run
```

Çıkış kodları: `0` başarılı (değişiklik olmasa da), `1` sayfa indirilemedi / istenen gün yok / 20'den az kayıt, `2` dosya/yapılandırma hatası. Hata durumunda hiçbir dosya değiştirilmez. Dosyalar yalnızca içerik değiştiyse yazılır.

## Otomatik güncelleme (GitHub Actions)
`.github/workflows/nobet.yml` her gün **01:00 UTC (04:00 TR)** çalışır: Python 3.12 kurar, gereksinimleri yükler, betiği çalıştırır, değişiklik varsa `index.html`, `ph.json`, `unmatched.json`'ı commit edip push eder. Betik hata verirse workflow fail olur ve `debug/` artifact olarak yüklenir. Elle çalıştırma: Actions → *Nöbetçi eczaneleri güncelle* → *Run workflow* (isteğe bağlı tarih girilebilir).

Gerekenler:
- Settings → Actions → General → Workflow permissions: **Read and write permissions**. Ek secret gerekmez (`GITHUB_TOKEN` otomatik).
- Eski `.github/workflows/update_map.yml` dosyasını **silin**; yoksa eski betik de çalışır ve iki iş aynı dosyaları günceller.

Harita, nöbet gününün sabah 09:00'da değiştiğini varsayar (`index.html` içinde `NOBET_SAATI`); 04:00'te yazılan bugünün tarihi sayfada "güncel" görünür.

## Güncelleme notları (2026-10-12)
- Yeni `update_pharmacies_new.py`: yalnızca eczaneler.gen.tr; `requests` + BeautifulSoup, curl_cffi/Playwright yedekleri; tarih Europe/Istanbul'a göre otomatik.
- Eşleşen eczanelerin adı `ph.json`'daki yazımla (BÜYÜK HARF) gösterilir; eşleşmeyenler Türkçe büyük harfe çevrilir.
- `ph.json`'a `pharmacies` (on_duty bayraklı) ve `on_duty_date` eklendi; `ph`/`groups` aynen korunur.
- Workflow `nobet.yml`: tek çalışma 01:00 UTC, `ph.json` da commit ediliyor, hatada fail + debug artifact, elle tarih girişi.
- `requirements.txt`: `requests` eklendi, sürüm alt sınırları belirtildi.
