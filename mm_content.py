from __future__ import annotations

import json
import os
import zipfile
import gzip as _gzip
import io as _io
import struct as _struct
from typing import Optional, Tuple, Dict, Set, List
from mm_core import (
    BP_FOLDER,
    BP_ITEM_FORMAT_VERSION,
    BP_RECIPE_FORMAT_VERSION,
    BP_RP_FORMAT_VERSION,
    RP_FOLDER,
    STATE,
    _warn,
    clean_java_artifact_name,
    sanitize_filename_keep_ext,
    sanitize_identifier,
)
from mm_java import (
    Fragment,
    JAVA_PARTICLE_MAP,
    JavaAST,
    JavaSymbolTable,
    JavaUnit,
    _EFFECT_NAME_MAP,
    _SOUND_NAME_MAP,
    _extract_method_body,
    _find_static_event_handlers,
    _int_arg_text,
    _jtok,
    _name_value_pairs,
    _parse_java_float,
    _translate_handler_body,
    annotation_args,
    annotation_names,
    as_unit,
    dotted,
    expr_text,
    extract_class_name,
    get_unit,
    identifier_set,
    iter_call_sites,
    iter_calls,
    iter_nodes,
    jt,
    method_statements,
    number_value,
    parse_statements,
    raw_number_text,
    simple_type_name,
    string_value,
    translate_statement,
    type_args,
    type_name,
)
from mm_assets import (
    BLOCK_SOUND_PROFILES,
    ITEM_SOUND_PROFILES,
    _finish_block_json,
    _mirror_bp_block_to_rp,
    find_best_texture_match,
    resolve_texture_reference,
    safe_write_json,
)
from mm_entities import (
    JAVA_BIOME_TO_BEDROCK,
    _guess_block_sound_profile,
    _guess_item_sound_profile,
    _is_sound_artifact,
    _mentions,
    _plain_float,
    _resource_location_args,
    _signed_number,
)

def _charset_ok(s: Optional[str], extra: str = '_:-') -> bool:

    return bool(s) and all((c.isascii() and c.isalnum()) or c in extra for c in s)

def _unsigned_number(node) -> Optional[float]:

    txt = raw_number_text(node)
    if txt is None or txt[:1] in '+-':
        return None
    return _parse_java_float(txt)

def _first_call_number(unit: JavaUnit, names, signed: bool = True) -> Optional[float]:
    for c in unit.walk(jt.MethodInvocation):
        if c.member in names and len(c.arguments or []) >= 1:
            v = _signed_number(c.arguments[0]) if signed else _unsigned_number(c.arguments[0])
            if v is not None:
                return v
    return None

def _first_call_int(unit: JavaUnit, pred) -> Optional[int]:
    for c in unit.walk(jt.MethodInvocation):
        if pred(c.member) and len(c.arguments or []) == 1 and _int_arg_text(c.arguments[0]):
            return int(_int_arg_text(c.arguments[0]))
    return None

def _class_parents_lower(java_code) -> Set[str]:
    return {JavaAST.strip_generics(p).lower() for _c, p in JavaAST(java_code).all_class_extends()}

def _registry_hint(unit: JavaUnit) -> Optional[str]:
    for c in unit.walk(jt.MethodInvocation):
        if c.member.lower() == 'setregistryname' and c.arguments:
            s = string_value(c.arguments[0])
            if _charset_ok(s):
                return s
    for cr in unit.walk(jt.ClassCreator):
        vals = _resource_location_args(cr)
        if vals and _charset_ok(vals[0]):
            return vals[0]
    return None

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
    unit = as_unit(java_code)
    calls = list(unit.walk(jt.MethodInvocation))
    for c in calls:
        args = c.arguments or []
        if c.member == 'strength' and 1 <= len(args) <= 2:
            vals = [_signed_number(a) for a in args]
            if all(v is not None for v in vals):
                props["destroy_time"] = vals[0]
                if len(vals) == 2:
                    props["explosion_resistance"] = vals[1]
                break
    if props["destroy_time"] is None:
        v = _first_call_number(unit, ('destroyTime',))
        if v is None:
            v = _first_call_number(unit, ('hardness',))
        props["destroy_time"] = v
    if props["explosion_resistance"] is None:
        v = _first_call_number(unit, ('explosionResistance', 'explosion_resistance', 'explosionResistant', 'resistance'), signed=False)
        if v is not None:
            props["explosion_resistance"] = v
    for ref in unit.walk(jt.MemberReference):
        if ref.qualifier == 'Material' and ref.member and all(c.isupper() or c == '_' for c in ref.member):
            props["material"] = ref.member.lower()
            break
    for c in calls:
        if c.member == 'lightLevel' and len(c.arguments or []) == 1:
            a = c.arguments[0]
            body = a.body if isinstance(a, jt.LambdaExpression) else None
            if _int_arg_text(body):
                props["light_emission"] = min(15, int(_int_arg_text(body)))
                break
    if not props["light_emission"]:
        v = _first_call_int(unit, lambda n: n == 'lightLevel')
        if v is not None:
            props["light_emission"] = min(15, v)
    if not props["light_emission"]:
        v = _first_call_int(unit, lambda n: n == 'lightEmission')
        if v is not None:
            props["light_emission"] = min(15, v)
    v = _first_call_number(unit, ('slipperiness', 'friction'), signed=False)
    if v is not None:
        props["friction"] = v
    hint = _registry_hint(unit)
    if hint:
        props["texture_hint"] = hint.split(":")[-1]
    for m in unit.methods('getLootTable'):
        lits = [string_value(l) for l in iter_nodes(m.body, jt.Literal)]
        lits = [s for s in lits if s is not None]
        loc = next((_resource_location_args(c) for c in iter_nodes(m.body, jt.ClassCreator) if _resource_location_args(c)), None)
        cand = loc[-1] if loc else (lits[0] if lits else None)
        if _charset_ok(cand, '_:/-'):
            props["loot_table"] = cand
            break
    for c in calls:
        if c.member == 'lootTable' and c.arguments:
            s = string_value(c.arguments[0])
            if _charset_ok(s, '_:/-'):
                props["loot_table"] = s
                break
    members = {c.member for c in calls}
    if members & {'noOcclusion', 'noCollission', 'noOcclusionBlock'}:
        props["is_opaque"] = False
    if members & {'noCollission', 'noCollision'}:
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
    parents = _class_parents_lower(java_code or "")
    code_itemy = bool(parents & {'item', 'blockitem', 'spawneggitem', 'food', 'fooditem', 'armoritem', 'sworditem',
                                 'pickaxeitem', 'axeitem', 'shovelitem', 'hoeitem', 'diggeritem', 'tridentitem'}) \
        or any(tok in code for tok in ["minecraft:item", "creativemodetab", "foodproperties", "max_stack_size"])
    code_blocky = bool(parents & {'block', 'rotatedpillarblock', 'bushblock', 'sandblock', 'leavesblock', 'liquidblock',
                                  'stairblock', 'slabblock', 'fenceblock', 'glazedterracottablock'}) \
        or any(tok in code for tok in ["minecraft:block", "material_instances", "destroy_time", "explosion_resistance"])
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

def _leading_run(s: str, pred) -> str:
    out = ''
    for c in s:
        if pred(c):
            out += c
        else:
            break
    return out

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
    unit = as_unit(java_code)
    calls = list(unit.walk(jt.MethodInvocation))
    pairs = _name_value_pairs(unit)
    idents = identifier_set(unit)
    parents = _class_parents_lower(unit)

    def int_pair(pred) -> Optional[int]:
        for name, val in pairs:
            if pred(name.lower()) and _int_arg_text(val):
                return int(_int_arg_text(val))
        return None

    for getter in (
            lambda: _first_call_int(unit, lambda n: n.lower() == 'stacksto'),
            lambda: _first_call_int(unit, lambda n: n.lower().endswith('maxstacksize')),
            lambda: int_pair(lambda n: n in ('stacksize', 'stack_size')) if True else None):
        v = getter()
        if v is not None:
            props["max_stack_size"] = v
            break
    for getter in (
            lambda: _first_call_int(unit, lambda n: n.lower() == 'defaultmaxdamage'),
            lambda: _first_call_int(unit, lambda n: n.lower() == 'durability'),
            lambda: _first_call_int(unit, lambda n: n.lower().endswith('maxdamage')),
            lambda: int_pair(lambda n: n.endswith(('durability', 'max_damage')))):
        v = getter()
        if v is not None:
            props["durability"] = v
            break
    raw = None
    for c in calls:
        if c.member.lower() == 'setregistryname' and c.arguments and _charset_ok(string_value(c.arguments[0])):
            raw = string_value(c.arguments[0])
            break
    if raw is None:
        for cr in unit.walk(jt.ClassCreator):
            vals = _resource_location_args(cr)
            if vals and len(vals) == 1 and _charset_ok(vals[0]):
                raw = vals[0]
                break
    if raw is None:
        for cr in unit.walk(jt.ClassCreator):
            vals = _resource_location_args(cr)
            if vals and len(vals) >= 2 and _charset_ok(vals[1], '_/:-'):
                raw = vals[1]
                break
    if raw is not None:
        props["registry_name"] = raw
        props["texture_hint"] = raw.split(":")[-1]

    def tab_const(qualifier_suffix: str) -> Optional[str]:
        for ref in unit.walk(jt.MemberReference):
            if ref.qualifier and ref.qualifier.split('.')[-1].endswith(qualifier_suffix):
                name = _leading_run(ref.member, lambda c: c.isupper() or c.isdigit() or c == '_')
                if name:
                    return name
        return None

    def tab_call(member: str) -> Optional[str]:
        for c in calls:
            if c.member == member and len(c.arguments or []) == 1:
                a = c.arguments[0]
                if isinstance(a, jt.MemberReference) and a.qualifier and a.member and \
                        all(ch.isupper() or ch.isdigit() or ch == '_' for ch in a.member):
                    return a.member
        return None
    for getter in (lambda: tab_const('ItemGroup'), lambda: tab_const('CreativeModeTab'),
                   lambda: tab_call('tab'), lambda: tab_call('creativeModeTab')):
        v = getter()
        if v:
            props["creative_tab"] = v.lower()
            break

    if (idents & {'foodproperties', 'nutrition', 'saturationmod'} or any(i == 'nutrition' for i in idents)
            or any(c.member == 'food' for c in calls) or parents & {'itemfood', 'bowlfooditem'}
            or any('foodproperties' in i or 'nutrition' in i or 'saturationmod' in i for i in idents)):
        props["is_food"] = True
        v = _first_call_int(unit, lambda n: 'nutrition' in n.lower())
        if v is not None:
            props["nutrition"] = v
        for c in calls:
            if c.member.lower().startswith('saturation') and c.arguments:
                num = _unsigned_number(c.arguments[0])
                if num is not None:
                    props["saturation"] = num
                    break
    slots = {}
    for ref in unit.walk(jt.MemberReference):
        m = ref.member.upper() if ref.member else ''
        if m in ('HEAD', 'CHEST', 'LEGS', 'FEET') and ((ref.qualifier or '').endswith('EquipmentSlot') or 'armoritem' in idents):
            slots.setdefault(m, True)
    for key, slot in (('HEAD', "slot.armor.head"), ('CHEST', "slot.armor.chest"),
                      ('LEGS', "slot.armor.legs"), ('FEET', "slot.armor.feet")):
        if key in slots:
            props["is_armor"] = True
            props["armor_slot"] = slot
            break
    if any(k in idents for k in ('sworditem', 'tiereditem', 'attack_damage_modifier')) or any('sword' in p for p in parents):
        props["is_weapon"] = True
        for name, val in pairs:
            if (name.lower() == 'attackdamage' or name.upper().endswith('ATTACK_DAMAGE')) and _unsigned_number(val) is not None:
                props["attack_damage"] = _unsigned_number(val)
                break
    if any(any(k in i for k in ('pickaxeitem', 'shovelitem', 'axeitem', 'hoeitem', 'diggeritem')) for i in idents) \
            or any('tool' in p for p in parents):
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

JAVA_TAG_TO_BEDROCK_GROUP = {
    "forge:ores": "ore",
    "forge:ingots": "ingot",
    "forge:gems": "gem",
    "forge:dusts": "dust",
    "forge:nuggets": "nugget",
    "forge:rods": "stick",
    "forge:plates": "plate",
    "forge:tools": "tool",
    "forge:tools/swords": "weapon",
    "forge:tools/axes": "tool",
    "forge:tools/pickaxes": "tool",
    "forge:tools/shovels": "tool",
    "forge:tools/hoes": "tool",
    "forge:weapons": "weapon",
    "forge:armor": "armor",
    "forge:armors": "armor",
    "forge:food": "food",
    "forge:seeds": "seeds",
    "forge:crops": "crop",
    "forge:bones": "misc",
    "forge:string": "misc",
    "forge:feathers": "misc",
    "forge:storage_blocks": "misc",
    "forge:raw_materials": "misc",
    "neoforge:ores": "ore",
    "neoforge:ingots": "ingot",
    "neoforge:gems": "gem",
    "c:ores": "ore",
    "c:ingots": "ingot",
    "c:gems": "gem",
    "c:dusts": "dust",
    "c:nuggets": "nugget",
    "c:foods": "food",
    "c:tools": "tool",
    "c:weapons": "weapon",
    "c:armors": "armor",
    "minecraft:logs": "log",
    "minecraft:logs_that_burn": "log",
    "minecraft:planks": "planks",
    "minecraft:slabs": "slab",
    "minecraft:stairs": "stair",
    "minecraft:doors": "door",
    "minecraft:trapdoors": "door",
    "minecraft:leaves": "leaves",
    "minecraft:saplings": "sapling",
    "minecraft:flowers": "flower",
    "minecraft:small_flowers": "flower",
    "minecraft:tall_flowers": "flower",
    "minecraft:wool": "wool",
    "minecraft:swords": "weapon",
    "minecraft:axes": "tool",
    "minecraft:pickaxes": "tool",
    "minecraft:shovels": "tool",
    "minecraft:hoes": "tool",
    "minecraft:helmets": "armor",
    "minecraft:chestplates": "armor",
    "minecraft:leggings": "armor",
    "minecraft:boots": "armor",
    "minecraft:coals": "misc",
    "minecraft:arrows": "misc",
    "minecraft:beds": "misc",
    "minecraft:banners": "misc",
    "minecraft:music_discs": "misc",
    "minecraft:fishes": "food",
    "minecraft:meat": "food",
}
def extract_item_tags_from_jar(jar_path: str, namespace: str):
    out_dir = os.path.join(BP_FOLDER, "item_catalog")
    os.makedirs(out_dir, exist_ok=True)
    catalog = {"format_version": BP_ITEM_FORMAT_VERSION, "minecraft:item_catalog": {"description": {"identifier": f"{namespace}:catalog"}, "groups": []}}
    groups: Dict[str, list] = {}
    with zipfile.ZipFile(jar_path, "r") as jar:
        for name in jar.namelist():
            lower = name.lower()
            if "/tags/items/" not in lower or not lower.endswith(".json"):
                continue
            try:
                with jar.open(name) as f:
                    data = json.loads(f.read().decode("utf-8"))
                tag_name = os.path.splitext(os.path.basename(name))[0]
                group = None
                for java_tag, bedrock_group in JAVA_TAG_TO_BEDROCK_GROUP.items():
                    if tag_name in java_tag or java_tag.split(":")[-1] in tag_name:
                        group = bedrock_group
                        break
                if not group:
                    group = sanitize_identifier(tag_name)
                if group not in groups:
                    groups[group] = []
                for value in data.get("values", []):
                    if isinstance(value, str) and ":" in value:
                        ns, item = value.split(":", 1)
                        if ns != "minecraft":
                            item_id = f"{namespace}:{sanitize_identifier(item)}"
                        else:
                            item_id = value
                        if item_id not in groups[group]:
                            groups[group].append(item_id)
            except Exception as e:
                _warn(f"[tags] Failed to parse {name}: {e}")
    for group_name, items in groups.items():
        if items:
            catalog["minecraft:item_catalog"]["groups"].append({
                "group_name": group_name,
                "items": items
            })
    if catalog["minecraft:item_catalog"]["groups"]:
        out_path = os.path.join(out_dir, f"{namespace}_catalog.json")
        safe_write_json(out_path, catalog)

