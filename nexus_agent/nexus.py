import json
import subprocess
import requests
import os
import sys

class NexusGenesis:
    def __init__(self, model="qwen2.5-coder:0.5b"):
        self.model = model
        self.api_url = "http://localhost:11434/api/generate"
        self.project_dir = os.path.join(os.getcwd(), "rehoboth-genesis-project")
        os.makedirs(self.project_dir, exist_ok=True)
        
    def log(self, level, msg):
        colors = {"INFO": "\033[94m", "ACTION": "\033[92m", "ERROR": "\033[91m"}
        print(f"{colors.get(level, '')}[{level}]\033[0m {msg}")

    def ask(self, prompt, system=""):
        payload = {
            "model": self.model,
            "prompt": prompt,
            "system": system,
            "stream": False,
            "options": {"temperature": 0.1},
            "keep_alive": "5m"
        }
        try:
            response = requests.post(self.api_url, json=payload, timeout=180)
            return response.json().get("response", "").strip()
        except Exception as e:
            return f"Error: {str(e)}"

    def run_mission(self, objective):
        self.log("INFO", f"Objective: {objective}")
        
        # Step 1: Plan & Execute in one go for efficiency
        prompt = f"""Target: {objective}
Provide a list of bash commands to complete this target. 
Output ONLY the commands, one per line. No explanation.
Example:
mkdir -p test
touch test/app.py
"""
        commands_raw = self.ask(prompt, system="You are an expert dev-ops agent. Output raw bash commands only.")
        commands = [c.strip() for c in commands_raw.split("\n") if c.strip() and not c.startswith("`")]
        
        for cmd in commands:
            self.log("ACTION", f"Running: {cmd}")
            result = subprocess.run(cmd, shell=True, capture_output=True, text=True, cwd=self.project_dir)
            if result.returncode != 0:
                self.log("ERROR", f"Failed: {result.stderr}")
            else:
                self.log("INFO", "Success.")

if __name__ == "__main__":
    agent = NexusGenesis()
    task = sys.argv[1] if len(sys.argv) > 1 else "Create a simple time API"
    agent.run_mission(task)
