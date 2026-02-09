#!/bin/bash
#
# Proxision Installer
# Installs the Proxision AI assistant into Proxmox VE
#
# Usage (main branch):
#   curl -sSL https://raw.githubusercontent.com/ProjectProxision/proxision/main/install.sh | bash
#
# Usage (dev branch):
#   curl -sSL https://raw.githubusercontent.com/ProjectProxision/proxision/dev/install.sh | bash
#
# Usage (local):
#   bash /path/to/install.sh
#

set -euo pipefail

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

# Branch / repo configuration
# Each branch's install.sh sets its own default here.
# Override with: PROXISION_BRANCH=dev curl ... | bash
BRANCH="${PROXISION_BRANCH:-main}"
REPO_URL="${PROXISION_REPO:-https://raw.githubusercontent.com/ProjectProxision/proxision/${BRANCH}}"

INSTALL_DIR="/opt/proxision"
BACKUP_DIR="/opt/proxision/backups"
SERVICE_NAME="pve-ai-proxy"
PVE_WWW_DIR="/usr/share/pve-manager"
PVE_JS="$PVE_WWW_DIR/js/pvemanagerlib.js"
PVE_CSS_DIR="$PVE_WWW_DIR/css"

# Detect if running from local repo (script directory has the source files)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-./install.sh}" 2>/dev/null || echo ".")" && pwd)"
LOCAL_MODE=false
if [[ -f "$SCRIPT_DIR/AIChatPanel.js" && -f "$SCRIPT_DIR/AIModelSettings.js" && -f "$SCRIPT_DIR/pve-ai-proxy.py" ]]; then
    LOCAL_MODE=true
fi

# Logging
log_info()    { echo -e "${BLUE}[INFO]${NC} $1"; }
log_success() { echo -e "${GREEN}[OK]${NC} $1"; }
log_warn()    { echo -e "${YELLOW}[WARN]${NC} $1"; }
log_error()   { echo -e "${RED}[ERROR]${NC} $1"; }

# Get a source file: local copy or download from GitHub
get_file() {
    local filename="$1"
    local dest="$2"

    if [[ "$LOCAL_MODE" == true ]]; then
        cp "$SCRIPT_DIR/$filename" "$dest"
    elif command -v curl &>/dev/null; then
        if ! curl -fsSL "${REPO_URL}/${filename}" -o "$dest"; then
            log_error "Failed to download ${filename} from ${REPO_URL}/${filename}"
            exit 1
        fi
    elif command -v wget &>/dev/null; then
        if ! wget -q "${REPO_URL}/${filename}" -O "$dest"; then
            log_error "Failed to download ${filename} from ${REPO_URL}/${filename}"
            exit 1
        fi
    else
        log_error "Neither curl nor wget available"
        exit 1
    fi

    if [[ ! -s "$dest" ]]; then
        log_error "Downloaded file is empty: $dest (source: ${REPO_URL}/${filename})"
        exit 1
    fi
}

# ── Pre-flight checks ──────────────────────────────────────────────────
preflight() {
    if [[ $EUID -ne 0 ]]; then
        log_error "This script must be run as root"
        exit 1
    fi

    if ! command -v pvesh &>/dev/null; then
        log_error "This doesn't appear to be a Proxmox VE server (pvesh not found)"
        exit 1
    fi

    if [[ ! -f "$PVE_JS" ]]; then
        log_error "Proxmox JS bundle not found: $PVE_JS"
        exit 1
    fi

    if ! command -v python3 &>/dev/null; then
        log_info "Installing python3..."
        apt-get update -qq && apt-get install -y -qq python3
    fi

    log_success "Pre-flight checks passed"
}

