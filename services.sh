#!/usr/bin/env bash
# ==============================================================================
# Synchrono Service Manager
# Mengelola semua container: PostgreSQL (pg-synchrono), SeaweedFS, dan Langflow
# ==============================================================================

set -e

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
COMPOSE_FILE="$DIR/infra/docker-compose.yml"

print_header() {
    echo "=================================================================="
    echo "                 SYNCHRONO SERVICE MANAGER                        "
    echo "=================================================================="
}

check_container_running() {
    local name="$1"
    docker ps --format '{{.Names}}' | grep -wq "^${name}$"
}

check_container_exists() {
    local name="$1"
    docker ps -a --format '{{.Names}}' | grep -wq "^${name}$"
}

start_services() {
    print_header
    echo "[1/3] Menyiapkan PostgreSQL (pg-synchrono)..."
    if check_container_running "pg-synchrono"; then
        echo "  • pg-synchrono: Sudah aktif (Up)"
    elif check_container_exists "pg-synchrono"; then
        echo "  • Memulai container pg-synchrono..."
        docker start pg-synchrono >/dev/null
        echo "  • pg-synchrono: Berhasil dinyalakan"
    else
        docker run -d --name pg-synchrono -p 5432:5432 \
          --restart no \
          -e POSTGRES_DB=synchrono -e POSTGRES_HOST_AUTH_METHOD=trust \
          postgres:15-alpine >/dev/null
        echo "  • pg-synchrono: Berhasil dibuat dan dinyalakan"
    fi

    # Pastikan restart policy tetap 'no' (hanya nyala manual saat dibutuhkan)
    docker update --restart no pg-synchrono >/dev/null 2>&1 || true

    echo -e "\n[2/3] Menyiapkan SeaweedFS & Langflow..."
    docker compose -f "$COMPOSE_FILE" up -d

    echo -e "\n[3/3] Menunggu kesiapan koneksi service..."
    local pg_ready=0
    for _ in {1..15}; do
        if docker exec pg-synchrono pg_isready -U postgres -d synchrono >/dev/null 2>&1; then
            pg_ready=1
            break
        fi
        sleep 1
    done

    if [ "$pg_ready" -eq 1 ]; then
        echo "  • PostgreSQL (Port 5432)  : SIAP (Healthy)"
    else
        echo "  • PostgreSQL (Port 5432)  : Menunggu koneksi..."
    fi

    echo -e "\n------------------------------------------------------------------"
    echo "STATUS LAYANAN:"
    echo "  • Langflow UI   : http://localhost:7860"
    echo "  • SeaweedFS S3  : http://localhost:8333"
    echo "  • PostgreSQL DB : localhost:5432 (Database: synchrono)"
    echo "------------------------------------------------------------------"
    echo "Mode On-Demand Aktif: Service TIDAK akan menyala otomatis saat boot."
    echo "Gunakan './services.sh stop' saat selesai untuk menghemat RAM & baterai."
    echo "=================================================================="
}

stop_services() {
    print_header
    echo "Menghentikan semua service Synchrono..."
    
    echo "  • Menghentikan Langflow & SeaweedFS..."
    docker compose -f "$COMPOSE_FILE" stop
    
    if check_container_running "pg-synchrono"; then
        echo "  • Menghentikan PostgreSQL (pg-synchrono)..."
        docker stop pg-synchrono >/dev/null
    fi
    
    echo "Semua service Synchrono berhasil dihentikan."
}

show_status() {
    print_header
    echo "Status Container Docker:"
    echo ""
    docker ps -a --filter "name=synchrono" --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}"
    echo ""
    
    echo "Pemeriksaan Port:"
    if docker exec pg-synchrono pg_isready -U postgres -d synchrono >/dev/null 2>&1; then
        echo "  • PostgreSQL (5432) : AKTIF & TERHUBUNG"
    else
        echo "  • PostgreSQL (5432) : TIDAK AKTIF / TERKUNCI"
    fi

    if curl -s http://localhost:7860/health_check >/dev/null 2>&1; then
        echo "  • Langflow (7860)   : AKTIF (API Ready)"
    else
        echo "  • Langflow (7860)   : MEMUAT / TIDAK AKTIF"
    fi

    if curl -s http://localhost:8333/ >/dev/null 2>&1; then
        echo "  • SeaweedFS (8333)  : AKTIF (S3 Ready)"
    else
        echo "  • SeaweedFS (8333)  : TIDAK AKTIF"
    fi
    echo "=================================================================="
}

case "${1:-start}" in
    start)
        start_services
        ;;
    stop)
        stop_services
        ;;
    restart)
        stop_services
        sleep 2
        start_services
        ;;
    status)
        show_status
        ;;
    *)
        echo "Penggunaan: $0 {start|stop|restart|status}"
        exit 1
        ;;
esac
