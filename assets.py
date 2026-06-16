
def read_all_java_files(root_dir=".") -> Dict[str, str]:
    if root_dir == "." and _DEOBFUSCATED_JAVA_FILES and not _dir_has_java_files(root_dir):
        return dict(_DEOBFUSCATED_JAVA_FILES)

    root_dir = _preferred_java_root(root_dir)

    java_files = {}
    skip_dir = os.path.normpath(OUTPUT_DIR)
    for root, dirs, files in os.walk(root_dir):
        norm_root = os.path.normpath(root)
        dirs[:] = [
            d for d in dirs
            if not os.path.normpath(os.path.join(norm_root, d)).startswith(skip_dir + os.sep)
            and os.path.normpath(os.path.join(norm_root, d)) != skip_dir
        ]
        for f in files:
            if f.endswith(".java"):
                path = os.path.join(root, f)
                try:
                    with open(path, "r", encoding="utf-8", errors="ignore") as fh:
                        java_files[path] = fh.read()
                except Exception:
                    continue
    return java_files

def _is_probably_obfuscated_java_name(name: Optional[str]) -> bool:
    if not name:
        return True
    n = re.sub(r'[^A-Za-z0-9_$]', '', str(name))
    if not n:
        return True
    if len(n) <= 2:
        return True
    if re.fullmatch(r'(?:[a-zA-Z]|\d+|[A-Za-z]?\d+[A-Za-z]?)', n):
        return True
    if n.startswith(('func_', 'field_', 'm_', 'f_', 'lambda$', 'access$')):
        return True
    return False

def _infer_java_class_role(java_code: str, filename: str = '', cls_name: Optional[str] = None) -> str:
    haystack = ' '.join([
        str(cls_name or ''),
        os.path.basename(filename) or '',
        os.path.splitext(os.path.basename(filename))[0] if filename else '',
        java_code[:1600] if java_code else '',
    ]).lower()

    role_checks = [
        ('renderer', ['renderer', 'geomodel', 'geoentityrenderer', 'blockentityrenderer', 'layerrenderer']),
        ('model', ['model', 'layerdefinition', 'meshdefinition', 'modelpart', 'createbodylayer', 'getmodelresource']),
        ('screen', ['screen', 'gui', 'abstractcontainerscreen', 'container screen', 'chestscreen']),
        ('entity', ['extends entity', 'livingentity', 'mob', 'animal', 'monster', 'projectile', 'entitytype']),
        ('block', ['extends block', 'blockstate', 'blockentity', 'tileentity', 'createblockstatedefinition']),
        ('item', ['extends item', 'itemstack', 'useon', 'inventorytick']),
        ('goal', ['goal', 'pathfind', 'targetselector', 'setmutexbits']),
        ('event_handler', ['@subscribeevent', 'eventbus', 'forgeevent', 'clienttickevent', 'servertickevent']),
        ('mixin', ['@mixin', 'inject(', 'redirect(', 'overwrite(', 'accessor(', 'invoker(']),
        ('registry', ['deferredregister', 'registryobject', 'registerevent', 'bootstrapcontext']),
        ('capability', ['capability', 'ifluidhandler', 'ienergystorage', 'iitemhandler']),
        ('packet', ['packet', 'network', 'friendlybytebuf', 'serverbound', 'clientbound']),
    ]

    for role, needles in role_checks:
        if any(n in haystack for n in needles):
            return role

    if '@mod(' in haystack or 'modid' in haystack or 'fabric.mod.json' in haystack:
        return 'mod_main'

    return 'class'

def _unique_java_name(base: str, used: Set[str]) -> str:
    candidate = sanitize_identifier(base) or "class"
    if candidate not in used:
        used.add(candidate)
        return candidate
    index = 2
    while f"{candidate}_{index}" in used:
        index += 1
    candidate = f"{candidate}_{index}"
    used.add(candidate)
    return candidate

def _semantic_member_name(role: str, kind: str, member_name: str, return_type: str = '', param_types: Optional[List[str]] = None, java_code: str = '') -> Optional[str]:
    if not _is_probably_obfuscated_java_name(member_name):
        return None

    lowered = (java_code or '').lower()
    return_type = (return_type or '').strip()

    if kind == 'method':
        if role == 'renderer':
            if 'resourcelocation' in return_type.lower():
                if 'texture' in lowered:
                    return 'getTextureLocation'
                if 'model' in lowered or 'geo' in lowered:
                    return 'getModelResource'
            if return_type.lower() == 'void':
                return 'render'
        elif role == 'model':
            if 'layerdefinition' in return_type.lower():
                return 'createBodyLayer'
            if return_type.lower() == 'void' and len(param_types or []) >= 4:
                return 'setupAnim'
        elif role == 'screen':
            if return_type.lower() == 'void':
                if 'init' in lowered:
                    return 'init'
                return 'render'
        elif role == 'registry':
            return 'register'
        elif role == 'event_handler':
            return 'handleEvent'
        elif role == 'goal':
            return 'tick'
        elif role == 'entity':
            if return_type.lower() == 'void':
                return 'tick'
        elif role == 'packet':
            if return_type.lower() == 'void':
                if 'encode' in lowered:
                    return 'encode'
                if 'decode' in lowered:
                    return 'decode'
                return 'handlePacket'
        return f"{role}_method"

    if kind == 'field':
        if role == 'renderer':
            return 'render_state'
        if role == 'model':
            return 'model_state'
        if role == 'entity':
            return 'entity_state'
        return f"{role}_field"

    return None

def _rewrite_java_identifiers(source: str, rename_map: Dict[str, str]) -> str:
    if not rename_map:
        return source
    for old_name, new_name in sorted(rename_map.items(), key=lambda kv: len(kv[0]), reverse=True):
        if not old_name or not new_name or old_name == new_name:
            continue
        source = re.sub(rf'\b{re.escape(old_name)}\b', new_name, source)
    return source


import urllib.request
import urllib.error


_MOJANG_MAPPING_CACHE: Dict[str, Tuple[Dict[str, str], Dict[str, str]]] = {}


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


def _cached_path(mc_version: str) -> str:
    os.makedirs(_MAPPING_CACHE_DIR, exist_ok=True)
    return os.path.join(_MAPPING_CACHE_DIR, f"client_{mc_version}.txt")


def download_mojang_mappings(mc_version: str) -> Optional[str]:

    cache_file = _cached_path(mc_version)
    if os.path.isfile(cache_file):
        try:
            return open(cache_file, encoding='utf-8', errors='ignore').read()
        except Exception:
            pass


    manifest_data = _fetch_url(_MOJANG_VERSION_MANIFEST)
    if not manifest_data:
        return None
    try:
        manifest = json.loads(manifest_data.decode('utf-8'))
    except Exception:
        return None

    version_url = None
    for entry in manifest.get('versions', []):
        if entry.get('id') == mc_version:
            version_url = entry.get('url')
            break
    if not version_url:

        for entry in manifest.get('versions', []):
            if entry.get('id', '').startswith(mc_version):
                version_url = entry.get('url')
                break
    if not version_url:
        return None


    version_data = _fetch_url(version_url)
    if not version_data:
        return None
    try:
        version_json = json.loads(version_data.decode('utf-8'))
    except Exception:
        return None

    mappings_url = (
        version_json
        .get('downloads', {})
        .get('client_mappings', {})
        .get('url')
    )
    if not mappings_url:

        return None

    mapping_data = _fetch_url(mappings_url)
    if not mapping_data:
        return None

    mapping_text = mapping_data.decode('utf-8', errors='ignore')
    try:
        with open(cache_file, 'w', encoding='utf-8') as fh:
            fh.write(mapping_text)
    except Exception:
        pass
    return mapping_text


def parse_mojang_mappings(mapping_text: str) -> Tuple[Dict[str, str], Dict[str, str]]:

    field_map: Dict[str, str] = {}
    method_map: Dict[str, str] = {}

    for line in mapping_text.splitlines():
        line = line.rstrip()
        if not line or line.startswith('#'):
            continue

        if not line.startswith('    '):
            continue

        stripped = line.strip()
        if ' -> ' not in stripped:
            continue
        readable_side, obf_name = stripped.rsplit(' -> ', 1)
        obf_name = obf_name.strip()


        readable_side = re.sub(r'^\d+:\d+:', '', readable_side).strip()

        parts = readable_side.split()
        if len(parts) < 2:
            continue
        readable_name_full = parts[1]

        if '(' in readable_name_full:

            readable_name = readable_name_full[:readable_name_full.index('(')]
        else:
            readable_name = readable_name_full

        if not obf_name or not readable_name or obf_name == readable_name:
            continue

        if '(' in readable_name_full:

            if len(obf_name) <= 3 or re.fullmatch(r'[a-z]{1,3}\d*', obf_name):
                method_map[obf_name] = readable_name
        else:
            if len(obf_name) <= 3 or re.fullmatch(r'[a-z]{1,3}\d*', obf_name):
                field_map[obf_name] = readable_name

    return field_map, method_map


_SRG_BUILTIN_REMAP: Dict[str, str] = {

    "m_135353_": "defineId",      "m_135370_": "get",
    "m_135372_": "define",        "m_135381_": "set",
    "f_135030_": "STRING",        "f_135035_": "BOOLEAN",
    "f_135028_": "BYTE",          "f_135029_": "INTEGER",
    "f_135031_": "FLOAT",         "f_135032_": "OPTIONAL_BLOCK_POS",
    "f_135033_": "DIRECTION",     "f_135034_": "OPTIONAL_UUID",
    "f_135036_": "COMPOUND_TAG",  "f_135040_": "POSE",

    "f_19804_": "entityData",     "f_19853_": "level",
    "f_20919_": "deathTime",      "f_21364_": "noPhysics",
    "f_20904_": "hurtTime",       "f_20926_": "lastHurt",
    "f_20909_": "invulnerableTime","f_20907_": "health",
    "f_20917_": "absorptionAmount","f_21153_": "jumping",
    "f_21345_": "goalSelector",   "f_21346_": "targetSelector",

    "m_5654_": "getAddEntityPacket","m_5912_": "isSprinting",
    "m_5993_": "killedEntity",    "m_6075_": "tick",
    "m_6153_": "tickDeath",       "m_6210_": "checkDespawn",
    "m_6336_": "getMobType",      "m_6469_": "hurt",
    "m_6518_": "finalizeSpawn",   "m_6639_": "getAttackReachSqr",
    "m_6785_": "requiresCustomPersistence",
    "m_6972_": "getDimensions",   "m_7515_": "getAmbientSound",
    "m_7640_": "getDirectEntity", "m_7975_": "getHurtSound",
    "m_5592_": "getDeathSound",   "m_8097_": "defineSynchedData",
    "m_8099_": "registerGoals",   "m_25352_": "addGoal",

    "m_20185_": "getX",           "m_20186_": "getY",
    "m_20189_": "getZ",           "m_20388_": "scale",
    "m_20202_": "setPos",         "m_20219_": "getDeltaMovement",
    "m_20223_": "setDeltaMovement",

    "m_21226_": "dropExperience", "m_21530_": "xpReward",
    "m_21552_": "createLivingAttributes",
    "m_21557_": "setCanPickUpLoot","m_142687_": "remove",

    "m_22268_": "add",
    "f_22276_": "FOLLOW_RANGE",   "f_22277_": "ARMOR",
    "f_22278_": "ATTACK_DAMAGE",  "f_22279_": "MOVEMENT_SPEED",
    "f_22280_": "ATTACK_SPEED",   "f_22281_": "KNOCKBACK_RESISTANCE",
    "f_22282_": "LUCK",           "f_22284_": "MAX_HEALTH",
    "f_22285_": "FLYING_SPEED",   "f_22286_": "ARMOR_TOUGHNESS",
    "f_22287_": "ATTACK_KNOCKBACK",

    "f_19306_": "IN_WALL",        "f_19307_": "CRAMMING",
    "f_19308_": "IN_FIRE",        "f_19310_": "ON_FIRE",
    "f_19311_": "LAVA",           "f_19312_": "HOT_FLOOR",
    "f_19313_": "STUCK",          "f_19314_": "DROWN",
    "f_19315_": "STARVE",         "f_19316_": "CACTUS",
    "f_19317_": "FALL",           "f_19319_": "OUT_OF_WORLD",
    "f_19320_": "GENERIC",        "f_19321_": "MAGIC",
    "f_19322_": "WITHER",         "f_19323_": "DRAGON_BREATH",
    "f_19326_": "FREEZE",

    "m_19372_": "isMagic",        "m_19374_": "isExplosion",
    "m_19376_": "isFire",         "m_19378_": "isProjectile",
    "m_19380_": "isBypassArmor",  "m_19385_": "getMsgId",
    "m_19388_": "getEntity",

    "m_7565_": "above",           "m_7566_": "below",
    "m_7567_": "north",           "m_7568_": "south",
    "m_7569_": "east",            "m_7570_": "west",

    "m_128366_": "getInt",        "m_128380_": "putInt",
    "m_128390_": "getFloat",      "m_128393_": "putFloat",
    "m_128425_": "getBoolean",    "m_128432_": "putBoolean",
    "m_128369_": "getString",     "m_128419_": "putString",
    "m_128442_": "contains",      "m_128445_": "remove",

    "m_8950_": "getBlockState",   "m_7702_": "setBlock",
    "m_6904_": "addFreshEntity",  "m_7918_": "playSound",
    "m_6552_": "explode",

    "m_6696_": "addAdditionalSaveData",
    "m_6701_": "readAdditionalSaveData",

    "f_21637_": "UNDEFINED",      "f_21638_": "UNDEAD",
    "f_21639_": "ARTHROPOD",      "f_21640_": "ILLAGER",
    "f_21641_": "WATER",
}

_SRG_PAT = re.compile(r'\b([fm]_\d+_)\b')


def _apply_srg_builtin_remap(source: str) -> str:

    return _SRG_PAT.sub(
        lambda m: _SRG_BUILTIN_REMAP.get(m.group(1), m.group(1)),
        source,
    )

_MOJ_FIELD_PAT  = re.compile(r'\b([a-z]{1,3}\d*)\b')
_MOJ_METHOD_PAT = re.compile(r'\b([a-z]{1,3}\d*)\s*\(')


def apply_mojang_mappings_to_source(
    source: str,
    field_map: Dict[str, str],
    method_map: Dict[str, str],
) -> str:

    if not field_map and not method_map:
        return source

    combined = {}
    combined.update(field_map)
    combined.update(method_map)

    if not combined:
        return source

    escaped = '|'.join(re.escape(k) for k in sorted(combined, key=len, reverse=True))
    pat = re.compile(rf'\b({escaped})\b')

    def _replace(m: re.Match) -> str:
        token = m.group(1)

        return combined.get(token, token)

    return pat.sub(_replace, source)


_DETECTED_MC_VERSION: Optional[str] = None


def _get_or_fetch_mojang_maps(
    jar_path: Optional[str] = None,
) -> Tuple[Dict[str, str], Dict[str, str]]:

    global _DETECTED_MC_VERSION

    mc_version = _DETECTED_MC_VERSION or detect_minecraft_version(jar_path)
    if mc_version:
        _DETECTED_MC_VERSION = mc_version

    if not mc_version:
        return {}, {}

    if mc_version in _MOJANG_MAPPING_CACHE:
        return _MOJANG_MAPPING_CACHE[mc_version]

    mapping_text = download_mojang_mappings(mc_version)
    if not mapping_text:
        _MOJANG_MAPPING_CACHE[mc_version] = ({}, {})
        return {}, {}

    field_map, method_map = parse_mojang_mappings(mapping_text)
    _MOJANG_MAPPING_CACHE[mc_version] = (field_map, method_map)
    return field_map, method_map


def deobfuscate_java_sources(java_files: Dict[str, str], namespace: str = "") -> Dict[str, str]:
    global _DEOBFUSCATED_JAVA_FILES, _DEOBFUSCATED_JAVA_PATHS

    out_root = os.path.join(OUTPUT_DIR, "deobfuscated_java")
    os.makedirs(out_root, exist_ok=True)

    abs_out_root = os.path.abspath(out_root)
    input_paths = [os.path.abspath(path) for path in java_files.keys()]
    if input_paths and all(p.startswith(abs_out_root + os.sep) or p == abs_out_root for p in input_paths):
        _DEOBFUSCATED_JAVA_FILES = dict(java_files)
        _DEOBFUSCATED_JAVA_PATHS = {path: path for path in java_files}
        return dict(java_files)

    java_files = {path: _apply_srg_builtin_remap(code)
                  for path, code in java_files.items()}

    try:
        jar_path = next(
            (os.path.abspath(f) for f in os.listdir('.')
             if f.endswith('.jar') and os.path.isfile(f)),
            None,
        )
        field_map, method_map = _get_or_fetch_mojang_maps(jar_path)
        if field_map or method_map:
            java_files = {
                path: apply_mojang_mappings_to_source(code, field_map, method_map)
                for path, code in java_files.items()
            }
    except Exception:
        pass

    class_name_map: Dict[str, str] = {}
    path_to_class: Dict[str, str] = {}
    used_class_names: Set[str] = set()

    for path, code in java_files.items():
        cls_name = extract_class_name(code) or os.path.splitext(os.path.basename(path))[0]
        path_to_class[path] = cls_name
        class_name_map[cls_name] = cls_name

    rewritten: Dict[str, str] = {}
    rewritten_paths: Dict[str, str] = {}

    for path, code in java_files.items():
        cls_name = path_to_class.get(path) or extract_class_name(code) or os.path.splitext(os.path.basename(path))[0]
        rename_map: Dict[str, str] = {}

        new_class_name = class_name_map.get(cls_name, cls_name)

        ast = JavaAST(code)
        ast._parse()
        tree = getattr(ast, '_tree', None)
        if tree is not None:
            try:
                for _, node in tree.filter(javalang.tree.ClassDeclaration):
                    if node.name != cls_name:
                        continue
                    break
            except Exception:
                pass

        new_code = _rewrite_java_identifiers(code, rename_map)

        rel_path = os.path.relpath(path, '.')
        rel_dir = os.path.dirname(rel_path)
        target_dir = os.path.join(out_root, rel_dir)
        os.makedirs(target_dir, exist_ok=True)
        safe_name = sanitize_identifier(new_class_name or cls_name) or sanitize_identifier(os.path.splitext(os.path.basename(path))[0]) or "Class"
        target_name = f"{safe_name}.java"
        target_path = os.path.join(target_dir, target_name)
        try:
            with open(target_path, 'w', encoding='utf-8') as fh:
                fh.write(new_code)
        except Exception:
            pass

        rewritten[target_path] = new_code
        rewritten_paths[path] = target_path

    _DEOBFUSCATED_JAVA_FILES = rewritten
    _DEOBFUSCATED_JAVA_PATHS = rewritten_paths

    try:
        _safe_json_dump(
            os.path.join(OUTPUT_DIR, 'deobfuscated_java_map.json'),
            {
                'namespace': namespace,
                'source_files': len(java_files),
                'deobfuscated_files': len(rewritten),
                'paths': rewritten_paths,
                'classes': class_name_map,
            },
        )
    except Exception:
        pass

    return rewritten

def extract_class_name(java_code: str) -> Optional[str]:
    ast = JavaAST(java_code)
    name = ast.primary_class_name()
    if name:
        return name
    m = re.search(r'\b(public\s+)?(class|interface|enum)\s+([A-Z][A-Za-z0-9_]*)', java_code)
    if m:
        return m.group(3)
    return None
