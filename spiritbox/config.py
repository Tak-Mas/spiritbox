"""SpiritBox project configuration. (P7: Convention over Configuration)"""

import shutil
import yaml
from dataclasses import dataclass
from pathlib import Path

SUPPORTED_LANGUAGES = {"ja": "ja-JP", "en": "en-US"}


@dataclass
class SpiritBoxConfig:
    language: str = "ja"
    duration_minutes: int = 0  # 0 = no constraint
    voice: str = ""
    voice_style: str = ""
    source_filename: str = ""  # original PPTX filename (set by init_project)
    context: str = ""  # rich context collected by Claude Code via AskUserQuestion

    @property
    def language_code(self) -> str:
        if self.language not in SUPPORTED_LANGUAGES:
            raise ValueError(
                f"Unsupported language: '{self.language}'. "
                f"Supported: {list(SUPPORTED_LANGUAGES.keys())}"
            )
        return SUPPORTED_LANGUAGES[self.language]

    @property
    def effective_voice(self) -> str:
        if self.voice:
            return self.voice
        return {"ja": "Aoede", "en": "Kore"}.get(self.language, "Kore")


def load_config(project_dir: str) -> SpiritBoxConfig:
    config_path = Path(project_dir) / "config.yaml"
    if config_path.exists():
        with open(config_path) as f:
            data = yaml.safe_load(f) or {}
        if not isinstance(data, dict):
            return SpiritBoxConfig()
        return SpiritBoxConfig(**{
            k: v for k, v in data.items()
            if k in SpiritBoxConfig.__dataclass_fields__
        })
    return SpiritBoxConfig()


def save_config(config: SpiritBoxConfig, project_dir: str):
    config_path = Path(project_dir) / "config.yaml"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "language": config.language,
        "duration_minutes": config.duration_minutes,
        "voice": config.voice,  # save raw value, not effective_voice
        "voice_style": config.voice_style,
        "source_filename": config.source_filename,
        "context": config.context,
    }
    with open(config_path, "w") as f:
        yaml.dump(data, f, allow_unicode=True, default_flow_style=False)


def get_source_pptx(project_dir: str, config: SpiritBoxConfig = None) -> Path:
    """Resolve the source PPTX path deterministically."""
    project = Path(project_dir)
    if config and config.source_filename:
        path = project / "source" / config.source_filename
        if path.exists():
            return path

    # fallback: glob (but warn if multiple)
    pptx_files = list((project / "source").glob("*.pptx"))
    if not pptx_files:
        raise FileNotFoundError("No PPTX found in source/ directory")
    if len(pptx_files) > 1:
        raise RuntimeError(
            f"Multiple PPTX files in source/: {[f.name for f in pptx_files]}. "
            "Set source_filename in config.yaml to specify which one."
        )
    return pptx_files[0]


def init_project(pptx_path: str, project_dir: str = None) -> str:
    """Initialize a SpiritBox project directory. (P7, P8)

    Copies the PPTX into source/ without modifying the original.
    Returns the project directory path.
    """
    pptx_path = Path(pptx_path).resolve()
    if not pptx_path.exists():
        raise FileNotFoundError(f"PPTX not found: {pptx_path}")

    if project_dir is None:
        project_dir = str(pptx_path.parent / f"{pptx_path.stem}_spiritbox")

    project = Path(project_dir)
    for subdir in ["source", "slides", "script", "audio", "output"]:
        (project / subdir).mkdir(parents=True, exist_ok=True)

    # P8: Non-Destructive — copy, never modify original
    dest = project / "source" / pptx_path.name
    if not dest.exists():
        shutil.copy2(str(pptx_path), str(dest))

    # Save config with source_filename for deterministic resolution
    config_path = project / "config.yaml"
    if not config_path.exists():
        config = SpiritBoxConfig(source_filename=pptx_path.name)
        save_config(config, str(project))

    return str(project)
