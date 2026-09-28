<#
.SYNOPSIS
    Jalankan satu skenario k6 terhadap KEDUA platform, berurutan, lalu bandingkan.

.DESCRIPTION
    Yang dikerjakan skrip ini, dan kenapa tiap langkahnya ada:

      1. Memastikan semua container yang dibutuhkan hidup. Benchmark yang
         separuh sasarannya mati akan tetap menghasilkan angka, dan angka itu
         terlihat sangat bagus.

      2. Membuatkan API key Langflow. Kuncinya hanya muncul sekali saat dibuat
         dan tidak bisa dibaca ulang, jadi tidak ada gunanya menyimpannya.

      3. Memanaskan kedua sisi. Permintaan pertama ke Langflow memuat flow-nya
         dari SQLite; permintaan pertama ke service membuka koneksi DuckDB.
         Tanpa pemanasan, biaya sekali-seumur-hidup itu ikut terhitung dan
         muncul sebagai p99 yang menyesatkan.

      4. Menjalankan k6 untuk tiap sasaran SATU PER SATU, tidak bersamaan.
         Kalau bersamaan, keduanya berebut CPU mesin yang sama dan yang
         terukur adalah rebutan itu.

      5. Mencatat CPU dan memori saat idle maupun saat dibebani.

.EXAMPLE
    .\jalankan.ps1
    Skenario status, 20 rps, 60 detik, kedua sasaran.

.EXAMPLE
    .\jalankan.ps1 -Skenario dasar -Rps 100
    Garis dasar: hanya /health, untuk memisahkan biaya framework dari biaya basis data.

.EXAMPLE
    .\jalankan.ps1 -Skenario status -Mode batas
    Naikkan laju bertahap sampai salah satu menyerah.
#>

[CmdletBinding()]
param(
    [ValidateSet("dasar", "status", "aturan", "campuran", "skala_jutaan", "per_api")]
    [string] $Skenario = "status",

    [int]    $Rps = 20,
    [int]    $Detik = 60,

    [ValidateSet("setara", "batas")]
    [string] $Mode = "setara",

    [string[]] $Target = @("service", "langflow"),

    [string] $KunciService = "synchrono-bench-key",

    # Jeda antar sasaran. Bukan basa-basi: grading yang terlanjur dilepas oleh
    # skenario `campuran` masih berjalan di latar setelah k6 berhenti, dan
    # kalau sasaran berikutnya langsung mulai, ia mengukur mesin yang masih
    # sibuk mengerjakan sisa pekerjaan sasaran sebelumnya.
    [int]    $Jeda = 30,

    # Matikan platform yang TIDAK sedang diukur, lalu nyalakan lagi sesudahnya.
    #
    # Tanpa ini keduanya hidup bersamaan sepanjang benchmark, dan itu TIDAK
    # simetris: Langflow menganggur di 1,26 GB sementara service di 111 MB.
    # Jadi saat service diukur ia hanya punya sisa memori setelah Langflow
    # mengambil bagiannya, sedangkan saat Langflow diukur ia hampir dapat
    # semuanya. Pada berkas jutaan baris — di mana memori adalah dindingnya —
    # ketimpangan itu bisa menentukan hasil.
    [switch] $Bergantian,

    [switch] $TanpaPrometheus,

    # ── Khusus skenario `skala_jutaan` ──────────────────────────────────────
    # Berkas jutaan baris yang dikirim lalu dipolling sampai selesai. Dibuat
    # oleh buat_data_besar.py, dan ADA DI bucket-test — bukan syncrono-uploads,
    # karena itu tempat pembangkitnya menulis.
    [string] $BerkasBesar = "besar-5000k",
    [string] $Bucket = "bucket-test",
    # Laju polling berkas LAIN, mewakili unggahan lain yang juga ditunggu.
    # Inilah sumber rebutan CPU-nya. 0 = tanpa rebutan, sebagai pembanding.
    [int] $Bising = 2,
    [int] $BatasMenit = 15
)

# BERKAS INI HARUS DISIMPAN SEBAGAI UTF-8 DENGAN BOM.
#
# Windows PowerShell 5.1 membaca berkas .ps1 tanpa BOM sebagai ANSI, dan setiap
# karakter non-ASCII di dalamnya berubah jadi sampah yang membuat parser gagal
# sebelum satu baris pun jalan. Kalau suatu saat skrip ini tiba-tiba penuh
# galat "Unexpected token", periksa encoding-nya lebih dulu:
#
#     Get-Content jalankan.ps1 -Encoding Byte -TotalCount 3
#     -> 239 187 191 berarti BOM-nya ada.

