<#
.SYNOPSIS
    Sapuan skala: grading + matching pada berkas berjuta baris, beberapa ukuran.

.DESCRIPTION
    Menjawab pertanyaan yang tidak dijawab benchmark API: berapa lama dan
    berapa memori yang dibutuhkan mesin grading pada data sebesar Dukcapil?

    Tiap ukuran dijalankan dalam PROSES TERPISAH. Memori puncak diukur lewat
    `ru_maxrss`, yang hanya naik dan tidak pernah turun — menguji 1 juta lalu
    10 juta dalam satu proses akan membuat angka pertama terbawa ke yang kedua.

    Dua pola data, untuk dua pertanyaan:
        konsisten  NIK disintesis & konsisten  -> mengukur GRADING
        padan      NIK dari master, cocok      -> mengukur MATCHING

.EXAMPLE
    .\skala.ps1
    1, 2, 5, dan 10 juta baris. Pola konsisten, tanpa matching.

.EXAMPLE
    .\skala.ps1 -Baris 1000000,5000000 -Pola padan
    Mengukur matching pada berkas yang NIK-nya benar-benar ada di master.
#>

[CmdletBinding()]
param(
    [long[]] $Baris = @(1000000, 2000000, 5000000, 10000000),

    [ValidateSet("konsisten", "padan")]
    [string] $Pola = "konsisten",

    # Batas memori DuckDB. WAJIB di container — tanpa ini DuckDB tidak melihat
    # batas cgroup dan bisa dibunuh kernel (exit 137) alih-alih menumpah ke disk.
    [string] $BatasMemori = "3GB",

    [switch] $TanpaMatching,

    [string] $Container = "synchrono-service"
)

# BERKAS INI HARUS UTF-8 DENGAN BOM — lihat catatan di jalankan.ps1.

$ErrorActionPreference = "Stop"
$AKAR = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $AKAR

function Native {
    param([string] $Perintah, [string[]] $Argumen, [switch] $AbaikanGalat)
    $simpan = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try { & $Perintah @Argumen 2>&1 | ForEach-Object { "$_" } }
    finally { $ErrorActionPreference = $simpan }
    if ($LASTEXITCODE -ne 0 -and -not $AbaikanGalat) { throw "$Perintah keluar $LASTEXITCODE" }
}

$jalan = docker ps --format "{{.Names}}"
if ($jalan -notcontains $Container) {
    throw "Container $Container belum jalan. cd .. ; docker compose up -d"
}

Write-Host ""
Write-Host "  pola data     : $Pola" -ForegroundColor Cyan
Write-Host "  batas memori  : $BatasMemori" -ForegroundColor Cyan
Write-Host "  matching      : $(if ($TanpaMatching) { 'tidak' } else { 'ya' })" -ForegroundColor Cyan

$hasil = @()

