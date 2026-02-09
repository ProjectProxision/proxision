# Proxision

AI powered assistant for Proxmox VE. Manage VMs, containers, and infrastructure through natural language.

## Quick Install

```bash
curl -sSL https://raw.githubusercontent.com/ProjectProxision/proxision/main/install.sh | bash
```

## Quick Uninstall

```bash
curl -sSL https://raw.githubusercontent.com/ProjectProxision/proxision/main/uninstall.sh | PROXISION_FORCE_UNINSTALL=1 bash
```

## Features

- **Container Management**: Create, start, stop, delete LXC containers
- **VM Management**: Create VMs with automatic ISO downloading
- **Command Execution**: Run commands inside containers automatically
- **Template Management**: Download and manage container templates
- **Natural Language**: Just describe what you want in plain English

## Supported AI Providers

- OpenAI (GPT-5.2)
- Google (Gemini 3 Flash)
- xAI (Grok 4.1)

## Files

| File | Description |
|------|-------------|
| `pve-ai-proxy.py` | Backend Python service that handles AI requests |
| `pve-ai-proxy.service` | Systemd service file |
| `AIChatPanel.js` | Frontend chat panel component |
| `AIModelSettings.js` | AI model configuration dialog |
| `install.sh` | Installation script |
| `uninstall.sh` | Clean uninstallation script |

## Requirements

- Proxmox VE 7.x or 8.x
- Python 3.x
- API key from one of the supported AI providers

## Usage

1. Install Proxision using the quick install command
2. Open Proxmox web UI at `https://your-server:8006`
3. Find "Proxision" in the left navigation panel
4. Click "Set Model" to configure your AI provider and API key
5. Start chatting!

## License

MIT