def find_model_geometry_in_code(java_code: str) -> Optional[Tuple[Optional[str], str]]:
    ast = JavaAST(java_code)
    ast._parse()
    if ast._tree is not None:
        for node in ast.object_creations_of('ResourceLocation'):
            args = getattr(node, 'arguments', []) or []
            ns_val, path_val = None, None
            if len(args) >= 2:
                if isinstance(args[0], javalang.tree.Literal):
                    ns_val = args[0].value.strip('"').strip("'")
                if isinstance(args[1], javalang.tree.Literal):
                    path_val = args[1].value.strip('"').strip("'")
            elif len(args) == 1:
                if isinstance(args[0], javalang.tree.Literal):
                    raw = args[0].value.strip('"').strip("'")
                    if ':' in raw:
                        ns_val, path_val = raw.split(':', 1)
                    else:
                        path_val = raw
            if path_val and ('geo/' in path_val or path_val.endswith('.geo.json') or path_val.endswith('.geo')):
                base = os.path.basename(path_val)
                name = re.sub(r'\.geo(\.json)?$', '', base, flags=re.IGNORECASE)
                return (ns_val.lower() if ns_val else None, sanitize_identifier(name))
        for lit in ast.all_string_literals():
            if 'geo/' in lit or lit.endswith('.geo.json') or lit.endswith('.geo'):
                ns, path = (lit.split(':', 1) if ':' in lit else (None, lit))
                base = os.path.basename(path)
                name = re.sub(r'\.geo(\.json)?$', '', base, flags=re.IGNORECASE)
                return (ns.lower() if ns else None, sanitize_identifier(name))
    m = re.search(r'new\s+ResourceLocation\s*\(\s*["\']([a-z0-9_\-]+)["\']\s*,\s*["\']([^"\']*?geo/[^"\']*?\.geo(?:\.json)?)["\']\s*\)', java_code, re.IGNORECASE)
    if m:
        ns = m.group(1).lower()
        base = os.path.basename(m.group(2))
        name = re.sub(r'\.geo(\.json)?$', '', base, flags=re.IGNORECASE)
        return (ns, sanitize_identifier(name))
    m2 = re.search(r'new\s+ResourceLocation\s*\(\s*["\']([a-z0-9_\-]+:[^"\']*?geo/[^"\']*?\.geo(?:\.json)?)["\']\s*\)', java_code, re.IGNORECASE)
    if m2:
        raw = m2.group(1)
        ns, path = (raw.split(':', 1) if ':' in raw else (None, raw))
        name = re.sub(r'\.geo(\.json)?$', '', os.path.basename(path), flags=re.IGNORECASE)
        return (ns.lower() if ns else None, sanitize_identifier(name))
    m3 = re.search(r'["\']([a-z0-9_\-:\/]*?geo\/[a-z0-9_\-]+(?:\.geo(?:\.json)?)?)["\']', java_code, re.IGNORECASE)
    if m3:
        raw = m3.group(1)
        ns, path = (raw.split(':', 1) if ':' in raw else (None, raw))
        name = re.sub(r'\.geo(\.json)?$', '', os.path.basename(path), flags=re.IGNORECASE)
        return (ns.lower() if ns else None, sanitize_identifier(name))
    m5 = re.search(r'["\']([a-z0-9_\-:\/]+\.geo(?:\.json)?)["\']', java_code, re.IGNORECASE)
    if m5:
        raw = m5.group(1)
        ns, path = (raw.split(':', 1) if ':' in raw else (None, raw))
        name = re.sub(r'\.geo(\.json)?$', '', os.path.basename(path), flags=re.IGNORECASE)
        return (ns.lower() if ns else None, sanitize_identifier(name))
    return None
_RENDERER_MAP: Dict[str, Dict] = {}
def build_renderer_entity_map():
    global _RENDERER_MAP
    _RENDERER_MAP = {}
    renderer_to_entity: Dict[str, str] = {}
    model_to_entity: Dict[str, str] = {}
    entity_to_renderer: Dict[str, str] = {}
    cls_to_code: Dict[str, str] = {}
    cls_to_path: Dict[str, str] = {}
    for path, code in _ALL_JAVA_FILES.items():
        cls = extract_class_name(code)
        if not cls:
            continue
        cls_to_code[cls] = code
        cls_to_path[cls] = path
        m = re.search(
            r'\bclass\s+(\w+)\s+extends\s+\w*(?:Renderer|Render)\w*\s*<\s*(\w+)',
            code
        )
        if m:
            renderer_cls, entity_arg = m.group(1), m.group(2)
            renderer_to_entity[renderer_cls] = entity_arg
        m2 = re.search(
            r'\bclass\s+(\w+)\s+extends\s+\w*(?:Model|GeoModel)\w*\s*<\s*(\w+)',
            code
        )
        if m2:
            model_cls, entity_arg = m2.group(1), m2.group(2)
            model_to_entity[model_cls] = entity_arg
        for m3 in re.finditer(
            r'EntityRenderers\s*\.\s*register\s*\(\s*(\w+(?:\.\w+)*)\s*,\s*(\w+)\s*::',
            code
        ):
            etype_expr, renderer_cls = m3.group(1), m3.group(2)
            etype_simple = etype_expr.split(".")[-1]
            entity_to_renderer[etype_simple] = renderer_cls
            entity_to_renderer[etype_simple.lower()] = renderer_cls
        for m4 in re.finditer(
            r'registerEntityRenderingHandler\s*\(\s*(\w+(?:\.\w+)*)\s*,\s*\w+\s*->\s*new\s+(\w+)',
            code
        ):
            etype_expr, renderer_cls = m4.group(1), m4.group(2)
            etype_simple = etype_expr.split(".")[-1]
            entity_to_renderer[etype_simple] = renderer_cls
            entity_to_renderer[etype_simple.lower()] = renderer_cls
        for m5 in re.finditer(
            r'bindEntityRenderer\s*\(\s*(\w+)\.class\s*,\s*(\w+)\.class',
            code
        ):
            entity_to_renderer[m5.group(1)] = m5.group(2)
    renderer_to_model: Dict[str, str] = {}
    for renderer_cls, rcode in {c: cls_to_code[c] for c in renderer_to_entity if c in cls_to_code}.items():
        m = re.search(r'super\s*\([^)]*new\s+(\w+)', rcode)
        if m:
            renderer_to_model[renderer_cls] = m.group(1)
        m2 = re.search(r'this\.model\s*=\s*new\s+(\w+)', rcode)
        if m2:
            renderer_to_model[renderer_cls] = m2.group(1)
    def _put(entity_cls: str, renderer_cls: Optional[str], model_cls: Optional[str]):
        if not entity_cls:
            return
        entry = _RENDERER_MAP.setdefault(entity_cls, {})
        if renderer_cls and "renderer" not in entry:
            entry["renderer"] = renderer_cls
            entry["renderer_code"] = cls_to_code.get(renderer_cls, "")
        if model_cls and "model" not in entry:
            entry["model"] = model_cls
            entry["model_code"] = cls_to_code.get(model_cls, "")
    for renderer_cls, entity_cls in renderer_to_entity.items():
        model_cls = renderer_to_model.get(renderer_cls)
        _put(entity_cls, renderer_cls, model_cls)
        _put(renderer_cls, renderer_cls, model_cls)
    for model_cls, entity_cls in model_to_entity.items():
        _put(entity_cls, None, model_cls)
    for etype_key, renderer_cls in entity_to_renderer.items():
        camel = "".join(w.capitalize() for w in etype_key.lower().split("_"))
        model_cls = renderer_to_model.get(renderer_cls)
        _put(camel,    renderer_cls, model_cls)
        _put(etype_key, renderer_cls, model_cls)
    found = sum(1 for v in _RENDERER_MAP.values() if v.get("renderer") or v.get("model"))

def build_geckolib_mappings(java_root="."):
    java_files = read_all_java_files(java_root)
    class_to_path: Dict[str, str] = {}
    class_code_map: Dict[str, str] = {}
    for path, code in java_files.items():
        cls = extract_class_name(code)
        if cls:
            class_to_path[cls] = path
            class_code_map[cls] = code
    model_map: Dict[str, Tuple[Optional[str], str]] = {}
    renderer_model: Dict[str, str] = {}
    renderer_entity: Dict[str, str] = {}
    for path, code in class_code_map.items():
        geom = find_model_geometry_in_code(code)
        if geom:
            model_map[path] = geom
    for cls, code in class_code_map.items():
        ast = JavaAST(code)
        ast._parse()
        if ast._tree is not None:
            for cls_decl in ast.get_class_declarations():
                if cls_decl.extends and hasattr(cls_decl.extends, 'name'):
                    if cls_decl.extends.name == 'GeoEntityRenderer':
                        args = getattr(cls_decl.extends, 'arguments', None) or []
                        if args:
                            arg = args[0]
                            ent = JavaAST.strip_generics(
                                arg.type.name if hasattr(arg, 'type') and hasattr(arg.type, 'name')
                                else (arg.name if hasattr(arg, 'name') else '')
                            )
                            if ent:
                                renderer_entity[cls] = ent
            for ctype in ast.all_object_creation_types():
                if ctype in class_code_map and ('Model' in ctype or ctype in model_map):
                    renderer_model[cls] = ctype
                    break
        else:
            m = re.search(r'extends\s+GeoEntityRenderer\s*<\s*([A-Za-z0-9_<>.,\s]+)\s*>', code)
            if m:
                ent = re.sub(r'<.*?>', '', m.group(1).split(",")[0]).strip()
                if ent:
                    renderer_entity[cls] = ent
            model_candidates = set(re.findall(r'new\s+([A-Z][A-Za-z0-9_]*)\s*\(', code))
            for cand in model_candidates:
                if cand in class_code_map and ('Model' in cand or cand in model_map):
                    renderer_model[cls] = cand
                    break
        if cls not in renderer_model:
            m2 = re.search(r'([A-Z][A-Za-z0-9_]*Model)\s+[a-zA-Z0-9_]+\s*=\s*new\s+([A-Z][A-Za-z0-9_]*Model)\s*\(', code)
            if m2:
                renderer_model[cls] = m2.group(1)
    entity_to_geometry: Dict[str, Tuple[Optional[str], str]] = {}
    entity_to_model: Dict[str, str] = {}
    for renderer_cls, model_cls in renderer_model.items():
        geom = model_map.get(model_cls)
        ent = renderer_entity.get(renderer_cls)
        if ent and geom:
            entity_to_geometry[ent] = geom
            entity_to_model[ent] = model_cls
    for renderer_cls, code in class_code_map.items():
        if renderer_cls not in renderer_model:
            ast = JavaAST(code)
            ast._parse()
            found_model = None
            if ast._tree is not None:
                for ctype in ast.all_object_creation_types():
                    if ctype in model_map:
                        found_model = ctype
                        break
            else:
                m = re.search(r'super\s*\(\s*[^\)]*new\s+([A-Z][A-Za-z0-9_]*)\s*\(', code)
                if m:
                    found_model = m.group(1)
            if found_model and found_model in model_map:
                renderer_model[renderer_cls] = found_model
        if renderer_cls in renderer_model and renderer_cls not in renderer_entity:
            ast2 = JavaAST(code)
            ast2._parse()
            if ast2._tree is not None:
                for cls_decl in ast2.get_class_declarations():
                    if cls_decl.extends and cls_decl.extends.name == 'GeoEntityRenderer':
                        args = getattr(cls_decl.extends, 'arguments', None) or []
                        if args:
                            arg = args[0]
                            ent = JavaAST.strip_generics(
                                arg.type.name if hasattr(arg, 'type') and hasattr(arg.type, 'name')
                                else (arg.name if hasattr(arg, 'name') else '')
                            )
                            if ent:
                                renderer_entity[renderer_cls] = ent
            else:
                m2 = re.search(r'extends\s+GeoEntityRenderer\s*<\s*([A-Za-z0-9_<>.,\s]+)\s*>', code)
                if m2:
                    ent = re.sub(r'<.*?>', '', m2.group(1).split(",")[0]).strip()
                    if ent:
                        renderer_entity[renderer_cls] = ent
    for renderer_cls, model_cls in renderer_model.items():
        ent = renderer_entity.get(renderer_cls)
        geom = model_map.get(model_cls)
        if ent and geom:
            entity_to_geometry[ent] = geom
            entity_to_model[ent] = model_cls
    global _LAYERDEF_GEO_MAP
    if _LAYERDEF_GEO_MAP:
        for renderer_cls, model_cls in renderer_model.items():
            if model_cls in _LAYERDEF_GEO_MAP:
                ent = renderer_entity.get(renderer_cls)
                if ent and ent not in entity_to_geometry:
                    geo_id = _LAYERDEF_GEO_MAP[model_cls]
                    parts = geo_id.split('.')
                    ns_h  = parts[1] if len(parts) >= 3 else None
                    nm_h  = '.'.join(parts[2:]) if len(parts) >= 3 else geo_id
                    entity_to_geometry[ent] = (ns_h, nm_h)
                    entity_to_model[ent]    = model_cls
        for model_cls, geo_id in _LAYERDEF_GEO_MAP.items():
            for ent_cls in renderer_entity.values():
                if ent_cls not in entity_to_geometry:
                    mc_stem = re.sub(r'(?i)Model$', '', model_cls).lower()
                    en_stem = re.sub(r'(?i)Entity$', '', ent_cls).lower()
                    if mc_stem and en_stem and mc_stem == en_stem:
                        parts = geo_id.split('.')
                        ns_h  = parts[1] if len(parts) >= 3 else None
                        nm_h  = '.'.join(parts[2:]) if len(parts) >= 3 else geo_id
                        entity_to_geometry[ent_cls] = (ns_h, nm_h)
    return {
        "class_code_map": class_code_map,
        "class_to_path": class_to_path,
        "model_map": model_map,
        "renderer_model": renderer_model,
        "renderer_entity": renderer_entity,
        "entity_to_geometry": entity_to_geometry,
        "entity_to_model": entity_to_model
    }
_JAVA_ATTR_NAME_MAP: Dict[str, str] = {
    "MAX_HEALTH": "health",
    "GENERIC_MAX_HEALTH": "health",
    "maxHealth": "health",
    "HEALTH": "health",
    "MOVEMENT_SPEED": "movement_speed",
    "GENERIC_MOVEMENT_SPEED": "movement_speed",
    "movementSpeed": "movement_speed",
    "FLYING_SPEED": "movement_speed",
    "SWIM_SPEED": "movement_speed",
    "ATTACK_DAMAGE": "attack_damage",
    "GENERIC_ATTACK_DAMAGE": "attack_damage",
    "attackDamage": "attack_damage",
    "ATTACK_SPEED": "attack_speed",
    "GENERIC_ATTACK_SPEED": "attack_speed",
    "ATTACK_KNOCKBACK": "attack_knockback",
    "GENERIC_ATTACK_KNOCKBACK": "attack_knockback",
    "FOLLOW_RANGE": "follow_range",
    "GENERIC_FOLLOW_RANGE": "follow_range",
    "followRange": "follow_range",
    "ARMOR": "armor",
    "GENERIC_ARMOR": "armor",
    "ARMOR_TOUGHNESS": "armor_toughness",
    "GENERIC_ARMOR_TOUGHNESS": "armor_toughness",
    "KNOCKBACK_RESISTANCE": "knockback_resistance",
    "GENERIC_KNOCKBACK_RESISTANCE": "knockback_resistance",
    "knockbackResistance": "knockback_resistance",
    "LUCK": "luck",
    "GENERIC_LUCK": "luck",
    "HORSE_JUMP_STRENGTH": "jump_strength",
    "ZOMBIE_SPAWN_REINFORCEMENTS": "spawn_reinforcements",
    "SPAWN_REINFORCEMENTS_CHANCE": "spawn_reinforcements",
}
_SRG_ATTR_FIELD_MAP: Dict[str, str] = {
    "f_22279_": "movement_speed",
    "f_22276_": "follow_range",
    "f_22284_": "health",
    "f_22281_": "knockback_resistance",
    "f_22277_": "armor",
    "f_22278_": "attack_damage",
    "m_6113_": "health",
    "m_6114_": "follow_range",
    "m_6115_": "movement_speed",
    "m_6116_": "attack_damage",
}
def _parse_java_float(s: str) -> Optional[float]:
    if s is None:
        return None
    cleaned = re.sub(r'[DdFfLl]$', '', str(s).strip())
    try:
        return float(cleaned)
    except (ValueError, TypeError):
        return None
def _extract_attr_block(java_code: str) -> str:
    method_patterns = [
        r'(?:public\s+static\s+)?(?:AttributeSupplier|AttributeModifierMap|AttributeMap|Builder)\s*'
        r'[\w.]*\s*createAttributes\s*\(\s*\)\s*\{',
        r'(?:public\s+static\s+)?(?:AttributeSupplier|AttributeModifierMap|AttributeMap|Builder)\s*'
        r'[\w.]*\s*getDefaultAttributes\s*\(\s*\)\s*\{',
        r'(?:public\s+static\s+)?(?:AttributeSupplier|AttributeModifierMap|Builder)\s*'
        r'[\w.]*\s*createMobAttributes\s*\(\s*\)\s*\{',
        r'(?:public\s+static\s+)?(?:AttributeSupplier|AttributeModifierMap|Builder)\s*'
        r'[\w.]*\s*createMonsterAttributes\s*\(\s*\)\s*\{',
        r'(?:public\s+static\s+)?(?:AttributeSupplier|AttributeModifierMap|Builder)\s*'
        r'[\w.]*\s*createAnimalAttributes\s*\(\s*\)\s*\{',
        r'static\s+\w*Builder\w*\s+\w+Attributes\w*\s*\(\s*\)\s*\{',
    ]
    for pat in method_patterns:
        m = re.search(pat, java_code, re.IGNORECASE | re.DOTALL)
        if m:
            start = m.end() - 1
            depth = 0
            i = start
            while i < len(java_code):
                if java_code[i] == '{':
                    depth += 1
                elif java_code[i] == '}':
                    depth -= 1
                    if depth == 0:
                        return java_code[start:i + 1]
                i += 1
    return java_code
def extract_attributes_from_java(java_code: str) -> dict:
    results: Dict[str, float] = {}
    block = _extract_attr_block(java_code)
    for m in re.finditer(
        r'\.add\s*\(\s*(?:[A-Za-z0-9_$]+\.)+([A-Z_][A-Z0-9_]*)\s*,\s*([-+]?[0-9]*\.?[0-9]+[DdFfLl]?)\s*\)',
        block, re.DOTALL
    ):
        bedrock_key = _JAVA_ATTR_NAME_MAP.get(m.group(1))
        val = _parse_java_float(m.group(2))
        if bedrock_key and val is not None and bedrock_key not in results:
            results[bedrock_key] = val
    for m in re.finditer(
        r'\.add\s*\(\s*["\']([A-Za-z_.]+)["\']\s*,\s*([-+]?[0-9]*\.?[0-9]+[DdFfLl]?)\s*\)',
        block, re.DOTALL
    ):
        raw_name = m.group(1).split(".")[-1].split(":")[-1]
        upper = re.sub(r'(?<=[a-z])(?=[A-Z])', '_', raw_name).upper()
        bedrock_key = _JAVA_ATTR_NAME_MAP.get(raw_name) or _JAVA_ATTR_NAME_MAP.get(upper)
        val = _parse_java_float(m.group(2))
        if bedrock_key and val is not None and bedrock_key not in results:
            results[bedrock_key] = val
    if not results:
        for m in re.finditer(
            r'\.add\s*\(\s*(f_[0-9_]+_)\s*,\s*([-+]?[0-9]*\.?[0-9]+[DdFfLl]?)\s*\)',
            block, re.DOTALL
        ):
            bedrock_key = _SRG_ATTR_FIELD_MAP.get(m.group(1))
            val = _parse_java_float(m.group(2))
            if bedrock_key and val is not None and bedrock_key not in results:
                results[bedrock_key] = val
    if not results:
        POSITIONAL_ORDER = [
            "movement_speed", "follow_range", "health",
            "knockback_resistance", "armor", "attack_damage"
        ]
        values = re.findall(r',\s*([-+]?[0-9]*\.?[0-9]+[DdFfLl]?)', block)
        for i, val_str in enumerate(values):
            if i < len(POSITIONAL_ORDER):
                val = _parse_java_float(val_str)
                if val is not None:
                    results[POSITIONAL_ORDER[i]] = val

    results = _normalize_entity_attributes(results, block)
    return results

def _normalize_entity_attributes(results: Dict[str, float], block: str = "") -> Dict[str, float]:
    if not results:
        return results

    out = dict(results)

    health = out.get("health")
    armor = out.get("armor")


    if health is not None and armor is not None:
        if (health <= 0 or health < 1) and armor >= 20:
            out["health"] = armor
            out["armor"] = 0.0
        elif armor > health and armor >= 100 and health <= 20:
            out["health"], out["armor"] = armor, health


    if out.get("health", 0) <= 0:
        m = re.search(
            r'(?:setHealth|setMaxHealth|setCurrentValue|getAttribute\s*\(\s*Attributes\.(?:MAX_HEALTH|GENERIC_MAX_HEALTH|HEALTH)\s*\))\s*'
            r'(?:\(|\.setBaseValue\s*\()\s*([-+]?[0-9]*\.?[0-9]+[DdFfLl]?)',
            block,
            re.IGNORECASE | re.DOTALL
        )
        if m:
            val = _parse_java_float(m.group(1))
            if val is not None and val > 0:
                out["health"] = val


    if out.get("health", 0) <= 0 and out.get("armor", 0) > 0:
        if out["armor"] >= 50:
            out["health"] = out["armor"]
            out["armor"] = 0.0

    return out