_ITEM_BASE_NAMES = frozenset({
    'Item', 'SwordItem', 'PickaxeItem', 'ShovelItem', 'AxeItem', 'HoeItem', 'ArmorItem', 'BowItem',
    'ShieldItem', 'FoodOnAStickItem', 'ThrowablePotionItem', 'TieredItem', 'DiggerItem', 'BlockItem',
    'DoubleHighBlockItem', 'StandingAndWallBlockItem'})
_BLOCK_BASE_NAMES = frozenset({
    'Block', 'BaseBlock', 'HalfTransparentBlock', 'BushBlock', 'FlowerBlock', 'SaplingBlock', 'CropBlock',
    'TrapDoorBlock', 'DoorBlock', 'FenceBlock', 'WallBlock', 'StairBlock', 'SlabBlock', 'PressurePlateBlock',
    'ButtonBlock', 'LeverBlock', 'TorchBlock', 'RedStoneWireBlock', 'ChestBlock', 'FurnaceBlock', 'LiquidBlock',
    'GrassBlock', 'RotatedPillarBlock', 'HorizontalDirectionalBlock', 'DirectionalBlock'})

def _extends_any(unit, names) -> bool:
    return any(c.extends is not None and simple_type_name(c.extends) in names for c in unit.classes())

def _mentions_qualified(unit, prefixes: tuple) -> bool:

    for n in unit.walk(jt.MemberReference, jt.MethodInvocation):
        q = n.qualifier or ''
        if any(q == p or q.startswith(p + '.') for p in prefixes):
            return True
    for n in unit.walk(jt.ClassCreator, jt.ReferenceType):
        t = type_name(n.type if isinstance(n, jt.ClassCreator) else n)
        if any(t == p or t.startswith(p + '.') for p in prefixes):
            return True
    return False

def _override_method(unit, name: str, first_param_type: Optional[str] = None) -> bool:
    for m in unit.methods(name):
        if 'Override' in annotation_names(m) and 'public' in (m.modifiers or ()):
            if first_param_type is None or (m.parameters and simple_type_name(m.parameters[0].type) == first_param_type):
                return True
    return False

def item_content_signals(code: str, superchain: List[str], fname_item_hint: bool) -> List[bool]:
    unit = get_unit(code)
    calls = {c.member for c in unit.walk(jt.MethodInvocation)}
    return [
        _extends_any(unit, _ITEM_BASE_NAMES),
        _mentions_qualified(unit, ('Item.Properties',)),
        bool(calls & {'stacksTo', 'durability'}) or _mentions_qualified(unit, ('FoodProperties.Builder',)),
        _override_method(unit, 'use', 'Level') or any(
            simple_type_name(t) == 'InteractionResultHolder' and 'ItemStack' in type_args(t)
            for t in unit.walk(jt.ReferenceType)),
        fname_item_hint,
        bool(set(superchain) & _ITEM_BASE_NAMES),
    ]

def block_content_signals(code: str, superchain: List[str], fname_block_hint: bool) -> List[bool]:
    unit = get_unit(code)
    calls = {c.member for c in unit.walk(jt.MethodInvocation)}
    return [
        _extends_any(unit, _BLOCK_BASE_NAMES),
        _mentions_qualified(unit, ('BlockBehaviour.Properties', 'Block.Properties')),
        bool(calls & {'strength', 'noCollission', 'lightLevel', 'randomTicks'}),
        _override_method(unit, 'use', 'BlockState') or bool(unit.methods('getStateForPlacement')) or 'getStateForPlacement' in calls,
        fname_block_hint,
        bool(set(superchain) & _BLOCK_BASE_NAMES),
    ]

def entity_registration_name(code: str, cls: str) -> Optional[str]:

    unit = get_unit(code)
    calls = list(unit.walk(jt.MethodInvocation))
    for c in calls:
        if c.member == 'setRegistryName' and c.arguments:
            s = string_value(c.arguments[0])
            if s and all(ch.isalnum() or ch in '_:-' for ch in s):
                return s
    for c in calls:
        args = c.arguments or []
        if c.member == 'register' and args:
            s = string_value(args[0])
            if s and all(ch.isalnum() or ch == '_' for ch in s) and cls and cls in [
                    n.expression.member for n in iter_nodes(list(args[1:]), jt.MethodReference)
                    if isinstance(n.expression, jt.MemberReference) and getattr(n.method, 'member', None) == 'new']:
                return s
    for c in calls:
        if c.member == 'of' and (c.qualifier or '').startswith('EntityType.Builder'):
            for sel in c.selectors or []:
                if isinstance(sel, jt.MethodInvocation) and sel.member == 'build' and sel.arguments:
                    s = string_value(sel.arguments[0])
                    if s and all(ch.isalnum() or ch == '_' for ch in s):
                        return s
    return None

JAVA_BLOCK_MATERIAL_MAP = {
    "WOOD": "wood", "STONE": "stone", "METAL": "metal", "SAND": "sand",
    "GLASS": "glass", "CLOTH": "wool", "PLANT": "plant", "DIRT": "dirt",
    "GRASS": "dirt", "ICE": "ice", "LEAVES": "leaves", "WEB": "web",
    "SPONGE": "sponge", "WATER": "water", "LAVA": "lava",
    "FIRE": "decoration", "DECORATION": "decoration",
}
def _prop_field_matches(unit, prop_type: str, key: str) -> bool:

    for node in list(unit.walk(jt.FieldDeclaration)) + list(unit.walk(jt.LocalVariableDeclaration)):
        if simple_type_name(node.type) != prop_type:
            continue
        for d in node.declarators:
            strings = [string_value(l) or '' for l in iter_nodes(d.initializer, jt.Literal)] if d.initializer is not None else []
            if key in d.name.lower() or any(key in s.lower() for s in strings):
                return True
    return False

def _state_ref(unit, names) -> bool:

    for ref in unit.walk(jt.MemberReference):
        if (ref.qualifier or '').split('.')[-1] == 'BlockStateProperties' and \
                any(ref.member.upper().startswith(n) for n in names):
            return True
    return False

def _build_block_definition(block_id: str, safe_name: str, namespace: str, java_code, block_class_name: str = "") -> dict:

    unit = as_unit(java_code)
    props = extract_block_properties_from_java(unit)
    idents = set(identifier_set(unit))
    if block_class_name:
        idents.add(block_class_name.lower())
    material_key = (props.get("material") or "").upper()
    material = JAVA_BLOCK_MATERIAL_MAP.get(material_key, "stone")
    hardness = props.get("destroy_time") if props.get("destroy_time") is not None else 2.0
    resistance = props.get("explosion_resistance") if props.get("explosion_resistance") is not None else hardness * 3.0
    light_emission = props.get("light_emission", 0)
    friction = props.get("friction", 0.6)
    is_opaque = props.get("is_opaque", True)
    render_method = "opaque" if is_opaque else "alpha_test"
    tex_match = find_best_texture_match(safe_name, "blocks")
    doc = {
        "format_version": BP_RP_FORMAT_VERSION,
        "minecraft:block": {
            "description": {
                "identifier": block_id,
                "menu_category": {"category": "construction"}
            },
            "components": {
                "minecraft:material_instances": {
                    "*": {"texture": tex_match, "render_method": render_method}
                },
                "minecraft:destructible_by_mining": {"seconds_to_destroy": hardness},
                "minecraft:destructible_by_explosion": {"explosion_resistance": resistance},
                "minecraft:friction": friction,
                "minecraft:light_emission": light_emission,
            }
        }
    }
    comps = doc["minecraft:block"]["components"]
    geo_dir = os.path.join(RP_FOLDER, "geometry")
    geo_candidates = [
        safe_name + ".geo.json",
        safe_name + ".json",
    ]
    has_geo = any(os.path.exists(os.path.join(geo_dir, c)) for c in geo_candidates)
    if has_geo:
        comps["minecraft:geometry"] = f"geometry.{safe_name}"
    states = {}
    permutations = []
    if _state_ref(unit, ('FACING',)) or 'directionproperty' in idents:
        states["facing"] = ["north", "south", "east", "west", "up", "down"]
        rot_map = {"north": 0, "south": 180, "east": 90, "west": 270}
        for d, rot in rot_map.items():
            permutations.append({
                "condition": f'query.block_property("{namespace}:facing") == "{d}"',
                "components": {"minecraft:transformation": {"rotation": [0, rot, 0]}}
            })
    if _state_ref(unit, ('POWERED',)) or _prop_field_matches(unit, 'BooleanProperty', 'power'):
        states["powered"] = [False, True]
        permutations.append({
            "condition": f'query.block_property("{namespace}:powered") == true',
            "components": {"minecraft:light_emission": min(15, light_emission + 8)}
        })
    if _state_ref(unit, ('WATERLOGGED',)):
        states["waterlogged"] = [False, True]
    if _state_ref(unit, ('OPEN',)) or _prop_field_matches(unit, 'BooleanProperty', 'open'):
        states["open"] = [False, True]
    if _state_ref(unit, ('LIT',)) or _prop_field_matches(unit, 'BooleanProperty', 'lit'):
        states["lit"] = [False, True]
        permutations.append({
            "condition": f'query.block_property("{namespace}:lit") == true',
            "components": {"minecraft:light_emission": 15}
        })
    if _prop_field_matches(unit, 'IntegerProperty', 'age') or _state_ref(unit, ('AGE',)):
        max_age = 7
        for c in unit.walk(jt.MethodInvocation):
            if c.member == 'create' and c.qualifier == 'IntegerProperty' and len(c.arguments or []) >= 3 \
                    and _int_arg_text(c.arguments[1]) and _int_arg_text(c.arguments[2]):
                max_age = int(_int_arg_text(c.arguments[2]))
                break
        states["age"] = list(range(max_age + 1))
    if 'horizontal_facing' in idents or 'horizontaldirectionalblock' in idents:
        if "facing" not in states:
            states["facing"] = ["north", "south", "east", "west"]
    if 'stairblock' in idents:
        states["facing"] = ["north", "south", "east", "west"]
        states["upside_down_bit"] = [False, True]
    if 'slabblock' in idents:
        states.setdefault("top_slot_bit", [False, True])
    if states:
        doc["minecraft:block"]["description"]["states"] = {f"{namespace}:{k}": v for k, v in states.items()}
    if permutations:
        doc["minecraft:block"]["permutations"] = permutations
    return doc

def convert_java_block_full(java_code: str, java_path: str, namespace: str):
    cls = extract_class_name(java_code) or os.path.splitext(os.path.basename(java_path))[0]
    safe_name = clean_java_artifact_name(cls)
    block_id = f"{namespace}:{safe_name}"
    doc = _build_block_definition(block_id, safe_name, namespace, java_code, block_class_name=cls)
    generate_block_script(java_code, safe_name, block_id, namespace)
    _finish_block_json(doc, safe_name)

def _block_class_extends_block(class_name: str, cls_to_code: Dict[str, str]) -> bool:
    if not class_name:
        return False
    if class_name == "Block" or any(seg in _BLOCK_BASE_NAMES for seg in class_name.split('.')):
        return True
    if class_name in cls_to_code:
        return any(JavaAST.strip_generics(p) in _BLOCK_BASE_NAMES
                   for _c, p in JavaAST(cls_to_code[class_name]).all_class_extends())
    return False

def _first_string_arg_index(args: list, charset: str = '_./') -> Tuple[Optional[str], int]:

    for i, a in enumerate(args):
        for lit in iter_nodes(a, jt.Literal):
            s = string_value(lit)
            if s and _charset_ok(s, charset):
                return s, i
    return None, -1

def _creator_of(node) -> Optional[jt.ClassCreator]:

    if isinstance(node, jt.ClassCreator):
        return node
    if isinstance(node, jt.LambdaExpression) and isinstance(node.body, jt.ClassCreator):
        return node.body
    return None

def _block_registry_vars(unit: JavaUnit) -> Set[str]:
    out = set()
    for f in unit.fields():
        if simple_type_name(f.type) != 'DeferredRegister' and type_name(f.type).split('.')[0] != 'DeferredRegister':
            continue
        full = type_name(f.type)
        generic_block = 'Block' in type_args(f.type)
        if not (generic_block or full.endswith('.Blocks')):
            continue
        for d in f.declarators:
            init = d.initializer
            if isinstance(init, jt.MethodInvocation) and init.member in ('create', 'createBlocks') \
                    and (init.qualifier or '').split('.')[0] == 'DeferredRegister':
                out.add(d.name)
    return out

def _find_block_registrations(unit: JavaUnit, cls_to_code: Dict[str, str]) -> list:

    regs = []
    calls = list(unit.walk(jt.MethodInvocation))
    for var in _block_registry_vars(unit):
        for call in calls:
            if call.qualifier != var or not call.member.lower().startswith('register'):
                continue
            args = list(call.arguments or [])
            name, idx = _first_string_arg_index(args)
            if not name:
                continue
            rest = args[idx + 1:]
            creator = _creator_of(rest[0]) if rest else None
            if creator is not None:
                regs.append((name, simple_type_name(creator.type), [creator]))
            elif rest and isinstance(rest[0], jt.MethodReference) and isinstance(rest[0].expression, jt.MemberReference) \
                    and getattr(rest[0].method, 'member', None) == 'new':
                regs.append((name, rest[0].expression.member, rest[1:]))
            elif call.member.lower() == 'registersimpleblock':
                regs.append((name, "Block", rest))
    fabric_holders = {('Registries', 'BLOCK'), ('Registry', 'BLOCK'), ('BuiltInRegistries', 'BLOCK')}
    for call in calls:
        args = list(call.arguments or [])
        if call.member == 'register' and call.qualifier == 'Registry' and len(args) >= 3 \
                and isinstance(args[0], jt.MemberReference) and (args[0].qualifier, args[0].member) in fabric_holders:
            name = None
            for lit in iter_nodes(args[1], jt.Literal):
                s = string_value(lit)
                if s and _charset_ok(s, '_./'):
                    name = s
            creator = _creator_of(args[2])
            if name and creator is not None:
                regs.append((name, simple_type_name(creator.type), [creator]))
    for call in calls:
        args = list(call.arguments or [])
        if call.member.lower().startswith('register') and len(args) >= 2:
            name = string_value(args[0])
            creator = args[1] if isinstance(args[1], jt.ClassCreator) else None
            if name and _charset_ok(name, '_./') and creator is not None \
                    and _block_class_extends_block(simple_type_name(creator.type), cls_to_code):
                regs.append((name, simple_type_name(creator.type), [creator]))
    return regs

