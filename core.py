from __future__ import annotations

import os
import json
import uuid
import zipfile
import shutil
import builtins
import sys
import math
import subprocess
import re
import copy
from typing import Optional, Tuple, Dict, Set, List, Union

DEBUG_MODE = 1

_REAL_PRINT = builtins.print
if not DEBUG_MODE:
    builtins.print = lambda *a, **kw: None

class _SilentStream:
    def write(self, s): return len(s)
    def flush(self): return None

if not DEBUG_MODE:
    sys.stderr = _SilentStream()

Tool_Version = "1.6.1.3 'Blocks, AGAIN, AGAIN!'"
DEBUG_MODE = os.environ.get('MODMORPHER_DEBUG', '0') == '1'
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
        line = f"\r  {label:<40} {bar_filled}{bar_empty}  {percent:3d}%  {count}{suffix_part}"

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
        return self._original_print(*args, **kwargs)

    def flush(self):
        try:
            return self._original_print.flush()
        except Exception:
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
_warn = lambda *a: None

def _safe_rp_write(desc, path, data):
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        if DEBUG_MODE:
            _REAL_PRINT(f"[RP] wrote {desc}: {path}")
    except Exception as e:
        _REAL_PRINT(f"[ERROR] Failed to write {desc} to {path}: {e}")

_ALL_JAVA_FILES: Dict[str, str] = {}
_DEOBFUSCATED_JAVA_FILES: Dict[str, str] = {}
_DEOBFUSCATED_JAVA_PATHS: Dict[str, str] = {}
_RP_ASSET_INDEX: Dict[str, Union[list, dict]] = {
    "textures": [],
    "geometry": [],
    "flipbook_textures": {}
}
try:
    from PIL import Image
    PIL_AVAILABLE = True