def extract_animations_from_java(java_code: str, namespace: Optional[str] = None, entity_name: Optional[str] = None):
    animations = set()
    MOTION_KEYWORDS = {
        "idle", "stand", "standing", "pose", "float", "floating", "ambient",
        "breathe", "blink", "twitch", "fidget",
        "walk", "walking", "wander", "wander",
        "run", "running", "chase", "sprint", "sprinting", "dash", "gallop",
        "swim", "swimming", "paddle", "crawl", "slither", "jump", "jumping", "leap",
        "fly", "flying", "hover", "hovering", "glide", "gliding", "soar",
        "climb", "climbing", "roll",
        "attack", "attacking", "strike", "striking", "bite", "biting",
        "swipe", "swiping", "slam", "slamming", "lunge", "lunging",
        "claw", "clawing", "charge", "charging", "thrust", "shoot", "shooting",
        "breath", "roar",
        "hurt", "hit", "flinch", "pain", "stagger", "reel",
        "death", "die", "dying", "dead", "collapse", "fall",
        "sit", "sitting", "crouch", "crouching", "lay", "laying", "lie",
        "sleep", "sleeping", "rest", "resting", "curl",
        "spawn", "appear", "emerge", "summon", "summon",
        "open", "close", "dig", "eat", "drink",
        "tail", "wing", "wings", "ear", "head", "jaw", "mouth",
        "flap", "wag", "sway", "spin", "shake",
    }
    def _looks_like_anim_id(s: str) -> bool:
        if not s:
            return False
        if s.startswith("animation."):
            tail = s[len("animation."):]
            last_seg = tail.split(".")[-1].lower()
            return any(kw in last_seg for kw in MOTION_KEYWORDS)
        if "animations/" in s.lower():
            stem = re.sub(r'\.json$', '', s.split("/")[-1], flags=re.I).lower()
            return any(kw in stem for kw in MOTION_KEYWORDS)
        return False
    def _add(raw: str, trusted: bool = False):
        s = str(raw).strip().strip('"').strip("'")
        if not s or len(s) < 3:
            return
        if not trusted and not _looks_like_anim_id(s):
            return
        anim_id = canonicalize_animation_id(s, namespace, entity_name)
        if anim_id:
            animations.add(anim_id)
    ast = JavaAST(java_code)
    ast._parse()
    if ast._tree is not None:
        for inv in ast.invocations_of('addAnimation') + ast.invocations_of('then'):
            s = JavaAST.first_string_arg(inv)
            if s:
                _add(s, trusted=True)
        for method in ('thenPlay', 'thenLoop', 'thenPlayAndHold', 'playAnim', 'playAnimation', 'setAnimation'):
            for inv in ast.invocations_of(method):
                s = JavaAST.first_string_arg(inv)
                if s:
                    _add(s, trusted=True)
        for lit in ast.all_string_literals():
            _add(lit, trusted=False)
        for _, node in ast._tree.filter(javalang.tree.FieldDeclaration):
            for decl in node.declarators:
                if re.match(r'(?:ANIMATION|ANIM)[_A-Z0-9]*', decl.name, re.I):
                    if decl.initializer and isinstance(decl.initializer, javalang.tree.Literal):
                        val = decl.initializer.value.strip('"').strip("'")
                        _add(val, trusted=False)
    else:
        for m in re.finditer(r'addAnimation\(\s*["\']+([^"\']+)["\']+', java_code):
            _add(m.group(1), trusted=True)
        for m in re.finditer(r'animation\.([A-Za-z0-9_]+(?:\.[A-Za-z0-9_]+)+)', java_code):
            _add("animation." + m.group(1), trusted=False)
        for m in re.finditer(r'\.then\s*\(\s*["\']+([^"\']+)["\']+', java_code):
            _add(m.group(1), trusted=True)
        for m in re.finditer(r'animations/([^"\'\.]+)\.json', java_code, re.I):
            _add(m.group(1), trusted=False)
        for m in re.finditer(r'(?:ANIMATION|ANIM)[_A-Z0-9]*\s*=\s*["\']+([^"\']+)["\']+', java_code):
            _add(m.group(1), trusted=False)
        for m in re.finditer(r'thenPlay\s*\(\s*["\'"]([^"\']+)["\'"]', java_code):
            _add(m.group(1), trusted=True)
        for m in re.finditer(r'thenLoop\s*\(\s*["\'"]([^"\']+)["\'"]', java_code):
            _add(m.group(1), trusted=True)
        for m in re.finditer(r'setAnimation\s*\(\s*RawAnimation\.begin\s*\(\s*\)\s*\.then(?:Play|Loop)\s*\(\s*["\'"]([^"\']+)["\'"]', java_code, re.DOTALL):
            _add(m.group(1), trusted=True)
        for m in re.finditer(r'playAnim(?:ation)?\s*\(\s*["\'"]([^"\']+)["\'"]', java_code):
            _add(m.group(1), trusted=True)
    return animations
VANILLA_GOALS: Set[str] = {
    "FloatGoal", "SwimGoal", "BreatheAirGoal",
    "NearestAttackableTargetGoal", "NearestAttackableTargetExpiringGoal",
    "ToggleableNearestAttackableTargetGoal", "NonTamedTargetGoal",
    "DefendVillageTargetGoal", "HurtByTargetGoal",
    "OwnerHurtByTargetGoal", "OwnerHurtTargetGoal", "ResetAngerGoal",
    "MeleeAttackGoal", "OcelotAttackGoal", "CreeperSwellGoal",
    "RangedAttackGoal", "RangedBowAttackGoal", "RangedCrossbowAttackGoal",
    "LeapAtTargetGoal", "MoveTowardsTargetGoal",
    "AvoidEntityGoal", "PanicGoal", "RunAroundLikeCrazyGoal",
    "FleeSunGoal", "RestrictSunGoal",
    "OpenDoorGoal", "InteractDoorGoal", "BreakDoorGoal",
    "BreakBlockGoal", "UseItemGoal",
    "FollowOwnerGoal", "FollowParentGoal", "FollowMobGoal",
    "FollowBoatGoal", "FollowSchoolLeaderGoal", "LlamaFollowCaravanGoal",
    "LandOnOwnersShoulderGoal", "MoveToBlockGoal",
    "MoveTowardsRestrictionGoal", "MoveThroughVillageGoal",
    "MoveThroughVillageAtNightGoal", "MoveTowardsRaidGoal",
    "ReturnToVillageGoal", "PatrolVillageGoal", "FindWaterGoal",
    "SitWhenOrderedToGoal", "SitGoal",
    "BreedGoal", "TemptGoal", "EatGrassGoal", "BegGoal",
    "TradeWithPlayerGoal", "LookAtCustomerGoal", "ShowVillagerFlowerGoal",
    "TriggerSkeletonTrapGoal", "DolphinJumpGoal", "JumpGoal",
    "CatLieOnBedGoal", "CatSitOnBlockGoal",
    "WaterAvoidingRandomStrollGoal", "RandomWalkingGoal",
    "RandomSwimmingGoal", "RandomStrollGoal",
    "LookAtGoal", "LookAtPlayerGoal", "LookAtWithoutMovingGoal",
    "LookRandomlyGoal", "RandomLookAroundGoal",
}
GOAL_NAME_ALIASES: Dict[str, str] = {
    "PathfinderGoalFloat":                    "FloatGoal",
    "PathfinderGoalSwimming":                 "SwimGoal",
    "PathfinderGoalMeleeAttack":              "MeleeAttackGoal",
    "PathfinderGoalBowShoot":                 "RangedBowAttackGoal",
    "PathfinderGoalArrowAttack":              "RangedAttackGoal",
    "PathfinderGoalCrossbowAttack":           "RangedCrossbowAttackGoal",
    "PathfinderGoalLeapAtTarget":             "LeapAtTargetGoal",
    "PathfinderGoalMoveTowardsTarget":        "MoveTowardsTargetGoal",
    "PathfinderGoalAvoidEntity":              "AvoidEntityGoal",
    "PathfinderGoalPanic":                    "PanicGoal",
    "PathfinderGoalOpenDoor":                 "OpenDoorGoal",
    "PathfinderGoalBreakDoor":                "BreakDoorGoal",
    "PathfinderGoalFollowOwner":              "FollowOwnerGoal",
    "PathfinderGoalFollowParent":             "FollowParentGoal",
    "PathfinderGoalFollowMob":                "FollowMobGoal",
    "PathfinderGoalMoveToBlock":              "MoveToBlockGoal",
    "PathfinderGoalRestrictSun":              "RestrictSunGoal",
    "PathfinderGoalFleeSun":                  "FleeSunGoal",
    "PathfinderGoalWaterJumping":             "DolphinJumpGoal",
    "PathfinderGoalBreed":                    "BreedGoal",
    "PathfinderGoalTempt":                    "TemptGoal",
    "PathfinderGoalEatTile":                  "EatGrassGoal",
    "PathfinderGoalBeg":                      "BegGoal",
    "PathfinderGoalTradeWithPlayer":          "TradeWithPlayerGoal",
    "PathfinderGoalLookAtPlayer":             "LookAtPlayerGoal",
    "PathfinderGoalLookAtTradingPlayer":      "LookAtCustomerGoal",
    "PathfinderGoalRandomLookaround":         "RandomLookAroundGoal",
    "PathfinderGoalRandomStroll":             "RandomStrollGoal",
    "PathfinderGoalRandomSwim":               "RandomSwimmingGoal",
    "PathfinderGoalWaterAvoidingRandomStroll":"WaterAvoidingRandomStrollGoal",
    "PathfinderGoalSit":                      "SitGoal",
    "PathfinderGoalHurtByTarget":             "HurtByTargetGoal",
    "PathfinderGoalNearestAttackableTarget":  "NearestAttackableTargetGoal",
    "PathfinderGoalDefendVillage":            "DefendVillageTargetGoal",
    "PathfinderGoalOwnerHurtByTarget":        "OwnerHurtByTargetGoal",
    "PathfinderGoalOwnerHurtTarget":          "OwnerHurtTargetGoal",
    "EntityAIFloat":           "FloatGoal",
    "EntityAISwimming":        "SwimGoal",
    "EntityAIAttackMelee":     "MeleeAttackGoal",
    "EntityAIAttackRanged":    "RangedAttackGoal",
    "EntityAIAttackRangedBow": "RangedBowAttackGoal",
    "EntityAILeapAtTarget":    "LeapAtTargetGoal",
    "EntityAIAvoidEntity":     "AvoidEntityGoal",
    "EntityAIPanic":           "PanicGoal",
    "EntityAIOpenDoor":        "OpenDoorGoal",
    "EntityAIFollowOwner":     "FollowOwnerGoal",
    "EntityAIFollowParent":    "FollowParentGoal",
    "EntityAIFollowMob":       "FollowMobGoal",
    "EntityAIBreed":           "BreedGoal",
    "EntityAITempt":           "TemptGoal",
    "EntityAIEatGrass":        "EatGrassGoal",
    "EntityAIWatchClosest":    "LookAtPlayerGoal",
    "EntityAILookIdle":        "RandomLookAroundGoal",
    "EntityAIWander":          "RandomStrollGoal",
    "EntityAIHurtByTarget":    "HurtByTargetGoal",
    "EntityAINearestAttackableTarget": "NearestAttackableTargetGoal",
    "EntityAISit":             "SitGoal",
}
_GOAL_PARENT_MAP: Dict[str, str] = {}
_GOAL_MAP_BUILT: bool = False
_ENTITY_SOURCE_MAP: Dict[str, str] = {}
def _strip_generics(name: str) -> str:
    return JavaAST.strip_generics(name)
def build_goal_inheritance_map(java_files: Dict[str, str]) -> None:
    global _GOAL_PARENT_MAP, _GOAL_MAP_BUILT, _ENTITY_SOURCE_MAP
    raw: Dict[str, str] = {}
    entity_src: Dict[str, str] = {}
    for _path, code in java_files.items():
        ast = JavaAST(code)
        ast._parse()
        if ast._tree is not None:
            for cls_decl in ast.get_class_declarations():
                entity_src[cls_decl.name] = code
            for child, parent in ast.all_class_extends():
                child  = JavaAST.strip_generics(child)
                parent = JavaAST.strip_generics(parent)
                if (child.endswith("Goal") or parent.endswith("Goal")
                        or parent in VANILLA_GOALS or child in VANILLA_GOALS
                        or parent in GOAL_NAME_ALIASES or child in GOAL_NAME_ALIASES):
                    raw[child] = GOAL_NAME_ALIASES.get(parent, parent)
        else:
            m_cls = re.search(r'\bclass\s+([A-Za-z0-9_]+)', code)
            if m_cls:
                entity_src[m_cls.group(1)] = code
            for m in re.finditer(
                r'\bclass\s+([A-Za-z0-9_]+)\s*(?:<[^>]*>)?\s+extends\s+([A-Za-z0-9_]+)\s*(?:<[^>]*>)?',
                code
            ):
                child  = _strip_generics(m.group(1))
                parent = _strip_generics(m.group(2))
                if (child.endswith("Goal") or parent.endswith("Goal")
                        or parent in VANILLA_GOALS or child in VANILLA_GOALS
                        or parent in GOAL_NAME_ALIASES or child in GOAL_NAME_ALIASES):
                    raw[child] = GOAL_NAME_ALIASES.get(parent, parent)
    _GOAL_PARENT_MAP = raw
    _ENTITY_SOURCE_MAP = entity_src
    _GOAL_MAP_BUILT = True
    custom_count = sum(1 for c in raw if c not in VANILLA_GOALS)

def resolve_custom_goal(custom_class: str, visited: Optional[Set[str]] = None) -> Optional[str]:
    if visited is None:
        visited = set()
    if custom_class in visited:
        return None
    visited.add(custom_class)
    if custom_class in GOAL_NAME_ALIASES:
        resolved = GOAL_NAME_ALIASES[custom_class]

        return resolved
    if custom_class in VANILLA_GOALS:
        return custom_class
    parent = _GOAL_PARENT_MAP.get(custom_class)
    if not parent:
        return None
    if parent in VANILLA_GOALS:

        return parent

    return resolve_custom_goal(parent, visited)
def _collect_super_goals(entity_class: str,
                         java_files: Dict[str, str],
                         visited: Optional[Set[str]] = None) -> List[str]:
    if visited is None:
        visited = set()
    if entity_class in visited:
        return []
    visited.add(entity_class)
    BASE_ENTITY_CLASSES = {
        "Mob", "PathfinderMob", "Animal", "Monster", "AmbientCreature",
        "WaterAnimal", "AbstractFish", "Creature", "AbstractVillager",
        "TamableAnimal", "AbstractGolem", "AbstractSkeleton",
        "AbstractZombie", "Slime", "Ghast", "FlyingMob",
    }
    src = _ENTITY_SOURCE_MAP.get(entity_class)
    if not src:
        for _path, code in java_files.items():
            ast_check = JavaAST(code)
            ast_check._parse()
            if ast_check._tree is not None:
                if any(d.name == entity_class for d in ast_check.get_class_declarations()):
                    src = code
                    break
            elif re.search(rf'\bclass\s+{re.escape(entity_class)}\b', code):
                src = code
                break
    if not src:
        return []
    parent_entity = None
    ast_src = JavaAST(src)
    ast_src._parse()
    if ast_src._tree is not None:
        parent_entity = ast_src.superclass_name(entity_class)
        if parent_entity:
            parent_entity = JavaAST.strip_generics(parent_entity)
    else:
        parent_m = re.search(
            r'\bclass\s+' + re.escape(entity_class) + r'\s*(?:<[^>]*>)?\s+extends\s+([A-Za-z0-9_]+)',
            src
        )
        if parent_m:
            parent_entity = parent_m.group(1)
    if not parent_entity or parent_entity in BASE_ENTITY_CLASSES:
        return []
    parent_src = _ENTITY_SOURCE_MAP.get(parent_entity)
    if not parent_src:
        for _path, code in java_files.items():
            ast_check = JavaAST(code)
            ast_check._parse()
            if ast_check._tree is not None:
                if any(d.name == parent_entity for d in ast_check.get_class_declarations()):
                    parent_src = code
                    break
            elif re.search(rf'\bclass\s+{re.escape(parent_entity)}\b', code):
                parent_src = code
                break
    if not parent_src:
        return []
    inherited: List[str] = []
    inherited.extend(extract_ai_goals_from_java(parent_src))
    parent_ast = JavaAST(parent_src)
    parent_ast._parse()
    calls_super = False
    if parent_ast._tree is not None:
        for _, inv in parent_ast._tree.filter(javalang.tree.MethodInvocation):
            if inv.member == 'registerGoals' and getattr(inv, 'qualifier', '') == 'super':
                calls_super = True
                break
    else:
        calls_super = bool(re.search(r'\bsuper\s*\.\s*registerGoals\s*\(\s*\)', parent_src))
    if calls_super:
        inherited.extend(_collect_super_goals(parent_entity, java_files, visited))
    return inherited
def extract_ai_goals_from_java(java_code: str,
                                extra_java_files: Optional[Dict[str, str]] = None):
    if not _GOAL_MAP_BUILT:
        build_goal_inheritance_map(extra_java_files or {"<inline>": java_code})
    java_files_ref = extra_java_files or {}
    ai_goals: List[str] = []
    def _add(goal: str):
        if goal and goal not in ai_goals:
            ai_goals.append(goal)
    ast = JavaAST(java_code)
    ast._parse()
    if ast._tree is not None:
        all_new_types = [JavaAST.strip_generics(t) for t in ast.all_object_creation_types()]
        for ctype in all_new_types:
            if ctype in VANILLA_GOALS:
                _add(ctype)
            elif ctype in GOAL_NAME_ALIASES:
                _add(GOAL_NAME_ALIASES[ctype])
        for inv in ast.invocations_of('addGoal'):
            args = getattr(inv, 'arguments', []) or []
            if len(args) >= 2:
                goal_arg = args[1]
                if isinstance(goal_arg, javalang.tree.ClassCreator):
                    cls_name = JavaAST.strip_generics(goal_arg.type.name)
                    if cls_name in VANILLA_GOALS:
                        _add(cls_name)
                    elif cls_name in GOAL_NAME_ALIASES:
                        _add(GOAL_NAME_ALIASES[cls_name])
        custom_instantiated: Set[str] = set()
        for ctype in all_new_types:
            if ctype not in VANILLA_GOALS and ctype not in GOAL_NAME_ALIASES and ctype.endswith('Goal'):
                custom_instantiated.add(ctype)
        for custom_cls in sorted(custom_instantiated):
            for child, parent in ast.all_class_extends():
                if child == custom_cls:
                    local_parent = GOAL_NAME_ALIASES.get(parent, parent)
                    if custom_cls not in _GOAL_PARENT_MAP:
                        _GOAL_PARENT_MAP[custom_cls] = local_parent
            resolved = resolve_custom_goal(custom_cls)
            if resolved:
                if resolved not in ai_goals:
                    pass

                _add(resolved)
            else:
                pass

        calls_super_register = any(
            inv.member == 'registerGoals'
            for _, inv in ast._tree.filter(javalang.tree.MethodInvocation)
            if getattr(inv, 'qualifier', '') in ('', 'super')
        ) if ast._tree else False
        for _, inv in ast._tree.filter(javalang.tree.MethodInvocation):
            if inv.member == 'registerGoals' and getattr(inv, 'qualifier', '') == 'super':
                calls_super_register = True
                break
        if calls_super_register:
            entity_cls = ast.primary_class_name()
            if entity_cls:
                inherited = _collect_super_goals(entity_cls, java_files_ref)
                for g in inherited:
                    _add(g)
                if inherited:
                    pass

    else:
        for goal_name in VANILLA_GOALS:
            if re.search(rf'\bnew\s+{re.escape(goal_name)}\s*[(<]', java_code):
                _add(goal_name)
        for alias, canonical in GOAL_NAME_ALIASES.items():
            if re.search(rf'\bnew\s+{re.escape(alias)}\s*[(<]', java_code):
                _add(canonical)
        for m in re.finditer(
            r'(?:goalSelector|targetSelector)?\s*\.?\s*addGoal\s*\(\s*\d+\s*,\s*new\s+([A-Za-z0-9_]+)\s*[(<]',
            java_code, re.DOTALL
        ):
            cls_name = _strip_generics(m.group(1))
            if cls_name in VANILLA_GOALS:
                _add(cls_name)
            elif cls_name in GOAL_NAME_ALIASES:
                _add(GOAL_NAME_ALIASES[cls_name])
        custom_instantiated = set()
        for m in re.finditer(r'\bnew\s+([A-Za-z0-9_]+Goal)\s*[(<]', java_code):
            cls_name = _strip_generics(m.group(1))
            if cls_name not in VANILLA_GOALS and cls_name not in GOAL_NAME_ALIASES:
                custom_instantiated.add(cls_name)
        for custom_cls in sorted(custom_instantiated):
            local_m = re.search(
                rf'\bclass\s+{re.escape(custom_cls)}\s*(?:<[^>]*>)?\s+extends\s+([A-Za-z0-9_]+)\s*(?:<[^>]*>)?',
                java_code
            )
            if local_m:
                local_parent = GOAL_NAME_ALIASES.get(_strip_generics(local_m.group(1)), _strip_generics(local_m.group(1)))
                if custom_cls not in _GOAL_PARENT_MAP:
                    _GOAL_PARENT_MAP[custom_cls] = local_parent
            resolved = resolve_custom_goal(custom_cls)
            if resolved:
                if resolved not in ai_goals:
                    pass

                _add(resolved)
            else:
                pass

        if re.search(r'\bsuper\s*\.\s*registerGoals\s*\(\s*\)', java_code):
            cls_m = re.search(r'\bclass\s+([A-Za-z0-9_]+)', java_code)
            if cls_m:
                entity_cls = cls_m.group(1)
                inherited = _collect_super_goals(entity_cls, java_files_ref)
                for g in inherited:
                    _add(g)
                if inherited:
                    pass

    LEGACY_EXTEND_MAP = {
        "MeleeAttackGoal", "RangedAttackGoal",
        "NearestAttackableTargetGoal", "HurtByTargetGoal",
        "AvoidEntityGoal", "PanicGoal", "FollowOwnerGoal",
    }
    ast2 = JavaAST(java_code)
    ast2._parse()
    for base in LEGACY_EXTEND_MAP:
        if ast2._tree is not None:
            for child, parent in ast2.all_class_extends():
                if parent == base:
                    if base not in ai_goals and child in [JavaAST.strip_generics(t) for t in ast2.all_object_creation_types()]:
                        _add(base)
        else:
            custom = re.search(rf'\bclass\s+(\w+)\s*(?:<[^>]*>)?\s+extends\s+{re.escape(base)}', java_code)
            if custom:
                if base not in ai_goals and re.search(rf'\bnew\s+{re.escape(custom.group(1))}\s*[(<]', java_code):
                    _add(base)
    return ai_goals
