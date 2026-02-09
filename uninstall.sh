#!/bin/bash
#
# Proxision Uninstaller
# Removes Proxision AI assistant from Proxmox VE
#
# Usage (piped):
#   curl -sSL https://raw.githubusercontent.com/ProjectProxision/proxision/main/uninstall.sh | PROXISION_FORCE_UNINSTALL=1 bash
#
# Usage (interactive):
#   bash /path/to/uninstall.sh
#

set -euo pipefail

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

# Ensure PROXISION_FORCE_UNINSTALL has a default (needed for set -u)
PROXISION_FORCE_UNINSTALL="${PROXISION_FORCE_UNINSTALL:-0}"

# Configuration
INSTALL_DIR="/opt/proxision"
BACKUP_DIR="/opt/proxision/backups"
SERVICE_NAME="pve-ai-proxy"
PVE_WWW_DIR="/usr/share/pve-manager"
PVE_JS="$PVE_WWW_DIR/js/pvemanagerlib.js"
PVE_CSS_DIR="$PVE_WWW_DIR/css"

# Logging
log_info()    { echo -e "${BLUE}[INFO]${NC} $1"; }
log_success() { echo -e "${GREEN}[OK]${NC} $1"; }
log_warn()    { echo -e "${YELLOW}[WARN]${NC} $1"; }
log_error()   { echo -e "${RED}[ERROR]${NC} $1"; }

# ── Pre-flight ─────────────────────────────────────────────────────────
check_root() {
    if [[ $EUID -ne 0 ]]; then
        log_error "This script must be run as root"
        exit 1
    fi
}

# ── Confirmation ───────────────────────────────────────────────────────
confirm_uninstall() {
    echo ""
    echo -e "${YELLOW}╔════════════════════════════════════════════════════════════╗${NC}"
    echo -e "${YELLOW}║                  Proxision Uninstaller                     ║${NC}"
    echo -e "${YELLOW}╚════════════════════════════════════════════════════════════╝${NC}"
    echo ""
    echo "This will remove Proxision from your Proxmox server."
    echo ""

    if [[ -t 0 ]]; then
        REPLY=""
        read -p "Are you sure you want to continue? (y/N) " -n 1 -r REPLY
        echo
        if [[ ! $REPLY =~ ^[Yy]$ ]]; then
            log_info "Uninstallation cancelled"
            exit 0
        fi
    else
        if [[ "${PROXISION_FORCE_UNINSTALL}" != "1" ]]; then
            log_info "Running in non-interactive mode. Set PROXISION_FORCE_UNINSTALL=1 to confirm."
            log_info "Example: curl -sSL ... | PROXISION_FORCE_UNINSTALL=1 bash"
            exit 0
        fi
    fi
}

# ── Stop and remove systemd service ───────────────────────────────────
remove_service() {
    log_info "Stopping and removing Proxision service..."

    systemctl stop "$SERVICE_NAME" 2>/dev/null || true
    systemctl disable "$SERVICE_NAME" 2>/dev/null || true

    if [[ -f "/etc/systemd/system/${SERVICE_NAME}.service" ]]; then
        rm -f "/etc/systemd/system/${SERVICE_NAME}.service"
        systemctl daemon-reload
    fi

    log_success "Service removed"
}