foreach ($n in $Baris) {
    $label = "$([math]::Round($n / 1000000, 1)) juta"
    Write-Host ""
    Write-Host ("=" * 66) -ForegroundColor DarkGray
    Write-Host "  $label baris — membangkitkan" -ForegroundColor Yellow
    Write-Host ("=" * 66) -ForegroundColor DarkGray

    $nama = "besar-$([long]($n / 1000))k" + $(if ($Pola -eq "padan") { "-padan" } else { "" })

    Native docker @("exec", "-e", "BESAR_POLA=$Pola", $Container,
                    "python", "/synchrono/beban/buat_data_besar.py", "$n") -AbaikanGalat

    Write-Host ""
    Write-Host "  $label baris — grading$(if (-not $TanpaMatching) { ' + matching' })" -ForegroundColor Yellow

    $arg = @("exec",
             "-e", "DUCKDB_MEMORY_LIMIT=$BatasMemori",
             "-e", "DUCKDB_TEMP_DIR=/tmp/duckdb_spill",
             $Container, "sh", "-c")
    $perintah = "mkdir -p /tmp/duckdb_spill && python /synchrono/beban/uji_skala.py $nama --json"
    if ($TanpaMatching) { $perintah += " --tanpa-matching" }
    $arg += $perintah

    $keluaran = & docker @arg 2>&1
    $baris_json = $keluaran | Select-String -Pattern "^HASIL_JSON " | Select-Object -First 1

    if (-not $baris_json) {
        # Exit 137 = SIGKILL = dibunuh kernel karena kehabisan memori. Ini
        # temuan, bukan kegagalan skrip — dicatat lalu sapuan dilanjutkan.
        $sebab = if ($LASTEXITCODE -eq 137) { "OOM (exit 137)" } else { "gagal (exit $LASTEXITCODE)" }
        Write-Host "  $label : $sebab" -ForegroundColor Red
        $hasil += [pscustomobject]@{ Baris = $n; Status = $sebab }
        continue
    }

    $j = ($baris_json -replace "^HASIL_JSON ", "") | ConvertFrom-Json
    $hasil += [pscustomobject]@{
        Baris        = $j.baris
        Kolom        = $j.kolom
        Status       = "ok"
        Grade        = $j.grade
        GradingDetik = $j.grading_detik
        BarisPerDetik= $j.grading_baris_per_detik
        G2           = $j.tahap.G2
        G3           = $j.tahap.G3
        G5           = $j.tahap.G5
        EnrichedMB   = $j.enriched_mb
        RssMB        = $j.rss_puncak_mb
        TumpahanMB   = $j.tumpahan_disk_mb
        MatchDetik   = $j.matching_detik
        Cocok        = $j.cocok
    }
    Write-Host ("  {0} : grading {1}s ({2:N0} baris/dtk), RSS {3:N0} MB" -f `
        $label, $j.grading_detik, $j.grading_baris_per_detik, $j.rss_puncak_mb) -ForegroundColor Green
}

Write-Host ""
Write-Host ("=" * 66) -ForegroundColor DarkGray
Write-Host "  RINGKASAN — pola $Pola, batas memori $BatasMemori" -ForegroundColor Yellow
Write-Host ("=" * 66) -ForegroundColor DarkGray

$hasil | Where-Object { $_.Status -eq "ok" } | Format-Table `
    @{n='baris';e={"{0:N0}" -f $_.Baris}},
    @{n='kol';e={$_.Kolom}},
    @{n='grade';e={$_.Grade}},
    @{n='grading s';e={"{0:N1}" -f $_.GradingDetik}},
    @{n='baris/dtk';e={"{0:N0}" -f $_.BarisPerDetik}},
    @{n='G2 s';e={"{0:N1}" -f $_.G2}},
    @{n='G3 s';e={"{0:N1}" -f $_.G3}},
    @{n='G5 s';e={"{0:N1}" -f $_.G5}},
    @{n='enriched MB';e={"{0:N0}" -f $_.EnrichedMB}},
    @{n='RSS MB';e={"{0:N0}" -f $_.RssMB}},
    @{n='tumpah MB';e={"{0:N0}" -f $_.TumpahanMB}},
    @{n='match s';e={if ($_.MatchDetik) {"{0:N1}" -f $_.MatchDetik} else {"-"}}},
    @{n='cocok';e={if ($_.Cocok -ne $null) {"{0:N0}" -f $_.Cocok} else {"-"}}} `
    -AutoSize

$gagal = $hasil | Where-Object { $_.Status -ne "ok" }
if ($gagal) {
    Write-Host "  Ukuran yang TIDAK selesai:" -ForegroundColor Red
    foreach ($g in $gagal) {
        Write-Host ("     {0:N0} baris : {1}" -f $g.Baris, $g.Status) -ForegroundColor Red
    }
    Write-Host "  Naikkan -BatasMemori, atau naikkan memori container." -ForegroundColor DarkGray
}
Write-Host ""
