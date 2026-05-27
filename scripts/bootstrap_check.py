import sys
import importlib
from pathlib import Path
import os
from dotenv import load_dotenv

# Cargar env para validar variables
load_dotenv()

REQUIRED_PKGS = [
    "pydantic",
    "httpx",
    "dotenv",
    "google.genai",
    "anthropic"
]

REQUIRED_ENV = [
    "ANTHROPIC_API_KEY",
    "GOOGLE_API_KEY",
]

def check():
    print("--- Environment Health Check ---")
    print(f"Python: {sys.version}")
    
    # 1. Version Check (Allowing 3.12, 3.13, 3.14 for this environment)
    if sys.version_info < (3, 12):
        print("[FAIL] Python 3.12+ required")
        sys.exit(1)

        
    # 2. Package Check
    for pkg in REQUIRED_PKGS:
        try:
            importlib.import_module(pkg)
            print(f"[OK] {pkg}")
        except ImportError as e:
            print(f"[FAIL] {pkg} not installed: {e}")
            sys.exit(1)
            
    # 3. Env Check
    strict_env = "--strict-env" in sys.argv
    env_path = Path(".env")
    if not env_path.exists():
        msg = "[WARN] .env file missing"
        if strict_env:
            print(f"[FAIL] {msg}")
            sys.exit(1)
        print(msg)
    else:
        print("[OK] .env file found")
        for var in REQUIRED_ENV:
            if not os.getenv(var):
                print(f"[FAIL] Missing {var}")
                sys.exit(1)
            print(f"[OK] {var} present")

if __name__ == "__main__":
    check()
