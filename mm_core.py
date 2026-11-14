from __future__ import annotations

import builtins
import json
import os
import re
import sys
import uuid
import zipfile
from typing import Optional, Dict, List, Union

def _import_or_install(module_name: str, pip_name: str):
    import importlib
    import subprocess
    try:
        return importlib.import_module(module_name)
    except ImportError:
        subprocess.check_call([sys.executable, "-m", "pip", "install", pip_name],
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return importlib.import_module(module_name)

try:
    Image = _import_or_install("PIL.Image", "pillow")
    PIL_AVAILABLE = True
except Exception:
    Image = None
    PIL_AVAILABLE = False

try:
    _import_or_install("javalang", "javalang")
    pass
except Exception as _exc:
    raise SystemExit("ModMorpher needs the 'javalang' package (pip install javalang): %s" % _exc)

class _State:

    def __init__(self):
        self.reset()

    def reset(self) -> None:

        self.all_java_files: Dict[str, str] = {}

        self.deobfuscated_java_files: Dict[str, str] = {}

        self.deobfuscated_java_paths: Dict[str, str] = {}

        self.rp_asset_index: Dict[str, Union[list, dict]] = {
            "textures": [],
            "geometry": [],
            "flipbook_textures": {}
        }

        self.collected_sound_defs: Dict[str, dict] = {}

        self.layerdef_geo_map: Dict[str, str] = {}

        self.detected_mc_version: Optional[str] = None

        self.renderer_map: Dict[str, Dict] = {}

        self.goal_parent_map: Dict[str, str] = {}

        self.goal_map_built: bool = False

        self.entity_source_map: Dict[str, str] = {}

        self.inheritance_graph: Dict[str, str] = {}

        self.porting_notes: list = []

        self.entity_registry: Dict[str, str] = {}

        self.attrs_registry: Dict[str, dict] = {}

        self.sound_const_map: Dict[str, str] = {}

STATE = _State()

Tool_Version = "1.6.1.5 'The Great Warning"
DEBUG_MODE = os.environ.get('MODMORPHER_DEBUG', '0') == '1'

_REAL_PRINT = builtins.print
def _silent_print(*args, **kwargs):
    return None

if not DEBUG_MODE:
    builtins.print = _silent_print

class _SilentStream:
    def write(self, text):
        return len(text)

    def flush(self):
        return None

if not DEBUG_MODE:
    sys.stderr = _SilentStream()

PROGRESS_AVAILABLE = True

class _ProgressBar:
    _CYAN   = "\033[36m"
    _GREEN  = "\033[32m"
    _DIM    = "\033[2m"
    _RESET  = "\033[0m"
    _BAR_W  = 40

    def __init__(self, message: str = "", max: int = 1):
        self.message = message
        self.max = max if max and max > 0 else 1
        self.index = 0
        self.suffix = ""
        self._render()

    def _render(self, final: bool = False):
        filled = int(self._BAR_W * self.index / self.max)
        filled = max(0, min(self._BAR_W, filled))
        empty  = self._BAR_W - filled

        bar_filled = self._CYAN + "━" * filled + self._RESET
        bar_empty  = self._DIM  + "╌" * empty  + self._RESET

        percent = int(100 * self.index / self.max)
        count   = f"{self.index}/{self.max}"

        label_colour = self._GREEN if final else self._CYAN
        label = f"{label_colour}{self.message}{self._RESET}"

        suffix_part = f"  {self._DIM}{self.suffix}{self._RESET}" if self.suffix else ""

        line = f"\r\033[K  {label:<40} {bar_filled}{bar_empty}  {percent:3d}%  {count}{suffix_part}"

        sys.stdout.write(line + ("\n" if final else ""))
        sys.stdout.flush()

    def next(self, n: int = 1):
        self.index = min(self.max, self.index + n)
        self._render(final=self.index >= self.max)

    def finish(self):
        self.index = self.max
        self._render(final=True)

class _ProgressLogger:
    def __init__(self):
        self._original_print = _REAL_PRINT
        self._active_bar = None
        self._intercepting = False

    def write(self, *args, **kwargs):
        return None

    def warn(self, text: str):
        return None

    def error(self, text: str):
        return None

    class _Phase:
        def __init__(self, logger, desc, total, unit, colour):
            self._logger = logger
            self._desc = desc
            self._total = total
            self._unit = unit
            self._colour = colour
            self._bar = None

        def __enter__(self):
            self._bar = _ProgressBar(self._desc, max=self._total if self._total > 0 else 1)
            self._bar.suffix = f"0/{self._bar.max} {self._unit}"
            self._logger._active_bar = self._bar
            self._logger._intercepting = True
            builtins.print = self._logger.write
            return self

        def __exit__(self, *_):
            builtins.print = self._logger._original_print
            self._logger._intercepting = False
            self._logger._active_bar = None
            if self._bar is not None:
                try:
                    if getattr(self._bar, 'index', 0) < getattr(self._bar, 'max', 1):
                        self._bar.next(getattr(self._bar, 'max', 1) - getattr(self._bar, 'index', 0))
                    elif hasattr(self._bar, 'finish'):
                        self._bar.finish()
                except Exception:
                    pass

        def update(self, n: int = 1):
            if self._bar:
                self._bar.next(n)

        def set_postfix_str(self, s: str):
            if self._bar:
                self._bar.suffix = s

        def set_description(self, s: str):
            if self._bar:
                self._bar.message = s

    def phase(self, desc: str, total: int = 0,
              unit: str = "file", colour: str = "cyan"):
        return self._Phase(self, desc, total, unit, colour)

_logger = _ProgressLogger()

def _warn(message: str) -> None:

    text = str(message)
    try:
        STATE.porting_notes.append(f"[warning] {text}")
    except NameError:

        pass

def _safe_rp_write(desc, path, data):
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        if DEBUG_MODE:
            _REAL_PRINT(f"[RP] wrote {desc}: {path}")
    except Exception as e:
        _REAL_PRINT(f"[ERROR] Failed to write {desc} to {path}: {e}")

def log_critical_failure(message: str):
    porting_notes_path = "PORTING_NOTES.txt"
    with open(porting_notes_path, "a", encoding="utf-8") as f:
        f.write(f"CRITICAL FAILURE: {message}\n")

OUTPUT_DIR = "Bedrock_Pack"
BP_FOLDER = os.path.join(OUTPUT_DIR, "bp")
RP_FOLDER = os.path.join(OUTPUT_DIR, "rp")
BP_RP_FORMAT_VERSION       = "1.26.20"
BP_ITEM_FORMAT_VERSION     = "1.26.20"
BP_RECIPE_FORMAT_VERSION   = "1.26.20"
RP_ENTITY_FORMAT_VERSION   = "1.26.20"
RP_LEGACY_RENDER_FORMAT = "1.10.0"
RP_LEGACY_ANIM_FORMAT = "1.10.0"
VALID_ICON_SIZES = [2, 4, 8, 16, 32, 64, 128, 256]
JAVA_GOAL_PRIORITIES = {
    "FloatGoal": 0, "SwimGoal": 0, "BreatheAirGoal": 0,
    "NearestAttackableTargetGoal": 1, "NearestAttackableTargetExpiringGoal": 1,
    "ToggleableNearestAttackableTargetGoal": 1, "NonTamedTargetGoal": 1,
    "DefendVillageTargetGoal": 1, "HurtByTargetGoal": 2,
    "OwnerHurtByTargetGoal": 2, "OwnerHurtTargetGoal": 2, "ResetAngerGoal": 2,
    "MeleeAttackGoal": 3, "OcelotAttackGoal": 3, "CreeperSwellGoal": 3,
    "RangedAttackGoal": 3, "RangedBowAttackGoal": 3, "RangedCrossbowAttackGoal": 3,
    "LeapAtTargetGoal": 4, "MoveTowardsTargetGoal": 4,
    "AvoidEntityGoal": 5, "PanicGoal": 5, "RunAroundLikeCrazyGoal": 5,
    "FleeSunGoal": 5, "RestrictSunGoal": 5,
    "OpenDoorGoal": 6, "InteractDoorGoal": 6, "BreakDoorGoal": 6,
    "BreakBlockGoal": 6, "UseItemGoal": 6,
    "FollowOwnerGoal": 7, "FollowParentGoal": 7, "FollowMobGoal": 7,
    "FollowBoatGoal": 7, "FollowSchoolLeaderGoal": 7, "LlamaFollowCaravanGoal": 7,
    "LandOnOwnersShoulderGoal": 7, "MoveToBlockGoal": 7,
    "MoveTowardsRestrictionGoal": 7, "MoveThroughVillageGoal": 7,
    "MoveThroughVillageAtNightGoal": 7, "MoveTowardsRaidGoal": 7,
    "ReturnToVillageGoal": 7, "PatrolVillageGoal": 7, "FindWaterGoal": 7,
    "SitWhenOrderedToGoal": 7, "SitGoal": 7,
    "BreedGoal": 8, "TemptGoal": 8, "EatGrassGoal": 8, "BegGoal": 8,
    "TradeWithPlayerGoal": 8, "LookAtCustomerGoal": 8, "ShowVillagerFlowerGoal": 8,
    "TriggerSkeletonTrapGoal": 8, "DolphinJumpGoal": 8, "JumpGoal": 8,
    "CatLieOnBedGoal": 8, "CatSitOnBlockGoal": 8,
    "WaterAvoidingRandomStrollGoal": 8, "RandomWalkingGoal": 8,
    "RandomSwimmingGoal": 8, "RandomStrollGoal": 8,
    "LookAtGoal": 9, "LookAtPlayerGoal": 9, "LookAtWithoutMovingGoal": 9,
    "LookRandomlyGoal": 10, "RandomLookAroundGoal": 10,
}
_ENTITY_SOUND_EVENTS: Dict[str, dict] = {}
def ensure_dirs():
    rp_subs = [
        "textures",
        "textures/blocks",
        "textures/items",
        "textures/entity",
        "sound",
        "sounds",
        "models",
        "animations",
        "items",
        "entity",
        "render_controllers",
        "geometry",
        "lang",
        "assets",
        "misc",
        "biome_modifiers",
        "dimensions"
    ]
    bp_subs = [
        "entities",
        "items",
        "blocks",
        "functions",
        "scripts",
        "animations",
        "data",
        "recipes",
        "loot_tables",
        "dimensions"
    ]
    for folder, subs in [(RP_FOLDER, rp_subs), (BP_FOLDER, bp_subs)]:
        os.makedirs(folder, exist_ok=True)
        for s in subs:
            os.makedirs(os.path.join(folder, s), exist_ok=True)
def create_manifest(pack_name: str, pack_type: str, has_scripting: bool = False):
    manifest = {
        "format_version": 2,
        "header": {
            "name": pack_name,
            "description": f"{pack_name} converted pack",
            "uuid": str(uuid.uuid4()),
            "version": [1, 0, 0],
            "min_engine_version": [1, 21, 50]
        },
        "modules": [
            {
                "type": "resources" if pack_type == "RP" else "data",
                "uuid": str(uuid.uuid4()),
                "version": [1, 0, 0]
            }
        ]
    }
    if has_scripting and pack_type == "BP":
        manifest["modules"].append({
            "type": "script",
            "language": "javascript",
            "uuid": str(uuid.uuid4()),
            "version": [1, 0, 0],
            "entry": "scripts/main.js"
        })
    return manifest
def write_manifest_for(folder: str, pack_name: str, pack_type: str):
    path = os.path.join(folder, "manifest.json")
    os.makedirs(folder, exist_ok=True)
    scripts_dir = os.path.join(folder, "scripts")
    has_scripting = pack_type == "BP" and os.path.isdir(scripts_dir) and any(f.endswith(".js") for f in os.listdir(scripts_dir))
    manifest = create_manifest(pack_name, pack_type, has_scripting)
    if has_scripting:
        entry_scripts = [
            f for f in os.listdir(scripts_dir)
            if f.endswith(".js") and f != "main.js"
        ]
        main_js = os.path.join(scripts_dir, "main.js")
        if entry_scripts and not os.path.exists(main_js):
            imports = "\n".join(f'import "./{f}";' for f in sorted(entry_scripts))
            with open(main_js, "w", encoding="utf-8") as mf:
                mf.write(imports + "\n")
        dependencies = [
            {
                "module_name": "@minecraft/server",
                "version": "1.13.0"
            }
        ]
        dependencies.extend(collect_script_module_dependencies(scripts_dir))
        manifest["dependencies"] = dependencies
    with open(path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=4)

def sanitize_identifier(name: Optional[str]) -> str:
    if not name:
        return ""
    s = str(name).strip().lower()

    s = ''.join('_' if c.isspace() else c for c in s)

    s = ''.join(c if c.isalnum() or c in '._' else '_' for c in s)

    while '__' in s:
        s = s.replace('__', '_')

    while '..' in s:
        s = s.replace('..', '.')
    s = s.strip('._')
    return s
def clean_java_artifact_name(name: Optional[str]) -> str:

    if not name:
        return ""

    raw = str(name).strip()
    slug = re.sub(r'[^A-Za-z0-9]+', '', raw).lower()
    if not slug:
        return ""

    original = slug
    suffixes = (
        'entityanimationfactory',
        'modvariables',
        'mobeffect',
        'blockentity',
        'tileentity',
        'renderer',
        'factory',
        'feature',
        'entity',
        'block',
        'model',
        'screen',
        'menu',
        'item',
        'effect',
    )

    changed = True
    while changed:
        changed = False
        for suffix in suffixes:
            if slug.endswith(suffix) and len(slug) > len(suffix) + 2:
                candidate = slug[:-len(suffix)]
                if candidate:
                    slug = candidate
                    changed = True
                    break

    return sanitize_identifier(slug) or sanitize_identifier(original) or sanitize_identifier(raw)

_ENTITY_ARTIFACT_SKIP_MARKERS = (
    'mobeffect',
    'modvariables',
    'animation',
    'mixin',
    'utils',
    'utility',
    'effect',
    'helper',
)

def _should_skip_entity_artifact(java_code: str, filename: str = '', cls_name: Optional[str] = None) -> bool:
    haystack_parts = [
        cls_name or '',
        os.path.basename(filename) or '',
        os.path.splitext(os.path.basename(filename))[0] if filename else '',
        java_code[:400] if java_code else '',
    ]
    haystack = ' '.join(part for part in haystack_parts if part).lower()
    return any(marker in haystack for marker in _ENTITY_ARTIFACT_SKIP_MARKERS)

def sanitize_filename_keep_ext(filename: str) -> str:
    base, ext = os.path.splitext(filename)
    base_s = base.lower()

    base_s = base_s.replace(' ', '_').replace('-', '_')

    base_s = ''.join(c if c.isalnum() or c in '._' else '_' for c in base_s)

    while '__' in base_s:
        base_s = base_s.replace('__', '_')
    base_s = base_s.strip('._')
    ext_s = ext.lower()
    return base_s + ext_s
    return base_s + ext_s
def build_geometry_id(namespace: Optional[str], name: str) -> str:
    n = sanitize_identifier(name)
    if namespace:
        ns = sanitize_identifier(namespace)
        if ns:
            return f"geometry.{ns}.{n}"
    return f"geometry.{n}"
def collect_script_module_dependencies(scripts_dir: str) -> List[dict]:
    module_versions = {
        '@minecraft/common': '1.13.0',
        '@minecraft/debug-utilities': '1.13.0',
        '@minecraft/server-ui': '1.13.0',
        '@minecraft/server-net': '1.13.0',
        '@minecraft/server-admin': '1.13.0',
        '@minecraft/server-gametest': '1.13.0',
    }
    deps: List[dict] = []
    if not os.path.isdir(scripts_dir):
        return deps
    for filename in os.listdir(scripts_dir):
        if not filename.endswith('.js'):
            continue
        path = os.path.join(scripts_dir, filename)
        try:
            with open(path, 'r', encoding='utf-8') as f:
                content = f.read()
        except Exception:
            continue
        for module_name, version in module_versions.items():
            if module_name in content and not any(d['module_name'] == module_name for d in deps):
                deps.append({'module_name': module_name, 'version': version})
    return deps

def find_jar_file(search_dir=".") -> Optional[str]:
    SKIP_SUFFIXES = ("-sources.jar", "-javadoc.jar", "-api.jar", "-slim.jar", "-dev.jar")
    candidates = []
    for f in os.listdir(search_dir):
        if not f.endswith(".jar"):
            continue
        if any(f.lower().endswith(s) for s in SKIP_SUFFIXES):

            continue
        candidates.append(os.path.join(search_dir, f))
    if not candidates:
        return None
    if len(candidates) > 1:
        _warn(f"Warning: Multiple JAR files found: {[os.path.basename(c) for c in candidates]}")

    return candidates[0]
def detect_loader_from_jar(jar_path: str) -> str:
    try:
        with zipfile.ZipFile(jar_path, 'r') as jar:
            names_lower = [n.lower() for n in jar.namelist()]
            if any("meta-inf/neoforge.mods.toml" in n for n in names_lower):
                return "neoforge"
            if any("meta-inf/mods.toml" in n for n in names_lower):
                return "forge"
            if any("fabric.mod.json" in n for n in names_lower):
                return "fabric"
            if any("quilt.mod.json" in n for n in names_lower):
                return "quilt"
    except Exception:
        pass
    return "unknown"
def _extract_first_logo_from_jar_legacy(jar_path: str) -> Optional[str]:
    temp_dir = ".temp_logo_extract"
    try:
        with zipfile.ZipFile(jar_path, 'r') as jar:
            for file in jar.namelist():
                if file.lower().endswith("logo.png"):
                    jar.extract(file, temp_dir)
                    return os.path.join(temp_dir, file)
    except Exception:
        pass
    return None
def sanitize_path_parts(path_str: str) -> List[str]:
    parts = path_str.replace("\\", "/").split("/")
    if not parts:
        return []
    sanitized = []
    for p in parts[:-1]:
        sanitized.append(sanitize_identifier(p) or "_")
    sanitized.append(sanitize_filename_keep_ext(parts[-1]))
    return sanitized

def _safe_json_dump(path: str, data: object) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w', encoding='utf-8') as fh:
        json.dump(data, fh, indent=2, ensure_ascii=False)

def _ensure_main_import(main_path: str, import_line: str) -> None:
    os.makedirs(os.path.dirname(main_path), exist_ok=True)
    existing = ''
    if os.path.exists(main_path):
        with open(main_path, 'r', encoding='utf-8') as fh:
            existing = fh.read()
    if import_line.strip() not in existing:
        with open(main_path, 'w', encoding='utf-8') as fh:
            fh.write(import_line + ('\n' if not import_line.endswith('\n') else '') + existing)

def _read_text_file(path: str) -> str:
    try:
        with open(path, 'r', encoding='utf-8', errors='ignore') as fh:
            return fh.read()
    except Exception:
        return ''