def scan_block_registrations(java_files: Dict[str, str], namespace: str, stats: Optional[dict] = None) -> Tuple[set, set]:

    handled_block_names: set = set()
    handled_files: set = set()

    cls_to_code: Dict[str, str] = {}
    cls_to_path: Dict[str, str] = {}
    for path, code in java_files.items():
        cls = extract_class_name(code)
        if cls:
            cls_to_code[cls] = code
            cls_to_path[cls] = path

    for path, raw_code in java_files.items():
        if _is_sound_artifact(raw_code, path, extract_class_name(raw_code)):
            continue

        unit = get_unit(raw_code)
        registrations = _find_block_registrations(unit, cls_to_code)
        if not registrations:
            continue

        for reg_name, block_class, nodes in registrations:
            safe_name = sanitize_identifier(reg_name.split('/')[-1].split(':')[-1])
            if not safe_name or safe_name in handled_block_names:
                continue
            block_id = f"{namespace}:{safe_name}"

            parts = list(nodes)
            custom_path = cls_to_path.get(block_class)
            if custom_path:
                custom_unit = get_unit(cls_to_code.get(block_class, ""))
                if custom_unit.tree is not None:
                    parts.append(custom_unit.tree)
                handled_files.add(custom_path)
            combined = Fragment(parts)

            try:
                doc = _build_block_definition(block_id, safe_name, namespace, combined, block_class_name=block_class)
                generate_block_script(combined, safe_name, block_id, namespace)
                _finish_block_json(doc, safe_name)
                handled_block_names.add(safe_name)
                handled_files.add(path)
                if stats is not None:
                    stats.setdefault("converted_blocks", []).append(f"{path} :: {reg_name}")
            except Exception as e:
                _warn(f" Failed to convert registered block '{reg_name}' from {os.path.basename(path)}: {e}")
                if stats is not None:
                    stats.setdefault("errors", []).append(f"{path} :: {reg_name}: {e}")

    return handled_block_names, handled_files
_BLOCK_EVENT_METHOD_MAP = {
    "use":             ("afterEvents", "playerInteractWithBlock", "event.player"),
    "attack":          ("afterEvents", "playerInteractWithBlock", "event.player"),
    "stepOn":          ("afterEvents", "entityStepOnBlock",       "event.entity"),
    "fallOn":          ("afterEvents", "entityFallOnBlock",       "event.entity"),
    "entityInside":    ("afterEvents", "entityEnterBlock",        "event.entity"),
    "neighborChanged": ("afterEvents", "playerPlaceBlock",        "event.player"),
    "onPlace":         ("afterEvents", "playerPlaceBlock",        "event.player"),
    "onRemove":        ("afterEvents", "playerBreakBlock",        "event.player"),
    "playerDestroy":   ("afterEvents", "playerBreakBlock",        "event.player"),
}

_BLOCK_TICK_METHODS = ["randomTick", "tick", "animateTick"]

def _has_block_entity_ticker(unit) -> bool:
    return bool({c.member for c in unit.walk(jt.MethodInvocation)} & {'getTicker', 'createTickerHelper'}) or \
        any(simple_type_name(t) == 'BlockEntityTicker' for t in unit.walk(jt.ReferenceType))

def _mentions_container_menu(unit) -> bool:
    return any('abstractcontainermenu' in i or 'menutype' in i or i in ('createmenu',) for i in identifier_set(unit))

def generate_block_script(java_code, safe_name: str, block_id: str, namespace: str) -> bool:
    unit = as_unit(java_code)
    found_methods = []
    for method_name, (phase, bedrock_event, entity_ref) in _BLOCK_EVENT_METHOD_MAP.items():
        body = method_statements(unit, method_name)
        if body:
            found_methods.append((method_name, phase, bedrock_event, entity_ref, body))

    tick_bodies = []
    for method_name in _BLOCK_TICK_METHODS:
        body = method_statements(unit, method_name)
        if body:
            tick_bodies.append((method_name, body))

    if _has_block_entity_ticker(unit) and not tick_bodies:
        tick_bodies.append(("blockEntityTick", []))

    if _mentions_container_menu(unit):
        STATE.porting_notes.append(
            f"[block] {safe_name}: uses a ContainerMenu / custom GUI. "
            f"Custom GUIs have no direct Bedrock equivalent — consider using block inventory "
            f"components (minecraft:inventory) and reading them via Scripting API, or a FormUI addon."
        )

    static_handlers = _find_static_event_handlers(unit)

    if not found_methods and not tick_bodies and not static_handlers:
        return False

    needs_permutation = _needs_repair_helper(static_handlers)
    needs_system = bool(tick_bodies)
    imports_parts = ["world"]
    if needs_system:
        imports_parts.append("system")
    imports_parts += ["GameMode", "ItemStack"]
    if needs_permutation:
        imports_parts.append("BlockPermutation")
    base_imports = ", ".join(imports_parts)
    script_lines = [f'import {{ {base_imports} }} from "@minecraft/server";', '']

    for method_name, phase, bedrock_event, entity_ref, body in found_methods:
        translated = _translate_use_body(body, namespace, safe_name)
        script_lines += [
            f'// {method_name}() → {bedrock_event}',
            f'world.{phase}.{bedrock_event}.subscribe((event) => {{',
            f'    const block = event.block;',
            f'    if (!block || block.typeId !== "{block_id}") return;',
            f'    const actor = {entity_ref};',
            f'    if (!actor) return;',
        ] + translated + ['});', '']

    if tick_bodies:
        script_lines += [
            f'// Periodic tick behavior ported from: {", ".join(m for m, _ in tick_bodies)}',
            f'// Uses system.runInterval — adjust interval as needed (20 = once per second)',
            f'system.runInterval(() => {{',
            f'    for (const dimName of ["overworld", "nether", "the_end"]) {{',
            f'        const dim = world.getDimension(dimName);',
            f'        // world.afterEvents.blockRandomTick is available in @minecraft/server ≥1.9',
            f'        // For older packs, iterate nearby blocks manually:',
        ]
        for method_name, body in tick_bodies:
            if body:
                translated = _translate_use_body(body, namespace, safe_name)
                script_lines += [f'        // === {method_name} ==='] + [
                    '    ' + l if l.strip() else l for l in translated
                ]
            else:
                script_lines.append(f'        // TODO: {method_name} — fill in block-entity tick logic here')
        script_lines += [
            '    }',
            '}, 20);',
            '',
        ]

        script_lines += [
            f'// Modern alternative: use world.afterEvents.blockRandomTick (requires @minecraft/server ≥1.9):',
            f'// world.afterEvents.blockRandomTick.subscribe((event) => {{',
            f'//     if (event.block.typeId !== "{block_id}") return;',
            f'//     // tick logic here',
            f'// }});',
            '',
        ]

    for _, event_type, phase, bedrock_event, param, body in static_handlers:
        if script_lines[-1] != '':
            script_lines.append('')
        translated = _translate_handler_body(body, event_type, param, unit, namespace, safe_name)
        script_lines += [f'world.{phase}.{bedrock_event}.subscribe(({param}) => {{'] + translated + ['});']

    if _needs_repair_helper(static_handlers):
        script_lines += [''] + _emit_repair_helper()

    out_path = os.path.join(BP_FOLDER, "scripts", f"block_{safe_name}.js")
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(script_lines))

    return True

def _holder_refs(nodes, holder: str) -> List[str]:

    out = []
    for n in iter_nodes(nodes, jt.MemberReference, jt.MethodInvocation):
        if isinstance(n, jt.MemberReference):
            if n.qualifier == holder:
                out.append(n.member)
        elif n.member == 'get' and n.qualifier and n.qualifier.startswith(holder + '.') and n.qualifier.count('.') == 1:
            out.append(n.qualifier.split('.')[1])
    return out

def _three_numbers(args) -> Optional[list]:
    if len(args) >= 3:
        txts = [raw_number_text(a) for a in args[:3]]
        if all(t is not None and all(c.isdigit() or c in '.-' for c in t) for t in txts):
            return txts
    return None

def _as_statements(body) -> list:
    if body is None:
        return []
    if isinstance(body, list):
        return body
    return parse_statements(body) or []

def _translate_use_body(body, namespace: str, safe_name: str) -> list:

    lines = []
    stmts = _as_statements(body)
    sites = list(iter_call_sites(stmts))

    def first(members, pred=None):
        return next((s for s in sites if s[1] in members and (pred is None or pred(s))), None)

    for creator in iter_nodes(stmts, jt.ClassCreator):
        args = creator.arguments or []
        if simple_type_name(creator.type) == 'MobEffectInstance' and len(args) >= 2 \
                and isinstance(args[0], jt.MemberReference) and args[0].qualifier == 'MobEffects' and _int_arg_text(args[1]):
            amp = _int_arg_text(args[2]) if len(args) > 2 else None
            effect_key = args[0].member
            bedrock_effect = _EFFECT_NAME_MAP.get(effect_key, effect_key.lower())
            duration_sec = int(_int_arg_text(args[1])) / 20
            lines.append(f'        player.addEffect("minecraft:{bedrock_effect}", {duration_sec}, {{ amplifier: {int(amp) if amp else 0}, showParticles: true }});')

    for hit in _holder_refs(stmts, 'SoundEvents'):
        bedrock_sound = _SOUND_NAME_MAP.get(hit, "random.pop")
        lines.append(f'        player.dimension.playSound("{bedrock_sound}", player.location);')

    if first(('heal',), lambda s: s[0].endswith('player')) or first(('setHealth',)):
        heal = first(('heal',), lambda s: s[0].endswith('player') and s[2] and _plain_float(s[2][0]) is not None)
        amount = _plain_float(heal[2][0]) if heal else 4.0
        lines.append(f'        const health = player.getComponent("minecraft:health");')
        lines.append(f'        if (health) health.setCurrentValue(Math.min(health.currentValue + {amount}, health.effectiveMax));')

    for _r, member, args in sites:
        if member in ('addFreshEntity', 'summon', 'spawnEntity') and args and isinstance(args[0], jt.ClassCreator):
            entity_id = f"{namespace}:{sanitize_identifier(simple_type_name(args[0].type))}"
            lines.append(f'        player.dimension.spawnEntity("{entity_id}", player.location);')

    if first(('setOnFire',), lambda s: s[0].endswith('player')) or first(('setSecondsOnFire',)):
        fire = first(('setOnFire',), lambda s: s[2] and _int_arg_text(s[2][0])) or \
            first(('setSecondsOnFire',), lambda s: s[2] and _int_arg_text(s[2][0]))
        seconds = int(_int_arg_text(fire[2][0])) if fire else 5
        lines.append(f'        player.setOnFire({seconds});')

    if first(('teleportTo', 'teleport'), lambda s: s[0].endswith('player')):
        tp = first(('teleportTo', 'teleport'), lambda s: _three_numbers(s[2]))
        if tp:
            x, y, z = _three_numbers(tp[2])
            lines.append(f'        player.teleport({{ x: {x}, y: {y}, z: {z} }});')
        else:
            lines.append(f'        // TODO: player.teleport(targetLocation);')

    cd = first(('addCooldown',), lambda s: len(s[2]) >= 2 and isinstance(s[2][0], jt.This) and _int_arg_text(s[2][1]))
    if cd:
        lines.append(f'        player.startItemCooldown("{safe_name}", {int(_int_arg_text(cd[2][1]))});')

    if first(('shrink',), lambda s: s[0].split('.')[-1] in ('itemStack', 'stack') and len(s[2]) == 1 and _int_arg_text(s[2][0]) == '1'):
        lines.append(f'        const inv = player.getComponent("minecraft:inventory");')
        lines.append(f'        if (inv) {{ const slot = inv.container.getSlot(player.selectedSlotIndex); slot.amount = Math.max(0, slot.amount - 1); }}')

    ex = first(('explode', 'createExplosion'), lambda s: len(s[2]) >= 2 and _plain_float(s[2][1]) is not None)
    if ex:
        lines.append(f'        player.dimension.createExplosion(player.location, {_plain_float(ex[2][1])}, {{ breaksBlocks: true }});')

    xp = first(('addXp', 'giveExperiencePoints', 'giveExperience', 'addExperience'), lambda s: s[2] and _int_arg_text(s[2][0]))
    if xp:
        lines.append(f'        player.addExperience({_int_arg_text(xp[2][0])});')

    msg_members = ('sendSystemMessage', 'displayClientMessage', 'sendMessage')
    msg = None
    for _r, member, args in sites:
        if member in msg_members and args:
            first_arg = args[0]
            if isinstance(first_arg, jt.MethodInvocation) and first_arg.qualifier == 'Component' \
                    and first_arg.member in ('literal', 'translatable') and first_arg.arguments:
                first_arg = first_arg.arguments[0]
            text = string_value(first_arg)
            if text and '"' not in text and "'" not in text:
                msg = text
                break
    if msg is not None:
        lines.append(f'        player.sendMessage("{msg}");')
    elif first(msg_members):
        lines.append(f'        // TODO: player.sendMessage("...");')

    sb = first(('setBlockAndUpdate', 'setBlock'), lambda s: len(s[2]) >= 2 and isinstance(s[2][1], jt.MemberReference) and s[2][1].qualifier == 'Blocks')
    if sb:
        lines.append(f'        // TODO: block.setPermutation(BlockPermutation.resolve("minecraft:{sb[2][1].member.lower()}"));')

    sched = first(('scheduleTick', 'scheduleBlockTick'), lambda s: len(s[2]) >= 3 and _int_arg_text(s[2][2]))
    if sched:
        delay = int(_int_arg_text(sched[2][2]))
        lines.append(f'        system.runTimeout(() => {{')
        lines.append(f'            // TODO: scheduled tick logic (originally {delay} game ticks)')
        lines.append(f'        }}, {delay});')

    part = first(('addParticle',), lambda s: s[2] and dotted(s[2][0]))
    if part:
        java_particle = dotted(part[2][0]).split(".")[-1].lower()
        bedrock_particle = JAVA_PARTICLE_MAP.get(java_particle, "minecraft:enchantment_table_particle")
        lines.append(f'        player.dimension.spawnParticle("{bedrock_particle}", player.location);')

    vel = first(('setDeltaMovement',), lambda s: _three_numbers(s[2]))
    if vel:
        x, y, z = _three_numbers(vel[2])
        lines.append(f'        player.applyImpulse({{ x: {x}, y: {y}, z: {z} }});')

    for recv, member, args in sites:
        if recv.endswith('getPersistentData()') and member in ('putInt', 'putFloat', 'putDouble', 'putBoolean', 'putString') \
                and args and string_value(args[0]) and all(c.isalnum() or c == '_' for c in string_value(args[0])):
            lines.append(f'        // TODO: entity.setDynamicProperty("{namespace}:{string_value(args[0])}", value);')
            break

    return lines

_CAP_FIELD_TYPE_MAP: Dict[str, str] = {
    'int': 'number', 'float': 'number', 'double': 'number', 'long': 'number', 'short': 'number', 'byte': 'number',
    'Integer': 'number', 'Float': 'number', 'Double': 'number', 'Long': 'number',
    'boolean': 'boolean', 'Boolean': 'boolean',
    'String': 'string', 'ItemStack': 'string', 'ResourceLocation': 'string', 'UUID': 'string',
}

_CAP_INTERFACES = {'ICapabilityProvider', 'ICapabilitySerializable', 'INBTSerializable', 'IEnergyStorage', 'IFluidHandler'}

def _implements_any(unit, names: Set[str]) -> bool:
    return any(simple_type_name(i) in names for c in unit.classes() for i in (c.implements or []))

def _type_text(t) -> str:

    base = type_name(t)
    args = type_args(t)
    return f"{base}<{', '.join(args)}>" if args else base

def build_inheritance_graph(java_files: Dict[str, str]) -> None:
    STATE.inheritance_graph = {}
    for code in java_files.values():
        for child, parent in JavaAST(code).all_class_extends():
            STATE.inheritance_graph[child] = parent

def resolve_superchain(cls_name: str, max_depth: int = 12) -> List[str]:
    chain = []
    current = cls_name
    seen = set()
    for _ in range(max_depth):
        parent = STATE.inheritance_graph.get(current)
        if not parent or parent in seen:
            break
        chain.append(parent)
        seen.add(parent)
        current = parent
    return chain

def class_extends_any(cls_name: str, targets: Set[str]) -> bool:
    if cls_name in targets:
        return True
    for ancestor in resolve_superchain(cls_name):
        if ancestor in targets:
            return True
    return False

