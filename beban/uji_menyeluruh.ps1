<#
.SYNOPSIS
    Pengujian menyeluruh: seluruh skenario, dari lapisan API sampai 10 juta baris.

.DESCRIPTION
    Menjalankan SATU rangkaian lengkap dalam urutan yang sama tiap kali, dengan
    kedua sisi selalu diukur BERGANTIAN - container yang tidak sedang diukur
    dimatikan lebih dulu.

    Kenapa satu skrip, bukan dipanggil satu per satu: supaya seluruh angka
    dalam satu laporan berasal dari keadaan mesin yang sama. Menjalankan
    kapasitas hari ini dan skala besok menghasilkan dua angka yang tidak boleh
    diletakkan di tabel yang sama.

    Tiap tahap menulis catatannya sendiri ke hasil/menyeluruh-<stempel>/, dan
    ringkasannya dikumpulkan ke satu berkas RINGKASAN.txt di folder yang sama.

.EXAMPLE
    .\uji_menyeluruh.ps1
    .\uji_menyeluruh.ps1 -LewatiBesar          # tanpa 5 dan 10 juta baris
    .\uji_menyeluruh.ps1 -HanyaTahap kapasitas
#>

[CmdletBinding()]
param(
    # Melewati berkas 5 dan 10 juta baris. Keduanya memakan belasan menit.
    [switch] $LewatiBesar,

    # Menjalankan sebagian tahap saja, untuk mengulang satu tahap yang gagal.
    [string[]] $HanyaTahap = @(),

    [int] $Jeda = 30
)

# BERKAS INI HARUS UTF-8 DENGAN BOM - lihat catatan yang sama di jalankan.ps1.
$ErrorActionPreference = "Stop"
$AKAR = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $AKAR

# `powershell -File` tidak memecah koma; lihat catatan yang sama di jalankan.ps1.
$HanyaTahap = @($HanyaTahap | ForEach-Object { $_ -split "," } |
                ForEach-Object { $_.Trim() } | Where-Object { $_ })

$STEMPEL = Get-Date -Format "yyyyMMdd-HHmmss"
$KELUARAN = Join-Path $AKAR "hasil\menyeluruh-$STEMPEL"
New-Item -ItemType Directory -Force -Path $KELUARAN | Out-Null
$RINGKASAN = Join-Path $KELUARAN "RINGKASAN.txt"

function Catat {
    param([string] $Teks, [string] $Warna = "Gray")
    Write-Host $Teks -ForegroundColor $Warna
    Add-Content -Path $RINGKASAN -Value $Teks -Encoding utf8
}

function Bagian {
    param([string] $Judul)
    Catat ""
    Catat ("=" * 72)
    Catat "  $Judul"
    Catat ("=" * 72)
}