def extract_damage_immunities_from_java(java_code: str):
    immunities = set()
    projectile_types = {
        "AbstractArrow", "Arrow", "SpectralArrow", "Trident",
        "ShulkerBullet", "FireworkRocketEntity", "ThrownPotion",
        "ThrownSplashPotion", "WindCharge", "SmallFireball", "LargeFireball",
    }
    for cls in projectile_types:
        if re.search(rf'\binstanceof\s+{re.escape(cls)}\b', java_code):
            immunities.add("projectile")
            break
    if re.search(r'\binstanceof\s+(?:Player|ServerPlayer|EntityPlayer)\b', java_code):
        immunities.add("player")
    fire_patterns = [
        r'\bfireImmune\s*\(\)',
        r'fireImmune\s*=\s*true',
        r'isFireImmune\s*\(\s*\)\s*\{[^}]*return\s+true',
        r'DamageSource\.(?:ON_FIRE|IN_FIRE|LIGHTNING|HOT_FLOOR|LAVA|CAMPFIRE)',
        r'DamageTypes\.(?:ON_FIRE|IN_FIRE|LAVA|HOT_FLOOR)',
        r'"fire"\s*,',
        r'DamageSource\.f_19315_',
        r'isOnFire\s*\(\s*\)',
    ]
    for pat in fire_patterns:
        if re.search(pat, java_code, re.IGNORECASE):
            immunities.add("fire")
            break
    drown_patterns = [
        r'canBreatheUnderwater\s*\(\s*\)\s*\{[^}]*return\s+true',
        r'DamageSource\.(?:DROWN|DROWN_ING)',
        r'DamageTypes\.DROWN',
        r'"drown"',
        r'DamageSource\.f_19314_',
    ]
    for pat in drown_patterns:
        if re.search(pat, java_code, re.IGNORECASE):
            immunities.add("drown")
            break
    fall_patterns = [
        r'causeFallDamage\s*\([^)]*\)\s*\{[^}]*return\s+false',
        r'DamageSource\.(?:FALL|STALAGMITE)',
        r'DamageTypes\.FALL',
        r'"fall"',
        r'DamageSource\.f_19312_',
    ]
    for pat in fall_patterns:
        if re.search(pat, java_code, re.IGNORECASE):
            immunities.add("fall")
            break
    if re.search(r'DamageSource\.(?:EXPLOSION|GENERIC_KILL|CRAMMING)|DamageTypes\.EXPLOSION|"explosion"', java_code, re.IGNORECASE):
        immunities.add("explosion")
    magic_patterns = [
        r'DamageSource\.(?:MAGIC|WITHER|DRAGON_BREATH)',
        r'DamageTypes\.(?:MAGIC|WITHER|DRAGON_BREATH)',
        r'isMagic\s*\(\s*\)',
        r'"magic"',
        r'm_19372_\(\)',
    ]
    for pat in magic_patterns:
        if re.search(pat, java_code, re.IGNORECASE):
            immunities.add("magic")
            break
    if re.search(r'(?:witherSkull|WitherBoss|WITHER_SKULL)', java_code, re.IGNORECASE):
        immunities.add("wither")
    if re.search(
        r'isInvulnerableTo\s*\([^)]*\)\s*\{[^}]*return\s+true',
        java_code, re.DOTALL
    ):
        immunities.add("all")
    return sorted(immunities)
def detect_dynamic_bounding_procedure(java_code: str) -> Optional[str]:
    m = re.search(r'([A-Za-z0-9_]+)BoundingBoxScaleProcedure', java_code)
    if m:
        return m.group(0)
    m2 = re.search(r'([A-Za-z0-9_]+Procedure)\.execute', java_code)
    if m2:
        return m2.group(1)
    return None
def detect_despawn_ticks(java_code: str) -> Optional[int]:
    m = re.search(r'==\s*([0-9]{1,5})\)\s*{[^}]*remove\(', java_code)
    if m:
        try:
            return int(m.group(1))
        except Exception:
            return None
    m2 = re.search(r'(?:tickCount|age|lifeTicks?)\s*[>]=?\s*([0-9]{1,5})[^;{]*(?:discard|remove|kill)\s*\(', java_code)
    if m2:
        try:
            val = int(m2.group(1))
            if 1 <= val <= 24000:
                return val
        except Exception:
            pass
    return None
def write_render_controller(entity_basename: str, namespace: str, geometry_identifier: str, uv_anim: Optional[Dict] = None) -> str:
    entity_basename_clean = sanitize_identifier(entity_basename)
    namespace_clean = sanitize_identifier(namespace)
    if not geometry_identifier:
        geometry_identifier = f"geometry.{namespace_clean}.{entity_basename_clean}"
        _warn(f"[RP] Missing geometry identifier, using fallback: {geometry_identifier}")
    if geometry_identifier.startswith("geometry."):
        geom_tail = geometry_identifier.split(".", 1)[1]
        geom_ident = "geometry." + sanitize_identifier(geom_tail)
    else:
        geom_ident = "geometry." + sanitize_identifier(geometry_identifier)
    controller_id = f"controller.render.{namespace_clean}.{entity_basename_clean}"
    controller = {
        "format_version": RP_LEGACY_RENDER_FORMAT,
        "render_controllers": {
            controller_id: {
                "geometry": geom_ident,
                "textures": ["texture.default"],
                "materials": [{"*": "Material.default"}],
                "uv_anim": uv_anim or {}
            }
        }
    }
    out_path = os.path.join(RP_FOLDER, "render_controllers", f"{entity_basename_clean}.render_controllers.json")
    _REAL_PRINT(f"[DEBUG] write_render_controller -> {out_path}")
    _safe_rp_write("render controller", out_path, controller)
    return controller_id
def write_rp_entity_json(entity_basename: str, namespace: str, texture_ref: str, geometry_identifier: str, animation_key: Optional[str], controller_id: str):
    entity_basename_clean = sanitize_identifier(entity_basename)
    namespace_clean = sanitize_identifier(namespace)
    if not texture_ref:
        texture_ref = f"{namespace_clean}:entity/{entity_basename_clean}"
        _warn(f"[RP] Missing texture reference for {entity_basename_clean}, using fallback: {texture_ref}")
    texture_path = texture_ref_to_rp_path(texture_ref, default_kind="entity")
    if not texture_path.startswith("textures/"):
        texture_path_with_prefix = f"textures/{texture_path}"
    else:
        texture_path_with_prefix = texture_path
    if not geometry_identifier:
        geometry_identifier = f"geometry.{namespace_clean}.{entity_basename_clean}"
        _warn(f"[RP] Missing geometry identifier for {entity_basename_clean}, using fallback: {geometry_identifier}")
    if geometry_identifier.startswith("geometry."):
        geom_tail = geometry_identifier.split(".", 1)[1]
        geom_ident = "geometry." + sanitize_identifier(geom_tail)
    else:
        geom_ident = "geometry." + sanitize_identifier(geometry_identifier)
    description = {
        "identifier": f"{namespace_clean}:{entity_basename_clean}",
        "textures": {"default": texture_path_with_prefix},
        "geometry": {"default": geom_ident},
        "render_controllers": [controller_id],
        "materials": {"default": "entity_alphatest"}
    }
    pending_sounds = _ENTITY_SOUND_EVENTS.get(f"{namespace_clean}:{entity_basename_clean}")
    if pending_sounds:
        _ANIM_SLOTS = {"attack"}
        anim_fx = {k: v for k, v in pending_sounds.get("events", {}).items() if k in _ANIM_SLOTS}
        if anim_fx:
            description["sound_effects"] = anim_fx
    client_entity = {
        "format_version": RP_ENTITY_FORMAT_VERSION,
        "minecraft:client_entity": {"description": description}
    }
    out_path = os.path.join(RP_FOLDER, "entity", f"{entity_basename_clean}.entity.json")
    _REAL_PRINT(f"[DEBUG] write_rp_entity_json -> {out_path}")
    _safe_rp_write("RP entity", out_path, client_entity)

def extract_block_properties_from_java(java_code: str):
    props = {
        "destroy_time": None,
        "explosion_resistance": None,
        "material": None,
        "texture_hint": None,
        "loot_table": None,
        "light_emission": 0,
        "friction": 0.6,
        "is_solid": True,
        "is_opaque": True,
    }
    _float_pat = r'[-+]?[0-9]*\.?[0-9]+[FfDd]?'
    m = re.search(rf'\.strength\s*\(\s*({_float_pat})(?:\s*,\s*({_float_pat}))?\s*\)', java_code)
    if m:
        try: props["destroy_time"] = float(re.sub(r'[FfDd]$', '', m.group(1)))
        except Exception: pass
        if m.group(2):
            try: props["explosion_resistance"] = float(re.sub(r'[FfDd]$', '', m.group(2)))
            except Exception: pass
    m_dt = re.search(r'\.destroyTime\s*\(\s*([-+]?[0-9]*\.?[0-9]+[FfDd]?)\s*\)', java_code)
    if m_dt and props["destroy_time"] is None:
        try: props["destroy_time"] = float(re.sub(r'[FfDd]$', '', m_dt.group(1)))
        except Exception: pass
    m_h = re.search(r'\.hardness\s*\(\s*([-+]?[0-9]*\.?[0-9]+[FfDd]?)\s*\)', java_code)
    if m_h and props["destroy_time"] is None:
        try: props["destroy_time"] = float(re.sub(r'[FfDd]$', '', m_h.group(1)))
        except Exception: pass
    m2 = re.search(r'(?:explosionResistance|explosion_resistance|explosionResistant|resistance)\s*\(?\s*([0-9]+(?:\.[0-9]+)?)\s*\)?', java_code)
    if m2 and props["explosion_resistance"] is None:
        try: props["explosion_resistance"] = float(m2.group(1))
        except Exception: pass
    m3 = re.search(r'Material\.([A-Z_]+)', java_code)
    if m3:
        props["material"] = m3.group(1).lower()
    m_ll_lambda = re.search(r'\.lightLevel\s*\(\s*\(?\s*[a-zA-Z_]\w*\s*\)?\s*->\s*([0-9]+)\s*\)', java_code)
    if m_ll_lambda:
        try: props["light_emission"] = min(15, int(m_ll_lambda.group(1)))
        except Exception: pass
    if not props["light_emission"]:
        m_ll = re.search(r'\.lightLevel\s*\(\s*([0-9]+)\s*\)', java_code)
        if m_ll:
            try: props["light_emission"] = min(15, int(m_ll.group(1)))
            except Exception: pass
    if not props["light_emission"]:
        m_le = re.search(r'\.lightEmission\s*\(\s*([0-9]+)\s*\)', java_code)
        if m_le:
            try: props["light_emission"] = min(15, int(m_le.group(1)))
            except Exception: pass
    m4 = re.search(r'(?:slipperiness|friction)\s*\(?\s*([0-9]+(?:\.[0-9]+)?)\s*\)?', java_code)
    if m4:
        try: props["friction"] = float(m4.group(1))
        except Exception: pass
    m_rn = re.search(r'setRegistryName\s*\(\s*["\']([a-z0-9_:-]+)["\']', java_code, re.I)
    if m_rn:
        props["texture_hint"] = m_rn.group(1).split(":")[-1]
    else:
        m_rl = re.search(r'new\s+ResourceLocation\s*\(\s*["\']([a-z0-9_:-]+)["\']', java_code, re.I)
        if m_rl:
            props["texture_hint"] = m_rl.group(1).split(":")[-1]
    m6 = re.search(r'getLootTable\(\)\s*.*?["\']([a-z0-9_:/-]+)["\']', java_code, re.I | re.DOTALL)
    if m6:
        props["loot_table"] = m6.group(1)
    m7 = re.search(r'lootTable\(\s*["\']([a-z0-9_:/-]+)["\']', java_code, re.I)
    if m7:
        props["loot_table"] = m7.group(1)
    if re.search(r'\.noOcclusion\(\)|noCollission\(\)|noOcclusionBlock\(\)', java_code):
        props["is_opaque"] = False
    if re.search(r'\.noCollission\(\)|noCollision\(\)', java_code):
        props["is_solid"] = False
    return props
def _looks_like_item_artifact(java_code: str, block_basename: str) -> bool:
    name = sanitize_identifier(block_basename or "")
    if not name:
        return False
    item_markers = (
        "spawn_egg", "egg", "item", "meat", "food", "logo", "effect",
        "potion", "bucket", "ingot", "nugget", "dust", "gem", "shard",
        "disc", "record", "music", "armor", "tool", "weapon",
        "drops", "drop", "fluid", "fluidbucket", "mask", "icon",
    )
    block_markers = (
        "log", "plank", "planks", "slab", "stairs", "stair", "wall",
        "ore", "stone", "deepslate", "dirt", "sand", "gravel", "glass",
        "wool", "bed", "leaf", "leaves", "mushroom", "root", "crop",
        "fence", "pane", "door", "trapdoor",
    )
    name_is_itemy = any(m in name for m in item_markers)
    name_is_blocky = any(m in name for m in block_markers)
    code = (java_code or "").lower()
    code_itemy = bool(re.search(r'\bextends\s+(?:item|blockitem|spawneggitem|food|fooditem|armoritem|sworditem|pickaxeitem|axeitem|shovelitem|hoeitem|diggeritem|tridentitem)\b', code, re.I)) or any(tok in code for tok in ["minecraft:item", "creativemodetab", "foodproperties", "max_stack_size"])
    code_blocky = bool(re.search(r'\bextends\s+(?:block|rotatedpillarblock|bushblock|sandblock|leavesblock|liquidblock|stairblock|slabblock|fenceblock|glazedterracottablock)\b', code, re.I)) or any(tok in code for tok in ["minecraft:block", "material_instances", "destroy_time", "explosion_resistance"])
    if code_itemy and not code_blocky:
        return True
    if name_is_itemy and not name_is_blocky:
        return True
    if name_is_itemy and ("spawn_egg" in name or "effect" in name or "logo" in name or "meat" in name):
        return True
    return False


def convert_java_block_to_bedrock(java_path: str, namespace: str):
    try:
        with open(java_path, 'r', encoding='utf-8', errors='ignore') as f:
            java_code = f.read()
    except Exception as e:
        _warn(f" Failed to read block java {java_path}: {e}")
        return
    block_basename = os.path.splitext(os.path.basename(java_path))[0]
    if _looks_like_item_artifact(java_code, block_basename):
        _warn(f" Redirecting obvious item-like block artifact to item converter: {java_path}")
        convert_java_item_to_bedrock(java_path, namespace)
        return
    block_id = f"{sanitize_identifier(namespace)}:{sanitize_identifier(block_basename)}"
    props = extract_block_properties_from_java(java_code)
    block_json = {
        "format_version": BP_RP_FORMAT_VERSION,
        "minecraft:block": {
            "description": {
                "identifier": block_id,
                "is_experimental": False,
                "register_to_creative_menu": True
            },
            "components": {}
        }
    }
    comps = block_json["minecraft:block"]["components"]
    comps["minecraft:destroy_time"] = props.get("destroy_time") if props.get("destroy_time") is not None else 1.5
    comps["minecraft:explosion_resistance"] = props.get("explosion_resistance") if props.get("explosion_resistance") is not None else 6.0
    texture_ref = resolve_texture_reference(namespace, props.get("texture_hint"), "blocks", fallback_name=sanitize_identifier(block_basename))
    comps["minecraft:material_instances"] = {"*": {"texture": texture_ref, "render_method": "opaque"}}
    if props.get("loot_table"):
        comps["minecraft:loot"] = {"table": props["loot_table"]}
    else:
        comps["minecraft:loot"] = {"table": f"loot_tables/blocks/{sanitize_identifier(block_basename)}.json"}
    sound_profile = _guess_block_sound_profile(java_code, namespace, block_id)
    if sound_profile:
        BLOCK_SOUND_PROFILES[block_id] = sound_profile
    comps["_converter_metadata"] = {"source_java_file": os.path.basename(java_path), "parsed_props": props, "sound_profile": sound_profile}
    safe_name = sanitize_identifier(block_basename)
    out_path = os.path.join(BP_FOLDER, "blocks", f"{safe_name}.json")
    safe_write_json(out_path, block_json)
    _mirror_bp_block_to_rp(block_json, safe_name)

