#!/usr/bin/env python3
"""Proxmox AI Chat Proxy with PVE integration."""

import json
import re
import ssl
import subprocess
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from http.server import HTTPServer, BaseHTTPRequestHandler
from socketserver import ThreadingMixIn

CERT_FILE = '/etc/pve/local/pve-ssl.pem'
KEY_FILE = '/etc/pve/local/pve-ssl.key'
PORT = 5555

# URL patterns for constructing ISO download URLs for various distros.
# These patterns allow the AI to generate URLs for newer versions.
# Placeholders: {major}, {minor}, {patch}, {version}, {build}
ISO_URL_PATTERNS = {
    'ubuntu-server': {
        'pattern': 'https://releases.ubuntu.com/{version}/ubuntu-{version}-live-server-amd64.iso',
        'filename_pattern': 'ubuntu-{version}-live-server-amd64.iso',
        'example_version': '24.04.3',
        'description': 'Ubuntu LTS Server (replace {version} with e.g. 24.04.3, 26.04)',
    },
    'ubuntu-desktop': {
        'pattern': 'https://releases.ubuntu.com/{version}/ubuntu-{version}-desktop-amd64.iso',
        'filename_pattern': 'ubuntu-{version}-desktop-amd64.iso',
        'example_version': '24.04.3',
        'description': 'Ubuntu LTS Desktop',
    },
    'debian': {
        'pattern': 'https://cdimage.debian.org/debian-cd/current/amd64/iso-cd/debian-{version}-amd64-netinst.iso',
        'filename_pattern': 'debian-{version}-amd64-netinst.iso',
        'example_version': '12.9.0',
        'description': 'Debian stable netinst (current always points to latest)',
    },
    'debian-archive': {
        'pattern': 'https://cdimage.debian.org/cdimage/archive/{version}/amd64/iso-cd/debian-{version}-amd64-netinst.iso',
        'filename_pattern': 'debian-{version}-amd64-netinst.iso',
        'example_version': '11.11.0',
        'description': 'Debian archived releases (for older versions)',
    },
    'fedora-server': {
        'pattern': 'https://download.fedoraproject.org/pub/fedora/linux/releases/{major}/Server/x86_64/iso/Fedora-Server-dvd-x86_64-{major}-{build}.iso',
        'filename_pattern': 'Fedora-Server-dvd-x86_64-{major}-{build}.iso',
        'example_version': '41-1.4',
        'description': 'Fedora Server (major=41,42..., build=1.4)',
    },
    'fedora-workstation': {
        'pattern': 'https://download.fedoraproject.org/pub/fedora/linux/releases/{major}/Workstation/x86_64/iso/Fedora-Workstation-Live-x86_64-{major}-{build}.iso',
        'filename_pattern': 'Fedora-Workstation-Live-x86_64-{major}-{build}.iso',
        'example_version': '41-1.4',
        'description': 'Fedora Workstation Desktop',
    },
    'rocky': {
        'pattern': 'https://download.rockylinux.org/pub/rocky/{major}/isos/x86_64/Rocky-{major}-latest-x86_64-minimal.iso',
        'filename_pattern': 'Rocky-{major}-latest-x86_64-minimal.iso',
        'example_version': '9',
        'description': 'Rocky Linux minimal (uses "latest" symlink, stays current)',
    },
    'almalinux': {
        'pattern': 'https://repo.almalinux.org/almalinux/{major}/isos/x86_64/AlmaLinux-{major}-latest-x86_64-minimal.iso',
        'filename_pattern': 'AlmaLinux-{major}-latest-x86_64-minimal.iso',
        'example_version': '9',
        'description': 'AlmaLinux minimal (uses "latest" symlink, stays current)',
    },
    'opensuse-leap': {
        'pattern': 'https://download.opensuse.org/distribution/leap/{version}/iso/openSUSE-Leap-{version}-NET-x86_64-Media.iso',
        'filename_pattern': 'openSUSE-Leap-{version}-NET-x86_64-Media.iso',
        'example_version': '15.6',
        'description': 'openSUSE Leap netinstall',
    },
    'linuxmint': {
        'pattern': 'https://mirrors.kernel.org/linuxmint/stable/{major}/linuxmint-{major}-cinnamon-64bit.iso',
        'filename_pattern': 'linuxmint-{major}-cinnamon-64bit.iso',
        'example_version': '22',
        'description': 'Linux Mint Cinnamon Edition',
    },
}

# Pre-verified ISO entries with static URLs.
# Use these as known-good fallbacks; they use "latest" symlinks where available.
ISO_URLS = {
    # Rolling releases and stable URLs (always current)
    'arch': {
        'url': 'https://geo.mirror.pkgbuild.com/iso/latest/archlinux-x86_64.iso',
        'filename': 'archlinux-x86_64.iso',
        'description': 'Arch Linux (latest rolling release - always current)',
    },
    'opensuse-tumbleweed': {
        'url': 'https://download.opensuse.org/tumbleweed/iso/openSUSE-Tumbleweed-NET-x86_64-Current.iso',
        'filename': 'openSUSE-Tumbleweed-NET-x86_64-Current.iso',
        'description': 'openSUSE Tumbleweed (rolling release - always current)',
    },
    'centos-stream-9': {
        'url': 'https://mirror.stream.centos.org/9-stream/BaseOS/x86_64/iso/CentOS-Stream-9-latest-x86_64-boot.iso',
        'filename': 'CentOS-Stream-9-latest-x86_64-boot.iso',
        'description': 'CentOS Stream 9 Boot/Netinstall (uses "latest" symlink)',
    },
    'rocky-9': {
        'url': 'https://download.rockylinux.org/pub/rocky/9/isos/x86_64/Rocky-9-latest-x86_64-minimal.iso',
        'filename': 'Rocky-9-latest-x86_64-minimal.iso',
        'description': 'Rocky Linux 9 Minimal (uses "latest" symlink)',
    },
    'almalinux-9': {
        'url': 'https://repo.almalinux.org/almalinux/9/isos/x86_64/AlmaLinux-9-latest-x86_64-minimal.iso',
        'filename': 'AlmaLinux-9-latest-x86_64-minimal.iso',
        'description': 'AlmaLinux 9 Minimal (uses "latest" symlink)',
    },
    'virtio-win': {
        'url': 'https://fedorapeople.org/groups/virt/virtio-win/direct-downloads/stable-virtio/virtio-win.iso',
        'filename': 'virtio-win.iso',
        'description': 'VirtIO drivers for Windows VMs (always latest stable)',
    },
}

