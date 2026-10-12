from flask import Flask, render_template, jsonify, request, send_from_directory
import base64
import hashlib
import hmac
import json
import os
import random
import secrets
import time
from datetime import datetime, timedelta, timezone

import openpyxl
import requests

# --- AYARLAR ---
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
TEMPLATE_DIR = os.path.join(BASE_DIR, 'templates')
STATIC_DIR = os.path.join(BASE_DIR, 'static')
EXCEL_FILE = os.path.join(BASE_DIR, 'sorular.xlsx')

# --- OYUN KURALLARI ---
SORU_SAYISI = 20
KOLAY_SAYISI = 7          # Oyun 7 kolay soruyla açılır, kalanı zor
SORU_SURESI = 20          # Soru başına saniye
GECIS_SURESI = 1.6        # Cevaptan sonra doğru/yanlış gösterilen süre (app.js ile aynı olmalı)
TABAN_PUAN = 100
MAX_HIZ_BONUSU = 50
SERI_BONUSU = 10          # Seri uzadıkça her doğru için eklenir
MAX_SERI_BONUSU = 50
OYUN_OMRU = 60 * 60       # Bu kadar saniyeden eski oyunlar geçersiz
BOLGELER = ('Avrupa', 'Anadolu', 'Karışık')
ISTANBUL = timezone(timedelta(hours=3))

# --- SKOR VERİTABANI (Upstash Redis) ---
# Vercel'deki Upstash bağlantısı bu ortam değişkenlerini kendisi tanımlar.
REDIS_URL = (os.environ.get('KV_REST_API_URL') or os.environ.get('UPSTASH_REDIS_REST_URL') or '').rstrip('/')
REDIS_TOKEN = os.environ.get('KV_REST_API_TOKEN') or os.environ.get('UPSTASH_REDIS_REST_TOKEN')
ANAHTAR_ON_EKI = os.environ.get('SKOR_ON_EKI', 'iett')
TABLO_BOYUTU = 20
TUM_ZAMANLAR_SINIRI = 1000    # Tüm zamanlar listesinde tutulan en fazla skor
HAFTALIK_OMUR = 30 * 24 * 60 * 60

# --- İMZA ANAHTARI ---
# Oyun durumu tarayıcıda imzalı bir jetonla taşınır; anahtar SECRET_KEY ortam değişkeninden gelir.
SECRET_KEY = os.environ.get('SECRET_KEY')
if not SECRET_KEY:
    print("UYARI: SECRET_KEY tanımlı değil, yedek anahtar kullanılıyor. Canlı ortamda mutlaka tanımlayın!")
    with open(EXCEL_FILE, 'rb') as f:
        SECRET_KEY = hashlib.sha256(f.read() + BASE_DIR.encode()).hexdigest()

app = Flask(__name__, template_folder=TEMPLATE_DIR, static_folder=STATIC_DIR)


# --- GOOGLE ADSENSE İZNİ (ADS.TXT) ---
@app.route('/ads.txt')
def ads_txt():
    # static klasörü içindeki ads.txt dosyasını dış dünyaya açar
    return send_from_directory(STATIC_DIR, 'ads.txt')


# --- SAYFALAR ---
@app.route('/')
def index():
    return render_template('index.html', soru_sayisi=SORU_SAYISI, soru_suresi=SORU_SURESI)


@app.route('/hakkimizda')
def hakkimizda():
    return render_template('hakkimizda.html')


@app.route('/gizlilik')
def gizlilik():
    return render_template('gizlilik.html')


@app.route('/iletisim')
def iletisim():
    return render_template('iletisim.html')


# --- SORU HAVUZU (Excel bir kez okunur, bellekte tutulur) ---
_sorular = None


def _metin(deger):
    if deger is None:
        return ''
    if isinstance(deger, float) and deger.is_integer():
        deger = int(deger)
    return str(deger).strip()


def sorulari_yukle():
    global _sorular
    if _sorular is not None:
        return _sorular

    sorular = {}
    wb = openpyxl.load_workbook(EXCEL_FILE, read_only=True, data_only=True)
    satirlar = wb.active.iter_rows(values_only=True)
    basliklar = [_metin(b) for b in next(satirlar)]
    for satir_no, satir in enumerate(satirlar, start=2):
        row = dict(zip(basliklar, satir))
        soru = _metin(row.get('Soru'))
        dogru = _metin(row.get('Dogru_Cevap'))
        secenekler = [_metin(row.get(k)) for k in ('A', 'B', 'C', 'D')]
        if not soru or not dogru:
            continue
        if dogru not in secenekler:
            print(f"Excel satır {satir_no}: doğru cevap şıklarda yok, soru atlandı.")
            continue
        try:
            zorluk = int(row.get('Zorluk'))
        except (TypeError, ValueError):
            zorluk = 2  # Zorluk yazılmamışsa zor say
        sorular[satir_no] = {
            "soru": soru,
            "secenekler": secenekler,
            "dogru_cevap": dogru,
            "bolge": _metin(row.get('Bolge')),
            "zorluk": zorluk,
        }
    wb.close()
    _sorular = sorular
    return sorular