def extract_item_properties_from_java(java_code: str):
    props = {
        "max_stack_size": None,
        "durability": None,
        "texture_hint": None,
        "creative_tab": None,
        "registry_name": None,
        "is_food": False,
        "nutrition": 0,
        "saturation": 0.0,
        "is_armor": False,
        "armor_slot": None,
        "is_weapon": False,
        "attack_damage": 0,
        "is_tool": False,
    }
    for pat in [
        r'\.stacksTo\s*\(\s*([0-9]+)\s*\)',
        r'maxStackSize\s*\(\s*([0-9]+)\s*\)',
        r'setMaxStackSize\s*\(\s*([0-9]+)\s*\)',
        r'stack(?:Size|_size)\s*[=:]\s*([0-9]+)',
    ]:
        m = re.search(pat, java_code, re.I)
        if m:
            try: props["max_stack_size"] = int(m.group(1)); break
            except Exception: pass
    for pat in [
        r'\.defaultMaxDamage\s*\(\s*([0-9]+)\s*\)',
        r'\.durability\s*\(\s*([0-9]+)\s*\)',
        r'maxDamage\s*\(\s*([0-9]+)\s*\)',
        r'setMaxDamage\s*\(\s*([0-9]+)\s*\)',
        r'(?:DURABILITY|MAX_DAMAGE)\s*[=:]\s*([0-9]+)',
    ]:
        m = re.search(pat, java_code, re.I)
        if m:
            try: props["durability"] = int(m.group(1)); break
            except Exception: pass
    for pat in [
        r'setRegistryName\s*\(\s*["\']([a-z0-9_:-]+)["\']',
        r'new\s+ResourceLocation\s*\(\s*["\']([a-z0-9_:-]+)["\']\s*\)',
        r'ResourceLocation\s*\(\s*["\'][^"\']+["\']\s*,\s*["\']([a-z0-9_/:-]+)["\']',
    ]:
        m = re.search(pat, java_code, re.I)
        if m:
            raw = m.group(1)
            props["registry_name"] = raw
            props["texture_hint"] = raw.split(":")[-1]
            break
    for pat in [
        r'ItemGroup\.([A-Z0-9_]+)',
        r'CreativeModeTab\.([A-Z0-9_]+)',
        r'\.tab\s*\(\s*(?:[A-Za-z0-9_]+\.)+([A-Z0-9_]+)\s*\)',
        r'creativeModeTab\s*\(\s*(?:[A-Za-z0-9_]+\.)+([A-Z0-9_]+)\s*\)',
    ]:
        m = re.search(pat, java_code)
        if m:
            props["creative_tab"] = m.group(1).lower()
            break
    if re.search(r'FoodProperties|\.food\s*\(|nutrition|saturationMod|extends\s+(?:ItemFood|BowlFoodItem)', java_code, re.I):
        props["is_food"] = True
        m3 = re.search(r'nutrition\s*\(?\s*(\d+)', java_code, re.I)
        if m3: props["nutrition"] = int(m3.group(1))
        m4 = re.search(r'saturation(?:Modifier|Mod)?\s*\(?\s*([0-9.]+)', java_code, re.I)
        if m4: props["saturation"] = float(m4.group(1))
    slot_map = {
        r'EquipmentSlot\.HEAD|ArmorItem.*HEAD': "slot.armor.head",
        r'EquipmentSlot\.CHEST|ArmorItem.*CHEST': "slot.armor.chest",
        r'EquipmentSlot\.LEGS|ArmorItem.*LEGS': "slot.armor.legs",
        r'EquipmentSlot\.FEET|ArmorItem.*FEET': "slot.armor.feet",
    }
    for pat, slot in slot_map.items():
        if re.search(pat, java_code, re.I):
            props["is_armor"] = True
            props["armor_slot"] = slot
            break
    if re.search(r'SwordItem|TieredItem|extends.*Sword|ATTACK_DAMAGE_MODIFIER', java_code, re.I):
        props["is_weapon"] = True
        m5 = re.search(r'attackDamage\s*[=+]+\s*([0-9.]+)|ATTACK_DAMAGE\s*[=:]\s*([0-9.]+)', java_code, re.I)
        if m5:
            try: props["attack_damage"] = float(m5.group(1) or m5.group(2))
            except Exception: pass
    if re.search(r'PickaxeItem|ShovelItem|AxeItem|HoeItem|DiggerItem|extends.*Tool', java_code, re.I):
        props["is_tool"] = True
    return props
def convert_java_item_to_bedrock(java_path: str, namespace: str):
    try:
        with open(java_path, 'r', encoding='utf-8', errors='ignore') as f:
            java_code = f.read()
    except Exception as e:
        _warn(f" Failed to read item java {java_path}: {e}")
        return
    item_basename = os.path.splitext(os.path.basename(java_path))[0]
    item_id = f"{sanitize_identifier(namespace)}:{sanitize_identifier(item_basename)}"
    props = extract_item_properties_from_java(java_code)
    bp_item = {
        "format_version": BP_ITEM_FORMAT_VERSION,
        "minecraft:item": {
            "description": {"identifier": item_id, "register_to_creative_menu": True},
            "components": {}
        }
    }
    comps = bp_item["minecraft:item"]["components"]
    comps["minecraft:max_stack_size"] = props.get("max_stack_size") if props.get("max_stack_size") is not None else 64
    if props.get("durability") is not None:
        comps["minecraft:durability"] = {"max_durability": props["durability"]}
    sound_profile = _guess_item_sound_profile(java_code, namespace, item_id)
    if sound_profile:
        ITEM_SOUND_PROFILES[item_id] = sound_profile
    comps["_converter_metadata"] = {"source_java_file": os.path.basename(java_path), "parsed_props": props, "sound_profile": sound_profile}
    out_bp = os.path.join(BP_FOLDER, "items", f"{sanitize_identifier(item_basename)}.json")
    safe_write_json(out_bp, bp_item)

    texture_ref = resolve_texture_reference(namespace, props.get("texture_hint"), "items", fallback_name=sanitize_identifier(item_basename))
    rp_item = {
        "format_version": BP_ITEM_FORMAT_VERSION,
        "minecraft:item": {
            "description": {"identifier": item_id, "category": props.get("creative_tab") or "misc"},
            "components": {"minecraft:icon": texture_ref}
        }
    }
    out_rp = os.path.join(RP_FOLDER, "items", f"{sanitize_identifier(item_basename)}.item.json")
    safe_write_json(out_rp, rp_item)

NON_ENTITY_KEYWORDS = [
    "renderer", "render", "model", "procedure", "tickupdate", "factory",
    "packet", "handler", "provider", "command", "ui", "screen", "container",
    "event", "client", "server", "loader", "registry", "setup",
    "capability", "config", "network", "message", "gui", "recipe",
    "serializer", "codec", "datafixer", "loot", "structure"
]
ENTITY_OVERRIDE_KEYWORDS = ["entity", "mob", "monster", "creature", "animal", "boss", "npc"]
SOUND_ARTIFACT_KEYWORDS = [
    "sound", "sounds", "sfx", "audio", "voice", "whisper", "scream",
    "roar", "howl", "growl", "ambient", "music", "song", "jingle",
    "note", "soundevent", "soundsource", "soundinstance", "playsound",
]

def _is_sound_artifact(java_code: str, filename: str = '', cls_name: Optional[str] = None) -> bool:
    haystack = ' '.join([
        str(cls_name or ''),
        os.path.basename(filename) or '',
        os.path.splitext(os.path.basename(filename))[0] if filename else '',
        java_code[:2000] if java_code else '',
    ]).lower()

    if any(k in haystack for k in ("soundevent", "soundsource", "soundinstance", "playsound", "sounds.json")):
        return True

    if any(k in haystack for k in (
        "assets/", "/sounds/", "/sound/", "sound/", "sounds/", "sfx/", "audio/"
    )):
        return True

    if any(k in haystack for k in SOUND_ARTIFACT_KEYWORDS):

        if any(k in haystack for k in ("register", "registry", "soundevent", "playsound", "soundsource", "soundinstance")):
            return True

    return False

_ENTITY_SUPERCLASSES = {
    'Entity', 'Mob', 'Monster', 'Animal', 'PathfinderMob',
    'TamableAnimal', 'TameableAnimal',
    'CreatureEntity', 'LivingEntity', 'MobEntity',
    'WaterAnimal', 'AmbientCreature', 'FlyingMob',
    'AbstractGolem', 'AbstractVillager', 'AbstractPiglin', 'AbstractSkeleton',
    'Projectile', 'AbstractArrow',
    'AbstractNeutralMob', 'AbstractHurtingProjectile',
    'FireworkRocketEntity', 'ThrowableProjectile', 'ThrowableItemProjectile',
    'AbstractFish', 'AbstractSchoolingFish', 'AbstractChestedHorse',
    'AbstractHorse', 'AbstractIllager', 'AbstractRaider', 'AbstractZombie',
    'SpellcasterIllager', 'PatrollingMonster', 'Slime', 'Ghast',
    'Ageable', 'AgeableMob', 'AbstractCreature',
    'ShoulderRidingEntity', 'OcelotBase',
    'NeoForgeEntity', 'NeoForgeMob', 'ForgeEntity',
    'HostileEntity', 'PassiveEntity', 'AnimalEntity', 'WaterCreatureEntity',
    'FlyingEntity', 'BlazeEntity', 'SlimeEntity', 'GolemEntity',
}
_ENTITY_METHOD_NAMES = {
    'registerGoals', 'defineSynchedData', 'createAttributes',
    'getAddEntityPacket', 'getDefaultAttributes', 'createMobAttributes',
    'createNavigation', 'createBodyControl', 'createMonsterAttributes',
    'createAnimalAttributes', 'createLivingAttributes',
    'initializeClient', 'onAddedToWorld', 'onRemovedFromWorld',
}
def is_likely_entity(java_code: str, filename: str) -> bool:
    fname = os.path.basename(filename).lower()
    cls = extract_class_name(java_code) or ""
    if _should_skip_entity_artifact(java_code, filename, cls):
        return False
    has_override = any(k in fname for k in ENTITY_OVERRIDE_KEYWORDS)
    if not has_override:
        for k in NON_ENTITY_KEYWORDS:
            if k in fname:
                return False
    if cls.lower().endswith("entity"):
        return True
    _SUPERCLASS_SUFFIXES = (
        "Entity", "Mob", "Monster", "Animal", "Creature",
        "Npc", "Boss", "Guardian", "Dragon", "Golem",
    )
    ast = JavaAST(java_code)
    ast._parse()
    if ast._tree is not None:
        for child, parent in ast.all_class_extends():
            parent_clean = JavaAST.strip_generics(parent)
            if parent_clean in _ENTITY_SUPERCLASSES:
                return True
            if any(parent_clean.endswith(sfx) for sfx in _SUPERCLASS_SUFFIXES):
                if ast.method_names() & _ENTITY_METHOD_NAMES:
                    return True
        if ast.method_names() & _ENTITY_METHOD_NAMES:
            return True
        for ctype in ast.all_object_creation_types():
            ctype_clean = JavaAST.strip_generics(ctype)
            if ctype_clean in _ENTITY_SUPERCLASSES:
                return True
            if ctype_clean.endswith("Entity") or ctype_clean.endswith("Mob"):
                if ast.method_names() & _ENTITY_METHOD_NAMES:
                    return True
        if _ENTITY_SOURCE_MAP:
            parent_name: Optional[str] = None
            if ast._tree is not None:
                for _c, _p in ast.all_class_extends():
                    parent_name = JavaAST.strip_generics(_p)
                    break
            else:
                _m = re.search(r'extends\s+([A-Za-z0-9_]+)', java_code)
                parent_name = _m.group(1) if _m else None
            _visited: Set[str] = set()
            while parent_name and parent_name not in _visited and len(_visited) < 8:
                _visited.add(parent_name)
                if parent_name in _ENTITY_SUPERCLASSES:
                    return True
                if parent_name in _ENTITY_SOURCE_MAP:
                    _pcode = _ENTITY_SOURCE_MAP[parent_name]
                    _past = JavaAST(_pcode)
                    _past._parse()
                    if _past._tree is not None:
                        if _past.method_names() & _ENTITY_METHOD_NAMES:
                            return True
                        _next_parent: Optional[str] = None
                        for _c2, _p2 in _past.all_class_extends():
                            _next_parent = JavaAST.strip_generics(_p2)
                            break
                        parent_name = _next_parent
                    else:
                        if any(re.search(p, _pcode) for p in [
                            r'\bregisterGoals\s*\(', r'\bcreateAttributes\s*\(',
                            r'\bcreateNavigation\s*\(', r'\bdefineSynchedData\s*\('
                        ]):
                            return True
                        _m2 = re.search(r'extends\s+([A-Za-z0-9_]+)', _pcode)
                        parent_name = _m2.group(1) if _m2 else None
                else:
                    break
        return False
    exact_names = "|".join(re.escape(n) for n in sorted(_ENTITY_SUPERCLASSES, key=len, reverse=True))
    if re.search(rf'extends\s+(?:[A-Za-z0-9_<>.,\s]*\b(?:{exact_names})\b)', java_code):
        return True
    if re.search(
        r'extends\s+[A-Za-z0-9_]+(?:Entity|Mob|Monster|Animal|Creature|Boss|Golem|Npc|Guardian)\b',
        java_code
    ):
        pass
    entity_methods = [
        r'\bregisterGoals\s*\(',
        r'\bdefineSynchedData\s*\(',
        r'\bcreateAttributes\s*\(',
        r'\bgetAddEntityPacket\s*\(',
        r'\bgetDefaultAttributes\s*\(',
        r'\bcreateMobAttributes\s*\(',
        r'\bcreateMonsterAttributes\s*\(',
        r'\bcreateAnimalAttributes\s*\(',
        r'\bcreateNavigation\s*\(',
        r'\bcreateBodyControl\s*\(',
        r'EntityType\.Builder\.of\b',
        r'\binitializeClient\s*\(',
        r'net\.neoforged\.[a-z.]+Entity',
        r'@EventBusSubscriber\b',
        r'extends\s+GeoEntity\b',
        r'GeoEntityRenderer\b',
        r'extends\s+HostileEntity\b',
        r'extends\s+PassiveEntity\b',
        r'extends\s+AnimalEntity\b',
    ]
    for pat in entity_methods:
        if re.search(pat, java_code):
            return True
    return False
def extract_entity_texture_hint(java_code: str, entity_basename: Optional[str] = None) -> Optional[str]:
    def _first_likely(candidates):
        for c in candidates:
            if c and is_probable_texture(c, entity_basename):
                return c
        return None
    for pat in [
        r'getTextureResource\s*\([^)]*\)[^{]*\{[^}]*new\s+ResourceLocation\s*\(\s*["\']([^"\']+)["\']\s*,\s*["\']([^"\']+)["\']\s*\)',
        r'getTextureLocation\s*\([^)]*\)[^{]*\{[^}]*new\s+ResourceLocation\s*\(\s*["\']([^"\']+)["\']\s*,\s*["\']([^"\']+)["\']\s*\)',
    ]:
        m = re.search(pat, java_code, re.DOTALL)
        if m:
            candidate = f"{m.group(1)}:{m.group(2)}"
            if is_probable_texture(candidate, entity_basename):
                return candidate
    for pat in [
        r'getTextureResource\s*\([^)]*\)[^{]*\{[^}]*new\s+ResourceLocation\s*\(\s*["\']([^"\']+)["\']\s*\)',
        r'getTextureLocation\s*\([^)]*\)[^{]*\{[^}]*new\s+ResourceLocation\s*\(\s*["\']([^"\']+)["\']\s*\)',
    ]:
        m = re.search(pat, java_code, re.DOTALL)
        if m:
            candidate = m.group(1)
            if is_probable_texture(candidate, entity_basename):
                return candidate
    texture_field_patterns = [
        r'(?:TEXTURE|TEXTURE_LOCATION|LAYER_0|TEXTURE_LOC|MODEL_LOCATION|SKIN)\s*=\s*new\s+ResourceLocation\s*\(\s*["\']([^"\']+)["\']\s*,\s*["\']([^"\']+)["\']\s*\)',
        r'(?:TEXTURE|TEXTURE_LOCATION|LAYER_0|TEXTURE_LOC)\s*=\s*new\s+ResourceLocation\s*\(\s*["\']([^"\']+)["\']\s*\)',
        r'(?:TEXTURE|TEXTURE_PATH|TEXTURE_NAME)\s*=\s*["\']([^"\']{4,})["\']',
    ]
    for pat in texture_field_patterns:
        m = re.search(pat, java_code, re.IGNORECASE)
        if m:
            candidate = f"{m.group(1)}:{m.group(2)}" if m.lastindex and m.lastindex >= 2 else m.group(1)
            if is_probable_texture(candidate, entity_basename):
                return candidate
    m = re.search(r'setTexture\s*\(\s*["\']([^"\']+)["\']', java_code)
    if m:
        candidate = m.group(1)
        if is_probable_texture(candidate, entity_basename):
            return candidate
    for m in re.finditer(
        r'new\s+ResourceLocation\s*\(\s*["\']([a-z0-9_:-]+)["\']\s*,\s*["\']([^"\']+)["\']\s*\)',
        java_code, re.IGNORECASE
    ):
        candidate = f"{m.group(1)}:{m.group(2)}"
        if is_probable_texture(candidate, entity_basename):
            return candidate
    for m in re.finditer(
        r'new\s+ResourceLocation\s*\(\s*["\']([a-z0-9_:/-][^"\']*)["\']',
        java_code, re.IGNORECASE
    ):
        candidate = m.group(1)
        if is_probable_texture(candidate, entity_basename):
            return candidate
    m = re.search(r'TEXTURE[^\n\r]*?["\']([A-Za-z0-9_:/\-\.]+)["\']', java_code)
    if m:
        candidate = m.group(1)
        if is_probable_texture(candidate, entity_basename):
            return candidate
    for m in re.finditer(r'["\']([^"\']*(?:textures/|\.png)[^"\']*)["\']', java_code, re.IGNORECASE):
        candidate = m.group(1)
        if is_probable_texture(candidate, entity_basename):
            return candidate
    return None
def is_probable_texture(candidate: Optional[str], entity_basename: Optional[str] = None) -> bool:
    if not candidate:
        return False
    candidate = str(candidate)
    if "textures/" in candidate.lower() or candidate.lower().endswith(".png"):
        return True
    if re.search(r'(blocks|items|entity|textures)[\/:]', candidate, re.I):
        return True
    name = candidate.split(":")[-1].replace(".png", "")
    probes = [f"entity/{name}", f"items/{name}", f"blocks/{name}", f"{name}"]
    for p in probes:
        if rp_texture_exists(p):
            return True
    sound_indicators = ["sound", "sounds", "whisper", "sfx", "ambient", "step", "attack", "wraith", "growl", "roar"]
    if any(k in candidate.lower() for k in sound_indicators) and not "/" in candidate:
        return False
    if ":" in candidate and "/" in candidate:
        return True
    if entity_basename and entity_basename.lower() in candidate.lower():
        return True
    return False
def _find_related_code(cls_name: str) -> Optional[str]:
    target = cls_name.lower()
    for path, code in _ALL_JAVA_FILES.items():
        fname_stem = os.path.splitext(os.path.basename(path))[0].lower()
        if fname_stem == target:
            return code
        declared = extract_class_name(code)
        if declared and declared.lower() == target:
            return code
    return None
def _referenced_class_names(code: str) -> List[str]:
    found: List[str] = []
    for m in re.finditer(
        r'EntityRenderers\.register\s*\([^,)]+,\s*([A-Z][A-Za-z0-9_]+)\s*::',
        code
    ):
        found.append(m.group(1))
    for m in re.finditer(
        r'(?:bindEntityRenderer|registerEntityRenderingHandler)\s*\([^,)]+,\s*([A-Z][A-Za-z0-9_]+)',
        code
    ):
        found.append(m.group(1))
    for m in re.finditer(r'(?:setModel|this\.model)\s*\(?.*?new\s+([A-Z][A-Za-z0-9_]+)', code, re.DOTALL):
        found.append(m.group(1))
    for m in re.finditer(
        r'extends\s+\w+Renderer\s*<[^,>]+,\s*([A-Z][A-Za-z0-9_]+)',
        code
    ):
        found.append(m.group(1))
    for m in re.finditer(
        r'import\s+[\w.]+\.((?:[A-Z][A-Za-z0-9_]*)?(?:Renderer|Model|Layer))\s*;',
        code
    ):
        found.append(m.group(1))
    for m in re.finditer(
        r'extends\s+Geo\w+Renderer\s*<([A-Z][A-Za-z0-9_]+)>',
        code
    ):
        found.append(m.group(1) + "Model")
    return list(dict.fromkeys(found))
def _resolve_tex_hint_to_ref(hint: Optional[str], namespace: str, entity_basename: str) -> Optional[str]:
    if not hint:
        return None
    ns = sanitize_identifier(namespace) or "converted"
    candidate = hint.split(":")[-1].replace(".png", "").strip("/")
    if candidate.startswith("textures/"):
        candidate = candidate[len("textures/"):]
    for probe in [
        candidate,
        f"entity/{candidate}",
        f"entity/{os.path.basename(candidate)}",
        f"entity/{entity_basename}",
    ]:
        probe = probe.replace("\\", "/")
        if rp_texture_exists(probe):
            return f"{ns}:{probe}"
    return None