_MIXIN_TARGET_TO_BEDROCK: Dict[str, Tuple[str, str, str]] = {
    "LivingEntity":        ("afterEvents", "entityHurt",              "event.hurtEntity"),
    "Player":              ("afterEvents", "playerInteractWithBlock",  "event.player"),
    "ServerPlayer":        ("afterEvents", "playerInteractWithBlock",  "event.player"),
    "Mob":                 ("afterEvents", "entitySpawn",              "event.entity"),
    "PathfinderMob":       ("afterEvents", "entitySpawn",              "event.entity"),
    "Animal":              ("afterEvents", "entitySpawn",              "event.entity"),
    "Monster":             ("afterEvents", "entitySpawn",              "event.entity"),
    "AbstractArrow":       ("afterEvents", "projectileHitEntity",      "event.projectile"),
    "Arrow":               ("afterEvents", "projectileHitEntity",      "event.projectile"),
    "ThrownPotion":        ("afterEvents", "projectileHitEntity",      "event.projectile"),
    "ItemEntity":          ("afterEvents", "itemStartPickUp",          "event.itemEntity"),
    "BlockEntity":         ("afterEvents", "playerInteractWithBlock",  "event.block"),
    "Level":               ("afterEvents", "worldInitialize",          "event"),
    "ServerLevel":         ("afterEvents", "worldInitialize",          "event"),
    "UseOnContext":        ("afterEvents", "playerInteractWithBlock",  "event"),
    "InteractionHand":     ("afterEvents", "playerInteractWithBlock",  "event"),
}

_INJECT_HEAD_BEDROCK: Dict[str, str] = {
    "tick":          "world.afterEvents.entityHurt",
    "hurt":          "world.afterEvents.entityHurt",
    "die":           "world.afterEvents.entityDie",
    "aiStep":        "world.afterEvents.entitySpawn",
    "interact":      "world.afterEvents.playerInteractWithEntity",
    "interactAt":    "world.afterEvents.playerInteractWithEntity",
    "use":           "world.afterEvents.useItem",
    "attack":        "world.afterEvents.entityHitEntity",
    "performAttack": "world.afterEvents.entityHitEntity",
    "shoot":         "world.afterEvents.projectileHitEntity",
    "onBlockActivated": "world.afterEvents.playerInteractWithBlock",
    "use":           "world.afterEvents.playerInteractWithBlock",
    "playerDestroy": "world.afterEvents.playerBreakBlock",
    "place":         "world.afterEvents.playerPlaceBlock",
    "onRemove":      "world.afterEvents.playerBreakBlock",
    "explode":       "world.afterEvents.explosion",
    "onCraftedBy":   "world.afterEvents.crafted",
    "finishUsingItem": "world.afterEvents.useItem",
    "onEquip":       "world.afterEvents.playerInteractWithBlock",
}

def scan_capabilities(java_files: Dict[str, str], namespace: str) -> None:
    for path, code in java_files.items():
        unit = get_unit(code)
        if not _implements_any(unit, _CAP_INTERFACES):
            continue
        cls_name = extract_class_name(code) or os.path.splitext(os.path.basename(path))[0]
        safe_name = clean_java_artifact_name(cls_name)

        idents = identifier_set(unit)
        is_energy = 'ienergystorage' in idents
        is_fluid = 'ifluidhandler' in idents

        simple_types = {'int', 'float', 'double', 'long', 'boolean', 'String', 'ItemStack', 'ResourceLocation'}
        fields = []
        for f in unit.fields():
            if set(f.modifiers or ()) & {'private', 'protected', 'public'} and type_name(f.type) in simple_types and not f.type.dimensions:
                for d in f.declarators:
                    fields.append((type_name(f.type), d.name))
        if not fields and not is_energy and not is_fluid:
            STATE.porting_notes.append(
                f"[capability] {cls_name}: implements ICapabilityProvider but no simple fields detected — "
                f"convert to Bedrock dynamic properties manually"
            )
            continue

        script_lines = [f'import {{ world }} from "@minecraft/server";', '']

        if is_energy:
            script_lines += [f'// dynamic property "{namespace}:{safe_name}_energy" (number, default 0)', '']
        if is_fluid:
            script_lines += [f'// dynamic property "{namespace}:{safe_name}_fluid_amount" (number, default 0)',
                             f'// dynamic property "{namespace}:{safe_name}_fluid_type" (string, default "minecraft:water")', '']

        if is_energy:
            script_lines += [
                f'function receiveEnergy(entity, amount, simulate = false) {{',
                f'    let current = entity.getDynamicProperty("{namespace}:{safe_name}_energy") || 0;',
                f'    let newAmount = Math.min(current + amount, 1000000); // Assuming max capacity',
                f'    if (!simulate) entity.setDynamicProperty("{namespace}:{safe_name}_energy", newAmount);',
                f'    return newAmount - current;',
                f'}}',
                '',
                f'function extractEnergy(entity, amount, simulate = false) {{',
                f'    let current = entity.getDynamicProperty("{namespace}:{safe_name}_energy") || 0;',
                f'    let extracted = Math.min(current, amount);',
                f'    if (!simulate) entity.setDynamicProperty("{namespace}:{safe_name}_energy", current - extracted);',
                f'    return extracted;',
                f'}}',
                '',
                f'function getEnergyStored(entity) {{',
                f'    return entity.getDynamicProperty("{namespace}:{safe_name}_energy") || 0;',
                f'}}',
                '',
            ]

        if is_fluid:
            script_lines += [
                f'function fill(entity, fluidStack, simulate = false) {{',
                f'    let currentAmount = entity.getDynamicProperty("{namespace}:{safe_name}_fluid_amount") || 0;',
                f'    let currentType = entity.getDynamicProperty("{namespace}:{safe_name}_fluid_type") || "minecraft:water";',
                f'    if (currentAmount > 0 && fluidStack.type !== currentType) return 0;',
                f'    let space = 1000 - currentAmount; // Assuming capacity 1000',
                f'    let filled = Math.min(space, fluidStack.amount);',
                f'    if (!simulate) {{',
                f'        entity.setDynamicProperty("{namespace}:{safe_name}_fluid_amount", currentAmount + filled);',
                f'        entity.setDynamicProperty("{namespace}:{safe_name}_fluid_type", fluidStack.type);',
                f'    }}',
                f'    return filled;',
                f'}}',
                '',
                f'function drain(entity, amount, simulate = false) {{',
                f'    let currentAmount = entity.getDynamicProperty("{namespace}:{safe_name}_fluid_amount") || 0;',
                f'    let drained = Math.min(currentAmount, amount);',
                f'    if (!simulate) {{',
                f'        entity.setDynamicProperty("{namespace}:{safe_name}_fluid_amount", currentAmount - drained);',
                f'    }}',
                f'    return {{ amount: drained, type: entity.getDynamicProperty("{namespace}:{safe_name}_fluid_type") || "minecraft:water" }};',
                f'}}',
                '',
                f'function getFluidAmount(entity) {{',
                f'    return entity.getDynamicProperty("{namespace}:{safe_name}_fluid_amount") || 0;',
                f'}}',
                '',
            ]

        for java_type, field_name in fields:
            bedrock_type = _CAP_FIELD_TYPE_MAP.get(java_type, "string")
            script_lines.append(f'// dynamic property "{namespace}:{safe_name}_{field_name}" ({bedrock_type}) - no registration needed')
        if fields:
            script_lines.append('')

        public_methods = [m for m in unit.methods() if 'public' in (m.modifiers or ())]
        getter_methods = [m.name for m in public_methods if m.return_type is not None and m.name.startswith('get') and not m.parameters]
        setter_methods = [m.name for m in public_methods if m.return_type is None and m.name.startswith('set') and len(m.parameters or []) == 1]

        if getter_methods or setter_methods:
            script_lines += [
                f'function getCapability(entity, key) {{',
                f'    return entity.getDynamicProperty("{namespace}:{safe_name}_" + key);',
                f'}}',
                '',
                f'function setCapability(entity, key, value) {{',
                f'    entity.setDynamicProperty("{namespace}:{safe_name}_" + key, value);',
                f'}}',
                '',
            ]

        out_path = os.path.join(BP_FOLDER, "scripts", f"cap_{safe_name}.js")
        os.makedirs(os.path.dirname(out_path), exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as f:
            f.write("\n".join(script_lines))

_CHANNEL_FACTORIES = (
    ('SimpleChannel', ('newSimpleChannel', 'create')),
    ('ChannelBuilder', ('named',)),
    ('NetworkRegistry', ('newSimpleChannel',)),
)

def _channel_name(unit) -> Optional[str]:

    for holder, members in _CHANNEL_FACTORIES:
        for call in unit.walk(jt.MethodInvocation):
            if call.qualifier == holder and call.member in members and call.arguments:
                vals = _resource_location_args(call.arguments[0])
                if vals:
                    return vals[0]
                first = next((string_value(l) for l in iter_nodes(call.arguments[0], jt.Literal)
                              if string_value(l) is not None), None)
                if first is not None and isinstance(call.arguments[0], jt.ClassCreator):
                    return first
    return None

def _packet_class_refs(unit) -> List[str]:
    out = []
    for call in unit.walk(jt.MethodInvocation):
        args = call.arguments or []
        idx = None
        if call.member == 'registerMessage' and len(args) >= 2:
            idx = 1
        elif call.member == 'messageBuilder' and args:
            idx = 0
        elif call.member in ('toClient', 'toServer') and (call.qualifier or '').endswith('play') and args:
            idx = 0
        if idx is not None and isinstance(args[idx], jt.ClassReference):
            out.append(type_name(args[idx].type))
    return out

def _classify_packet_direction(pcode: str) -> str:
    unit = get_unit(pcode)
    for m in unit.methods('handle'):
        if m.return_type is not None:
            continue
        text = ' '.join(type_name(p.type) + ' ' + p.name for p in m.parameters or [])
        if any(k in text for k in ('Level', 'ServerLevel', 'Player', 'ServerPlayer')):
            return 'server'
    for m in unit.methods('handle'):
        if m.return_type is not None:
            continue
        text = ' '.join(type_name(p.type) + ' ' + p.name for p in m.parameters or [])
        if 'Minecraft' in text:
            return 'client'
    idents = {t.value for t in unit.tokens if isinstance(t, _jtok.Identifier)}
    if idents & {'ServerPayloadHandler', 'PlayPayloadHandler'}:
        return 'server'
    if 'ClientPayloadHandler' in idents:
        return 'client'
    return 'unknown'

def _extract_packet_fields(pcode: str) -> list:
    fields = []
    for f in get_unit(pcode).fields():
        if set(f.modifiers or ()) & {'private', 'public', 'protected', 'final'}:
            for d in f.declarators:
                fields.append((_type_text(f.type), d.name))
    skip = {'LOGGER', 'HANDLER', 'TYPE', 'STREAM_CODEC', 'ID', 'serialVersionUID'}
    return [(ft, fn) for ft, fn in fields if fn not in skip][:12]

def scan_networking(java_files: Dict[str, str], namespace: str) -> None:
    channel_files: dict = {}
    packet_classes: dict = {}

    for path, code in java_files.items():
        unit = get_unit(code)
        channel = _channel_name(unit)
        if channel is not None:
            channel_files[path] = (code, channel)

        if _implements_any(unit, {'CustomPacketPayload', 'FriendlyByteBuf'}):
            cls_name = extract_class_name(code)
            if cls_name:
                packet_classes[cls_name] = code

        for pcls in _packet_class_refs(unit):
            if pcls not in packet_classes:
                packet_classes[pcls] = ""

    if not channel_files and not packet_classes:
        return

    serverbound_lines: list = [
        f'import {{ world, system }} from "@minecraft/server";', ''
    ]
    clientbound_lines: list = []

    for pcls, pcode in packet_classes.items():
        safe = sanitize_identifier(pcls)
        direction = _classify_packet_direction(pcode) if pcode else 'unknown'
        fields = _extract_packet_fields(pcode) if pcode else []

        field_lines: list = ['    const data = JSON.parse(event.message);']
        for ftype, fname in fields:
            btype = _CAP_FIELD_TYPE_MAP.get(ftype, 'any')
            cast = '' if btype in ('any', 'string') else (
                'Number(' + f'data.{fname}' + ')'
                if btype == 'number' else
                'Boolean(' + f'data.{fname}' + ')'
                if btype == 'boolean' else
                f'data.{fname}'
            )
            field_lines.append(
                f'    const {fname} = {cast if cast else f"data.{fname}"};')

        handle_body = _extract_method_body(pcode, 'handle') if pcode else ''
        handle_comment = []
        if handle_body:
            for line in handle_body.strip().splitlines()[:8]:
                handle_comment.append(f'    // java: {line.strip()}')

        if direction in ('server', 'unknown'):
            event_id = f'{namespace}:{safe}'
            serverbound_lines += [
                f'// Packet: {pcls}  [{direction}-bound]',
                f'system.afterEvents.scriptEventReceive.subscribe((event) => {{',
                f'    if (event.id !== "{event_id}") return;',
            ] + field_lines + [
                f'    const player = [...world.getAllPlayers()].find(p => p.name === data.sender);',
                f'    if (!player) return;',
            ] + handle_comment + [
                f'    // TODO: implement server logic for {pcls}',
                f'}});',
                '',
            ]
        else:

            event_id = f'client:{namespace}:{safe}'
            clientbound_lines += [
                f'// Client-bound Packet: {pcls}',
                f'// Bedrock has no direct client-side scripting API equivalent.',
                f'// This handler re-emits the data as a "client:" prefixed script event',
                f'// that the UI layer can subscribe to via system.afterEvents.scriptEventReceive.',
                f'system.afterEvents.scriptEventReceive.subscribe((event) => {{',
                f'    if (event.id !== "{event_id}") return;',
            ] + field_lines + [
                f'    // Re-broadcast to all players (or filter by data.target)',
                f'    for (const p of world.getAllPlayers()) {{',
                f'        p.runCommand(`scriptevent {namespace}:{safe}_ack ${{JSON.stringify(data)}}`);',
                f'    }}',
                f'}});',
                '',
            ]

        STATE.porting_notes.append(
            f"[network] {direction.upper()} packet '{pcls}' → "
            f"scriptEventReceive id='{event_id}'.  "
            f"Java sender must call: world.events.server.execute(() -> "
            f"MinecraftServer#execute('/scriptevent {event_id} {{...}}'))."
        )

    all_lines = serverbound_lines
    if clientbound_lines:
        all_lines += ['// ── Client-bound packets ──', ''] + clientbound_lines

    if all_lines[-1] != '':
        all_lines.append('')

    out_path = os.path.join(BP_FOLDER, "scripts", "network_packets.js")
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(all_lines))

_CLIENT_RENDERER_BASES = {
    "EntityRenderer", "MobRenderer", "LivingEntityRenderer",
    "BlockEntityRenderer", "ParticleEngine", "GameRenderer",
    "ItemRenderer", "FontRenderer", "GlStateManager",
    "RenderType", "VertexConsumer", "PoseStack",
    "ShaderInstance", "PostChain",
}

_CLIENT_ONLY_IMPORTS = {
    "net.minecraft.client", "com.mojang.blaze3d", "net.minecraftforge.client",
    "net.neoforged.neoforge.client", "net.fabricmc.fabric.api.client",
}

_CLIENT_ONLY_IMPORT_ORDER = ("net.minecraft.client", "com.mojang.blaze3d", "net.minecraftforge.client",
                             "net.neoforged.neoforge.client", "net.fabricmc.fabric.api.client")

def detect_client_only(java_code: str, cls_name: str) -> Optional[str]:
    unit = get_unit(java_code)
    imports = [i.path for i in (unit.tree.imports if unit.tree is not None else []) if not i.static]
    for imp in _CLIENT_ONLY_IMPORT_ORDER:
        if any(path.startswith(imp) for path in imports):
            return f"uses client-only import package {imp}"

    parents = JavaAST(java_code).all_class_extends()
    if parents:
        base = parents[0][1]
        if base in _CLIENT_RENDERER_BASES:
            return f"extends {base}"
        for ancestor in resolve_superchain(base):
            if ancestor in _CLIENT_RENDERER_BASES:
                return f"extends {base} (which extends {ancestor})"

    for ann in unit.annotations():
        value = dotted(annotation_args(ann).get('value'))
        if (ann.name == 'OnlyIn' and value == 'Dist.CLIENT') or (ann.name == 'Environment' and value == 'EnvType.CLIENT'):
            return "@OnlyIn(Dist.CLIENT) annotation"

    return None

