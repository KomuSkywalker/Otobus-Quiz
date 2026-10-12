const $ = id => document.getElementById(id);

// localStorage gizli sekmede veya kapalıyken hata fırlatabilir; oyun onsuz da çalışmalı
const depo = {
    al(anahtar, varsayilan) {
        try {
            const v = localStorage.getItem(anahtar);
            return v === null ? varsayilan : JSON.parse(v);
        } catch (e) { return varsayilan; }
    },
    yaz(anahtar, deger) {
        try { localStorage.setItem(anahtar, JSON.stringify(deger)); } catch (e) {}
    }
};

let sorular = [];
let suankiSoruIndex = 0;
let jeton = null;
let oyuncuIsmi = "";
let secilenBolge = "Karışık";
let sonSonuc = null;
let kilit = false;
let sureSayaci = null;
let sesAcik = depo.al('ses', true);
let liderlikDonemi = 'hafta';

const bgMusic = $("bg-music");
const soundWin = $("sound-win");
const soundLose = $("sound-lose");

// --- EKRANLAR ---
function ekranGoster(id) {
    document.querySelectorAll('.screen').forEach(s => s.classList.add('gizli'));
    const target = $(id);
    if (target) target.classList.remove('gizli');
    window.scrollTo(0, 0);
}

let toastZamanlayici = null;
function toast(mesaj) {
    const t = $('toast');
    t.textContent = mesaj;
    t.classList.add('gorunur');
    clearTimeout(toastZamanlayici);
    toastZamanlayici = setTimeout(() => t.classList.remove('gorunur'), 2800);
}

// --- SES ---
function muzigiBaslat() {
    if (!sesAcik) return;
    bgMusic.currentTime = 0;
    bgMusic.volume = 0.3;
    bgMusic.play().catch(() => console.log("Otomatik oynatma engellendi."));
}

function muzigiDurdur() {
    bgMusic.pause();
    bgMusic.currentTime = 0;
}

function efektCal(tip) {
    if (!sesAcik) return;
    const ses = tip === 'dogru' ? soundWin : soundLose;
    ses.currentTime = 0;
    ses.volume = tip === 'dogru' ? 1.0 : 0.3;
    ses.play().catch(() => {});
}

// iOS Safari, kullanıcı dokunuşu dışında başlatılan sesi engeller. Efektler cevap
// sunucudan döndükten sonra çaldığı için ilk dokunuşta sessizce bir kez oynatıp açıyoruz.
let seslerHazir = false;
function sesleriHazirla() {
    if (seslerHazir) return;
    seslerHazir = true;
    [soundWin, soundLose].forEach(ses => {
        ses.muted = true;
        ses.play().then(() => { ses.pause(); ses.currentTime = 0; })
            .catch(() => {})
            .finally(() => { ses.muted = false; });
    });
}

function sesDugmesiniGuncelle() {
    const btn = $('ses-btn');
    btn.textContent = sesAcik ? '🔊' : '🔇';
    btn.setAttribute('aria-label', sesAcik ? 'Sesi kapat' : 'Sesi aç');
}

function sesiDegistir() {
    sesAcik = !sesAcik;
    depo.yaz('ses', sesAcik);
    sesDugmesiniGuncelle();
    const oyunda = !$('screen-game').classList.contains('gizli');
    if (sesAcik && oyunda) muzigiBaslat();
    if (!sesAcik) muzigiDurdur();
}

// --- MENÜ ---
function isimKontrolVeIlerle(e) {
    e.preventDefault();
    oyuncuIsmi = $('oyuncu-ismi').value.trim();
    $('isim-hata').classList.toggle('gizli', !!oyuncuIsmi);
    if (!oyuncuIsmi) { $('oyuncu-ismi').focus(); return; }
    depo.yaz('isim', oyuncuIsmi);
    ekranGoster('screen-map-select');
}

function anaMenuyeDon() {
    sureyiDurdur();
    muzigiDurdur();
    ekranGoster('screen-menu');
}

// --- OYUN ---
async function api(yol, govde) {
    const r = await fetch(yol, {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(govde)
    });
    const veri = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(veri.hata || "Bağlantı hatası!");
    return veri;
}

