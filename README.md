# Nexus Genesis: Autonomous Local-AI Developer

**Nexus Genesis** is a local-first autonomous developer agent designed to run entirely on your device (optimized for Android/Termux). It uses a "Dual-Brain" architecture to plan, execute, and verify software engineering tasks without sending data to the cloud.

## 🚀 Features
- **Dual-Brain Architecture:** Uses `Qwen2.5-Coder:1.5B` for high-level architectural planning and `0.5B` for lightning-fast command execution.
- **Local-First:** Runs entirely via [Ollama](https://ollama.com/), ensuring total privacy and offline capability.
- **Autonomous Loops:** Can initialize projects, write code, and run shell commands to build full applications.
- **Termux Optimized:** Low memory footprint, perfect for mobile development environments.

## 🛠️ Architecture
1. **The Architect (1.5B):** Breaks down complex requirements into logical, step-by-step engineering plans.
2. **The Scout (0.5B):** Translates each plan step into precise bash commands or code snippets.
3. **The Nexus Core:** Orchestrates the flow, manages the filesystem, and handles error correction.

## 📦 Installation
1. Install [Ollama](https://ollama.com/) on your device.
2. Pull the required models:
   ```bash
   ollama pull qwen2.5-coder:1.5b
   ollama pull qwen2.5-coder:0.5b
   ```
3. Clone this repository:
   ```bash
   git clone https://github.com/alpha-1-design/rehoboth-genesis.git
   cd rehoboth-genesis
   ```

## 🎮 Usage
Run the agent with a mission:
```bash
python3 nexus_agent/nexus.py "Build a React-based todo list with local storage"
```

## 🛡️ License
MIT License - 100% Open Source.
