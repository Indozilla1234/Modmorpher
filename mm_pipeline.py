"""Orchestration: Minecraft-version / Mojang-mapping lookup, deobfuscation, decompiler
invocation, pre-scan, the conversion pipeline, orphan pruning, validation and main()."""
from __future__ import annotations

import json
import csv
import os
import re
import shutil
import subprocess
import zipfile
import io
import xml.etree.ElementTree as ET
import urllib.request
import javalang
from typing import Optional, Dict, Set, List
from mm_core import (
    BP_FOLDER,
    OUTPUT_DIR,
    RP_FOLDER,
    STATE,
    _REAL_PRINT,
    _ensure_main_import,
    _logger,
    _read_text_file,
    _safe_json_dump,
    _should_skip_entity_artifact,
    _warn,
    clean_java_artifact_name,
    detect_loader_from_jar,
    ensure_dirs,
    find_jar_file,
    log_critical_failure,
    sanitize_identifier,
    write_manifest_for,
)
from mm_java import (
    GlobalCapabilityRegistry,
    JavaAST,
    JavaGUIConverter,
    RecursiveNBTSerializer,
    _jtok,
    _rewrite_java_identifiers,
    extract_class_name,
    get_unit,
    identifier_set,
    iter_calls,
    iter_nodes,
    jt,
    read_all_java_files,
    rename_identifiers,
    simple_type_name,
    string_value,
    type_args,
)
from mm_assets import (
    _is_java_texture_pack,
    build_rp_asset_index,
    convert_java_texture_pack,
    convert_lang_files,
    copy_assets_from_jar,
    copy_geckolib_animations_from_jar,
    ensure_and_fix_pack_icon,
    extract_logo_from_jar,
    fix_animation_format_versions,
    generate_sounds_registry,
    generate_texture_registry,
    load_animation_keys,
    load_geometry_identifiers,
    normalise_all_geometry_to_geckolib,
    normalize_geometry_file_identifiers,
    sanitize_animation_keys_in_files,
    sanitize_sound_key,
    scan_and_convert_layerdefinition_models,
)
from mm_entities import (
    ENTITY_OVERRIDE_KEYWORDS,
    NON_ENTITY_KEYWORDS,
    _is_sound_artifact,
    build_geckolib_mappings,
    build_goal_inheritance_map,
    build_renderer_entity_map,
    convert_java_to_bedrock,
    extract_attributes_from_java,
    generate_sound_playback_script,
    is_likely_entity,
    process_loot_tables_from_jar,
)
from mm_content import (
    block_content_signals,
    build_inheritance_graph,
    convert_java_block_full,
    convert_java_item_full,
    entity_registration_name,
    extract_item_tags_from_jar,
    generate_bedrock_runtime_bridge,
    item_content_signals,
    process_recipes_from_jar,
    process_structures_from_jar,
    resolve_superchain,
    scan_block_registrations,
    scan_capabilities,
    scan_client_classes,
    scan_mixins,
    scan_networking,
    write_porting_notes,
)

def detect_minecraft_version(jar_path: Optional[str] = None) -> Optional[str]:

    version_re = re.compile(r'\b(1\.\d{1,2}(?:\.\d{1,2})?)\b')

    def _first_match(text: str) -> Optional[str]:
        m = version_re.search(text)
        return m.group(1) if m else None

    if jar_path and os.path.isfile(jar_path):
        try:
            with zipfile.ZipFile(jar_path, 'r') as z:

                for entry in z.namelist():
                    if entry.upper().endswith('MANIFEST.MF'):
                        manifest = z.read(entry).decode('utf-8', errors='ignore')
                        for line in manifest.splitlines():
                            kl = line.lower()
                            if any(k in kl for k in ('minecraft-version', 'implementation-version',
                                                       'forge-version', 'fabric-version')):
                                v = _first_match(line)
                                if v:
                                    return v

                for entry in z.namelist():
                    el = entry.lower()
                    if el.endswith('mods.toml') or el.endswith('neoforge.mods.toml'):
                        text = z.read(entry).decode('utf-8', errors='ignore')

                        for pat in [
                            r'modId\s*=\s*["\']minecraft["\']\s*.*?versionRange\s*=\s*["\']([^"\']+)["\']',
                            r'minecraft\s*=\s*["\']([^"\']+)["\']',
                        ]:
                            m = re.search(pat, text, re.DOTALL | re.IGNORECASE)
                            if m:
                                v = _first_match(m.group(1))
                                if v:
                                    return v

                for entry in z.namelist():
                    if entry.lower().endswith('fabric.mod.json'):
                        data = json.loads(z.read(entry).decode('utf-8', errors='ignore'))
                        mc = (data.get('depends') or {}).get('minecraft', '')
                        v = _first_match(str(mc))
                        if v:
                            return v

                for entry in z.namelist():
                    if entry.lower().endswith('quilt.mod.json'):
                        data = json.loads(z.read(entry).decode('utf-8', errors='ignore'))
                        mc = (data.get('quilt_loader', {}).get('depends') or [])
                        for dep in mc:
                            if isinstance(dep, dict) and dep.get('id') == 'minecraft':
                                v = _first_match(str(dep.get('versions', '')))
                                if v:
                                    return v
        except Exception:
            pass

    for root, _, files in os.walk('.'):
        for fname in files:
            fpath = os.path.join(root, fname)
            try:
                if fname in ('gradle.properties',):
                    text = open(fpath, encoding='utf-8', errors='ignore').read()
                    for pat in [
                        r'minecraft_version\s*=\s*([0-9][^\s#]+)',
                        r'minecraftVersion\s*=\s*([0-9][^\s#]+)',
                        r'mc_version\s*=\s*([0-9][^\s#]+)',
                    ]:
                        m = re.search(pat, text, re.IGNORECASE)
                        if m:
                            v = _first_match(m.group(1))
                            if v:
                                return v
                if fname in ('build.gradle', 'build.gradle.kts'):
                    text = open(fpath, encoding='utf-8', errors='ignore').read()
                    for pat in [
                        r"minecraft\s+['\"]([0-9][^'\"]+)['\"]",
                        r"mc_version\s*=\s*['\"]([0-9][^'\"]+)['\"]",
                        r"minecraft_version\s*=\s*['\"]([0-9][^'\"]+)['\"]",
                    ]:
                        m = re.search(pat, text, re.IGNORECASE)
                        if m:
                            v = _first_match(m.group(1))
                            if v:
                                return v
                if fname in ('mods.toml', 'neoforge.mods.toml'):
                    text = open(fpath, encoding='utf-8', errors='ignore').read()
                    for pat in [
                        r'modId\s*=\s*["\']minecraft["\']\s*.*?versionRange\s*=\s*["\']([^"\']+)["\']',
                        r'minecraft\s*=\s*["\']([^"\']+)["\']',
                    ]:
                        m = re.search(pat, text, re.DOTALL | re.IGNORECASE)
                        if m:
                            v = _first_match(m.group(1))
                            if v:
                                return v
                if fname == 'fabric.mod.json':
                    data = json.load(open(fpath, encoding='utf-8'))
                    mc = (data.get('depends') or {}).get('minecraft', '')
                    v = _first_match(str(mc))
                    if v:
                        return v
            except Exception:
                continue
    return None

_MOJANG_VERSION_MANIFEST = "https://piston-meta.mojang.com/mc/game/version_manifest_v2.json"
_MAPPING_CACHE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".mapping_cache")

def _fetch_url(url: str, timeout: int = 30) -> Optional[bytes]:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "ModMorpher/1.0"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read()
    except Exception:
        return None

def _cached_path(mc_version: str, side: str = "client") -> str:
    os.makedirs(_MAPPING_CACHE_DIR, exist_ok=True)
    return os.path.join(_MAPPING_CACHE_DIR, f"{side}_{mc_version}.txt")