def soru_sec(bolge, gorulen):
    """7 kolay + 13 zor soru seçer; oyuncunun en son gördükleri en sona kalır.

    gorulen: oyuncunun gördüğü soru numaraları, eskiden yeniye sıralı.
    """
    havuz = [sid for sid, s in sorulari_yukle().items()
             if bolge == 'Karışık' or s['bolge'] == bolge]
    random.shuffle(havuz)
    sira = {sid: i for i, sid in enumerate(gorulen)}
    havuz.sort(key=lambda sid: sira.get(sid, -1))

    sorular = sorulari_yukle()
    kolay = [sid for sid in havuz if sorular[sid]['zorluk'] == 1]
    diger = [sid for sid in havuz if sorular[sid]['zorluk'] != 1]
    zor_sayisi = SORU_SAYISI - KOLAY_SAYISI
    if len(kolay) < KOLAY_SAYISI or len(diger) < zor_sayisi:
        # Yeterli ayrım yoksa direkt rastgele al
        return havuz[:SORU_SAYISI]
    return kolay[:KOLAY_SAYISI] + diger[:zor_sayisi]


# --- OYUN JETONU ---
def jeton_uret(durum):
    govde = base64.urlsafe_b64encode(
        json.dumps(durum, separators=(',', ':'), ensure_ascii=False).encode()).decode()
    imza = hmac.new(SECRET_KEY.encode(), govde.encode(), hashlib.sha256).hexdigest()
    return f"{govde}.{imza}"