def scan_client_classes(java_files: Dict[str, str]) -> None:
    for path, code in java_files.items():
        cls_name = extract_class_name(code) or os.path.splitext(os.path.basename(path))[0]
        reason = detect_client_only(code, cls_name)
        if reason:
            STATE.porting_notes.append(
                f"[client-only] {cls_name} ({reason}): "
                f"client-side rendering has no Bedrock equivalent. "
                f"Textures/models are handled by the RP; custom shaders and render layers cannot be ported."
            )

def write_porting_notes() -> None:
    if not STATE.porting_notes:
        return

    out_path = "PORTING_NOTES.txt"
    categories = {"mixin": [], "capability": [], "network": [], "client-only": [], "other": []}
    for note in STATE.porting_notes:
        matched = False
        for cat in categories:
            if note.startswith(f"[{cat}]"):
                categories[cat].append(note)
                matched = True
                break
        if not matched:
            categories["other"].append(note)
    lines = [
        "ModMorpher — Porting Notes",
        "=" * 60,
        "",
        "These items could not be automatically converted and require",
        "manual attention before the addon will be fully functional.",
        "",
    ]
    section_titles = {
        "mixin": "Mixin Injections",
        "capability": "Capability Providers",
        "network": "Network Packets",
        "client-only": "Client-Side Rendering",
        "other": "Other",
    }
    for cat, notes in categories.items():
        if not notes:
            continue
        lines += [section_titles[cat], "-" * len(section_titles[cat])]
        for note in notes:
            body = note[len(f'[{cat}]'):].lstrip()
            lines.append(f"  {body}")
        lines.append("")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

def _needs_repair_helper(handlers: list) -> bool:
    return any(any(c.member == 'repairState' for c in iter_calls(_as_statements(body)))
               for _, _, _, _, _, body in handlers)

def _emit_repair_helper() -> list:
    return [
        'function repairBlockId(typeId) {',
        '    const path = typeId.replace(/^minecraft:/, "");',
        '    let modified = path',
        '        .replace(/^damaged_/, "chipped_")',
        '        .replace(/_damaged$/, "_chipped")',
        '        .replace(/_damaged_/, "_chipped_");',
        '    if (modified !== path) return "minecraft:" + modified;',
        '    for (const word of ["cracked", "mossy", "polished", "chiseled", "smooth", "cut", "chipped"]) {',
        '        modified = path',
        '            .replace(new RegExp("^" + word + "_"), "")',
        '            .replace(new RegExp("_" + word + "$"), "")',
        '            .replace(new RegExp("_" + word + "_"), "_");',
        '        if (modified !== path) return "minecraft:" + modified;',
        '    }',
        '    return null;',
        '}',
    ]

def generate_scripting_stub(java_code, safe_name: str, item_id: str, namespace: str) -> bool:
    use_body        = method_statements(java_code, "use")
    hurt_body       = method_statements(java_code, "hurtEnemy")
    tick_body       = method_statements(java_code, "inventoryTick")
    finish_body     = method_statements(java_code, "finishUsingItem")
    crafted_body    = method_statements(java_code, "onCraftedBy")
    static_handlers = _find_static_event_handlers(java_code)

    has_instance_methods = any([use_body, hurt_body, tick_body, finish_body, crafted_body])
    if not has_instance_methods and not static_handlers:
        return False

    needs_permutation = _needs_repair_helper(static_handlers)
    needs_system      = bool(tick_body)
    imports_parts     = ["world"]
    if needs_system:
        imports_parts.append("system")
    imports_parts += ["GameMode", "ItemStack"]
    if needs_permutation:
        imports_parts.append("BlockPermutation")
    base_imports = ", ".join(imports_parts)
    script_lines = [f'import {{ {base_imports} }} from "@minecraft/server";', '']

    if has_instance_methods:
        script_lines += [
            f'const COMPONENT_ID = "{namespace}:{safe_name}_use";',
            '',
            'class UseHandler {',
        ]
        if use_body:
            translated = _translate_use_body(use_body, namespace, safe_name)
            script_lines += ['    onUse(event) {', '        const player = event.source;', '        if (!player) return;'] + translated + ['    }']
        if hurt_body:
            translated = _translate_use_body(hurt_body, namespace, safe_name)
            script_lines += ['    onHitEntity(event) {', '        const player = event.attackingEntity;', '        if (!player) return;'] + translated + ['    }']
        if finish_body:

            translated = _translate_use_body(finish_body, namespace, safe_name)
            script_lines += ['    onConsume(event) {', '        const player = event.source;', '        if (!player) return;'] + translated + ['    }']
        if crafted_body:
            script_lines += [
                '    // onCraftedBy → subscribe via world.afterEvents.crafted instead of a component method',
                '    // See the world.afterEvents.crafted.subscribe block below.',
            ]
        if tick_body:

            script_lines += [
                '    // inventoryTick has no direct item-component callback.',
                '    // Handled by the system.runInterval block below.',
            ]
        script_lines += [
            '}',
            '',
            'world.beforeEvents.worldInitialize.subscribe((e) => {',
            f'    e.itemComponentRegistry.registerCustomComponent(COMPONENT_ID, new UseHandler());',
            '});',
            '',
        ]

    if tick_body:
        translated = _translate_use_body(tick_body, namespace, safe_name)
        script_lines += [
            f'// inventoryTick() → scan every player inventory each tick',
            f'system.runInterval(() => {{',
            f'    for (const player of world.getAllPlayers()) {{',
            f'        const inv = player.getComponent("minecraft:inventory")?.container;',
            f'        if (!inv) continue;',
            f'        for (let i = 0; i < inv.size; i++) {{',
            f'            const item = inv.getItem(i);',
            f'            if (!item || item.typeId !== "{item_id}") continue;',
        ] + ['            ' + l.strip() for l in translated] + [
            '        }',
            '    }',
            '}, 1);',
            '',
        ]

    if crafted_body:
        translated = _translate_use_body(crafted_body, namespace, safe_name)
        script_lines += [
            f'// onCraftedBy() → crafted event',
            f'world.afterEvents.crafted.subscribe((event) => {{',
            f'    if (!event.craftingSlots) return;',
            f'    const result = event.craftingSlots[0]?.item;',
            f'    if (!result || result.typeId !== "{item_id}") return;',
            f'    const player = event.player;',
            f'    if (!player) return;',
        ] + translated + ['});', '']

    for _, event_type, phase, bedrock_event, param, body in static_handlers:
        if script_lines and script_lines[-1] != '':
            script_lines.append('')
        translated = _translate_handler_body(body, event_type, param, java_code, namespace, safe_name)
        script_lines += [f'world.{phase}.{bedrock_event}.subscribe(({param}) => {{'] + translated + ['});']

    if _needs_repair_helper(static_handlers):
        script_lines += [''] + _emit_repair_helper()

    out_path = os.path.join(BP_FOLDER, "scripts", f"{safe_name}.js")
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(script_lines))

    return True

def _any_ident(idents: Set[str], *needles: str) -> bool:
    return any(n in i for i in idents for n in needles)

def convert_java_item_full(java_code: str, java_path: str, namespace: str):
    cls = extract_class_name(java_code) or os.path.splitext(os.path.basename(java_path))[0]
    safe_name = clean_java_artifact_name(cls)
    item_id = f"{namespace}:{safe_name}"
    unit = get_unit(java_code)
    calls = list(unit.walk(jt.MethodInvocation))
    idents = identifier_set(unit)
    parents = _class_parents_lower(unit)
    pairs = _name_value_pairs(unit)

    max_stack = 64
    v = _first_call_int(unit, lambda n: n.lower().endswith(('maxstacksize', 'stacksto')))
    if v is not None:
        max_stack = v
    durability = 0
    v = _first_call_int(unit, lambda n: n.lower().endswith(('maxdamage', 'durability', 'defaultdurability')))
    if v is not None:
        durability = v
    components = {
        "minecraft:icon": {"texture": find_best_texture_match(safe_name, "items")},
        "minecraft:max_stack_size": max_stack,
    }
    if durability > 0:
        components["minecraft:durability"] = {"max_durability": durability}
    is_food = bool(_any_ident(idents, 'foodproperties', 'nutrition', 'saturation', 'isfood')
                   or any(c.member == 'food' for c in calls) or parents & {'itemfood', 'bowlfooditem'})
    if is_food:
        nutrition = 4
        saturation = 0.3
        v = _first_call_int(unit, lambda n: 'nutrition' in n.lower())
        if v is not None:
            nutrition = v
        for c in calls:
            if c.member.lower().startswith('saturation') and c.arguments and _unsigned_number(c.arguments[0]) is not None:
                saturation = _unsigned_number(c.arguments[0])
                break
        components["minecraft:food"] = {
            "nutrition": nutrition,
            "saturation_modifier": saturation,
            "can_always_eat": _any_ident(idents, 'alwayseat')
        }
        components["minecraft:use_animation"] = "eat"
        components["minecraft:use_duration"] = 32
    slot_members = {r.member.upper() for r in unit.walk(jt.MemberReference)
                    if (r.qualifier or '').split('.')[-1] == 'EquipmentSlot'}
    armor_slot = None
    if 'armoritem' in idents or 'HEAD' in slot_members:
        armor_slot = "slot.armor.head"
    elif 'CHEST' in slot_members:
        armor_slot = "slot.armor.chest"
    elif 'LEGS' in slot_members:
        armor_slot = "slot.armor.legs"
    elif 'FEET' in slot_members:
        armor_slot = "slot.armor.feet"
    if armor_slot:
        components["minecraft:wearable"] = {"protection": 3, "slot": armor_slot}
    if _any_ident(idents, 'sworditem', 'tiereditem') or any('sword' in p for p in parents):
        atk = 4.0
        for name, val in pairs:
            if name.lower() == 'attackdamage' and _unsigned_number(val) is not None:
                atk = _unsigned_number(val)
                break
        components["minecraft:damage"] = int(atk)
        components["minecraft:hand_equipped"] = True
    doc = {
        "format_version": BP_ITEM_FORMAT_VERSION,
        "minecraft:item": {
            "description": {
                "identifier": item_id,
                "menu_category": {"category": "items"}
            },
            "components": components
        }
    }
    enchant_value = 0
    if _any_ident(idents, 'enchantmentcategory', 'getenchantmentvalue', 'enchantmentvalue', 'enchantable'):
        enchant_value = 10
        for m in unit.methods():
            if m.name in ('enchantmentValue', 'getEnchantmentValue') and not m.parameters:
                ret = next(iter(iter_nodes(m.body, jt.ReturnStatement)), None)
                if ret is not None and _int_arg_text(ret.expression):
                    enchant_value = int(_int_arg_text(ret.expression))
                    break
    if enchant_value > 0:
        ench_slot = "all"
        if _any_ident(idents, 'sworditem', 'axeitem', 'weapon'):
            ench_slot = "weapon"
        elif _any_ident(idents, 'armoritem', 'bootsitem', 'helmitem', 'armor'):
            ench_slot = "armor"
        elif _any_ident(idents, 'pickaxeitem', 'shovelitem', 'hoeitem', 'tool'):
            ench_slot = "tool"
        components["minecraft:enchantable"] = {"value": enchant_value, "slot": ench_slot}
    if _any_ident(idents, 'isfoil', 'hasglint', 'isenchanted'):
        components["minecraft:glint"] = True
    has_instance_use = any(
        m.name in ('use', 'hurtEnemy', 'inventoryTick') and 'Override' in annotation_names(m)
        and 'public' in (m.modifiers or ())
        for m in unit.methods())
    has_static_handlers = bool(_find_static_event_handlers(unit))
    if has_instance_use or has_static_handlers:
        stub_written = generate_scripting_stub(java_code, safe_name, item_id, namespace)
        if stub_written and has_instance_use:
            components["minecraft:custom_components"] = [f"{namespace}:{safe_name}_use"]
    out_path = os.path.join(BP_FOLDER, "items", f"{safe_name}.json")
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    safe_write_json(out_path, doc)
JAVA_RECIPE_ITEM_MAP = {
    "minecraft:crafting_table": "minecraft:crafting_table",
    "minecraft:furnace": "minecraft:furnace",
    "minecraft:smithing_table": "minecraft:smithing_table",
}
def convert_java_recipe(recipe_data: dict, namespace: str) -> Optional[dict]:
    rtype = recipe_data.get("type", "")
    if "crafting_shaped" in rtype:
        pattern = recipe_data.get("pattern", [])
        key_map = recipe_data.get("key", {})
        result = recipe_data.get("result", {})
        result_item = result.get("item", result) if isinstance(result, dict) else result
        if ":" in str(result_item):
            ns, itm = str(result_item).split(":", 1)
            if ns != "minecraft":
                result_item = f"{namespace}:{sanitize_identifier(itm)}"
        count = result.get("count", 1) if isinstance(result, dict) else 1
        bedrock_key = {}
        for char, ingredient in key_map.items():
            item = ingredient.get("item", "") if isinstance(ingredient, dict) else ingredient
            if isinstance(item, list):
                item = item[0].get("item", "") if item else ""
            bedrock_key[char] = {"item": item}
        return {
            "format_version": BP_RECIPE_FORMAT_VERSION,
            "minecraft:recipe_shaped": {
                "description": {"identifier": f"{namespace}:{sanitize_identifier(str(result_item).split(':')[-1])}_shaped"},
                "tags": ["crafting_table"],
                "pattern": pattern,
                "key": bedrock_key,
                "result": {"item": result_item, "count": count}
            }
        }
    elif "crafting_shapeless" in rtype:
        ingredients = recipe_data.get("ingredients", [])
        result = recipe_data.get("result", {})
        result_item = result.get("item", result) if isinstance(result, dict) else result
        if ":" in str(result_item):
            ns, itm = str(result_item).split(":", 1)
            if ns != "minecraft":
                result_item = f"{namespace}:{sanitize_identifier(itm)}"
        count = result.get("count", 1) if isinstance(result, dict) else 1
        bedrock_ingredients = []
        for ing in ingredients:
            item = ing.get("item", "") if isinstance(ing, dict) else ing
            if isinstance(item, list):
                item = item[0].get("item", "") if item else ""
            bedrock_ingredients.append({"item": item})
        return {
            "format_version": BP_RECIPE_FORMAT_VERSION,
            "minecraft:recipe_shapeless": {
                "description": {"identifier": f"{namespace}:{sanitize_identifier(str(result_item).split(':')[-1])}_shapeless"},
                "tags": ["crafting_table"],
                "ingredients": bedrock_ingredients,
                "result": {"item": result_item, "count": count}
            }
        }
    elif "smelting" in rtype or "smoking" in rtype or "blasting" in rtype:
        ingredient = recipe_data.get("ingredient", {})
        item = ingredient.get("item", "") if isinstance(ingredient, dict) else ingredient
        result = recipe_data.get("result", "")
        if ":" in str(result):
            ns, itm = str(result).split(":", 1)
            if ns != "minecraft":
                result = f"{namespace}:{sanitize_identifier(itm)}"
        cook_time = recipe_data.get("cookingtime", 200) / 20
        return {
            "format_version": BP_RECIPE_FORMAT_VERSION,
            "minecraft:recipe_furnace": {
                "description": {"identifier": f"{namespace}:{sanitize_identifier(str(result).split(':')[-1])}_furnace"},
                "tags": ["furnace", "smoker", "blast_furnace"],
                "input": {"item": item},
                "output": str(result)
            }
        }
    return None