def find_entity_assets_aggressively(
    java_code: str,
    entity_basename: str,
    namespace: str,
    entity_cls: Optional[str] = None,
) -> Tuple[Optional[str], Optional[str]]:
    ns       = sanitize_identifier(namespace) or "converted"
    ent_toks = _camel_tokens(entity_basename)
    if entity_cls:
        ent_toks = ent_toks | _camel_tokens(entity_cls)
    ent_toks -= _ASSET_NOISE
    if not ent_toks:
        ent_toks = _camel_tokens(entity_basename)
    def _tex_ref_from_hint(hint: Optional[str]) -> Optional[str]:
        if not hint:
            return None
        candidate = hint.split(":")[-1].replace(".png", "").strip("/")
        if candidate.startswith("textures/"):
            candidate = candidate[len("textures/"):]
        for probe in [candidate, f"entity/{candidate}", f"entity/{os.path.basename(candidate)}"]:
            if rp_texture_exists(probe):
                return f"{ns}:{probe}"
        if "/" in candidate or "." in candidate:
            return f"{ns}:{candidate}"
        return None
    def _geom_from_code(code: str) -> Optional[str]:
        result = find_model_geometry_in_code(code)
        if not result:
            return None
        ns_hint, geom_name = result
        return f"geometry.{sanitize_identifier(ns_hint or namespace)}.{sanitize_identifier(geom_name)}"
    def _best_texture_on_disk() -> Optional[str]:
        best_score, best_ref = 0.0, None
        for rel_no_ext, _ in _RP_ASSET_INDEX.get("textures", []):
            score = _asset_score(ent_toks, rel_no_ext)
            if rel_no_ext.startswith("entity/"):
                score += 0.15
            if score > best_score and score >= 0.30:
                best_score = score
                best_ref   = f"{ns}:{rel_no_ext}"
        return best_ref
    def _best_geometry_on_disk() -> Optional[str]:
        best_score, best_ident = 0.0, None
        for ident, _ in _RP_ASSET_INDEX.get("geometry", []):
            ident_lower = ident.lower()
            skip_keywords = [
                "spawn_egg",
                "glass", "stone", "wood", "brick", "ore", "concrete", "sand", "dirt", "grass",
                "item", "tool", "weapon", "armor", "bow", "sword", "pickaxe", "axe", "shovel", "hoe",
                "cube", "box", "plane", "simple", "basic", "block",
                "slab", "stair", "fence", "door", "gate", "lamp", "lamp", "button"
            ]
            if any(kw in ident_lower for kw in skip_keywords):
                continue
            tail  = ident.replace("geometry.", "")
            score = _asset_score(ent_toks, tail)
            if score > best_score and score >= 0.40:
                best_score = score
                best_ident = ident
        return best_ident
    def _try_codes(codes_and_labels):
        tex, geom = None, None
        for label, code in codes_and_labels:
            if not tex:
                raw = extract_entity_texture_hint(code, entity_basename)
                tex = _tex_ref_from_hint(raw)
                if tex:
                    pass

            if not geom:
                geom = _geom_from_code(code)
                if geom:
                    pass

            if tex and geom:
                break
        return tex, geom
    tex_ref, geom_ident = _try_codes([("entity file", java_code)])
    if not (tex_ref and geom_ident):
        candidates_cls = list(dict.fromkeys(filter(None, [
            entity_cls,
            entity_basename,
            "".join(w.capitalize() for w in entity_basename.split("_")),
        ])))
        for lookup_key in candidates_cls:
            entry = _RENDERER_MAP.get(lookup_key) or _RENDERER_MAP.get(lookup_key + "Entity")
            if not entry:
                continue
            extra: List[Tuple[str, str]] = []
            if entry.get("renderer_code"):
                extra.append((f"renderer:{entry['renderer']}", entry["renderer_code"]))
            if entry.get("model_code"):
                extra.append((f"model:{entry['model']}", entry["model_code"]))
            if extra:
                t2, g2 = _try_codes(extra)
                tex_ref   = tex_ref   or t2
                geom_ident = geom_ident or g2
            if tex_ref and geom_ident:
                break
    if not (tex_ref and geom_ident):
        ent_lower = entity_basename.lower().replace("entity", "").strip("_")
        related_codes: List[Tuple[str, str]] = []
        for path, code in _ALL_JAVA_FILES.items():
            fname_stem = os.path.splitext(os.path.basename(path))[0].lower()
            if ent_lower and ent_lower in fname_stem and fname_stem != entity_basename.lower():
                related_codes.append((fname_stem, code))
            elif entity_cls and entity_cls in code and path not in java_code:
                cls_there = extract_class_name(code)
                if cls_there and any(kw in cls_there for kw in ("Renderer", "Model", "Layer")):
                    related_codes.append((cls_there, code))
        if related_codes:
            t3, g3 = _try_codes(related_codes[:8])
            tex_ref    = tex_ref    or t3
            geom_ident = geom_ident or g3
    if not tex_ref:
        tex_ref = _best_texture_on_disk()
        if tex_ref:
            pass

    if not geom_ident:
        geom_ident = _best_geometry_on_disk()
        if geom_ident:
            pass

    return tex_ref, geom_ident


def _path_looks_like_procedure(path: str, code: str = "") -> bool:
    norm_path = os.path.normpath(path).lower()
    stem = os.path.splitext(os.path.basename(path))[0].lower()
    if "procedure" in stem or "procudure" in stem or "procedures" in norm_path:
        return True
    if re.search(r'\b(?:procedure|procudure)\b', code or "", re.I):
        return True
    return False

def _procedure_entity_tokens(entity_identifier: str,
                             entity_cls_name: Optional[str] = None,
                             entity_basename: Optional[str] = None) -> Set[str]:
    tokens: Set[str] = set()
    for name in (entity_identifier, entity_cls_name, entity_basename):
        if not name:
            continue
        clean = str(name).split(":")[-1]
        tokens.update(_camel_tokens(clean))
        tokens.update(
            t for t in re.split(r'[^A-Za-z0-9]+', clean.lower())
            if len(t) > 1
        )
    return {t for t in tokens if len(t) > 1}

def _procedure_matches_entity(path: str, code: str, entity_identifier: str,
                              entity_cls_name: Optional[str] = None,
                              entity_basename: Optional[str] = None) -> bool:
    if not _path_looks_like_procedure(path, code):
        return False

    norm_path = os.path.normpath(path).lower()
    stem = os.path.splitext(os.path.basename(path))[0].lower()
    hay = " ".join([
        norm_path,
        stem,
        code[:4000] if code else "",
        entity_identifier.lower() if entity_identifier else "",
        (entity_cls_name or "").lower(),
        (entity_basename or "").lower(),
    ])

    tokens = _procedure_entity_tokens(entity_identifier, entity_cls_name, entity_basename)
    score = 0.0
    if "/procedures/" in norm_path or norm_path.endswith("/procedures"):
        score += 0.75
    if "procedure" in stem or "procudure" in stem:
        score += 0.4
    if entity_identifier and entity_identifier.lower() in hay:
        score += 2.0
    if entity_cls_name and re.search(rf'\b{re.escape(entity_cls_name)}\b', code or "", re.I):
        score += 1.25
    if entity_basename and re.search(rf'\b{re.escape(entity_basename)}\b', code or "", re.I):
        score += 1.25

    shared = 0
    for tok in sorted(tokens, key=len, reverse=True):
        if len(tok) >= 3 and re.search(rf'\b{re.escape(tok)}\b', hay, re.I):
            shared += 1
    score += min(shared, 4) * 0.35

    if re.search(r'\b(?:execute|tick|update|spawn|hurt|attack|interact|entity)\b', hay, re.I):
        score += 0.25

    return score >= 1.2

def _collect_entity_procedure_sources(java_code: str, java_path: str,
                                      entity_identifier: str,
                                      entity_cls_name: Optional[str] = None,
                                      entity_basename: Optional[str] = None) -> List[Tuple[str, str]]:
    if not _ALL_JAVA_FILES:
        return []

    collected: List[Tuple[str, str]] = []
    for path, code in _ALL_JAVA_FILES.items():
        if path == java_path:
            continue
        if not _procedure_matches_entity(path, code, entity_identifier, entity_cls_name, entity_basename):
            continue
        collected.append((path, code))

    collected.sort(
        key=lambda item: (
            -int("/procedures/" in os.path.normpath(item[0]).lower()),
            -int("procedure" in os.path.basename(item[0]).lower()),
            -len(item[1]),
            os.path.basename(item[0]).lower(),
        )
    )
    return collected[:12]

def _extract_procedure_method_bodies(java_code: str) -> List[Tuple[str, str]]:
    method_names = [
        "execute", "executeProcedure", "onExecute", "tick", "update",
        "run", "apply", "process", "handle", "onUpdate", "entityTick"
    ]
    bodies: List[Tuple[str, str]] = []
    for method_name in method_names:
        body = _extract_method_body(java_code, [method_name])
        if body:
            bodies.append((method_name, body))
    if not bodies and "public static void" in java_code and "entity" in java_code.lower():
        body = _extract_method_body(java_code, ["execute"])
        if body:
            bodies.append(("execute", body))
    return bodies

def _translate_procedure_body_to_js(java_body: str, namespace: str,
                                    entity_var: str = "entity") -> List[str]:
    if not java_body:
        return []
    if not JAVALANG_AVAILABLE:
        return [
            f'// {line.strip()}'
            for line in java_body.splitlines()
            if line.strip()
        ]

    dummy_code = f"""
public class Dummy {{
    public void dummy() {{
        {java_body}
    }}
}}
"""
    try:
        tree = javalang.parse.parse(dummy_code)
    except Exception:
        return [
            f'// {line.strip()}'
            for line in java_body.splitlines()
            if line.strip()
        ]

    symbol_table = JavaSymbolTable()
    symbol_table.set_variable_type(entity_var, "Entity")
    lines: List[str] = []
    for _, node in tree.filter(javalang.tree.MethodDeclaration):
        if node.name != "dummy":
            continue
        for stmt in node.body or []:
            translated = translate_statement(stmt, entity_var, namespace, symbol_table)
            if translated:
                lines.extend(translated)

    if not lines:
        return [
            f'// {line.strip()}'
            for line in java_body.splitlines()
            if line.strip()
        ]
    return lines

def _infer_procedure_trigger(path: str, code: str) -> str:
    hay = " ".join([
        os.path.basename(path).lower(),
        os.path.splitext(os.path.basename(path))[0].lower(),
        code[:2500].lower() if code else "",
    ])
    if re.search(r'\b(spawn|join|load|init|onentityspawn)\b', hay):
        return "spawn"
    if re.search(r'\b(hurt|damage|attack|interact|use|click|rightclick|leftclick)\b', hay):
        return "event"
    return "tick"

def _emit_entity_procedure_script(entity_identifier: str, namespace: str,
                                  related_sources: List[Tuple[str, str]],
                                  bp_folder: str) -> Optional[str]:
    if not related_sources:
        return None

    safe_entity = sanitize_identifier(entity_identifier.split(":")[-1]) or "entity"
    script_lines: List[str] = [
        'import { world, system } from "@minecraft/server";',
        '',
        f'// Auto-generated procedure bridge for {entity_identifier}',
        'const PROCEDURE_HANDLERS = [];',
        '',
    ]

    for idx, (src_path, src_code) in enumerate(related_sources):
        src_name = extract_class_name(src_code) or os.path.splitext(os.path.basename(src_path))[0]
        src_safe = sanitize_identifier(src_name) or f"procedure_{idx}"
        trigger = _infer_procedure_trigger(src_path, src_code)
        method_bodies = _extract_procedure_method_bodies(src_code)
        if not method_bodies:
            script_lines.extend([
                f'// {os.path.basename(src_path)}: no executable procedure body detected',
                '',
            ])
            continue

        for method_name, body in method_bodies:
            translated = _translate_procedure_body_to_js(body, namespace, entity_var="entity")
            script_lines.extend([
                f'PROCEDURE_HANDLERS.push({{',
                f'  entityId: "{entity_identifier}",',
                f'  trigger: "{trigger}",',
                f'  source: "{os.path.basename(src_path)}::{method_name}",',
                f'  run(entity) {{',
            ])
            for line in translated:
                if line.startswith("//"):
                    script_lines.append(f'    {line}')
                else:
                    script_lines.append(f'    {line}')
            script_lines.extend([
                '  }',
                '});',
                '',
            ])

    script_lines.extend([
        'system.runInterval(() => {',
        '  const overworld = world.getDimension("minecraft:overworld");',
        '  for (const handler of PROCEDURE_HANDLERS) {',
        '    try {',
        '      const entities = overworld.getEntities({ type: handler.entityId });',
        '      for (const entity of entities) {',
        '        handler.run(entity);',
        '      }',
        '    } catch (error) {',
        '      console.warn(`procedure bridge failed: ${String(error)}`);',
        '    }',
        '  }',
        '}, 1);',
        '',
    ])

    os.makedirs(os.path.join(bp_folder, "scripts"), exist_ok=True)
    out_path = os.path.join(bp_folder, "scripts", f"{safe_entity}_procedures.js")
    with open(out_path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(script_lines))

    main_path = os.path.join(bp_folder, "scripts", "main.js")
    import_line = f'import "./{os.path.basename(out_path)}";\n'
    if os.path.exists(main_path):
        with open(main_path, "r", encoding="utf-8") as fh:
            content = fh.read()
        if os.path.basename(out_path) not in content:
            with open(main_path, "w", encoding="utf-8") as fh:
                fh.write(import_line + content)
    else:
        with open(main_path, "w", encoding="utf-8") as fh:
            fh.write(import_line)

    return out_path