$ErrorActionPreference = "Stop"
$AKAR = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $AKAR

function Bagian($teks) {
    Write-Host ""
    Write-Host ("-" * 66) -ForegroundColor DarkGray
    Write-Host "  $teks" -ForegroundColor Cyan
    Write-Host ("-" * 66) -ForegroundColor DarkGray
}

# Menjalankan perintah native (docker) TANPA membuat skrip mati begitu ada satu
# baris di stderr.
#
# Ini jebakan khas Windows PowerShell 5.1: dengan $ErrorActionPreference =
# "Stop", tulisan apa pun ke stderr dari sebuah exe dianggap galat yang
# menghentikan seluruh skrip. Dan docker memakai stderr untuk hal-hal yang sama
# sekali bukan galat - "Unable to find image ... locally" sebelum ia mengunduh,
# misalnya, yang sempat menghentikan benchmark ini tepat sebelum k6 jalan.
#
# Yang menentukan berhasil atau tidak adalah $LASTEXITCODE, bukan stderr.
function Native {
    param([string] $Perintah, [string[]] $Argumen, [switch] $AbaikanGalat)

    $simpan = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        & $Perintah @Argumen 2>&1 | ForEach-Object { Write-Host "$_" }
    } finally {
        $ErrorActionPreference = $simpan
    }
    if ($LASTEXITCODE -ne 0 -and -not $AbaikanGalat) {
        throw "$Perintah keluar dengan kode $LASTEXITCODE"
    }
}

# Menunggu sebuah sasaran benar-benar sanggup menjawab lagi.
#
# Dibutuhkan karena k6 berhenti LEBIH DULU daripada sasarannya. Setelah satu
# jalan beban, Langflow masih menghabiskan antrean eksekusi yang terlanjur
# masuk: diukur langsung, CPU-nya masih 100% dan /health_check masih timeout
# sepuluh detik setelah k6 keluar. Jalan berikutnya yang dimulai saat itu
# mengukur mesin yang sedang menghabiskan pekerjaan jalan sebelumnya.
function TungguSiap {
    param([string] $Url, [int] $BatasDetik = 120)

    $selesai = (Get-Date).AddSeconds($BatasDetik)
    while ((Get-Date) -lt $selesai) {
        try {
            $r = Invoke-WebRequest $Url -UseBasicParsing -TimeoutSec 5 -ErrorAction Stop
            if ($r.StatusCode -eq 200) { return $true }
        } catch { }
        Start-Sleep -Seconds 3
    }
    return $false
}

# `powershell -File jalankan.ps1 -Target service,langflow` mengirimkan koma itu
# apa adanya, sebagai SATU string, bukan dua elemen — beda dengan memanggil
# skripnya langsung dari prompt PowerShell. Dipecah di sini supaya kedua cara
# memanggil menghasilkan hal yang sama.
$Target = @($Target | ForEach-Object { $_ -split "," } | ForEach-Object { $_.Trim() } |
            Where-Object { $_ })

$SASARAN_SAH = @("service", "langflow")
$salah = @($Target | Where-Object { $SASARAN_SAH -notcontains $_ })
if ($salah) {
    throw "Sasaran tidak dikenal: $($salah -join ', '). Yang ada: $($SASARAN_SAH -join ', ')."
}

$URL_SIAP = @{
    service  = "http://localhost:8000/health"
    langflow = "http://localhost:7860/health_check"
}

$CONTAINER = @{
    service  = "synchrono-service"
    langflow = "synchrono-langflow"
}