SYSTEM_PROMPT = """You are an AI assistant built into Proxmox Virtual Environment. You manage VMs, containers, and infrastructure on this server. You can create containers, run commands inside them, and fully set up services end-to-end.

## Current Server State
{context}

## Available Actions
Perform actions by including a tool call in your response using this EXACT format:
<tool_call>{{"action": "name", "params": {{}}}}</tool_call>

### Container Actions
- create_container: params: hostname, cores, memory (MB), disk_size (GB), storage, template (full volid e.g. "local:vztmpl/debian-12-standard_12.7-1_amd64.tar.zst"), net_bridge (default vmbr0), password, privileged (true for Docker-in-LXC/NFS — default false), features (e.g. "nesting=1" for Docker-in-LXC), nameserver (DNS IP), ip (CIDR like "192.168.1.50/24" or "dhcp" — default "dhcp"), gw (gateway IP, required if static ip)
- start_container / stop_container: params: vmid
- suspend_container / resume_container: params: vmid — suspend freezes execution in place; resume unfreezes it
- delete_container: params: vmid (must be stopped first)
- exec_container: params: vmid, command — run a bash command inside a RUNNING container as root. Returns stdout, stderr, exit_code. Max 300s timeout.

### Container Status & Config
- get_container_status: params: vmid — detailed status including CPU, memory, uptime, disk usage
- get_container_config: params: vmid — full current configuration (cores, memory, network, mounts, etc.)
- set_container_config: params: vmid, plus any config keys to change: cores, memory, swap, hostname, nameserver, searchdomain, net0-net3, onboot, description, tags, features, cpulimit, cpuunits, protection, tty, startup, mp0-mp3, etc. Use "delete" param (comma-separated key names) to remove config keys. Container may need restart for some changes to apply.

### Container Disk
- resize_container_disk: params: vmid, disk (e.g. "rootfs", "mp0"), size (e.g. "+5G" for relative or "20G" for absolute)

### Container Snapshots
- snapshot_container: params: vmid, snapname, description (optional) — create a named snapshot
- list_container_snapshots: params: vmid — list all snapshots for a container
- rollback_container_snapshot: params: vmid, snapname — rollback to a snapshot (container must be stopped)
- delete_container_snapshot: params: vmid, snapname — delete a snapshot

### Container Clone & Migrate
- clone_container: params: vmid, newid (target VMID), hostname (optional), full (1 for full clone, 0 for linked — default 1), storage (optional target storage), description (optional). Returns new vmid.
- migrate_container: params: vmid, target (target node name) — migrate container to another cluster node. Container should be stopped for offline migration.

### Template Actions
- list_available_templates: no params — shows all downloadable CT templates
- download_template: params: template (e.g. "debian-12-standard_12.7-1_amd64.tar.zst"), storage (default: auto-pick)

### ISO Actions
- download_iso: params: url (direct link to ISO), filename (e.g. "ubuntu-24.04.3-live-server-amd64.iso"), storage (optional — auto-picks ISO-capable storage). Downloads an ISO from a URL to PVE storage. Can take several minutes for large files.

**Always-Current ISOs** (use "latest" symlinks - no version updates needed):
{iso_urls}

**URL Patterns for Other Distros** (construct URLs for any version):
{iso_patterns}

**ISO Download Strategy**:
1. First, check if the desired OS is already available in the server's existing ISOs (listed in context above).
2. For rolling/stable releases (Arch, Tumbleweed, Rocky, Alma, CentOS Stream, VirtIO), use the "Always-Current ISOs" table - these always download the latest version.
3. For versioned releases (Ubuntu, Debian, Fedora, openSUSE Leap, Linux Mint), use the URL patterns to construct the download URL. Replace placeholders like {{version}}, {{major}}, {{build}} with the requested version numbers.
4. If the distro is NOT in any table and you cannot construct a URL, ASK THE USER to provide a direct download URL. Say: "I don't have a download pattern for [distro]. Could you provide a direct download URL from the official website?"
5. For Windows ISOs: Microsoft licensing prevents auto-download. Ask the user to upload manually via Proxmox web UI (Storage > ISO Images > Upload). Recommend also downloading virtio-win for drivers.

### VM Actions
- create_vm: params: name, cores, memory (MB), disk_size (GB), storage, ostype (l26/win11/win10/win7/wxp), iso (full volid e.g. "local:iso/ubuntu-24.04-server.iso"), net_bridge (default vmbr0), bios (seabios or ovmf — auto-selected per ostype: ovmf for Windows, seabios for Linux), machine (q35 or i440fx — default q35), cpu (default x86-64-v2-AES). Notes: OVMF + EFI disk are auto-configured for Windows; TPM v2.0 is auto-added for win11.
- start_vm / stop_vm: params: vmid
- delete_vm: params: vmid (must be stopped first)

### Host Actions
- exec_host: params: command — run a bash command directly on the Proxmox HOST as root. Use this for host-level tasks: checking host services, editing host config files, managing networking, installing host packages, checking logs, firewall rules, etc. Same best practices as exec_container. Max 300s timeout. NO interactive commands.

### Notes / Documentation
- save_notes: params: vmid, notes (text), kind ("ct" or "vm") — append important notes (access info, credentials, setup details) to the VM/CT description field visible in the Proxmox UI. ALWAYS use this after finishing a setup to save access details.

### Info
- list_vms / list_containers / get_resources: no params

## Resource Sizing — USE THE MINIMUM NEEDED
- Tunnel/agent (Cloudflare, Tailscale, WireGuard): 1 core, 128MB RAM, 1-2GB disk
- DNS/ad-block (Pi-hole, AdGuard Home): 1 core, 256MB RAM, 2GB disk
- Small bot/script: 1 core, 128-256MB RAM, 2GB disk
- Reverse proxy (Nginx Proxy Manager, Traefik, Caddy): 1 core, 256MB RAM, 4GB disk
- Light app (Node.js, Flask, wiki): 1-2 cores, 512MB RAM, 4-8GB disk
- App + database (WordPress, Nextcloud): 2 cores, 1-2GB RAM, 10-20GB disk
- Database (PostgreSQL, MySQL, Redis): 2-4 cores, 2-4GB RAM, 20-40GB disk
- Game server (Minecraft, Valheim): 2-4 cores, 4-8GB RAM, 10-20GB disk
- CI/CD (Jenkins, Gitea+runners): 4 cores, 4GB RAM, 30-50GB disk
- Windows / Docker host / GUI desktop: USE VM (not container)

ALWAYS use containers unless user explicitly asks for VM or needs Windows/Docker/GUI desktop.

## Container Setup — Step-by-Step
1. **Templates**: Check available templates in context. If empty, use list_available_templates then download_template (prefer debian-12 or ubuntu-24.04).
2. **Create**: create_container with appropriate sizing.
3. **Start**: start_container.
4. **Wait for network**: First exec should be: "sleep 3 && cat /etc/resolv.conf && hostname -I" — verify DNS and IP are ready.
5. **Install & configure**: Run setup commands via exec_container.
6. **Get IP**: exec_container with "hostname -I".
7. **Report**: Give user the IP, port, credentials, and connection instructions.

### exec_container Best Practices
- ALWAYS use DEBIAN_FRONTEND=noninteractive for apt
- Chain commands: "apt-get update && apt-get install -y pkg1 pkg2"
- Config files via heredoc: exec with command 'cat > /path/file << "EOF"\nline1\nline2\nEOF'
- Enable services: "systemctl enable --now servicename"
- NO interactive commands (no vim, nano, or prompts that wait for input)
- If a command fails, read stderr carefully and fix in the next round
- For large installs, chain everything into one command to minimize rounds

### When to Use Privileged Containers
- Docker/Podman inside LXC: privileged=true, features="nesting=1"
- NFS server: privileged=true
- Most other workloads: unprivileged (default) is fine

## VM Setup — Step-by-Step
1. **ISOs**: Check available ISOs in context. If none match the desired OS, use download_iso with a URL from the known ISO table above. For Windows ISOs, ask the user to upload manually via the Proxmox web UI (Storage > ISO Images > Upload).
2. **Create**: create_vm with appropriate sizing and correct ostype (l26 for Linux, win11/win10/win7 for Windows).
3. **Start**: start_vm.
4. **Inform user**: VMs require manual OS installation via the Proxmox VNC console (Hardware > Console). Unlike containers, you CANNOT exec commands inside VMs.
5. **Windows tip**: For Windows VMs, recommend the user also attach the VirtIO drivers ISO as a second CD-ROM for disk/network detection during install.
6. **Report**: Give the user the VM ID, allocated resources (cores, RAM, disk), and instructions to open the VNC console to complete OS installation.

## Multi-Round Execution
Include multiple <tool_call> tags in one response — they execute in order. After execution you'll see results and can continue with more calls. When completely done, respond with NO tool_call tags — just the final summary.

## Rules
1. CHECK server resources before creating. Never exceed 80% of free RAM or storage.
2. ALWAYS prefer containers over VMs.
3. ASK follow-up questions if the user's request is ambiguous.
4. BEFORE creating, present a summary (type, name, CPU, RAM, disk, OS, what will be installed) and ask "Shall I proceed?"
5. Only include <tool_call> tags AFTER user confirms.
6. Use next available VMID: {next_vmid}
7. Pick the storage pool with the most free space for the content type.
8. If an action fails, read the error and try to fix it. Don't give up immediately.
9. Be concise.
10. At the end, ALWAYS provide: IP address, port, credentials, and how to connect/use the service.
11. If no templates are available, download one before creating a container.
12. If a container creation fails, use delete_container to clean up, then retry.
13. After completing ANY container/VM setup, ALWAYS use save_notes to store access details (IP, port, credentials, service URL, how to connect) in the VM/CT notes. This ensures the user can always find the info in the Proxmox UI later.
14. For host-level tasks (checking logs, editing host configs, managing host services, networking), use exec_host instead of telling the user to SSH in manually.

## Limitations
- You CANNOT run commands inside VMs (only containers via exec_container and only on the host via exec_host).
- You CANNOT modify existing VM configs (resize, add disks, change network) — only container configs can be modified.
- Some container config changes (e.g. memory, cores) may require a restart to take effect. Inform the user when this applies.
- Do NOT claim failure unless the tool result explicitly says success=false."""