function Jalankan {
    <#
        Memanggil skrip beban yang sudah ada, menyimpan seluruh keluarannya,
        lalu memungut baris yang menarik ke RINGKASAN.

        Keluaran mentahnya TIDAK dibuang: bila ada angka yang dipertanyakan,
        berkas per tahap inilah yang dibuka.
    #>
    param(
        [string] $Nama,
        [string] $Skrip,
        [string[]] $Argumen,
        [string[]] $Pungut
    )

    if ($HanyaTahap -and ($HanyaTahap -notcontains $Nama)) {
        Write-Host "  (lewati $Nama)" -ForegroundColor DarkGray
        return
    }

    $berkas = Join-Path $KELUARAN "$Nama.txt"
    Bagian $Nama
    Write-Host "  menjalankan... (catatan: $Nama.txt)" -ForegroundColor DarkGray

    $mulai = Get-Date
    & powershell -NoProfile -ExecutionPolicy Bypass -File $Skrip @Argumen `
        *> $berkas
    $kode = $LASTEXITCODE
    $durasi = [int]((Get-Date) - $mulai).TotalSeconds

    # k6 keluar dengan 108 saat skenario sengaja dihentikan lebih awal; itu
    # bukan kegagalan. Yang lain perlu terlihat di ringkasan.
    if ($kode -ne 0 -and $kode -ne 108) {
        Catat "  PERINGATAN: keluar dengan kode $kode" "Yellow"
    }
    Catat "  durasi $durasi detik"
    Catat ""

    foreach ($baris in (Get-Content $berkas -ErrorAction SilentlyContinue)) {
        foreach ($pola in $Pungut) {
            if ($baris -match $pola) { Catat "  $($baris.Trim())"; break }
        }
    }
}

# Pola baris yang dipungut dari tiap jenis keluaran.
$P_BEBAN = @("^\s{4}latency (p95|p99|avg)", "^\s{4}rps terlayani",
             "^\s{4}check lulus", "^\s{4}synchrono-(langflow|service)\s*:",
             "berbanding service", "SELESAI status=", "dispatch \d+ ms",
             "TIDAK SELESAI", "^\s+(LANGFLOW|SERVICE)\s*$")
$P_KAPASITAS = @("^\s+\d+\s+[\d,\.]+\s+", "KURVA KAPASITAS")

Catat "PENGUJIAN MENYELURUH - $STEMPEL"
Catat ""
Catat "Lingkungan:"
$stat = docker stats --no-stream --format "  {{.Name}}  CPU {{.CPUPerc}}  MEM {{.MemUsage}}" `
    synchrono-service synchrono-langflow synchrono-seaweedfs
foreach ($b in $stat) { Catat $b }
$lfw = docker exec synchrono-langflow printenv LANGFLOW_WORKERS 2>$null
Catat "  langflow workers: $lfw"

# ── 1. Lapisan API ─────────────────────────────────────────────────────────
Jalankan "1-dasar" ".\jalankan.ps1" `
    @("-Skenario", "dasar", "-Target", "langflow,service", "-Bergantian",
      "-Rps", "50", "-Detik", "30") $P_BEBAN

Jalankan "2-status-2rps" ".\jalankan.ps1" `
    @("-Skenario", "status", "-Target", "langflow,service", "-Bergantian",
      "-Rps", "2", "-Detik", "30") $P_BEBAN

Jalankan "3-status-10rps" ".\jalankan.ps1" `
    @("-Skenario", "status", "-Target", "langflow,service", "-Bergantian",
      "-Rps", "10", "-Detik", "30") $P_BEBAN

Jalankan "4-aturan" ".\jalankan.ps1" `
    @("-Skenario", "aturan", "-Target", "langflow,service", "-Bergantian",
      "-Rps", "5", "-Detik", "30") $P_BEBAN

Jalankan "5-campuran" ".\jalankan.ps1" `
    @("-Skenario", "campuran", "-Target", "langflow,service", "-Bergantian",
      "-Rps", "2", "-Detik", "45") $P_BEBAN

Jalankan "5b-per-api" ".\jalankan.ps1" `
    @("-Skenario", "per_api", "-Target", "langflow,service", "-Bergantian",
      "-Detik", "25") @("^\s+(endpoint|config-|grading)", "satuan ms", "check yang gagal",
                        "^\s+[a-z-]+\s+\d+\s+[\d,\.]+", "PER API")

# ── 2. Kurva kapasitas ─────────────────────────────────────────────────────
# Dijalankan terpisah per sisi karena kapasitas.ps1 memang satu sasaran per
# jalan. Container lawan dimatikan manual di sini.
Jalankan "6-kapasitas-langflow" ".\kapasitas.ps1" `
    @("-Target", "langflow", "-Bergantian", "-Vu", "1,2,4,8,16,32,64,141", "-Detik", "20") $P_KAPASITAS

Jalankan "7-kapasitas-service" ".\kapasitas.ps1" `
    @("-Target", "service", "-Bergantian", "-Vu", "1,4,16,64,141", "-Detik", "20") $P_KAPASITAS

# ── 3. Skala jutaan baris ──────────────────────────────────────────────────
Jalankan "8-skala-1juta" ".\jalankan.ps1" `
    @("-Skenario", "skala_jutaan", "-Target", "langflow,service", "-Bergantian",
      "-BerkasBesar", "besar-1000k", "-Bucket", "bucket-test",
      "-Bising", "2", "-BatasMenit", "12") $P_BEBAN

if (-not $LewatiBesar) {
    Jalankan "9-skala-5juta" ".\jalankan.ps1" `
        @("-Skenario", "skala_jutaan", "-Target", "langflow,service", "-Bergantian",
          "-BerkasBesar", "besar-5000k", "-Bucket", "bucket-test",
          "-Bising", "2", "-BatasMenit", "20") $P_BEBAN

    Jalankan "10-skala-10juta" ".\jalankan.ps1" `
        @("-Skenario", "skala_jutaan", "-Target", "langflow,service", "-Bergantian",
          "-BerkasBesar", "besar-10000k", "-Bucket", "bucket-test",
          "-Bising", "2", "-BatasMenit", "30") $P_BEBAN
}

Bagian "SELESAI"
Catat "  Catatan per tahap : $KELUARAN"
Catat "  Ringkasan ini     : $RINGKASAN"
Write-Host ""
Write-Host "  Selesai. Ringkasan: $RINGKASAN" -ForegroundColor Green