function HanyaHidupkan {
    param([string] $Sasaran)

    foreach ($nama in $CONTAINER.Keys) {
        $c = $CONTAINER[$nama]
        if ($nama -eq $Sasaran) { continue }
        Write-Host "  mematikan $c (bukan yang diukur)" -ForegroundColor DarkGray
        Native docker @("stop", $c) -AbaikanGalat
    }
    # DINYALAKAN ULANG, bukan sekadar dinyalakan.
    #
    # `docker start` tidak berpengaruh pada container yang sudah jalan. Sisi
    # yang diukur BELAKANGAN selalu mendapat container segar karena dimatikan
    # lebih dulu, sedangkan yang diukur duluan memakai container yang bisa
    # sudah hidup berjam-jam. Itu ketimpangan yang nyata: Langflow yang sudah
    # hidup tiga jam terukur memakai 5,01 GB untuk berkas 1 juta baris dan
    # gagal menyelesaikannya, sementara setelah disegarkan berkas yang sama
    # selesai dengan 3,36 GB.
    Write-Host "  menyegarkan $($CONTAINER[$Sasaran])" -ForegroundColor DarkGray
    Native docker @("restart", $CONTAINER[$Sasaran]) -AbaikanGalat

    # Langflow butuh puluhan detik memuat komponennya; service belasan.
    if (-not (TungguSiap $URL_SIAP[$Sasaran] 180)) {
        throw "$Sasaran tidak siap setelah dinyalakan sendirian."
    }
    Write-Host "  hanya $($CONTAINER[$Sasaran]) yang hidup" -ForegroundColor Green
}

# ── 1. Prasyarat ───────────────────────────────────────────────────────────

Bagian "Memeriksa prasyarat"

$wajib = [ordered]@{
    "synchrono-seaweedfs" = "cd ..\..\langflow-synchrono\infra; docker compose up -d"
    "synchrono-langflow"  = "cd ..\..\langflow-synchrono\infra; docker compose up -d"
    "synchrono-service"   = "cd ..; docker compose up -d"
}
if (-not $TanpaPrometheus) {
    $wajib["bench-prometheus"] = "docker compose up -d   (dari folder beban)"
    $wajib["bench-pengukur"]   = "docker compose up -d   (dari folder beban)"
}

$jalan = docker ps --format "{{.Names}}"
$kurang = @()
foreach ($nama in $wajib.Keys) {
    if ($jalan -contains $nama) {
        Write-Host "  ok    $nama" -ForegroundColor Green
    } else {
        Write-Host "  MATI  $nama  ->  $($wajib[$nama])" -ForegroundColor Red
        $kurang += $nama
    }
}
if ($kurang.Count -gt 0) {
    throw "$($kurang.Count) container belum jalan. Naikkan dulu, lalu ulangi."
}

# PostgreSQL ada di host (DBngin), bukan di Docker - jadi tidak terlihat oleh
# `docker ps` dan harus diperiksa tersendiri.
$pg = Test-NetConnection -ComputerName 127.0.0.1 -Port 5432 -WarningAction SilentlyContinue
if (-not $pg.TcpTestSucceeded) {
    throw "PostgreSQL :5432 tidak menjawab. Nyalakan DBngin - tanpa itu SEMUA endpoint balas 500 di kedua sisi."
}
Write-Host "  ok    postgresql :5432" -ForegroundColor Green

# Diunduh SEKARANG, bukan nanti saat k6 mau jalan. Unduhan yang terjadi di
# tengah menambah waktu yang tidak ada hubungannya dengan apa pun yang diukur.
$adaK6 = docker images -q grafana/k6:latest
if (-not $adaK6) {
    Write-Host "  ...   mengunduh image k6" -ForegroundColor DarkGray
    Native docker @("pull", "-q", "grafana/k6:latest")
}
Write-Host "  ok    image grafana/k6" -ForegroundColor Green

# ── 2. API key Langflow ────────────────────────────────────────────────────

$kunciLangflow = ""
if ($Target -contains "langflow") {
    Bagian "Membuat API key Langflow"
    if (-not (TungguSiap $URL_SIAP["langflow"])) {
        throw "Langflow tidak menjawab /health_check dalam 2 menit. Kalau baru saja dibebani, beri waktu lebih lama."
    }
    try {
        $tok = (Invoke-RestMethod "http://localhost:7860/api/v1/login" -Method POST `
                -Body "username=admin&password=synchrono123" `
                -ContentType "application/x-www-form-urlencoded").access_token
        $kunciLangflow = (Invoke-RestMethod "http://localhost:7860/api/v1/api_key/" -Method POST `
                -Headers @{ Authorization = "Bearer $tok" } -ContentType "application/json" `
                -Body "{`"name`":`"k6-$(Get-Date -Format yyyyMMdd-HHmmss)`"}").api_key
        Write-Host "  ok    kunci dibuat ($($kunciLangflow.Substring(0,12))...)" -ForegroundColor Green
    } catch {
        throw "Gagal membuat API key Langflow: $_"
    }
}

