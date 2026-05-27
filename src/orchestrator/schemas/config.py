import json
import os
from pathlib import Path
from typing import List, Optional

from pydantic import BaseModel, Field

SCHEMA_VERSION = "1.0"

class TargetCapabilities(BaseModel):
    detected_supports_python: bool = False
    detected_supports_typescript: bool = False
    detected_supports_tests: bool = False
    detected_supports_typecheck: bool = False

    effective_supports_python: bool = False
    effective_supports_typescript: bool = False
    effective_supports_tests: bool = False
    effective_supports_typecheck: bool = False


class TargetConfig(BaseModel):
    schema_version: str = SCHEMA_VERSION
    target_path: Path
    workspace_path: Path
    ignore_dirs: List[str] = ["node_modules", ".venv", "__pycache__", ".git", "workspace"]
    extensions: List[str] = [".py", ".ts", ".tsx", ".js"]
    
    # Custom commands overrides
    lint_command: Optional[List[str]] = None
    test_command: Optional[List[str]] = None
    typecheck_command: Optional[List[str]] = None
    
    capabilities: TargetCapabilities = Field(default_factory=TargetCapabilities)

    @classmethod
    def load(
        cls,
        target_path: Path,
        workspace_path: Optional[Path] = None,
        ignore_dirs: Optional[List[str]] = None,
        extensions: Optional[List[str]] = None,
        lint_command: Optional[List[str]] = None,
        test_command: Optional[List[str]] = None,
        typecheck_command: Optional[List[str]] = None,
        capabilities_overrides: Optional[dict] = None
    ) -> "TargetConfig":
        """
        Loads configuration by merging priority levels:
        1. Explicit parameters passed to this function (CLI overrides)
        2. Config file 'orchestrator.json' at target_path
        3. Auto-detected values and defaults
        """
        target_path = Path(target_path).resolve()
        
        # 1. Start with defaults & auto-detect capabilities
        detected_caps = detect_capabilities(target_path, ignore_dirs or ["node_modules", ".venv", "__pycache__", ".git", "workspace"])
        
        default_workspace = target_path / "workspace"
        
        config_data = {
            "target_path": target_path,
            "workspace_path": default_workspace,
            "capabilities": detected_caps,
        }

        # 2. Merge target's config file (orchestrator.json) if it exists
        config_file_path = target_path / "orchestrator.json"
        if config_file_path.exists():
            try:
                with open(config_file_path, "r", encoding="utf-8") as f:
                    file_data = json.load(f)
                    
                # Merge top-level config keys
                for key in ["workspace_path", "ignore_dirs", "extensions", "lint_command", "test_command", "typecheck_command"]:
                    if key in file_data and file_data[key] is not None:
                        if key == "workspace_path":
                            config_data[key] = Path(file_data[key]).resolve()
                        else:
                            config_data[key] = file_data[key]
                            
                # Merge capabilities overrides from file
                if "capabilities" in file_data and isinstance(file_data["capabilities"], dict):
                    for cap_key, val in file_data["capabilities"].items():
                        eff_key = f"effective_{cap_key.replace('effective_', '').replace('detected_', '')}"
                        if hasattr(detected_caps, eff_key):
                            setattr(detected_caps, eff_key, bool(val))
            except Exception as e:
                # If loading config fails, we proceed with defaults but log a warning
                print(f"[Warning] Failed to load config file: {e}")

        # 3. Apply CLI Overrides
        if workspace_path is not None:
            config_data["workspace_path"] = Path(workspace_path).resolve()
        if ignore_dirs is not None:
            config_data["ignore_dirs"] = ignore_dirs
        if extensions is not None:
            config_data["extensions"] = extensions
        if lint_command is not None:
            config_data["lint_command"] = lint_command
        if test_command is not None:
            config_data["test_command"] = test_command
        if typecheck_command is not None:
            config_data["typecheck_command"] = typecheck_command
            
        # Apply CLI capabilities overrides
        if capabilities_overrides:
            for cap_key, val in capabilities_overrides.items():
                eff_key = f"effective_{cap_key.replace('effective_', '').replace('detected_', '')}"
                if hasattr(detected_caps, eff_key):
                    setattr(detected_caps, eff_key, bool(val))

        config_data["capabilities"] = detected_caps
        return cls(**config_data)


def detect_capabilities(target_path: Path, ignore_dirs: List[str]) -> TargetCapabilities:
    target_path = Path(target_path).resolve()
    
    has_python = False
    has_typescript = False
    
    ignore_set = set(ignore_dirs)
    
    if target_path.exists():
        for root, dirs, files in os.walk(target_path):
            # Prune ignored directories in-place
            dirs[:] = [d for d in dirs if d not in ignore_set]
            for f in files:
                if f.endswith(".py"):
                    has_python = True
                if f.endswith(".ts") or f.endswith(".tsx"):
                    has_typescript = True
            if has_python and has_typescript:
                break

    # Test suite detection
    has_tests = False
    if has_python:
        # Standard pytest structures
        has_tests = (target_path / "tests").is_dir() or (target_path / "test").is_dir() or (target_path / "pytest.ini").exists()
    if has_typescript:
        package_json = target_path / "package.json"
        if package_json.exists():
            has_tests = True
            
    # Typecheck detection
    has_typecheck = has_typescript and (target_path / "tsconfig.json").exists()
    
    return TargetCapabilities(
        detected_supports_python=has_python,
        detected_supports_typescript=has_typescript,
        detected_supports_tests=has_tests,
        detected_supports_typecheck=has_typecheck,
        
        effective_supports_python=has_python,
        effective_supports_typescript=has_typescript,
        effective_supports_tests=has_tests,
        effective_supports_typecheck=has_typecheck,
    )