def process_recipes_from_jar(jar_path: str, namespace: str):
    out_base = os.path.join(BP_FOLDER, "recipes")
    os.makedirs(out_base, exist_ok=True)
    count = 0
    with zipfile.ZipFile(jar_path, "r") as jar:
        for name in jar.namelist():
            lower = name.lower()
            if "/recipes/" not in lower or not lower.endswith(".json"):
                continue
            try:
                with jar.open(name) as f:
                    data = json.loads(f.read().decode("utf-8"))
                bedrock = convert_java_recipe(data, namespace)
                if not bedrock:
                    continue
                fname = sanitize_filename_keep_ext(os.path.basename(name))
                out_path = os.path.join(out_base, fname)
                safe_write_json(out_path, bedrock)
                count += 1
            except Exception as e:
                _warn(f"[recipe] Failed to convert {name}: {e}")
JAVA_TO_BEDROCK_BLOCK = {
    "minecraft:air": "minecraft:air",
    "minecraft:cave_air": "minecraft:air",
    "minecraft:void_air": "minecraft:air",
    "minecraft:stone": "minecraft:stone",
    "minecraft:granite": "minecraft:stone",
    "minecraft:polished_granite": "minecraft:stone",
    "minecraft:diorite": "minecraft:stone",
    "minecraft:polished_diorite": "minecraft:stone",
    "minecraft:andesite": "minecraft:stone",
    "minecraft:polished_andesite": "minecraft:stone",
    "minecraft:cobblestone": "minecraft:cobblestone",
    "minecraft:mossy_cobblestone": "minecraft:mossy_cobblestone",
    "minecraft:stone_bricks": "minecraft:stonebrick",
    "minecraft:mossy_stone_bricks": "minecraft:stonebrick",
    "minecraft:cracked_stone_bricks": "minecraft:stonebrick",
    "minecraft:chiseled_stone_bricks": "minecraft:stonebrick",
    "minecraft:infested_stone": "minecraft:stone",
    "minecraft:gravel": "minecraft:gravel",
    "minecraft:sand": "minecraft:sand",
    "minecraft:red_sand": "minecraft:sand",
    "minecraft:sandstone": "minecraft:sandstone",
    "minecraft:smooth_sandstone": "minecraft:sandstone",
    "minecraft:chiseled_sandstone": "minecraft:sandstone",
    "minecraft:red_sandstone": "minecraft:red_sandstone",
    "minecraft:dirt": "minecraft:dirt",
    "minecraft:coarse_dirt": "minecraft:dirt",
    "minecraft:podzol": "minecraft:podzol",
    "minecraft:grass_block": "minecraft:grass",
    "minecraft:mycelium": "minecraft:mycelium",
    "minecraft:oak_log": "minecraft:log",
    "minecraft:spruce_log": "minecraft:log",
    "minecraft:birch_log": "minecraft:log",
    "minecraft:jungle_log": "minecraft:log",
    "minecraft:acacia_log": "minecraft:log2",
    "minecraft:dark_oak_log": "minecraft:log2",
    "minecraft:oak_planks": "minecraft:planks",
    "minecraft:spruce_planks": "minecraft:planks",
    "minecraft:birch_planks": "minecraft:planks",
    "minecraft:jungle_planks": "minecraft:planks",
    "minecraft:acacia_planks": "minecraft:planks",
    "minecraft:dark_oak_planks": "minecraft:planks",
    "minecraft:oak_leaves": "minecraft:leaves",
    "minecraft:spruce_leaves": "minecraft:leaves",
    "minecraft:birch_leaves": "minecraft:leaves",
    "minecraft:jungle_leaves": "minecraft:leaves",
    "minecraft:acacia_leaves": "minecraft:leaves2",
    "minecraft:dark_oak_leaves": "minecraft:leaves2",
    "minecraft:coal_ore": "minecraft:coal_ore",
    "minecraft:iron_ore": "minecraft:iron_ore",
    "minecraft:gold_ore": "minecraft:gold_ore",
    "minecraft:diamond_ore": "minecraft:diamond_ore",
    "minecraft:emerald_ore": "minecraft:emerald_ore",
    "minecraft:lapis_ore": "minecraft:lapis_ore",
    "minecraft:redstone_ore": "minecraft:redstone_ore",
    "minecraft:nether_quartz_ore": "minecraft:quartz_ore",
    "minecraft:bricks": "minecraft:brick_block",
    "minecraft:nether_bricks": "minecraft:nether_brick",
    "minecraft:red_nether_bricks": "minecraft:red_nether_brick",
    "minecraft:obsidian": "minecraft:obsidian",
    "minecraft:bedrock": "minecraft:bedrock",
    "minecraft:water": "minecraft:water",
    "minecraft:lava": "minecraft:lava",
    "minecraft:glass": "minecraft:glass",
    "minecraft:glowstone": "minecraft:glowstone",
    "minecraft:netherrack": "minecraft:netherrack",
    "minecraft:soul_sand": "minecraft:soul_sand",
    "minecraft:soul_soil": "minecraft:soul_sand",
    "minecraft:magma_block": "minecraft:magma",
    "minecraft:ice": "minecraft:ice",
    "minecraft:packed_ice": "minecraft:packed_ice",
    "minecraft:snow_block": "minecraft:snow",
    "minecraft:clay": "minecraft:clay",
    "minecraft:terracotta": "minecraft:hardened_clay",
    "minecraft:white_terracotta": "minecraft:stained_hardened_clay",
    "minecraft:chest": "minecraft:chest",
    "minecraft:trapped_chest": "minecraft:trapped_chest",
    "minecraft:crafting_table": "minecraft:crafting_table",
    "minecraft:furnace": "minecraft:furnace",
    "minecraft:bookshelf": "minecraft:bookshelf",
    "minecraft:spawner": "minecraft:mob_spawner",
    "minecraft:tnt": "minecraft:tnt",
    "minecraft:torch": "minecraft:torch",
    "minecraft:wall_torch": "minecraft:torch",
    "minecraft:ladder": "minecraft:ladder",
    "minecraft:iron_bars": "minecraft:iron_bars",
    "minecraft:glass_pane": "minecraft:glass_pane",
    "minecraft:vine": "minecraft:vine",
    "minecraft:cobweb": "minecraft:web",
    "minecraft:hay_block": "minecraft:hay_block",
    "minecraft:sponge": "minecraft:sponge",
    "minecraft:prismarine": "minecraft:prismarine",
    "minecraft:sea_lantern": "minecraft:sea_lantern",
    "minecraft:dark_prismarine": "minecraft:prismarine",
    "minecraft:prismarine_bricks": "minecraft:prismarine",
    "minecraft:purpur_block": "minecraft:purpur_block",
    "minecraft:purpur_pillar": "minecraft:purpur_block",
    "minecraft:end_stone": "minecraft:end_stone",
    "minecraft:end_stone_bricks": "minecraft:end_bricks",
    "minecraft:end_rod": "minecraft:end_rod",
    "minecraft:shulker_box": "minecraft:undyed_shulker_box",
    "minecraft:barrel": "minecraft:barrel",
    "minecraft:campfire": "minecraft:campfire",
    "minecraft:lantern": "minecraft:lantern",
    "minecraft:soul_lantern": "minecraft:soul_lantern",
    "minecraft:beehive": "minecraft:beehive",
    "minecraft:bee_nest": "minecraft:bee_nest",
    "minecraft:honey_block": "minecraft:honey_block",
    "minecraft:honeycomb_block": "minecraft:honeycomb_block",
    "minecraft:target": "minecraft:target",
    "minecraft:ancient_debris": "minecraft:ancient_debris",
    "minecraft:nether_gold_ore": "minecraft:nether_gold_ore",
    "minecraft:crimson_nylium": "minecraft:crimson_nylium",
    "minecraft:warped_nylium": "minecraft:warped_nylium",
    "minecraft:crimson_stem": "minecraft:crimson_stem",
    "minecraft:warped_stem": "minecraft:warped_stem",
    "minecraft:shroomlight": "minecraft:shroomlight",
    "minecraft:blackstone": "minecraft:blackstone",
    "minecraft:gilded_blackstone": "minecraft:gilded_blackstone",
    "minecraft:crying_obsidian": "minecraft:crying_obsidian",
    "minecraft:respawn_anchor": "minecraft:respawn_anchor",
    "minecraft:calcite": "minecraft:calcite",
    "minecraft:tuff": "minecraft:tuff",
    "minecraft:amethyst_block": "minecraft:amethyst_block",
    "minecraft:budding_amethyst": "minecraft:budding_amethyst",
    "minecraft:deepslate": "minecraft:deepslate",
    "minecraft:cobbled_deepslate": "minecraft:cobbled_deepslate",
    "minecraft:deepslate_bricks": "minecraft:deepslate_bricks",
    "minecraft:deepslate_tiles": "minecraft:deepslate_tiles",
    "minecraft:reinforced_deepslate": "minecraft:reinforced_deepslate",
    "minecraft:mud": "minecraft:mud",
    "minecraft:packed_mud": "minecraft:packed_mud",
    "minecraft:mud_bricks": "minecraft:mud_bricks",
    "minecraft:mangrove_log": "minecraft:mangrove_log",
    "minecraft:mangrove_planks": "minecraft:mangrove_planks",
    "minecraft:cherry_log": "minecraft:cherry_log",
    "minecraft:cherry_planks": "minecraft:cherry_planks",
    "minecraft:bamboo_block": "minecraft:bamboo_block",
}
NBT_END       = 0
NBT_BYTE      = 1
NBT_SHORT     = 2
NBT_INT       = 3
NBT_LONG      = 4
NBT_FLOAT     = 5
NBT_DOUBLE    = 6
NBT_BYTE_ARRAY= 7
NBT_STRING    = 8
NBT_LIST      = 9
NBT_COMPOUND  = 10
NBT_INT_ARRAY = 11
NBT_LONG_ARRAY= 12
def _nbt_read_tag(buf: _io.BytesIO, tag_type: int):
    if tag_type == NBT_BYTE:
        return _struct.unpack(">b", buf.read(1))[0]
    elif tag_type == NBT_SHORT:
        return _struct.unpack(">h", buf.read(2))[0]
    elif tag_type == NBT_INT:
        return _struct.unpack(">i", buf.read(4))[0]
    elif tag_type == NBT_LONG:
        return _struct.unpack(">q", buf.read(8))[0]
    elif tag_type == NBT_FLOAT:
        return _struct.unpack(">f", buf.read(4))[0]
    elif tag_type == NBT_DOUBLE:
        return _struct.unpack(">d", buf.read(8))[0]
    elif tag_type == NBT_BYTE_ARRAY:
        length = _struct.unpack(">i", buf.read(4))[0]
        return list(_struct.unpack(f">{length}b", buf.read(length)))
    elif tag_type == NBT_STRING:
        length = _struct.unpack(">H", buf.read(2))[0]
        return buf.read(length).decode("utf-8", errors="replace")
    elif tag_type == NBT_LIST:
        elem_type = _struct.unpack(">b", buf.read(1))[0]
        length = _struct.unpack(">i", buf.read(4))[0]
        return [_nbt_read_tag(buf, elem_type) for _ in range(length)]
    elif tag_type == NBT_COMPOUND:
        d = {}
        while True:
            t = _struct.unpack(">b", buf.read(1))[0]
            if t == NBT_END:
                break
            name_len = _struct.unpack(">H", buf.read(2))[0]
            name = buf.read(name_len).decode("utf-8", errors="replace")
            d[name] = _nbt_read_tag(buf, t)
        return d
    elif tag_type == NBT_INT_ARRAY:
        length = _struct.unpack(">i", buf.read(4))[0]
        return list(_struct.unpack(f">{length}i", buf.read(length * 4)))
    elif tag_type == NBT_LONG_ARRAY:
        length = _struct.unpack(">i", buf.read(4))[0]
        return list(_struct.unpack(f">{length}q", buf.read(length * 8)))
    else:
        raise ValueError(f"Unknown NBT tag type: {tag_type}")
def read_java_nbt(data: bytes) -> dict:
    try:
        data = _gzip.decompress(data)
    except Exception:
        pass
    buf = _io.BytesIO(data)
    root_type = _struct.unpack(">b", buf.read(1))[0]
    name_len  = _struct.unpack(">H", buf.read(2))[0]
    buf.read(name_len)
    return _nbt_read_tag(buf, root_type)
def _nbt_write_tag(buf: _io.BytesIO, tag_type: int, value):
    if tag_type == NBT_BYTE:
        buf.write(_struct.pack("<b", int(value)))
    elif tag_type == NBT_SHORT:
        buf.write(_struct.pack("<h", int(value)))
    elif tag_type == NBT_INT:
        buf.write(_struct.pack("<i", int(value)))
    elif tag_type == NBT_LONG:
        buf.write(_struct.pack("<q", int(value)))
    elif tag_type == NBT_FLOAT:
        buf.write(_struct.pack("<f", float(value)))
    elif tag_type == NBT_DOUBLE:
        buf.write(_struct.pack("<d", float(value)))
    elif tag_type == NBT_BYTE_ARRAY:
        buf.write(_struct.pack("<i", len(value)))
        buf.write(_struct.pack(f"<{len(value)}b", *value))
    elif tag_type == NBT_STRING:
        encoded = str(value).encode("utf-8")
        buf.write(_struct.pack("<H", len(encoded)))
        buf.write(encoded)
    elif tag_type == NBT_LIST:
        if not value:
            buf.write(_struct.pack("<b", NBT_END))
            buf.write(_struct.pack("<i", 0))
        else:
            first = value[0]
            if isinstance(first, bool):   elem_type = NBT_BYTE
            elif isinstance(first, int):  elem_type = NBT_INT
            elif isinstance(first, float):elem_type = NBT_FLOAT
            elif isinstance(first, str):  elem_type = NBT_STRING
            elif isinstance(first, dict): elem_type = NBT_COMPOUND
            elif isinstance(first, list): elem_type = NBT_LIST
            else:                         elem_type = NBT_STRING
            buf.write(_struct.pack("<b", elem_type))
            buf.write(_struct.pack("<i", len(value)))
            for item in value:
                _nbt_write_tag(buf, elem_type, item)
    elif tag_type == NBT_COMPOUND:
        for k, v in value.items():
            t = _infer_nbt_type(v)
            buf.write(_struct.pack("<b", t))
            enc_k = k.encode("utf-8")
            buf.write(_struct.pack("<H", len(enc_k)))
            buf.write(enc_k)
            _nbt_write_tag(buf, t, v)
        buf.write(_struct.pack("<b", NBT_END))
    elif tag_type == NBT_INT_ARRAY:
        buf.write(_struct.pack("<i", len(value)))
        buf.write(_struct.pack(f"<{len(value)}i", *value))
    elif tag_type == NBT_LONG_ARRAY:
        buf.write(_struct.pack("<i", len(value)))
        buf.write(_struct.pack(f"<{len(value)}q", *value))
def _infer_nbt_type(value) -> int:
    if isinstance(value, bool):  return NBT_BYTE
    if isinstance(value, int):   return NBT_INT
    if isinstance(value, float): return NBT_FLOAT
    if isinstance(value, str):   return NBT_STRING
    if isinstance(value, dict):  return NBT_COMPOUND
    if isinstance(value, list):
        if not value:            return NBT_LIST
        first = value[0]
        if isinstance(first, bool):  return NBT_LIST
        if isinstance(first, int):   return NBT_INT_ARRAY
        if isinstance(first, float): return NBT_LIST
        if isinstance(first, dict):  return NBT_LIST
        if isinstance(first, list):  return NBT_LIST
        return NBT_LIST
    return NBT_STRING
def write_bedrock_nbt(root_name: str, compound: dict) -> bytes:
    buf = _io.BytesIO()
    buf.write(_struct.pack("<b", NBT_COMPOUND))
    enc = root_name.encode("utf-8")
    buf.write(_struct.pack("<H", len(enc)))
    buf.write(enc)
    _nbt_write_tag(buf, NBT_COMPOUND, compound)
    return buf.getvalue()