# ── Step 1: Backup original PVE files ──────────────────────────────────
backup_files() {
    echo -e "\n${BLUE}[1/7]${NC} Backing up original files..."
    mkdir -p "$BACKUP_DIR"

    if [[ ! -f "$BACKUP_DIR/pvemanagerlib.js.bak" ]]; then
        cp "$PVE_JS" "$BACKUP_DIR/pvemanagerlib.js.bak"
        log_info "Backed up pvemanagerlib.js"
    fi

    # Backup the main PVE CSS file
    local pve_css="$PVE_CSS_DIR/ext6-pve.css"
    if [[ -f "$pve_css" && ! -f "$BACKUP_DIR/ext6-pve.css.bak" ]]; then
        cp "$pve_css" "$BACKUP_DIR/ext6-pve.css.bak"
        log_info "Backed up ext6-pve.css"
    fi

    log_success "Backups complete"
}

# ── Step 2: Restore clean state (for re-installs) ─────────────────────
restore_clean() {
    echo -e "${BLUE}[2/7]${NC} Restoring clean state..."

    if [[ -f "$BACKUP_DIR/pvemanagerlib.js.bak" ]]; then
        cp "$BACKUP_DIR/pvemanagerlib.js.bak" "$PVE_JS"
        log_info "Restored JS from backup"
    fi

    local pve_css="$PVE_CSS_DIR/ext6-pve.css"
    if [[ -f "$BACKUP_DIR/ext6-pve.css.bak" ]]; then
        cp "$BACKUP_DIR/ext6-pve.css.bak" "$pve_css"
        log_info "Restored CSS from backup"
    fi

    log_success "Clean state restored"
}

# ── Step 3: Append AI JS files to bundle ───────────────────────────────
append_js() {
    echo -e "${BLUE}[3/7]${NC} Appending AI JS files to bundle..."

    mkdir -p "$INSTALL_DIR"

    # Download/copy source files to install dir
    get_file "AIChatPanel.js" "$INSTALL_DIR/AIChatPanel.js"
    get_file "AIModelSettings.js" "$INSTALL_DIR/AIModelSettings.js"

    log_info "AIChatPanel.js: $(wc -c < "$INSTALL_DIR/AIChatPanel.js") bytes"
    log_info "AIModelSettings.js: $(wc -c < "$INSTALL_DIR/AIModelSettings.js") bytes"

    # Append to pvemanagerlib.js (AIModelSettings first, then AIChatPanel)
    {
        echo ""
        echo "// ─── Proxision AI Assistant ─── DO NOT EDIT BELOW ───"
        cat "$INSTALL_DIR/AIModelSettings.js"
        echo ""
        cat "$INSTALL_DIR/AIChatPanel.js"
        echo ""
    } >> "$PVE_JS"

    # Verify the class definitions were appended
    if grep -q "PVE.panel.AIChatPanel" "$PVE_JS" && grep -q "PVE.window.AIModelSettings" "$PVE_JS"; then
        log_success "AI JS appended to bundle"
    else
        log_error "JS append failed — class definitions not found in bundle"
        exit 1
    fi
}