# ── Restore original PVE files from backup ────────────────────────
restore_files() {
    log_info "Restoring original Proxmox files..."

    local js_restored=false
    local css_restored=false

    # Restore pvemanagerlib.js
    if [[ -f "$BACKUP_DIR/pvemanagerlib.js.bak" ]]; then
        cp "$BACKUP_DIR/pvemanagerlib.js.bak" "$PVE_JS"
        js_restored=true
        log_info "Restored pvemanagerlib.js from backup"
    elif [[ -f "$BACKUP_DIR/pvemanagerlib.js.original" ]]; then
        cp "$BACKUP_DIR/pvemanagerlib.js.original" "$PVE_JS"
        js_restored=true
        log_info "Restored pvemanagerlib.js from legacy backup"
    else
        log_warn "No JS backup found — cannot restore pvemanagerlib.js"
    fi

    # Restore ext6-pve.css
    local pve_css="$PVE_CSS_DIR/ext6-pve.css"
    if [[ -f "$BACKUP_DIR/ext6-pve.css.bak" ]]; then
        cp "$BACKUP_DIR/ext6-pve.css.bak" "$pve_css"
        css_restored=true
        log_info "Restored ext6-pve.css from backup"
    else
        log_warn "No CSS backup found — cannot restore ext6-pve.css"
    fi

    # Verify Proxision code is gone from restored files
    if [[ "$js_restored" == true ]]; then
        if grep -q "pveAIChatPanel" "$PVE_JS" 2>/dev/null; then
            log_warn "pveAIChatPanel still found in JS after restore — backup may be stale"
        else
            log_success "Verified: JS is clean (no Proxision code)"
        fi
    fi
    if [[ "$css_restored" == true ]]; then
        if grep -q "Proxision AI Chat" "$pve_css" 2>/dev/null; then
            log_warn "Proxision CSS still found after restore — backup may be stale"
        else
            log_success "Verified: CSS is clean (no Proxision styles)"
        fi
    fi

    # Clean up any leftover files from old installer versions
    local old_files=(
        "$PVE_WWW_DIR/js/proxision-loader.js"
        "$PVE_WWW_DIR/js/manager6/panel/AIChatPanel.js"
        "$PVE_WWW_DIR/js/manager6/window/AIModelSettings.js"
        "$PVE_WWW_DIR/css/proxision.css"
    )
    for f in "${old_files[@]}"; do
        if [[ -f "$f" ]]; then
            rm -f "$f"
            log_info "Removed leftover: $f"
        fi
    done

    # Remove CSS injection from index.html.tpl (old installer)
    local index_html="$PVE_WWW_DIR/index.html.tpl"
    if [[ -f "$index_html" ]] && grep -q "proxision.css" "$index_html" 2>/dev/null; then
        sed -i '/proxision.css/d' "$index_html"
        log_info "Removed proxision.css reference from index.html.tpl"
    fi

    log_success "Original files restored"
}

# ── Remove install directory ──────────────────────────────────────────
remove_install_dir() {
    log_info "Removing $INSTALL_DIR..."
    rm -rf "$INSTALL_DIR"
    log_success "Installation directory removed"
}

# ── Restart pveproxy ──────────────────────────────────────────
restart_pveproxy() {
    log_info "Restarting pveproxy..."

    echo ""
    echo -e "  ${YELLOW}>>> Hard-refresh your browser so the sidebar disappears:${NC}"
    echo -e "      ${BLUE}Windows/Linux:${NC} Ctrl + Shift + R"
    echo -e "      ${BLUE}Mac:${NC}           Cmd + Shift + R"
    echo ""

    systemctl restart pveproxy

    sleep 3
    if systemctl is-active --quiet pveproxy; then
        log_success "pveproxy restarted"
    else
        log_warn "pveproxy may need manual restart: systemctl restart pveproxy"
    fi
}

# ── Success banner ────────────────────────────────────────────
print_success() {
    echo ""
    echo -e "${GREEN}╔════════════════════════════════════════════════════════════╗${NC}"
    echo -e "${GREEN}║          Proxision uninstalled successfully!               ║${NC}"
    echo -e "${GREEN}╚════════════════════════════════════════════════════════════╝${NC}"
    echo ""
    echo "  Proxision has been completely removed."
    echo "  Your Proxmox installation has been restored to its original state."
    echo ""
    echo -e "  ${YELLOW}Hard-refresh your browser${NC} (Ctrl+Shift+R) to clear cached JS/CSS."
    echo ""
    echo -e "  ${BLUE}To reinstall:${NC}"
    echo "  curl -sSL https://raw.githubusercontent.com/ProjectProxision/proxision/main/install.sh | bash"
    echo ""
}

# ── Main ──────────────────────────────────────────────────────────────
main() {
    check_root
    confirm_uninstall
    remove_service
    restore_files
    remove_install_dir
    restart_pveproxy
    print_success
}

main "$@"
