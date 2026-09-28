<#
.SYNOPSIS
    Kurva kapasitas: jalankan kapasitas.js pada beberapa tingkat konkurensi.

.DESCRIPTION
    Menjawab satu pertanyaan yang tidak bisa dijawab skenario arrival-rate:
    berapa sebenarnya batas atas sasaran ini?

    Caranya dengan menaikkan jumlah penelepon serentak dan melihat kapan
    throughput BERHENTI NAIK. Selama menambah penelepon masih menambah
    throughput, kapasitasnya belum tercapai. Begitu throughput datar sementara
    latency naik sebanding, itulah batasnya - dan menambah beban setelah titik
    itu hanya memperpanjang antrean.

.EXAMPLE
    .\kapasitas.ps1 -Target langflow
    .\kapasitas.ps1 -Target service -Vu 1,4,16,64
#>

[CmdletBinding()]
param(
    [ValidateSet("service", "langflow")]
    [string] $Target = "langflow",

    # Dideklarasikan string[], bukan int[], supaya `powershell -File kapasitas.ps1
    # -Vu 1,2,4,8` tidak diam-diam menjadi SATU nilai "12 48". Lewat -File,
    # PowerShell tidak memecah komanya seperti kalau skripnya dipanggil langsung
    # dari prompt; pemecahannya dikerjakan di bawah.
    [string[]] $Vu = @("1", "2", "4", "8"),

    [int] $Detik = 20,

    [string] $KunciService = "synchrono-bench-key",

    # Matikan platform yang TIDAK diukur selama kurva diambil, lalu nyalakan
    # lagi sesudahnya. Alasannya sama seperti di jalankan.ps1: Langflow menahan
    # 1,2 GB saat menganggur, jadi sisi service akan diukur dengan sisa memori
    # lebih sedikit kalau keduanya dibiarkan hidup.
    [switch] $Bergantian
)

# BERKAS INI HARUS UTF-8 DENGAN BOM - lihat catatan yang sama di jalankan.ps1.

$ErrorActionPreference = "Stop"
$AKAR = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $AKAR

function Native {
    param([string] $Perintah, [string[]] $Argumen, [switch] $AbaikanGalat)
    $simpan = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try { & $Perintah @Argumen 2>&1 | ForEach-Object { Write-Host "$_" } }
    finally { $ErrorActionPreference = $simpan }
    if ($LASTEXITCODE -ne 0 -and -not $AbaikanGalat) { throw "$Perintah keluar $LASTEXITCODE" }
}