# ── Step 4: Patch workspace layout ─────────────────────────────────────
patch_workspace() {
    echo -e "${BLUE}[4/7]${NC} Patching Workspace layout..."

    local PANEL_CFG="xtype: 'pveAIChatPanel', stateful: true, stateId: 'pveeast', itemId: 'east', region: 'east', collapsible: true, collapseDirection: 'right', animCollapse: true, split: true, width: 340, minWidth: 280, maxWidth: 500, border: false, margin: '0 5 0 0'"
    local PATCHED=false

    # Try multiple patterns to handle different PVE formatting
    # Pattern 1: xtype: 'pveStatusPanel',  (single quotes, space after colon)
    if grep -q "xtype: 'pveStatusPanel'," "$PVE_JS" 2>/dev/null; then
        sed -i "s|xtype: 'pveStatusPanel',|${PANEL_CFG}}, {xtype: 'pveStatusPanel',|" "$PVE_JS"
        PATCHED=true
        log_info "Matched pattern: xtype: 'pveStatusPanel',"
    # Pattern 2: xtype:'pveStatusPanel',  (no space after colon)
    elif grep -q "xtype:'pveStatusPanel'," "$PVE_JS" 2>/dev/null; then
        sed -i "s|xtype:'pveStatusPanel',|${PANEL_CFG}}, {xtype:'pveStatusPanel',|" "$PVE_JS"
        PATCHED=true
        log_info "Matched pattern: xtype:'pveStatusPanel',"
    # Pattern 3: xtype: \"pveStatusPanel\",  (double quotes)
    elif grep -q 'xtype: "pveStatusPanel",' "$PVE_JS" 2>/dev/null; then
        sed -i 's|xtype: "pveStatusPanel",|'"${PANEL_CFG}"'}, {xtype: "pveStatusPanel",|' "$PVE_JS"
        PATCHED=true
        log_info 'Matched pattern: xtype: "pveStatusPanel",'
    fi

    # Verify sed actually injected the panel
    if [[ "$PATCHED" == true ]]; then
        if grep -q "pveAIChatPanel" "$PVE_JS"; then
            log_success "Workspace patched (east region — sed)"
        else
            log_warn "sed ran but pveAIChatPanel not found — falling back to monkey-patch"
            PATCHED=false
        fi
    fi

    # Fallback: monkey-patch if sed didn't work
    if [[ "$PATCHED" == false ]]; then
        log_warn "pveStatusPanel pattern not found — using fallback monkey-patch"
        cat >> "$PVE_JS" << 'PROXISION_PATCH'

// ─── Proxision Workspace Integration (fallback) ───
(function() {
    Ext.onReady(function() {
        // Wait for the workspace to be created
        var checkInterval = setInterval(function() {
            var vp = Ext.ComponentQuery.query('viewport')[0];
            if (!vp) return;
            // Check if already added
            if (Ext.ComponentQuery.query('pveAIChatPanel').length > 0) {
                clearInterval(checkInterval);
                return;
            }
            try {
                vp.add(Ext.create('PVE.panel.AIChatPanel', {
                    stateful: true, stateId: 'pveeast', itemId: 'east',
                    region: 'east', collapsible: true, collapseDirection: 'right',
                    animCollapse: true, split: true, width: 340,
                    minWidth: 280, maxWidth: 500, border: false, margin: '0 5 0 0'
                }));
                vp.updateLayout();
                clearInterval(checkInterval);
                console.log('Proxision: AI Chat Panel loaded (fallback)');
            } catch (e) {
                console.error('Proxision: fallback init error', e);
                clearInterval(checkInterval);
            }
        }, 500);
        // Stop trying after 30 seconds
        setTimeout(function() { clearInterval(checkInterval); }, 30000);
    });
})();
// ─── End Proxision ───
PROXISION_PATCH
        log_success "Workspace patched (fallback monkey-patch)"
    fi
}

