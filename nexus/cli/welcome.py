"""Epic Startup for Nexus."""

import os
import time
import sys

def clear():
    os.system('clear' if os.name == 'posix' else 'cls')

def fade_print(text, delay=0.01):
    for char in text:
        sys.stdout.write(char)
        sys.stdout.flush()
        time.sleep(delay)
    print()

def get_logo():
    bold_cyan = "\033[1;36m"
    cyan = "\033[36m"
    blue = "\033[34m"
    reset = "\033[0m"
    
    return f"""
    {bold_cyan}░▒█║  ░▒█║ ░▒█║▀▀▀ ░▒█║  ░▒█║ ░▒█║  ░▒█║ ░▒█║▀▀▀
    {bold_cyan}░▒█║▀█░▒█║ ░▒█║▀▀  ░▒█║  ░▒█║ ░▒█║  ░▒█║ ░▒█║▀▀ 
    {cyan}░▒█║  ░▒█║ ░▒█║▄▄▄ ░▒█║▄█░▒█║ ░▒█║▄█░▒█║ ░▒█║▄▄▄
    {blue}▀▀▀▀  ▀▀▀▀ ▀▀▀▀▀▀▀  ▀▀▀ ▀▀▀▀   ▀▀▀ ▀▀▀▀  ▀▀▀▀▀▀▀
    {reset}"""

def display_welcome():
    clear()
    logo = get_logo()
    print(logo)
    
    cyan = "\033[36m"
    bold = "\033[1m"
    reset = "\033[0m"
    dim = "\033[90m"
    green = "\033[32m"
    
    fade_print(f"    {bold}N E X U S   O S   I N I T I A L I Z E D{reset}  {dim}[build 2026.04.22]{reset}", 0.005)
    print(f"    {dim}──────────────────────────────────────────────────────────{reset}")
    
    subsystems = [
        ("NEURAL", "Synaptic weights loaded"),
        ("MEMRY", "SQLite vector-mesh active"),
        ("TOOLS", "Execution registry online"),
        ("SYNC ", "Cross-device uplink standby"),
    ]
    
    for sub, msg in subsystems:
        time.sleep(0.1)
        print(f"    {cyan}[{sub}]{reset} {msg}... {green}OK{reset}")
    
    print(f"\n    {bold}Welcome to the Nexus.{reset}")
    print(f"    {dim}Enter a command or type{reset} {cyan}/help{reset} {dim}to begin the cycle.{reset}\n")

if __name__ == "__main__":
    display_welcome()