# ── 3. Pemanasan ───────────────────────────────────────────────────────────

Bagian "Memanaskan kedua sisi"

try {
    Invoke-RestMethod "http://localhost:8000/api/v1/config/rules" `
        -Headers @{ "x-api-key" = $KunciService } | Out-Null
    Write-Host "  ok    service" -ForegroundColor Green
} catch { Write-Host "  gagal memanaskan service: $_" -ForegroundColor Yellow }

if ($kunciLangflow) {
    try {
        $b = '{"output_type":"chat","input_type":"text","input_value":"","tweaks":{"GradingRuleGet-9c9c5":{"grade_id":""}}}'
        Invoke-RestMethod "http://localhost:7860/api/v1/run/config-rules?stream=false" -Method POST `
            -Headers @{ "x-api-key" = $kunciLangflow } -ContentType "application/json" -Body $b | Out-Null
        Write-Host "  ok    langflow" -ForegroundColor Green
    } catch { Write-Host "  gagal memanaskan langflow: $_" -ForegroundColor Yellow }
}

# ── 4. Sumber daya saat IDLE ───────────────────────────────────────────────
#
# Diambil SEBELUM beban dimulai. Ini angka yang paling sering dilupakan, dan
# untuk pertanyaan "muat berapa banyak di satu server" justru dialah yang
# paling menentukan: sebuah service yang memakan 1 GB saat tidak melakukan
# apa-apa tetap memakan 1 GB sepanjang malam.

Bagian "Sumber daya saat idle"
Native docker @("stats", "--no-stream", "--format", "  {{.Name}}`t{{.CPUPerc}}`t{{.MemUsage}}",
                "synchrono-service", "synchrono-langflow", "synchrono-seaweedfs")

$stempel = Get-Date -Format "yyyyMMdd-HHmmss"
$ringkasan = @()

# ── 5. Jalankan ────────────────────────────────────────────────────────────

