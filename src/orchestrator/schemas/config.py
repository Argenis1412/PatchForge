"""Target configuration: loading, detection, and validation of workspace paths."""

__all__ = [
    "LEGACY_SCHEMA_VERSION",
    "SCHEMA_VERSION",
    "ProviderModelConfig",
    "ProvidersConfig",
    "TargetCapabilities",
    "TargetConfig",
    "ValidatorConfig",
    "ValidatorRole",
    "default_workspace_path",
    "detect_capabilities",
    "validate_workspace_path",
]

import hashlib
import json
import logging
import os
from enum import Enum
from pathlib import Path
from typing import List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from orchestrator.git import resolve_git_root as _resolve_git_root

logger = logging.getLogger(__name__)

SCHEMA_VERSION = "2.0"
LEGACY_SCHEMA_VERSION = "1.0"


class ValidatorRole(str, Enum):
    LINT = "lint"
    TEST = "test"
    TYPECHECK = "typecheck"


_FIXED_ADAPTER_ROLES: dict[str, tuple[ValidatorRole, ...]] = {
    "ruff": (ValidatorRole.LINT,),
    "flake8": (ValidatorRole.LINT,),
    "pylint": (ValidatorRole.LINT,),
    "pytest": (ValidatorRole.TEST,),
    "unittest": (ValidatorRole.TEST,),
    "mypy": (ValidatorRole.TYPECHECK,),
    "tsc": (ValidatorRole.TYPECHECK,),
}