class PVEHelper:
    """Proxmox VE operations via pvesh CLI."""

    def __init__(self):
        self._node = None
        self._ctx_cache = None
        self._ctx_time = 0

    @property
    def node(self):
        if not self._node:
            nodes = self.pvesh('get', '/nodes')
            self._node = nodes[0]['node'] if nodes else 'localhost'
        return self._node

    def pvesh(self, method, path, params=None, timeout=30):
        cmd = ['pvesh', method, path, '--output-format', 'json']
        if params:
            for k, v in params.items():
                if v is not None:
                    cmd.extend(['-' + str(k), str(v)])
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        if result.returncode != 0:
            raise Exception(result.stderr.strip() or 'pvesh command failed')
        out = result.stdout.strip()
        if not out:
            return None
        try:
            return json.loads(out)
        except json.JSONDecodeError:
            return out

    def get_context(self):
        """Fetch current server state for AI system prompt (parallel, cached 10s)."""
        now = time.time()
        if self._ctx_cache and now - self._ctx_time < 10:
            return self._ctx_cache

        n = self.node
        ctx = {'node': n}

        # Phase 1: run all independent queries in parallel
        def fetch_status():
            return self.pvesh('get', '/nodes/' + n + '/status')

        def fetch_storage():
            return self.pvesh('get', '/nodes/' + n + '/storage') or []

        def fetch_vms():
            return self.pvesh('get', '/nodes/' + n + '/qemu') or []

        def fetch_cts():
            return self.pvesh('get', '/nodes/' + n + '/lxc') or []

        def fetch_nextid():
            return self.pvesh('get', '/cluster/nextid')

        results = {}
        with ThreadPoolExecutor(max_workers=5) as pool:
            futures = {
                pool.submit(fetch_status): 'status',
                pool.submit(fetch_storage): 'storage',
                pool.submit(fetch_vms): 'vms',
                pool.submit(fetch_cts): 'cts',
                pool.submit(fetch_nextid): 'nextid',
            }
            for f in as_completed(futures):
                key = futures[f]
                try:
                    results[key] = f.result()
                except Exception:
                    results[key] = None

        # Process status
        st = results.get('status')
        if isinstance(st, dict):
            cpu = st.get('cpuinfo', {})
            mem = st.get('memory', {})
            ctx['cpu'] = {
                'total_cores': cpu.get('cpus', 0),
                'sockets': cpu.get('sockets', 1),
                'model': cpu.get('model', ''),
                'usage_pct': round(st.get('cpu', 0) * 100, 1),
            }
            ctx['memory'] = {
                'total_gb': round(mem.get('total', 0) / 1073741824, 1),
                'used_gb': round(mem.get('used', 0) / 1073741824, 1),
                'free_gb': round((mem.get('total', 0) - mem.get('used', 0)) / 1073741824, 1),
            }

        # Process storage
        stor = results.get('storage') or []
        ctx['storage'] = [
            {
                'name': s['storage'],
                'type': s.get('type'),
                'total_gb': round(s.get('total', 0) / 1073741824, 1),
                'free_gb': round(s.get('avail', 0) / 1073741824, 1),
                'content': s.get('content', ''),
            }
            for s in stor if s.get('active')
        ]

        # Process VMs
        vms = results.get('vms') or []
        ctx['vms'] = [
            {
                'vmid': v['vmid'],
                'name': v.get('name', ''),
                'status': v.get('status'),
                'cores': v.get('cpus', 0),
                'mem_mb': round(v.get('maxmem', 0) / 1048576),
            }
            for v in vms
        ]

        # Process containers
        cts = results.get('cts') or []
        ctx['containers'] = [
            {
                'vmid': c['vmid'],
                'name': c.get('name', ''),
                'status': c.get('status'),
                'cores': c.get('cpus', 0),
                'mem_mb': round(c.get('maxmem', 0) / 1048576),
            }
            for c in cts
        ]

        ctx['next_vmid'] = results.get('nextid') or 100

        # Phase 2: fetch ISOs and templates in parallel
        ctx['isos'] = []
        ctx['templates'] = []
        content_futures = []
        with ThreadPoolExecutor(max_workers=4) as pool:
            for s in ctx['storage']:
                sname = s['name']
                content = s.get('content', '')
                if 'iso' in content:
                    content_futures.append((
                        'iso',
                        pool.submit(self.pvesh, 'get', '/nodes/' + n + '/storage/' + sname + '/content', {'content': 'iso'}),
                    ))
                if 'vztmpl' in content:
                    content_futures.append((
                        'vztmpl',
                        pool.submit(self.pvesh, 'get', '/nodes/' + n + '/storage/' + sname + '/content', {'content': 'vztmpl'}),
                    ))
            for ctype, f in content_futures:
                try:
                    items = f.result() or []
                    volids = [i.get('volid', '') for i in items]
                    if ctype == 'iso':
                        ctx['isos'].extend(volids)
                    else:
                        ctx['templates'].extend(volids)
                except Exception:
                    pass

        self._ctx_cache = ctx
        self._ctx_time = time.time()
        return ctx

    def execute(self, action, params):
        """Execute a tool call and return the result."""
        read_only = ('exec_container', 'exec_host', 'list_vms', 'list_containers', 'get_resources', 'list_available_templates',
                     'get_container_status', 'get_container_config', 'list_container_snapshots')
        if action not in read_only:
            self._ctx_cache = None
        n = self.node

        if action == 'list_vms':
            data = self.pvesh('get', '/nodes/' + n + '/qemu') or []
            return {'success': True, 'data': data}

        if action == 'list_containers':
            data = self.pvesh('get', '/nodes/' + n + '/lxc') or []
            return {'success': True, 'data': data}

        if action == 'get_resources':
            data = self.pvesh('get', '/cluster/resources') or []
            return {'success': True, 'data': data}

        if action in ('start_vm', 'stop_vm'):
            vmid = params.get('vmid')
            if not vmid:
                return {'success': False, 'error': 'vmid is required'}
            op = 'start' if 'start' in action else 'stop'
            self.pvesh('create', '/nodes/' + n + '/qemu/' + str(vmid) + '/status/' + op, timeout=120)
            verb = 'started' if op == 'start' else 'stopped'
            expected = 'running' if op == 'start' else 'stopped'
            if self._poll_state('vm', vmid, expected):
                return {'success': True, 'message': 'VM ' + str(vmid) + ' ' + verb + ' successfully'}
            return {'success': False, 'error': 'VM ' + str(vmid) + ' ' + op + ' did not reach ' + expected + ' state'}

        if action in ('start_container', 'stop_container'):
            vmid = params.get('vmid')
            if not vmid:
                return {'success': False, 'error': 'vmid is required'}
            op = 'start' if 'start' in action else 'stop'
            self.pvesh('create', '/nodes/' + n + '/lxc/' + str(vmid) + '/status/' + op, timeout=120)
            verb = 'started' if op == 'start' else 'stopped'
            expected = 'running' if op == 'start' else 'stopped'
            if self._poll_state('ct', vmid, expected):
                return {'success': True, 'message': 'Container ' + str(vmid) + ' ' + verb + ' successfully'}
            return {'success': False, 'error': 'Container ' + str(vmid) + ' ' + op + ' did not reach ' + expected + ' state'}

        if action == 'create_vm':
            vmid = params.get('vmid') or self.pvesh('get', '/cluster/nextid')
            storage = params.get('storage') or self._best_storage('images')
            name = params.get('name', 'vm-' + str(vmid))
            ostype = params.get('ostype', 'l26')
            cores = int(params.get('cores', 2))
            memory = int(params.get('memory', 2048))
            disk_size = int(params.get('disk_size', 32))
            net_bridge = params.get('net_bridge', 'vmbr0')
            is_windows = ostype.startswith('win')
            bios = params.get('bios', 'ovmf' if is_windows else 'seabios')
            machine = params.get('machine', 'q35')
            cpu_type = params.get('cpu', 'x86-64-v2-AES')
            p = {
                'vmid': vmid,
                'name': name,
                'cores': cores,
                'memory': memory,
                'ostype': ostype,
                'bios': bios,
                'machine': machine,
                'cpu': cpu_type,
                'scsihw': 'virtio-scsi-single',
                'scsi0': storage + ':' + str(disk_size) + ',iothread=1',
                'net0': 'virtio,bridge=' + net_bridge,
                'agent': 1,
            }
            # EFI disk for OVMF bios
            if bios == 'ovmf':
                efidisk = storage + ':1,efitype=4m'
                if is_windows:
                    efidisk += ',pre-enrolled-keys=1'
                p['efidisk0'] = efidisk
            # TPM for Windows 11
            if ostype == 'win11':
                p['tpmstate0'] = storage + ':1,version=v2.0'
            # ISO and boot order
            if params.get('iso'):
                p['ide2'] = params['iso'] + ',media=cdrom'
                p['boot'] = 'order=ide2;scsi0;net0'
            else:
                p['boot'] = 'order=scsi0;net0'
            self.pvesh('create', '/nodes/' + n + '/qemu', p, timeout=120)
            if self._poll_state('vm', vmid, None, timeout=60):
                return {'success': True, 'message': 'VM ' + str(vmid) + ' (' + name + ') created successfully', 'vmid': vmid}
            return {'success': False, 'error': 'VM creation timed out — check Proxmox task log'}

        if action == 'create_container':
            vmid = params.get('vmid') or self.pvesh('get', '/cluster/nextid')
            template = params.get('template')
            if not template:
                return {'success': False, 'error': 'template is required. Use list_available_templates and download_template if none are available in the server context.'}
            storage = params.get('storage') or self._best_storage('rootdir')
            hostname = params.get('hostname', 'ct-' + str(vmid))
            mem = int(params.get('memory', 512))
            cores = int(params.get('cores', 1))
            disk_size = int(params.get('disk_size', 8))
            net_bridge = params.get('net_bridge', 'vmbr0')
            ip_cfg = params.get('ip', 'dhcp')
            net0 = 'name=eth0,bridge=' + net_bridge
            if ip_cfg == 'dhcp':
                net0 += ',ip=dhcp'
            else:
                net0 += ',ip=' + str(ip_cfg)
                if params.get('gw'):
                    net0 += ',gw=' + str(params['gw'])
            p = {
                'vmid': vmid,
                'hostname': hostname,
                'cores': cores,
                'memory': mem,
                'swap': max(mem // 2, 64),
                'rootfs': storage + ':' + str(disk_size),
                'ostemplate': template,
                'net0': net0,
                'password': params.get('password', 'changeme123'),
                'unprivileged': 0 if params.get('privileged') else 1,
            }
            if params.get('features'):
                p['features'] = params['features']
            if params.get('nameserver'):
                p['nameserver'] = params['nameserver']
            if params.get('ssh_public_keys'):
                p['ssh-public-keys'] = params['ssh_public_keys']
            self.pvesh('create', '/nodes/' + n + '/lxc', p, timeout=120)
            if self._poll_state('ct', vmid, None, timeout=60):
                return {'success': True, 'message': 'Container ' + str(vmid) + ' (' + hostname + ') created successfully', 'vmid': vmid}
            return {'success': False, 'error': 'Container creation timed out — check Proxmox task log'}

        if action == 'exec_container':
            vmid = params.get('vmid')
            command = params.get('command')
            if not vmid or not command:
                return {'success': False, 'error': 'vmid and command are required'}
            if not self._verify_ct(vmid, 'running'):
                return {'success': False, 'error': 'Container ' + str(vmid) + ' is not running. Start it first with start_container.'}
            try:
                result = subprocess.run(
                    ['pct', 'exec', str(vmid), '--', 'bash', '-c', command],
                    capture_output=True, text=True, timeout=300,
                )
                stdout = result.stdout
                if len(stdout) > 3000:
                    stdout = '...(truncated)...\n' + stdout[-3000:]
                stderr = result.stderr
                if len(stderr) > 1500:
                    stderr = '...(truncated)...\n' + stderr[-1500:]
                return {
                    'success': result.returncode == 0,
                    'output': stdout,
                    'stderr': stderr,
                    'exit_code': result.returncode,
                }
            except subprocess.TimeoutExpired:
                return {'success': False, 'error': 'Command timed out after 300s'}
            except Exception as e:
                return {'success': False, 'error': str(e)}

        if action == 'exec_host':
            command = params.get('command')
            if not command:
                return {'success': False, 'error': 'command is required'}
            try:
                result = subprocess.run(
                    ['bash', '-c', command],
                    capture_output=True, text=True, timeout=300,
                )
                stdout = result.stdout
                if len(stdout) > 3000:
                    stdout = '...(truncated)...\n' + stdout[-3000:]
                stderr = result.stderr
                if len(stderr) > 1500:
                    stderr = '...(truncated)...\n' + stderr[-1500:]
                return {
                    'success': result.returncode == 0,
                    'output': stdout,
                    'stderr': stderr,
                    'exit_code': result.returncode,
                }
            except subprocess.TimeoutExpired:
                return {'success': False, 'error': 'Command timed out after 300s'}
            except Exception as e:
                return {'success': False, 'error': str(e)}

        if action == 'save_notes':
            vmid = params.get('vmid')
            notes = params.get('notes')
            kind = params.get('kind', 'ct')
            if not vmid or not notes:
                return {'success': False, 'error': 'vmid and notes are required'}
            path_prefix = '/nodes/' + n + '/lxc/' if kind == 'ct' else '/nodes/' + n + '/qemu/'
            try:
                existing = self.pvesh('get', path_prefix + str(vmid) + '/config')
                old_desc = ''
                if isinstance(existing, dict):
                    old_desc = existing.get('description', '')
                separator = '\n\n---\n' if old_desc else ''
                new_desc = old_desc + separator + notes
                self.pvesh('set', path_prefix + str(vmid) + '/config', {'description': new_desc})
                return {'success': True, 'message': 'Notes saved to ' + ('container' if kind == 'ct' else 'VM') + ' ' + str(vmid)}
            except Exception as e:
                return {'success': False, 'error': str(e)}

        if action == 'delete_container':
            vmid = params.get('vmid')
            if not vmid:
                return {'success': False, 'error': 'vmid is required'}
            if self._verify_ct(vmid, 'running'):
                return {'success': False, 'error': 'Container ' + str(vmid) + ' is running. Stop it first with stop_container.'}
            try:
                self.pvesh('delete', '/nodes/' + n + '/lxc/' + str(vmid), timeout=120)
                return {'success': True, 'message': 'Container ' + str(vmid) + ' deleted successfully'}
            except Exception as e:
                return {'success': False, 'error': str(e)}

        if action == 'get_container_status':
            vmid = params.get('vmid')
            if not vmid:
                return {'success': False, 'error': 'vmid is required'}
            try:
                data = self.pvesh('get', '/nodes/' + n + '/lxc/' + str(vmid) + '/status/current')
                return {'success': True, 'data': data}
            except Exception as e:
                return {'success': False, 'error': str(e)}

        if action == 'get_container_config':
            vmid = params.get('vmid')
            if not vmid:
                return {'success': False, 'error': 'vmid is required'}
            try:
                data = self.pvesh('get', '/nodes/' + n + '/lxc/' + str(vmid) + '/config')
                return {'success': True, 'data': data}
            except Exception as e:
                return {'success': False, 'error': str(e)}

        if action == 'set_container_config':
            vmid = params.get('vmid')
            if not vmid:
                return {'success': False, 'error': 'vmid is required'}
            allowed_keys = {
                'cores', 'memory', 'swap', 'hostname', 'nameserver', 'searchdomain',
                'net0', 'net1', 'net2', 'net3', 'onboot', 'description', 'tags',
                'features', 'cpulimit', 'cpuunits', 'protection', 'tty', 'startup',
                'mp0', 'mp1', 'mp2', 'mp3', 'delete',
            }
            p = {}
            for k, v in params.items():
                if k == 'vmid':
                    continue
                if k in allowed_keys:
                    p[k] = v
            if not p:
                return {'success': False, 'error': 'No valid config keys provided. Allowed: ' + ', '.join(sorted(allowed_keys - {'delete'}))}
            try:
                self.pvesh('set', '/nodes/' + n + '/lxc/' + str(vmid) + '/config', p)
                return {'success': True, 'message': 'Container ' + str(vmid) + ' configuration updated'}
            except Exception as e:
                return {'success': False, 'error': str(e)}

        if action == 'resize_container_disk':
            vmid = params.get('vmid')
            disk = params.get('disk', 'rootfs')
            size = params.get('size')
            if not vmid or not size:
                return {'success': False, 'error': 'vmid and size are required'}
            try:
                self.pvesh('set', '/nodes/' + n + '/lxc/' + str(vmid) + '/resize', {'disk': disk, 'size': size})
                return {'success': True, 'message': 'Container ' + str(vmid) + ' disk "' + disk + '" resized to ' + str(size)}
            except Exception as e:
                return {'success': False, 'error': str(e)}

        if action == 'clone_container':
            vmid = params.get('vmid')
            newid = params.get('newid')
            if not vmid or not newid:
                return {'success': False, 'error': 'vmid and newid are required'}
            p = {'newid': int(newid)}
            if params.get('hostname'):
                p['hostname'] = params['hostname']
            if params.get('description'):
                p['description'] = params['description']
            if params.get('storage'):
                p['storage'] = params['storage']
            p['full'] = int(params.get('full', 1))
            try:
                result = self.pvesh('create', '/nodes/' + n + '/lxc/' + str(vmid) + '/clone', p, timeout=600)
                upid = result if isinstance(result, str) and result.startswith('UPID:') else None
                if upid:
                    success, status = self._poll_task(upid, timeout=600)
                    if not success:
                        exitstatus = status.get('exitstatus', 'unknown error') if isinstance(status, dict) else str(status)
                        return {'success': False, 'error': 'Clone failed: ' + exitstatus}
                return {'success': True, 'message': 'Container ' + str(vmid) + ' cloned to ' + str(newid), 'vmid': int(newid)}
            except Exception as e:
                return {'success': False, 'error': str(e)}

        if action == 'snapshot_container':
            vmid = params.get('vmid')
            snapname = params.get('snapname')
            if not vmid or not snapname:
                return {'success': False, 'error': 'vmid and snapname are required'}
            p = {'snapname': snapname}
            if params.get('description'):
                p['description'] = params['description']
            try:
                self.pvesh('create', '/nodes/' + n + '/lxc/' + str(vmid) + '/snapshot', p, timeout=120)
                return {'success': True, 'message': 'Snapshot "' + snapname + '" created for container ' + str(vmid)}
            except Exception as e:
                return {'success': False, 'error': str(e)}

        if action == 'list_container_snapshots':
            vmid = params.get('vmid')
            if not vmid:
                return {'success': False, 'error': 'vmid is required'}
            try:
                data = self.pvesh('get', '/nodes/' + n + '/lxc/' + str(vmid) + '/snapshot')
                return {'success': True, 'data': data}
            except Exception as e:
                return {'success': False, 'error': str(e)}

        if action == 'rollback_container_snapshot':
            vmid = params.get('vmid')
            snapname = params.get('snapname')
            if not vmid or not snapname:
                return {'success': False, 'error': 'vmid and snapname are required'}
            try:
                self.pvesh('create', '/nodes/' + n + '/lxc/' + str(vmid) + '/snapshot/' + str(snapname) + '/rollback', timeout=120)
                return {'success': True, 'message': 'Container ' + str(vmid) + ' rolled back to snapshot "' + snapname + '"'}
            except Exception as e:
                return {'success': False, 'error': str(e)}

        if action == 'delete_container_snapshot':
            vmid = params.get('vmid')
            snapname = params.get('snapname')
            if not vmid or not snapname:
                return {'success': False, 'error': 'vmid and snapname are required'}
            try:
                self.pvesh('delete', '/nodes/' + n + '/lxc/' + str(vmid) + '/snapshot/' + str(snapname), timeout=120)
                return {'success': True, 'message': 'Snapshot "' + snapname + '" deleted from container ' + str(vmid)}
            except Exception as e:
                return {'success': False, 'error': str(e)}

        if action == 'migrate_container':
            vmid = params.get('vmid')
            target = params.get('target')
            if not vmid or not target:
                return {'success': False, 'error': 'vmid and target are required'}
            try:
                result = self.pvesh('create', '/nodes/' + n + '/lxc/' + str(vmid) + '/migrate', {'target': target}, timeout=600)
                upid = result if isinstance(result, str) and result.startswith('UPID:') else None
                if upid:
                    success, status = self._poll_task(upid, timeout=600)
                    if not success:
                        exitstatus = status.get('exitstatus', 'unknown error') if isinstance(status, dict) else str(status)
                        return {'success': False, 'error': 'Migration failed: ' + exitstatus}
                return {'success': True, 'message': 'Container ' + str(vmid) + ' migrated to node "' + target + '"'}
            except Exception as e:
                return {'success': False, 'error': str(e)}

        if action == 'suspend_container':
            vmid = params.get('vmid')
            if not vmid:
                return {'success': False, 'error': 'vmid is required'}
            try:
                self.pvesh('create', '/nodes/' + n + '/lxc/' + str(vmid) + '/status/suspend', timeout=120)
                return {'success': True, 'message': 'Container ' + str(vmid) + ' suspended'}
            except Exception as e:
                return {'success': False, 'error': str(e)}

        if action == 'resume_container':
            vmid = params.get('vmid')
            if not vmid:
                return {'success': False, 'error': 'vmid is required'}
            try:
                self.pvesh('create', '/nodes/' + n + '/lxc/' + str(vmid) + '/status/resume', timeout=120)
                return {'success': True, 'message': 'Container ' + str(vmid) + ' resumed'}
            except Exception as e:
                return {'success': False, 'error': str(e)}

        if action == 'delete_vm':
            vmid = params.get('vmid')
            if not vmid:
                return {'success': False, 'error': 'vmid is required'}
            if self._verify_vm(vmid, 'running'):
                return {'success': False, 'error': 'VM ' + str(vmid) + ' is running. Stop it first with stop_vm.'}
            try:
                self.pvesh('delete', '/nodes/' + n + '/qemu/' + str(vmid), {'purge': 1, 'destroy-unreferenced-disks': 1}, timeout=120)
                return {'success': True, 'message': 'VM ' + str(vmid) + ' deleted successfully'}
            except Exception as e:
                return {'success': False, 'error': str(e)}

        if action == 'list_available_templates':
            try:
                result = subprocess.run(
                    ['pveam', 'available'],
                    capture_output=True, text=True, timeout=30,
                )
                if result.returncode != 0:
                    return {'success': False, 'error': result.stderr.strip() or 'pveam available failed. Try running "pveam update" on the host first.'}
                lines = result.stdout.strip().split('\n')
                templates = []
                for line in lines:
                    parts = line.split()
                    if len(parts) >= 2:
                        templates.append({'section': parts[0], 'package': parts[1]})
                return {'success': True, 'data': templates}
            except Exception as e:
                return {'success': False, 'error': str(e)}

        if action == 'download_template':
            template = params.get('template')
            if not template:
                return {'success': False, 'error': 'template name is required (e.g. "debian-12-standard_12.7-1_amd64.tar.zst")'}
            storage = params.get('storage') or self._best_storage('vztmpl')
            try:
                subprocess.run(['pveam', 'update'], capture_output=True, text=True, timeout=60)
                result = subprocess.run(
                    ['pveam', 'download', storage, template],
                    capture_output=True, text=True, timeout=600,
                )
                if result.returncode == 0:
                    volid = storage + ':vztmpl/' + template
                    return {'success': True, 'message': 'Template downloaded successfully', 'volid': volid}
                return {'success': False, 'error': result.stderr.strip() or 'Download failed'}
            except subprocess.TimeoutExpired:
                return {'success': False, 'error': 'Template download timed out (600s)'}
            except Exception as e:
                return {'success': False, 'error': str(e)}

        if action == 'download_iso':
            url = params.get('url')
            filename = params.get('filename')
            if not url or not filename:
                return {'success': False, 'error': 'url and filename are required'}
            if not filename.endswith('.iso'):
                filename += '.iso'
            storage = params.get('storage') or self._best_storage('iso')
            try:
                result = self.pvesh('create', '/nodes/' + n + '/storage/' + storage + '/download-url', {
                    'url': url,
                    'content': 'iso',
                    'filename': filename,
                }, timeout=1800)
                # pvesh may block until done or return a UPID — handle both
                upid = result if isinstance(result, str) and result.startswith('UPID:') else None
                if upid:
                    success, status = self._poll_task(upid, timeout=1800)
                    if not success:
                        exitstatus = status.get('exitstatus', 'unknown error') if isinstance(status, dict) else str(status)
                        return {'success': False, 'error': 'ISO download failed: ' + exitstatus}
                volid = storage + ':iso/' + filename
                return {'success': True, 'message': 'ISO downloaded successfully', 'volid': volid}
            except subprocess.TimeoutExpired:
                return {'success': False, 'error': 'ISO download timed out (1800s)'}
            except Exception as e:
                return {'success': False, 'error': str(e)}

        return {'success': False, 'error': 'Unknown action: ' + str(action)}

    def _best_storage(self, content_type):
        """Pick the active storage with the most free space for given content type."""
        try:
            stor = self.pvesh('get', '/nodes/' + self.node + '/storage') or []
            best = None
            for s in stor:
                if s.get('active') and content_type in s.get('content', ''):
                    if best is None or s.get('avail', 0) > best.get('avail', 0):
                        best = s
            if best:
                return best['storage']
        except Exception:
            pass
        return 'local-lvm'

    def _poll_state(self, kind, vmid, expected, timeout=15):
        """Poll actual VM/CT status until it matches expected, up to timeout seconds."""
        verify = self._verify_vm if kind == 'vm' else self._verify_ct
        start = time.time()
        while time.time() - start < timeout:
            if verify(vmid, expected):
                return True
            time.sleep(1)
        return verify(vmid, expected)

    def _poll_task(self, upid, timeout=1800):
        """Poll a Proxmox async task (UPID) until completion.
        Returns (success, status_dict)."""
        start = time.time()
        while time.time() - start < timeout:
            try:
                st = self.pvesh('get', '/nodes/' + self.node + '/tasks/' + str(upid) + '/status')
                if isinstance(st, dict) and st.get('status') == 'stopped':
                    return st.get('exitstatus') == 'OK', st
            except Exception:
                pass
            time.sleep(5)
        return False, {'exitstatus': 'timeout'}

    def _verify_vm(self, vmid, expected_status=None):
        """Check if a VM exists and optionally verify its status."""
        try:
            st = self.pvesh('get', '/nodes/' + self.node + '/qemu/' + str(vmid) + '/status/current')
            if isinstance(st, dict):
                if expected_status is None:
                    return True
                return st.get('status') == expected_status
        except Exception:
            pass
        return False

    def _verify_ct(self, vmid, expected_status=None):
        """Check if a container exists and optionally verify its status."""
        try:
            st = self.pvesh('get', '/nodes/' + self.node + '/lxc/' + str(vmid) + '/status/current')
            if isinstance(st, dict):
                if expected_status is None:
                    return True
                return st.get('status') == expected_status
        except Exception:
            pass
        return False


pve = PVEHelper()


class AIProxyHandler(BaseHTTPRequestHandler):

    def log_message(self, fmt, *args):
        pass

    def _cors(self):
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'POST, GET, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')

    def _json_resp(self, code, data):
        body = json.dumps(data).encode()
        self.send_response(code)
        self._cors()
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(200)
        self._cors()
        self.end_headers()

    def do_GET(self):
        self._json_resp(200, {'status': 'ok', 'message': 'PVE AI Proxy is running'})

    def do_POST(self):
        try:
            length = int(self.headers.get('Content-Length', 0))
            body = json.loads(self.rfile.read(length))
        except Exception:
            self._json_resp(400, {'error': 'Invalid request body'})
            return

        if self.path == '/execute':
            self._handle_execute(body)
        elif self.path == '/chat':
            self._handle_chat_request(body)
        else:
            self._json_resp(404, {'error': 'Not found'})

    def _handle_execute(self, body):
        """Direct action execution endpoint (for stop/delete from frontend)."""
        action = body.get('action', '')
        params = body.get('params', {})
        if not action:
            self._json_resp(400, {'error': 'Missing action'})
            return
        try:
            result = pve.execute(action, params)
            self._json_resp(200, result)
        except Exception as e:
            self._json_resp(500, {'success': False, 'error': str(e)})

    def _handle_chat_request(self, body):
        model = body.get('model', '')
        api_key = body.get('api_key', '')
        messages = body.get('messages', [])

        if not model or not api_key:
            self._json_resp(400, {'error': 'Missing model or api_key'})
            return

        self.send_response(200)
        self._cors()
        self.send_header('Content-Type', 'application/x-ndjson')
        self.send_header('Cache-Control', 'no-cache')
        self.end_headers()

        self._client_gone = False

        def emit(data):
            if self._client_gone:
                raise ConnectionAbortedError('Client disconnected')
            try:
                self.wfile.write((json.dumps(data) + '\n').encode())
                self.wfile.flush()
            except Exception:
                self._client_gone = True
                raise ConnectionAbortedError('Client disconnected')

        try:
            self._handle_chat(model, api_key, messages, emit)
        except ConnectionAbortedError:
            return
        except urllib.error.HTTPError as e:
            error_body = e.read().decode('utf-8', errors='replace')
            try:
                err = json.loads(error_body).get('error', {})
                if isinstance(err, dict):
                    err = err.get('message', str(err))
            except Exception:
                err = error_body[:500]
            emit({'type': 'error', 'error': 'API error (' + str(e.code) + '): ' + str(err)})
        except Exception as e:
            emit({'type': 'error', 'error': str(e)})

    def _handle_chat(self, model, api_key, messages, emit):
        """Main chat handler with multi-round tool execution."""
        emit({'type': 'status', 'message': 'Reading server state...'})
        ctx = pve.get_context()

        # Build table of always-current ISOs (with "latest" symlinks)
        iso_table = '\n'.join(
            '  - ' + name + ': ' + info.get('description', '') + '\n    url="' + info['url'] + '", filename="' + info['filename'] + '"'
            for name, info in ISO_URLS.items()
        )

        # Build table of URL patterns for constructing versioned URLs
        pattern_table = '\n'.join(
            '  - ' + name + ': ' + info.get('description', '') + '\n    pattern: ' + info['pattern'] + '\n    filename: ' + info['filename_pattern'] + '\n    example: ' + info.get('example_version', '')
            for name, info in ISO_URL_PATTERNS.items()
        )

        sys_prompt = SYSTEM_PROMPT.format(
            context=json.dumps(ctx, indent=2),
            next_vmid=ctx.get('next_vmid', 100),
            iso_urls=iso_table,
            iso_patterns=pattern_table,
        )

        ai_msgs = [{'role': 'system', 'content': sys_prompt}]
        for m in messages:
            if m.get('role') != 'system':
                ai_msgs.append({'role': m['role'], 'content': m['content']})

        MAX_ROUNDS = 10
        for round_num in range(MAX_ROUNDS):
            emit({'type': 'status', 'message': 'Thinking...' if round_num == 0 else 'Planning next steps...'})
            response = self._call_ai(model, api_key, ai_msgs)

            tool_calls = re.findall(r'<tool_call>(.*?)</tool_call>', response, re.DOTALL)
            if not tool_calls:
                clean = re.sub(r'<tool_call>.*?</tool_call>', '', response, flags=re.DOTALL).strip()
                emit({'type': 'done', 'response': clean})
                return

            all_results = []
            failed_critical = False
            for tc_str in tool_calls:
                if failed_critical:
                    all_results.append({'action': 'skipped', 'result': {'success': False, 'error': 'Skipped — a previous critical step failed'}})
                    continue
                try:
                    tc = self._parse_tool_call(tc_str)
                    action = tc.get('action', '')
                    params = tc.get('params', {})
                    emit({'type': 'status', 'message': self._describe_action(action, params)})
                    if action == 'exec_container':
                        result = self._stream_exec(params, emit)
                    elif action == 'exec_host':
                        result = self._stream_exec_host(params, emit)
                    else:
                        result = pve.execute(action, params)
                    all_results.append({'action': action, 'result': result})
                    if result.get('success') and result.get('vmid'):
                        ctype = 'ct' if 'container' in action else 'vm'
                        emit({'type': 'status', 'message': self._describe_action(action, params), 'created_vmid': result['vmid'], 'created_type': ctype})
                    if not result.get('success') and action in ('create_vm', 'create_container', 'start_vm', 'start_container', 'download_template', 'download_iso',
                                                                   'clone_container', 'rollback_container_snapshot', 'migrate_container'):
                        failed_critical = True
                except json.JSONDecodeError:
                    all_results.append({'action': 'unknown', 'result': {'success': False, 'error': 'Malformed tool call'}})
                except Exception as e:
                    all_results.append({'action': 'unknown', 'result': {'success': False, 'error': str(e)}})

            ai_msgs.append({'role': 'assistant', 'content': response})
            ai_msgs.append({
                'role': 'user',
                'content': '[System: Tool results — round ' + str(round_num + 1) + '/' + str(MAX_ROUNDS) + ']\n' +
                           json.dumps(all_results, indent=2) +
                           '\n\nIf more steps are needed, continue with <tool_call> tags. When completely done, respond with a final summary and NO <tool_call> tags.',
            })

        emit({'type': 'status', 'message': 'Generating summary...'})
        ai_msgs.append({'role': 'user', 'content': 'Max rounds reached. Summarize what was done and list any remaining manual steps. Do NOT include <tool_call> tags.'})
        final = self._call_ai(model, api_key, ai_msgs)
        final = re.sub(r'<tool_call>.*?</tool_call>', '', final, flags=re.DOTALL).strip()
        emit({'type': 'done', 'response': final})

    @staticmethod
    def _fix_json_strings(s):
        """Replace literal newlines/tabs inside JSON string values with escape sequences."""
        result = []
        in_string = False
        i = 0
        while i < len(s):
            ch = s[i]
            if in_string:
                if ch == '\\' and i + 1 < len(s):
                    result.append(ch)
                    result.append(s[i + 1])
                    i += 2
                    continue
                if ch == '"':
                    in_string = False
                    result.append(ch)
                elif ch == '\n':
                    result.append('\\n')
                elif ch == '\r':
                    pass
                elif ch == '\t':
                    result.append('\\t')
                else:
                    result.append(ch)
            else:
                if ch == '"':
                    in_string = True
                result.append(ch)
            i += 1
        return ''.join(result)

    @staticmethod
    def _parse_tool_call(raw):
        """Robustly parse a tool call string into a dict."""
        s = raw.strip()
        # Remove markdown code fences: ```json ... ``` or ```...```
        s = re.sub(r'^```\w*\n?', '', s)
        s = re.sub(r'\n?```$', '', s)
        s = s.strip()
        # Try direct parse first
        try:
            return json.loads(s)
        except json.JSONDecodeError:
            pass
        # Fix literal newlines inside JSON strings and retry
        fixed_nl = AIProxyHandler._fix_json_strings(s)
        try:
            return json.loads(fixed_nl)
        except json.JSONDecodeError:
            pass
        # Extract first { ... } block
        m = re.search(r'(\{[\s\S]*\})', s)
        if m:
            candidate = m.group(1)
            # Remove trailing commas before } or ]
            candidate = re.sub(r',\s*([}\]])', r'\1', candidate)
            try:
                return json.loads(candidate)
            except json.JSONDecodeError:
                pass
            # Fix newlines in extracted block
            fixed_candidate = AIProxyHandler._fix_json_strings(candidate)
            try:
                return json.loads(fixed_candidate)
            except json.JSONDecodeError:
                pass
            # Try replacing single quotes with double quotes
            fixed = candidate.replace("'", '"')
            try:
                return json.loads(fixed)
            except json.JSONDecodeError:
                pass
        # --- Last resort: regex extraction for known actions ---
        result = AIProxyHandler._regex_extract_tool_call(s)
        if result:
            return result
        raise json.JSONDecodeError('Cannot parse tool call', s, 0)

    @staticmethod
    def _regex_extract_tool_call(s):
        """Last-resort regex extraction when JSON parsing completely fails."""
        action_m = re.search(r'"action"\s*:\s*"(\w+)"', s)
        if not action_m:
            return None
        action = action_m.group(1)
        params = {}
        # Extract vmid (int or string)
        vmid_m = re.search(r'"vmid"\s*:\s*"?(\d+)"?', s)
        if vmid_m:
            params['vmid'] = int(vmid_m.group(1))
        if action in ('exec_container', 'exec_host'):
            # Find "command": " then scan to extract the full command value
            cmd_m = re.search(r'"command"\s*:\s*"', s)
            if cmd_m:
                start = cmd_m.end()
                # Strategy: find the last " before the final }} of the object
                tail = s.rstrip()
                # Strip trailing braces/whitespace to locate the closing quote
                brace_tail = re.search(r'"\s*\}\s*\}?\s*$', tail[start:])
                if brace_tail:
                    end = start + brace_tail.start()
                else:
                    # Fallback: scan for unescaped " followed by , or }
                    end = None
                    j = start
                    last_quote = -1
                    while j < len(s):
                        if s[j] == '\\' and j + 1 < len(s):
                            j += 2
                            continue
                        if s[j] == '"':
                            last_quote = j
                        j += 1
                    if last_quote > start:
                        end = last_quote
                if end is not None and end > start:
                    raw_cmd = s[start:end]
                    # Decode JSON string escapes
                    raw_cmd = raw_cmd.replace('\\n', '\n').replace('\\t', '\t')
                    raw_cmd = raw_cmd.replace('\\"', '"').replace('\\/', '/')
                    raw_cmd = raw_cmd.replace('\\\\', '\\')
                    params['command'] = raw_cmd
        else:
            # Extract common string params for other actions
            for key in ('hostname', 'name', 'template', 'storage', 'filename',
                        'url', 'ostype', 'password', 'ostemplate', 'net0',
                        'content', 'description', 'snapname', 'target', 'disk',
                        'size', 'tags', 'features', 'nameserver', 'searchdomain',
                        'startup', 'delete', 'net_bridge', 'notes', 'kind'):
                km = re.search(r'"' + key + r'"\s*:\s*"([^"]*)"', s)
                if km:
                    params[key] = km.group(1)
            for key in ('memory', 'disk', 'cores', 'swap', 'rootfs',
                        'newid', 'onboot', 'protection', 'tty', 'cpuunits', 'full'):
                km = re.search(r'"' + key + r'"\s*:\s*(\d+)', s)
                if km:
                    params[key] = int(km.group(1))
        if not params:
            return None
        return {'action': action, 'params': params}

    def _stream_exec(self, params, emit):
        """Stream exec_container output line-by-line via shell events."""
        vmid = params.get('vmid')
        command = params.get('command')
        if not vmid or not command:
            return {'success': False, 'error': 'vmid and command are required'}
        if not pve._verify_ct(vmid, 'running'):
            return {'success': False, 'error': 'Container ' + str(vmid) + ' is not running. Start it first with start_container.'}

        emit({'type': 'shell_start', 'vmid': str(vmid), 'command': command, 'node': pve.node})

        try:
            proc = subprocess.Popen(
                ['pct', 'exec', str(vmid), '--', 'bash', '-c', command],
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
            )
            kill_timer = threading.Timer(300, lambda: proc.kill())
            kill_timer.start()

            output_chunks = []
            try:
                while True:
                    line = proc.stdout.readline()
                    if not line:
                        break
                    output_chunks.append(line)
                    emit({'type': 'shell_output', 'vmid': str(vmid), 'output': line})
            except ConnectionAbortedError:
                proc.kill()
                kill_timer.cancel()
                raise
            finally:
                kill_timer.cancel()

            exit_code = proc.wait(timeout=10)
            emit({'type': 'shell_end', 'vmid': str(vmid), 'exit_code': exit_code})

            stdout = ''.join(output_chunks)
            if len(stdout) > 3000:
                stdout = '...(truncated)...\n' + stdout[-3000:]
            return {
                'success': exit_code == 0,
                'output': stdout,
                'stderr': '',
                'exit_code': exit_code,
            }
        except ConnectionAbortedError:
            raise
        except subprocess.TimeoutExpired:
            try:
                proc.kill()
            except Exception:
                pass
            try:
                emit({'type': 'shell_end', 'vmid': str(vmid), 'exit_code': -1})
            except Exception:
                pass
            return {'success': False, 'error': 'Command timed out after 300s'}
        except Exception as e:
            try:
                emit({'type': 'shell_end', 'vmid': str(vmid), 'exit_code': -1})
            except Exception:
                pass
            return {'success': False, 'error': str(e)}

    def _stream_exec_host(self, params, emit):
        """Stream exec_host output line-by-line via shell events."""
        command = params.get('command')
        if not command:
            return {'success': False, 'error': 'command is required'}

        host_id = 'host'
        emit({'type': 'shell_start', 'vmid': host_id, 'command': command, 'node': pve.node, 'is_host': True})

        try:
            proc = subprocess.Popen(
                ['bash', '-c', command],
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
            )
            kill_timer = threading.Timer(300, lambda: proc.kill())
            kill_timer.start()

            output_chunks = []
            try:
                while True:
                    line = proc.stdout.readline()
                    if not line:
                        break
                    output_chunks.append(line)
                    emit({'type': 'shell_output', 'vmid': host_id, 'output': line})
            except ConnectionAbortedError:
                proc.kill()
                kill_timer.cancel()
                raise
            finally:
                kill_timer.cancel()

            exit_code = proc.wait(timeout=10)
            emit({'type': 'shell_end', 'vmid': host_id, 'exit_code': exit_code})

            stdout = ''.join(output_chunks)
            if len(stdout) > 3000:
                stdout = '...(truncated)...\n' + stdout[-3000:]
            return {
                'success': exit_code == 0,
                'output': stdout,
                'stderr': '',
                'exit_code': exit_code,
            }
        except ConnectionAbortedError:
            raise
        except subprocess.TimeoutExpired:
            try:
                proc.kill()
            except Exception:
                pass
            try:
                emit({'type': 'shell_end', 'vmid': host_id, 'exit_code': -1})
            except Exception:
                pass
            return {'success': False, 'error': 'Command timed out after 300s'}
        except Exception as e:
            try:
                emit({'type': 'shell_end', 'vmid': host_id, 'exit_code': -1})
            except Exception:
                pass
            return {'success': False, 'error': str(e)}

    @staticmethod
    def _describe_action(action, params):
        """Human-readable status for a tool action."""
        if action == 'create_vm':
            return 'Creating VM "' + params.get('name', 'vm') + '"...'
        if action == 'create_container':
            return 'Creating container "' + params.get('hostname', 'ct') + '"...'
        if action == 'start_vm':
            return 'Starting VM ' + str(params.get('vmid', '')) + '...'
        if action == 'stop_vm':
            return 'Stopping VM ' + str(params.get('vmid', '')) + '...'
        if action == 'start_container':
            return 'Starting container ' + str(params.get('vmid', '')) + '...'
        if action == 'stop_container':
            return 'Stopping container ' + str(params.get('vmid', '')) + '...'
        if action == 'list_vms':
            return 'Listing VMs...'
        if action == 'list_containers':
            return 'Listing containers...'
        if action == 'exec_container':
            cmd = params.get('command', '')
            if len(cmd) > 80:
                cmd = cmd[:80] + '...'
            return 'Running: ' + cmd
        if action == 'delete_container':
            return 'Deleting container ' + str(params.get('vmid', '')) + '...'
        if action == 'delete_vm':
            return 'Deleting VM ' + str(params.get('vmid', '')) + '...'
        if action == 'download_template':
            return 'Downloading template ' + params.get('template', '') + '...'
        if action == 'download_iso':
            return 'Downloading ISO ' + params.get('filename', '') + '...'
        if action == 'list_available_templates':
            return 'Fetching available templates...'
        if action == 'get_resources':
            return 'Fetching cluster resources...'
        if action == 'get_container_status':
            return 'Fetching status for container ' + str(params.get('vmid', '')) + '...'
        if action == 'get_container_config':
            return 'Fetching config for container ' + str(params.get('vmid', '')) + '...'
        if action == 'set_container_config':
            return 'Updating config for container ' + str(params.get('vmid', '')) + '...'
        if action == 'resize_container_disk':
            return 'Resizing disk "' + params.get('disk', 'rootfs') + '" on container ' + str(params.get('vmid', '')) + '...'
        if action == 'clone_container':
            return 'Cloning container ' + str(params.get('vmid', '')) + ' to ' + str(params.get('newid', '')) + '...'
        if action == 'snapshot_container':
            return 'Creating snapshot "' + params.get('snapname', '') + '" for container ' + str(params.get('vmid', '')) + '...'
        if action == 'list_container_snapshots':
            return 'Listing snapshots for container ' + str(params.get('vmid', '')) + '...'
        if action == 'rollback_container_snapshot':
            return 'Rolling back container ' + str(params.get('vmid', '')) + ' to snapshot "' + params.get('snapname', '') + '"...'
        if action == 'delete_container_snapshot':
            return 'Deleting snapshot "' + params.get('snapname', '') + '" from container ' + str(params.get('vmid', '')) + '...'
        if action == 'migrate_container':
            return 'Migrating container ' + str(params.get('vmid', '')) + ' to node "' + params.get('target', '') + '"...'
        if action == 'suspend_container':
            return 'Suspending container ' + str(params.get('vmid', '')) + '...'
        if action == 'resume_container':
            return 'Resuming container ' + str(params.get('vmid', '')) + '...'
        if action == 'exec_host':
            cmd = params.get('command', '')
            if len(cmd) > 80:
                cmd = cmd[:80] + '...'
            return 'Running on host: ' + cmd
        if action == 'save_notes':
            kind = params.get('kind', 'ct')
            label = 'container' if kind == 'ct' else 'VM'
            return 'Saving notes to ' + label + ' ' + str(params.get('vmid', '')) + '...'
        return 'Executing ' + action + '...'

    def _call_ai(self, model, api_key, messages):
        """Dispatch to the correct AI provider and return text."""
        if 'gpt' in model.lower():
            return self._openai(model, api_key, messages)
        if 'gemini' in model.lower():
            return self._gemini(model, api_key, messages)
        if 'grok' in model.lower():
            return self._xai(model, api_key, messages)
        raise Exception('Unknown model: ' + model)

    def _http_post(self, url, headers, data, timeout=180):
        req = urllib.request.Request(
            url,
            data=json.dumps(data).encode(),
            headers=headers,
            method='POST',
        )
        ctx = ssl.create_default_context()
        resp = urllib.request.urlopen(req, timeout=timeout, context=ctx)
        return json.loads(resp.read())

    def _extract_responses_text(self, result):
        text = ''
        annotations = []
        for item in result.get('output', []):
            if item.get('type') == 'message':
                for part in item.get('content', []):
                    if part.get('type') == 'output_text':
                        text += part.get('text', '')
                        for ann in part.get('annotations', []):
                            if ann.get('type') == 'url_citation':
                                annotations.append(ann)
        if not text:
            return str(result)
        if annotations:
            annotations.sort(key=lambda a: a.get('start_index', 0), reverse=True)
            for ann in annotations:
                url = ann.get('url', '')
                title = ann.get('title', url)
                start = ann.get('start_index', 0)
                end = ann.get('end_index', start)
                link = '[' + title + '](' + url + ')'
                text = text[:start] + link + text[end:]
        return text

    def _openai(self, model, api_key, messages):
        msgs = [{'role': m['role'], 'content': m['content']} for m in messages]
        body = {'model': model, 'input': msgs}
        if '5' in model:
            body['tools'] = [{'type': 'web_search'}]
        result = self._http_post(
            'https://api.openai.com/v1/responses',
            {'Content-Type': 'application/json', 'Authorization': 'Bearer ' + api_key},
            body,
        )
        return self._extract_responses_text(result)

    def _gemini(self, model, api_key, messages):
        system_text = ''
        contents = []
        for m in messages:
            if m['role'] == 'system':
                system_text = m['content']
            else:
                role = 'user' if m['role'] == 'user' else 'model'
                contents.append({'role': role, 'parts': [{'text': m['content']}]})
        body = {'contents': contents}
        if system_text:
            body['systemInstruction'] = {'parts': [{'text': system_text}]}
        result = self._http_post(
            'https://generativelanguage.googleapis.com/v1beta/models/gemini-3-flash-preview:generateContent',
            {'Content-Type': 'application/json', 'x-goog-api-key': api_key},
            body,
        )
        return result['candidates'][0]['content']['parts'][0]['text']

    def _xai(self, model, api_key, messages):
        msgs = [{'role': m['role'], 'content': m['content']} for m in messages]
        result = self._http_post(
            'https://api.x.ai/v1/responses',
            {'Content-Type': 'application/json', 'Authorization': 'Bearer ' + api_key},
            {'model': 'grok-4-1-fast-reasoning', 'input': msgs},
            timeout=3600,
        )
        return self._extract_responses_text(result)


class ThreadedHTTPServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True


def main():
    server = ThreadedHTTPServer(('0.0.0.0', PORT), AIProxyHandler)
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(CERT_FILE, KEY_FILE)
    server.socket = ctx.wrap_socket(server.socket, server_side=True)
    print('PVE AI Proxy running on https://0.0.0.0:' + str(PORT))
    server.serve_forever()


if __name__ == '__main__':
    main()