def convert_java_to_bedrock(java_path: str, entity_identifier: str, gecko_maps: dict, geom_file_map: dict, geom_ns_map: dict, anim_key_map: dict, stats: dict):
    _REAL_PRINT(f"[DEBUG] convert_java_to_bedrock called for {java_path} as {entity_identifier}")
    try:
        with open(java_path, 'r', encoding='utf-8', errors='ignore') as f:
            java_code = f.read()
    except Exception as e:
        _warn(f" Failed to read {java_path}: {e}")
        stats["errors"].append(f"read:{java_path}:{e}")
        return
    if not is_likely_entity(java_code, java_path) or _should_skip_entity_artifact(java_code, java_path, extract_class_name(java_code)):
        stats["skipped_files"].append(java_path)
        return

    symbol_table = JavaSymbolTable()
    symbol_table.scan_java_file(java_code)

    parts = entity_identifier.split(":")
    namespace = sanitize_identifier(parts[0]) if parts else "converted"
    entity_name = clean_java_artifact_name(parts[1]) if len(parts) > 1 else clean_java_artifact_name("entity")
    clean_identifier = f"{namespace}:{entity_name}"
    entity_cls_name = extract_class_name(java_code)
    entity_basename = clean_java_artifact_name(os.path.splitext(os.path.basename(java_path))[0])
    related_procedure_sources = _collect_entity_procedure_sources(
        java_code,
        java_path,
        clean_identifier,
        entity_cls_name=entity_cls_name,
        entity_basename=entity_basename,
    )
    merged_java_code = java_code
    if related_procedure_sources:
        merged_java_code = java_code + "\n\n" + "\n\n".join(
            f"// PROCEDURE SOURCE: {os.path.basename(src_path)}\n{src_code}"
            for src_path, src_code in related_procedure_sources
        )
        for _proc_path, _proc_code in related_procedure_sources:
            try:
                symbol_table.scan_java_file(_proc_code)
            except Exception:
                pass
    ai_goals = extract_ai_goals_from_java(merged_java_code)
    animations = extract_animations_from_java(java_code, namespace, entity_name)
    attributes = extract_attributes_from_java(merged_java_code)
    immunities = extract_damage_immunities_from_java(merged_java_code)
    bounding_proc = detect_dynamic_bounding_procedure(merged_java_code)
    despawn_ticks = detect_despawn_ticks(merged_java_code)
    collision_w, collision_h = 0.6, 1.8
    if bounding_proc:
        collision_w, collision_h = 2.5, 3.0
    m_dims = re.search(r'EntityDimensions\.(?:scalable|fixed)\s*\(\s*([0-9.]+)\s*,\s*([0-9.]+)', java_code)
    if m_dims:
        collision_w, collision_h = float(m_dims.group(1)), float(m_dims.group(2))
    else:
        m_dims2 = re.search(r'getDimensions[^{]*\{[^}]*\(\s*([0-9.]+)\s*,\s*([0-9.]+)\s*\)', java_code, re.DOTALL)
        if m_dims2:
            collision_w, collision_h = float(m_dims2.group(1)), float(m_dims2.group(2))
    bedrock_entity = {
        "format_version": BP_RP_FORMAT_VERSION,
        "minecraft:entity": {
            "description": {"identifier": clean_identifier, "is_spawnable": True, "is_experimental": False},
            "components": {
                "minecraft:type_family": {"family": ["mob", namespace]},
                "minecraft:physics": {"has_gravity": True, "has_collision": True},
                "minecraft:collision_box": {"width": collision_w, "height": collision_h},
                "minecraft:health": {"value": int(attributes.get("health", 20)), "max": int(attributes.get("health", 20))},
                "minecraft:movement": {"value": attributes.get("movement_speed", 0.3)},
                "minecraft:navigation.walk": {"can_path_over_water": False, "avoid_water": True, "can_pass_doors": True},
                "minecraft:movement.basic": {},
                "minecraft:jump.static": {},
                "minecraft:behavior.float": {"priority": 0}
            },
            "events": {}
        }
    }

    dynamic_properties = {}
    if 'IEnergyStorage' in java_code or 'receiveEnergy' in java_code:
        dynamic_properties[f"{namespace}:energy_stored"] = {"type": "int", "default": 0}
        dynamic_properties[f"{namespace}:max_energy"] = {"type": "int", "default": 1000}
    if 'IFluidHandler' in java_code or 'fill' in java_code:
        dynamic_properties[f"{namespace}:fluid_amount"] = {"type": "int", "default": 0}
        dynamic_properties[f"{namespace}:fluid_type"] = {"type": "string", "default": ""}
    if 'IItemHandler' in java_code or 'insertItem' in java_code:
        dynamic_properties[f"{namespace}:slot_contents"] = {"type": "string", "default": "[]"}
    if dynamic_properties:
        bedrock_entity["minecraft:entity"]["description"]["properties"] = dynamic_properties
    if attributes.get("attack_damage", 0) > 0:
        damage_value = min(int(attributes["attack_damage"]), 50)
        bedrock_entity["minecraft:entity"]["components"]["minecraft:attack"] = {"damage": damage_value}
    armor_value = float(attributes.get("armor", 0.0))
    damage_triggers = []
    if armor_value and armor_value != 0.0:
        reduction = min(0.80, armor_value * 0.035)
        multiplier = max(0.20, 1.0 - reduction)
        damage_triggers.append({"cause": "all", "damage_multiplier": round(multiplier, 4), "description": "converted_armor_java_scale"})
    for cause in immunities:
        if cause == "all":
            continue
        damage_triggers.append({"cause": cause, "damage_multiplier": 0.001, "description": f"converted_immunity_{cause}"})
    damage_triggers.append({"cause": "entity_attack", "deals_damage": True})
    bedrock_entity["minecraft:entity"]["components"]["minecraft:damage_sensor"] = {"triggers": damage_triggers}
    behaviors = {}
    move_speed = attributes.get("movement_speed", 0.3)
    follow_range = attributes.get("follow_range", 16.0)
    for goal in ai_goals:
        priority = JAVA_GOAL_PRIORITIES.get(goal, 10)
        if goal == "NearestAttackableTargetGoal":
            behaviors["minecraft:behavior.nearest_attackable_target"] = {
                "priority": priority,
                "entity_types": [{"filters": {"test": "is_family", "subject": "other", "value": "player"}, "max_dist": int(follow_range)}],
                "must_see": False,
                "reselect_targets": True
            }
        elif goal == "HurtByTargetGoal":
            behaviors["minecraft:behavior.hurt_by_target"] = {
                "priority": priority,
                "alert_same_type": False
            }
        elif goal in ("OwnerHurtByTargetGoal", "OwnerHurtTargetGoal"):
            behaviors["minecraft:behavior.owner_hurt_by_target"] = {"priority": priority}
        elif goal == "MeleeAttackGoal":
            behaviors["minecraft:behavior.melee_attack"] = {
                "priority": priority,
                "speed_multiplier": max(1.0, move_speed * 2.0),
                "track_target": True,
                "require_complete_path": False
            }
        elif goal in ("RangedAttackGoal", "RangedBowAttackGoal"):
            behaviors["minecraft:behavior.ranged_attack"] = {
                "priority": priority,
                "attack_interval_min": 1.0,
                "attack_interval_max": 3.0,
                "attack_radius": min(follow_range, 15.0),
                "speed_multiplier": max(1.0, move_speed * 1.5)
            }
            bedrock_entity["minecraft:entity"]["components"]["minecraft:shooter"] = {
                "def": "minecraft:arrow"
            }
        elif goal == "LeapAtTargetGoal":
            behaviors["minecraft:behavior.leap_at_target"] = {
                "priority": priority,
                "yd": 0.4
            }
        elif goal == "AvoidEntityGoal":
            behaviors["minecraft:behavior.avoid_mob_type"] = {
                "priority": priority,
                "entity_types": [{"filters": {"test": "is_family", "subject": "other", "value": "player"}, "max_dist": 6.0}],
                "walk_speed_multiplier": max(1.0, move_speed * 1.2),
                "sprint_speed_multiplier": max(1.2, move_speed * 2.0)
            }
        elif goal == "PanicGoal":
            behaviors["minecraft:behavior.panic"] = {
                "priority": priority,
                "speed_multiplier": max(1.25, move_speed * 2.5)
            }
        elif goal == "RunAroundLikeCrazyGoal":
            behaviors["minecraft:behavior.run_around_like_crazy"] = {
                "priority": priority,
                "speed_multiplier": max(1.0, move_speed * 2.0)
            }
        elif goal == "OpenDoorGoal":
            behaviors["minecraft:behavior.open_door"] = {
                "priority": priority,
                "close_door_after": True
            }
            bedrock_entity["minecraft:entity"]["components"]["minecraft:navigation.walk"]["can_open_doors"] = True
        elif goal == "BreakDoorGoal":
            behaviors["minecraft:behavior.break_door"] = {
                "priority": priority
            }
            bedrock_entity["minecraft:entity"]["components"]["minecraft:navigation.walk"]["can_break_doors"] = True
        elif goal == "FollowOwnerGoal":
            behaviors["minecraft:behavior.follow_owner"] = {
                "priority": priority,
                "speed_multiplier": max(1.0, move_speed * 1.5),
                "start_distance": 10.0,
                "stop_distance": 2.0
            }
        elif goal == "FollowParentGoal":
            behaviors["minecraft:behavior.follow_parent"] = {
                "priority": priority,
                "speed_multiplier": max(1.0, move_speed * 1.1)
            }
        elif goal == "FollowMobGoal":
            behaviors["minecraft:behavior.follow_mob"] = {
                "priority": priority,
                "speed_multiplier": max(1.0, move_speed * 1.1),
                "stop_distance": 3.0,
                "search_range": int(follow_range)
            }
        elif goal == "SitWhenOrderedToGoal":
            behaviors["minecraft:behavior.sit"] = {"priority": priority}
            bedrock_entity["minecraft:entity"]["components"]["minecraft:is_tamed"] = {}
        elif goal == "BreedGoal":
            behaviors["minecraft:behavior.breed"] = {
                "priority": priority,
                "speed_multiplier": max(1.0, move_speed * 1.0)
            }
            bedrock_entity["minecraft:entity"]["components"]["minecraft:breedable"] = {
                "require_tame": False,
                "breeds_with": []
            }
        elif goal == "TemptGoal":
            behaviors["minecraft:behavior.tempt"] = {
                "priority": priority,
                "speed_multiplier": max(1.0, move_speed * 1.25),
                "within_radius": 6.0,
                "can_tempt_while_leashed": False
            }
        elif goal == "FloatGoal":
            behaviors["minecraft:behavior.float"] = {"priority": priority}
            bedrock_entity["minecraft:entity"]["components"]["minecraft:navigation.walk"]["can_swim"] = True
        elif goal == "WaterAvoidingRandomStrollGoal":
            behaviors["minecraft:behavior.random_stroll"] = {
                "priority": priority,
                "speed_multiplier": move_speed,
                "xz_dist": 10,
                "y_dist": 7
            }
            bedrock_entity["minecraft:entity"]["components"]["minecraft:navigation.walk"]["avoid_water"] = True
        elif goal == "RandomSwimmingGoal":
            behaviors["minecraft:behavior.random_swimming"] = {
                "priority": priority,
                "speed_multiplier": max(1.0, move_speed * 1.5),
                "xz_dist": 30,
                "y_dist": 15
            }
            bedrock_entity["minecraft:entity"]["components"]["minecraft:navigation.walk"]["can_swim"] = True
        elif goal == "RandomStrollGoal":
            behaviors["minecraft:behavior.random_stroll"] = {
                "priority": priority,
                "speed_multiplier": move_speed
            }
        elif goal == "LookAtPlayerGoal":
            behaviors["minecraft:behavior.look_at_player"] = {
                "priority": priority,
                "look_distance": follow_range / 2.0,
                "probability": 0.02
            }
        elif goal == "RandomLookAroundGoal":
            behaviors["minecraft:behavior.random_look_around"] = {"priority": priority}
        elif goal == "SwimGoal":
            behaviors["minecraft:behavior.float"] = {"priority": priority}
            bedrock_entity["minecraft:entity"]["components"]["minecraft:navigation.walk"]["can_swim"] = True
        elif goal == "BreatheAirGoal":
            behaviors["minecraft:behavior.move_to_water"] = {"priority": priority, "search_range": 8, "search_height": 4}
        elif goal in ("NearestAttackableTargetExpiringGoal", "ToggleableNearestAttackableTargetGoal"):
            behaviors.setdefault("minecraft:behavior.nearest_attackable_target", {
                "priority": priority, "must_see": False, "reselect_targets": True,
                "entity_types": [{"filters": {"test": "is_family", "subject": "other", "value": "player"}, "max_dist": int(follow_range)}]
            })
        elif goal == "NonTamedTargetGoal":
            behaviors["minecraft:behavior.nearest_attackable_target"] = {
                "priority": priority, "must_see": True,
                "entity_types": [{"filters": {"all_of": [
                    {"test": "is_family", "subject": "other", "value": "player"},
                    {"test": "is_owner", "subject": "other", "operator": "!=", "value": True}
                ]}, "max_dist": int(follow_range)}]
            }
        elif goal == "DefendVillageTargetGoal":
            behaviors["minecraft:behavior.nearest_attackable_target"] = {
                "priority": priority, "must_see": False,
                "entity_types": [{"filters": {"test": "is_family", "subject": "other", "value": "monster"}, "max_dist": int(follow_range)}]
            }
        elif goal == "ResetAngerGoal":
            bedrock_entity["minecraft:entity"]["components"].setdefault("minecraft:anger_level", {"max_anger": 20, "anger_decrement_interval": 1.0})
        elif goal == "OcelotAttackGoal":
            behaviors["minecraft:behavior.melee_attack"] = {"priority": priority, "speed_multiplier": max(1.0, move_speed * 2.0), "track_target": True, "require_complete_path": False}
        elif goal == "CreeperSwellGoal":
            behaviors["minecraft:behavior.swell"] = {"priority": priority}
        elif goal == "RangedCrossbowAttackGoal":
            behaviors["minecraft:behavior.ranged_attack"] = {"priority": priority, "attack_interval_min": 1.0, "attack_interval_max": 3.0, "attack_radius": min(follow_range, 15.0), "speed_multiplier": max(1.0, move_speed * 1.5)}
            bedrock_entity["minecraft:entity"]["components"]["minecraft:shooter"] = {"def": "minecraft:arrow"}
        elif goal == "MoveTowardsTargetGoal":
            behaviors["minecraft:behavior.move_towards_target"] = {"priority": priority, "speed_multiplier": max(1.0, move_speed * 1.5), "within": int(follow_range)}
        elif goal == "FleeSunGoal":
            behaviors["minecraft:behavior.move_outdoors"] = {"priority": priority, "speed_multiplier": max(1.0, move_speed * 1.2), "timeout_cooldown": 8.0}
        elif goal == "RestrictSunGoal":
            behaviors["minecraft:behavior.restrict_sun"] = {"priority": priority}
        elif goal == "InteractDoorGoal":
            behaviors["minecraft:behavior.open_door"] = {"priority": priority, "close_door_after": True}
            bedrock_entity["minecraft:entity"]["components"]["minecraft:navigation.walk"]["can_open_doors"] = True
        elif goal == "BreakBlockGoal":
            behaviors["minecraft:behavior.break_door"] = {"priority": priority}
        elif goal == "UseItemGoal":
            pass
        elif goal in ("MoveThroughVillageGoal", "MoveThroughVillageAtNightGoal", "ReturnToVillageGoal", "PatrolVillageGoal", "MoveTowardsRaidGoal"):
            behaviors["minecraft:behavior.move_through_village"] = {"priority": priority, "speed_multiplier": move_speed, "only_at_night": goal == "MoveThroughVillageAtNightGoal"}
        elif goal == "MoveTowardsRestrictionGoal":
            behaviors["minecraft:behavior.move_towards_restriction"] = {"priority": priority, "speed_multiplier": move_speed}
        elif goal == "MoveToBlockGoal":
            behaviors["minecraft:behavior.move_to_block"] = {"priority": priority, "speed_multiplier": move_speed, "search_range": 8, "search_height": 4, "goal_radius": 1.0}
        elif goal == "FindWaterGoal":
            behaviors["minecraft:behavior.move_to_water"] = {"priority": priority, "search_range": 8, "search_height": 4}
            bedrock_entity["minecraft:entity"]["components"]["minecraft:navigation.walk"]["can_swim"] = True
        elif goal == "RandomWalkingGoal":
            behaviors["minecraft:behavior.random_stroll"] = {"priority": priority, "speed_multiplier": move_speed}
        elif goal == "FollowBoatGoal":
            behaviors["minecraft:behavior.follow_mob"] = {"priority": priority, "speed_multiplier": max(1.0, move_speed * 1.2), "stop_distance": 3.0, "search_range": int(follow_range)}
        elif goal == "FollowSchoolLeaderGoal":
            behaviors["minecraft:behavior.follow_mob"] = {"priority": priority, "speed_multiplier": max(1.0, move_speed * 1.1), "stop_distance": 2.0, "search_range": int(follow_range)}
        elif goal == "LlamaFollowCaravanGoal":
            behaviors["minecraft:behavior.follow_caravan"] = {"priority": priority, "speed_multiplier": max(1.0, move_speed * 1.2)}
        elif goal == "LandOnOwnersShoulderGoal":
            behaviors.setdefault("minecraft:behavior.float", {"priority": 0})
        elif goal == "SitGoal":
            behaviors["minecraft:behavior.sit"] = {"priority": priority}
        elif goal == "EatGrassGoal":
            behaviors["minecraft:behavior.eat_block"] = {"priority": priority, "eat_and_replace_block_pairs": [{"eat_block": "grass", "replace_block": "dirt"}], "success_chance": "0.05", "time_until_eat": 1.8}
        elif goal == "BegGoal":
            behaviors["minecraft:behavior.beg"] = {"priority": priority, "look_distance": 8.0, "look_time": 40}
        elif goal == "TradeWithPlayerGoal":
            behaviors["minecraft:behavior.trade_with_player"] = {"priority": priority}
        elif goal == "LookAtCustomerGoal":
            behaviors["minecraft:behavior.look_at_trading_player"] = {"priority": priority}
        elif goal == "ShowVillagerFlowerGoal":
            behaviors["minecraft:behavior.offer_flower"] = {"priority": priority}
        elif goal == "TriggerSkeletonTrapGoal":
            behaviors["minecraft:behavior.summon_entity"] = {"priority": priority, "summon_choices": [{"min_activation_range": 0, "max_activation_range": 16, "summon_cap": 4, "summon_cap_radius": 8.0, "weight": 10, "entity_type": "minecraft:skeleton_horse"}]}
        elif goal == "DolphinJumpGoal":
            behaviors["minecraft:behavior.jump_for_food"] = {"priority": priority}
        elif goal == "JumpGoal":
            behaviors["minecraft:behavior.jump_to_block"] = {"priority": priority, "search_width": 8, "search_height": 4, "minimum_path_length": 2}
        elif goal == "CatLieOnBedGoal":
            behaviors["minecraft:behavior.sleep"] = {"priority": priority, "sleep_collider_height": 0.3, "sleep_collider_width": 1.0, "sleep_y_offset": 0.6, "timeout_cooldown": 10.0}
        elif goal == "CatSitOnBlockGoal":
            behaviors["minecraft:behavior.move_to_block"] = {"priority": priority, "speed_multiplier": move_speed, "search_range": 8, "search_height": 4, "goal_radius": 1.0}
        elif goal == "LookAtGoal":
            behaviors["minecraft:behavior.look_at_entity"] = {"priority": priority, "look_distance": follow_range / 2.0, "probability": 0.02}
        elif goal == "LookAtWithoutMovingGoal":
            behaviors["minecraft:behavior.look_at_player"] = {"priority": priority, "look_distance": follow_range / 2.0, "probability": 0.02}
        elif goal == "LookRandomlyGoal":
            behaviors["minecraft:behavior.random_look_around"] = {"priority": priority}
    if "minecraft:behavior.float" not in behaviors:
        behaviors["minecraft:behavior.float"] = {"priority": 0}
    if behaviors:
        bedrock_entity["minecraft:entity"]["components"].update(behaviors)
    if any(g in ai_goals for g in ("SitWhenOrderedToGoal", "FollowOwnerGoal", "OwnerHurtByTargetGoal", "OwnerHurtTargetGoal")):
        bedrock_entity["minecraft:entity"]["components"].setdefault("minecraft:tameable", {
            "probability": 0.33,
            "tame_items": "bone",
            "tame_event": {"event": "minecraft:on_tame", "target": "self"}
        })
        bedrock_entity["minecraft:entity"]["components"].setdefault("minecraft:is_tamed", {})
    comps = bedrock_entity["minecraft:entity"]["components"]
    is_flyer = bool(
        re.search(r'extends\s+(?:FlyingMob|Ghast|Phantom|Bee|Parrot|AbstractFlyingEntity)', java_code, re.I) or
        re.search(r'FlyingMoveControl|setNoGravity\s*\(\s*true', java_code)
    )
    is_swimmer = bool(
        re.search(r'extends\s+(?:WaterAnimal|Squid|Dolphin|TropicalFish|AbstractFish)', java_code, re.I) or
        "RandomSwimmingGoal" in ai_goals or "FindWaterGoal" in ai_goals
    )
    is_climber = bool(re.search(r'canClimb\(\)|onClimbable|Spider|CaveSpider', java_code, re.I))
    if is_flyer and not is_swimmer:
        comps.pop("minecraft:navigation.walk", None)
        comps["minecraft:navigation.fly"] = {"can_path_over_water": True}
        comps["minecraft:movement.fly"] = {}
        comps.pop("minecraft:jump.static", None)
        comps["minecraft:can_fly"] = {}
    elif is_swimmer:
        comps["minecraft:navigation.walk"]["can_swim"] = True
        comps.setdefault("minecraft:navigation.swim", {"can_path_over_water": True})
        comps["minecraft:underwater_movement"] = {"value": round(attributes.get("movement_speed", 0.3) * 0.85, 4)}
    if is_climber:
        comps["minecraft:navigation.climb"] = {}
    generate_entity_events(bedrock_entity, ai_goals, merged_java_code, namespace, clean_identifier, attributes)
    if despawn_ticks is not None and despawn_ticks <= 600:
        bedrock_entity["minecraft:entity"]["components"]["minecraft:timer"] = {
            "looping": False,
            "time": round(despawn_ticks / 20.0, 2),
            "time_down_event": {"event": "minecraft:entity_spawned"}
        }
    metadata = {
        "source_java_file": os.path.basename(java_path),
        "raw_attributes": attributes,
        "animations_extracted": sorted(list(animations)),
        "immunities_detected": immunities,
        "dynamic_bounding_box_procedure": bounding_proc,
        "despawn_after_ticks": despawn_ticks,
        "related_procedures": [os.path.basename(p) for p, _ in related_procedure_sources],
    }
    bedrock_entity["minecraft:entity"]["components"]["_converter_metadata"] = metadata
    def should_loop(anim_name: str) -> bool:
        n = anim_name.lower()
        if any(k in n for k in ["idle", "chase", "walk", "run", "pose", "sit", "hover"]):
            return True
        if any(k in n for k in ["attack", "hit", "strike", "death", "slam", "bite"]):
            return False
        return True
    anim_json = {"format_version": RP_LEGACY_ANIM_FORMAT, "animations": {}}
    primary_animation_key = None
    if animations:
        for anim in sorted(animations):
            loop = should_loop(anim)
            length = 1.0 if loop else 0.5
            anim_json["animations"][anim] = {"loop": loop, "animation_length": length}
        primary_animation_key = sorted(animations)[0]
    else:
        base_id = f"animation.{namespace}.{entity_name}"
        idle_id = f"{base_id}.idle"
        anim_json["animations"][idle_id] = {"loop": True, "animation_length": 1.0}
        anim_json["animations"][f"{base_id}.walk"] = {"loop": True, "animation_length": 0.5}
        anim_json["animations"][f"{base_id}.run"] = {"loop": True, "animation_length": 0.4}
        anim_json["animations"][f"{base_id}.attack"] = {"loop": False, "animation_length": 0.5}
        primary_animation_key = idle_id
    entity_basename = clean_java_artifact_name(os.path.splitext(os.path.basename(java_path))[0])
    anim_json_path_rp = os.path.join(RP_FOLDER, "animations", f"{entity_basename}.animation.json")
    if not os.path.exists(anim_json_path_rp):
        safe_write_json(anim_json_path_rp, anim_json)

    else:
        pass

    entity_json_path = os.path.join(BP_FOLDER, "entities", f"{entity_basename}.json")
    safe_write_json(entity_json_path, bedrock_entity)

    stats["converted_entities_bp"].append(entity_json_path)
    try:
        _emit_entity_procedure_script(clean_identifier, namespace, related_procedure_sources, BP_FOLDER)
    except Exception as _proc_emit_err:
        stats["warnings"].append(f"procedure-script:{java_path}:{_proc_emit_err}")
    java_geom_tuple = find_model_geometry_in_code(java_code)
    java_geom_identifier: Optional[str] = None
    if java_geom_tuple:
        java_ns, java_name = java_geom_tuple
        java_ns_clean = sanitize_identifier(java_ns) if java_ns else None
        java_name_clean = sanitize_identifier(java_name) if java_name else None
        java_key = (java_ns_clean, java_name_clean)
        java_key2 = (namespace.lower(), java_name_clean)
        if java_key in geom_ns_map:
            java_geom_identifier = geom_ns_map[java_key]

        elif java_key2 in geom_ns_map:
            java_geom_identifier = geom_ns_map[java_key2]

        elif java_name_clean in geom_file_map:
            java_geom_identifier = geom_file_map[java_name_clean]

    aggressive_tex, aggressive_geom = find_entity_assets_aggressively(
        java_code, entity_basename, namespace, entity_cls=entity_cls_name
    )
    geom_identifier: Optional[str] = None
    if java_geom_identifier:
        geom_identifier = java_geom_identifier
    entity_class_simple = os.path.splitext(os.path.basename(java_path))[0]
    geom_tuple = None
    geom_tuple = gecko_maps.get("entity_to_geometry", {}).get(entity_class_simple)
    if not geom_tuple:
        for k, v in gecko_maps.get("entity_to_geometry", {}).items():
            if (k.lower() == entity_class_simple.lower()
                    or k.lower().endswith(entity_class_simple.lower())
                    or entity_class_simple.lower().endswith(k.lower())):
                geom_tuple = v
                break
    if not geom_tuple:
        model_cls = gecko_maps.get("entity_to_model", {}).get(entity_class_simple)
        if model_cls:
            geom_tuple = gecko_maps.get("model_map", {}).get(model_cls)
    if not geom_tuple:
        for model_cls, geom in gecko_maps.get("model_map", {}).items():
            if entity_basename.lower() in model_cls.lower() or entity_basename.lower() in geom[1].lower():
                geom_tuple = geom
                break
    if geom_tuple:
        ns_hint, geom_name = geom_tuple
        if geom_name:
            geom_name_lower = geom_name.lower()
            skip_keywords = [
                "spawn_egg",
                "glass", "stone", "wood", "brick", "ore", "concrete", "sand", "dirt", "grass",
                "item", "tool", "weapon", "armor", "bow", "sword", "pickaxe", "axe", "shovel", "hoe",
                "cube", "box", "plane", "simple", "basic", "block",
                "slab", "stair", "fence", "door", "gate", "lamp", "button"
            ]
            if any(kw in geom_name_lower for kw in skip_keywords):
                geom_tuple = None
        if geom_tuple:
            ns_hint, geom_name = geom_tuple
            ns_hint_clean  = sanitize_identifier(ns_hint)  if ns_hint  else None
            geom_name_clean = sanitize_identifier(geom_name) if geom_name else None
            key  = (ns_hint_clean, geom_name_clean)
            key2 = (namespace.lower(), geom_name_clean)
            if key in geom_ns_map:
                geom_identifier = geom_ns_map[key]
            elif key2 in geom_ns_map:
                geom_identifier = geom_ns_map[key2]
            elif geom_name_clean in geom_file_map:
                geom_identifier = geom_file_map[geom_name_clean]
            else:
                for (ns_k, name_k), ident in geom_ns_map.items():
                    if name_k and geom_name_clean and name_k.endswith(geom_name_clean):
                        geom_identifier = ident
                        break
    if not geom_identifier and entity_basename.lower() in geom_file_map:
        geom_identifier = geom_file_map[entity_basename.lower()]
    if not geom_identifier and geom_file_map:
        ns_tokens = set(re.split(r'[_\-]', namespace.lower())) - {'the', 'a', 'an', 'of', ''}
        ent_tokens = set(re.split(r'[_\-]', entity_basename.lower())) - {'entity', 'mob', ''}
        best_key = None
        best_score = 0
        for gkey, gident in geom_file_map.items():
            geo_tokens = set(re.split(r'[_\-]', gkey.lower()))
            score = len(geo_tokens & ns_tokens) * 2 + len(geo_tokens & ent_tokens)
            if score > best_score:
                best_score = score
                best_key = gkey
        if best_key and best_score >= 2:
            geom_identifier = geom_file_map[best_key]
        elif len(geom_file_map) == 1:
            geom_identifier = next(iter(geom_file_map.values()))
    if not geom_identifier:
        if aggressive_geom:
            aggressive_lower = aggressive_geom.lower()
            skip_keywords = [
                "spawn_egg",
                "glass", "stone", "wood", "brick", "ore", "concrete", "sand", "dirt", "grass",
                "item", "tool", "weapon", "armor", "bow", "sword", "pickaxe", "axe", "shovel", "hoe",
                "cube", "box", "plane", "simple", "basic", "block",
                "slab", "stair", "fence", "door", "gate", "lamp", "button"
            ]
            is_invalid = any(kw in aggressive_lower for kw in skip_keywords)
            if not is_invalid:
                geom_identifier = aggressive_geom
            elif is_invalid:
                skip_reason = "spawn_egg" if "spawn_egg" in aggressive_lower else "item/simple geometry"

                pass
    if aggressive_tex:
        texture_ref = aggressive_tex
    else:
        texture_hint = extract_entity_texture_hint(java_code, entity_basename)
        texture_ref  = resolve_texture_reference(namespace, texture_hint, "entity", fallback_name=entity_basename)
    if not geom_identifier:
        entity_cls = extract_class_name(java_code) or entity_basename
        if _LAYERDEF_GEO_MAP:
            if entity_cls in _LAYERDEF_GEO_MAP:
                geom_identifier = _LAYERDEF_GEO_MAP[entity_cls]
            else:
                ent_stem = re.sub(r'(?i)Entity$', '', entity_cls).lower()
                for model_cls, geo_id in _LAYERDEF_GEO_MAP.items():
                    model_stem = re.sub(r'(?i)Model$', '', model_cls).lower()
                    if ent_stem and model_stem and ent_stem == model_stem:
                        geom_identifier = geo_id

                        break
                if not geom_identifier:
                    geo_data = convert_layerdefinition_to_geckolib(
                        java_code, entity_basename, namespace, entity_name=entity_name
                    )
                    if geo_data:
                        out_path = os.path.join(RP_FOLDER, "geometry", f"{entity_basename}.geo.json")
                        try:
                            safe_write_json(out_path, geo_data)
                            geom_identifier = geo_data['minecraft:geometry'][0]['description']['identifier']

                        except Exception as _le:
                            _warn(f"[layerdef-inline] Write failed: {_le}")
        if not geom_identifier:
            geo_data = convert_layerdefinition_to_geckolib(
                java_code, entity_basename, namespace, entity_name=entity_name
            )
            if geo_data:
                out_path = os.path.join(RP_FOLDER, "geometry", f"{entity_basename}.geo.json")
                try:
                    safe_write_json(out_path, geo_data)
                    geom_identifier = geo_data['minecraft:geometry'][0]['description']['identifier']

                except Exception as _le:
                    _warn(f"[layerdef-inline] Write failed: {_le}")
    if not geom_identifier:
        geom_identifier = f"geometry.{namespace}.{entity_name}"
        stats["missing_geometry"].append((java_path, entity_basename))
        stub_geo = {
            "format_version": "1.12.0",
            "minecraft:geometry": [{
                "description": {
                    "identifier": geom_identifier,
                    "texture_width": 64,
                    "texture_height": 64,
                    "visible_bounds_width": 2,
                    "visible_bounds_height": 2,
                    "visible_bounds_offset": [0, 1, 0]
                },
                "bones": [{"name": "root", "pivot": [0, 0, 0]}]
            }]
        }
        stub_geo_path = os.path.join(RP_FOLDER, "geometry", f"{entity_basename}.geo.json")
        if not os.path.exists(stub_geo_path):
            safe_write_json(stub_geo_path, stub_geo)

    chosen_animation_key = None
    if primary_animation_key:
        candidate = canonicalize_animation_id(primary_animation_key, namespace, entity_name)
        found = False
        for keys in anim_key_map.values():
            if candidate in keys:
                chosen_animation_key = candidate
                found = True
                break
        if not found:
            for keys in anim_key_map.values():
                for k in keys:
                    if entity_basename.lower() in k.lower() or (geom_tuple and geom_tuple[1].lower() in k.lower()):
                        chosen_animation_key = k
                        found = True
                        break
                if found:
                    break
        if not found and candidate:
            chosen_animation_key = candidate
        if chosen_animation_key:
            chosen_animation_key = canonicalize_animation_id(chosen_animation_key, namespace, entity_name)
    anim_controller_id = None
    if animations:
        anim_controller_id = generate_animation_controller(
            clean_identifier, animations, namespace,
            ai_goals=ai_goals, java_code=java_code
        )
    try:
        controller_id = write_render_controller(entity_basename.lower(), namespace.lower(), geom_identifier, uv_anim=None)
    except Exception as e:
        _REAL_PRINT(f"[ERROR] write_render_controller failed: {e}")
        controller_id = f"controller.render.{namespace.lower()}.{entity_basename.lower()}"
    try:
        write_rp_entity_json(
            entity_basename.lower(),
            namespace.lower(),
            texture_ref,
            geom_identifier,
            chosen_animation_key,
            controller_id
        )
        stats["converted_entities_rp"].append(
            os.path.join(RP_FOLDER, "entity", f"{entity_basename.lower()}.entity.json")
        )
    except Exception as e:
        _REAL_PRINT(f"[ERROR] write_rp_entity_json failed: {e}")
        fallback_rp = {
            "format_version": "1.10.0",
            "minecraft:client_entity": {
                "description": {
                    "identifier": f"{namespace.lower()}:{entity_basename.lower()}",
                    "textures": {"default": "textures/entity/missing_texture"},
                    "geometry": {"default": "geometry.missing"},
                    "render_controllers": [controller_id],
                    "materials": {"default": "entity_alphatest"}
                }
            }
        }
        out_path = os.path.join(RP_FOLDER, "entity", f"{entity_basename.lower()}.entity.json")
        _safe_rp_write("fallback RP entity", out_path, fallback_rp)
    try:
        patch_rp_entity_with_controller(entity_basename.lower(), animations, anim_controller_id, namespace)
    except Exception as e:
        _REAL_PRINT(f"[ERROR] patch_rp_entity_with_controller failed: {e}")
    generate_spawn_rules(clean_identifier, java_code, namespace)
    extract_and_generate_particles(java_code, clean_identifier, namespace)
    if "TradeWithPlayerGoal" in ai_goals:
        generate_trading_table(clean_identifier, java_code, namespace)

    generate_entity_script(java_code, clean_identifier.split(":")[-1], clean_identifier, namespace)
