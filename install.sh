#!/bin/bash
#
# Proxision Installer
# Installs the Proxision AI assistant into Proxmox VE
#
# Usage: curl -sSL https://raw.githubusercontent.com/ProjectProxision/proxision/main/install.sh | bash
#

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Configuration
REPO_URL="${PROXISION_REPO:-https://raw.githubusercontent.com/ProjectProxision/proxision/main}"
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

# Check if this is a Proxmox server
check_proxmox() {
    if ! command -v pvesh &> /dev/null; then
        log_error "This doesn't appear to be a Proxmox VE server (pvesh not found)"
        exit 1
    fi
    
    if [[ ! -d "$PVE_WWW_DIR" ]]; then
        log_error "Proxmox web directory not found: $PVE_WWW_DIR"
        exit 1
    fi
    
    log_info "Detected Proxmox VE installation"
}

# Check dependencies
check_dependencies() {
    log_info "Checking dependencies..."
    
    local missing_deps=()
    
    if ! command -v python3 &> /dev/null; then
        missing_deps+=("python3")
    fi
    
    if ! command -v curl &> /dev/null && ! command -v wget &> /dev/null; then
        missing_deps+=("curl or wget")
    fi
    
    if [[ ${#missing_deps[@]} -gt 0 ]]; then
        log_error "Missing dependencies: ${missing_deps[*]}"
        log_info "Installing missing dependencies..."
        apt-get update && apt-get install -y python3 curl
    fi
    
    log_success "All dependencies satisfied"
}

# Download a file from the repo
download_file() {
    local remote_path="$1"
    local local_path="$2"
    
    local url="${REPO_URL}/${remote_path}"
    
    if command -v curl &> /dev/null; then
        curl -sSL "$url" -o "$local_path"
    elif command -v wget &> /dev/null; then
        wget -q "$url" -O "$local_path"
    else
        log_error "Neither curl nor wget available"
        exit 1
    fi
}

# Create backup of original files
backup_original_files() {
    log_info "Backing up original Proxmox files..."
    
    mkdir -p "$BACKUP_DIR"
    
    # Backup the main JS file we'll patch
    local pve_js="$PVE_WWW_DIR/js/pvemanagerlib.js"
    if [[ -f "$pve_js" && ! -f "$BACKUP_DIR/pvemanagerlib.js.original" ]]; then
        cp "$pve_js" "$BACKUP_DIR/pvemanagerlib.js.original"
        log_info "Backed up pvemanagerlib.js"
    fi
    
    log_success "Backup complete"
}

# Install the AI proxy service
install_proxy_service() {
    log_info "Installing Proxision AI proxy service..."
    
    mkdir -p "$INSTALL_DIR"
    
    # Download the proxy script
    download_file "pve-ai-proxy.py" "$INSTALL_DIR/pve-ai-proxy.py"
    chmod +x "$INSTALL_DIR/pve-ai-proxy.py"
    
    # Create systemd service file
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

# Security hardening
NoNewPrivileges=false
ProtectSystem=false
ProtectHome=false

[Install]
WantedBy=multi-user.target
EOF
    
    # Reload systemd and enable service
    systemctl daemon-reload
    systemctl enable "$SERVICE_NAME"
    systemctl start "$SERVICE_NAME"
    
    # Verify service is running
    sleep 2
    if systemctl is-active --quiet "$SERVICE_NAME"; then
        log_success "AI proxy service installed and running"
    else
        log_error "AI proxy service failed to start"
        systemctl status "$SERVICE_NAME" --no-pager
        exit 1
    fi
}

# Install frontend components
install_frontend() {
    log_info "Installing Proxision frontend components..."
    
    local js_dir="$PVE_WWW_DIR/js"
    local manager6_dir="$PVE_WWW_DIR/js/manager6"
    
    # Create directories if they don't exist
    mkdir -p "$manager6_dir/panel"
    mkdir -p "$manager6_dir/window"
    
    # Download frontend files
    download_file "AIChatPanel.js" "$manager6_dir/panel/AIChatPanel.js"
    download_file "AIModelSettings.js" "$manager6_dir/window/AIModelSettings.js"
    
    log_success "Frontend components installed"
}

# Patch the Proxmox manager to include our components
patch_proxmox_manager() {
    log_info "Patching Proxmox manager to include Proxision..."
    
    local pve_js="$PVE_WWW_DIR/js/pvemanagerlib.js"
    
    # Check if already patched
    if grep -q "AIChatPanel" "$pve_js" 2>/dev/null; then
        log_warn "Proxmox manager already patched, skipping"
        return 0
    fi
    
    # Create a JavaScript file that loads our components
    cat > "$PVE_WWW_DIR/js/proxision-loader.js" << 'EOF'
// Proxision Loader - Adds AI Chat Panel to Proxmox VE
(function() {
    'use strict';
    
    // Wait for ExtJS to be ready
    Ext.onReady(function() {
        // Load our component files dynamically
        var basePath = '/pve2/js/manager6/';
        var scripts = [
            basePath + 'panel/AIChatPanel.js',
            basePath + 'window/AIModelSettings.js'
        ];
        
        var loaded = 0;
        var total = scripts.length;
        
        function loadScript(src, callback) {
            var script = document.createElement('script');
            script.type = 'text/javascript';
            script.src = src;
            script.onload = callback;
            script.onerror = function() {
                console.error('Proxision: Failed to load ' + src);
                callback();
            };
            document.head.appendChild(script);
        }
        
        function onScriptLoaded() {
            loaded++;
            if (loaded >= total) {
                initProxision();
            }
        }
        
        function initProxision() {
            // Wait a bit for PVE to finish initializing
            setTimeout(function() {
                try {
                    addAIChatPanel();
                } catch (e) {
                    console.error('Proxision: Failed to initialize', e);
                }
            }, 1000);
        }
        
        function addAIChatPanel() {
            // Find the main viewport
            var viewport = Ext.ComponentQuery.query('viewport')[0];
            if (!viewport) {
                console.warn('Proxision: Viewport not found, retrying...');
                setTimeout(addAIChatPanel, 1000);
                return;
            }
            
            // Find the navigation panel (west region)
            var westPanel = viewport.down('panel[region=west]');
            if (!westPanel) {
                console.warn('Proxision: West panel not found');
                return;
            }
            
            // Check if AIChatPanel class exists
            if (!Ext.ClassManager.get('PVE.panel.AIChatPanel')) {
                console.warn('Proxision: AIChatPanel class not loaded yet, retrying...');
                setTimeout(addAIChatPanel, 500);
                return;
            }
            
            // Create and add the AI chat panel
            var aiPanel = Ext.create('PVE.panel.AIChatPanel', {
                region: 'south',
                height: 400,
                collapsible: true,
                collapsed: true,
                split: true,
                title: 'Proxision',
                iconCls: 'fa fa-comments'
            });
            
            // Add to the west panel
            westPanel.add(aiPanel);
            
            console.log('Proxision: AI Chat Panel added successfully');
        }
        
        // Start loading scripts
        scripts.forEach(function(src) {
            loadScript(src, onScriptLoaded);
        });
    });
})();
EOF
    
    # Inject loader into pvemanagerlib.js (append at end)
    echo "" >> "$pve_js"
    echo "// Proxision Integration - DO NOT REMOVE" >> "$pve_js"
    echo "Ext.Loader.loadScript({url: '/pve2/js/proxision-loader.js'});" >> "$pve_js"
    
    log_success "Proxmox manager patched"
}

# Add CSS styles for the chat panel
install_styles() {
    log_info "Installing Proxision styles..."
    
    cat > "$PVE_WWW_DIR/css/proxision.css" << 'EOF'
/* Proxision AI Chat Panel Styles */
.pve-ai-chat-messages {
    background: #1a1a2e;
}

.pve-ai-bubble-wrap {
    padding: 8px 12px;
}

.pve-ai-bubble {
    border-radius: 12px;
    padding: 10px 14px;
    max-width: 90%;
}

.pve-ai-bubble-user {
    background: #3a7bc8;
    color: #fff;
    margin-left: auto;
}

.pve-ai-bubble-assistant {
    background: #2d2d44;
    color: #e0e0e0;
}

.pve-ai-bubble-header {
    font-size: 11px;
    opacity: 0.7;
    margin-bottom: 4px;
}

.pve-ai-bubble-body {
    font-size: 13px;
    line-height: 1.5;
}

.pve-ai-code {
    background: #1e1e2e;
    border-radius: 6px;
    padding: 8px 10px;
    font-family: monospace;
    font-size: 12px;
    overflow-x: auto;
    margin: 8px 0;
}

.pve-ai-icode {
    background: rgba(0,0,0,0.3);
    padding: 2px 5px;
    border-radius: 3px;
    font-family: monospace;
    font-size: 12px;
}

.pve-ai-chat-input-bar {
    background: #16213e !important;
    border-top: 1px solid #333;
}

.pve-ai-chat-input textarea {
    background: #1a1a2e;
    color: #fff;
    border: 1px solid #444;
    border-radius: 8px;
}

.pve-ai-send-btn {
    background: #4a90d9 !important;
    border-color: #4a90d9 !important;
}

.pve-ai-stop-btn {
    background: #d9534f !important;
    border-color: #d9534f !important;
}

.pve-ai-loading-body {
    color: #888;
    font-style: italic;
}

.pve-ai-chat-welcome {
    text-align: center;
    color: #888;
}

.pve-ai-chat-welcome h2 {
    color: #4a90d9;
    margin-bottom: 8px;
}

.pve-ai-chat-welcome-icon {
    color: #4a90d9;
    margin-bottom: 12px;
}

.pve-ai-shell-preview {
    background: #0d1117;
    border-radius: 8px;
    overflow: hidden;
    font-family: monospace;
    font-size: 12px;
    margin: 8px 0;
}

.pve-ai-shell-header {
    background: #21262d;
    padding: 6px 10px;
    display: flex;
    justify-content: space-between;
    align-items: center;
}

.pve-ai-shell-title {
    color: #8b949e;
}

.pve-ai-shell-open {
    color: #58a6ff;
    cursor: pointer;
    font-size: 11px;
}

.pve-ai-shell-open:hover {
    text-decoration: underline;
}

.pve-ai-shell-body {
    padding: 10px;
    max-height: 200px;
    overflow-y: auto;
    color: #c9d1d9;
}

.pve-ai-shell-prompt {
    color: #7ee787;
}

.pve-ai-shell-cmd {
    color: #fff;
}

.pve-ai-shell-output {
    color: #8b949e;
    white-space: pre-wrap;
    margin: 4px 0;
}

.pve-ai-shell-exit-err {
    color: #f85149;
    margin-top: 4px;
}

.pve-ai-shell-cursor {
    animation: blink 1s infinite;
}

@keyframes blink {
    0%, 50% { opacity: 1; }
    51%, 100% { opacity: 0; }
}

.pve-ai-link {
    color: #58a6ff;
    text-decoration: none;
}

.pve-ai-link:hover {
    text-decoration: underline;
}
EOF
    
    # Inject CSS into the main HTML
    local index_html="$PVE_WWW_DIR/index.html.tpl"
    if [[ -f "$index_html" ]] && ! grep -q "proxision.css" "$index_html"; then
        # Add CSS link before </head>
        sed -i 's|</head>|<link rel="stylesheet" type="text/css" href="/pve2/css/proxision.css" />\n</head>|' "$index_html"
        log_info "Added CSS to index template"
    fi
    
    log_success "Styles installed"
}

# Restart Proxmox web service to apply changes
restart_pve_proxy() {
    log_info "Restarting Proxmox web proxy..."
    
    systemctl restart pveproxy
    
    sleep 2
    if systemctl is-active --quiet pveproxy; then
        log_success "Proxmox web proxy restarted"
    else
        log_error "Failed to restart pveproxy"
        exit 1
    fi
}

# Create version file
create_version_file() {
    local version="1.0.0"
    local install_date=$(date -Iseconds)
    
    cat > "$INSTALL_DIR/version.json" << EOF
{
    "name": "Proxision",
    "version": "$version",
    "installed": "$install_date",
    "repo": "$REPO_URL"
}
EOF
}

# Print success message and next steps
print_success() {
    echo ""
    echo -e "${GREEN}╔════════════════════════════════════════════════════════════╗${NC}"
    echo -e "${GREEN}║           Proxision installed successfully!                ║${NC}"
    echo -e "${GREEN}╚════════════════════════════════════════════════════════════╝${NC}"
    echo ""
    echo -e "${BLUE}Next steps:${NC}"
    echo "  1. Open the Proxmox web UI: https://$(hostname):8006"
    echo "  2. Look for 'Proxision' in the left navigation panel"
    echo "  3. Click 'Set Model' to configure your AI provider API key"
    echo ""
    echo -e "${BLUE}Supported AI providers:${NC}"
    echo "  • OpenAI (GPT-5.2)"
    echo "  • Google (Gemini 3 Flash)"
    echo "  • xAI (Grok 4.1)"
    echo ""
    echo -e "${YELLOW}Note:${NC} Accept the self-signed certificate at https://$(hostname):5555"
    echo "      if you see connection errors in the chat."
    echo ""
    echo -e "${BLUE}To uninstall:${NC}"
    echo "  curl -sSL ${REPO_URL}/uninstall.sh | bash"
    echo ""
}

# Main installation flow
main() {
    echo ""
    echo -e "${BLUE}╔════════════════════════════════════════════════════════════╗${NC}"
    echo -e "${BLUE}║              Proxision Installer v1.0.0                    ║${NC}"
    echo -e "${BLUE}║          AI Assistant for Proxmox VE                       ║${NC}"
    echo -e "${BLUE}╚════════════════════════════════════════════════════════════╝${NC}"
    echo ""
    
    check_root
    check_proxmox
    check_dependencies
    backup_original_files
    install_proxy_service
    install_frontend
    install_styles
    patch_proxmox_manager
    create_version_file
    restart_pve_proxy
    print_success
}

# Run main
main "$@"