# ── Step 5: Deploy CSS ─────────────────────────────────────────────────
deploy_css() {
    echo -e "${BLUE}[5/7]${NC} Deploying CSS..."

    local pve_css="$PVE_CSS_DIR/ext6-pve.css"

    # Append Proxision styles (Proxmox-native theme) to the main PVE CSS file
    cat >> "$pve_css" << 'PROXISION_CSS'

/* ─── Proxision AI Chat Sidebar ─── DO NOT EDIT ─── */

/* ── Chat messages scroll area ── */
.pve-ai-chat-messages {
  padding: 10px 0 12px 0;
  min-height: 0;
}
.pve-ai-chat-messages::-webkit-scrollbar { width: 6px; }
.pve-ai-chat-messages::-webkit-scrollbar-track { background: transparent; }
.pve-ai-chat-messages::-webkit-scrollbar-thumb { background: rgba(128,128,128,0.25); border-radius: 3px; }
.pve-ai-chat-messages::-webkit-scrollbar-thumb:hover { background: rgba(128,128,128,0.45); }

/* ── Welcome screen ── */
.pve-ai-chat-welcome { text-align: center; padding: 30px 20px 20px 20px; }
.pve-ai-chat-welcome-inner h2 { font-size: 16px; font-weight: 600; color: inherit; margin: 10px 0 6px 0; }
.pve-ai-chat-welcome-inner p { font-size: 12px; opacity: 0.65; line-height: 1.6; margin: 0; }
.pve-ai-chat-welcome-icon { color: #3892d4; margin-bottom: 4px; opacity: 0.85; }

/* ── Input bar ── */
.pve-ai-chat-input-bar { border: none; }
.pve-ai-input-panel { border-top: 1px solid rgba(128,128,128,0.2) !important; }

/* ── Chat bubbles — shared ── */
.pve-ai-bubble-wrap { padding: 3px 14px 3px 12px; }
.pve-ai-bubble { padding: 8px 12px; border-radius: 4px; max-width: 92%; word-wrap: break-word; overflow-wrap: break-word; }
.pve-ai-bubble-header { font-size: 11px; margin-bottom: 4px; opacity: 0.65; display: flex; align-items: center; gap: 4px; }
.pve-ai-bubble-body { font-size: 13px; line-height: 1.5; }
.pve-ai-loading-body { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }

/* ── User bubble ── */
.pve-ai-bubble-user { background-color: #3892d4; color: #fff; margin-left: auto; border-radius: 4px 4px 0 4px; }
.pve-ai-bubble-user .pve-ai-bubble-header { opacity: 0.8; }

/* ── Assistant bubble ── */
.pve-ai-bubble-assistant { background: rgba(128,128,128,0.12); color: inherit; margin-right: auto; border-radius: 4px 4px 4px 0; }

/* ── Markdown inside bubbles ── */
.pve-ai-bubble-body pre.pve-ai-code { background: rgba(0,0,0,0.07); border-radius: 4px; padding: 8px 10px; margin: 6px 0; overflow-x: auto; white-space: pre; font-size: 12px; line-height: 1.4; }
.pve-ai-bubble-body code.pve-ai-icode { background: rgba(0,0,0,0.07); border-radius: 3px; padding: 1px 5px; font-size: 12px; }
.pve-ai-bubble-body pre.pve-ai-code code { background: none; padding: 0; font-size: inherit; }
.pve-ai-bubble-body strong { font-weight: 600; }
.pve-ai-bubble-body a.pve-ai-link { color: #3892d4; text-decoration: none; border-bottom: 1px dotted #3892d4; }
.pve-ai-bubble-body a.pve-ai-link:hover { color: #2a6fa8; border-bottom-style: solid; }

/* ── Shell preview — nested inside assistant bubble ── */
.pve-ai-shell-preview { background: #1e1e1e; border-radius: 4px; overflow: hidden; border: 1px solid rgba(128,128,128,0.2); margin-top: 4px; }
.pve-ai-shell-body {
  padding: 8px 10px;
  max-height: 200px;
  overflow-y: auto;
  overflow-x: hidden;
  font-family: 'Menlo','Consolas','DejaVu Sans Mono','Liberation Mono',monospace;
  font-size: 11.5px;
  line-height: 1.45;
}
.pve-ai-shell-body::-webkit-scrollbar { width: 5px; }
.pve-ai-shell-body::-webkit-scrollbar-track { background: #1e1e1e; }
.pve-ai-shell-body::-webkit-scrollbar-thumb { background: #444; border-radius: 3px; }
.pve-ai-shell-entry { margin-bottom: 6px; }
.pve-ai-shell-entry:last-child { margin-bottom: 0; }
.pve-ai-shell-prompt-line { white-space: pre-wrap; word-break: break-all; }
.pve-ai-shell-prompt { color: #3892d4; font-weight: 600; }
.pve-ai-shell-cmd { color: #e0e0e0; }
.pve-ai-shell-output { color: #a0a0a0; white-space: pre-wrap; word-break: break-all; padding-left: 0; margin-top: 1px; }
.pve-ai-shell-truncated { color: #666; font-style: italic; font-size: 10px; margin-top: 1px; }
.pve-ai-shell-exit-err { color: #e06060; font-size: 10.5px; margin-top: 2px; }
.pve-ai-shell-exit-err i { margin-right: 3px; }
.pve-ai-shell-cursor { color: #c0c0c0; font-size: 10px; line-height: 1; animation: pve-ai-blink 1s step-end infinite; }
@keyframes pve-ai-blink { 50% { opacity: 0; } }

/* ── Open Shell link ── */
.pve-ai-shell-open { margin-left: auto; color: #5ba0d0; font-size: 11px; cursor: pointer; padding: 2px 8px; border-radius: 3px; transition: background 0.15s; }
.pve-ai-shell-open:hover { background: rgba(91,160,208,0.15); color: #7dbde8; }
.pve-ai-shell-open i { margin-left: 3px; font-size: 10px; }

/* ── Send / Stop buttons ── */
.pve-ai-send-btn { border-radius: 3px; }
.pve-ai-stop-btn { background-color: #3892d4 !important; color: #fff !important; border-color: #3078b4 !important; border-radius: 3px; }
.pve-ai-stop-btn .x-btn-inner { color: #fff !important; }
.pve-ai-stop-btn .x-btn-icon-el { color: #fff !important; }

/* ── Chat history window ── */
.pve-ai-history-item { transition: background 0.12s; }
.pve-ai-history-item:hover { background: rgba(56,146,212,0.08); }
.pve-ai-history-scroll::-webkit-scrollbar { width: 6px; }
.pve-ai-history-scroll::-webkit-scrollbar-track { background: transparent; }
.pve-ai-history-scroll::-webkit-scrollbar-thumb { background: rgba(128,128,128,0.25); border-radius: 3px; }
.pve-ai-history-scroll::-webkit-scrollbar-thumb:hover { background: rgba(128,128,128,0.45); }

/* ─── End Proxision CSS ─── */
PROXISION_CSS

    log_success "CSS deployed"
}

# ── Step 6: Install AI proxy service ───────────────────────────────────
install_proxy() {
    echo -e "${BLUE}[6/7]${NC} Installing AI proxy..."

    mkdir -p "$INSTALL_DIR"
    get_file "pve-ai-proxy.py" "$INSTALL_DIR/pve-ai-proxy.py"
    chmod +x "$INSTALL_DIR/pve-ai-proxy.py"

    # Stop existing service if running
    systemctl stop "$SERVICE_NAME" 2>/dev/null || true

    # Write systemd unit
    cat > "/etc/systemd/system/${SERVICE_NAME}.service" << 'EOF'
[Unit]
Description=Proxision AI Proxy for Proxmox VE
After=network.target pvedaemon.service
Wants=pvedaemon.service

[Service]
Type=simple
ExecStart=/usr/bin/python3 /opt/proxision/pve-ai-proxy.py
Restart=always
RestartSec=5
User=root
WorkingDirectory=/opt/proxision

NoNewPrivileges=false
ProtectSystem=false
ProtectHome=false

[Install]
WantedBy=multi-user.target
EOF

    systemctl daemon-reload &>/dev/null
    systemctl enable "$SERVICE_NAME" &>/dev/null
    systemctl start "$SERVICE_NAME"

    sleep 2
    if systemctl is-active --quiet "$SERVICE_NAME"; then
        log_success "AI proxy service running"
    else
        log_error "AI proxy service failed to start"
        systemctl status "$SERVICE_NAME" --no-pager || true
        exit 1
    fi
}

# ── Step 7: Restart pveproxy ───────────────────────────────────────────
restart_pveproxy() {
    echo -e "${BLUE}[7/7]${NC} Restarting pveproxy..."

    echo ""
    echo -e "  ${YELLOW}>>> Hard-refresh your browser to see the new sidebar:${NC}"
    echo -e "      ${BLUE}Windows/Linux:${NC} Ctrl + Shift + R"
    echo -e "      ${BLUE}Mac:${NC}           Cmd + Shift + R"
    echo ""

    systemctl restart pveproxy

    sleep 3
    if systemctl is-active --quiet pveproxy; then
        log_success "pveproxy restarted"
    else
        log_error "Failed to restart pveproxy"
        systemctl status pveproxy --no-pager || true
        exit 1
    fi
}

# ── Version file ───────────────────────────────────────────────────────
write_version() {
    cat > "$INSTALL_DIR/version.json" << EOF
{
    "name": "Proxision",
    "version": "1.0.0",
    "installed": "$(date -Iseconds)",
    "mode": "$( [[ "$LOCAL_MODE" == true ]] && echo "local" || echo "remote" )"
}
EOF
}

# ── Diagnostics ───────────────────────────────────────────────────────
print_diagnostics() {
    echo ""
    echo -e "${BLUE}── Diagnostic Summary ──${NC}"
    echo -e "  Branch:       ${BRANCH}"
    echo -e "  Repo URL:     ${REPO_URL}"
    echo -e "  Mode:         $( [[ "$LOCAL_MODE" == true ]] && echo 'local' || echo 'remote')"
    echo -e "  JS classes:   $(grep -c 'pveAIChatPanel' "$PVE_JS" 2>/dev/null || echo 0) references to pveAIChatPanel"
    echo -e "  CSS marker:   $(grep -c 'Proxision AI Chat' "$PVE_CSS_DIR/ext6-pve.css" 2>/dev/null || echo 0) Proxision CSS block(s)"
    echo -e "  Proxy svc:    $(systemctl is-active $SERVICE_NAME 2>/dev/null || echo 'unknown')"
    echo -e "  pveproxy:     $(systemctl is-active pveproxy 2>/dev/null || echo 'unknown')"
    echo ""
}

# ── Success banner ─────────────────────────────────────────────────────
print_success() {
    local HOST_IP
    HOST_IP=$(hostname -I 2>/dev/null | awk '{print $1}')
    [[ -z "$HOST_IP" ]] && HOST_IP="your-server-ip"

    echo ""
    echo -e "${GREEN}╔════════════════════════════════════════════════════════════╗${NC}"
    echo -e "${GREEN}║           Proxision installed successfully!                ║${NC}"
    echo -e "${GREEN}╚════════════════════════════════════════════════════════════╝${NC}"
    echo ""
    echo -e "  ${BLUE}1.${NC} Open Proxmox UI: https://${HOST_IP}:8006"
    echo -e "  ${BLUE}2.${NC} ${YELLOW}Hard-refresh your browser${NC} (Ctrl+Shift+R or Cmd+Shift+R)"
    echo -e "  ${BLUE}3.${NC} Find ${GREEN}Proxision${NC} in the right sidebar (collapsible)"
    echo -e "  ${BLUE}4.${NC} Click ${GREEN}Set Model${NC} to configure your AI provider API key"
    echo ""
    echo -e "  ${YELLOW}Note:${NC} Accept the self-signed cert at https://${HOST_IP}:5555"
    echo -e "        if you see connection errors in the chat."
    echo ""
    echo -e "  ${BLUE}To uninstall:${NC}"
    echo -e "  curl -sSL ${REPO_URL}/uninstall.sh | PROXISION_FORCE_UNINSTALL=1 bash"
    echo ""
}

# ── Main ───────────────────────────────────────────────────────────────
main() {
    echo ""
    echo -e "${BLUE}╔════════════════════════════════════════════════════════════╗${NC}"
    echo -e "${BLUE}║              Proxision Installer v1.0.0                    ║${NC}"
    echo -e "${BLUE}║          AI Assistant for Proxmox VE                       ║${NC}"
    echo -e "${BLUE}╚════════════════════════════════════════════════════════════╝${NC}"
    echo ""

    if [[ "$LOCAL_MODE" == true ]]; then
        log_info "Local mode: using files from $SCRIPT_DIR"
    else
        log_info "Remote mode: downloading from ${REPO_URL}"
    fi

    preflight
    backup_files
    restore_clean
    append_js
    patch_workspace
    deploy_css
    install_proxy
    write_version
    restart_pveproxy
    print_diagnostics
    print_success
}

main "$@"