def choose_icon_size_for(width: int, height: int) -> int:
    m = min(width, height)
    valid_under = [s for s in VALID_ICON_SIZES if s <= m]
    if valid_under:
        return max(valid_under)
    return VALID_ICON_SIZES[0]
def ensure_and_fix_pack_icon(src_path: str, dest_path: str):
    if not os.path.exists(src_path):
        _warn(f"[icon] source icon not found: {src_path}")
        return False
    if not PIL_AVAILABLE:

        os.makedirs(os.path.dirname(dest_path), exist_ok=True)
        shutil.copy(src_path, dest_path)
        return False
    try:
        with Image.open(src_path) as im:
            w, h = im.size
            side = min(w, h)
            left = (w - side) // 2
            top = (h - side) // 2
            right = left + side
            bottom = top + side
            im_cropped = im.crop((left, top, right, bottom))
            target_size = choose_icon_size_for(side, side)
            if (im_cropped.size[0], im_cropped.size[1]) != (target_size, target_size):
                im_resized = im_cropped.resize((target_size, target_size), Image.LANCZOS)
            else:
                im_resized = im_cropped
            os.makedirs(os.path.dirname(dest_path), exist_ok=True)
            im_resized.save(dest_path, format="PNG")

            return True
    except Exception as e:
        _warn(f"[icon] Failed to process icon (PIL): {e}. Copying without transform.")
        os.makedirs(os.path.dirname(dest_path), exist_ok=True)
        shutil.copy(src_path, dest_path)
        return False
def sanitize_sound_key(k: str) -> str:
    if not k:
        return ""
    s = str(k).lower()
    s = s.replace('-s', '_s')
    s = s.replace('-', '_')
    s = re.sub(r'\s+', '_', s)
    s = re.sub(r'[^a-z0-9_\.]', '_', s)
    s = re.sub(r'_+', '_', s)
    s = s.strip('._')
    return s
def _normalize_sound_name(name: str) -> str:
    name = name.split(":")[-1]
    for prefix in ("sounds/", "sound/"):
        if name.startswith(prefix):
            name = name[len(prefix):]
            break
    if "." in os.path.basename(name):
        name = name.rsplit(".", 1)[0]
    name = sanitize_sound_key(name)
    return f"sound/{name}"
def _sanitize_sound_def(v) -> dict:
    if not isinstance(v, dict):
        return v
    result = dict(v)
    if "sounds" in result and isinstance(result["sounds"], list):
        cleaned = []
        for entry in result["sounds"]:
            if isinstance(entry, str):
                cleaned.append(_normalize_sound_name(entry))
            elif isinstance(entry, dict):
                e = dict(entry)
                if "name" in e and isinstance(e["name"], str):
                    e["name"] = _normalize_sound_name(e["name"])
                cleaned.append(e)
            else:
                cleaned.append(entry)
        result["sounds"] = cleaned
    return result
def generate_sounds_registry(mod_name: str):
    global COLLECTED_SOUND_DEFS
    sounds_dir = os.path.join(RP_FOLDER, "sound")
    if os.path.isdir(sounds_dir):
        for root, _, files in os.walk(sounds_dir):
            for f in files:
                if not f.lower().endswith(".ogg"):
                    continue
                stem = os.path.splitext(f)[0]
                sanitized_key = sanitize_sound_key(stem)
                if sanitized_key not in COLLECTED_SOUND_DEFS:
                    COLLECTED_SOUND_DEFS[sanitized_key] = {"sounds": [{"name": f"sound/{sanitized_key}"}]}
    if COLLECTED_SOUND_DEFS:
        final_defs: Dict[str, dict] = {}
        collisions = 0
        for raw_k, v in COLLECTED_SOUND_DEFS.items():
            new_k = sanitize_sound_key(raw_k)
            v = _sanitize_sound_def(v)
            if new_k in final_defs:
                collisions += 1
                fallback_k = f"{sanitize_sound_key(mod_name)}.{new_k}"
                if fallback_k in final_defs:
                    i = 2
                    while f"{fallback_k}_{i}" in final_defs:
                        i += 1
                    fallback_k = f"{fallback_k}_{i}"
                final_defs[fallback_k] = v
            else:
                final_defs[new_k] = v
        out_path = os.path.join(RP_FOLDER, "sounds", "sound_definitions.json")
        safe_write_json(out_path, {
            "format_version": "1.14.0",
            "sound_definitions": final_defs
        })

    else:
        pass

    if _ENTITY_SOUND_EVENTS:
        entities_block: dict = {}
        for entity_id, entry in _ENTITY_SOUND_EVENTS.items():
            events = entry.get("events", {}) if isinstance(entry, dict) else {}
            pitch  = entry.get("pitch",  [0.8, 1.2]) if isinstance(entry, dict) else [0.8, 1.2]
            volume = entry.get("volume", 1.0) if isinstance(entry, dict) else 1.0
            events_out = {}
            for slot, sound_key in events.items():
                events_out[slot] = {"sound": sound_key, "volume": volume, "pitch": pitch}
            entities_block[entity_id] = {
                "volume": volume,
                "pitch": pitch,
                "events": events_out,
            }
        sounds_json = {"entity_sounds": {"entities": entities_block}}
        out_path = os.path.join(RP_FOLDER, "sounds.json")
        safe_write_json(out_path, sounds_json)

    else:
        pass

JAVA_MOB_EFFECT_MAP = {
    "MOVEMENT_SPEED": "speed", "MOVEMENT_SLOWDOWN": "slowness",
    "DIG_SPEED": "haste", "DIG_SLOWDOWN": "mining_fatigue",
    "DAMAGE_BOOST": "strength", "HEAL": "instant_health",
    "HARM": "instant_damage", "JUMP": "jump_boost",
    "CONFUSION": "nausea", "REGENERATION": "regeneration",
    "DAMAGE_RESISTANCE": "resistance", "FIRE_RESISTANCE": "fire_resistance",
    "WATER_BREATHING": "water_breathing", "INVISIBILITY": "invisibility",
    "BLINDNESS": "blindness", "NIGHT_VISION": "night_vision",
    "HUNGER": "hunger", "WEAKNESS": "weakness", "POISON": "poison",
    "WITHER": "wither", "HEALTH_BOOST": "health_boost",
    "ABSORPTION": "absorption", "SATURATION": "saturation",
    "GLOWING": "glowing", "LEVITATION": "levitation",
    "LUCK": "luck", "UNLUCK": "unluck", "SLOW_FALLING": "slow_falling",
    "CONDUIT_POWER": "conduit_power", "DOLPHINS_GRACE": "dolphins_grace",
    "BAD_OMEN": "bad_omen", "HERO_OF_THE_VILLAGE": "village_hero",
    "DARKNESS": "darkness",
}
def extract_mob_effects_from_java(java_code: str) -> list:
    effects = []
    for m in re.finditer(
        r'new\s+MobEffectInstance\s*\(\s*MobEffects\.([A-Z_]+)\s*,\s*(\d+)\s*(?:,\s*(\d+))?',
        java_code):
        java_name = m.group(1)
        duration_ticks = int(m.group(2))
        amplifier = int(m.group(3)) if m.group(3) else 0
        bedrock_name = JAVA_MOB_EFFECT_MAP.get(java_name)
        if bedrock_name:
            effects.append({
                "effect": bedrock_name,
                "duration": duration_ticks / 20.0,
                "amplifier": amplifier,
                "ambient": False,
                "visible": True
            })
    return effects
JAVA_SOUND_EVENT_MAP = {
    "ENTITY_GENERIC_AMBIENT":      "ambient",
    "ENTITY_GENERIC_DEATH":        "death",
    "ENTITY_GENERIC_HURT":         "hurt",
    "ENTITY_GENERIC_STEP":         "step",
    "ENTITY_GENERIC_SPLASH":       "splash",
    "ENTITY_GENERIC_SWIM":         "swim",
    "ENTITY_GENERIC_BIG_FALL":     "fall.big",
    "ENTITY_GENERIC_SMALL_FALL":   "fall.small",
    "ENTITY_GENERIC_DRINK":        "drink",
    "ENTITY_GENERIC_EAT":          "eat",
    "ENTITY_GENERIC_EXPLODE":      "explode",
    "ENTITY_GENERIC_ATTACK":       "attack",
    "ENTITY_ZOMBIE_AMBIENT":       "ambient",
    "ENTITY_ZOMBIE_DEATH":         "death",
    "ENTITY_ZOMBIE_HURT":          "hurt",
    "ENTITY_SKELETON_AMBIENT":     "ambient",
    "ENTITY_SKELETON_DEATH":       "death",
    "ENTITY_SKELETON_HURT":        "hurt",
    "ENTITY_CREEPER_PRIMED":       "ambient",
    "ENTITY_WOLF_AMBIENT":         "ambient",
    "ENTITY_WOLF_DEATH":           "death",
    "ENTITY_WOLF_HURT":            "hurt",
    "ENTITY_CAT_AMBIENT":          "ambient",
    "ENTITY_PLAYER_ATTACK_STRONG": "attack",
    "ENTITY_PLAYER_HURT":          "hurt",
    "ENTITY_PLAYER_DEATH":         "death",
    "ENTITY_ENDERMAN_AMBIENT":     "ambient",
    "ENTITY_ENDERMAN_DEATH":       "death",
    "ENTITY_ENDERMAN_HURT":        "hurt",
    "ENTITY_ENDERMAN_STARE":       "ambient.stare",
    "ENTITY_WARDEN_AMBIENT":       "ambient",
    "ENTITY_WARDEN_DEATH":         "death",
    "ENTITY_WARDEN_HURT":          "hurt",
    "ENTITY_WARDEN_ROAR":          "roar",
}
JAVA_SOUND_METHOD_MAP = {
    "getAmbientSound":  "ambient",
    "ambientSound":     "ambient",
    "getDeathSound":    "death",
    "deathSound":       "death",
    "getHurtSound":     "hurt",
    "hurtSound":        "hurt",
    "getStepSound":     "step",
    "stepSound":        "step",
    "getSwimSound":     "swim",
    "swimSound":        "swim",
    "getSplashSound":   "splash",
    "splashSound":      "splash",
    "getAttackSound":   "attack",
    "attackSound":      "attack",
}
def _best_sound_key(raw_id: str, namespace: str) -> str:
    raw_id = raw_id.strip().strip('"').strip("'")
    if ":" in raw_id:
        raw_id = raw_id.split(":", 1)[1]
    return sanitize_sound_key(raw_id)
def extract_entity_sounds_from_java(java_code: str, entity_name: str, namespace: str) -> dict:
    sounds = {}
    for method, slot in JAVA_SOUND_METHOD_MAP.items():
        pat = rf'{method}\s*\([^)]*\)\s*\{{[^}}]*?(?:return\s+)?(?:SoundEvents\.|ModSounds\.|Sounds\.)([A-Z0-9_]+)'
        m = re.search(pat, java_code, re.DOTALL)
        if m and slot not in sounds:
            java_const = m.group(1)
            bedrock_slot = JAVA_SOUND_EVENT_MAP.get(java_const)
            if bedrock_slot:
                sounds[slot] = f"{namespace}.{entity_name}.{slot}"
            else:
                sounds[slot] = sanitize_sound_key(java_const.lower())
    for method, slot in JAVA_SOUND_METHOD_MAP.items():
        if slot in sounds:
            continue
        pat = rf'{method}\s*\([^)]*\)\s*\{{[^}}]*?return\s+([A-Za-z_][A-Za-z0-9_.]*(?:\.get\(\))?)'
        m = re.search(pat, java_code, re.DOTALL)
        if m:
            ref = m.group(1).rstrip(")").rstrip("(").rstrip(".get")
            ref_lower = sanitize_sound_key(ref.split(".")[-1])
            if len(ref_lower) > 2 and ref_lower not in ("null", "super", "this"):
                sounds[slot] = f"{namespace}.{ref_lower}"
    PLAY_SLOT_HINTS = {
        "ambient": ("ambient", "idle", "random"),
        "hurt":    ("hurt", "pain", "damage"),
        "death":   ("death", "die"),
        "attack":  ("attack", "strike", "hit"),
        "step":    ("step", "footstep", "walk"),
    }
    for m in re.finditer(
        r'playSound\s*\([^,)]*,\s*(?:SoundEvents\.|ModSounds\.|Sounds\.)([A-Z0-9_]+)',
        java_code
    ):
        java_const = m.group(1)
        for slot, hints in PLAY_SLOT_HINTS.items():
            if slot in sounds:
                continue
            if any(h in java_const.lower() for h in hints):
                sounds[slot] = f"{namespace}.{entity_name}.{slot}"
                break
    for m in re.finditer(
        r'new\s+ResourceLocation\s*\(\s*["\']([^"\']+)["\']\s*,\s*["\']([^"\']+)["\']',
        java_code
    ):
        sound_path = m.group(2).lower()
        for slot in ("ambient", "death", "hurt", "step", "attack", "swim", "splash"):
            if slot in sounds:
                continue
            if slot in sound_path:
                key = sanitize_sound_key(f"{m.group(1)}.{sound_path}")
                sounds[slot] = key
                break
    for m in re.finditer(
        r'["\']([a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*){2,})["\']',
        java_code
    ):
        path = m.group(1)
        parts = path.split(".")
        if len(parts) < 2:
            continue
        for slot in ("ambient", "death", "hurt", "step", "attack", "swim", "splash"):
            if slot in sounds:
                continue
            if parts[-1] == slot or (len(parts) >= 2 and parts[-2] == slot):
                sounds[slot] = sanitize_sound_key(path)
                break
    return sounds