function TungguSiap {
    param([string] $Url, [int] $BatasDetik = 180)
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

$URL_SIAP = @{
    service  = "http://localhost:8000/health"
    langflow = "http://localhost:7860/health_check"
}

$CONTAINER = @{ service = "synchrono-service"; langflow = "synchrono-langflow" }

if ($Bergantian) {
    foreach ($nama in $CONTAINER.Keys) {
        if ($nama -eq $Target) { continue }
        Write-Host "  mematikan $($CONTAINER[$nama]) (bukan yang diukur)" -ForegroundColor DarkGray
        Native docker @("stop", $CONTAINER[$nama]) -AbaikanGalat
    }
    Native docker @("start", $CONTAINER[$Target]) -AbaikanGalat
    if (-not (TungguSiap $URL_SIAP[$Target] 180)) {
        throw "$Target tidak siap setelah dinyalakan sendirian."
    }
    Write-Host "  hanya $($CONTAINER[$Target]) yang hidup" -ForegroundColor Green
}

$kunciLangflow = ""
if ($Target -eq "langflow") {
    if (-not (TungguSiap $URL_SIAP["langflow"])) { throw "Langflow tidak menjawab." }
    $tok = (Invoke-RestMethod "http://localhost:7860/api/v1/login" -Method POST `
            -Body "username=admin&password=synchrono123" `
            -ContentType "application/x-www-form-urlencoded").access_token
    $kunciLangflow = (Invoke-RestMethod "http://localhost:7860/api/v1/api_key/" -Method POST `
            -Headers @{ Authorization = "Bearer $tok" } -ContentType "application/json" `
            -Body "{`"name`":`"kapasitas-$(Get-Date -Format HHmmss)`"}").api_key
}

$stempel = Get-Date -Format "yyyyMMdd-HHmmss"
$baris = @()

$Vu = @($Vu | ForEach-Object { $_ -split "," } | ForEach-Object { $_.Trim() } |
         Where-Object { $_ } | ForEach-Object {
             $n = 0
             if (-not [int]::TryParse($_, [ref] $n)) {
                 throw "Nilai -Vu tidak berupa angka: '$_'"
             }
             $n
         })
if (-not $Vu) { throw "-Vu kosong." }

foreach ($v in $Vu) {
    Write-Host ""
    Write-Host ("=" * 60) -ForegroundColor DarkGray
    Write-Host "  $Target - $v penelepon serentak, $Detik detik" -ForegroundColor Cyan
    Write-Host ("=" * 60) -ForegroundColor DarkGray

    if (-not (TungguSiap $URL_SIAP[$Target])) {
        Write-Host "  sasaran tidak siap - dilewati" -ForegroundColor Red
        continue
    }

    $nama = "$stempel-kapasitas-$Target-vu$v"
    $arg = @(
        "run", "--rm", "--network", "infra_default",
        "-v", "$AKAR\k6:/skrip:ro",
        "-v", "$AKAR\hasil:/hasil",
        "-e", "TARGET=$Target",
        "-e", "VU=$v",
        "-e", "DETIK=$Detik",
        "-e", "SERVICE_API_KEY=$KunciService",
        "-e", "LANGFLOW_API_KEY=$kunciLangflow",
        "-e", "HASIL_JSON=/hasil/$nama.json",
        "grafana/k6:latest", "run", "--quiet", "/skrip/kapasitas.js"
    )
    Native docker $arg -AbaikanGalat

    $berkas = "$AKAR\hasil\$nama.json"
    if (Test-Path $berkas) {
        $d = Get-Content $berkas -Raw | ConvertFrom-Json
        $baris += [pscustomobject]@{
            VU      = $v
            Rps     = [math]::Round($d.metrics.http_reqs.values.rate, 2)
            AvgMs   = [math]::Round($d.metrics.http_req_duration.values.avg, 1)
            P95Ms   = [math]::Round($d.metrics.http_req_duration.values.'p(95)', 1)
        }
    }

    # Beri waktu sasaran menghabiskan sisa pekerjaan sebelum tingkat berikutnya.
    Start-Sleep -Seconds 10
}

Write-Host ""
Write-Host ("=" * 60) -ForegroundColor DarkGray
Write-Host "  KURVA KAPASITAS - $Target" -ForegroundColor Yellow
Write-Host ("=" * 60) -ForegroundColor DarkGray
Write-Host ""
Write-Host ("  {0,3}  {1,9}  {2,10}  {3,10}  {4}" -f "VU", "rps", "avg ms", "p95 ms", "")
Write-Host ("  {0}" -f ("-" * 52))

$rpsSebelum = 0
foreach ($b in $baris) {
    # Kalau menggandakan penelepon hanya menambah sedikit throughput sementara
    # latency ikut berlipat, kapasitasnya sudah tercapai di tingkat sebelumnya.
    $tanda = ""
    if ($rpsSebelum -gt 0) {
        $naik = ($b.Rps - $rpsSebelum) / $rpsSebelum
        if ($naik -lt 0.15) { $tanda = "<- mentok, throughput berhenti naik" }
    }
    Write-Host ("  {0,3}  {1,9:N2}  {2,10:N1}  {3,10:N1}  {4}" -f `
        $b.VU, $b.Rps, $b.AvgMs, $b.P95Ms, $tanda)
    $rpsSebelum = $b.Rps
}
Write-Host ""

# Kembalikan keadaan semula. Meninggalkan salah satu platform mati akan
# membingungkan uji berikutnya.
if ($Bergantian) {
    Write-Host "  menyalakan kembali kedua platform..." -ForegroundColor DarkGray
    foreach ($c in $CONTAINER.Values) { Native docker @("start", $c) -AbaikanGalat }
}