try {

foreach ($t in $Target) {
    Bagian "k6 - $Skenario terhadap $t  ($Rps rps, $Detik detik, mode $Mode)"

    if ($Bergantian) { HanyaHidupkan $t }

    if (-not (TungguSiap $URL_SIAP[$t])) {
        Write-Host "  $t tidak siap - dilewati" -ForegroundColor Red
        continue
    }

    $namaHasil = "$stempel-$Skenario-$t"
    $arg = @(
        "run", "--rm", "--network", "infra_default",
        "-v", "$AKAR\k6:/skrip:ro",
        "-v", "$AKAR\hasil:/hasil",
        "-e", "TARGET=$t",
        "-e", "RPS=$Rps",
        "-e", "DETIK=$Detik",
        "-e", "MODE=$Mode",
        "-e", "SERVICE_API_KEY=$KunciService",
        "-e", "LANGFLOW_API_KEY=$kunciLangflow",
        "-e", "HASIL_JSON=/hasil/$namaHasil.json",
        "-e", "BERKAS_BESAR=$BerkasBesar",
        "-e", "BUCKET=$Bucket",
        "-e", "BISING=$Bising",
        "-e", "BATAS_MENIT=$BatasMenit"
    )

    # --quiet mematikan progress bar (satu baris per detik per skenario),
    # bukan ringkasannya. Tanpa ini satu jalan 60 detik menghasilkan ratusan
    # baris yang menenggelamkan angka yang dicari.
    $argK6 = @("run", "--quiet")
    if (-not $TanpaPrometheus) {
        $arg += @(
            "-e", "K6_PROMETHEUS_RW_SERVER_URL=http://prometheus:9090/api/v1/write",
            # Tanpa ini, metrik trend hanya dikirim sebagai rata-rata dan p95
            # tidak pernah sampai ke Grafana - grafiknya kosong tanpa satu pun
            # pesan galat.
            "-e", "K6_PROMETHEUS_RW_TREND_STATS=avg,p(95),p(99),max"
        )
        $argK6 += @("-o", "experimental-prometheus-rw")
    }
    $argK6 += "/skrip/$Skenario.js"

    # Memantau pemakaian sumber daya selagi k6 berjalan. Dijalankan sebagai job
    # terpisah supaya tidak menghalangi, dan hasilnya jadi angka cadangan kalau
    # Grafana tidak sempat dibuka.
    # DUA HAL YANG DULU SALAH DI SINI, DAN KEDUANYA MEMBUAT KOLOM MEMORI TIDAK
    # BERARTI:
    #
    #   1. Memori TIDAK diambil maksimumnya. CPU dibandingkan dan disimpan yang
    #      tertinggi, sedangkan memori hanya ditimpa tiap putaran — sehingga
    #      yang tercetak nilai pada sampel TERAKHIR, bukan puncak. Labelnya
    #      tetap "puncak", jadi salahnya tidak terlihat.
    #
    #   2. Jendela pengamatannya `$Detik + 10`. Untuk skenario `skala_jutaan`,
    #      `-Detik` bukan lama jalannya: jalannya berlangsung sampai berkasnya
    #      selesai, hingga `-BatasMenit`. Pada berkas 10 juta baris, jendela 70
    #      detik itu berhenti saat penilaian baru belasan persen — dan sisi yang
    #      selesai lebih cepat justru terbaca memakai memori lebih banyak,
    #      semata karena sampelnya diambil di titik yang berbeda.
    #
    # Sekarang: memori diubah ke byte lalu diambil yang tertinggi, dan
    # pemantauannya berhenti ketika k6 selesai, bukan pada detik yang ditebak.
    $tandaHenti = Join-Path $env:TEMP "beban-henti-$PID-$t.tmp"
    Remove-Item $tandaHenti -ErrorAction SilentlyContinue
    $pantau = Start-Job -ScriptBlock {
        param($batasKeras, $tanda)

        # docker stats MENCAMPUR memori proses dengan cache berkas. Yang
        # menentukan kebutuhan memori adalah `anon` di cgroup: halaman yang
        # benar-benar dipegang proses dan tidak bisa dilepas begitu saja.
        # Keduanya dicatat supaya selisihnya terlihat, bukan disembunyikan.
        function AnonByte([string] $container) {
            $t = docker exec $container sh -c "awk '/^anon /{print `$2}' /sys/fs/cgroup/memory.stat" 2>$null
            if ($t -match '^\d+$') { return [double]$t }
            return 0
        }

        function KeByte([string] $teks) {
            if ($teks -notmatch '^([\d\.]+)\s*([KMGT]?i?B)$') { return 0 }
            $n = [double]$matches[1]
            switch ($matches[2]) {
                'B'   { $n }
                'KiB' { $n * 1KB } 'KB' { $n * 1KB }
                'MiB' { $n * 1MB } 'MB' { $n * 1MB }
                'GiB' { $n * 1GB } 'GB' { $n * 1GB }
                'TiB' { $n * 1TB } 'TB' { $n * 1TB }
                default { 0 }
            }
        }

        $puncak = @{}
        $selesai = (Get-Date).AddSeconds($batasKeras)
        while ((Get-Date) -lt $selesai -and -not (Test-Path $tanda)) {
            $baris = docker stats --no-stream --format "{{.Name}}|{{.CPUPerc}}|{{.MemUsage}}" `
                synchrono-service synchrono-langflow 2>$null
            foreach ($b in $baris) {
                $p = "$b" -split '\|'
                if ($p.Count -lt 3) { continue }
                $cpu = [double]($p[1] -replace '%', '')
                $memTeks = ($p[2] -split ' / ')[0].Trim()
                $memByte = KeByte $memTeks
                if (-not $puncak.ContainsKey($p[0])) {
                    $puncak[$p[0]] = @{ cpu = 0.0; mem = ""; memByte = 0.0; anonByte = 0.0 }
                }
                if ($cpu -gt $puncak[$p[0]].cpu) { $puncak[$p[0]].cpu = $cpu }
                if ($memByte -gt $puncak[$p[0]].memByte) {
                    $puncak[$p[0]].memByte = $memByte
                    $puncak[$p[0]].mem = $memTeks
                }
                $anon = AnonByte $p[0]
                if ($anon -gt $puncak[$p[0]].anonByte) { $puncak[$p[0]].anonByte = $anon }
            }
        }
        $puncak
    } -ArgumentList (($BatasMenit * 60) + 120), $tandaHenti

    # -AbaikanGalat: k6 keluar dengan kode 99 kalau ada threshold yang
    # terlampaui. Itu informasi, bukan alasan menghentikan benchmark - justru
    # sisi yang melampaui ambanglah yang sedang ingin diketahui.
    # Nama image berada DI ANTARA opsi docker dan argumen k6. Ketinggalan
    # sekali, dan docker memperlakukan kata "run" milik k6 sebagai nama
    # image: "pull access denied for run".
    Native docker ($arg + @("grafana/k6:latest") + $argK6) -AbaikanGalat

    # Docker pernah putus di tengah jalan yang panjang, dan saat itu job
    # pemantau ikut rusak. Kegagalan MEMBACA pemantau tidak boleh membuang
    # hasil k6 yang sudah tersimpan — itu bagian yang sesungguhnya diukur.
    # Menghentikan pemantau BARU SETELAH k6 selesai, supaya jendelanya selalu
    # menutupi seluruh jalan — berapa pun lamanya.
    New-Item -ItemType File -Path $tandaHenti -Force | Out-Null

    $puncak = @{}
    try { $puncak = Receive-Job -Job $pantau -Wait -AutoRemoveJob }
    catch { Write-Host "  (pemantau sumber daya gagal dibaca: $_)" -ForegroundColor DarkGray }
    Remove-Item $tandaHenti -ErrorAction SilentlyContinue
    $ringkasan += [pscustomobject]@{
        Sasaran = $t
        Berkas  = "$AKAR\hasil\$namaHasil.json"
        Puncak  = $puncak
    }

    if ($t -ne $Target[-1]) {
        Write-Host "`n  menunggu $Jeda detik supaya pekerjaan latar selesai..." -ForegroundColor DarkGray
        Start-Sleep -Seconds $Jeda
    }
}

}
finally {
    # Wajib lewat `finally`: kalau satu sasaran gagal di tengah jalan, mode
    # bergantian sudah terlanjur mematikan platform yang lain, dan
    # meninggalkannya mati akan membingungkan uji berikutnya.
    if ($Bergantian) {
        Write-Host ""
        Write-Host "  menyalakan kembali kedua platform..." -ForegroundColor DarkGray
        foreach ($c in $CONTAINER.Values) { Native docker @("start", $c) -AbaikanGalat }
    }
}

# ── 6. Bandingkan ──────────────────────────────────────────────────────────

Bagian "Hasil"

$p95 = @{}
foreach ($r in $ringkasan) {
    if (-not (Test-Path $r.Berkas)) {
        Write-Host "  $($r.Sasaran): berkas hasil tidak ada - k6 gagal jalan?" -ForegroundColor Red
        continue
    }
    $d = Get-Content $r.Berkas -Raw | ConvertFrom-Json
    $m = $d.metrics.http_req_duration.values
    $q = $d.metrics.http_reqs.values
    $c = $d.metrics.checks.values
    $p95[$r.Sasaran] = $m.'p(95)'

    Write-Host ""
    Write-Host "  $($r.Sasaran.ToUpper())" -ForegroundColor Yellow
    Write-Host ("    latency p95   : {0,9:N1} ms" -f $m.'p(95)')
    Write-Host ("    latency p99   : {0,9:N1} ms" -f $m.'p(99)')
    Write-Host ("    latency avg   : {0,9:N1} ms" -f $m.avg)
    Write-Host ("    rps terlayani : {0,9:N1}" -f $q.rate)
    Write-Host ("    check lulus   : {0,9:P1}" -f $c.rate)
    foreach ($nama in $r.Puncak.Keys) {
        $anonMB = [double]$r.Puncak[$nama].anonByte / 1MB
        $anonTeks = if ($anonMB -gt 0) { "{0:N0} MB" -f $anonMB } else { "-" }
        Write-Host ("    {0,-18}: CPU puncak {1,6:N1}%   proses {2,9}   container {3}" -f `
            $nama, $r.Puncak[$nama].cpu, $anonTeks, $r.Puncak[$nama].mem)
    }
}

if ($p95.Count -eq 2 -and $p95["service"] -gt 0) {
    Write-Host ""
    Write-Host ("  langflow berbanding service: {0:N1}x pada p95" -f ($p95["langflow"] / $p95["service"])) -ForegroundColor Magenta
}

Write-Host ""
Write-Host "  Berkas hasil : $AKAR\hasil\" -ForegroundColor DarkGray
if (-not $TanpaPrometheus) {
    Write-Host "  Grafana      : http://localhost:3005/d/synchrono-beban" -ForegroundColor DarkGray
}
Write-Host ""