except Exception:
    try:
        subprocess.check_call([
            sys.executable,
            "-m", "pip", "install", "pillow"
        ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        from PIL import Image
        PIL_AVAILABLE = True
    except Exception:
        PIL_AVAILABLE = False
JAVALANG_AVAILABLE = False
try:
    import javalang
    JAVALANG_AVAILABLE = True
except ImportError:
    try:
        subprocess.check_call([sys.executable, "-m", "pip", "install", "javalang"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        import javalang
        JAVALANG_AVAILABLE = True
    except Exception:
        JAVALANG_AVAILABLE = False
def translate_expression(expr: object, symbol_table=None) -> Optional[str]:
    if expr is None:
        return None
    if isinstance(expr, str):
        return expr
    if not JAVALANG_AVAILABLE:
        if hasattr(expr, 'value'):
            return str(expr.value)
        return str(expr)
    if isinstance(expr, javalang.tree.Literal):
        return str(expr.value)
    elif isinstance(expr, javalang.tree.MemberReference):
        qualifier = f'{expr.qualifier}.' if getattr(expr, 'qualifier', None) else ''
        return f'{qualifier}{expr.member}'
    elif isinstance(expr, javalang.tree.MethodInvocation):
        member = getattr(expr, 'member', '')
        args = getattr(expr, 'arguments', [])
        arg_strs = [translate_expression(a, symbol_table) for a in args]
        arg_strs = [a for a in arg_strs if a is not None]
        qual = getattr(expr, 'qualifier', None)
        if qual and symbol_table is not None:
            try:
                resolved = symbol_table.resolve_method_call(str(qual), member, arg_strs)
                if resolved:
                    return resolved
            except Exception:
                pass
        qual_prefix = f'{qual}.' if qual else ''
        return f'{qual_prefix}{member}({", ".join(arg_strs)})'
    elif isinstance(expr, javalang.tree.BinaryOperation):
        left = translate_expression(expr.operandl, symbol_table)
        right = translate_expression(expr.operandr, symbol_table)
        if left is not None and right is not None:
            return f'({left} {expr.operator} {right})'
    elif isinstance(expr, javalang.tree.Cast):
        return translate_expression(expr.expression, symbol_table)
    elif isinstance(expr, javalang.tree.TernaryExpression):
        cond = translate_expression(expr.condition, symbol_table)
        t = translate_expression(expr.if_true, symbol_table)
        f = translate_expression(expr.if_false, symbol_table)
        if cond and t and f:
            return f'({cond} ? {t} : {f})'
    elif isinstance(expr, javalang.tree.This):
        return 'this'
    elif isinstance(expr, javalang.tree.SuperMethodInvocation):
        args = [translate_expression(a, symbol_table) for a in (expr.arguments or [])]
        args = [a for a in args if a is not None]
        return f'super.{expr.member}({", ".join(args)})'
    if hasattr(expr, 'value'):
        return str(expr.value)
    return str(expr)

def detect_tick_method(java_code: str) -> Optional[Tuple[str, str]]:
    if not JAVALANG_AVAILABLE:
        return None
    try:
        tree = javalang.parse.parse(java_code)
        for _, node in tree:
            if isinstance(node, javalang.tree.MethodDeclaration):
                if node.name in ('onTick', 'tick', 'serverTick', 'update', 'doUpdate'):

                    method_body = []
                    for stmt in node.body:
                        method_body.extend(translate_statement(stmt, 'this', 'namespace'))
                    return (node.name, '\n'.join(method_body))
    except Exception:
        pass
    return None

def generate_tick_handler_js(namespace: str, entity_id: str, tick_logic: str) -> list:
    lines = [
        f"world.afterEvents.entitySpawn.subscribe((e) => {{",
        f"    if (!e.entity.typeId.includes('{namespace}:{entity_id}')) return;",
        f"    const tick_id = setInterval(() => {{",
        f"        {tick_logic}",
        f"    }}, 50); // Bedrock tick = ~50ms in scripting API",
        f"    e.entity.onTickEventCalls = (e.entity.onTickEventCalls || 0) + 1;",
        f"}});",
    ]
    return lines
class MoLangBridge:
    _FUNC_MAP = [
        (r'Math\.sin\(', 'math.sin('),
        (r'Math\.cos\(', 'math.cos('),
        (r'Math\.tan\(', 'math.tan('),
        (r'Math\.asin\(', 'math.asin('),
        (r'Math\.acos\(', 'math.acos('),
        (r'Math\.atan2?\(', 'math.atan('),
        (r'Math\.sqrt\(', 'math.sqrt('),
        (r'Math\.abs\(', 'math.abs('),
        (r'Math\.floor\(', 'math.floor('),
        (r'Math\.ceil\(', 'math.ceil('),
        (r'Math\.round\(', 'math.round('),
        (r'Math\.min\(', 'math.min('),
        (r'Math\.max\(', 'math.max('),
        (r'Math\.clamp\(', 'math.clamp('),
        (r'Math\.pow\(([^,]+),\s*2\)', r'(\1 * \1)'),
        (r'Math\.pow\(([^,]+),\s*([^)]+)\)', r'math.pow(\1, \2)'),
        (r'Math\.PI', '3.14159265'),
        (r'Math\.toRadians\(([^)]+)\)', r'(\1 * 0.01745329)'),
        (r'Math\.toDegrees\(([^)]+)\)', r'(\1 * 57.2957795)'),
        (r'Math\.random\(\)', 'math.random(0, 1)'),
        (r'Math\.lerp\(([^,]+),\s*([^,]+),\s*([^)]+)\)', r'math.lerp(\1, \2, \3)'),
    ]

    _VAR_MAP = [
        (r'\bentity\.tickCount\b',           'query.anim_time * 20'),
        (r'\bthis\.tickCount\b',              'query.anim_time * 20'),
        (r'\btickCount\b',                     'query.anim_time * 20'),
        (r'\banimationTick\b',                 'query.anim_time * 20'),
        (r'\bpartialTick\b',                   'query.anim_time'),
        (r'\bentity\.isInWater\(\)\b',     'query.is_in_water'),
        (r'\bentity\.isOnGround\(\)\b',    'query.is_on_ground'),
        (r'\bentity\.isSprinting\(\)\b',   'query.is_sprinting'),
        (r'\bentity\.isSneaking\(\)\b',    'query.is_sneaking'),
        (r'\bentity\.isSwimming\(\)\b',    'query.is_swimming'),
        (r'\bentity\.isBaby\(\)\b',        'query.is_baby'),
        (r'\bentity\.isOnFire\(\)\b',      'query.is_on_fire'),
        (r'\bentity\.getHealth\(\)',         'query.health'),
        (r'\bentity\.getSpeed\(\)',          'query.ground_speed'),
        (r'\bentity\.xRot\b',                'query.body_x_rotation'),
        (r'\bentity\.yRot\b',                'query.body_y_rotation'),
        (r'\bthis\.(\w+)\b',                r'variable.\1'),
    ]

    _TERNARY_RE = re.compile(
        r'([^?]+)\?\s*([^:]+):\s*(.+)'
    )

    @staticmethod
    def java_to_molang(java_expr: str) -> str:
        m = java_expr.strip()

        m = re.sub(r'\((?:float|double|int|long)\)\s*', '', m)

        m = re.sub(r'^return\s+', '', m.strip())
        m = m.rstrip(';')

        tern = MoLangBridge._TERNARY_RE.match(m)
        if tern:
            cond = MoLangBridge.java_to_molang(tern.group(1).strip())
            t_val = MoLangBridge.java_to_molang(tern.group(2).strip())
            f_val = MoLangBridge.java_to_molang(tern.group(3).strip())
            return f'({cond} ? {t_val} : {f_val})'

        for pattern, replacement in MoLangBridge._FUNC_MAP:
            m = re.sub(pattern, replacement, m)

        for pattern, replacement in MoLangBridge._VAR_MAP:
            m = re.sub(pattern, replacement, m)

        m = re.sub(r'(\d+\.?\d*)f\b', r'\1', m)

        while '((' in m and '))' in m:
            m = re.sub(r'\(\(([^()]+)\)\)', r'(\1)', m)
        return m.strip()

    @staticmethod
    def build_animation_json_entry(bone: str, channel: str,
                                   java_expr: str, namespace: str) -> dict:
        molang = MoLangBridge.java_to_molang(java_expr)
        entry = {
            "0.0": {
                channel: [molang, "0.0", "0.0"]
                if channel != 'rotation' else [molang, "0.0", "0.0"]
            }
        }
        return entry

    @staticmethod
    def inject_molang_into_anim_file(anim_path: str, entity_name: str,
                                     bone_channel_map: Dict[str, Dict[str, str]]) -> None:
        if not os.path.exists(anim_path):
            return
        try:
            with open(anim_path, 'r', encoding='utf-8') as fh:
                data = json.load(fh)
        except Exception:
            return
        animations = data.get('animations', {})
        for anim_id, anim_body in animations.items():
            if entity_name not in anim_id:
                continue
            bones = anim_body.setdefault('bones', {})
            for bone, channels in bone_channel_map.items():
                bone_entry = bones.setdefault(bone, {})
                for ch, java_expr in channels.items():
                    molang = MoLangBridge.java_to_molang(java_expr)
                    bone_entry[ch] = molang
        with open(anim_path, 'w', encoding='utf-8') as fh:
            json.dump(data, fh, indent=2)

class AnimationControllerGenerator:
    @staticmethod
    def generate_default_controller(entity_name: str, animations: Dict[str, str]) -> dict:
        controller_name = f"controller.animation.{entity_name}.default"
        states = {
            "default": {
                "animations": list(animations.keys())[:1],
            }
        }

        for anim_name in list(animations.keys())[1:]:
            states[anim_name] = {
                "animations": [anim_name],
                "transitions": [{"default": "!variable.playing_" + anim_name}]
            }

        return {
            "format_version": "1.10.0",
            "animation_controllers": {
                controller_name: {
                    "states": states
                }
            }
        }

class NBTTranslator:
    NBT_TO_BEDROCK_MAP = {
        'readAdditionalSaveData': 'getDynamicProperty',
        'addAdditionalSaveData':  'setDynamicProperty',
        'getInt':    'getDynamicProperty', 'putInt':    'setDynamicProperty',
        'getString': 'getDynamicProperty', 'putString': 'setDynamicProperty',
        'getFloat':  'getDynamicProperty', 'putFloat':  'setDynamicProperty',
        'getBoolean':'getDynamicProperty', 'putBoolean':'setDynamicProperty',
        'getDouble': 'getDynamicProperty', 'putDouble': 'setDynamicProperty',
        'getLong':   'getDynamicProperty', 'putLong':   'setDynamicProperty',
        'getByte':   'getDynamicProperty', 'putByte':   'setDynamicProperty',
        'getList':   'getDynamicProperty', 'put':       'setDynamicProperty',
        'getCompound': 'getDynamicProperty',
    }

    _TYPE_HINTS = {
        'getInt': 'number', 'getFloat': 'number', 'getDouble': 'number',
        'getLong': 'number', 'getByte': 'number', 'getShort': 'number',
        'getString': 'string', 'getBoolean': 'boolean',
        'getList': 'array',   'getCompound': 'object',
    }

    @staticmethod
    def translate_nbt_call(method: str, args, namespace: str,
                           entity_var: str = 'entity') -> Optional[str]:
        bedrock = NBTTranslator.NBT_TO_BEDROCK_MAP.get(method)
        if bedrock is None:
            return None
        if not args:
            return None

        key_arg = None
        if JAVALANG_AVAILABLE:
            first = args[0]
            if isinstance(first, javalang.tree.Literal) and first.value.startswith('"'):
                key_arg = first.value.strip('"')
        if key_arg is None:

            m = re.search(r'"([^"]+)"', str(args))
            key_arg = m.group(1) if m else 'unknown'
        prop_key = f'"{namespace}:{key_arg}"'
        type_hint = NBTTranslator._TYPE_HINTS.get(method, 'any')
        default = {'number': 0, 'string': '""""', 'boolean': 'false',
                   'array': '[]', 'object': '{}'}.get(type_hint, 'null')
        if method.startswith('get') or method == 'readAdditionalSaveData':
            return f'    ({entity_var}.getDynamicProperty({prop_key}) ?? {default})'
        elif method.startswith('put') or method == 'addAdditionalSaveData':
            val_arg = 'value'
            if len(args) > 1 and JAVALANG_AVAILABLE:
                lit = args[1]
                if isinstance(lit, javalang.tree.Literal):
                    val_arg = lit.value
            return f'    {entity_var}.setDynamicProperty({prop_key}, {val_arg});'
        return None

class RecursiveNBTSerializer:
    _MAX_VALUE_LEN = 32_000

    @staticmethod
    def flatten(nbt: dict, prefix: str = '', depth: int = 0,
                _out: Optional[list] = None) -> list:
        if _out is None:
            _out = []
        if depth > 16:

            _out.append((prefix, json.dumps(nbt)[:RecursiveNBTSerializer._MAX_VALUE_LEN]))
            return _out
        for k, v in nbt.items():
            path = f'{prefix}.{k}' if prefix else k
            if isinstance(v, dict):
                RecursiveNBTSerializer.flatten(v, path, depth + 1, _out)
            elif isinstance(v, list):
                for i, item in enumerate(v):
                    item_path = f'{path}.{i}'
                    if isinstance(item, dict):
                        RecursiveNBTSerializer.flatten(item, item_path, depth + 1, _out)
                    else:
                        _out.append((item_path, item))
            else:

                _out.append((path, v))
        return _out

    @staticmethod
    def emit_set_js(nbt: dict, namespace: str, entity_var: str = 'entity') -> list:
        lines = [f'// NBT serialization — {len(nbt)} top-level keys']
        pairs = RecursiveNBTSerializer.flatten(nbt)
        for dot_path, value in pairs:
            prop_key = f'{namespace}:{dot_path}'
            if isinstance(value, str):
                js_val = json.dumps(value)
            elif isinstance(value, bool):
                js_val = 'true' if value else 'false'
            elif value is None:
                js_val = 'null'
            else:
                js_val = str(value)
            if len(js_val) > RecursiveNBTSerializer._MAX_VALUE_LEN:
                js_val = json.dumps(js_val[:RecursiveNBTSerializer._MAX_VALUE_LEN])
            lines.append(f'{entity_var}.setDynamicProperty("{prop_key}", {js_val});')
        return lines

    @staticmethod
    def reconstruct_js(dot_paths: list, namespace: str,
                       entity_var: str = 'entity',
                       out_var: str = 'nbt') -> list:
        lines = [
            f'const {out_var} = {{}};',
            f'const _set = (obj, path, val) => {{',
            f'    const parts = path.split(".");',
            f'    let cur = obj;',
            f'    for (let i = 0; i < parts.length - 1; i++) {{',
            f'        const k = isNaN(parts[i]) ? parts[i] : +parts[i];',
            f'        if (cur[k] === undefined) cur[k] = isNaN(parts[i+1]) ? {{}} : [];',
            f'        cur = cur[k];',
            f'    }}',
            f'    cur[parts[parts.length-1]] = val;',
            f'}};',
        ]
        for path in dot_paths:
            prop_key = f'{namespace}:{path}'
            lines.append(
                f'_set({out_var}, "{path}", {entity_var}.getDynamicProperty("{prop_key}"));')
        lines.append(f'// {out_var} is now the reconstructed nested object')
        return lines

    @staticmethod
    def scan_and_emit_nbt_scripts(java_code: str, entity_id: str,
                                   namespace: str, bp_folder: str) -> None:
        safe = sanitize_identifier(entity_id.split(':')[-1])
        write_calls: list = []
        read_calls:  list = []

        save_body = _extract_method_body(java_code, 'addAdditionalSaveData')
        if save_body:
            for m in re.finditer(
                r'(?:tag|nbt|compound)\.(put\w+)\s*\(\s*"([^"]+)"\s*,\s*([^;)]+)',
                save_body
            ):
                method, key, val = m.group(1), m.group(2), m.group(3).strip()
                prop = f'{namespace}:{safe}.{key}'
                write_calls.append(f'entity.setDynamicProperty("{prop}", {val});')

        load_body = _extract_method_body(java_code, 'readAdditionalSaveData')
        if load_body:
            for m in re.finditer(
                r'(?:tag|nbt|compound)\.(get\w+)\s*\(\s*"([^"]+)"\)',
                load_body
            ):
                method, key = m.group(1), m.group(2)
                prop = f'{namespace}:{safe}.{key}'
                default = {'getInt':'0','getFloat':'0','getDouble':'0',
                           'getString':'\'\'','getBoolean':'false','getLong':'0'}.get(method, 'null')
                read_calls.append(
                    f'const {key} = entity.getDynamicProperty("{prop}") ?? {default};')

        if not write_calls and not read_calls:
            return

        lines = [
            f'import {{ world }} from "@minecraft/server";',
            '',
            f'// Auto-generated NBT serializer for {entity_id}',
            f'export function saveNBT_{safe}(entity) {{',
        ] + [f'    {l}' for l in write_calls] + [
            '}',
            '',
            f'export function loadNBT_{safe}(entity) {{',
        ] + [f'    {l}' for l in read_calls] + [
            '}',
            '',
        ]
        out_path = os.path.join(bp_folder, 'scripts', f'{safe}_nbt.js')
        os.makedirs(os.path.dirname(out_path), exist_ok=True)
        with open(out_path, 'w', encoding='utf-8') as fh:
            fh.write('\n'.join(lines))

class CapabilityRegistry:
    CAPABILITY_TYPES = {
        'IEnergyStorage': {
            'properties': ['energy_stored', 'max_energy'],
            'methods': ['receiveEnergy', 'extractEnergy', 'getEnergyStored', 'getMaxEnergyStored'],
            'bedrock_type': 'number',
        },
        'IFluidHandler': {
            'properties': ['fluid_amount', 'fluid_type', 'fluid_capacity'],
            'methods': ['fill', 'drain', 'getFluidAmount', 'getTankCapacity'],
            'bedrock_type': 'compound',
        },
        'IItemHandler': {
            'properties': ['slot_contents', 'slot_count'],
            'methods': ['insertItem', 'extractItem', 'getStackInSlot', 'getSlots'],
            'bedrock_type': 'itemstack',
        },
    }

    @staticmethod
    def generate_capability_manager(capability_type: str, namespace: str, entity_id: str) -> list:
        lines = [
            'import { ItemStack } from "@minecraft/server";',
            "",
        ]

        if capability_type not in CapabilityRegistry.CAPABILITY_TYPES:
            return lines

        cap = CapabilityRegistry.CAPABILITY_TYPES[capability_type]

        lines.append(f"// {capability_type} Manager for {entity_id}")
        lines.append(f"const {entity_id}_capabilities = {{")

        for prop in cap['properties']:
            lines.append(f"    {prop}: 0,")

        lines.append(f"}};")
        lines.append("")

        for method in cap['methods']:
            if method.startswith('get'):
                prop_name = method[3:].lower()
                lines.append(f"function {entity_id}_{method}(entity) {{")
                lines.append(f'    return entity.getDynamicProperty("{namespace}:{entity_id}_{prop_name}") || 0;')
                lines.append(f"}}")
            elif method in ['receiveEnergy', 'fill']:
                lines.append(f"function {entity_id}_{method}(entity, amount) {{")
                lines.append(f'    const current = entity.getDynamicProperty("{namespace}:{entity_id}_stored") || 0;')
                lines.append(f'    const max = entity.getDynamicProperty("{namespace}:{entity_id}_max") || 1000;')
                lines.append(f"    const accepted = Math.min(amount, max - current);")
                lines.append(f'    entity.setDynamicProperty("{namespace}:{entity_id}_stored", current + accepted);')
                lines.append(f"    return accepted;")
                lines.append(f"}}")
            elif method in ['extractEnergy', 'drain']:
                lines.append(f"function {entity_id}_{method}(entity, amount) {{")
                lines.append(f'    const current = entity.getDynamicProperty("{namespace}:{entity_id}_stored") || 0;')
                lines.append(f"    const extracted = Math.min(amount, current);")
                lines.append(f'    entity.setDynamicProperty("{namespace}:{entity_id}_stored", current - extracted);')
                lines.append(f"    return extracted;")
                lines.append(f"}}")

        lines.append("")
        return lines

class EventRouter:
    FORGE_TO_BEDROCK: Dict[str, tuple] = {

        'LivingHurtEvent':                      ('entityHurt',                 'entity',  False),
        'LivingDamageEvent':                    ('entityHurt',                 'entity',  False),
        'LivingDeathEvent':                     ('entityDie',                  'entity',  False),
        'LivingKnockBackEvent':                 ('entityHurt',                 'entity',  True),

        'PlayerInteractEvent.RightClickEntity': ('playerInteractWithEntity',   'player',  True),
        'PlayerInteractEvent.RightClickBlock':  ('playerPlaceBlock',           'player',  True),
        'PlayerInteractEvent.LeftClickBlock':   ('playerBreakBlock',           'player',  True),
        'PlayerInteractEvent.LeftClickEmpty':   ('playerBreakBlock',           'player',  False),

        'BlockEvent.BreakEvent':                ('playerBreakBlock',           'player',  True),
        'BlockEvent.PlaceEvent':                ('playerPlaceBlock',           'player',  True),
        'BlockEvent.EntityPlaceEvent':          ('playerPlaceBlock',           'entity',  True),
        'BlockEvent.EntityMultiPlaceEvent':     ('playerPlaceBlock',           'entity',  True),

        'ItemPickupEvent':                      ('entityPickUpItem',           'entity',  False),
        'ItemTossEvent':                        ('entityDropItem',             'entity',  True),
        'AnvilRepairEvent':                     ('playerInteractWithEntity',   'player',  False),
        'PlayerDestroyItemEvent':               ('playerBreakBlock',           'player',  False),

        'EntityJoinWorldEvent':                 ('entitySpawn',                'entity',  False),
        'EntityJoinLevelEvent':                 ('entitySpawn',                'entity',  False),
        'EntityLeaveWorldEvent':                ('entityRemove',               'entity',  False),
        'EntityLeaveLevelEvent':                ('entityRemove',               'entity',  False),
        'EntityTeleportEvent':                  ('entityHitBlock',             'entity',  True),
        'EntityMountEvent':                     ('playerInteractWithEntity',   'player',  True),

        'PlayerLoggedInEvent':                  ('playerJoin',                 'player',  False),
        'PlayerLoggedOutEvent':                 ('playerLeave',                'player',  False),
        'PlayerChangedDimensionEvent':          ('playerDimensionChange',      'player',  False),
        'PlayerRespawnEvent':                   ('playerSpawn',                'player',  False),

        'TickEvent.ServerTickEvent':            ('worldInitialize',            None,      False),
        'TickEvent.ClientTickEvent':            ('tick',                       None,      False),
        'TickEvent.LevelTickEvent':             ('worldInitialize',            None,      False),
        'LevelTickEvent':                       ('worldInitialize',            None,      False),

        'ProjectileImpactEvent':                ('projectileHitBlock',         'entity',  False),
        'ArrowNockEvent':                       ('playerInteractWithEntity',   'player',  True),

        'ServerChatEvent':                      ('chatSend',                   'player',  True),
        'CommandEvent':                         ('chatSend',                   'player',  False),

        'ExplosionEvent.Detonate':              ('explosion',                  None,      False),
        'FillBucketEvent':                      ('playerInteractWithBlock',    'player',  True),
    }

    _CANCEL_PATTERN = re.compile(
        r'event\.setCanceled\s*\(\s*true\s*\)|event\.isCanceled\s*\(\s*\)'
    )

    @staticmethod
    def generate_event_wrapper(forge_event: str, java_logic: str,
                                namespace: str, symbol_table=None) -> list:
        lines: list = []
        mapping = EventRouter.FORGE_TO_BEDROCK.get(forge_event)
        if mapping is None:

            safe = re.sub(r'[^\w]', '_', forge_event).lower()
            lines.append(f'// TODO: No Bedrock equivalent for Forge event: {forge_event}')
            lines.append(f'// Original Java handler body preserved below as reference:')
            for line in java_logic.splitlines():
                lines.append(f'//   {line}')
            lines.append('')
            return lines

        bedrock_event, entity_param, use_before = mapping
        bus = 'beforeEvents' if use_before else 'afterEvents'

        if EventRouter._CANCEL_PATTERN.search(java_logic):
            bus = 'beforeEvents'

        if bedrock_event == 'worldInitialize':
            lines.append(f'world.afterEvents.worldInitialize.subscribe((e) => {{')
        else:
            lines.append(f'world.{bus}.{bedrock_event}.subscribe((e) => {{')
            if entity_param:
                lines.append(f'    const {entity_param} = e.{entity_param};')

        translated = re.sub(
            r'event\.setCanceled\s*\(\s*true\s*\)',
            'e.cancel()',
            java_logic
        )
        lines.append(translated)
        lines.append('});')
        lines.append('')
        return lines

    @staticmethod
    def scan_and_emit_all_handlers(java_code: str, namespace: str,
                                    safe_name: str, symbol_table=None) -> list:
        all_lines: list = []

        handler_re = re.compile(
            r'@SubscribeEvent[\s\S]*?public\s+\w+\s+(\w+)\s*\(\s*(\w+)(?:\.[\w.]+)?\s+\w+\s*\)',
            re.MULTILINE
        )
        for m in handler_re.finditer(java_code):
            method_name = m.group(1)
            event_type  = m.group(2)

            body_start = java_code.find('{', m.end())
            if body_start == -1:
                continue
            depth, i = 0, body_start
            while i < len(java_code):
                if java_code[i] == '{'  : depth += 1
                elif java_code[i] == '}':
                    depth -= 1
                    if depth == 0:
                        break
                i += 1
            body = java_code[body_start + 1: i].strip()
            all_lines += EventRouter.generate_event_wrapper(
                event_type, body, namespace, symbol_table
            )
        return all_lines

class MathTranspiler:
    @staticmethod
    def transpile_vector_op(java_expr: str) -> str:

        bedrock = re.sub(r'new Vector3d\(([^,]+),\s*([^,]+),\s*([^)]+)\)', r'new Vector3(\1, \2, \3)', java_expr)

        bedrock = re.sub(r'(\w+)\.add\((\w+)\)', r'{ x: \1.x + \2.x, y: \1.y + \2.y, z: \1.z + \2.z }', bedrock)

        bedrock = re.sub(r'(\w+)\.subtract\((\w+)\)', r'{ x: \1.x - \2.x, y: \1.y - \2.y, z: \1.z - \2.z }', bedrock)

        bedrock = re.sub(r'(\w+)\.scale\(([^)]+)\)', r'{ x: \1.x * \2, y: \1.y * \2, z: \1.z * \2 }', bedrock)

        bedrock = re.sub(r'\.getX\(\)', '.x', bedrock)
        bedrock = re.sub(r'\.getY\(\)', '.y', bedrock)
        bedrock = re.sub(r'\.getZ\(\)', '.z', bedrock)

        bedrock = re.sub(r'new AxisAlignedBB\(([^,]+),\s*([^,]+),\s*([^,]+),\s*([^,]+),\s*([^,]+),\s*([^)]+)\)',
                         r'new BlockVolume({ min: { x: \1, y: \2, z: \3 }, max: { x: \4, y: \5, z: \6 } })', bedrock)
        return bedrock

    @staticmethod
    def transpile_math_expr(java_expr: str) -> str:
        bedrock = java_expr.replace('Math.PI', 'Math.PI')
        bedrock = bedrock.replace('Math.sqrt', 'Math.sqrt')
        bedrock = bedrock.replace('Math.pow', 'Math.pow')
        bedrock = bedrock.replace('Math.random', 'Math.random')
        bedrock = re.sub(r'Math\.toRadians\(([^)]+)\)', r'(\1) * Math.PI / 180', bedrock)
        bedrock = re.sub(r'Math\.toDegrees\(([^)]+)\)', r'(\1) * 180 / Math.PI', bedrock)
        return bedrock

class JavaToBedrockMethodMap:
    STRICT_MAPPING = {

        'world.setBlockState': 'dimension.getBlock({0}).setPermutation({1})',
        'world.getBlockState': 'dimension.getBlock({0}).permutation',
        'world.getBlockEntity': 'dimension.getBlock({0})',
        'level.setBlockState': 'dimension.getBlock({0}).setPermutation({1})',

        'entity.getHealth': 'entity.getComponent("health").currentValue',
        'entity.setHealth': 'entity.getComponent("health").setCurrentValue({0})',
        'entity.getMaxHealth': 'entity.getComponent("health").maxValue',
        'entity.getPosition': 'entity.location',
        'entity.setPosition': 'entity.teleport({0})',
        'entity.getVelocity': 'entity.velocity',
        'entity.setVelocity': 'entity.applyImpulse({0})',
        'entity.kill': 'entity.kill()',

        'player.sendMessage': 'player.sendMessage({0})',
        'player.getInventory': 'player.getComponent("minecraft:inventory")',
        'player.addItem': 'player.getComponent("minecraft:inventory").container.addItem({0})',
        'player.removeItem': 'player.getComponent("minecraft:inventory").container.removeItem({0})',

        'itemStack.getTag': 'itemStack.getComponent("minecraft:enchantable")',
        'compoundTag.getInt': 'entity.getDynamicProperty({0}) || 0',
        'compoundTag.putInt': 'entity.setDynamicProperty({0}, {1})',

        'new ItemStack': 'new ItemStack({0})',
        'itemStack.getCount': 'itemStack.amount',
        'itemStack.setCount': 'itemStack.amount = {0}',

        'world.addParticle': 'dimension.spawnParticle({0}, {1})',
        'world.playSound': 'dimension.playSound({0}, {1})',
    }

    @staticmethod
    def lookup_method(java_method: str) -> Optional[str]:
        bedrock = JavaToBedrockMethodMap.STRICT_MAPPING.get(java_method)
        if bedrock is None:
            log_critical_failure(f"No Bedrock equivalent for Java method: {java_method}")
        return bedrock

    @staticmethod
    def translate_method_call(java_method: str, args: list, qualifier: Optional[str] = None) -> Optional[str]:
        template = JavaToBedrockMethodMap.lookup_method(java_method)
        if not template:
            return None

        translated_args = [translate_expression(arg) for arg in args]

        for i, arg in enumerate(translated_args):
            template = template.replace(f'{{{i}}}', arg)

        return template

class TickRegistry:
    def __init__(self):
        self.tick_handlers: Dict[str, list] = {}
        self.tick_priority: Dict[str, int] = {}

    def register_tick_handler(self, entity_id: str, logic: str, priority: int = 100):
        if entity_id not in self.tick_handlers:
            self.tick_handlers[entity_id] = []
        self.tick_handlers[entity_id].append(logic)
        self.tick_priority[entity_id] = priority

    def generate_central_tick_loop(self, namespace: str) -> list:
        lines = [
            'import { world, system } from "@minecraft/server";',
            "",
            "// Central Tick Registry - Prevents Script Watchdog Timeout",
            "const tick_registry = {",
            "    handlers: {},",
            "    max_ms_per_tick: 10, // 10ms max per tick",
            "};",
            "",
            "system.runInterval(() => {",
            "    const start_time = Date.now();",
            "    const active_entities = world.getDimension('minecraft:overworld').getEntities({",
            "        tags: ['mod:needs_tick']",
            "    });",
            "",
            "    for (const entity of active_entities) {",
            "        if (Date.now() - start_time > tick_registry.max_ms_per_tick) break;",
            "        const handler_id = entity.typeId;",
            "        if (tick_registry.handlers[handler_id]) {",
            "            try {",
            "                tick_registry.handlers[handler_id](entity);",
            "            } catch (e) {",
            "                console.error(`Tick error for ${handler_id}: ${e.message}`);",
            "            }",
            "        }",
            "    }",
            "}, 1); // Run every game tick",
            "",
        ]

        for entity_id, handlers in self.tick_handlers.items():
            lines.append(f"tick_registry.handlers['{namespace}:{entity_id}'] = (entity) => {{")
            for handler in handlers:
                lines.extend(handler.split('\n'))
            lines.append("};")
            lines.append("")

        return lines

class ComponentUIBridge:
    @staticmethod
    def detect_container_class(java_code: str) -> Optional[Dict[str, str]]:
        if 'class ' not in java_code or 'Container' not in java_code:
            return None

        container_info = {
            'class_name': '',
            'slots': [],
            'buttons': [],
            'fields': []
        }

        match = re.search(r'class\s+(\w+)\s+extends\s+Container', java_code)
        if match:
            container_info['class_name'] = match.group(1)

        slot_pattern = r'this\.addSlotToContainer\s*\(\s*new\s+Slot\s*\(([^)]+)\)'
        for match in re.finditer(slot_pattern, java_code):
            container_info['slots'].append(match.group(1))

        button_pattern = r'new\s+GuiButton\s*\(\s*(\d+),\s*([^,]+),\s*([^,]+),\s*"([^"]+)"'
        for match in re.finditer(button_pattern, java_code):
            container_info['buttons'].append({
                'id': match.group(1),
                'x': match.group(2),
                'y': match.group(3),
                'label': match.group(4)
            })

        return container_info if container_info['class_name'] else None

    @staticmethod
    def generate_action_form(container_info: Dict[str, str]) -> list:
        lines = [
            f"// Container → UI Form: {container_info['class_name']}",
            "const show_container_form = async (player) => {",
            "    const form = new ActionFormData();",
            f"    form.title('{container_info['class_name']}');",
            "    ",
        ]

        for button in container_info.get('buttons', []):
            lines.append(f"    form.button('{button['label']}');")

        lines.extend([
            "    ",
            "    const response = await form.show(player);",
            "    if (response.canceled) return;",
            "    ",
            "    switch (response.selection) {",
        ])

        for i, button in enumerate(container_info.get('buttons', [])):
            lines.append(f"        case {i}:")
            lines.append(f"            handle_container_action_{button['id']}(player);")
            lines.append(f"            break;")

        lines.extend([
            "    }",
            "};",
            "",
        ])

        return lines

class JavaGUIConverter:
    _GUI_BASES = {
        'Screen', 'AbstractContainerScreen', 'GuiScreen',
        'AbstractGui', 'ContainerScreen', 'ChestScreen',
    }

    @staticmethod
    def is_gui_class(java_code: str) -> bool:
        return bool(re.search(
            r'extends\s+(?:' + '|'.join(JavaGUIConverter._GUI_BASES) + r')',
            java_code
        ))

    @staticmethod
    def extract_gui_info(java_code: str) -> Dict:
        info: Dict = {
            'class_name': '',
            'title':      None,
            'width':      176,
            'height':     166,
            'slots':      [],
            'buttons':    [],
            'labels':     [],
            'text_fields':[],
        }

        cm = re.search(r'class\s+(\w+)\s+extends', java_code)
        if cm:
            info['class_name'] = cm.group(1)

        for w_pat in [r'imageWidth\s*=\s*(\d+)', r'xSize\s*=\s*(\d+)']:
            wm = re.search(w_pat, java_code)
            if wm:
                info['width'] = int(wm.group(1))
                break
        for h_pat in [r'imageHeight\s*=\s*(\d+)', r'ySize\s*=\s*(\d+)']:
            hm = re.search(h_pat, java_code)
            if hm:
                info['height'] = int(hm.group(1))
                break

        tm = re.search(r'super\s*\([^,]*Component\.translatable\s*\("([^"]+)"\)', java_code)
        if tm:
            info['title'] = tm.group(1)

        for sm in re.finditer(
            r'addSlot\s*\(\s*new\s+\w*Slot\s*\([^,]+,\s*(\d+),\s*(\d+),\s*(\d+)\)',
            java_code
        ):
            info['slots'].append({
                'index': int(sm.group(1)),
                'x':     int(sm.group(2)),
                'y':     int(sm.group(3)),
            })

        for bm in re.finditer(
            r'addRenderableWidget\s*\([^;]*Button\s*\.\s*builder\s*\(Component\.translatable\s*\("([^"]+)"\)[^)]*\)'
            r'|addButton\s*\(\s*new\s+\w*Button\s*\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*,\s*"([^"]+)"',
            java_code
        ):
            label = bm.group(1) or bm.group(6) or 'Button'
            x = int(bm.group(2)) if bm.group(2) else 0
            y = int(bm.group(3)) if bm.group(3) else 0
            w = int(bm.group(4)) if bm.group(4) else 80
            h = int(bm.group(5)) if bm.group(5) else 20
            info['buttons'].append({'label': label, 'x': x, 'y': y, 'w': w, 'h': h})

        for tfm in re.finditer(
            r'new\s+(?:EditBox|TextField)\s*\([^)]*\)',
            java_code
        ):
            info['text_fields'].append({'hint': 'text_input'})

        for lm in re.finditer(
            r'drawString\s*\([^,]*,\s*"([^"]+)"',
            java_code
        ):
            info['labels'].append({'text': lm.group(1)})

        return info

    @staticmethod
    def generate_variables_grid_json(gui_info: Dict, namespace: str) -> dict:
        slots = gui_info.get('slots', [])
        if not slots:
            return {}
        grid_items = []
        for slot in slots:
            grid_items.append({
                'type': 'slot',
                'index': slot['index'],
                'offset': [slot['x'] - gui_info['width'] // 2,
                             slot['y'] - gui_info['height'] // 2, 0],
            })
        return {
            'namespace': namespace,
            f'{gui_info["class_name"]}_grid': {
                'type':   'grid',
                'grid_dimensions': {'x': len(slots), 'y': 1},
                'grid_item_template': f'{namespace}.{gui_info["class_name"]}_slot',
                'collection_name': f'{namespace}_container',
                'grid_rescaling_type': 'none',
                '__items__': grid_items,
            },
        }

    @staticmethod
    def generate_controls_json(gui_info: Dict, namespace: str) -> dict:
        controls = {}
        for i, btn in enumerate(gui_info.get('buttons', [])):
            key = f'{sanitize_identifier(btn["label"])}_button_{i}'
            controls[key] = {
                'type':        'button',
                'text':        btn['label'],
                'size':        [btn['w'], btn['h']],
                'offset':      [btn['x'], btn['y']],
                'button_mappings': [],
            }
        for i, lbl in enumerate(gui_info.get('labels', [])):
            key = f'label_{i}'
            controls[key] = {
                'type':    'label',
                'text':    lbl['text'],
                'offset':  [0, i * 10],
                'color':   [0.2, 0.2, 0.2, 1.0],
            }
        return {'namespace': namespace, 'controls': controls}

    @staticmethod
    def generate_modal_form_js(gui_info: Dict, namespace: str) -> list:
        cls = gui_info['class_name']
        safe = clean_java_artifact_name(cls)
        title = gui_info.get('title') or cls
        lines = [
            f'// GUI Form: {cls} → Bedrock ModalFormData',
            f'import {{ world }} from "@minecraft/server";',
            f'import {{ ModalFormData, ActionFormData }} from "@minecraft/server-ui";',
            '',
            f'export async function show_{safe}_form(player) {{',
        ]
        has_text = bool(gui_info.get('text_fields'))
        has_btns = bool(gui_info.get('buttons'))
        if has_text:
            lines.append(f'    const form = new ModalFormData();')
            lines.append(f'    form.title("{title}");')
            for i, tf in enumerate(gui_info['text_fields']):
                lines.append(f'    form.textField("Field {i}", "Enter value");')
            lines += [
                f'    const res = await form.show(player);',
                f'    if (res.canceled) return;',
                f'    const [{ ", ".join(f"field{i}" for i in range(len(gui_info["text_fields"]))) }] = res.formValues;',
            ]
        elif has_btns:
            lines.append(f'    const form = new ActionFormData();')
            lines.append(f'    form.title("{title}");')
            for btn in gui_info['buttons']:
                lines.append(f'    form.button("{btn["label"]}");')
            lines += [
                f'    const res = await form.show(player);',
                f'    if (res.canceled) return;',
                f'    switch (res.selection) {{',
            ]
            for i, btn in enumerate(gui_info['buttons']):
                fn = sanitize_identifier(btn['label'])
                lines += [
                    f'        case {i}: handle_{safe}_{fn}(player); break;',
                ]
            lines.append(f'    }}')
        else:
            lines.append(f'    // No interactive components detected — check PORTING_NOTES')
        lines += [f'}}', '']
        return lines

    @staticmethod
    def process(java_code: str, namespace: str, out_rp: str, out_bp_scripts: str) -> None:
        if not JavaGUIConverter.is_gui_class(java_code):
            return
        gui_info = JavaGUIConverter.extract_gui_info(java_code)
        if not gui_info['class_name']:
            return
        safe = sanitize_identifier(gui_info['class_name'])

        grid = JavaGUIConverter.generate_variables_grid_json(gui_info, namespace)
        if grid:
            grid_path = os.path.join(out_rp, 'ui', f'{safe}_grid.json')
            os.makedirs(os.path.dirname(grid_path), exist_ok=True)
            with open(grid_path, 'w', encoding='utf-8') as fh:
                json.dump(grid, fh, indent=2)

        ctrl = JavaGUIConverter.generate_controls_json(gui_info, namespace)
        ctrl_path = os.path.join(out_rp, 'ui', f'{safe}_controls.json')
        os.makedirs(os.path.dirname(ctrl_path), exist_ok=True)
        with open(ctrl_path, 'w', encoding='utf-8') as fh:
            json.dump(ctrl, fh, indent=2)

        js_lines = JavaGUIConverter.generate_modal_form_js(gui_info, namespace)
        js_path = os.path.join(out_bp_scripts, f'ui_{safe}.js')
        os.makedirs(os.path.dirname(js_path), exist_ok=True)
        with open(js_path, 'w', encoding='utf-8') as fh:
            fh.write('\n'.join(js_lines))

class DependencyRegistry:
    def __init__(self):
        self.scripts: Dict[str, Dict] = {}
        self.nbt_properties: Dict[str, Set[str]] = {}
        self.dynamic_props: Dict[str, Set[str]] = {}
        self.tick_entities: Set[str] = set()
        self.capabilities: Dict[str, str] = {}

    def register_script(self, script_id: str, depends_on: List[str] = None):
        self.scripts[script_id] = {'depends_on': depends_on or [], 'code': ''}

    def register_nbt_property(self, entity_id: str, prop_name: str):
        if entity_id not in self.nbt_properties:
            self.nbt_properties[entity_id] = set()
        self.nbt_properties[entity_id].add(prop_name)

    def register_dynamic_property(self, entity_id: str, prop_name: str):
        if entity_id not in self.dynamic_props:
            self.dynamic_props[entity_id] = set()
        self.dynamic_props[entity_id].add(prop_name)

    def mark_entity_for_ticking(self, entity_id: str):
        self.tick_entities.add(entity_id)

class GlobalCapabilityRegistry:
    @staticmethod
    def generate_registry_js(namespace: str) -> list:
        return [
            'import { world } from "@minecraft/server";',
            '',
            '// ── Global Capability Registry ────────────────────────────────────────────',
            '// Shared by all converted mods in this pack.  Keys are namespaced to avoid',
            '// collisions when multiple mods are loaded side-by-side.',
            '',
            'const _ns = (ns, key) => `${ns}:cap_${key}`;',
            '',
            'export const CapRegistry = {',
            '',
            '    // ── Energy ──────────────────────────────────────────────────────────',
            '    energy: {',
            '        /** Receive energy into entity up to maxCapacity RF/FE.  Returns accepted amount. */',
            '        receive(entity, ns, amount, maxCapacity = 1_000_000) {',
            '            const key = _ns(ns, "energy");',
            '            const cur = entity.getDynamicProperty(key) ?? 0;',
            '            const accepted = Math.min(amount, maxCapacity - cur);',
            '            entity.setDynamicProperty(key, cur + accepted);',
            '            return accepted;',
            '        },',
            '        extract(entity, ns, amount) {',
            '            const key = _ns(ns, "energy");',
            '            const cur = entity.getDynamicProperty(key) ?? 0;',
            '            const extracted = Math.min(amount, cur);',
            '            entity.setDynamicProperty(key, cur - extracted);',
            '            return extracted;',
            '        },',
            '        get(entity, ns) { return entity.getDynamicProperty(_ns(ns,"energy")) ?? 0; },',
            '        set(entity, ns, v){ entity.setDynamicProperty(_ns(ns,"energy"), v); },',
            '    },',
            '',
            '    // ── Fluid ───────────────────────────────────────────────────────────',
            '    fluid: {',
            '        fill(entity, ns, fluidId, amount, capacity = 1000) {',
            '            const amtKey  = _ns(ns, "fluid_amount");',
            '            const typeKey = _ns(ns, "fluid_type");',
            '            const curAmt  = entity.getDynamicProperty(amtKey) ?? 0;',
            '            const curType = entity.getDynamicProperty(typeKey) ?? fluidId;',
            '            if (curAmt > 0 && curType !== fluidId) return 0;',
            '            const filled  = Math.min(amount, capacity - curAmt);',
            '            entity.setDynamicProperty(amtKey,  curAmt + filled);',
            '            entity.setDynamicProperty(typeKey, fluidId);',
            '            return filled;',
            '        },',
            '        drain(entity, ns, amount) {',
            '            const amtKey = _ns(ns, "fluid_amount");',
            '            const cur    = entity.getDynamicProperty(amtKey) ?? 0;',
            '            const drained = Math.min(amount, cur);',
            '            entity.setDynamicProperty(amtKey, cur - drained);',
            '            return { amount: drained, type: entity.getDynamicProperty(_ns(ns,"fluid_type")) ?? "minecraft:water" };',
            '        },',
            '        getAmount(entity, ns){ return entity.getDynamicProperty(_ns(ns,"fluid_amount")) ?? 0; },',
            '        getType(entity,   ns){ return entity.getDynamicProperty(_ns(ns,"fluid_type"))   ?? "minecraft:water"; },',
            '    },',
            '',
            '    // ── Item slots ──────────────────────────────────────────────────────',
            '    item: {',
            '        /** Insert an ItemStack JSON into a named slot. */',
            '        insert(entity, ns, slotIndex, itemJson) {',
            '            const key = _ns(ns, `item_slot_${slotIndex}`);',
            '            if (entity.getDynamicProperty(key)) return false;',
            '            entity.setDynamicProperty(key, JSON.stringify(itemJson));',
            '            return true;',
            '        },',
            '        extract(entity, ns, slotIndex) {',
            '            const key  = _ns(ns, `item_slot_${slotIndex}`);',
            '            const raw  = entity.getDynamicProperty(key);',
            '            if (!raw) return null;',
            '            entity.setDynamicProperty(key, undefined);',
            '            return JSON.parse(raw);',
            '        },',
            '        get(entity, ns, slotIndex) {',
            '            const raw = entity.getDynamicProperty(_ns(ns,`item_slot_${slotIndex}`));',
            '            return raw ? JSON.parse(raw) : null;',
            '        },',
            '    },',
            '',
            '    // ── Cross-mod DynamicProperty sharing ──────────────────────────────',
            '    data: {',
            '        get(entity, ns, key)      { return entity.getDynamicProperty(_ns(ns, key)); },',
            '        set(entity, ns, key, val) { entity.setDynamicProperty(_ns(ns, key), val); },',
            '        has(entity, ns, key)      { return entity.getDynamicProperty(_ns(ns, key)) !== undefined; },',
            '        del(entity, ns, key)      { entity.setDynamicProperty(_ns(ns, key), undefined); },',
            '    },',
            '',
            '    // ── Registration (call from worldInitialize) ───────────────────────',
            '    registerProperties(propertyRegistry, ns, energyMax = 1_000_000) {',
            '        propertyRegistry.defineEntityNumberProperty(_ns(ns,"energy"),       0);',
            '        propertyRegistry.defineEntityNumberProperty(_ns(ns,"fluid_amount"), 0);',
            '        propertyRegistry.defineEntityStringProperty(_ns(ns,"fluid_type"),   "minecraft:water");',
            '    },',
            '',
            '};  // end CapRegistry',
            '',
            '// Auto-register on world init',
            f'world.afterEvents.worldInitialize.subscribe((e) => {{',
            f'    CapRegistry.registerProperties(e.propertyRegistry, "{namespace}");',
            f'}});',
            '',
        ]

    @staticmethod
    def write(namespace: str, bp_folder: str) -> None:
        out_path = os.path.join(bp_folder, 'scripts', 'cap_registry.js')
        os.makedirs(os.path.dirname(out_path), exist_ok=True)
        with open(out_path, 'w', encoding='utf-8') as fh:
            fh.write('\n'.join(GlobalCapabilityRegistry.generate_registry_js(namespace)))

    @staticmethod
    def ensure_import_in_main(bp_folder: str) -> None:
        main_path = os.path.join(bp_folder, 'scripts', 'main.js')
        import_line = 'import "./cap_registry.js";\n'
        if os.path.exists(main_path):
            with open(main_path, 'r', encoding='utf-8') as fh:
                content = fh.read()
            if 'cap_registry' not in content:
                with open(main_path, 'w', encoding='utf-8') as fh:
                    fh.write(import_line + content)
        else:
            with open(main_path, 'w', encoding='utf-8') as fh:
                fh.write(import_line)

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
COLLECTED_SOUND_DEFS: Dict[str, dict] = {}
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
def _normalize_texture_subfolder(token: str) -> str:
    token = token.lower()
    if token in ("block", "blocks", "blockstate", "blockstates"):
        return "blocks"
    if token in ("item", "items"):
        return "items"
    if token in ("entity", "entities", "mob", "mobs"):
        return "entity"
    return token
def _read_json_from_jar(jar, file_path: str) -> Optional[dict]:
    try:
        with jar.open(file_path) as fh:
            raw = fh.read().decode('utf-8')
            return json.loads(raw)
    except Exception:
        return None
def copy_assets_from_jar(jar_path: str, resource_pack: str):
    global COLLECTED_SOUND_DEFS
    with zipfile.ZipFile(jar_path, 'r') as jar:
        for file in jar.namelist():
            normalized = file.replace("\\", "/")
            lower_file = normalized.lower()
            try:
                if lower_file.endswith(".png") and "/textures/" in lower_file:
                    parts = normalized.split('/')
                    try:
                        idx = [p.lower() for p in parts].index("textures")
                        after = parts[idx + 1:]
                    except ValueError:
                        after = parts[-1:]
                    if after:
                        first = after[0].lower()
                        category = _normalize_texture_subfolder(first)
                        first_is_category = (
                            category != first
                            or first in (
                                "entity", "entities", "mob", "mobs",
                                "item", "items", "block", "blocks",
                                "mob_effect", "particle", "environment",
                                "colormap", "misc", "ui", "map", "gui",
                                "painting", "armor", "font", "effect",
                                "screens", "screen",
                            )
                        ) and "." not in first
                        if first_is_category and len(after) > 1:
                            dest_dir = os.path.join(resource_pack, "textures", category, *[sanitize_identifier(p) for p in after[1:-1]])
                            os.makedirs(dest_dir, exist_ok=True)
                            dest_name = sanitize_filename_keep_ext(after[-1])
                            dest = os.path.join(dest_dir, dest_name)
                        elif first_is_category:

                            dest_dir = os.path.join(resource_pack, "textures", category)
                            os.makedirs(dest_dir, exist_ok=True)
                            dest_name = sanitize_filename_keep_ext(after[0])
                            dest = os.path.join(dest_dir, dest_name)
                        else:

                            dest_dir = os.path.join(resource_pack, "textures")
                            os.makedirs(dest_dir, exist_ok=True)
                            dest_name = sanitize_filename_keep_ext(after[-1])
                            dest = os.path.join(dest_dir, dest_name)
                    else:
                        dest = os.path.join(resource_pack, "textures", sanitize_filename_keep_ext(os.path.basename(file)))
                    os.makedirs(os.path.dirname(dest), exist_ok=True)
                    with jar.open(file) as src_file, open(dest, "wb") as out_file:
                        shutil.copyfileobj(src_file, out_file)
                    continue
                if lower_file.endswith(".png"):
                    dest = os.path.join(resource_pack, "textures", sanitize_filename_keep_ext(os.path.basename(file)))
                    os.makedirs(os.path.dirname(dest), exist_ok=True)
                    with jar.open(file) as src_file, open(dest, "wb") as out_file:
                        shutil.copyfileobj(src_file, out_file)

                    mcmeta_file = file + '.mcmeta'
                    try:
                        with jar.open(mcmeta_file) as mcmeta_src:
                            mcmeta_data = json.load(mcmeta_src)
                            if 'animation' in mcmeta_data:
                                anim = mcmeta_data['animation']
                                frames = anim.get('frames', [])
                                if isinstance(frames, list) and frames:

                                    if all(isinstance(f, int) for f in frames):
                                        frame_list = frames
                                    else:
                                        frame_list = [f['index'] if isinstance(f, dict) and 'index' in f else i for i, f in enumerate(frames)]
                                    flipbook_entry = {
                                        "flipbook_texture": dest.replace(resource_pack + '/', '').replace('\\', '/'),
                                        "atlas_tile": dest.replace(resource_pack + '/', '').replace('\\', '/').replace('.png', ''),
                                        "ticks_per_frame": anim.get('frametime', 1),
                                        "frames": frame_list if len(frame_list) > 1 and frame_list != list(range(len(frame_list))) else len(frame_list)
                                    }
                                    _RP_ASSET_INDEX["flipbook_textures"][flipbook_entry["atlas_tile"]] = flipbook_entry
                    except:
                        pass
                    continue
                if lower_file.endswith(".ogg"):
                    dest = os.path.join(resource_pack, "sound", sanitize_filename_keep_ext(os.path.basename(file)))
                    os.makedirs(os.path.dirname(dest), exist_ok=True)
                    with jar.open(file) as src_file, open(dest, "wb") as out_file:
                        shutil.copyfileobj(src_file, out_file)
                    continue
                if lower_file.endswith(".geo.json") or lower_file.endswith(".geo"):
                    dest_geo = os.path.join(resource_pack, "geometry", sanitize_filename_keep_ext(os.path.basename(file)))
                    os.makedirs(os.path.dirname(dest_geo), exist_ok=True)
                    with jar.open(file) as src_file, open(dest_geo, "wb") as out_file:
                        shutil.copyfileobj(src_file, out_file)
                    continue
                if lower_file.endswith(".json") and "/models/" in lower_file and "/assets/" not in lower_file:
                    if try_convert_model_from_jar(jar, file, resource_pack):
                        continue
                    dest = os.path.join(resource_pack, "models", sanitize_filename_keep_ext(os.path.basename(file)))
                    os.makedirs(os.path.dirname(dest), exist_ok=True)
                    with jar.open(file) as src_file, open(dest, "wb") as out_file:
                        shutil.copyfileobj(src_file, out_file)
                    continue
                if lower_file.endswith(".json") and "/animations/" in lower_file:
                    dest = os.path.join(resource_pack, "animations", sanitize_filename_keep_ext(os.path.basename(file)))
                    os.makedirs(os.path.dirname(dest), exist_ok=True)
                    with jar.open(file) as src_file, open(dest, "wb") as out_file:
                        shutil.copyfileobj(src_file, out_file)
                    continue
                if lower_file.endswith(".json"):
                    if os.path.basename(lower_file) in ("sounds.json", "sound_definitions.json") or                        (os.path.basename(lower_file).startswith("sounds") and lower_file.endswith(".json") and "/sounds/" in lower_file):
                        j = _read_json_from_jar(jar, file)
                        if isinstance(j, dict):
                            defs = j.get("sound_definitions", j) if isinstance(j.get("sound_definitions"), dict) else j
                            for k, v in defs.items():
                                if not isinstance(v, dict):
                                    continue
                                bare_k = k.split(":")[-1]
                                clean_k = sanitize_sound_key(bare_k)
                                if clean_k not in COLLECTED_SOUND_DEFS:
                                    cleaned = _sanitize_sound_def(v)
                                    if not cleaned.get("sounds"):
                                        cleaned["sounds"] = [{"name": f"sound/{clean_k}"}]
                                    COLLECTED_SOUND_DEFS[clean_k] = cleaned
                        continue
                    if "/data/" in lower_file:
                        sub = normalized.split("/data/", 1)[1]
                        parts = sanitize_path_parts(sub)
                        dest = os.path.join(BP_FOLDER, *parts)
                        os.makedirs(os.path.dirname(dest), exist_ok=True)
                        with jar.open(file) as src_file, open(dest, "wb") as out_file:
                            shutil.copyfileobj(src_file, out_file)
                        continue
                    if "/assets/" in lower_file:
                        sub = normalized.split("/assets/", 1)[1]
                        parts_raw = sub.split("/")
                        sub_after = "/".join(parts_raw[1:]) if len(parts_raw) > 1 else sub
                        lower_after = sub_after.lower()
                        if "/sounds/" in lower_after or os.path.basename(lower_after).startswith("sounds") or os.path.basename(lower_after) == "sounds.json":
                            j = _read_json_from_jar(jar, file)
                            if isinstance(j, dict):
                                defs = j.get("sound_definitions", j) if isinstance(j.get("sound_definitions"), dict) else j
                                for k, v in defs.items():
                                    if not isinstance(v, dict):
                                        continue
                                    bare_k = k.split(":")[-1]
                                    clean_k = sanitize_sound_key(bare_k)
                                    if clean_k not in COLLECTED_SOUND_DEFS:
                                        cleaned = _sanitize_sound_def(v)
                                        if not cleaned.get("sounds"):
                                            cleaned["sounds"] = [{"name": f"sound/{clean_k}"}]
                                        COLLECTED_SOUND_DEFS[clean_k] = cleaned
                            continue
                        if "/lang/" in lower_after or lower_after.startswith("lang"):
                            if "/lang/" in lower_after:
                                after = sub_after.split("/lang/", 1)[1]
                            else:
                                after = os.path.basename(sub_after)
                            dest = os.path.join(resource_pack, "lang", sanitize_filename_keep_ext(os.path.basename(after)))
                            os.makedirs(os.path.dirname(dest), exist_ok=True)
                            with jar.open(file) as src_file, open(dest, "wb") as out_file:
                                shutil.copyfileobj(src_file, out_file)
                            continue
                        fname_base = os.path.basename(lower_after)
                        if any(seg in lower_after for seg in ("/blockstates/", "/models/block/", "/models/item/")):
                            continue
                        if fname_base in ("axe.json", "shovel.json", "sword.json", "pickaxe.json",
                                          "hoe.json", "bow.json", "crossbow.json", "trident.json"):
                            continue
                        if "biome_modifier" in fname_base or "biome_modifier" in lower_after:
                            continue
                        if "/neoforge/" in lower_after:
                            continue
                        if "/recipes/" in lower_after or fname_base.endswith("_recipe.json") or fname_base.endswith("_recipes.json"):
                            continue
                        if fname_base.endswith('.json') and len(fname_base) == 7 and fname_base[2] == '_' and fname_base[:2].islower() and fname_base[3:5].islower():
                            dest = os.path.join(resource_pack, "lang", sanitize_filename_keep_ext(fname_base))
                            os.makedirs(os.path.dirname(dest), exist_ok=True)
                            with jar.open(file) as src_file, open(dest, "wb") as out_file:
                                shutil.copyfileobj(src_file, out_file)
                            continue
                        j = _read_json_from_jar(jar, file)
                        if isinstance(j, dict):
                            if "minecraft:item" in j or ("item" in j and isinstance(j.get("item"), dict)):
                                destname = sanitize_filename_keep_ext(os.path.basename(file))
                                if not destname.endswith(".item.json"):
                                    destname = os.path.splitext(destname)[0] + ".item.json"
                                dest = os.path.join(resource_pack, "items", destname)
                                os.makedirs(os.path.dirname(dest), exist_ok=True)
                                with open(dest, "w", encoding="utf-8") as fh:
                                    json.dump(j, fh, indent=2)
                                continue
                            if "minecraft:block" in j or ("block" in j and isinstance(j.get("block"), dict)):
                                destname = sanitize_filename_keep_ext(os.path.basename(file))
                                dest = os.path.join(BP_FOLDER, "blocks", destname)
                                os.makedirs(os.path.dirname(dest), exist_ok=True)
                                with open(dest, "w", encoding="utf-8") as fh:
                                    json.dump(j, fh, indent=2)
                                _mirror_bp_block_to_rp(j, destname)
                                continue
                            if "minecraft:client_entity" in j or "minecraft:entity" in j:
                                destname = sanitize_identifier(os.path.splitext(os.path.basename(file))[0]) + ".entity.json"
                                dest = os.path.join(resource_pack, "entity", destname)
                                os.makedirs(os.path.dirname(dest), exist_ok=True)
                                with open(dest, "w", encoding="utf-8") as fh:
                                    json.dump(j, fh, indent=2)
                                continue
                            if "recipe" in os.path.basename(file).lower() or "recipe" in lower_after or                                "recipes" in j or any("ingredient" in str(k).lower() for k in j.keys()):
                                destname = sanitize_filename_keep_ext(os.path.basename(file))
                                dest = os.path.join(BP_FOLDER, "recipes", destname)
                                os.makedirs(os.path.dirname(dest), exist_ok=True)
                                with open(dest, "w", encoding="utf-8") as fh:
                                    json.dump(j, fh, indent=2)
                                continue
                            if "biome_modifier" in os.path.basename(file).lower() or                                "biome_modifier" in lower_after or                                any("biome" in str(k).lower() for k in j.keys()):
                                destname = sanitize_filename_keep_ext(os.path.basename(file))
                                dest = os.path.join(BP_FOLDER, "data", destname)
                                os.makedirs(os.path.dirname(dest), exist_ok=True)
                                with open(dest, "w", encoding="utf-8") as fh:
                                    json.dump(j, fh, indent=2)
                                continue
                            if all(isinstance(v, str) for v in j.values()) and len(j) > 10:
                                destname = sanitize_filename_keep_ext(os.path.basename(file))
                                dest = os.path.join(resource_pack, "lang", destname)
                                os.makedirs(os.path.dirname(dest), exist_ok=True)
                                with open(dest, "w", encoding="utf-8") as fh:
                                    json.dump(j, fh, indent=2)
                                continue
                        continue
                    continue
                continue
            except Exception as ex:
                _warn(f"Asset copy error: {file} -> {ex}")
def convert_vanilla_model_to_geckolib(classic: dict, model_name: str = "model") -> dict:
    try:
        bones = []
        elements = classic.get("elements", [])
        groups = classic.get("groups", [])
        tex_size = classic.get("texture_size", [16, 16])
        if not elements and not groups:
            raise ValueError("Model must contain either 'elements' or 'groups'")
        if not isinstance(tex_size, list) or len(tex_size) < 2:
            tex_size = [16, 16]
        try:
            tex_width = int(tex_size[0])
            tex_height = int(tex_size[1])
        except (ValueError, TypeError):
            tex_width, tex_height = 16, 16
        def extract_face_uvs(element: dict) -> dict:
            faces = element.get("faces", {})
            result = {}
            for face_name in ["north", "south", "east", "west", "up", "down"]:
                face_data = faces.get(face_name)
                uv = face_data.get("uv") if isinstance(face_data, dict) else None
                if isinstance(uv, list) and len(uv) >= 4:
                    u, v = float(uv[0]), float(uv[1])
                    w, h = float(uv[2]) - u, float(uv[3]) - v
                else:
                    u, v, w, h = 0.0, 0.0, float(tex_width), float(tex_height)
                result[face_name] = {"uv": [u, v], "uv_size": [w, h]}
            return result
        def convert_rotation(rot: dict) -> dict:
            if not isinstance(rot, dict):
                return {"x": 0, "y": 0, "z": 0}
            axis = rot.get("axis", "x")
            angle = rot.get("angle", 0)
            try:
                angle = float(angle)
            except (ValueError, TypeError):
                angle = 0
            rotation = {"x": 0, "y": 0, "z": 0}
            if axis in ["x", "y", "z"]:
                rotation[axis] = angle
            return rotation
        def element_to_cube(el: dict) -> dict:
            if not isinstance(el, dict) or "from" not in el or "to" not in el:
                raise ValueError(f"Invalid element structure: {el}")
            from_pos = el["from"]
            to_pos = el["to"]
            if not (isinstance(from_pos, list) and isinstance(to_pos, list) and
                    len(from_pos) >= 3 and len(to_pos) >= 3):
                raise ValueError(f"Invalid from/to coordinates in element: {el}")
            cube = {
                "origin": [float(from_pos[0]) - 8, float(from_pos[1]), float(from_pos[2]) - 8],
                "size": [float(to_pos[0]) - float(from_pos[0]),
                        float(to_pos[1]) - float(from_pos[1]),
                        float(to_pos[2]) - float(from_pos[2])],
                "uv": extract_face_uvs(el),
            }
            if "rotation" in el:
                cube["rotation"] = convert_rotation(el["rotation"])
            return cube
        def process_group(group, parent_pivot=[0, 0, 0]):
            if isinstance(group, int):
                if 0 <= group < len(elements):
                    bone = {
                        "name": f"bone_{group}",
                        "pivot": [0.0, 0.0, 0.0],
                        "cubes": [element_to_cube(elements[group])],
                    }
                    bones.append(bone)
                return
            if not isinstance(group, dict):
                return
            group_name = group.get("name", "bone")
            origin = group.get("origin", [0, 0, 0])
            if not isinstance(origin, list) or len(origin) < 3:
                origin = [0, 0, 0]
            pivot = [float(origin[0]) - 8, float(origin[1]), float(origin[2]) - 8]
            bone = {
                "name": group_name,
                "pivot": pivot,
                "cubes": [],
            }
            children = group.get("children", [])
            if not isinstance(children, list):
                children = []
            for child in children:
                if isinstance(child, int) and 0 <= child < len(elements):
                    bone["cubes"].append(element_to_cube(elements[child]))
                elif isinstance(child, dict):
                    process_group(child)
            if bone["cubes"]:
                bones.append(bone)
        if groups:
            for group in groups:
                process_group(group)
        else:
            root = {"name": "root", "pivot": [0.0, 0.0, 0.0], "cubes": []}
            for el in elements:
                try:
                    root["cubes"].append(element_to_cube(el))
                except ValueError as e:
                    _warn(f"Skipping invalid element: {e}")
                    continue
            if root["cubes"]:
                bones.append(root)
        if not bones:
            raise ValueError("No valid bones could be created from the model")
        return {
            "format_version": "1.12.0",
            "minecraft:geometry": [
                {
                    "description": {
                        "identifier": f"geometry.{model_name}",
                        "texture_width": tex_width,
                        "texture_height": tex_height,
                        "visible_bounds_width": 2,
                        "visible_bounds_height": 2,
                        "visible_bounds_offset": [0, 1, 0],
                    },
                    "bones": bones,
                }
            ],
        }
    except Exception as e:
        raise ValueError(f"Failed to convert vanilla model '{model_name}': {str(e)}") from e
def _extract_call_args(text: str, call_start: int, n_args: int) -> Optional[List[str]]:
    paren_pos = text.find('(', call_start)
    if paren_pos == -1:
        return None
    i = paren_pos + 1
    args: List[str] = []
    depth = 1
    buf: List[str] = []
    while i < len(text) and depth > 0:
        c = text[i]
        if c == '(':
            depth += 1
            buf.append(c)
        elif c == ')':
            depth -= 1
            if depth == 0:
                if len(args) < n_args:
                    args.append(''.join(buf).strip())
                break
            else:
                buf.append(c)
        elif c == ',' and depth == 1:
            if len(args) < n_args:
                args.append(''.join(buf).strip())
            buf = []
        else:
            buf.append(c)
        i += 1
    return args if len(args) >= n_args else None
def _eval_rot_expr(expr: str) -> Optional[float]:
    s = expr.strip().rstrip('Ff ')
    s = s.replace('(float)', '').strip()
    s = s.replace('Math.PI', str(math.pi))
    try:
        val = float(s)
        return math.degrees(val)
    except (ValueError, TypeError):
        pass

    allowed_chars = set('0123456789.+-*/() ')
    if not all(c in allowed_chars for c in s):
        return None
    try:
        val = eval(s)
        return math.degrees(val)
    except Exception:
        pass
    return None
    return None
def convert_layerdefinition_to_geckolib(
    java_code: str,
    model_name: str,
    namespace: str,
    entity_name: Optional[str] = None,
) -> Optional[dict]:
    try:
        if not isinstance(java_code, str) or not java_code.strip():
            return None
        if 'LayerDefinition' not in java_code and 'MeshDefinition' not in java_code:
            return None
        LAYER_METHOD_NAMES = [
            'createBodyLayer', 'createBodyModel', 'createMeshes', 'createLayers',
            'createLayer', 'createModel', 'createModelData', 'bakeRoot',
        ]
        body = _extract_method_body(java_code, LAYER_METHOD_NAMES)
        if body is None:
            if 'addOrReplaceChild' not in java_code:
                return None
            body = java_code
        tex_w, tex_h = 64, 32
        idx = body.find('LayerDefinition.create(')
        if idx == -1:
            idx = java_code.find('LayerDefinition.create(')
        if idx != -1:
            start = idx + len('LayerDefinition.create(')
            comma1 = java_code.find(',', start)
            if comma1 != -1:
                comma2 = java_code.find(',', comma1 + 1)
                if comma2 != -1:
                    try:
                        tex_w = int(java_code[comma1 + 1:comma2].strip())
                        comma3 = java_code.find(',', comma2 + 1)
                        if comma3 != -1:
                            tex_h = int(java_code[comma2 + 1:comma3].strip())
                    except (ValueError, IndexError):
                        pass
        root_var = 'partdefinition'
        idx = body.find(' = ')
        if idx != -1:
            end = body.find('.getRoot()', idx)
            if end != -1:
                var_part = body[max(0, idx-20):idx].strip()
                eq_idx = var_part.rfind(' ')
                if eq_idx != -1:
                    root_var = var_part[eq_idx+1:].strip()
        var_to_bone: Dict[str, dict] = {
            root_var: {'name': '__root__', 'pivot': [0.0, 0.0, 0.0], 'rotation': [0.0, 0.0, 0.0], 'cubes': []}
        }
        var_to_parent_var: Dict[str, str] = {}
        start_pos = 0
        while True:
            idx = body.find(' = ', start_pos)
            if idx == -1:
                break
            idx2 = body.find('.addOrReplaceChild(', idx)
            if idx2 == -1:
                start_pos = idx + 1
                continue
            var_part = body[max(0, idx-20):idx].strip()
            eq_idx = var_part.rfind(' ')
            if eq_idx == -1:
                start_pos = idx + 1
                continue
            var_name = var_part[eq_idx+1:].strip()
            parent_part = body[idx:idx2].strip()
            eq_idx2 = parent_part.find(' = ')
            if eq_idx2 == -1:
                start_pos = idx + 1
                continue
            parent_var_part = parent_part[max(0, eq_idx2-20):eq_idx2].strip()
            sp_idx = parent_var_part.rfind(' ')
            if sp_idx == -1:
                parent_var = parent_var_part
            else:
                parent_var = parent_var_part[sp_idx+1:].strip()
            paren_start = idx2 + len('.addOrReplaceChild(')
            depth, i = 0, paren_start
            while i < len(body):
                if body[i] == '(':
                    depth += 1
                elif body[i] == ')':
                    depth -= 1
                    if depth == 0:
                        args_content = body[paren_start:i]
                        break
                i += 1
            else:
                start_pos = idx + 1
                continue
            var_name = cs.group(1)
            parent_var = cs.group(2)
            try:
                paren_start = body.index('(', cs.end() - 1)
            except ValueError:
                continue
            depth, i = 0, paren_start
            while i < len(body):
                if body[i] == '(':
                    depth += 1
                elif body[i] == ')':
                    depth -= 1
                    if depth == 0:
                        break
                i += 1
            args_content = body[paren_start + 1:i]
            bone_name = var_name
            quote1 = args_content.find('"')
            if quote1 != -1:
                quote2 = args_content.find('"', quote1 + 1)
                if quote2 != -1:
                    bone_name = args_content[quote1+1:quote2]
            pivot = [0.0, 0.0, 0.0]
            rotation = [0.0, 0.0, 0.0]
            offset_idx = args_content.find('PartPose.offset(')
            if offset_idx != -1:
                offset_args = _extract_call_args(args_content, offset_idx, 3)
                if offset_args:
                    for idx, arg in enumerate(offset_args):
                        v = _parse_java_float(arg.strip())
                        if v is not None:
                            pivot[idx] = v
            rot_idx = args_content.find('PartPose.offsetAndRotation(')
            if rot_idx != -1:
                rot_args = _extract_call_args(args_content, rot_idx, 6)
                if rot_args and len(rot_args) >= 6:
                    for idx in range(3):
                        v = _parse_java_float(rot_args[idx].strip())
                        if v is not None:
                            pivot[idx] = v
                    for idx in range(3, 6):
                        deg = _eval_rot_expr(rot_args[idx].strip())
                        if deg is not None:
                            rotation[idx - 3] = round(deg, 4)
            cubes: list = []
            cur_u, cur_v = 0, 0
            tex_start = 0
            while True:
                tex_idx = args_content.find('.texOffs(', tex_start)
                if tex_idx == -1:
                    break
                tex_args = _extract_call_args(args_content, tex_idx, 2)
                if tex_args and len(tex_args) >= 2:
                    try:
                        cur_u = int(tex_args[0].strip())
                        cur_v = int(tex_args[1].strip())
                    except (ValueError, IndexError):
                        pass
                add_idx = args_content.find('.addBox(', tex_idx)
                if add_idx != -1:
                    add_args = _extract_call_args(args_content, add_idx, 6)
                    if add_args and len(add_args) >= 6:
                        try:
                            vals = [float(add_args[k].strip().rstrip('Ff')) for k in range(6)]
                            cubes.append({
                                "origin": [pivot[0]+vals[0], pivot[1]+vals[1], pivot[2]+vals[2]],
                                "size":   vals[3:6],
                                "uv":     [cur_u, cur_v],
                            })
                        except (ValueError, TypeError, IndexError):
                            pass
                tex_start = tex_idx + 1
            add_start = 0
            while True:
                add_idx = args_content.find('.addBox(', add_start)
                if add_idx == -1:
                    break
                add_args = _extract_call_args(args_content, add_idx, 6)
                if add_args and len(add_args) >= 6:
                    try:
                        vals = [float(add_args[k].strip().rstrip('Ff')) for k in range(6)]
                        candidate = {
                            "origin": [pivot[0]+vals[0], pivot[1]+vals[1], pivot[2]+vals[2]],
                            "size":   vals[3:6],
                            "uv":     [cur_u, cur_v],
                        }
                        if candidate not in cubes:
                            cubes.append(candidate)
                    except (ValueError, TypeError, IndexError):
                        pass
                add_start = add_idx + 1
            var_to_bone[var_name] = {
                'name': bone_name, 'pivot': pivot, 'rotation': rotation, 'cubes': cubes,
            }
            var_to_parent_var[var_name] = parent_var
            start_pos = idx + 1
        def _abs_pivot(var: str) -> List[float]:
            if var not in var_to_parent_var:
                return var_to_bone.get(var, {}).get('pivot', [0.0, 0.0, 0.0])

            path = []
            current = var
            visited = set()
            while current in var_to_parent_var and current not in visited:
                if current == root_var:
                    break
                visited.add(current)
                path.append(current)
                current = var_to_parent_var[current]
                if len(path) > 100:
                    break
            if current == root_var:

                abs_pivot = [0.0, 0.0, 0.0]
                for v in reversed(path):
                    rel = var_to_bone[v]['pivot']
                    abs_pivot = [abs_pivot[i] + rel[i] for i in range(3)]
                return abs_pivot
            else:
                return var_to_bone.get(var, {}).get('pivot', [0.0, 0.0, 0.0])
        gecko_bones = []
        for var, bone in var_to_bone.items():
            if bone['name'] == '__root__':
                continue
            abs_piv = _abs_pivot(var)
            fixed_cubes = []
            for cube in bone['cubes']:
                rel = [cube['origin'][k] - bone['pivot'][k] for k in range(3)]
                fixed_cubes.append({
                    "origin": [round(abs_piv[k] + rel[k], 4) for k in range(3)],
                    "size":   cube['size'],
                    "uv":     cube['uv'],
                })
            b: dict = {"name": bone['name'], "pivot": [round(x, 4) for x in abs_piv]}
            if any(r != 0.0 for r in bone['rotation']):
                b["rotation"] = [round(r, 4) for r in bone['rotation']]
            pv2 = var_to_parent_var.get(var)
            if pv2 and pv2 != root_var and pv2 in var_to_bone:
                pbn = var_to_bone[pv2]['name']
                if pbn != '__root__':
                    b["parent"] = pbn
            if fixed_cubes:
                b["cubes"] = fixed_cubes
            gecko_bones.append(b)
        if not gecko_bones:
            return None
        geo_id = (
            f"geometry.{sanitize_identifier(namespace)}"
            f".{sanitize_identifier(entity_name or model_name)}"
        )
        return {
            "format_version": "1.12.0",
            "minecraft:geometry": [{
                "description": {
                    "identifier":            geo_id,
                    "texture_width":         tex_w,
                    "texture_height":        tex_h,
                    "visible_bounds_width":  2,
                    "visible_bounds_height": 2,
                    "visible_bounds_offset": [0, 1, 0],
                },
                "bones": gecko_bones,
            }],
        }
    except Exception as e:
        _warn(f"Failed to convert LayerDefinition model '{model_name}': {str(e)}")
        return None
def try_convert_model_from_jar(jar, file_path: str, resource_pack: str) -> bool:
    try:
        with jar.open(file_path) as fh:
            data = json.loads(fh.read().decode("utf-8"))
    except Exception:
        return False
    if "elements" not in data and "groups" not in data:
        return False
    model_name = sanitize_identifier(os.path.splitext(os.path.basename(file_path))[0])
    try:
        geckolib_data = convert_vanilla_model_to_geckolib(data, model_name)
        validation_issues = validate_geckolib_geometry(geckolib_data, model_name)
        if validation_issues:
            _warn(f"Validation warnings for {model_name}:")
            for warning in validation_issues[:3]:
                _warn(f"       {warning}")
    except Exception as e:
        _warn(f"Failed to convert {file_path}: {e}")
        return False
    out_path = os.path.join(resource_pack, "geometry", f"{model_name}.geo.json")
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    safe_write_json(out_path, geckolib_data)
    status_msg = f"Converted vanilla model to GeckoLib: {file_path} to {out_path}"
    if 'validation_issues' in locals() and validation_issues:
        status_msg += f"  ({len(validation_issues)} warnings)"

    return True
def convert_modelbase_to_geckolib(
    java_code: str,
    model_name: str,
    namespace: str,
    entity_name: Optional[str] = None,
) -> Optional[dict]:
    try:
        if not isinstance(java_code, str) or not java_code.strip():
            return None
        if 'setRotationPoint' not in java_code and 'addBox' not in java_code:
            return None
        if 'addOrReplaceChild' in java_code and 'setRotationPoint' not in java_code:
            return None
        tex_w, tex_h = 64, 64
        for pat in [
            r'this\.texWidth\s*=\s*(\d+)',
            r'textureWidth\s*=\s*(\d+)',
            r'this\.xTexSize\s*=\s*(\d+)',
        ]:
            m = re.search(pat, java_code)
            if m:
                try:
                    tex_w = int(m.group(1))
                except (ValueError, IndexError):
                    pass
                break
        for pat in [
            r'this\.texHeight\s*=\s*(\d+)',
            r'textureHeight\s*=\s*(\d+)',
            r'this\.yTexSize\s*=\s*(\d+)',
        ]:
            m = re.search(pat, java_code)
            if m:
                try:
                    tex_h = int(m.group(1))
                except (ValueError, IndexError):
                    pass
                break
        ctor_body = None
        cls_name_for_ctor = extract_class_name(java_code)
        if cls_name_for_ctor:
            ctor_body = _extract_method_body(java_code, [cls_name_for_ctor])
        if not ctor_body:
            ctor_body = _extract_method_body(java_code,
                ['init', 'registerParts', 'buildModel', 'setupModel', 'defineModel'])
        if not ctor_body:
            ctor_body = java_code
        var_to_name: Dict[str, str] = {}
        for m in re.finditer(
            r'(?:this\.)?(\w+)\s*=\s*new\s+(?:AdvancedModelBox|ExtendedModelRenderer'
            r'|ModelBoxRenderer|CubeRenderer|AModelRenderer)\s*\([^,)]*,\s*["\']([^"\']+)["\']',
            ctor_body
        ):
            var_to_name[m.group(1)] = m.group(2)
        for m in re.finditer(
            r'(?:this\.)?(\w+)\s*=\s*new\s+(?:ModelRenderer|ModelPart)\s*\(',
            ctor_body
        ):
            vname = m.group(1)
            if vname not in var_to_name:
                var_to_name[vname] = vname
        for m in re.finditer(
            r'new\s+(?:AdvancedModelBox|ModelRenderer)\s*\(\s*this\s*,\s*["\']([^"\']+)["\']',
            ctor_body
        ):
            window = ctor_body[max(0, m.start()-80):m.start()]
            am = re.search(r'(?:this\.)?(\w+)\s*=\s*$', window.rstrip())
            if am:
                var_to_name[am.group(1)] = m.group(1)
        if not var_to_name:
            return None
        var_pivot:    Dict[str, List[float]] = {}
        var_rotation: Dict[str, List[float]] = {}
        var_cubes:    Dict[str, list]        = {}
        var_parent:   Dict[str, str]         = {}
        for var in var_to_name:
            pat_rp = (
                rf'(?:this\.)?{re.escape(var)}\.setRotationPoint\s*\('
                rf'\s*({_FLOAT_RE})\s*,\s*({_FLOAT_RE})\s*,\s*({_FLOAT_RE})\s*\)'
            )
            m = re.search(pat_rp, ctor_body)
            if m:
                try:
                    var_pivot[var] = [
                        _pjf(m.group(1)), _pjf(m.group(2)), _pjf(m.group(3))
                    ]
                except (ValueError, TypeError, IndexError):
                    var_pivot[var] = [0.0, 0.0, 0.0]
            else:
                var_pivot[var] = [0.0, 0.0, 0.0]
            rx, ry, rz = 0.0, 0.0, 0.0
            pat_sra = (
                rf'setRotationAngle\s*\(\s*(?:this\.)?{re.escape(var)}'
                rf'\s*,\s*({_FLOAT_RE})\s*,\s*({_FLOAT_RE})\s*,\s*({_FLOAT_RE})\s*\)'
            )
            m = re.search(pat_sra, ctor_body)
            if m:
                try:
                    rx = math.degrees(_pjf(m.group(1)))
                    ry = math.degrees(_pjf(m.group(2)))
                    rz = math.degrees(_pjf(m.group(3)))
                except (ValueError, TypeError, IndexError):
                    pass
            else:
                for axis, idx in (('X', 0), ('Y', 1), ('Z', 2)):
                    pat_ax = (
                        rf'(?:this\.)?{re.escape(var)}\.rotateAngle{axis}\s*=\s*({_FLOAT_EXPR_RE})'
                    )
                    am = re.search(pat_ax, ctor_body)
                    if am:
                        deg = _eval_rot_expr(am.group(1))
                        if deg is not None:
                            if idx == 0: rx = deg
                            elif idx == 1: ry = deg
                            else: rz = deg
            var_rotation[var] = [round(rx, 4), round(ry, 4), round(rz, 4)]
            cubes: list = []
            cur_u, cur_v = 0, 0
            uv_pats = [
                rf'(?:this\.)?{re.escape(var)}\.setTextureOffset\s*\(\s*(\d+)\s*,\s*(\d+)\s*\)',
                rf'(?:this\.)?{re.escape(var)}\.texOffset\s*\(\s*(\d+)\s*,\s*(\d+)\s*\)',
            ]
            for uv_pat in uv_pats:
                for uvm in re.finditer(uv_pat, ctor_body):
                    try:
                        cur_u = int(uvm.group(1))
                        cur_v = int(uvm.group(2))
                    except (ValueError, IndexError):
                        continue
                    after = ctor_body[uvm.end():uvm.end() + 300]
                    ab = re.match(
                        r'\s*(?:\.\s*)?addBox\s*\('
                        rf'\s*({_FLOAT_RE})\s*,\s*({_FLOAT_RE})\s*,\s*({_FLOAT_RE})'
                        rf'\s*,\s*({_FLOAT_RE})\s*,\s*({_FLOAT_RE})\s*,\s*({_FLOAT_RE})',
                        after
                    )
                    if ab:
                        try:
                            ox, oy, oz = _pjf(ab.group(1)), _pjf(ab.group(2)), _pjf(ab.group(3))
                            sx, sy, sz = _pjf(ab.group(4)), _pjf(ab.group(5)), _pjf(ab.group(6))
                            pivot = var_pivot.get(var, [0., 0., 0.])
                            cubes.append({
                                "origin": [round(pivot[0]+ox, 4), round(pivot[1]+oy, 4), round(pivot[2]+oz, 4)],
                                "size":   [sx, sy, sz],
                                "uv":     [cur_u, cur_v],
                            })
                        except (ValueError, TypeError, IndexError):
                            pass
            for ab in re.finditer(
                rf'(?:this\.)?{re.escape(var)}\.addBox\s*\('
                rf'\s*({_FLOAT_RE})\s*,\s*({_FLOAT_RE})\s*,\s*({_FLOAT_RE})'
                rf'\s*,\s*({_FLOAT_RE})\s*,\s*({_FLOAT_RE})\s*,\s*({_FLOAT_RE})',
                ctor_body
            ):
                try:
                    ox, oy, oz = _pjf(ab.group(1)), _pjf(ab.group(2)), _pjf(ab.group(3))
                    sx, sy, sz = _pjf(ab.group(4)), _pjf(ab.group(5)), _pjf(ab.group(6))
                    pivot = var_pivot.get(var, [0., 0., 0.])
                    candidate = {
                        "origin": [round(pivot[0]+ox, 4), round(pivot[1]+oy, 4), round(pivot[2]+oz, 4)],
                        "size":   [sx, sy, sz],
                        "uv":     [cur_u, cur_v],
                    }
                    if candidate not in cubes:
                        cubes.append(candidate)
                except (ValueError, TypeError, IndexError):
                    pass
            var_cubes[var] = cubes
        for m in re.finditer(
            r'(?:this\.)?(\w+)\.addChild\s*\(\s*(?:this\.)?(\w+)\s*\)',
            ctor_body
        ):
            parent_var = m.group(1)
            child_var  = m.group(2)
            if child_var in var_to_name and parent_var in var_to_name:
                var_parent[child_var] = parent_var
        all_children = set(var_parent.keys())
        def _abs_piv(var: str, depth: int = 0, visited: set = None) -> List[float]:
            if visited is None:
                visited = set()
            if var in visited or depth > 10:
                return var_pivot.get(var, [0., 0., 0.])
            visited.add(var)
            p = var_parent.get(var)
            if p is None or p == var or p not in var_to_name:
                visited.remove(var)
                return var_pivot.get(var, [0., 0., 0.])
            parent_abs = _abs_piv(p, depth + 1, visited)
            rel        = var_pivot.get(var, [0., 0., 0.])
            visited.remove(var)
            return [parent_abs[i] + rel[i] for i in range(3)]
        gecko_bones = []
        for var, bone_name in var_to_name.items():
            abs_piv = _abs_piv(var)
            pivot   = var_pivot.get(var, [0., 0., 0.])
            fixed_cubes = []
            for cube in var_cubes.get(var, []):
                rel = [cube['origin'][i] - pivot[i] for i in range(3)]
                fixed_cubes.append({
                    "origin": [round(abs_piv[i] + rel[i], 4) for i in range(3)],
                    "size":   cube['size'],
                    "uv":     cube['uv'],
                })
            b: dict = {
                "name":  bone_name,
                "pivot": [round(x, 4) for x in abs_piv],
            }
            rot = var_rotation.get(var, [0., 0., 0.])
            if any(r != 0. for r in rot):
                b["rotation"] = rot
            p_var = var_parent.get(var)
            if p_var and p_var in var_to_name:
                b["parent"] = var_to_name[p_var]
            if fixed_cubes:
                b["cubes"] = fixed_cubes
            gecko_bones.append(b)
        if not gecko_bones:
            return None
        geo_id = (
            f"geometry.{sanitize_identifier(namespace)}"
            f".{sanitize_identifier(entity_name or model_name)}"
        )
        return {
            "format_version": "1.12.0",
            "minecraft:geometry": [{
                "description": {
                    "identifier":            geo_id,
                    "texture_width":         tex_w,
                    "texture_height":        tex_h,
                    "visible_bounds_width":  2,
                    "visible_bounds_height": 2,
                    "visible_bounds_offset": [0, 1, 0],
                },
                "bones": gecko_bones,
            }],
        }
    except Exception as e:
        _warn(f"Failed to convert ModelBase model '{model_name}': {str(e)}")
        return None
_FLOAT_RE      = r'[-+]?[0-9]*\.?[0-9]+[FfDdLl]?'
_FLOAT_EXPR_RE = r'[-+]?(?:\(float\)\s*)?[A-Za-z0-9_.*+\-/()\s]+'
def _pjf(s: str) -> float:
    v = _parse_java_float(str(s).strip())
    return v if v is not None else 0.0
def validate_geckolib_geometry(geo_data: dict, model_name: str) -> List[str]:
    warnings = []
    try:
        if not isinstance(geo_data, dict):
            return ["Geometry data is not a dictionary"]
        if "minecraft:geometry" not in geo_data:
            return ["Missing 'minecraft:geometry' key"]
        geometries = geo_data.get("minecraft:geometry", [])
        if not isinstance(geometries, list) or not geometries:
            return ["'minecraft:geometry' is not a non-empty list"]
        geometry = geometries[0]
        if not isinstance(geometry, dict):
            return ["First geometry entry is not a dictionary"]
        desc = geometry.get("description", {})
        if not isinstance(desc, dict):
            warnings.append("Geometry description is not a dictionary")
        else:
            required_desc_fields = ["identifier", "texture_width", "texture_height"]
            for field in required_desc_fields:
                if field not in desc:
                    warnings.append(f"Missing required description field: {field}")
                elif not isinstance(desc[field], (str, int)):
                    warnings.append(f"Description field '{field}' has invalid type")
        bones = geometry.get("bones", [])
        if not isinstance(bones, list):
            return ["'bones' is not a list"]
        if not bones:
            warnings.append("No bones found in geometry")
        bone_names = set()
        for i, bone in enumerate(bones):
            if not isinstance(bone, dict):
                warnings.append(f"Bone {i} is not a dictionary")
                continue
            if "name" not in bone:
                warnings.append(f"Bone {i} missing 'name' field")
            else:
                name = bone["name"]
                if not isinstance(name, str):
                    warnings.append(f"Bone {i} 'name' is not a string")
                elif name in bone_names:
                    warnings.append(f"Duplicate bone name: {name}")
                else:
                    bone_names.add(name)
            if "pivot" not in bone:
                warnings.append(f"Bone '{bone.get('name', i)}' missing 'pivot' field")
            else:
                pivot = bone["pivot"]
                if not isinstance(pivot, list) or len(pivot) != 3:
                    warnings.append(f"Bone '{bone.get('name', i)}' 'pivot' is not a 3-element list")
                else:
                    for j, coord in enumerate(pivot):
                        if not isinstance(coord, (int, float)):
                            warnings.append(f"Bone '{bone.get('name', i)}' pivot[{j}] is not numeric")
            cubes = bone.get("cubes", [])
            if not isinstance(cubes, list):
                warnings.append(f"Bone '{bone.get('name', i)}' 'cubes' is not a list")
            else:
                for j, cube in enumerate(cubes):
                    if not isinstance(cube, dict):
                        warnings.append(f"Bone '{bone.get('name', i)}' cube {j} is not a dictionary")
                        continue
                    for field in ["origin", "size", "uv"]:
                        if field not in cube:
                            warnings.append(f"Bone '{bone.get('name', i)}' cube {j} missing '{field}' field")
                            continue
                        value = cube[field]
                        if field == "uv":
                            valid_box_uv = isinstance(value, list) and len(value) == 2
                            valid_per_face_uv = (
                                isinstance(value, dict)
                                and all(
                                    isinstance(value.get(face), dict)
                                    and isinstance(value[face].get("uv"), list)
                                    and len(value[face]["uv"]) == 2
                                    and isinstance(value[face].get("uv_size"), list)
                                    and len(value[face]["uv_size"]) == 2
                                    for face in ("north", "south", "east", "west", "up", "down")
                                )
                            )
                            if not (valid_box_uv or valid_per_face_uv):
                                warnings.append(f"Bone '{bone.get('name', i)}' cube {j} '{field}' has wrong format")
                        elif not isinstance(value, list) or len(value) != 3:
                            warnings.append(f"Bone '{bone.get('name', i)}' cube {j} '{field}' has wrong format")
            parent = bone.get("parent")
            if parent is not None:
                if not isinstance(parent, str):
                    warnings.append(f"Bone '{bone.get('name', i)}' 'parent' is not a string")
                elif parent not in bone_names and parent != "__root__":
                    warnings.append(f"Bone '{bone.get('name', i)}' references unknown parent '{parent}'")
    except Exception as e:
        return [f"Validation failed with exception: {str(e)}"]
    return warnings
def safe_write_json(out_path: str, data: dict) -> None:
    try:
        os.makedirs(os.path.dirname(out_path) or '.', exist_ok=True)
        model_name = os.path.splitext(os.path.basename(out_path))[0]
        warnings = validate_geckolib_geometry(data, model_name)
        if warnings:
            _warn(f"Validation warnings for {out_path}:")
            for warning in warnings[:5]:
                _warn(f"       {warning}")
            if len(warnings) > 5:
                _warn(f"       ... and {len(warnings) - 5} more warnings")
        with open(out_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
    except Exception as e:
        raise IOError(f"Failed to write JSON to {out_path}: {str(e)}") from e
def scan_and_convert_layerdefinition_models(
    java_files: Dict[str, str],
    namespace: str,
) -> Dict[str, str]:
    model_names = [
        'EntityModel', 'HierarchicalModel', 'AgeableMobModel',
        'LayerDefinition', 'BookOpenModel', 'ArmedModel', 'HeadedModel', 'SkullModelBase',
        'AdvancedEntityModel', 'ExtendedEntityModel', 'CitadelEntityModel',
        'BipedModel', 'QuadrupedModel', 'AgeableModel',
        'GeoModel', 'GeoLayerRenderer',
        'TileEntitySpecialRenderer', 'BlockEntityRenderer',
        'Block'
    ]
    CTOR_SIGNALS = ('setRotationPoint', 'addBox', 'addChild', 'setTextureOffset',
                    'texOffset', 'rotateAngleX', 'rotateAngleY', 'rotateAngleZ',
                    'setRotationAngle', 'AdvancedModelBox', 'ModelRenderer')
    result: Dict[str, str] = {}
    converted = 0
    for path, code in java_files.items():
        fname = os.path.basename(path).lower()
        if any(k in fname for k in ('renderer', 'entity', 'layer', 'event',
                                     'handler', 'registry', 'screen', 'gui',
                                     'packet', 'provider', 'capability')):
            if 'Model' not in os.path.splitext(os.path.basename(path))[0]:
                continue
        is_layerdef   = ('LayerDefinition' in code or 'MeshDefinition' in code
                         or 'addOrReplaceChild' in code)
        is_ctor_model = any(sig in code for sig in CTOR_SIGNALS)
        if not is_layerdef and not is_ctor_model:
            continue
        extends_model = False
        idx = code.find('extends ')
        if idx != -1:
            end = code.find('{', idx)
            if end == -1:
                end = code.find(';', idx)
            if end != -1:
                extends_part = code[idx:end]
                extends_model = any(name in extends_part for name in model_names)
        if not extends_model:
            sig_count = sum(1 for s in CTOR_SIGNALS if s in code)
            if sig_count < 3:
                continue
        if ('GeoModel' in code or 'IAnimatable' in code
                or 'getModelResource' in code or 'getAnimationResource' in code):
            continue
        cls_name   = extract_class_name(code) or os.path.splitext(os.path.basename(path))[0]
        model_stem = clean_java_artifact_name(cls_name)
        out_path   = os.path.join(RP_FOLDER, "geometry", f"{model_stem}.geo.json")
        if os.path.exists(out_path):
            try:
                with open(out_path, encoding='utf-8') as fh:
                    existing = json.load(fh)
                geos = existing.get('minecraft:geometry', [])
                if geos:
                    geo_id = (geos[0].get('description') or {}).get('identifier', '')
                    if geo_id:
                        result[cls_name] = geo_id
            except Exception:
                pass
            continue
        geo_data: Optional[dict] = None
        method_used = ''
        conversion_warnings = []
        if is_layerdef:
            geo_data = convert_layerdefinition_to_geckolib(code, cls_name, namespace)
            if geo_data:
                method_used = 'layerdef'
                validation_issues = validate_geckolib_geometry(geo_data, cls_name)
                if validation_issues:
                    conversion_warnings.extend(validation_issues)
        if geo_data is None and is_ctor_model:
            geo_data = convert_modelbase_to_geckolib(code, cls_name, namespace)
            if geo_data:
                method_used = 'modelbase'
                validation_issues = validate_geckolib_geometry(geo_data, cls_name)
                if validation_issues:
                    conversion_warnings.extend(validation_issues)
        if geo_data is None:
            continue
        try:
            os.makedirs(os.path.join(RP_FOLDER, "geometry"), exist_ok=True)
            safe_write_json(out_path, geo_data)
            geo_id = geo_data['minecraft:geometry'][0]['description']['identifier']
            result[cls_name] = geo_id
            converted += 1
            status_msg = f"[{method_used}] Converted {cls_name} to {model_stem}.geo.json ({geo_id})"
            if conversion_warnings:
                status_msg += f"  ({len(conversion_warnings)} warnings)"

            if conversion_warnings:
                for warning in conversion_warnings[:3]:
                    _warn(f"       {warning}")
        except Exception as e:
            _warn(f"Failed to write {out_path}: {e}")
    if converted:
        pass

    return result
_LAYERDEF_GEO_MAP: Dict[str, str] = {}
def normalise_all_geometry_to_geckolib(resource_pack: str, namespace: str) -> int:
    geom_dir = os.path.join(resource_pack, "geometry")
    os.makedirs(geom_dir, exist_ok=True)
    written = 0
    seen_stems: set = set()
    sweep_dirs = [
        os.path.join(resource_pack, "geometry"),
        os.path.join(resource_pack, "models"),
    ]
    for sweep_dir in sweep_dirs:
        if not os.path.isdir(sweep_dir):
            continue
        for dirpath, _dirs, files in os.walk(sweep_dir):
            for fname in files:
                lower = fname.lower()
                if not lower.endswith(".json") and not lower.endswith(".geo.json"):
                    continue
                src = os.path.join(dirpath, fname)
                try:
                    with open(src, "r", encoding="utf-8", errors="ignore") as fh:
                        data = json.load(fh)
                except Exception:
                    continue
                if not isinstance(data, dict):
                    continue
                if "minecraft:geometry" in data:
                    base = re.sub(r'\.geo(\.json)?$', '', fname, flags=re.I)
                    base = re.sub(r'\.json$', '', base, flags=re.I)
                    stem = sanitize_identifier(base) or sanitize_identifier(fname)
                    dest_name = stem + ".geo.json"
                    dest = os.path.join(geom_dir, dest_name)
                    if os.path.abspath(src) != os.path.abspath(dest) and stem not in seen_stems:
                        geos = data.get("minecraft:geometry", [])
                        if isinstance(geos, list):
                            for g in geos:
                                desc = g.get("description") or {}
                                ident = desc.get("identifier", "")
                                if ident and not ident.startswith(f"geometry.{namespace}"):
                                    pass
                        safe_write_json(dest, data)
                        if os.path.abspath(src) != os.path.abspath(dest) and os.path.exists(src):
                            try:
                                os.remove(src)
                            except Exception:
                                pass
                        seen_stems.add(stem)
                        written += 1

                    else:
                        seen_stems.add(stem)
                    continue
                if "elements" in data or "groups" in data:
                    base = re.sub(r'\.json$', '', fname, flags=re.I)
                    stem = sanitize_identifier(base) or sanitize_identifier(fname)
                    dest_name = stem + ".geo.json"
                    dest = os.path.join(geom_dir, dest_name)
                    if stem in seen_stems or os.path.exists(dest):
                        seen_stems.add(stem)
                        continue
                    try:
                        converted = convert_vanilla_model_to_geckolib(data, stem)
                        geos = converted.get("minecraft:geometry", [])
                        if geos:
                            desc = geos[0].setdefault("description", {})
                            current_id = desc.get("identifier", "")
                            if not current_id or current_id == f"geometry.{stem}":
                                desc["identifier"] = f"geometry.{namespace}.{stem}"
                        safe_write_json(dest, converted)
                        if os.path.abspath(src) != os.path.abspath(dest) and os.path.exists(src):
                            try:
                                os.remove(src)
                            except Exception:
                                pass
                        seen_stems.add(stem)
                        written += 1

                    except Exception as e:
                        _warn(f"[geo-sweep] Conversion failed for {src}: {e}")
                    continue
    if written:
        pass


    for dirpath, _, filenames in os.walk(os.path.join(resource_pack, "models")):
        for fname in filenames:
            lower = fname.lower()
            if not lower.endswith(".json") or lower.endswith(".geo.json"):
                continue
            src = os.path.join(dirpath, fname)
            try:
                with open(src, "r", encoding="utf-8", errors="ignore") as fh:
                    data = json.load(fh)
                if not isinstance(data, dict) or "minecraft:geometry" not in data:
                    os.remove(src)

            except Exception:
                try:
                    os.remove(src)

                except Exception:
                    pass
    return written
def copy_geckolib_animations_from_jar(jar_path: str, resource_pack: str):
    with zipfile.ZipFile(jar_path, 'r') as jar:
        for file in jar.namelist():
            lower = file.lower()
            if ("animation" in lower and lower.endswith(".json")) or ("/animations/" in lower and lower.endswith(".json")):
                dest_name = sanitize_filename_keep_ext(os.path.basename(file))
                dest = os.path.join(resource_pack, "animations", dest_name)
                os.makedirs(os.path.dirname(dest), exist_ok=True)
                with jar.open(file) as src_file, open(dest, "wb") as out_file:
                    shutil.copyfileobj(src_file, out_file)

def rp_texture_exists(texture_path_without_ext: str) -> bool:
    variants = [
        os.path.join(RP_FOLDER, "textures", texture_path_without_ext + ".png"),
        os.path.join(RP_FOLDER, "textures", texture_path_without_ext, os.path.basename(texture_path_without_ext) + ".png"),
        os.path.join(RP_FOLDER, "textures", os.path.basename(texture_path_without_ext) + ".png")
    ]
    for p in variants:
        if os.path.exists(p):
            return True
    return False
def resolve_texture_reference(namespace: str, texture_hint: Optional[str], kind_hint: str, fallback_name: Optional[str] = None) -> str:
    ns = sanitize_identifier(namespace) or "converted"
    if texture_hint:
        candidate = texture_hint.split(":")[-1]
        candidate = candidate.replace(".png", "").strip("/")
        if candidate.startswith("textures/"):
            candidate = candidate[len("textures/"):]
        for probe in [
            candidate,
            f"{kind_hint}/{candidate}",
            f"{kind_hint}/{os.path.basename(candidate)}",
        ]:
            if rp_texture_exists(probe):
                return f"{ns}:{probe}"
        return f"{ns}:{candidate if '/' in candidate else kind_hint + '/' + sanitize_identifier(candidate)}"
    if fallback_name:
        for probe in [f"{kind_hint}/{fallback_name}", fallback_name]:
            if rp_texture_exists(probe):
                return f"{ns}:{probe}"
        return f"{ns}:{kind_hint}/{sanitize_identifier(fallback_name)}"
    return f"{ns}:{kind_hint}/missing_texture"
def texture_ref_to_rp_path(texture_ref: Optional[str], default_kind: str = "entity") -> str:
    if not texture_ref:
        return f"{default_kind}/missing_texture"
    path = texture_ref.split(":", 1)[-1]
    if path.startswith("textures/"):
        path = path[len("textures/"):]
    return path
def generate_texture_registry(pack_name: str):
    item_textures: Dict[str, Dict[str, str]] = {}
    block_textures: Dict[str, Dict[str, str]] = {}
    items_dir = os.path.join(RP_FOLDER, "textures", "items")
    blocks_dir = os.path.join(RP_FOLDER, "textures", "blocks")
    if os.path.isdir(items_dir):
        for root, _, files in os.walk(items_dir):
            for file in files:
                if file.lower().endswith(".png"):
                    rel_dir = os.path.relpath(root, os.path.join(RP_FOLDER, "textures", "items"))
                    name = os.path.splitext(file)[0]
                    if rel_dir != ".":
                        key = os.path.join(rel_dir, name).replace("\\", "/")
                    else:
                        key = name
                    item_textures[key] = {"textures": [f"textures/items/{key}"]}
    if os.path.isdir(blocks_dir):
        for root, _, files in os.walk(blocks_dir):
            for file in files:
                if file.lower().endswith(".png"):
                    rel_dir = os.path.relpath(root, os.path.join(RP_FOLDER, "textures", "blocks"))
                    name = os.path.splitext(file)[0]
                    if rel_dir != ".":
                        key = os.path.join(rel_dir, name).replace("\\", "/")
                    else:
                        key = name
                    block_textures[key] = {"textures": [f"textures/blocks/{key}"]}
    item_registry = {
        "resource_pack_name": pack_name,
        "texture_name": "atlas.items",
        "texture_data": item_textures
    }
    item_path = os.path.join(RP_FOLDER, "textures", "item_texture.json")
    safe_write_json(item_path, item_registry)
    terrain_registry = {
        "resource_pack_name": pack_name,
        "texture_name": "atlas.terrain",
        "texture_data": block_textures
    }
    terrain_path = os.path.join(RP_FOLDER, "textures", "terrain_texture.json")
    safe_write_json(terrain_path, terrain_registry)

def normalize_geometry_file_identifiers():
    geom_dir = os.path.join(RP_FOLDER, "geometry")
    if not os.path.isdir(geom_dir):
        return
    for fname in os.listdir(geom_dir):
        if not (fname.lower().endswith(".geo.json") or fname.lower().endswith(".geo")):
            continue
        path = os.path.join(geom_dir, fname)
        try:
            with open(path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except Exception:
            try:
                txt = open(path, "r", encoding="utf-8", errors="ignore").read()
                m = re.search(r'"identifier"\s*:\s*["\']([^"\']+)["\']', txt)
                if not m:
                    continue
                orig = m.group(1)
                if orig.startswith("geometry."):
                    tail = orig.split(".", 1)[1]
                    newidf = "geometry." + sanitize_identifier(tail)
                else:
                    newidf = "geometry." + sanitize_identifier(orig)
                txt2 = txt.replace(m.group(0), f'"identifier": "{newidf}"')
                with open(path, "w", encoding="utf-8") as fh2:
                    fh2.write(txt2)

            except Exception:
                continue
            continue
        def set_identifiers(obj):
            changed = False
            if isinstance(obj, dict):
                for k, v in list(obj.items()):
                    if k == "identifier" and isinstance(v, str):
                        orig = v
                        if orig.startswith("geometry."):
                            tail = orig.split(".", 1)[1]
                            newidf = "geometry." + sanitize_identifier(tail)
                        else:
                            newidf = "geometry." + sanitize_identifier(orig)
                        obj[k] = newidf
                        changed = True
                    else:
                        ch = set_identifiers(v)
                        changed = changed or ch
            elif isinstance(obj, list):
                for item in obj:
                    ch = set_identifiers(item)
                    changed = changed or ch
            return changed
        changed = set_identifiers(data)
        if changed:
            safe_write_json(path, data)

def fix_animation_format_versions():
    for folder in [os.path.join(RP_FOLDER, "animations"), os.path.join(BP_FOLDER, "animations")]:
        if not os.path.isdir(folder):
            continue
        for fname in os.listdir(folder):
            if not fname.endswith(".json"):
                continue
            fpath = os.path.join(folder, fname)
            try:
                with open(fpath, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if data.get("format_version") == "1.8.0":
                    data["format_version"] = "1.10.0"
                    with open(fpath, "w", encoding="utf-8") as f:
                        json.dump(data, f, indent=2)

            except Exception:
                pass
def sanitize_animation_keys_in_files():
    anim_dir = os.path.join(RP_FOLDER, "animations")
    if not os.path.isdir(anim_dir):
        return
    for fname in os.listdir(anim_dir):
        if not fname.lower().endswith(".json"):
            continue
        path = os.path.join(anim_dir, fname)
        try:
            with open(path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except Exception:
            continue
        if not isinstance(data, dict):
            continue
        anims = data.get("animations")
        if not isinstance(anims, dict):
            continue
        new_anims = {}
        changed = False
        for k, v in anims.items():
            new_key = canonicalize_animation_id(k)
            if not new_key:
                new_key = k
            if new_key != k or new_key in new_anims:
                changed = True
            if new_key in new_anims:
                continue
            new_anims[new_key] = v
        if changed:
            data["animations"] = new_anims
            safe_write_json(path, data)

def canonicalize_animation_id(raw: str, namespace: Optional[str] = None, entity_name: Optional[str] = None) -> str:
    MOTION_KEYWORDS = {
        "idle", "stand", "pose", "float",
        "walk", "walking",
        "run", "running", "chase", "sprint",
        "attack", "strike", "bite", "swipe", "slam", "lunge", "claw",
        "hurt", "hit", "flinch", "pain",
        "death", "die", "dying", "dead",
        "sit", "sitting", "crouch", "lay",
        "swim", "swimming",
        "fly", "flying", "hover", "glide",
        "sleep", "sleeping", "rest",
        "spawn", "appear", "emerge", "summon",
        "open", "close", "blink", "tail", "wing", "flap",
    }
    if raw is None:
        return ""
    s = str(raw).strip().strip('"')
    s = s.strip("'")
    if not s:
        return ""
    s = s.replace("\\", "/")
    s = re.sub(r'\.json$', '', s, flags=re.I)
    if s.lower().startswith("animations/"):
        s = s.split("/", 1)[1]
    s = s.replace("/", ".")
    ns = sanitize_identifier(namespace) if namespace else ""
    ent = sanitize_identifier(entity_name) if entity_name else ""
    if s.startswith("animation."):
        tail = s[len("animation."):]
        parts = [sanitize_identifier(p) for p in tail.split(".")]
        parts = [p for p in parts if p]
        if not parts:
            return ""
        last = parts[-1].lower()
        if not any(kw in last for kw in MOTION_KEYWORDS):
            return ""
        return "animation." + ".".join(parts)
    bare = sanitize_identifier(s)
    if not bare:
        return ""
    if not any(kw in bare.lower() for kw in MOTION_KEYWORDS):
        return ""
    if ns and ent:
        if bare.startswith(f"{ns}.{ent}."):
            return f"animation.{bare}"
        if bare.startswith(f"{ent}."):
            return f"animation.{ns}.{bare}"
        return f"animation.{ns}.{ent}.{bare}"
    if ns:
        if bare.startswith(f"{ns}."):
            return f"animation.{bare}"
        return f"animation.{ns}.{bare}"
    return f"animation.{bare}"
def build_rp_asset_index():
    global _RP_ASSET_INDEX
    textures: list = []
    geometry: list = []
    tex_root = os.path.join(RP_FOLDER, "textures")
    if os.path.isdir(tex_root):
        for dirpath, _, filenames in os.walk(tex_root):
            for fname in filenames:
                if fname.lower().endswith(".png"):
                    abs_path = os.path.join(dirpath, fname)
                    rel = os.path.relpath(abs_path, tex_root).replace("\\", "/")
                    rel_no_ext = os.path.splitext(rel)[0]
                    textures.append((rel_no_ext, abs_path))
    for geo_root in [os.path.join(RP_FOLDER, "models"), os.path.join(RP_FOLDER, "geometry")]:
        if not os.path.isdir(geo_root):
            continue
        for dirpath, _, filenames in os.walk(geo_root):
            for fname in filenames:
                if not (fname.lower().endswith(".geo.json") or fname.lower().endswith(".json")):
                    continue
                abs_path = os.path.join(dirpath, fname)
                try:
                    with open(abs_path, "r", encoding="utf-8", errors="ignore") as fh:
                        data = json.load(fh)
                    geos = data.get("minecraft:geometry", [])
                    extracted = False
                    if isinstance(geos, list):
                        for g in geos:
                            ident = (g.get("description") or {}).get("identifier", "")
                            if ident:
                                geometry.append((ident, abs_path))
                                extracted = True
                    if not extracted:
                        stem = re.sub(r'\.geo(\.json)?$', '', fname, flags=re.I)
                        geometry.append((f"geometry.{sanitize_identifier(stem)}", abs_path))
                except Exception:
                    stem = re.sub(r'\.geo(\.json)?$', '', fname, flags=re.I)
                    geometry.append((f"geometry.{sanitize_identifier(stem)}", abs_path))
    _RP_ASSET_INDEX["textures"] = textures
    _RP_ASSET_INDEX["geometry"] = geometry

    if _RP_ASSET_INDEX["flipbook_textures"]:
        flipbook_path = os.path.join(RP_FOLDER, "textures", "flipbook_textures.json")
        os.makedirs(os.path.dirname(flipbook_path), exist_ok=True)
        with open(flipbook_path, "w", encoding="utf-8") as f:
            json.dump(_RP_ASSET_INDEX["flipbook_textures"], f, indent=2)

def _camel_tokens(s: str) -> set:
    s = re.sub(r'([A-Z])', r'_\1', s).lower().strip("_")
    return {t for t in re.split(r'[_\s\-]+', s) if len(t) > 1}
_ASSET_NOISE = frozenset({
    "entity", "mob", "model", "geo", "texture", "renderer", "render",
    "layer", "type", "base", "abstract", "common", "generic",
})
def _asset_score(entity_tokens: set, candidate_stem: str) -> float:
    cand_base = os.path.basename(candidate_stem)
    cand_tokens = _camel_tokens(cand_base) | set(cand_base.split("_"))
    cand_tokens = {t for t in cand_tokens if len(t) > 1}
    if not entity_tokens or not cand_tokens:
        return 0.0
    et = entity_tokens - _ASSET_NOISE or entity_tokens
    ct = cand_tokens - _ASSET_NOISE or cand_tokens
    shared = et & ct
    if not shared:
        ent_str = "".join(sorted(et))
        cand_str = "".join(sorted(ct))
        if ent_str in cand_str or cand_str in ent_str:
            return 0.38
        for e in sorted(et, key=len, reverse=True):
            if len(e) >= 4:
                for c in ct:
                    if e in c or c in e:
                        return 0.32
        return 0.0
    precision = len(shared) / len(ct) if ct else 0.0
    recall    = len(shared) / len(et) if et else 0.0
    if precision + recall == 0.0:
        return 0.0
    return 2.0 * precision * recall / (precision + recall)
def load_geometry_identifiers() -> Tuple[Dict[str, str], Dict[Tuple[Optional[str], Optional[str]], str]]:
    map_by_file = {}
    map_by_ns_name = {}
    geom_dir = os.path.join(RP_FOLDER, "geometry")
    if not os.path.isdir(geom_dir):
        return map_by_file, map_by_ns_name
    for fname in os.listdir(geom_dir):
        if not (fname.lower().endswith(".geo.json") or fname.lower().endswith(".geo")):
            continue
        path = os.path.join(geom_dir, fname)
        identifier = None
        try:
            with open(path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except Exception:
            try:
                txt = open(path, "r", encoding="utf-8", errors="ignore").read()
                m = re.search(r'"identifier"\s*:\s*["\']([^"\']+)["\']', txt)
                identifier = m.group(1) if m else None
            except Exception:
                identifier = None
        else:
            def find_identifier(obj):
                if isinstance(obj, dict):
                    if "identifier" in obj and isinstance(obj["identifier"], str):
                        return obj["identifier"]
                    for v in obj.values():
                        res = find_identifier(v)
                        if res:
                            return res
                elif isinstance(obj, list):
                    for item in obj:
                        res = find_identifier(item)
                        if res:
                            return res
                return None
            identifier = find_identifier(data)
        basename = os.path.splitext(os.path.splitext(fname)[0])[0]
        basename_norm = sanitize_identifier(basename) or basename.lower()
        if identifier:
            map_by_file[basename_norm] = identifier
            parts = identifier.split(".")
            if len(parts) >= 3 and parts[0] == "geometry":
                ns = parts[1]
                name = ".".join(parts[2:])
                map_by_ns_name[(sanitize_identifier(ns), sanitize_identifier(name))] = identifier
                map_by_ns_name[(None, sanitize_identifier(name))] = identifier
            elif len(parts) >= 2 and parts[0] == "geometry":
                name = ".".join(parts[1:])
                map_by_ns_name[(None, sanitize_identifier(name))] = identifier
        else:
            map_by_file[basename_norm] = build_geometry_id(None, basename_norm)
    return map_by_file, map_by_ns_name
def load_animation_keys() -> Dict[str, Set[str]]:
    anim_dir = os.path.join(RP_FOLDER, "animations")
    result: Dict[str, Set[str]] = {}
    if not os.path.isdir(anim_dir):
        return result
    for fname in os.listdir(anim_dir):
        if not fname.lower().endswith(".json"):
            continue
        path = os.path.join(anim_dir, fname)
        try:
            with open(path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except Exception:
            continue
        keys: Set[str] = set()
        if isinstance(data, dict):
            anims = data.get("animations") or {}
            if isinstance(anims, dict):
                for k in anims.keys():
                    keys.add(k)
        result[os.path.splitext(fname)[0].lower()] = keys
    return result
def _dir_has_java_files(root_dir: str) -> bool:
    if not root_dir or not os.path.isdir(root_dir):
        return False
    for _, _, files in os.walk(root_dir):
        if any(f.endswith(".java") for f in files):
            return True
    return False

def _preferred_java_root(root_dir: str = ".") -> str:
    root_dir = root_dir or "."
    if _dir_has_java_files(root_dir):
        return root_dir
    deobf_root = os.path.join(OUTPUT_DIR, "deobfuscated_java")
    if _dir_has_java_files(deobf_root):
        return deobf_root
    return root_dir