class ValidatorConfig(BaseModel):
    """A V2 declaration of one ordered validator instance."""

    model_config = ConfigDict(extra="forbid")

    id: str
    adapter: Literal[
        "ruff", "pytest", "tsc", "flake8", "mypy", "pylint", "unittest", "tox", "command"
    ]
    roles: Optional[List[ValidatorRole]] = None
    command: Optional[List[str]] = None
    success_codes: List[int] = Field(default_factory=lambda: [0])

    @field_validator("id")
    @classmethod
    def _validate_id(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Validator id must not be empty")
        return value

    @field_validator("command")
    @classmethod
    def _validate_command(cls, value: Optional[List[str]]) -> Optional[List[str]]:
        invalid_command = value is not None and (
            not value or any(not isinstance(arg, str) or not arg for arg in value)
        )
        if invalid_command:
            raise ValueError("Validator command must contain non-empty string arguments")
        return value

    @field_validator("success_codes")
    @classmethod
    def _validate_success_codes(cls, value: List[int]) -> List[int]:
        if not value or len(set(value)) != len(value):
            raise ValueError("success_codes must be a non-empty list of unique integers")
        return value

    @model_validator(mode="after")
    def _validate_adapter_contract(self) -> "ValidatorConfig":
        if self.roles is not None and len(set(self.roles)) != len(self.roles):
            raise ValueError("Validator roles must be unique")
        fixed_roles = _FIXED_ADAPTER_ROLES.get(self.adapter)
        if fixed_roles is not None:
            if self.roles is None:
                self.roles = list(fixed_roles)
            elif tuple(self.roles) != fixed_roles:
                expected = [role.value for role in fixed_roles]
                raise ValueError(f"{self.adapter} roles must be {expected}")
        elif self.adapter == "command":
            if self.command is None or not self.roles:
                raise ValueError("command requires command and roles")
        elif self.adapter == "tox" and not self.roles:
            raise ValueError("tox requires one or more declared roles")
        return self


def _workspace_hash(root_path: Path) -> str:
    path_str = root_path.as_posix()
    if os.name == "nt":
        path_str = path_str.lower()
    return hashlib.sha256(path_str.encode("utf-8")).hexdigest()[:12]


def default_workspace_path(target_path: Path) -> Path:
    repo_root = _resolve_git_root(target_path)
    return Path.home() / ".cache" / "patchforge" / "workspaces" / _workspace_hash(repo_root)


def _is_inside(child: Path, parent: Path) -> bool:
    child = Path(child).resolve()
    parent = Path(parent).resolve()
    try:
        child.relative_to(parent)
        return True
    except ValueError:
        return False


def validate_workspace_path(target_path: Path, workspace_path: Path) -> Path:
    target_root = _resolve_git_root(target_path)
    resolved_workspace = Path(workspace_path).expanduser().resolve()
    if _is_inside(resolved_workspace, target_root):
        raise ValueError(
            f"Workspace path must be outside the target repository: {resolved_workspace}"
        )
    return resolved_workspace


class ProviderModelConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    model: Optional[str] = None

    @field_validator("model", mode="before")
    @classmethod
    def _strip_model(cls, v: str | None) -> str | None:
        return v.strip() if isinstance(v, str) else v


class ProvidersConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    gemini: ProviderModelConfig = Field(default_factory=ProviderModelConfig)
    openrouter: ProviderModelConfig = Field(default_factory=ProviderModelConfig)
    claude: ProviderModelConfig = Field(default_factory=ProviderModelConfig)


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
    schema_version: Literal[LEGACY_SCHEMA_VERSION, SCHEMA_VERSION] = SCHEMA_VERSION
    target_path: Path
    workspace_path: Path
    ignore_dirs: List[str] = [
        "node_modules",
        ".venv",
        "__pycache__",
        ".git",
        "workspace",
        ".ruff_cache",
        ".pytest_cache",
    ]
    extensions: List[str] = [".py", ".ts", ".tsx", ".js"]

    # Custom commands overrides
    lint_command: Optional[List[str]] = None
    test_command: Optional[List[str]] = None
    typecheck_command: Optional[List[str]] = None
    validator_timeout: Optional[int] = Field(default=None, gt=0)
    validators: Optional[List[ValidatorConfig]] = None

    providers: ProvidersConfig = Field(default_factory=ProvidersConfig)

    capabilities: TargetCapabilities = Field(default_factory=TargetCapabilities)

    @model_validator(mode="after")
    def _validate_workspace_is_external(self) -> "TargetConfig":
        self.workspace_path = validate_workspace_path(self.target_path, self.workspace_path)
        if self.validators is not None:
            ids = [validator.id for validator in self.validators]
            if len(ids) != len(set(ids)):
                raise ValueError("Validator ids must be unique")
        return self

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
        capabilities_overrides: Optional[dict] = None,
        validator_timeout: Optional[int] = None,
    ) -> "TargetConfig":
        """
        Loads configuration by merging priority levels:
        1. Explicit parameters passed to this function (CLI overrides)
        2. Config file 'orchestrator.json' at target_path
        3. Auto-detected values and defaults
        """
        target_path = Path(target_path).resolve()

        # 1. Start with defaults & auto-detect capabilities
        detected_caps = detect_capabilities(
            target_path,
            ignore_dirs
            or [
                "node_modules",
                ".venv",
                "__pycache__",
                ".git",
                "workspace",
                ".ruff_cache",
                ".pytest_cache",
            ],
        )

        default_workspace = default_workspace_path(target_path)

        config_data = {
            "schema_version": LEGACY_SCHEMA_VERSION,
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
            except (OSError, json.JSONDecodeError) as exc:
                raise ValueError(f"Invalid orchestrator.json: {exc}") from exc

            if not isinstance(file_data, dict):
                raise ValueError("Invalid orchestrator.json: root value must be an object")

            version = file_data.get("schema_version", LEGACY_SCHEMA_VERSION)
            if version not in (LEGACY_SCHEMA_VERSION, SCHEMA_VERSION):
                raise ValueError(f"Unsupported orchestrator.json schema_version: {version!r}")
            if version == LEGACY_SCHEMA_VERSION and "validators" in file_data:
                raise ValueError("validators requires orchestrator.json schema_version 2.0")

            allowed_keys = {
                "schema_version",
                "workspace_path",
                "ignore_dirs",
                "extensions",
                "lint_command",
                "test_command",
                "typecheck_command",
                "validator_timeout",
                "providers",
                "capabilities",
            }
            if version == SCHEMA_VERSION:
                allowed_keys.add("validators")
            unknown_keys = set(file_data) - allowed_keys
            if unknown_keys and version == SCHEMA_VERSION:
                names = ", ".join(sorted(unknown_keys))
                raise ValueError(f"Invalid orchestrator.json: unknown fields: {names}")

            config_data["schema_version"] = version
            for key in allowed_keys - {"schema_version", "capabilities"}:
                if key in file_data and file_data[key] is not None:
                    if key == "workspace_path":
                        config_data[key] = Path(file_data[key]).expanduser().resolve()
                    else:
                        config_data[key] = file_data[key]

            if "capabilities" in file_data:
                if not isinstance(file_data["capabilities"], dict):
                    raise ValueError("Invalid orchestrator.json: capabilities must be an object")
                for cap_key, val in file_data["capabilities"].items():
                    stripped = cap_key.replace("effective_", "").replace("detected_", "")
                    eff_key = f"effective_{stripped}"
                    if not hasattr(detected_caps, eff_key):
                        raise ValueError(
                            f"Invalid orchestrator.json: unknown capability {cap_key!r}"
                        )
                    setattr(detected_caps, eff_key, bool(val))

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
        if validator_timeout is not None:
            config_data["validator_timeout"] = validator_timeout

        # Env var fallback: PATCHFORGE_VALIDATOR_TIMEOUT (only if not already set)
        if config_data.get("validator_timeout") is None:
            env_val = os.environ.get("PATCHFORGE_VALIDATOR_TIMEOUT")
            if env_val is not None:
                try:
                    parsed = int(env_val)
                    if parsed > 0:
                        config_data["validator_timeout"] = parsed
                    else:
                        logger.warning("PATCHFORGE_VALIDATOR_TIMEOUT must be > 0, ignoring")
                except ValueError:
                    logger.warning("PATCHFORGE_VALIDATOR_TIMEOUT is not a valid integer, ignoring")

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
        for _root, dirs, files in os.walk(target_path):
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
        has_tests = (
            (target_path / "tests").is_dir()
            or (target_path / "test").is_dir()
            or (target_path / "pytest.ini").exists()
        )
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