def download_mojang_mappings(mc_version: str, side: str = "client") -> Optional[str]:
    """Download Mojang's official ProGuard mapping text for one Minecraft side."""
    if side not in ("client", "server"):
        raise ValueError(f"Unsupported Mojang mapping side: {side}")

    cache_file = _cached_path(mc_version, side)
    if os.path.isfile(cache_file):
        try:
            cached = open(cache_file, encoding="utf-8", errors="ignore").read()
            if " -> " in cached and re.search(r"(?m)^[^\s#].* -> [^\s:]+:$", cached):
                return cached
        except OSError:
            pass

    manifest_data = _fetch_url(_MOJANG_VERSION_MANIFEST)
    if not manifest_data:
        _warn(f"Could not download Mojang version manifest for Minecraft {mc_version}.")
        return None
    try:
        manifest = json.loads(manifest_data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        _warn(f"Mojang version manifest was invalid: {exc}")
        return None

    version_url = next(
        (entry.get("url") for entry in manifest.get("versions", [])
         if entry.get("id") == mc_version),
        None,
    )
    if not version_url:
        _warn(f"Minecraft version {mc_version} was not found in Mojang's version manifest.")
        return None

    version_data = _fetch_url(version_url)
    if not version_data:
        _warn(f"Could not download Minecraft {mc_version} version metadata.")
        return None
    try:
        version_json = json.loads(version_data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        _warn(f"Minecraft {mc_version} version metadata was invalid: {exc}")
        return None

    mappings_url = (version_json.get("downloads", {}).get(f"{side}_mappings", {}) or {}).get("url")
    if not mappings_url:
        _warn(f"Mojang did not publish {side} mappings for Minecraft {mc_version}.")
        return None

    mapping_data = _fetch_url(mappings_url, timeout=120)
    if not mapping_data:
        _warn(f"Could not download Mojang {side} mappings for Minecraft {mc_version}.")
        return None

    mapping_text = mapping_data.decode("utf-8", errors="replace")
    if " -> " not in mapping_text:
        _warn(f"Downloaded Mojang {side} mappings for {mc_version}, but the file did not look valid.")
        return None
    try:
        with open(cache_file, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(mapping_text)
    except OSError as exc:
        _warn(f"Could not cache Mojang mappings: {exc}")
    return mapping_text

_FORGE_MCP_CONFIG_METADATA = (
    "https://maven.minecraftforge.net/de/oceanlabs/mcp/mcp_config/maven-metadata.xml"
)
_FORGE_MCP_CONFIG_BASE = "https://maven.minecraftforge.net/de/oceanlabs/mcp/mcp_config"

_MCPCONFIG_TSRG_URLS = (
    "https://raw.githubusercontent.com/MinecraftForge/MCPConfig/master/versions/release/{v}/joined.tsrg",
    "https://raw.githubusercontent.com/MinecraftForge/MCPConfig/master/versions/pre/{v}/joined.tsrg",
)
_PRIMITIVE_DESC = {"int": "I", "long": "J", "float": "F", "double": "D", "boolean": "Z",
                   "byte": "B", "short": "S", "char": "C", "void": "V"}
_SRG_ID_PREFIXES = ("m_", "f_")

def _srg_cache_path(mc_version: str) -> str:

    os.makedirs(_MAPPING_CACHE_DIR, exist_ok=True)
    return os.path.join(_MAPPING_CACHE_DIR, f"srg_to_mojmap_v4_{mc_version}.json")

def _mcp_config_versions(mc_version: str) -> List[str]:
    """Return timestamped Forge MCPConfig artifact versions for one MC version."""
    metadata = _fetch_url(_FORGE_MCP_CONFIG_METADATA, timeout=60)
    versions: List[str] = []
    if metadata:
        try:
            root = ET.fromstring(metadata)
            versions = [
                node.text.strip()
                for node in root.iter()
                if node.tag.rsplit("}", 1)[-1] == "version"
                and node.text and node.text.strip().startswith(mc_version + "-")
            ]
        except ET.ParseError as exc:
            _warn(f"Forge MCPConfig Maven metadata could not be parsed: {exc}")
    versions = sorted(set(versions), reverse=True)

    if mc_version not in versions:
        versions.append(mc_version)

    if mc_version == "1.19.2" and "1.19.2-20220805.130853" not in versions:
        versions.insert(0, "1.19.2-20220805.130853")
    return versions

def download_srg2off_archive(mc_version: str) -> Optional[bytes]:
    """Download Forge's pre-joined SRG-to-official CSV mapping archive.

    MCPConfig publishes this as the `srg2off` Maven classifier. It already
    combines joined.tsrg with Mojang's client/server mappings, avoiding a
    hand-built join in ModMorpher.
    """
    cache = os.path.join(_MAPPING_CACHE_DIR, f"srg2off_{mc_version}.jar")
    if os.path.isfile(cache):
        try:
            data = open(cache, "rb").read()
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                has_csv = any(name.lower().endswith(".csv") for name in archive.namelist())
            if has_csv and len(parse_srg2off_archive(data)) >= 100:
                return data
        except (OSError, zipfile.BadZipFile):
            pass

    attempted_versions = _mcp_config_versions(mc_version)
    for artifact_version in attempted_versions:
        artifact_name = f"mcp_config-{artifact_version}-srg2off.jar"
        url = f"{_FORGE_MCP_CONFIG_BASE}/{artifact_version}/{artifact_name}"
        data = _fetch_url(url, timeout=120)
        if not data:
            continue
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                csv_files = [name for name in archive.namelist() if name.lower().endswith(".csv")]
                if not csv_files:
                    continue

            parsed_count = len(parse_srg2off_archive(data))
            if parsed_count < 100:
                _warn(f"Forge mapping artifact {artifact_version} contained only {parsed_count} usable SRG rows.")
                continue
            os.makedirs(_MAPPING_CACHE_DIR, exist_ok=True)
            with open(cache, "wb") as fh:
                fh.write(data)
            _REAL_PRINT(f"  [Mappings] Downloaded Forge SRG-to-official CSVs ({artifact_version})")
            return data
        except (zipfile.BadZipFile, OSError):
            continue

    tried = ", ".join(attempted_versions) if attempted_versions else "no matching MCPConfig Maven versions found"
    _warn(f"Could not obtain Forge's srg2off mapping archive for Minecraft {mc_version} (tried: {tried}).")
    return None

def parse_srg2off_archive(data: bytes) -> Dict[str, str]:
    """Read Forge's fields/methods CSVs and return SRG identifier -> Mojmap name."""
    votes: Dict[str, Dict[str, int]] = {}
    srg_pattern = re.compile(r"^[mf]_\d+_$")
    java_name_pattern = re.compile(r"^[A-Za-z_$][A-Za-z0-9_$]*$")
    srg_headers = {"searge", "srg", "srgname", "srgid", "source", "from"}
    name_headers = {"name", "official", "mojmap", "mapped", "mappedname", "target", "to"}

    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        for member in archive.namelist():
            if not member.lower().endswith(".csv"):
                continue
            try:
                text = archive.read(member).decode("utf-8-sig", errors="replace")
            except KeyError:
                continue
            rows = list(csv.reader(io.StringIO(text)))
            if not rows:
                continue

            headers = [re.sub(r"[^a-z0-9]", "", cell.lower()) for cell in rows[0]]
            srg_index = next((i for i, h in enumerate(headers) if h in srg_headers), None)
            name_index = next((i for i, h in enumerate(headers) if h in name_headers), None)
            start = 1 if srg_index is not None or name_index is not None else 0

            for row in rows[start:]:
                if not row:
                    continue
                srg_name = row[srg_index].strip() if srg_index is not None and srg_index < len(row) else ""
                mapped_name = row[name_index].strip() if name_index is not None and name_index < len(row) else ""

                if not srg_pattern.fullmatch(srg_name):
                    srg_name = next((cell.strip() for cell in row if srg_pattern.fullmatch(cell.strip())), "")
                if not java_name_pattern.fullmatch(mapped_name):
                    candidates = [cell.strip() for cell in row
                                  if cell.strip() != srg_name and java_name_pattern.fullmatch(cell.strip())]

                    if name_index is not None and name_index < len(row):
                        candidate = row[name_index].strip()
                        if java_name_pattern.fullmatch(candidate):
                            mapped_name = candidate
                    elif candidates:
                        mapped_name = candidates[0]
                if not srg_pattern.fullmatch(srg_name) or not java_name_pattern.fullmatch(mapped_name):
                    continue
                if mapped_name == srg_name:
                    continue
                by_name = votes.setdefault(srg_name, {})
                by_name[mapped_name] = by_name.get(mapped_name, 0) + 1

    return {
        srg: sorted(names.items(), key=lambda item: (-item[1], item[0]))[0][0]
        for srg, names in votes.items()
    }

def download_tsrg(mc_version: str) -> Optional[str]:
    """Download Forge's MCPConfig archive and extract config/joined.tsrg.

    The Maven artifact version includes a timestamp (for example,
    1.19.2-20220805.130853), so resolve it from maven-metadata.xml rather
    than assuming the Minecraft version itself is the artifact version.
    """
    cache = os.path.join(_MAPPING_CACHE_DIR, f"joined_{mc_version}.tsrg")
    if os.path.isfile(cache):
        try:
            cached = open(cache, encoding="utf-8", errors="replace").read()
            if "\n\t" in cached and ("tsrg" in cached[:100] or cached.splitlines()):
                return cached
        except OSError:
            pass

    metadata = _fetch_url(_FORGE_MCP_CONFIG_METADATA, timeout=60)
    artifact_versions: List[str] = []
    if metadata:
        try:
            root = ET.fromstring(metadata)
            artifact_versions = [
                node.text.strip()
                for node in root.iter()
                if node.tag.rsplit("}", 1)[-1] == "version"
                and node.text and node.text.strip().startswith(mc_version + "-")
            ]
        except ET.ParseError as exc:
            _warn(f"Forge MCPConfig Maven metadata could not be parsed: {exc}")

    candidates = sorted(set(artifact_versions), reverse=True)

    if mc_version == "1.19.2" and "1.19.2-20220805.130853" not in candidates:
        candidates.append("1.19.2-20220805.130853")

    for artifact_version in candidates:
        artifact_name = f"mcp_config-{artifact_version}.zip"
        url = f"{_FORGE_MCP_CONFIG_BASE}/{artifact_version}/{artifact_name}"
        archive_data = _fetch_url(url, timeout=120)
        if not archive_data:
            continue
        try:
            with zipfile.ZipFile(io.BytesIO(archive_data)) as archive:
                member = next(
                    (name for name in archive.namelist()
                     if name.replace("\\", "/").lower() in
                     ("config/joined.tsrg", "config/joined.tsrg2", "joined.tsrg", "joined.tsrg2")),
                    None,
                )
                if not member:
                    continue
                mapping_text = archive.read(member).decode("utf-8", errors="replace")
            if not mapping_text.strip():
                continue
            os.makedirs(_MAPPING_CACHE_DIR, exist_ok=True)
            with open(cache, "w", encoding="utf-8", newline="\n") as fh:
                fh.write(mapping_text)
            _REAL_PRINT(f"  [Mappings] Downloaded Forge MCPConfig {artifact_version}")
            return mapping_text
        except (zipfile.BadZipFile, KeyError, OSError) as exc:
            _warn(f"Could not extract joined.tsrg from Forge MCPConfig {artifact_version}: {exc}")

    for template in _MCPCONFIG_TSRG_URLS:
        data = _fetch_url(template.format(v=mc_version), timeout=120)
        if data and (b"tsrg" in data[:100] or b"\n\t" in data):
            mapping_text = data.decode("utf-8", errors="replace")
            try:
                os.makedirs(_MAPPING_CACHE_DIR, exist_ok=True)
                with open(cache, "w", encoding="utf-8", newline="\n") as fh:
                    fh.write(mapping_text)
            except OSError:
                pass
            return mapping_text

    _warn(f"Could not obtain Forge MCPConfig joined.tsrg for Minecraft {mc_version}. "
          "Check your internet connection or the Forge Maven repository.")
    return None

def parse_tsrg(text: str) -> Dict[tuple, str]:
    """Parse MCPConfig TSRG/TSRG2 into (obf owner, obf member, descriptor) -> SRG id."""
    out: Dict[tuple, str] = {}
    current_class: Optional[str] = None
    for raw in text.splitlines():
        if not raw.strip() or raw.lstrip().startswith(("tsrg", "#")):
            continue
        indent = len(raw) - len(raw.lstrip("\t "))
        parts = raw.strip().split()
        if indent == 0:

            if len(parts) >= 2:
                current_class = parts[0]
            continue
        if current_class is None or indent > 1 or len(parts) < 2:
            continue

        if len(parts) >= 3 and parts[1].startswith("("):
            obf_name, descriptor = parts[0], parts[1]
            srg_name = next((part for part in parts[2:] if part.startswith(_SRG_ID_PREFIXES)), None)
            key = (current_class, obf_name, descriptor)
        else:
            obf_name = parts[0]
            srg_name = next((part for part in parts[1:] if part.startswith(_SRG_ID_PREFIXES)), None)
            key = (current_class, obf_name, None)
        if srg_name:
            out[key] = srg_name
    return out

def _split_params(params: str) -> List[str]:
    return [p.strip() for p in params.split(",") if p.strip()]

def parse_mojang_members(text: str) -> Dict[tuple, str]:
    """Parse Mojang ProGuard mappings into (obf owner, obf member, JVM descriptor) -> name."""
    class_to_obf: Dict[str, str] = {}
    for line in text.splitlines():
        if line and not line[0].isspace() and line.rstrip().endswith(":") and " -> " in line:
            readable, obf = line.rstrip()[:-1].split(" -> ", 1)
            class_to_obf[readable.strip()] = obf.strip()

    def desc_type(type_name: str) -> str:
        type_name = re.sub(r"<.*>", "", type_name.strip())
        type_name = type_name.replace("...", "[]")
        dims = 0
        while type_name.endswith("[]"):
            type_name = type_name[:-2].strip()
            dims += 1
        if type_name in _PRIMITIVE_DESC:
            base = _PRIMITIVE_DESC[type_name]
        else:
            obf_type = class_to_obf.get(type_name, type_name).replace(".", "/")
            base = "L" + obf_type + ";"
        return "[" * dims + base

    out: Dict[tuple, str] = {}
    current_obf: Optional[str] = None
    for line in text.splitlines():
        if not line or line.lstrip().startswith("#"):
            continue
        if not line[0].isspace():
            current_obf = None
            if line.rstrip().endswith(":") and " -> " in line:
                _readable, obf = line.rstrip()[:-1].split(" -> ", 1)
                current_obf = obf.strip()
            continue
        if current_obf is None or " -> " not in line:
            continue

        left, obf_name = line.strip().rsplit(" -> ", 1)

        while re.match(r"^\d+:\d+:", left):
            left = left.split(":", 2)[2]
        left = left.strip()
        if "(" in left and ")" in left:
            head, _, tail = left.partition("(")
            close = tail.rfind(")")
            if close < 0:
                continue
            return_type, _, member_name = head.rpartition(" ")
            if not return_type or not member_name:
                continue
            params = tail[:close].strip()
            param_types = _split_params(params)
            descriptor = "(" + "".join(desc_type(item) for item in param_types) + ")" + desc_type(return_type)
            out[(current_obf, obf_name.strip(), descriptor)] = member_name.strip()
        else:
            _type_name, _, member_name = left.rpartition(" ")
            if member_name:
                out[(current_obf, obf_name.strip(), None)] = member_name.strip()
    return out

def join_srg_to_official(tsrg: Dict[tuple, str], official: Dict[tuple, str]) -> Dict[str, str]:
    """Join Forge SRG ids to Mojang names, preferring exact JVM descriptor matches."""
    official_by_owner_member: Dict[tuple, Set[str]] = {}
    for (owner, member, descriptor), name in official.items():
        official_by_owner_member.setdefault((owner, member), set()).add(name)

    votes: Dict[str, Dict[str, int]] = {}
    for key, srg_name in tsrg.items():
        official_name = official.get(key)
        if not official_name:

            candidates = official_by_owner_member.get((key[0], key[1]), set())
            if len(candidates) == 1:
                official_name = next(iter(candidates))
        if official_name and official_name != srg_name and official_name.isidentifier():
            counts = votes.setdefault(srg_name, {})
            counts[official_name] = counts.get(official_name, 0) + 1

    return {
        srg_name: sorted(names.items(), key=lambda item: (-item[1], item[0]))[0][0]
        for srg_name, names in votes.items()
    }

def get_srg_to_official_map(jar_path: Optional[str] = None) -> Dict[str, str]:
    mc_version = STATE.detected_mc_version or detect_minecraft_version(jar_path)
    if not mc_version:
        _warn("Cannot download SRG mappings because the Minecraft version could not be detected.")
        return {}
    STATE.detected_mc_version = mc_version

    cache = _srg_cache_path(mc_version)
    if os.path.isfile(cache):
        try:
            with open(cache, encoding="utf-8") as fh:
                cached = json.load(fh)
            if isinstance(cached, dict) and len(cached) >= 100:
                _REAL_PRINT(f"  [Mappings] Loaded {len(cached):,} cached SRG-to-official names for Minecraft {mc_version}.")
                return cached
        except (OSError, ValueError):
            pass

    archive_data = download_srg2off_archive(mc_version)
    if archive_data:
        mapping = parse_srg2off_archive(archive_data)
        if len(mapping) >= 100:
            try:
                with open(cache, "w", encoding="utf-8") as fh:
                    json.dump(mapping, fh, indent=2, sort_keys=True)
            except OSError as exc:
                _warn(f"Could not cache the SRG-to-official mapping table: {exc}")
            _REAL_PRINT(f"  [Mappings] Parsed {len(mapping):,} direct SRG-to-official CSV entries for Minecraft {mc_version}.")
            return mapping
        _warn(f"Forge's srg2off archive was downloaded, but only {len(mapping)} usable mappings were read.")

    tsrg_text = download_tsrg(mc_version)
    if not tsrg_text:
        _warn(f"No usable Forge SRG mappings were obtained for Minecraft {mc_version}.")
        return {}
    tsrg_map = parse_tsrg(tsrg_text)
    if not tsrg_map:
        _warn(f"Forge MCPConfig joined.tsrg for {mc_version} downloaded, but no SRG entries were parsed.")
        return {}

    official_members: Dict[tuple, str] = {}
    downloaded_sides = []
    for side in ("client", "server"):
        moj_text = download_mojang_mappings(mc_version, side)
        if not moj_text:
            continue
        side_members = parse_mojang_members(moj_text)
        if side_members:
            official_members.update(side_members)
            downloaded_sides.append(side)

    if not official_members:
        _warn(f"Mojang mappings for Minecraft {mc_version} could not be downloaded or parsed.")
        return {}

    mapping = join_srg_to_official(tsrg_map, official_members)
    if len(mapping) < 100:
        _warn(f"Downloaded mapping files for Minecraft {mc_version}, but only {len(mapping)} SRG names joined.")
        return {}

    try:
        with open(cache, "w", encoding="utf-8") as fh:
            json.dump(mapping, fh, indent=2, sort_keys=True)
    except OSError as exc:
        _warn(f"Could not cache the joined SRG mapping table: {exc}")
    _REAL_PRINT(
        f"  [Mappings] Minecraft {mc_version}: loaded {len(tsrg_map):,} Forge SRG entries, "
        f"joined {len(mapping):,} readable names ({', '.join(downloaded_sides)} Mojang mappings)."
    )
    return mapping

def _replace_java_identifiers(source: str, mapping: Dict[str, str]) -> str:
    """Plain exact-text SRG replacement, as requested: old identifier -> mapped name."""
    if not mapping or not source:
        return source

    for old_name, new_name in mapping.items():
        if re.fullmatch(r"[mf]_\d+_", old_name) and new_name and old_name != new_name:
            source = source.replace(old_name, new_name)
    return source

def _java_srg_identifier_tokens(source: str) -> List[str]:
    """Return every SRG-looking occurrence in a Java file, including text literals/comments."""
    return re.findall(r"(?<![A-Za-z0-9_$])[mf]_\d+_(?![A-Za-z0-9_$])", source or "")

def deobfuscate_java_sources(java_files: Dict[str, str], namespace: str = "") -> Dict[str, str]:
    """Replace SRG identifiers in-place in every Java file from the decompiled tree."""
    if not java_files:
        STATE.deobfuscated_java_files = {}
        STATE.deobfuscated_java_paths = {}
        return {}

    try:
        srg_map = get_srg_to_official_map()
    except Exception as exc:
        _warn(f"Could not download/read the SRG mapping files: {exc}")
        srg_map = {}

    if not srg_map:
        _warn("No SRG mapping table was loaded; decompiled Java files were left unchanged.")
        STATE.deobfuscated_java_files = dict(java_files)
        STATE.deobfuscated_java_paths = {path: path for path in java_files}
        return dict(java_files)

    before_srg = set()
    rewritten: Dict[str, str] = {}
    changed_files = 0
    total_replacements = 0

    for path, original in java_files.items():
        source_tokens = _java_srg_identifier_tokens(original)
        before_srg.update(source_tokens)
        updated = _replace_java_identifiers(original, srg_map)
        rewritten[path] = updated
        if updated == original:
            continue

        try:
            with open(path, "w", encoding="utf-8", newline="") as fh:
                fh.write(updated)
            changed_files += 1
            total_replacements += sum(1 for token in source_tokens if token in srg_map)
        except OSError as exc:
            _warn(f"Could not write deobfuscated Java file {path}: {exc}")
            rewritten[path] = original

    after_srg = set()
    for code in rewritten.values():
        after_srg.update(_java_srg_identifier_tokens(code))
    resolved = len(before_srg - after_srg)
    _REAL_PRINT(
        f"  [Mappings] Replaced {resolved}/{len(before_srg)} distinct SRG identifiers "
        f"({total_replacements} occurrences) directly in {changed_files}/{len(java_files)} decompiled Java file(s)."
    )
    if after_srg:
        examples = ", ".join(sorted(after_srg)[:20])
        _warn(f"{len(after_srg)} SRG identifier(s) remain for Minecraft {STATE.detected_mc_version}: {examples}")
    elif before_srg:
        _REAL_PRINT("  [Mappings] No m_####_/f_####_ identifiers remain in the scanned Java sources.")

    STATE.deobfuscated_java_files = dict(rewritten)
    STATE.deobfuscated_java_paths = {path: path for path in rewritten}
    STATE.all_java_files = dict(rewritten)
    return rewritten

def run_class_decompiler(jar_file, output_dir):
    script_dir = os.path.dirname(os.path.abspath(__file__))
    lib_jar = os.path.join(script_dir, "tools", "ClassDecompiler.jar")

    if not os.path.exists(lib_jar):
        _warn(f"Error: ClassDecompiler.jar not found at {lib_jar}")
        return None

    if shutil.which("java") is None:
        _warn("Error: no 'java' executable found on PATH — a JDK/JRE is required to run the decompiler. "
              "Install one (e.g. `sudo apt install default-jre`) and re-run.")
        return None

    try:
        with zipfile.ZipFile(lib_jar, 'r') as z:
            internal_path = next(
                (name for name in z.namelist() if "vineflower.jar" in name.lower()),
                None
            )
            if internal_path:
                z.extract(internal_path, script_dir)
                extracted_engine = os.path.join(script_dir, internal_path)
            else:
                _warn("Vineflower jar not found in ClassDecompiler.jar")
                return None

        working_jar = os.path.abspath(jar_file)

        proc = subprocess.run(
            ["java", "-jar", os.path.abspath(lib_jar),
             os.path.abspath(working_jar), os.path.abspath(output_dir)],
            cwd=script_dir,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        if proc.returncode != 0:
            _warn(f"Decompiler exited with code {proc.returncode}")
            if proc.stderr:
                _warn(f"  stderr: {proc.stderr.strip()[-2000:]}")
            if proc.stdout:
                _warn(f"  stdout: {proc.stdout.strip()[-2000:]}")
            return None

        java_count = 0
        if os.path.isdir(output_dir):
            for _root, _dirs, _files in os.walk(output_dir):
                java_count += sum(1 for f in _files if f.endswith(".java"))
        if java_count == 0:
            _warn(f"Decompiler ran but produced no .java files in {output_dir}. "
                  f"stdout/stderr above (if any) may explain why.")
            return None

        return extracted_engine

    except Exception as e:
        _warn(f"Decompilation failure: {e}")
        return None

def main():
    all_zips = [f for f in os.listdir(".") if f.lower().endswith(".zip")]
    _REAL_PRINT(f"  [ModMorpher] Scanning for texture packs … ({len(all_zips)} .zip file(s) found)")
    texture_zips = [z for z in all_zips if _is_java_texture_pack(z)]

    if texture_zips:
        _REAL_PRINT(
            f"  [ModMorpher] Found {len(texture_zips)} Java texture pack(s): "
            + ", ".join(texture_zips)
        )
        for tp_zip in texture_zips:
            try:
                convert_java_texture_pack(tp_zip)
            except Exception as e:
                import traceback
                _warn(f"Texture pack conversion failed for {tp_zip}: {e}\n{traceback.format_exc()}")
        has_jar = any(f.endswith(".jar") for f in os.listdir("."))
        if not has_jar:
            return
    elif all_zips:
        _REAL_PRINT(
            f"  [ModMorpher] Found {len(all_zips)} .zip file(s) but none look like "
            "a Java texture pack (need pack.mcmeta or assets/…/textures/*.png inside)."
        )
        for z in all_zips:
            try:
                import zipfile as _zf
                with _zf.ZipFile(z) as _z:
                    top = _z.namelist()[:8]
                _REAL_PRINT(f"    {z}: {top}")
            except Exception:
                pass

    target_jar = next(
        (f for f in os.listdir(".") if f.endswith(".jar")),
        None
    )

    if not target_jar:
        _REAL_PRINT("  [ModMorpher] No .jar file found either. Nothing to do.")
        return

    STATE.detected_mc_version = detect_minecraft_version(target_jar)

    modmorpher_input_folder = f"src_{os.path.splitext(target_jar)[0]}"

    extracted_engine = run_class_decompiler(target_jar, modmorpher_input_folder)

    if extracted_engine:
        if os.path.exists(extracted_engine):
            os.remove(extracted_engine)
        source_root = modmorpher_input_folder
    else:
        _REAL_PRINT(
            "  [ModMorpher] ERROR: decompilation failed — see warnings above for the cause "
            "(missing Java runtime, missing tools/ClassDecompiler.jar, or a decompiler crash). "
            "Refusing to fall back to scanning the current directory, since that would silently "
            "process the wrong files. Fix the issue above and re-run."
        )
        return

    run_pipeline(source_root)
def prune_orphaned_assets() -> List[str]:
    """Walk the generated BP/RP and remove files that have no live references.

    Pass 1 — build reference sets from every JSON file that can cite assets.
    Pass 2 — delete unreferenced textures, geometry, and entity/block files
             that are missing their required assets.

    Returns a list of human-readable log lines describing what was removed.
    """
    removed: List[str] = []
    try:
        _prune_orphaned_assets_impl(removed)
    except Exception as exc:
        import traceback
        removed.append(f"[prune-error] Pruner crashed — {exc}")
        removed.append(f"[prune-error] {traceback.format_exc()}")
    return removed

def _prune_orphaned_assets_impl(removed: List[str]) -> None:
    """Implementation detail — called by prune_orphaned_assets."""

    def _all_strings(obj) -> List[str]:
        if isinstance(obj, str):
            return [obj]
        if isinstance(obj, dict):
            out = []
            for v in obj.values():
                out.extend(_all_strings(v))
            return out
        if isinstance(obj, list):
            out = []
            for v in obj:
                out.extend(_all_strings(v))
            return out
        return []

    def _load_json(path: str):
        try:
            with open(path, encoding="utf-8") as fh:
                return json.load(fh)
        except Exception:
            return None

    def _rel(path: str) -> str:
        """Relative path from OUTPUT_DIR for logging."""
        try:
            return os.path.relpath(path, OUTPUT_DIR).replace("\\", "/")
        except ValueError:
            return path

    def _remove(path: str, reason: str) -> None:
        try:
            os.remove(path)
            removed.append(f"[prune] {reason}: {_rel(path)}")
        except OSError:
            pass

    referenced_textures: set = set()
    referenced_geo_ids: set = set()
    referenced_geo_stems: set = set()
    all_json_strings: set = set()

    json_dirs = [
        os.path.join(RP_FOLDER, "entity"),
        os.path.join(RP_FOLDER, "render_controllers"),
        os.path.join(RP_FOLDER, "items"),
        os.path.join(RP_FOLDER, "attachables"),
        os.path.join(BP_FOLDER, "blocks"),
        os.path.join(BP_FOLDER, "items"),
        os.path.join(BP_FOLDER, "entities"),
        os.path.join(RP_FOLDER, "textures"),
    ]
    for jdir in json_dirs:
        if not os.path.isdir(jdir):
            continue
        for root, _, files in os.walk(jdir):
            for fname in files:
                if not fname.endswith(".json"):
                    continue
                data = _load_json(os.path.join(root, fname))
                if data is None:
                    continue
                for s in _all_strings(data):
                    all_json_strings.add(s)
                    if s.startswith("textures/") or "/" in s:
                        referenced_textures.add(s)
                        if s.startswith("textures/"):
                            referenced_textures.add(s[len("textures/"):])
                        referenced_textures.add(os.path.splitext(s)[0])
                    if s.lower().startswith("geometry."):
                        referenced_geo_ids.add(s.lower())
                        tail = s[len("geometry."):].lower()
                        referenced_geo_stems.add(tail)
                        referenced_geo_stems.add(tail.split(".")[-1])

    geo_id_to_file: Dict[str, str] = {}
    geo_stem_to_file: Dict[str, str] = {}
    for geo_root in [os.path.join(RP_FOLDER, "geometry"), os.path.join(RP_FOLDER, "models")]:
        if not os.path.isdir(geo_root):
            continue
        for fname in os.listdir(geo_root):
            if not fname.endswith(".json"):
                continue
            fpath = os.path.join(geo_root, fname)
            data = _load_json(fpath)
            if not isinstance(data, dict):
                continue
            for geo_list in (data.get("minecraft:geometry") or data.get("geometry") or []):
                if not isinstance(geo_list, dict):
                    continue
                ident = (geo_list.get("description") or {}).get("identifier", "")
                if ident:
                    ident_l = ident.lower()
                    geo_id_to_file[ident_l] = fpath
                    tail = ident_l[len("geometry."):] if ident_l.startswith("geometry.") else ident_l
                    geo_stem_to_file[tail] = fpath
                    geo_stem_to_file[tail.split(".")[-1]] = fpath
            stem = os.path.splitext(fname)[0].lower()
            geo_stem_to_file.setdefault(stem, fpath)

    tex_root = os.path.join(RP_FOLDER, "textures")

    tex_on_disk: set = set()
    if os.path.isdir(tex_root):
        for root, _, files in os.walk(tex_root):
            for f in files:
                if f.lower().endswith(".png"):
                    rel = os.path.relpath(os.path.join(root, f), RP_FOLDER).replace("\\", "/")
                    tex_on_disk.add(rel)
                    tex_on_disk.add(os.path.splitext(rel)[0])

    geo_on_disk: set = set()
    for geo_root in [os.path.join(RP_FOLDER, "geometry"), os.path.join(RP_FOLDER, "models")]:
        if os.path.isdir(geo_root):
            for f in os.listdir(geo_root):
                if f.endswith(".json"):
                    geo_on_disk.add(os.path.splitext(f)[0].lower())
                    geo_on_disk.add(os.path.splitext(os.path.splitext(f)[0])[0].lower())

    entity_rp_dir = os.path.join(RP_FOLDER, "entity")
    if os.path.isdir(entity_rp_dir):
        for fname in os.listdir(entity_rp_dir):
            if not fname.endswith(".json"):
                continue
            fpath = os.path.join(entity_rp_dir, fname)
            data = _load_json(fpath)
            if data is None:
                continue
            desc = data.get("minecraft:client_entity", {}).get("description", {})
            tex_refs = list(desc.get("textures", {}).values())
            geo_refs = list(desc.get("geometry", {}).values())

            has_texture = any(
                t in tex_on_disk or os.path.splitext(t)[0] in tex_on_disk
                or f"textures/{t}" in tex_on_disk
                for t in tex_refs
            )
            has_geo = True
            if geo_refs:
                has_geo = False
                for gid in geo_refs:
                    tail = gid[len("geometry."):] if gid.startswith("geometry.") else gid
                    base = tail.split(".")[-1] if "." in tail else tail
                    if tail.lower() in geo_on_disk or base.lower() in geo_on_disk:
                        has_geo = True
                        break

            if not has_texture and not has_geo and (tex_refs or geo_refs):
                _remove(fpath, "RP entity JSON has no resolvable texture or geometry on disk")
            elif not has_geo and geo_refs:
                _remove(
                    fpath,
                    f"RP entity JSON references geometry {geo_refs} not found on disk (UNLINK332)"
                )
            elif not has_texture and tex_refs:
                removed.append(
                    f"[warn] {_rel(fpath)}: missing texture(s) {tex_refs} — kept but will render incorrectly"
                )

    for geo_root in [os.path.join(RP_FOLDER, "geometry"), os.path.join(RP_FOLDER, "models")]:
        if not os.path.isdir(geo_root):
            continue
        for fname in os.listdir(geo_root):
            if not fname.endswith(".json"):
                continue
            fpath = os.path.join(geo_root, fname)
            stem = os.path.splitext(fname)[0].lower()
            base = os.path.splitext(stem)[0].lower()
            matched = (
                stem in referenced_geo_stems
                or base in referenced_geo_stems
                or fpath in geo_id_to_file.values()
                and any(gid in referenced_geo_ids for gid, gf in geo_id_to_file.items() if gf == fpath)
            )
            if not matched:
                _remove(fpath, "Orphaned geometry file (not referenced by any entity JSON)")
    SAFE_TO_PRUNE = {"entity", "mob_effect", "screens", "environment", "colormap", "misc"}

    live_tex_refs: set = set()
    live_json_dirs = [
        os.path.join(RP_FOLDER, "entity"),
        os.path.join(RP_FOLDER, "render_controllers"),
        os.path.join(RP_FOLDER, "items"),
        os.path.join(RP_FOLDER, "attachables"),
        os.path.join(BP_FOLDER, "items"),
        os.path.join(RP_FOLDER, "textures"),
    ]
    for jdir in live_json_dirs:
        if not os.path.isdir(jdir):
            continue
        for root, _, files in os.walk(jdir):
            for fname in files:
                if not fname.endswith(".json"):
                    continue
                data = _load_json(os.path.join(root, fname))
                if data is None:
                    continue
                for s in _all_strings(data):
                    if not s or "/" not in s:
                        continue
                    norm = s if s.startswith("textures/") else f"textures/{s}"
                    norm_no_ext = os.path.splitext(norm)[0]
                    live_tex_refs.add(norm)
                    live_tex_refs.add(norm_no_ext)
                    live_tex_refs.add(s)
                    live_tex_refs.add(os.path.splitext(s)[0])

    def _tex_is_live(rel: str) -> bool:
        rel_no_ext = os.path.splitext(rel)[0]
        return rel in live_tex_refs or rel_no_ext in live_tex_refs

    if os.path.isdir(tex_root):
        for entry in os.listdir(tex_root):
            entry_path = os.path.join(tex_root, entry)
            if os.path.isfile(entry_path) and entry.lower().endswith(".png"):
                rel = os.path.relpath(entry_path, RP_FOLDER).replace("\\", "/")
                if not _tex_is_live(rel):
                    _remove(entry_path, "Orphaned root texture (not referenced by any JSON)")
            elif os.path.isdir(entry_path) and entry.lower() in SAFE_TO_PRUNE:
                for root, _, files in os.walk(entry_path):
                    for fname in files:
                        if not fname.lower().endswith(".png"):
                            continue
                        fpath = os.path.join(root, fname)
                        rel = os.path.relpath(fpath, RP_FOLDER).replace("\\", "/")
                        if not _tex_is_live(rel):
                            _remove(
                                fpath,
                                f"Orphaned texture in textures/{entry}/ (not referenced by any surviving JSON)",
                            )

    bp_blocks_dir = os.path.join(BP_FOLDER, "blocks")
    rp_blocks_dir = os.path.join(RP_FOLDER, "blocks")
    terrain_path  = os.path.join(RP_FOLDER, "textures", "terrain_texture.json")
    terrain_data  = _load_json(terrain_path) if os.path.exists(terrain_path) else None
    terrain_keys: set = set()
    if isinstance(terrain_data, dict):
        terrain_keys = set((terrain_data.get("texture_data") or {}).keys())

    if os.path.isdir(bp_blocks_dir):
        for fname in os.listdir(bp_blocks_dir):
            if not fname.endswith(".json"):
                continue
            fpath = os.path.join(bp_blocks_dir, fname)
            data  = _load_json(fpath)
            if data is None:
                continue
            desc = (data.get("minecraft:block") or {}).get("description", {})
            ident: str = desc.get("identifier", "")
            plain = ident.split(":")[-1] if ":" in ident else os.path.splitext(fname)[0]
            rp_block_names = set()
            if os.path.isdir(rp_blocks_dir):
                rp_block_names = {
                    n.lower()
                    for n in os.listdir(rp_blocks_dir)
                    if n.lower().endswith(".json")
                }
            has_rp = (
                plain in terrain_keys
                or ident in terrain_keys
                or fname.lower() in rp_block_names
                or os.path.splitext(fname)[0].lower() in rp_block_names
            )
            if not has_rp:
                removed.append(
                    f"[warn] {_rel(fpath)}: BP block JSON has no terrain_texture entry or RP block definition"
                )

    for folder in [RP_FOLDER, BP_FOLDER]:
        for root, dirs, files in os.walk(folder, topdown=False):
            if root == folder:
                continue
            if not os.listdir(root):
                try:
                    os.rmdir(root)
                except OSError:
                    pass

def run_validation_pass() -> list:
    warnings = []
    tex_dir = os.path.join(RP_FOLDER, "textures")
    tex_on_disk = set()
    if os.path.isdir(tex_dir):
        for root, _, files in os.walk(tex_dir):
            for f in files:
                if f.lower().endswith(".png"):
                    rel = os.path.relpath(os.path.join(root, f), RP_FOLDER).replace("\\", "/")
                    tex_on_disk.add(rel)
                    tex_on_disk.add(os.path.splitext(rel)[0])
    geo_dir = os.path.join(RP_FOLDER, "geometry")
    geo_on_disk = set()
    if os.path.isdir(geo_dir):
        for f in os.listdir(geo_dir):
            geo_on_disk.add(os.path.splitext(f)[0].lower())
    anim_dir = os.path.join(RP_FOLDER, "animations")
    anim_on_disk = set()
    if os.path.isdir(anim_dir):
        for f in os.listdir(anim_dir):
            anim_on_disk.add(os.path.splitext(f)[0].lower())
    entity_dir = os.path.join(RP_FOLDER, "entity")
    if os.path.isdir(entity_dir):
        for fname in os.listdir(entity_dir):
            if not fname.endswith(".json"):
                continue
            fpath = os.path.join(entity_dir, fname)
            try:
                with open(fpath) as f:
                    data = json.load(f)
                desc = data.get("minecraft:client_entity", {}).get("description", {})
                for tex_key, tex_path in desc.get("textures", {}).items():
                    full = tex_path if tex_path.startswith("textures/") else f"textures/{tex_path}"
                    if full not in tex_on_disk and tex_path not in tex_on_disk:
                        warnings.append(f"[WARN] Missing texture '{tex_path}' referenced in {fname}")
                for geo_key, geo_id in desc.get("geometry", {}).items():
                    if geo_id.startswith("geometry."):
                        geo_tail = sanitize_identifier(geo_id[len("geometry."):])
                    else:
                        geo_tail = sanitize_identifier(geo_id)
                    geo_last = geo_tail.split(".")[-1] if "." in geo_tail else geo_tail
                    if (geo_tail not in geo_on_disk and geo_last not in geo_on_disk):
                        warnings.append(
                            f"[WARN] Geometry '{geo_id}' in {fname} has no matching .geo.json "
                            f"(placeholder — add the geometry file to fix rendering)"
                        )
                for anim_key, anim_id in desc.get("animations", {}).items():
                    anim_base = sanitize_identifier(anim_id.split(".")[-2]) if "." in anim_id else anim_id
                    if not anim_on_disk:
                        warnings.append(f"[WARN] Animation '{anim_id}' referenced in {fname} but no animation files found")
                        break
            except Exception as e:
                warnings.append(f"[WARN] Could not parse {fname}: {e}")
    bp_entity_dir = os.path.join(BP_FOLDER, "entities")
    if os.path.isdir(bp_entity_dir):
        for fname in os.listdir(bp_entity_dir):
            if not fname.endswith(".json"):
                continue
            fpath = os.path.join(bp_entity_dir, fname)
            try:
                with open(fpath) as f:
                    data = json.load(f)
                comps = data.get("minecraft:entity", {}).get("components", {})
                loot = comps.get("minecraft:loot", {}).get("table", "")
                if loot:
                    loot_full = os.path.join(BP_FOLDER, loot)
                    if not os.path.exists(loot_full):
                        warnings.append(f"[WARN] Loot table '{loot}' referenced in {fname} does not exist")
            except Exception:
                pass
    return warnings
ENTITY_SOUND_PROFILES: Dict[str, dict] = {}
def _is_mod_id(s: Optional[str]) -> bool:
    return bool(s) and all(c in 'abcdefghijklmnopqrstuvwxyz0123456789_-' for c in s)

def _registry_name_ok(s: Optional[str], extra: str = '') -> bool:
    return bool(s) and all(c in 'abcdefghijklmnopqrstuvwxyz0123456789_' + extra for c in s)

def _find_generic(t, outer: str):
    """First type named `outer` inside the (nested) generic arguments of type `t`, else None."""
    for a in (getattr(t, 'arguments', None) or []):
        inner = getattr(a, 'type', None)
        if inner is None:
            continue
        if simple_type_name(inner) == outer:
            return inner
        hit = _find_generic(inner, outer)
        if hit is not None:
            return hit
    return None

def _first_type_arg(t) -> Optional[str]:
    args = type_args(t)
    return args[0] if args else None

def detect_mod_id(java_files: dict) -> str:
    for path, code in java_files.items():
        ast = JavaAST(code)
        val = ast.annotation_value('Mod')
        if _is_mod_id(val):
            return sanitize_identifier(val)
        vals = ast.field_string_values({'MOD_ID', 'MODID', 'MOD_ID_STR', 'ID'})
        for _, v in vals.items():
            if _is_mod_id(v):
                return sanitize_identifier(v)
    return _detect_mod_id_from_config_files()

def _detect_mod_id_from_config_files() -> str:
    """Mod id from mods.toml / fabric.mod.json / gradle files (configuration, not Java)."""
    for root, _, files in os.walk("."):
        for f in files:
            if f == "neoforge.mods.toml":
                try:
                    c = open(os.path.join(root, f), encoding="utf-8", errors="ignore").read()
                    m = re.search(r'modId\s*=\s*["\']([a-z0-9_-]+)["\']', c)
                    if m:

                        return sanitize_identifier(m.group(1))
                except Exception:
                    pass
            if f == "mods.toml":
                try:
                    c = open(os.path.join(root, f), encoding="utf-8", errors="ignore").read()
                    m = re.search(r'modId\s*=\s*["\']([a-z0-9_-]+)["\']', c)
                    if m:
                        return sanitize_identifier(m.group(1))
                except Exception:
                    pass
            if f == "fabric.mod.json":
                try:
                    data = json.load(open(os.path.join(root, f), encoding="utf-8"))
                    if "id" in data:
                        return sanitize_identifier(data["id"])
                except Exception:
                    pass
            if f == "quilt.mod.json":
                try:
                    data = json.load(open(os.path.join(root, f), encoding="utf-8"))
                    qm = data.get("quilt_loader", {})
                    if "id" in qm:
                        return sanitize_identifier(qm["id"])
                except Exception:
                    pass
            if f in ("build.gradle", "build.gradle.kts"):
                try:
                    c = open(os.path.join(root, f), encoding="utf-8", errors="ignore").read()
                    for pat in [
                        r'archivesBaseName\s*=\s*["\']([a-z0-9_-]+)["\']',
                        r'mod_id\s*=\s*["\']([a-z0-9_-]+)["\']',
                        r'modId\s*[=:]\s*["\']([a-z0-9_-]+)["\']',
                    ]:
                        m = re.search(pat, c, re.I)
                        if m:
                            candidate = sanitize_identifier(m.group(1))
                            if candidate and len(candidate) >= 2:

                                return candidate
                except Exception:
                    pass
            if f == "gradle.properties":
                try:
                    c = open(os.path.join(root, f), encoding="utf-8", errors="ignore").read()
                    for pat in [
                        r'mod_id\s*=\s*([a-z0-9_-]+)',
                        r'modId\s*=\s*([a-z0-9_-]+)',
                        r'archivesBaseName\s*=\s*([a-z0-9_-]+)',
                    ]:
                        m = re.search(pat, c, re.I)
                        if m:
                            candidate = sanitize_identifier(m.group(1).strip())
                            if candidate and len(candidate) >= 2:
                                return candidate
                except Exception:
                    pass
    return ""
_RL_TYPE_NAMES = ('ResourceLocation', 'RL', 'Identifier')

def _build_resource_location_constants(java_files: dict) -> Dict[str, str]:
    constants: Dict[str, str] = {}
    for _path, code in java_files.items():
        unit = get_unit(code)
        for node in list(unit.walk(jt.FieldDeclaration)) + list(unit.walk(jt.LocalVariableDeclaration)):
            if simple_type_name(node.type) not in _RL_TYPE_NAMES:
                continue
            for d in node.declarators:
                init = d.initializer
                vals = None
                if isinstance(init, jt.ClassCreator) and simple_type_name(init.type) == 'ResourceLocation':
                    vals = [string_value(a) for a in init.arguments or []]
                    if len(vals) == 2 and all(v is not None for v in vals):
                        constants[d.name] = f"{vals[0]}:{vals[1]}"
                    elif len(vals) >= 1 and vals[0] and (vals[0][0].islower() or vals[0][0].isdigit() or vals[0][0] in '_:/-'):
                        constants[d.name] = vals[0]
                elif isinstance(init, jt.MethodInvocation) and init.qualifier == 'ResourceLocation':
                    vals = [string_value(a) for a in init.arguments or []]
                    if init.member in ('fromNamespaceAndPath', 'of') and len(vals) == 2 and all(v is not None for v in vals):
                        constants[d.name] = f"{vals[0]}:{vals[1]}"
                    elif init.member in ('tryParse', 'tryBuild', 'of') and vals and vals[0] \
                            and (vals[0][0].islower() or vals[0][0].isdigit() or vals[0][0] in '_:/-'):
                        constants[d.name] = vals[0]
    return constants
def _is_const_name(s: Optional[str]) -> bool:
    return bool(s) and len(s) >= 3 and (s[0].isupper() or s[0] == '_') and all(c.isupper() or c.isdigit() or c == '_' for c in s)

def _new_refs(node) -> List[str]:
    """Class names of every `X::new` method reference under `node`, in order."""
    return [n.expression.member for n in iter_nodes(node, jt.MethodReference)
            if isinstance(n.expression, jt.MemberReference) and getattr(n.method, 'member', None) == 'new']

def _builder_chains(unit):
    """Yield (of_call, {member: args}) for every `EntityType.Builder.of(..)...` chain."""
    for call in unit.walk(jt.MethodInvocation):
        if call.member == 'of' and (call.qualifier or '').startswith('EntityType.Builder'):
            chain = {s.member: list(s.arguments or []) for s in (call.selectors or []) if isinstance(s, jt.MethodInvocation)}
            yield call, chain

_ATTR_METHOD_NAMES = {'createattributes', 'getdefaultattributes', 'createmobattributes',
                      'createmonsterattributes', 'createanimalattributes', 'createlivingattributes'}

def _upper_name_ok(s: str, min_len: int = 1) -> bool:
    return len(s) >= min_len and all(c.isupper() or c.isdigit() or c == '_' for c in s)

def build_entity_registry(java_files: dict, namespace: str) -> dict:
    registry = {}
    for path, code in java_files.items():
        unit = get_unit(code)
        ast = JavaAST(code)
        calls = list(unit.walk(jt.MethodInvocation))
        for inv in calls:
            if inv.member != 'register':
                continue
            args = inv.arguments or []
            reg_name = string_value(args[0]) if args else None
            if _registry_name_ok(reg_name):
                for arg in args[1:]:
                    if isinstance(arg, jt.MethodReference):
                        cls_ref = getattr(arg.expression, 'member', None) or getattr(arg.expression, 'name', None)
                        if cls_ref and cls_ref not in ('super', 'this') and len(cls_ref) > 2:
                            registry[cls_ref] = f"{namespace}:{reg_name}"
        cls_name = ast.primary_class_name()
        if cls_name:
            for inv in calls:
                if inv.member == 'setRegistryName':
                    raw = JavaAST.first_string_arg(inv)
                    if raw:
                        registry[cls_name] = raw if ':' in raw else f"{namespace}:{raw}"
                        break
        for node in list(unit.walk(jt.FieldDeclaration)) + list(unit.walk(jt.LocalVariableDeclaration)):
            tname = simple_type_name(node.type)
            entity_cls = None
            if tname == 'RegistryObject':
                targs = getattr(node.type, 'arguments', None) or []
                first = getattr(targs[0], 'type', None) if targs else None
                entity_cls = _first_type_arg(first) if first is not None and simple_type_name(first) == 'EntityType' else None
            elif tname in ('DeferredHolder', 'DeferredEntity', 'Supplier'):
                hit = _find_generic(node.type, 'EntityType')
                entity_cls = _first_type_arg(hit) if hit is not None else None
            if not entity_cls:
                continue
            for d in node.declarators:
                init = d.initializer
                if isinstance(init, jt.MethodInvocation) and init.member == 'register' and init.qualifier \
                        and init.arguments and _registry_name_ok(string_value(init.arguments[0])):
                    registry[entity_cls] = f"{namespace}:{string_value(init.arguments[0])}"
        for inv in calls:
            args = inv.arguments or []
            if inv.member == 'register' and args and _registry_name_ok(string_value(args[0])):
                refs = _new_refs(args[1:])
                if refs and refs[0] not in ("super", "this") and len(refs[0]) > 2:
                    registry[refs[0]] = f"{namespace}:{string_value(args[0])}"
        for of_call, chain in _builder_chains(unit):
            refs = _new_refs(of_call.arguments)
            build = chain.get('build')
            if refs and build and _registry_name_ok(string_value(build[0]) if build else None):
                registry[refs[0]] = f"{namespace}:{string_value(build[0])}"
        if cls_name:
            for inv in calls:
                if inv.member == 'setRegistryName' and inv.arguments:
                    raw = string_value(inv.arguments[0])
                    if raw and _registry_name_ok(raw, ':-'):
                        registry[cls_name] = raw if ":" in raw else f"{namespace}:{raw}"
                        break
    rl_constants = _build_resource_location_constants(java_files)
    if rl_constants:
        for _path2, code2 in java_files.items():
            unit2 = get_unit(code2)
            for inv in unit2.walk(jt.MethodInvocation):
                args = inv.arguments or []
                if inv.member != 'register' or len(args) < 2:
                    continue
                const = args[0].member if isinstance(args[0], jt.MemberReference) and not args[0].qualifier else None
                if _is_const_name(const):
                    cls = None
                    if isinstance(args[1], jt.MethodReference) and isinstance(args[1].expression, jt.MemberReference):
                        cls = args[1].expression.member
                    elif isinstance(args[1], jt.LambdaExpression) and isinstance(args[1].body, jt.ClassCreator) and not args[1].parameters:
                        cls = simple_type_name(args[1].body.type)
                    if const in rl_constants and cls and cls not in ('super', 'this', 'EntityType'):
                        rl = rl_constants[const]
                        if ':' in rl and cls not in registry:
                            registry[cls] = rl
            for of_call, chain in _builder_chains(unit2):
                refs = _new_refs(of_call.arguments)
                build = chain.get('build')
                const = build[0].member if build and len(build) == 1 and isinstance(build[0], jt.MemberReference) and not build[0].qualifier else None
                if refs and _is_const_name(const) and const in rl_constants and refs[0] not in registry:
                    rl = rl_constants[const]
                    if ':' in rl:
                        registry[refs[0]] = rl
            for inv in unit2.walk(jt.MethodInvocation):
                args = inv.arguments or []
                if inv.member != 'register' or not args or not _registry_name_ok(string_value(args[0])):
                    continue
                for b in iter_calls(list(args[1:])):
                    const = b.arguments[0].member if b.member == 'build' and len(b.arguments or []) == 1 \
                        and isinstance(b.arguments[0], jt.MemberReference) and not b.arguments[0].qualifier else None
                    if _is_const_name(const) and const in rl_constants:
                        rl = rl_constants[const]
                        ns_part = rl.split(':')[0] if ':' in rl else namespace
                        refs = _new_refs(list(args[1:]))
                        if refs and refs[0] not in ('super', 'this', 'EntityType') and refs[0] not in registry:
                            registry[refs[0]] = f"{ns_part}:{string_value(args[0])}"
                        break
    return registry
def build_attributes_registry(java_files: dict) -> dict:
    attrs_reg = {}
    defaults = {"health":20.0,"attack_damage":3.0,"movement_speed":0.3,
                "follow_range":16.0,"knockback_resistance":0.0,"armor":0.0}
    for path, code in java_files.items():
        if not (identifier_set(code) & _ATTR_METHOD_NAMES):
            continue
        cls_name = extract_class_name(code)
        if not cls_name: continue
        attrs = extract_attributes_from_java(code)
        if any(attrs.get(k) != defaults.get(k) for k in defaults):
            attrs_reg[cls_name] = attrs
    return attrs_reg
def build_sound_registry_from_java(java_files: dict, namespace: str) -> dict:
    sound_map = {}
    for path, code in java_files.items():
        fname = os.path.basename(path).lower()
        unit = get_unit(code)
        idents = {t.value for t in unit.tokens if isinstance(t, _jtok.Identifier)}
        if not (any(k in fname for k in ("sound","sounds","sfx","audio")) or
                ("SoundEvent" in idents and "register" in idents)):
            continue
        decls = []
        for node in list(unit.walk(jt.FieldDeclaration)) + list(unit.walk(jt.LocalVariableDeclaration)):
            tname = simple_type_name(node.type)
            is_sound = (tname == 'SoundEvent') or (
                tname in ('RegistryObject', 'DeferredHolder', 'Supplier') and 'SoundEvent' in type_args(node.type))
            for d in node.declarators:
                decls.append((is_sound, d))
        for is_sound, d in decls:
            init = d.initializer
            if is_sound and _upper_name_ok(d.name) and isinstance(init, jt.MethodInvocation) and init.member == 'register' \
                    and init.qualifier and init.arguments:
                s = string_value(init.arguments[0])
                if s and all(c in 'abcdefghijklmnopqrstuvwxyz0123456789_.' for c in s):
                    sound_map[d.name] = sanitize_sound_key(f"{namespace}.{s}")
        for _is_sound, d in decls:
            init = d.initializer
            if _upper_name_ok(d.name, 3) and isinstance(init, jt.MethodInvocation) and (
                    (init.qualifier == 'SoundEvent' and init.member.startswith('create'))
                    or (init.qualifier == 'Registry' and init.member.startswith('register'))):
                lits = [string_value(l) for l in iter_nodes(list(init.arguments or []), jt.Literal)]
                lits = [s for s in lits if s and all(c in 'abcdefghijklmnopqrstuvwxyz0123456789_.:-' for c in s)]
                if lits:
                    sid = lits[-1]
                    if ":" in sid: sid = sid.split(":", 1)[1]
                    sound_map[d.name] = sanitize_sound_key(f"{namespace}.{sid}")
    return sound_map
def run_prescan(java_files: dict, namespace: str) -> str:
    detected = detect_mod_id(java_files)
    ns = detected or namespace
    build_inheritance_graph(java_files)
    STATE.entity_registry = build_entity_registry(java_files, ns)
    STATE.attrs_registry  = build_attributes_registry(java_files)
    STATE.sound_const_map = build_sound_registry_from_java(java_files, ns)
    build_goal_inheritance_map(java_files)

    for cls, eid in list(STATE.entity_registry.items())[:6]:
        pass

    return ns

def _run_conversion_pipeline(source_root: str = "."):
    _orig = _logger._original_print
    jar_path = find_jar_file(".")
    if jar_path:
        jar_base_raw = os.path.splitext(os.path.basename(jar_path))[0]
        _orig(f"    Found JAR: {os.path.basename(jar_path)}")
    else:
        jar_base_raw = os.path.split(os.getcwd())[-1]
        _orig("     No .jar found — using folder name as namespace, skipping JAR assets")
    pack_display_name = jar_base_raw
    namespace = sanitize_identifier(jar_base_raw) or "converted"
    ensure_dirs()
    if jar_path:
        with _logger.phase("Extracting JAR assets", total=0, unit="step", colour="blue"):
            jar_loader = detect_loader_from_jar(jar_path)

            copy_assets_from_jar(jar_path, RP_FOLDER)
            copy_geckolib_animations_from_jar(jar_path, RP_FOLDER)
            logo = extract_logo_from_jar(jar_path)
            if logo:
                try:
                    dest_bp = os.path.join(BP_FOLDER, "pack_icon.png")
                    dest_rp = os.path.join(RP_FOLDER, "pack_icon.png")
                    tmp_fixed_dir = ".temp_icon_fixed"
                    tmp_fixed = os.path.join(tmp_fixed_dir, "pack_icon.png")
                    ok = ensure_and_fix_pack_icon(logo, tmp_fixed)
                    if ok or os.path.exists(tmp_fixed):
                        os.makedirs(os.path.dirname(dest_bp), exist_ok=True)
                        os.makedirs(os.path.dirname(dest_rp), exist_ok=True)
                        shutil.copy(tmp_fixed, dest_bp)
                        shutil.copy(tmp_fixed, dest_rp)
                    else:
                        shutil.copy(logo, dest_bp)
                        shutil.copy(logo, dest_rp)

                    shutil.rmtree(".temp_logo_extract", ignore_errors=True)
                    shutil.rmtree(tmp_fixed_dir, ignore_errors=True)
                except Exception as e:
                    _warn(f" Failed to copy pack icon: {e}")
    with _logger.phase("Normalising RP assets", total=0, unit="step", colour="blue"):
        normalize_geometry_file_identifiers()
        sanitize_animation_keys_in_files()
        fix_animation_format_versions()
    with _logger.phase("Sweeping models → rp/geometry", total=0, unit="step", colour="blue"):
        normalise_all_geometry_to_geckolib(RP_FOLDER, namespace)
    with _logger.phase("Indexing RP assets", total=0, unit="step", colour="blue"):
        build_rp_asset_index()
    STATE.porting_notes = []
    stats = {
        "converted_entities_bp": [],
        "converted_entities_rp": [],
        "skipped_files":         [],
        "missing_geometry":      [],
        "errors":                [],
        "warnings":              [],
        "converted_items":       [],
        "converted_blocks":      [],
        "scripts_written":       [],
        "mixins_converted":      [],
    }
    with _logger.phase("Reading Java source", total=0, unit="file", colour="blue"):
        java_files = read_all_java_files(source_root if os.path.exists(source_root) else ".")
        java_files = deobfuscate_java_sources(java_files, namespace)
        STATE.all_java_files = java_files
    with _logger.phase("Pre-scanning registries", total=0, unit="step", colour="blue"):
        detected_mod_id = run_prescan(java_files, namespace)
        if detected_mod_id and detected_mod_id != namespace:

            namespace = detected_mod_id
        build_renderer_entity_map()
    with _logger.phase("Converting LayerDefinition models", total=0, unit="step", colour="blue"):
        STATE.layerdef_geo_map = scan_and_convert_layerdefinition_models(java_files, namespace)
        if STATE.layerdef_geo_map:
            geom_file_map, geom_ns_map = load_geometry_identifiers()
            build_rp_asset_index()
    with _logger.phase("Building asset maps", total=0, unit="step", colour="blue"):
        gecko_maps    = build_geckolib_mappings(".")
        geom_file_map, geom_ns_map = load_geometry_identifiers()
        anim_key_map  = load_animation_keys()
    with _logger.phase("Scanning block registries", total=0, unit="step", colour="blue"):
        registry_block_names, registry_block_files = scan_block_registrations(java_files, namespace, stats)
    total_files = len(java_files)
    with _logger.phase("Converting Java files", total=total_files, unit="file", colour="cyan") as bar:
        for path, code in java_files.items():
            fname  = os.path.basename(path)
            lname  = fname.lower()
            bar.set_postfix_str(fname[:38])
            try:
                cls_for_graph = extract_class_name(code)
                superchain = resolve_superchain(cls_for_graph) if cls_for_graph else []
                superchain_str = " ".join(superchain)

                fname_item_hint   = lname.endswith("item.java")  or "_item"  in lname
                fname_block_hint  = lname.endswith("block.java") or "_block" in lname
                fname_entity_hint = any(k in lname for k in ENTITY_OVERRIDE_KEYWORDS)
                fname_noise       = any(k in lname for k in NON_ENTITY_KEYWORDS) and not fname_entity_hint
                sound_artifact    = _is_sound_artifact(code, path, cls_for_graph)

                item_signals = item_content_signals(code, superchain, fname_item_hint)
                is_item = sum(item_signals) >= 2 or (fname_item_hint and sum(item_signals) >= 1)
                block_signals = block_content_signals(code, superchain, fname_block_hint)
                is_block = sum(block_signals) >= 2 or (fname_block_hint and sum(block_signals) >= 1)
                if sound_artifact:
                    is_block = False
                if path in registry_block_files:

                    is_block = False
                entity_candidate = (
                    is_likely_entity(code, path)
                    and not _should_skip_entity_artifact(code, path, cls_for_graph)
                    and not (is_item  and not fname_entity_hint)
                    and not (is_block and not fname_entity_hint)
                    and not fname_noise
                )
                if is_item:
                    convert_java_item_full(code, path, namespace)
                    stats["converted_items"].append(path)
                if is_block:
                    convert_java_block_full(code, path, namespace)
                    stats["converted_blocks"].append(path)
                if entity_candidate:
                    cls = extract_class_name(code) or os.path.splitext(fname)[0]
                    if cls and cls in STATE.entity_registry:
                        entity_identifier = STATE.entity_registry[cls]
                    else:
                        raw = entity_registration_name(code, cls or "")
                        reg_name = (raw if ":" in raw else f"{namespace}:{raw}") if raw else None
                        entity_identifier = reg_name or f"{namespace}:{clean_java_artifact_name(cls)}"
                    convert_java_to_bedrock(path, entity_identifier, gecko_maps, geom_file_map, geom_ns_map, anim_key_map, stats)
            except Exception as e:
                _warn(f" Error processing {fname}: {e}")
                stats["errors"].append(f"{path}: {e}")
            finally:
                bar.update(1)
    with _logger.phase("Writing registries & lang", total=0, unit="step", colour="blue"):
        generate_texture_registry(pack_display_name)
        generate_sounds_registry(namespace)
        generate_sound_playback_script(namespace)
        convert_lang_files()
    with _logger.phase("Scanning mixins", total=0, unit="step", colour="magenta"):
        scan_mixins(java_files, namespace)
    with _logger.phase("Scanning capabilities", total=0, unit="step", colour="magenta"):
        scan_capabilities(java_files, namespace)
    with _logger.phase("Scanning networking", total=0, unit="step", colour="magenta"):
        scan_networking(java_files, namespace)
    with _logger.phase("Scanning client-only classes", total=0, unit="step", colour="magenta"):
        scan_client_classes(java_files)
    with _logger.phase("Writing Global Cap Registry", total=0, unit="step", colour="green"):
        GlobalCapabilityRegistry.write(namespace, BP_FOLDER)
        GlobalCapabilityRegistry.ensure_import_in_main(BP_FOLDER)
    with _logger.phase("Scanning GUI / Screen classes", total=0, unit="step", colour="cyan"):
        for _gui_path, _gui_code in java_files.items():
            JavaGUIConverter.process(_gui_code, namespace, RP_FOLDER,
                                     os.path.join(BP_FOLDER, "scripts"))
    with _logger.phase("Scanning NBT serializers", total=0, unit="step", colour="cyan"):
        for _nbt_path, _nbt_code in java_files.items():
            _nbt_cls = extract_class_name(_nbt_code)
            if _nbt_cls and ({'addAdditionalSaveData', 'readAdditionalSaveData'}
                             & {m.name for m in get_unit(_nbt_code).methods()}):
                _nbt_id = f'{namespace}:{sanitize_identifier(_nbt_cls)}'
                RecursiveNBTSerializer.scan_and_emit_nbt_scripts(
                    _nbt_code, _nbt_id, namespace, BP_FOLDER)
    if jar_path:
        with _logger.phase("Processing loot / recipes / tags", total=0, unit="step", colour="blue"):
            process_loot_tables_from_jar(jar_path, namespace)
            process_recipes_from_jar(jar_path, namespace)
            extract_item_tags_from_jar(jar_path, namespace)
        with _logger.phase("Converting structures", total=0, unit="step", colour="blue"):
            process_structures_from_jar(jar_path, namespace, java_files=java_files)
    with _logger.phase("Writing manifests", total=0, unit="step", colour="blue"):
        write_manifest_for(BP_FOLDER, pack_display_name, "BP")
        write_manifest_for(RP_FOLDER, pack_display_name, "RP")
    with _logger.phase("Pruning orphaned assets", total=0, unit="step", colour="yellow"):
        prune_log = prune_orphaned_assets()
        for line in prune_log:
            if line.startswith("[prune]") or line.startswith("[warn]"):
                _warn(line[line.find("]") + 1:].strip())
        prune_removed = sum(1 for l in prune_log if l.startswith("[prune]"))
        prune_warned  = sum(1 for l in prune_log if l.startswith("[warn]"))
        if prune_removed or prune_warned:
            _logger._original_print(
                f"    Pruner: removed {prune_removed} file(s), {prune_warned} warning(s) — see PORTING_NOTES.txt"
            )
    with _logger.phase("Writing porting notes", total=0, unit="step", colour="yellow"):
        write_porting_notes()
    validation_warnings = []
    with _logger.phase("Validating output", total=0, unit="step", colour="blue"):
        validation_warnings = run_validation_pass()
    loot_dir   = os.path.join(BP_FOLDER, "loot_tables", "entities")
    loot_count = len(os.listdir(loot_dir)) if os.path.isdir(loot_dir) else 0
    recipe_dir   = os.path.join(BP_FOLDER, "recipes")
    recipe_count = len(os.listdir(recipe_dir)) if os.path.isdir(recipe_dir) else 0
    spawn_dir   = os.path.join(BP_FOLDER, "spawn_rules")
    spawn_count = len(os.listdir(spawn_dir)) if os.path.isdir(spawn_dir) else 0
    struct_dir  = os.path.join(BP_FOLDER, "structures")
    struct_count = len([f for f in os.listdir(struct_dir) if f.endswith(".mcstructure")]) if os.path.isdir(struct_dir) else 0
    feat_count  = len(os.listdir(os.path.join(BP_FOLDER, "features"))) if os.path.isdir(os.path.join(BP_FOLDER, "features")) else 0
    _orig("")
    _orig("")

    deobf_dir = os.path.join("Bedrock_Pack", "deobfuscated_java")
    deobf_map = os.path.join("Bedrock_Pack", "deobfuscated_java_map.json")
    for cleanup_path in (deobf_dir, deobf_map):
        if os.path.isdir(cleanup_path):
            shutil.rmtree(cleanup_path, ignore_errors=True)
        elif os.path.isfile(cleanup_path):
            os.remove(cleanup_path)

    shutil.make_archive("Bedrock_Pack", "zip", "Bedrock_Pack")
    shutil.move("Bedrock_Pack.zip", "Bedrock_Pack.mcaddon")

    for cleanup_path in (deobf_dir, deobf_map):
        if os.path.isdir(cleanup_path):
            try:
                shutil.rmtree(cleanup_path, ignore_errors=True)
            except Exception as e:
                _warn(f"Failed to delete {cleanup_path}: {e}")
        elif os.path.isfile(cleanup_path):
            try:
                os.remove(cleanup_path)
            except Exception as e:
                _warn(f"Failed to delete {cleanup_path}: {e}")

BEDROCK_API_IMPORTS: Dict[str, List[str]] = {"@minecraft/server": ["world", "system"]}

def generate_bedrock_script_boilerplate(namespace: str, entity_id: Optional[str] = None) -> list[str]:
    imports = sorted({name for names in BEDROCK_API_IMPORTS.values() for name in names})
    lines = [f'import {{ {", ".join(imports)} }} from "@minecraft/server";']
    if entity_id:
        lines += [
            '',
            f'// Bedrock Script API bootstrap for {namespace}:{entity_id}',
            f'const MOD_ID = "{namespace}:{sanitize_identifier(entity_id)}";',
            'function isTarget(entity) { return !!entity && (entity.typeId === MOD_ID || entity.typeId.endsWith(`:${MOD_ID.split(":").pop()}`)); }',
            '',
            'world.afterEvents.entitySpawn.subscribe(({ entity }) => {',
            '  if (!isTarget(entity)) return;',
            '  // attach per-entity tick / state here',
            '});',
            '',
            'world.afterEvents.entityHurt.subscribe(({ hurtEntity, damageSource }) => {',
            '  const entity = hurtEntity;',
            '  if (!isTarget(entity)) return;',
            '  // react to damage, status, or infection logic here',
            '});',
        ]
    return lines

def emit_bedrock_api_support_script(out_path: str, namespace: str, entity_id: str) -> None:
    lines = generate_bedrock_script_boilerplate(namespace, entity_id)
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines))

def _detect_project_loader() -> str:
    loaders = {'forge': 0, 'neoforge': 0, 'fabric': 0, 'quilt': 0}
    for root, _, files in os.walk('.'):
        for fname in files:
            low = fname.lower()
            path = os.path.join(root, fname)
            if low == 'fabric.mod.json':
                loaders['fabric'] += 3
                try:
                    data = json.loads(_read_text_file(path))
                    if isinstance(data, dict) and data.get('entrypoints'):
                        loaders['fabric'] += 1
                except Exception:
                    pass
            elif low == 'quilt.mod.json':
                loaders['quilt'] += 3
                try:
                    data = json.loads(_read_text_file(path))
                    if isinstance(data, dict) and data.get('quilt_loader'):
                        loaders['quilt'] += 1
                except Exception:
                    pass
            elif low == 'mods.toml':
                loaders['forge'] += 2
            elif low == 'neoforge.mods.toml':
                loaders['neoforge'] += 3
            elif low.endswith('.java'):
                code = _read_text_file(path)
                if '@Mixin' in code:
                    loaders['fabric'] += 1
                    loaders['quilt'] += 1
                if '@SubscribeEvent' in code:
                    loaders['forge'] += 1
    return max(loaders, key=loaders.get)

    return None

def _enhanced_postpass(namespace: str, java_files: Dict[str, str]) -> None:
    loader = _detect_project_loader()
    notes = []
    notes.append(f'[loader] detected project loader: {loader}')
    mixin_notes = scan_mixins(java_files, namespace)
    notes.extend(mixin_notes)
    scripts_dir = os.path.join(BP_FOLDER, 'scripts')
    os.makedirs(scripts_dir, exist_ok=True)
    runtime_path = os.path.join(scripts_dir, 'runtime_bridge.js')
    with open(runtime_path, 'w', encoding='utf-8') as fh:
        fh.write('\n'.join(generate_bedrock_runtime_bridge(namespace)))
    main_path = os.path.join(scripts_dir, 'main.js')
    _ensure_main_import(main_path, 'import "./runtime_bridge.js";\n')
    if os.path.exists(main_path):
        _ensure_main_import(main_path, 'import "./cap_registry.js";\n')
    report = {
        'namespace': namespace,
        'loader': loader,
        'java_files': len(java_files),
        'mixins': sum(1 for c in java_files.values() if '@Mixin' in c),
        'notes': notes[:500],
    }
    _safe_json_dump(os.path.join(OUTPUT_DIR, 'conversion_report.json'), report)
    if notes:
        port_notes = 'PORTING_NOTES.txt'
        with open(port_notes, 'a', encoding='utf-8') as fh:
            fh.write('\n'.join(notes) + '\n')

def run_pipeline(source_root: str = "."):
    _run_conversion_pipeline(source_root)
    try:
        java_files = read_all_java_files('.')
        namespace = detect_mod_id(java_files) if 'detect_mod_id' in globals() else None
        if not namespace:
            namespace = sanitize_identifier(os.path.basename(os.getcwd())) or 'converted'
        if not STATE.deobfuscated_java_files and java_files:
            java_files = deobfuscate_java_sources(java_files, namespace)
            STATE.all_java_files = java_files
        _enhanced_postpass(namespace, java_files)
    except Exception as e:
        try:
            log_critical_failure(f'Enhanced postpass failed: {e}')
        except Exception:
            pass