def _remap_block_name(java_name: str, namespace: str) -> str:
    if not java_name:
        return "minecraft:air"
    if ":" in java_name:
        ns, name = java_name.split(":", 1)
        if ns == "minecraft":
            return JAVA_TO_BEDROCK_BLOCK.get(java_name, "minecraft:air")
        else:
            return f"{namespace}:{sanitize_identifier(name)}"
    return JAVA_TO_BEDROCK_BLOCK.get(f"minecraft:{java_name}", f"minecraft:{java_name}")
def _convert_block_state(java_state: dict, bedrock_name: str) -> dict:
    if not java_state:
        return {}
    bedrock_states = {}
    PROP_MAP = {
        "facing":        "minecraft:facing_direction",
        "half":          None,
        "waterlogged":   None,
        "powered":       "powered_bit",
        "open":          "open_bit",
        "lit":           "lit",
        "persistent":    "persistent_bit",
        "snowy":         None,
        "axis":          "pillar_axis",
        "type":          None,
        "shape":         None,
        "age":           "age",
        "level":         "liquid_depth",
        "layers":        "height",
        "distance":      None,
        "occupied":      None,
        "part":          None,
        "in_wall":       None,
        "attached":      None,
        "disarmed":      None,
        "hinge":         None,
        "delay":         "output_lit_bit",
        "locked":        None,
    }
    for k, v in java_state.items():
        bedrock_key = PROP_MAP.get(k, k)
        if bedrock_key is None:
            continue
        if v == "true":  v = 1
        elif v == "false": v = 0
        elif k == "facing":
            v = {"north": 2, "south": 3, "west": 4, "east": 5,
                 "up": 1, "down": 0}.get(v, 0)
            bedrock_key = "facing_direction"
        elif k == "axis":
            v = {"x": 1, "y": 0, "z": 2}.get(v, 0)
            bedrock_key = "pillar_axis"
        try:
            v = int(v)
        except (ValueError, TypeError):
            pass
        bedrock_states[bedrock_key] = v
    return bedrock_states
def convert_java_nbt_to_mcstructure(nbt_data: dict, namespace: str) -> dict:
    size = nbt_data.get("size", [1, 1, 1])
    sx, sy, sz = int(size[0]), int(size[1]), int(size[2])
    total = sx * sy * sz
    BEDROCK_BLOCK_VERSION = 17959425
    java_palette = nbt_data.get("palette", [])
    bedrock_palette = []
    dedup_map  = {}
    java_to_bp = {}
    air_key = ("minecraft:air", ())
    dedup_map[air_key] = 0
    bedrock_palette.append({"name": "minecraft:air", "states": {}, "version": BEDROCK_BLOCK_VERSION})
    for i, entry in enumerate(java_palette):
        java_name = entry.get("Name", "minecraft:air")
        java_props = entry.get("Properties", {})
        bedrock_name = _remap_block_name(java_name, namespace)
        bedrock_states = _convert_block_state(java_props, bedrock_name)
        key = (bedrock_name, tuple(sorted(bedrock_states.items())))
        if key not in dedup_map:
            dedup_map[key] = len(bedrock_palette)
            bedrock_palette.append({
                "name": bedrock_name,
                "states": bedrock_states,
                "version": BEDROCK_BLOCK_VERSION
            })
        java_to_bp[i] = dedup_map[key]
    water_key = ("minecraft:water", ())
    if water_key not in dedup_map:
        dedup_map[water_key] = len(bedrock_palette)
        bedrock_palette.append({"name": "minecraft:water", "states": {"liquid_depth": 0}, "version": BEDROCK_BLOCK_VERSION})
    water_idx = dedup_map[water_key]
    layer0 = [-1] * total
    layer1 = [-1] * total
    block_position_data = {}
    for block in nbt_data.get("blocks", []):
        pos = block.get("pos", [0, 0, 0])
        state_idx = int(block.get("state", 0))
        x, y, z = int(pos[0]), int(pos[1]), int(pos[2])
        if not (0 <= x < sx and 0 <= y < sy and 0 <= z < sz):
            continue
        flat_idx = x + z * sx + y * sx * sz
        bedrock_idx = java_to_bp.get(state_idx, 0)
        layer0[flat_idx] = bedrock_idx
        java_entry = java_palette[state_idx] if state_idx < len(java_palette) else {}
        if java_entry.get("Properties", {}).get("waterlogged") == "true":
            layer1[flat_idx] = water_idx
        block_nbt = block.get("nbt")
        if block_nbt and isinstance(block_nbt, dict):
            converted_be = _convert_block_entity_nbt(block_nbt, namespace)
            if converted_be:
                block_position_data[str(flat_idx)] = {"block_entity_data": converted_be}
    bedrock_entities = []
    for i, ent in enumerate(nbt_data.get("entities", [])):
        try:
            pos = ent.get("pos", [0.0, 0.0, 0.0])
            ent_nbt = ent.get("nbt", {})
            entity_id = ent_nbt.get("id", "")
            if not entity_id:
                continue
            if ":" not in entity_id:
                entity_id = f"minecraft:{entity_id.lower()}"
            else:
                ns_e, name_e = entity_id.split(":", 1)
                if ns_e != "minecraft":
                    entity_id = f"{namespace}:{sanitize_identifier(name_e)}"
            bedrock_entities.append({
                "identifier": entity_id,
                "Pos": [float(p) for p in pos],
                "UniqueID": -(i + 1),
                "Tags": [],
            })
        except Exception:
            pass
    return {
        "format_version": 1,
        "size": [sx, sy, sz],
        "structure_world_origin": [0, 0, 0],
        "structure": {
            "block_indices": [layer0, layer1],
            "entities": bedrock_entities,
            "palette": {
                "default": {
                    "block_palette": bedrock_palette,
                    "block_position_data": block_position_data
                }
            }
        }
    }
def _convert_block_entity_nbt(java_nbt: dict, namespace: str) -> Optional[dict]:
    be_id = java_nbt.get("id", "")
    if not be_id:
        return None
    if ":" in be_id:
        be_id = be_id.split(":", 1)[1]
    be_id = be_id.lower()
    result = {"id": be_id, "isMovable": 1}
    if be_id in ("chest", "trapped_chest", "barrel", "shulker_box", "hopper", "dropper", "dispenser"):
        items = java_nbt.get("Items", [])
        bedrock_items = []
        for item in items:
            if not isinstance(item, dict):
                continue
            item_id = item.get("id", "minecraft:air")
            if ":" in item_id:
                ns_i, name_i = item_id.split(":", 1)
                if ns_i != "minecraft":
                    item_id = f"{namespace}:{sanitize_identifier(name_i)}"
            bedrock_items.append({
                "Count": item.get("Count", 1),
                "Damage": 0,
                "Name": item_id,
                "Slot": item.get("Slot", 0),
                "WasPickedUp": 0,
            })
        if bedrock_items:
            result["Items"] = bedrock_items
    elif be_id == "mob_spawner":
        spawn_data = java_nbt.get("SpawnData", {})
        entity_id = spawn_data.get("entity", {}).get("id", "") or java_nbt.get("EntityId", "")
        if not entity_id:
            entity_id = "minecraft:pig"
        if ":" not in entity_id:
            entity_id = f"minecraft:{entity_id.lower()}"
        result["EntityIdentifier"] = entity_id
        result["Delay"] = java_nbt.get("Delay", 20)
        result["MaxNearbyEntities"] = java_nbt.get("MaxNearbyEntities", 6)
        result["MaxSpawnDelay"] = java_nbt.get("MaxSpawnDelay", 800)
        result["MinSpawnDelay"] = java_nbt.get("MinSpawnDelay", 200)
        result["RequiredPlayerRange"] = java_nbt.get("RequiredPlayerRange", 16)
        result["SpawnCount"] = java_nbt.get("SpawnCount", 4)
        result["SpawnRange"] = java_nbt.get("SpawnRange", 4)
    elif be_id in ("sign", "hanging_sign"):
        import json as _json
        for side in ("front_text", "back_text", "Text1", "Text2", "Text3", "Text4"):
            val = java_nbt.get(side, "")
            if isinstance(val, dict):
                messages = val.get("messages", [])
                lines = []
                for msg in messages:
                    try:
                        parsed = _json.loads(msg)
                        text = parsed.get("text", "") if isinstance(parsed, dict) else str(parsed)
                    except Exception:
                        text = str(msg).strip('"')
                    lines.append(text)
                result["Text"] = "\n".join(lines)
                break
            elif isinstance(val, str) and val:
                try:
                    parsed = _json.loads(val)
                    result[side] = parsed.get("text", val) if isinstance(parsed, dict) else val
                except Exception:
                    result[side] = val
    elif be_id in ("furnace", "smoker", "blast_furnace"):
        result["BurnTime"] = java_nbt.get("BurnTime", 0)
        result["CookTime"] = java_nbt.get("CookTime", 0)
        result["CookTimeTotal"] = java_nbt.get("CookTimeTotal", 200)
    return result if len(result) > 2 else None
def extract_structure_metadata_from_java(java_code: str, namespace: str) -> dict:
    meta = {
        "biomes": ["overworld"],
        "step": "surface_pass",
        "spacing": 32,
        "separation": 8,
        "salt": 0,
        "start_height": 64,
        "terrain_adaptation": "beard_thin",
    }
    unit = get_unit(java_code)
    for ref in unit.walk(jt.MemberReference):
        q = ref.qualifier or ''
        if q.split('.')[0] in ('BiomeTags', 'BiomeDictionary') or q.startswith('Tags.Biomes'):
            b = _leading_run(ref.member, lambda c: c.isupper() or c == '_')
            if not b:
                continue
            bl = b.lower().replace("is_", "").replace("has_", "")
            for k, v in JAVA_BIOME_TO_BEDROCK.items():
                if k in bl:
                    if v not in meta["biomes"]:
                        meta["biomes"].append(v)
    pairs = _name_value_pairs(unit)
    for key in ('spacing', 'separation', 'salt'):
        for name, val in pairs:
            if key in name and _int_arg_text(val):
                meta[key] = int(_int_arg_text(val))
                break
    if _mentions(java_code, 'nether'):
        meta["biomes"] = ["nether"]
        meta["step"] = "surface_pass"
    if _mentions(java_code, 'the_end'):
        meta["biomes"] = ["the_end"]
    decos = [r.member for r in unit.walk(jt.MemberReference) if r.qualifier == 'GenerationStep.Decoration']
    if any(m.startswith('UNDERGROUND') for m in decos):
        meta["step"] = "underground_pass"
    if any(m.startswith('VEGETAL') for m in decos):
        meta["step"] = "surface_pass"
    height_nodes = [val for name, val in pairs if 'startHeight' in name]
    height_nodes += [list(c.arguments or []) for c in unit.walk(jt.MethodInvocation) if 'startHeight' in c.member]
    for node in height_nodes:
        for lit in iter_nodes(node, jt.Literal):
            n = number_value(lit)
            if n is not None and float(n).is_integer():
                meta["start_height"] = int(n)
                break
        else:
            continue
        break
    return meta
def generate_feature_json(structure_name: str, namespace: str) -> dict:
    full_id = f"{namespace}:{structure_name}"
    return {
        "format_version": "1.13.0",
        "minecraft:structure_template_feature": {
            "description": {
                "identifier": f"{namespace}:{structure_name}_feature"
            },
            "structure_name": full_id,
            "adjustment_radius": 4,
            "facing_direction": "random",
            "constraints": {
                "unburied": {},
                "block_intersection": {
                    "only_check_intersection_for_motion_blocking_blocks": False,
                    "block_allowlist": [
                        "minecraft:air",
                        "minecraft:grass",
                        "minecraft:dirt",
                        "minecraft:stone"
                    ]
                }
            }
        }
    }
def generate_feature_rule_json(structure_name: str, namespace: str, meta: dict) -> dict:
    biome_filters = []
    for biome in meta.get("biomes", ["overworld"]):
        biome_filters.append({
            "test": "has_biome_tag",
            "operator": "==",
            "value": biome
        })
    spacing = meta.get("spacing", 32)
    chance = max(0.01, min(1.0, round(1.0 / max(1, spacing / 8), 3)))
    return {
        "format_version": "1.13.0",
        "minecraft:feature_rules": {
            "description": {
                "identifier": f"{namespace}:{structure_name}_feature_rule",
                "places_feature": f"{namespace}:{structure_name}_feature"
            },
            "conditions": {
                "placement_pass": meta.get("step", "surface_pass"),
                "minecraft:biome_filter": biome_filters if len(biome_filters) > 1 else biome_filters[0] if biome_filters else {"test": "has_biome_tag", "value": "overworld"}
            },
            "distribution": {
                "iterations": 1,
                "scatter_chance": str(chance),
                "x": {"distribution": "uniform", "extent": [0, 16]},
                "y": meta.get("start_height", 64),
                "z": {"distribution": "uniform", "extent": [0, 16]}
            }
        }
    }
def process_structures_from_jar(jar_path: str, namespace: str, java_files: dict = None):
    if not jar_path or not os.path.exists(jar_path):
        return
    java_files = java_files or {}
    structures_processed = 0
    features_written = 0
    mcstructure_dir = os.path.join(BP_FOLDER, "structures")
    features_dir    = os.path.join(BP_FOLDER, "features")
    feat_rules_dir  = os.path.join(BP_FOLDER, "feature_rules")
    os.makedirs(mcstructure_dir, exist_ok=True)
    os.makedirs(features_dir, exist_ok=True)
    os.makedirs(feat_rules_dir, exist_ok=True)
    structure_meta_map = {}
    for path, code in java_files.items():
        if _class_parents_lower(code) & {'structure', 'structurefeature', 'jigsawstructure'}:
            cls = extract_class_name(code) or os.path.splitext(os.path.basename(path))[0]
            structure_meta_map[cls] = extract_structure_metadata_from_java(code, namespace)
    worldgen_metas = {}
    try:
        with zipfile.ZipFile(jar_path, "r") as jar:
            for file in jar.namelist():
                lower = file.lower()
                if lower.endswith(".nbt") and "/structures/" in lower:
                    try:
                        with jar.open(file) as f:
                            nbt_raw = f.read()
                        nbt_data = read_java_nbt(nbt_raw)
                        after = lower.split("/structures/", 1)[1]
                        stem = os.path.splitext(after)[0].replace("/", "_").replace("\\", "_")
                        safe_stem = sanitize_identifier(stem)
                        mcstructure = convert_java_nbt_to_mcstructure(nbt_data, namespace)
                        mcstructure_nbt = write_bedrock_nbt("", mcstructure)
                        out_path = os.path.join(mcstructure_dir, f"{safe_stem}.mcstructure")
                        with open(out_path, "wb") as out_f:
                            out_f.write(mcstructure_nbt)

                        meta = {"biomes": ["overworld"], "step": "surface_pass",
                                "spacing": 32, "separation": 8, "start_height": 64}
                        for cls_name, cls_meta in structure_meta_map.items():
                            if sanitize_identifier(cls_name.lower().replace("structure","")) in safe_stem:
                                meta = cls_meta
                                break
                        feat_json = generate_feature_json(safe_stem, namespace)
                        safe_write_json(os.path.join(features_dir, f"{safe_stem}_feature.json"), feat_json)
                        rule_json = generate_feature_rule_json(safe_stem, namespace, meta)
                        safe_write_json(os.path.join(feat_rules_dir, f"{safe_stem}_feature_rule.json"), rule_json)
                        structures_processed += 1
                        features_written += 1
                    except Exception as e:
                        _warn(f"[structure]  Failed to convert {file}: {e}")
                elif "/worldgen/structure/" in lower and lower.endswith(".json"):
                    try:
                        with jar.open(file) as f:
                            wg_data = json.load(f)
                        stem = os.path.splitext(os.path.basename(file))[0]
                        safe_stem = sanitize_identifier(stem)
                        biome_tag = wg_data.get("biomes", "")
                        if isinstance(biome_tag, str) and ":" in biome_tag:
                            biome_tag = biome_tag.split(":")[1]
                        worldgen_metas[safe_stem] = {
                            "raw": wg_data,
                            "biome_hint": biome_tag
                        }
                    except Exception:
                        pass
                elif "/worldgen/template_pool/" in lower and lower.endswith(".json"):
                    try:
                        with jar.open(file) as f:
                            pool_data = json.load(f)
                        safe_stem = sanitize_identifier(os.path.splitext(os.path.basename(file))[0])
                        ref_path = os.path.join(BP_FOLDER, "structures", f"_pool_{safe_stem}.json")
                        with open(ref_path, "w", encoding="utf-8") as out_f:
                            json.dump({
                                "__note": "Jigsaw template pool - manual conversion required",
                                "__source": file,
                                "data": pool_data
                            }, out_f, indent=2)
                    except Exception:
                        pass
    except Exception as e:
        _warn(f"[structure]  JAR read error: {e}")

