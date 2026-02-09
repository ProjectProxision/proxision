#!/bin/bash
#
# Proxision Uninstaller
# Removes Proxision AI assistant from Proxmox VE
#
# Usage: curl -sSL https://raw.githubusercontent.com/ProjectProxision/proxision/main/uninstall.sh | bash
#

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Configuration
INSTALL_DIR="/opt/proxision"
SERVICE_NAME="pve-ai-proxy"
PVE_WWW_DIR="/usr/share/pve-manager"
BACKUP_DIR="/opt/proxision/backups"

# Logging functions
log_info() { echo -e "${BLUE}[INFO]${NC} $1"; }
log_success() { echo -e "${GREEN}[SUCCESS]${NC} $1"; }
log_warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }
log_error() { echo -e "${RED}[ERROR]${NC} $1"; }

# Check if running as root
check_root() {
    if [[ $EUID -ne 0 ]]; then
        log_error "This script must be run as root"
        exit 1
    fi
}

# Check if Proxision is installed
check_installed() {
    if [[ ! -d "$INSTALL_DIR" ]]; then
        log_error "Proxision doesn't appear to be installed ($INSTALL_DIR not found)"
        exit 1
    fi
    log_info "Found Proxision installation at $INSTALL_DIR"
}

# Stop and remove the systemd service
remove_service() {
    log_info "Stopping and removing Proxision service..."
    
    if systemctl is-active --quiet "$SERVICE_NAME" 2>/dev/null; then
        systemctl stop "$SERVICE_NAME"
        log_info "Stopped $SERVICE_NAME service"
    fi
    
    if systemctl is-enabled --quiet "$SERVICE_NAME" 2>/dev/null; then
        systemctl disable "$SERVICE_NAME"
        log_info "Disabled $SERVICE_NAME service"
    fi
    
    if [[ -f "/etc/systemd/system/${SERVICE_NAME}.service" ]]; then
        rm -f "/etc/systemd/system/${SERVICE_NAME}.service"
        systemctl daemon-reload
        log_info "Removed service file"
    fi
    
    log_success "Service removed"
}

# Restore original Proxmox files from backup
restore_backups() {
    log_info "Restoring original Proxmox files..."
    
    local pve_js="$PVE_WWW_DIR/js/pvemanagerlib.js"
    
    # Restore pvemanagerlib.js if backup exists
    if [[ -f "$BACKUP_DIR/pvemanagerlib.js.original" ]]; then
        cp "$BACKUP_DIR/pvemanagerlib.js.original" "$pve_js"
        log_info "Restored pvemanagerlib.js from backup"
    else
        # If no backup, try to remove our injection manually
        if grep -q "Proxision Integration" "$pve_js" 2>/dev/null; then
            # Remove lines containing our marker
            sed -i '/Proxision Integration/d' "$pve_js"
            sed -i '/proxision-loader.js/d' "$pve_js"
            log_info "Removed Proxision injection from pvemanagerlib.js"
        fi
    fi
    
    # Remove CSS injection from index.html.tpl
    local index_html="$PVE_WWW_DIR/index.html.tpl"
    if [[ -f "$index_html" ]] && grep -q "proxision.css" "$index_html"; then
        sed -i '/proxision.css/d' "$index_html"
        log_info "Removed CSS injection from index template"
    fi
    
    log_success "Original files restored"
}

# Remove installed frontend files
remove_frontend_files() {
    log_info "Removing Proxision frontend files..."
    
    local files_to_remove=(
        "$PVE_WWW_DIR/js/proxision-loader.js"
        "$PVE_WWW_DIR/js/manager6/panel/AIChatPanel.js"
        "$PVE_WWW_DIR/js/manager6/window/AIModelSettings.js"
        "$PVE_WWW_DIR/css/proxision.css"
    )
    
    for file in "${files_to_remove[@]}"; do
        if [[ -f "$file" ]]; then
            rm -f "$file"
            log_info "Removed $file"
        fi
    done
    
    log_success "Frontend files removed"
}

# Remove installation directory
remove_install_dir() {
    log_info "Removing Proxision installation directory..."
    
    if [[ -d "$INSTALL_DIR" ]]; then
        rm -rf "$INSTALL_DIR"
        log_info "Removed $INSTALL_DIR"
    fi
    
    log_success "Installation directory removed"
}

# Restart Proxmox web service
restart_pve_proxy() {
    log_info "Restarting Proxmox web proxy..."
    
    systemctl restart pveproxy
    
    sleep 2
    if systemctl is-active --quiet pveproxy; then
        log_success "Proxmox web proxy restarted"
    else
        log_warn "pveproxy may need manual restart"
    fi
}

# Print success message
print_success() {
    echo ""
    echo -e "${GREEN}╔════════════════════════════════════════════════════════════╗${NC}"
    echo -e "${GREEN}║          Proxision uninstalled successfully!               ║${NC}"
    echo -e "${GREEN}╚════════════════════════════════════════════════════════════╝${NC}"
    echo ""
    echo "Proxision has been completely removed from your system."
    echo "Your Proxmox installation has been restored to its original state."
    echo ""
    echo -e "${BLUE}To reinstall Proxision:${NC}"
    echo "  curl -sSL https://raw.githubusercontent.com/ProjectProxision/proxision/main/install.sh | bash"
    echo ""
}

# Confirmation prompt
confirm_uninstall() {
    echo ""
    echo -e "${YELLOW}╔════════════════════════════════════════════════════════════╗${NC}"
    echo -e "${YELLOW}║                  Proxision Uninstaller                     ║${NC}"
    echo -e "${YELLOW}╚════════════════════════════════════════════════════════════╝${NC}"
    echo ""
    echo "This will remove Proxision from your Proxmox server."
    echo ""
    
    # Check if running interactively
    if [[ -t 0 ]]; then
        read -p "Are you sure you want to continue? (y/N) " -n 1 -r
        echo
        if [[ ! $REPLY =~ ^[Yy]$ ]]; then
            log_info "Uninstallation cancelled"
            exit 0
        fi
    else
        # Non-interactive mode (piped), check for FORCE env var
        if [[ "${PROXISION_FORCE_UNINSTALL}" != "1" ]]; then
            log_info "Running in non-interactive mode. Set PROXISION_FORCE_UNINSTALL=1 to confirm."
            log_info "Example: curl -sSL ... | PROXISION_FORCE_UNINSTALL=1 bash"
            exit 0
        fi
    fi
}

# Main uninstallation flow
main() {
    check_root
    confirm_uninstall
    check_installed
    remove_service
    restore_backups
    remove_frontend_files
    remove_install_dir
    restart_pve_proxy
    print_success
}

# Run main
main "$@"