def jeton_coz(jeton):
    try:
        govde, imza = str(jeton).rsplit('.', 1)
        beklenen = hmac.new(SECRET_KEY.encode(), govde.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(imza, beklenen):
            return None
        durum = json.loads(base64.urlsafe_b64decode(govde.encode()))
    except (ValueError, TypeError):
        return None
    if time.time() - durum['t'] > OYUN_OMRU:
        return None
    return durum


def isim_temizle(isim):
    isim = ' '.join(''.join(ch if ch.isprintable() else ' ' for ch in str(isim or '')).split())
    return isim[:12] or 'Anonim'


def redis(*komutlar):
    """Komutları tek istekte çalıştırır, sonuçları sırayla döndürür."""
    if not REDIS_URL or not REDIS_TOKEN:
        raise RuntimeError("Skor veritabanı tanımlı değil (KV_REST_API_URL / KV_REST_API_TOKEN).")
    r = requests.post(REDIS_URL + '/pipeline', json=[list(k) for k in komutlar],
                      headers={'Authorization': f'Bearer {REDIS_TOKEN}'}, timeout=5)
    r.raise_for_status()
    sonuclar = r.json()
    for s in sonuclar:
        if 'error' in s:
            raise RuntimeError(s['error'])
    return [s['result'] for s in sonuclar]


def lider_anahtari(donem):
    if donem == 'hafta':
        # Haftalık tablo her pazartesi (İstanbul saatiyle) yeni anahtarla sıfırdan başlar
        yil, hafta, _ = datetime.now(ISTANBUL).isocalendar()
        return f"{ANAHTAR_ON_EKI}:lider:hafta:{yil}-{hafta:02d}"
    return f"{ANAHTAR_ON_EKI}:lider:tum"


# --- API: OYUNU BAŞLAT ---
@app.route('/api/basla', methods=['POST'])
def oyunu_baslat():
    data = request.get_json(silent=True) or {}
    bolge = data.get('bolge')
    if bolge not in BOLGELER:
        bolge = 'Karışık'
    gorulen = data.get('gorulen')
    gorulen = [g for g in gorulen[-200:] if isinstance(g, int)] if isinstance(gorulen, list) else []

    try:
        secilen = soru_sec(bolge, gorulen)
    except Exception as e:
        print(f"Excel Hatası: {e}")
        secilen = []
    if not secilen:
        return jsonify({"hata": "Soru bulunamadı!"}), 503

    sorular = sorulari_yukle()
    liste = []
    for sid in secilen:
        secenekler = list(sorular[sid]['secenekler'])
        random.shuffle(secenekler)
        liste.append({"id": sid, "soru": sorular[sid]['soru'], "secenekler": secenekler})

    durum = {
        "g": secrets.token_hex(8),          # oyun kimliği
        "n": isim_temizle(data.get('isim')),
        "b": bolge,
        "q": secilen,
        "i": 0,                             # sıradaki soru
        "p": 0,                             # puan
        "d": 0,                             # doğru sayısı
        "s": 0,                             # güncel seri
        "e": 0,                             # en iyi seri
        "t": time.time(),                   # sorunun gösterildiği an
    }
    return jsonify({"jeton": jeton_uret(durum), "isim": durum["n"], "sorular": liste})


# --- API: CEVAP KONTROL (puan sunucuda hesaplanır) ---
@app.route('/api/cevap', methods=['POST'])
def cevap_kontrol():
    data = request.get_json(silent=True) or {}
    durum = jeton_coz(data.get('jeton'))
    if not durum or durum['i'] >= len(durum['q']):
        return jsonify({"hata": "Oyun süresi dolmuş, yeniden başla."}), 400

    soru = sorulari_yukle().get(durum['q'][durum['i']])
    if not soru:
        return jsonify({"hata": "Sorular güncellenmiş, yeniden başla."}), 400

    simdi = time.time()
    gecen = simdi - durum['t'] - (GECIS_SURESI if durum['i'] > 0 else 0)
    cevap = data.get('cevap')
    dogru_mu = cevap == soru['dogru_cevap'] and gecen <= SORU_SURESI + 2

    kazanilan = 0
    if dogru_mu:
        kalan = max(0.0, min(SORU_SURESI, SORU_SURESI - gecen))
        durum['s'] += 1
        durum['d'] += 1
        durum['e'] = max(durum['e'], durum['s'])
        kazanilan = (TABAN_PUAN
                     + round(MAX_HIZ_BONUSU * kalan / SORU_SURESI)
                     + min(MAX_SERI_BONUSU, SERI_BONUSU * (durum['s'] - 1)))
        durum['p'] += kazanilan
    else:
        durum['s'] = 0
    durum['i'] += 1
    durum['t'] = simdi

    bitti = durum['i'] >= len(durum['q'])
    yanit = {
        "dogru": dogru_mu,
        "dogru_cevap": soru['dogru_cevap'],
        "kazanilan": kazanilan,
        "puan": durum['p'],
        "seri": durum['s'],
        "bitti": bitti,
    }
    if bitti:
        yanit["sonuc"] = {
            "id": durum['g'],
            "puan": durum['p'],
            "dogru": durum['d'],
            "toplam": len(durum['q']),
            "en_iyi_seri": durum['e'],
            "kaydedildi": skor_kaydet(durum),
        }
    else:
        yanit["jeton"] = jeton_uret(durum)
    return jsonify(yanit)


def skor_kaydet(durum):
    if durum['p'] <= 0:
        return False
    # Oyun kimliği üyenin parçası olduğu için aynı oyun iki kez listeye giremez
    uye = json.dumps({
        "id": durum['g'],
        "isim": durum['n'],
        "puan": durum['p'],
        "dogru": durum['d'],
        "bolge": durum['b'],
    }, ensure_ascii=False, sort_keys=True)
    # Sıralama değeri: puan + eşitlikte önce yapanı öne alan küçük bir kesir
    sira = str(durum['p'] + 1 - time.time() / 1e10)
    tum, hafta = lider_anahtari('tum'), lider_anahtari('hafta')
    try:
        redis(['ZADD', tum, sira, uye],
              ['ZREMRANGEBYRANK', tum, 0, -(TUM_ZAMANLAR_SINIRI + 1)],
              ['ZADD', hafta, sira, uye],
              ['EXPIRE', hafta, HAFTALIK_OMUR])
        _liderlik_onbellek.clear()
        return True
    except Exception as e:
        print(f"Skor Kayıt Hatası: {e}")
        return False


# --- API: LİDERLİK TABLOSU ---
_liderlik_onbellek = {}
LIDERLIK_ONBELLEK_SURESI = 20


def skorlari_getir(donem):
    kayit = _liderlik_onbellek.get(donem)
    if kayit and time.time() - kayit[0] < LIDERLIK_ONBELLEK_SURESI:
        return kayit[1]

    uyeler = redis(['ZRANGE', lider_anahtari(donem), 0, TABLO_BOYUTU - 1, 'REV'])[0]
    skorlar = []
    for uye in uyeler:
        try:
            v = json.loads(uye)
        except ValueError:
            continue
        if not isinstance(v, dict) or type(v.get('puan')) is not int:
            continue
        skorlar.append({
            "id": str(v.get('id')),
            "isim": isim_temizle(v.get('isim')),
            "puan": v['puan'],
            "dogru": v.get('dogru') if type(v.get('dogru')) is int else None,
            "bolge": v.get('bolge') if v.get('bolge') in BOLGELER else 'Karışık',
        })
    _liderlik_onbellek[donem] = (time.time(), skorlar)
    return skorlar


@app.route('/api/liderlik')
def liderlik_tablosu():
    donem = 'hafta' if request.args.get('donem') == 'hafta' else 'tum'
    try:
        return jsonify(skorlari_getir(donem))
    except Exception as e:
        print(f"Liderlik Hatası: {e}")
        return jsonify({"hata": "Liderlik tablosu yüklenemedi."}), 502


if __name__ == '__main__':
    app.run(debug=True)