_MIXIN_KINDS = (
    'mixin', 'inject', 'redirect', 'overwrite', 'accessor', 'invoker',
    'shadow', 'unique', 'mutable', 'final', 'modifyvariable', 'modifyarg',
    'modifyargs', 'modifyconstant', 'wrapoperation', 'wrapwithcondition',
    'slice', 'at', 'group', 'coerce', 'desc'
)

_MIXIN_TARGET_ALIASES = {
    'PlayerEntity': 'Player',
    'ServerPlayerEntity': 'ServerPlayer',
    'ClientPlayerEntity': 'Player',
    'LivingEntity': 'LivingEntity',
    'PathfinderMob': 'PathfinderMob',
    'AbstractClientPlayerEntity': 'Player',
    'AbstractArrowEntity': 'AbstractArrow',
    'ThrownItemEntity': 'ItemEntity',
    'ItemEntity': 'ItemEntity',
    'BlockEntity': 'BlockEntity',
    'TileEntity': 'BlockEntity',
    'World': 'Level',
    'ServerWorld': 'ServerLevel',
    'ClientWorld': 'Level',
    'Level': 'Level',
    'ServerLevel': 'ServerLevel',
    'MinecraftServer': 'ServerLevel',
    'Block': 'Block',
    'AbstractBlock': 'AbstractBlock',
    'Item': 'Item',
    'ItemStack': 'ItemStack',
    'Screen': 'Screen',
    'HandledScreen': 'Screen',
    'AbstractContainerScreen': 'Screen',
    'ContainerScreen': 'Screen',
    'GuiScreen': 'Screen',
}

_MIXIN_METHOD_HINTS = {
    'tick': 'system.runInterval',
    'serverTick': 'system.runInterval',
    'clientTick': 'system.runInterval',
    'onTick': 'system.runInterval',
    'update': 'system.runInterval',
    'use': 'world.afterEvents.itemUse',
    'onUse': 'world.afterEvents.itemUse',
    'appendTooltip': 'world.afterEvents.itemUse',
    'interact': 'world.afterEvents.playerInteractWithEntity',
    'interactAt': 'world.afterEvents.playerInteractWithEntity',
    'attack': 'world.afterEvents.entityHitEntity',
    'performAttack': 'world.afterEvents.entityHitEntity',
    'hurt': 'world.afterEvents.entityHurt',
    'damage': 'world.afterEvents.entityHurt',
    'die': 'world.afterEvents.entityDie',
    'place': 'world.afterEvents.playerPlaceBlock',
    'onBlockActivated': 'world.afterEvents.playerInteractWithBlock',
    'onRemove': 'world.afterEvents.playerBreakBlock',
    'playerDestroy': 'world.afterEvents.playerBreakBlock',
    'onEntityHit': 'world.afterEvents.entityHitEntity',
    'shoot': 'world.afterEvents.projectileHitEntity',
    'explode': 'world.afterEvents.explosion',
    'finishUsingItem': 'world.afterEvents.useItem',
    'onCraftedBy': 'world.afterEvents.crafted',
}

_MIXIN_TARGET_EVENT_HINTS = {
    'Player': 'world.afterEvents.playerInteractWithEntity',
    'ServerPlayer': 'world.afterEvents.playerInteractWithEntity',
    'LivingEntity': 'world.afterEvents.entityHurt',
    'Mob': 'world.afterEvents.entitySpawn',
    'PathfinderMob': 'world.afterEvents.entitySpawn',
    'Animal': 'world.afterEvents.entitySpawn',
    'Monster': 'world.afterEvents.entitySpawn',
    'Entity': 'world.afterEvents.entitySpawn',
    'ItemEntity': 'world.afterEvents.itemStartPickUp',
    'AbstractArrow': 'world.afterEvents.projectileHitEntity',
    'Arrow': 'world.afterEvents.projectileHitEntity',
    'ThrownPotion': 'world.afterEvents.projectileHitEntity',
    'BlockEntity': 'world.afterEvents.playerInteractWithBlock',
    'Level': 'world.afterEvents.worldInitialize',
    'ServerLevel': 'world.afterEvents.worldInitialize',
    'Screen': 'system.runInterval',
    'Item': 'world.afterEvents.itemUse',
    'Block': 'world.afterEvents.playerPlaceBlock',
    'AbstractBlock': 'world.afterEvents.playerPlaceBlock',
}

_SUPPORTED_MIXIN_ANNOTATIONS = {
    'Mixin', 'Inject', 'Redirect', 'Overwrite', 'Accessor', 'Invoker',
    'Shadow', 'Unique', 'Mutable', 'Final', 'ModifyVariable', 'ModifyArg',
    'ModifyArgs', 'ModifyConstant', 'WrapOperation', 'WrapWithCondition',
    'Slice', 'At', 'Group', 'Coerce', 'Desc', 'Surrogate'
}

def annotation_text(node) -> str:

    if isinstance(node, jt.Annotation):
        inner = annotation_text(node.element) if node.element is not None else ''
        return f'@{node.name}({inner})'
    if isinstance(node, jt.ElementValuePair):
        return f'{node.name}={annotation_text(node.value)}'
    if isinstance(node, jt.ElementArrayValue):
        return '{' + ', '.join(annotation_text(v) for v in node.values or []) + '}'
    if isinstance(node, list):
        return ', '.join(annotation_text(n) for n in node)
    if isinstance(node, jt.ClassReference):
        return type_name(node.type) + '.class'
    if node is None:
        return ''
    return expr_text(node)

def _mixin_target_name(code) -> Optional[str]:

    unit = as_unit(code)
    for ann in unit.annotations():
        if ann.name != 'Mixin' or ann.element is None:
            continue
        quoted = [s for s in (string_value(l) for l in iter_nodes(ann.element, jt.Literal))
                  if s and _charset_ok(s, '._$/')]
        if quoted:
            return quoted[0].replace('/', '.').split('.')[-1]
        for ref in iter_nodes(ann.element, jt.ClassReference):
            return type_name(ref.type).split('.')[-1]
    return None

def _translate_mixin_stmts(stmts, namespace: str, safe_name: str) -> list:
    lines: list = []
    for stmt in stmts or []:
        lines.extend(translate_statement(stmt, 'entity', namespace, JavaSymbolTable()))
    return lines

def _mixin_methods(unit, kind: str) -> list:
    return [m for m in unit.methods() if kind in annotation_names(m)]

def scan_mixins(java_files: Dict[str, str], namespace: str) -> list[str]:
    notes: list[str] = []
    out_dir = os.path.join(BP_FOLDER, 'scripts')
    os.makedirs(out_dir, exist_ok=True)
    for path, code in java_files.items():
        unit = get_unit(code)
        if 'Mixin' not in {a.name for a in unit.annotations()} and 'mixin' not in os.path.basename(path).lower():
            continue
        target = _mixin_target_name(unit)
        cls_name = JavaAST(code).primary_class_name() or os.path.splitext(os.path.basename(path))[0]
        safe_name = clean_java_artifact_name(cls_name)
        script_lines = [f'import {{ world, system }} from "@minecraft/server";', '']
        wrote = False
        if target:
            script_lines += [f'// Mixin target: {target}', f'// Source: {cls_name}', '']
        for kind_name, kind in (('Inject', 'inject'), ('Redirect', 'redirect'), ('Overwrite', 'overwrite')):
            for m in _mixin_methods(unit, kind_name):
                ann = next(a for a in m.annotations if a.name == kind_name)
                annotation_args = annotation_text(ann.element) if ann.element is not None else ''
                method_name = m.name
                method_body = unit.body_text(m) or ''
                event = _mixin_event_guess(target, method_name, annotation_args, method_body)
                if event and event.startswith('system.runInterval'):
                    script_lines += [f'// {kind} {method_name} -> scheduled tick', 'system.runInterval(() => {']
                    script_lines += _translate_mixin_stmts(m.body, namespace, safe_name) or ['    // tick body could not be translated cleanly']
                    script_lines += ['}, 1);', '']
                elif event:
                    script_lines += [f'// {kind} {method_name} -> {event}', f'{event}.subscribe((event) => {{']
                    script_lines += _translate_mixin_stmts(m.body, namespace, safe_name) or ['    const entity = event.entity ?? event.player ?? event.hurtEntity ?? event.block ?? null;']
                    script_lines += ['});', '']
                else:
                    notes.append(f'[mixin] {cls_name}: {kind} {method_name} had no confident Bedrock mapping')
                wrote = True
        if {a.name for a in unit.annotations()} & {'Accessor', 'Invoker'}:
            notes.append(f'[mixin] {cls_name}: accessor/invoker patterns need manual Bedrock porting or helper wrappers')
            wrote = True
        if wrote:
            out_path = os.path.join(out_dir, f'mixin_{safe_name}.js')
            with open(out_path, 'w', encoding='utf-8') as fh:
                fh.write('\n'.join(script_lines))
    return notes

def generate_bedrock_runtime_bridge(namespace: str) -> list[str]:
    return [
        'import { world, system, GameMode, ItemStack, BlockPermutation } from "@minecraft/server";',
        '',
        f'export const MOD_NAMESPACE = {json.dumps(namespace)};',
        'export const runtime = {',
        '  schedule: (fn, ticks = 1) => system.runInterval(fn, Math.max(1, ticks)),',
        '  onEntitySpawn: (fn) => world.afterEvents.entitySpawn.subscribe(fn),',
        '  onEntityHurt: (fn) => world.afterEvents.entityHurt.subscribe(fn),',
        '  onBlockPlace: (fn) => world.afterEvents.playerPlaceBlock.subscribe(fn),',
        '  onBlockBreak: (fn) => world.afterEvents.playerBreakBlock.subscribe(fn),',
        '  getProp: (entity, key, fallback = null) => entity?.getDynamicProperty?.(key) ?? fallback,',
        '  setProp: (entity, key, value) => entity?.setDynamicProperty?.(key, value),',
        '  hasTag: (entity, tag) => !!entity?.hasTag?.(tag),',
        '  tag: (entity, tag) => entity?.addTag?.(tag),',
        '  untag: (entity, tag) => entity?.removeTag?.(tag),',
        '  getDimension: (dimensionId) => world.getDimension(dimensionId),',
        '  teleportToDimension: (entity, dimensionId, position, options = {}) => {',
        '    const dimension = world.getDimension(dimensionId);',
        '    if (!dimension) return false;',
        '    entity.teleport({ ...position, dimension, ...options });',
        '    return true;',
        '  },',
        '  getTopmostBlock: (dimensionId, x, z, minHeight = -64) => {',
        '    const dimension = world.getDimension(dimensionId);',
        '    return dimension ? dimension.getTopmostBlock({ x, z }, minHeight) : null;',
        '  },',
        '  getBlockFromRay: (dimensionId, origin, direction, options = {}) => {',
        '    const dimension = world.getDimension(dimensionId);',
        '    return dimension ? dimension.getBlockFromRay(origin, direction, options) : null;',
        '  },',
        '  containsBiomes: (dimensionId, volume, biomeFilter, isSuperset = false) => {',
        '    const dimension = world.getDimension(dimensionId);',
        '    return dimension && typeof dimension.containsBiomes === "function" ? dimension.containsBiomes(volume, { biomeFilter, isSuperset }) : false; // containsBiomes: newer @minecraft/server only',
        '  },',
        '  fillBlocks: (dimensionId, volume, permutation, options = {}) => {',
        '    const dimension = world.getDimension(dimensionId);',
        '    return dimension && typeof dimension.fillBlocks === "function" ? dimension.fillBlocks(volume, permutation, options) : false; // fillBlocks: newer @minecraft/server only',
        '  },',
        '  spawnEntityInDimension: (dimensionId, typeId, location) => {',
        '    const dimension = world.getDimension(dimensionId);',
        '    return dimension ? dimension.spawnEntity(typeId, location) : null;',
        '  },',
        '  onPlayerDimensionChange: (fn) => world.afterEvents.playerDimensionChange.subscribe(fn),',
        '  safeCall: (fn, fallback = undefined) => { try { return fn(); } catch { return fallback; } },',
        '};',
        '',
        'export function isTargetType(entity, id) {',
        '  return !!entity && typeof entity.typeId === "string" && (entity.typeId === id || entity.typeId.endsWith(`:${id.split(":").pop()}`));',
        '}',
        '',
        'export function withEntity(entity, fn) {',
        '  if (!entity) return;',
        '  try { fn(entity); } catch (e) { console.warn(`[runtime] ${e?.message ?? e}`); }',
        '}',
    ]

def _mixin_event_guess(target_cls: str, method_name: str, annotation_args: str, body: str, ann_names: Optional[List[str]] = None) -> Optional[str]:
    ann_names = ann_names or []
    needle = f'{target_cls} {method_name} {annotation_args} {body}'.lower()

    if any(k in needle for k in ('tick', 'update', 'inventorytick', 'aiset', 'servertick', 'clienttick')):
        return 'system.runInterval'
    if any(k in needle for k in ('chat', 'message', 'sendchat', 'chatsend')):
        return 'world.beforeEvents.chatSend'
    if any(k in needle for k in ('hurt', 'damage', 'attack', 'hurtentity')):
        return 'world.afterEvents.entityHurt'
    if any(k in needle for k in ('death', 'die', 'killed')):
        return 'world.afterEvents.entityDie'
    if any(k in needle for k in ('spawn', 'join', 'create', 'construct', 'addedtotick', 'entityjoin')):
        return 'world.afterEvents.entitySpawn'
    if any(k in needle for k in ('explode', 'explosion', 'detonate')):
        return 'world.afterEvents.explosion'
    if any(k in needle for k in ('pickup', 'pick up', 'pickupitem')):
        return 'world.afterEvents.playerPickUpItem'
    if any(k in needle for k in ('drop', 'toss', 'throw')):
        return 'world.afterEvents.playerDropItem'
    if any(k in needle for k in ('useon', 'place', 'blockactivated', 'interactblock', 'rightclickblock')):
        return 'world.afterEvents.playerPlaceBlock'
    if any(k in needle for k in ('break', 'destroy', 'mine', 'removeblock', 'leftclickblock')):
        return 'world.afterEvents.playerBreakBlock'
    if any(k in needle for k in ('interact', 'rightclick', 'use', 'attackentity', 'interactat', 'mount')):
        return 'world.afterEvents.playerInteractWithEntity'
    if any(k in needle for k in ('craft', 'crafted')):
        return 'world.afterEvents.itemCompleteUse'
    if any(k in needle for k in ('itemuse', 'useitem', 'finishusingitem', 'appendtooltip')):
        return 'world.afterEvents.itemUse'
    if any(k in needle for k in ('block', 'state', 'tileentity', 'worldgen')):
        return 'world.afterEvents.playerPlaceBlock'
    return None

def scan_fabric_quilt_mixins(java_files: Dict[str, str], namespace: str) -> List[str]:
    return scan_mixins(java_files, namespace)