async function oyunuBaslat(bolge) {
    if (kilit) return;
    kilit = true;
    secilenBolge = bolge;
    sesleriHazirla();
    muzigiBaslat();
    try {
        const d = await api('/api/basla', {
            isim: oyuncuIsmi, bolge: bolge, gorulen: depo.al('gorulen', [])
        });
        sorular = d.sorular;
        jeton = d.jeton;
        suankiSoruIndex = 0;

        // Sonraki oyunlarda aynı sorular gelmesin diye görülenleri hatırla (eskiden yeniye)
        const yeni = sorular.map(s => s.id);
        const gorulen = depo.al('gorulen', []).filter(id => !yeni.includes(id)).concat(yeni);
        depo.yaz('gorulen', gorulen.slice(-60));

        $('kullanici-adi-goster').textContent = "👤 " + d.isim;
        $('toplam-text').textContent = sorular.length;
        guncellePuan(0, 0);
        ekranGoster('screen-game');
        kilit = false;
        soruYukle();
    } catch (e) {
        console.error(e);
        kilit = false;
        anaMenuyeDon();
        toast(e.message || "Bağlantı hatası!");
    }
}

function guncellePuan(puan, seri) {
    $('skor-goster').textContent = puan + " puan";
    const seriEl = $('seri-goster');
    seriEl.textContent = "🔥 " + seri;
    seriEl.classList.toggle('gizli', seri < 2);
}

function soruYukle() {
    const s = sorular[suankiSoruIndex];
    $('sayac-text').textContent = suankiSoruIndex + 1;
    $('soru-metni').textContent = s.soru;
    $('kazanc').className = 'kazanc';

    const div = $('secenekler');
    div.innerHTML = '';
    s.secenekler.forEach(opt => {
        const btn = document.createElement('button');
        btn.type = 'button';
        btn.textContent = opt;
        btn.className = 'secenek';
        btn.onclick = () => cevapGonder(opt);
        div.appendChild(btn);
    });

    kilit = false;
    sureyiBaslat();
}

function sureyiBaslat() {
    sureyiDurdur();
    const bitis = performance.now() + AYAR.soruSuresi * 1000;
    const dolgu = $('sure-dolgu');
    const metin = $('sure-text');
    const guncelle = () => {
        const kalan = Math.max(0, (bitis - performance.now()) / 1000);
        dolgu.style.width = (kalan / AYAR.soruSuresi * 100) + '%';
        dolgu.classList.toggle('az', kalan <= 5);
        metin.textContent = Math.ceil(kalan);
        if (kalan <= 0) cevapGonder(null);
    };
    guncelle();
    sureSayaci = setInterval(guncelle, 100);
}

function sureyiDurdur() {
    clearInterval(sureSayaci);
    sureSayaci = null;
}

async function cevapGonder(secilen) {
    if (kilit) return;
    kilit = true;
    sureyiDurdur();

    const butonlar = [...document.querySelectorAll('#secenekler .secenek')];
    butonlar.forEach(b => {
        b.disabled = true;
        if (b.textContent === secilen) b.classList.add('secildi');
    });

    let d;
    try {
        d = await api('/api/cevap', {jeton: jeton, cevap: secilen});
    } catch (e) {
        console.error(e);
        anaMenuyeDon();
        kilit = false;
        toast(e.message || "Bağlantı hatası!");
        return;
    }

    butonlar.forEach(b => {
        if (b.textContent === d.dogru_cevap) b.classList.add('dogru');
        else if (b.textContent === secilen) b.classList.add('yanlis');
    });

    const kazanc = $('kazanc');
    if (d.dogru) kazanc.textContent = "+" + d.kazanilan;
    else kazanc.textContent = secilen === null ? "Süre doldu!" : "Yanlış!";
    kazanc.className = 'kazanc gorunur ' + (d.dogru ? 'arti' : 'eksi');

    efektCal(d.dogru ? 'dogru' : 'yanlis');
    guncellePuan(d.puan, d.seri);

    setTimeout(() => {
        // Geçiş beklenirken oyuncu oyundan çıkmış olabilir
        if ($('screen-game').classList.contains('gizli')) return;
        if (d.bitti) {
            oyunuBitir(d.sonuc);
        } else {
            jeton = d.jeton;
            suankiSoruIndex++;
            soruYukle();
        }
    }, AYAR.gecisSuresi);
}

function oyunuBitir(sonuc) {
    muzigiDurdur();
    kilit = false;
    sonSonuc = sonuc;

    const kazandi = sonuc.dogru / sonuc.toplam >= 0.75;
    $('sonuc-resim').src = kazandi ? "/static/sonuc-zafer.png" : "/static/sonuc-hata.png";
    $('sonuc-dogru').textContent = sonuc.dogru + "/" + sonuc.toplam;
    $('sonuc-puan').textContent = sonuc.puan;
    $('sonuc-seri').textContent = sonuc.en_iyi_seri;
    efektCal(kazandi ? 'dogru' : 'yanlis');

    ekranGoster('screen-result');
    if (sonuc.puan > 0 && !sonuc.kaydedildi) toast("Skorun liderlik tablosuna kaydedilemedi.");
}

