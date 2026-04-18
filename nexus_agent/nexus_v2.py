import json
import subprocess
import requests
import os
import time

class VirtualDeveloper:
    def __init__(self, model="qwen2.5-coder:0.5b"):
        self.model = model
        self.api_url = "http://localhost:11434/api/generate"
        self.workspace = "nexus_workspace"
        self.memory_path = "nexus_memory.json"
        os.makedirs(self.workspace, exist_ok=True)
        self.memory = self.load_memory()

    def load_memory(self):
        if os.path.exists(self.memory_path):
            with open(self.memory_path, 'r') as f:
                return json.load(f)
        return {"learned_fixes": {}, "project_history": [], "skills": []}

    def save_memory(self):
        with open(self.memory_path, 'w') as f:
            json.dump(self.memory, f, indent=4)

    def log_thought(self, thought):
        print(f"\033[94m[THOUGHT]\033[0m {thought}")
        with open("VIRTUAL_DEV_LOG.md", "a") as f:
            f.write(f"- {time.ctime()}: {thought}\n")

    def execute_with_reflection(self, task):
        self.log_thought(f"New mission received: {task}")
        
        # Step 1: Analyze & Plan
        plan = self.ask(f"Task: {task}. Create a 3-step execution plan using bash commands.")
        self.log_thought(f"Planning: {plan}")
        
        # Step 2: Iterative Execution
        commands = [c.strip() for c in plan.split("\n") if c.startswith("mkdir") or c.startswith("touch") or c.startswith("pip") or c.startswith("python")]
        
        for cmd in commands:
            success = False
            attempts = 0
            while not success and attempts < 3:
                attempts += 1
                self.log_thought(f"Attempting: {cmd}")
                result = subprocess.run(cmd, shell=True, capture_output=True, text=True, cwd=self.workspace)
                
                if result.returncode == 0:
                    self.log_thought("Action successful.")
                    success = True
                else:
                    self.log_thought(f"Action failed: {result.stderr}")
                    self.log_thought("Reflecting on error...")
                    # Ask AI for a fix based on memory
                    fix_prompt = f"Command '{cmd}' failed with error: {result.stderr}. Suggest a fix command."
                    cmd = self.ask(fix_prompt, system="Output ONLY the corrected bash command.")
            
            if not success:
                self.log_thought("Critical failure. Aborting step.")
                break

    def ask(self, prompt, system=""):
        payload = {"model": self.model, "prompt": prompt, "system": system, "stream": False}
        try:
            r = requests.post(self.api_url, json=payload, timeout=60)
            return r.json().get("response", "").strip()
        except:
            return "Error connecting to brain."

if __name__ == "__main__":
    dev = VirtualDeveloper()
    import sys
    if len(sys.argv) > 1:
        dev.execute_with_reflection(" ".join(sys.argv[1:]))