function sonucuPaylas() {
    if (!sonSonuc) return;
    const adres = location.origin;
    const metin = `İETT Quiz'de ${sonSonuc.toplam} soruda ${sonSonuc.dogru} doğruyla ${sonSonuc.puan} puan topladım! İstanbul'un kurduyum diyorsan sen de dene:`;

    if (navigator.share) {
        navigator.share({title: 'İETT Quiz Sonucum', text: metin, url: adres}).catch(() => {});
    } else if (navigator.clipboard) {
        navigator.clipboard.writeText(metin + " " + adres)
            .then(() => toast("Sonuç kopyalandı! WhatsApp'ta yapıştırabilirsin."))
            .catch(() => toast("Kopyalanamadı."));
    } else {
        toast("Bu tarayıcı paylaşımı desteklemiyor.");
    }
}

// --- LİDERLİK TABLOSU ---
function tabloMesaji(mesaj) {
    const tablo = $('lider-tablosu');
    tablo.innerHTML = '';
    const td = tablo.insertRow().insertCell();
    td.className = 'tablo-mesaj';
    td.textContent = mesaj;
}

async function liderleriGoster(donem) {
    liderlikDonemi = donem || liderlikDonemi;
    document.querySelectorAll('.sekme').forEach(s => {
        const aktif = s.dataset.donem === liderlikDonemi;
        s.classList.toggle('aktif', aktif);
        s.setAttribute('aria-selected', aktif);
    });
    sureyiDurdur();
    muzigiDurdur();
    ekranGoster('screen-leaderboard');
    tabloMesaji("Yükleniyor...");

    const istenen = liderlikDonemi;
    let data;
    try {
        const r = await fetch('/api/liderlik?donem=' + istenen);
        if (!r.ok) throw new Error();
        data = await r.json();
    } catch (e) {
        if (istenen === liderlikDonemi) tabloMesaji("Tablo yüklenemedi. Biraz sonra tekrar dene.");
        return;
    }
    if (istenen !== liderlikDonemi) return;

    if (!data.length) {
        tabloMesaji(istenen === 'hafta' ? "Bu hafta henüz skor yok. İlk sen ol!" : "Henüz skor yok. İlk sen ol!");
        return;
    }

    const tablo = $('lider-tablosu');
    tablo.innerHTML = '';
    const baslik = tablo.createTHead().insertRow();
    ['Sıra', 'İsim', 'Mod', 'Puan'].forEach(b => {
        const th = document.createElement('th');
        th.textContent = b;
        baslik.appendChild(th);
    });
    const govde = tablo.createTBody();
    const madalyalar = ['🥇', '🥈', '🥉'];
    data.forEach((kisi, index) => {
        const tr = govde.insertRow();
        if (sonSonuc && kisi.id === sonSonuc.id) tr.className = 'benim';
        // İsimler başkalarından geliyor: sadece textContent ile yaz
        tr.insertCell().textContent = madalyalar[index] || (index + 1) + ".";
        tr.insertCell().textContent = kisi.isim;
        const mod = tr.insertCell();
        mod.className = 'mod';
        mod.textContent = kisi.bolge;
        tr.insertCell().textContent = kisi.puan;
    });
}

// --- OLAYLAR ---
$('isim-formu').addEventListener('submit', isimKontrolVeIlerle);
$('ses-btn').addEventListener('click', sesiDegistir);
$('paylas-btn').addEventListener('click', sonucuPaylas);
$('tekrar-btn').addEventListener('click', () => oyunuBaslat(secilenBolge));
document.querySelectorAll('[data-bolge]').forEach(b =>
    b.addEventListener('click', () => oyunuBaslat(b.dataset.bolge)));
document.querySelectorAll('[data-git="menu"]').forEach(b => b.addEventListener('click', anaMenuyeDon));
document.querySelectorAll('[data-git="liderlik"]').forEach(b =>
    b.addEventListener('click', () => liderleriGoster()));
document.querySelectorAll('.sekme').forEach(s =>
    s.addEventListener('click', () => liderleriGoster(s.dataset.donem)));

$('oyuncu-ismi').value = depo.al('isim', '');
sesDugmesiniGuncelle();
