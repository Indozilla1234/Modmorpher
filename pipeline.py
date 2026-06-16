def run_prescan(java_files: dict, namespace: str) -> str:
    global ENTITY_REGISTRY, ATTRS_REGISTRY, SOUND_CONST_MAP
    detected = detect_mod_id(java_files)
    ns = detected or namespace
    build_inheritance_graph(java_files)
    ENTITY_REGISTRY = build_entity_registry(java_files, ns)
    ATTRS_REGISTRY  = build_attributes_registry(java_files)
    SOUND_CONST_MAP = build_sound_registry_from_java(java_files, ns)
    build_goal_inheritance_map(java_files)

    for cls, eid in list(ENTITY_REGISTRY.items())[:6]:
        pass

    return ns
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
import struct as _struct
import gzip as _gzip
import io as _io
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
    biome_matches = re.findall(
        r'(?:BiomeTags|Tags\.Biomes|BiomeDictionary)[^.(]*\.([A-Z_]+)', java_code)
    for b in biome_matches:
        bl = b.lower().replace("is_", "").replace("has_", "")
        for k, v in JAVA_BIOME_TO_BEDROCK.items():
            if k in bl:
                if v not in meta["biomes"]:
                    meta["biomes"].append(v)
    m = re.search(r'spacing\s*[=,]\s*(\d+)', java_code)
    if m: meta["spacing"] = int(m.group(1))
    m = re.search(r'separation\s*[=,]\s*(\d+)', java_code)
    if m: meta["separation"] = int(m.group(1))
    m = re.search(r'salt\s*[=,]\s*(\d+)', java_code)
    if m: meta["salt"] = int(m.group(1))
    if re.search(r'NETHER|nether', java_code, re.I): meta["biomes"] = ["nether"]; meta["step"] = "surface_pass"
    if re.search(r'THE_END|the_end', java_code, re.I): meta["biomes"] = ["the_end"]
    if re.search(r'GenerationStep\.Decoration\.UNDERGROUND', java_code): meta["step"] = "underground_pass"
    if re.search(r'GenerationStep\.Decoration\.VEGETAL', java_code): meta["step"] = "surface_pass"
    m = re.search(r'startHeight[^;]*?(-?\d+)', java_code)
    if m: meta["start_height"] = int(m.group(1))
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
                    "only_check_intersection_for_motion_blocking_blocks": false,
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
        if re.search(r'extends\s+(?:Structure|StructureFeature|JigsawStructure)', code):
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

def extract_logo_from_jar(jar_path: str) -> Optional[str]:
    if not jar_path or not os.path.exists(jar_path):
        return None
    icon_candidates = [
        "pack.png", "icon.png", "logo.png", "pack_icon.png",
        "META-INF/pack.png", "META-INF/icon.png", "META-INF/logo.png"
    ]
    try:
        with zipfile.ZipFile(jar_path, "r") as jar:
            for candidate in icon_candidates:
                try:
                    with jar.open(candidate) as f:
                        icon_data = f.read()
                    temp_dir = ".temp_logo_extract"
                    os.makedirs(temp_dir, exist_ok=True)
                    temp_path = os.path.join(temp_dir, "pack_icon.png")
                    with open(temp_path, "wb") as out:
                        out.write(icon_data)

                    return temp_path
                except KeyError:
                    continue
    except Exception as e:
        _warn(f"[icon] Failed to extract icon from JAR: {e}")
    return None

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

def _is_mixin_source(code: str, path: str = '') -> bool:
    code = code or ''
    low = (path or '').lower()
    if '@Mixin' in code or any(f'@{ann}' in code for ann in _SUPPORTED_MIXIN_ANNOTATIONS if ann != 'Mixin'):
        return True
    return 'mixin' in low

def _normalize_mixin_target(target: Optional[str]) -> Optional[str]:
    if not target:
        return None
    t = str(target).strip().strip('"\'')
    t = t.replace('/', '.').replace('$', '.')
    t = re.sub(r'<.*?>', '', t)
    t = t.split('.')[-1]
    return _MIXIN_TARGET_ALIASES.get(t, t)

def _extract_mixin_targets(code: str) -> list[str]:
    code = code or ''
    targets: list[str] = []
    for m in re.finditer(r'@Mixin\s*\((.*?)\)', code, re.DOTALL):
        body = m.group(1)
        for raw in re.findall(r'([A-Za-z_][\w.$/]+)\.class', body):
            targets.append(_normalize_mixin_target(raw))
        for raw in re.findall(r'"([^"]+)"', body):
            if '/' in raw or '.' in raw:
                targets.append(_normalize_mixin_target(raw))
    cleaned = [t for t in targets if t]
    return list(dict.fromkeys(cleaned))

def _annotation_arg_block(text: str, annotation: str) -> str:
    m = re.search(rf'@{re.escape(annotation)}\s*\((.*?)\)', text, re.DOTALL)
    return m.group(1) if m else ''

def _method_annotations(method_block: str) -> list[str]:
    return re.findall(r'@([A-Za-z_][A-Za-z0-9_]*)\b', method_block or '')

def _extract_annotated_methods(code: str) -> list[dict]:
    cleaned = _strip_java_comments(code or '')
    results: list[dict] = []
    pat = re.compile(
        r'(?P<ann>(?:\s*@\w+(?:\([^)]*\))?\s*)+)' \
        r'(?P<sig>(?:public|protected|private|static|final|native|synchronized|abstract|default|\s|@\w+(?:\([^)]*\))?\s*)+' \
        r'(?P<rettype>[\w<>,\[\].?\s]+?)\s+(?P<name>\w+)\s*\((?P<params>[^)]*)\)\s*(?:throws\s+[^{]+)?\{)',
        re.DOTALL,
    )
    for m in pat.finditer(cleaned):
        sig = m.group('sig')
        brace = cleaned.find('{', m.start('sig'))
        if brace == -1:
            continue
        body = _extract_block(cleaned, m.start('sig'))
        ann_text = m.group('ann')
        results.append({
            'name': m.group('name'),
            'return_type': (m.group('rettype') or '').strip(),
            'annotations': _method_annotations(ann_text),
            'annotation_text': ann_text,
            'signature_text': sig,
            'body': body,
            'params': m.group('params') or '',
        })
    return results

def _pick_mixin_event(target_cls: Optional[str], method_name: str, annotation_args: str = '', body: str = '') -> Optional[str]:
    hay = f'{target_cls or ""} {method_name} {annotation_args} {body}'.lower()
    if 'construct' in hay or '<init>' in hay:
        return 'world.afterEvents.entitySpawn'
    if method_name in _MIXIN_METHOD_HINTS:
        return _MIXIN_METHOD_HINTS[method_name]
    for key, event in _MIXIN_METHOD_HINTS.items():
        if key in hay:
            return event
    if any(k in hay for k in ('hurt', 'damage', 'attack')):
        return 'world.afterEvents.entityHurt'
    if any(k in hay for k in ('tick', 'update')):
        return 'system.runInterval'
    if any(k in hay for k in ('use', 'interact', 'rightclick', 'right_click')):
        return 'world.afterEvents.itemUse'
    if any(k in hay for k in ('place', 'break', 'destroy', 'remove')):
        return 'world.afterEvents.playerBreakBlock'
    if any(k in hay for k in ('spawn', 'join', 'load', 'init')):
        return 'world.afterEvents.entitySpawn'
    return None

def _accessor_member_name(method_name: str, annotation_args: str = '') -> str:
    if annotation_args:
        m = re.search(r'value\s*=\s*"([^"]+)"', annotation_args)
        if m:
            return m.group(1)
        m = re.search(r'target\s*=\s*"([^"]+)"', annotation_args)
        if m:
            return m.group(1)
    for prefix in ('get', 'set', 'is', 'call', 'invoke'):
        if method_name.startswith(prefix) and len(method_name) > len(prefix):
            stem = method_name[len(prefix):]
            return stem[:1].lower() + stem[1:]
    return method_name

def _emit_preserved_body(body: str) -> list[str]:
    lines: list[str] = []
    if not body:
        return lines
    for raw in body.splitlines():
        raw = raw.rstrip()
        if raw.strip():
            lines.append(f'// {raw.strip()}')
    return lines

def _emit_js_hook(event_name: str, body_lines: list[str], method_name: str, annotation_args: str = '', cancellable: bool = False) -> list[str]:
    out: list[str] = []
    if event_name == 'system.runInterval':
        out.append(f'// {method_name}: scheduled interval hook')
        out.append('system.runInterval(() => {')
        out.extend(body_lines or ['    // no automatic translation available'])
        out.append('}, 1);')
        return out
    out.append(f'// {method_name}: {event_name}')
    out.append(f'{event_name}.subscribe((event) => {{')
    if cancellable:
        out.append('    let cancelled = false;')
        out.append('    const cancel = () => { cancelled = true; };')
    if body_lines:
        for line in body_lines:
            if line.startswith('//'):
                out.append(f'    {line}')
            else:
                out.append(line)
    else:
        out.append('    const entity = event.entity ?? event.player ?? event.hurtEntity ?? event.block ?? event.itemEntity ?? null;')
        out.append('    if (!entity) return;')
    if cancellable:
        out.append('    if (cancelled) return;')
    out.append('});')
    return out

def _emit_accessor_stub(cls_name: str, method_name: str, annotation_args: str) -> list[str]:
    member = _accessor_member_name(method_name, annotation_args)
    is_setter = method_name.startswith('set')
    lines = [f'// @Accessor {method_name}']
    if is_setter:
        lines += [
            f'export function {clean_java_artifact_name(cls_name)}_{sanitize_identifier(method_name)}(target, value) {{',
            f'    if (!target) return;',
            f'    target[{json.dumps(member)}] = value;',
            '}',
        ]
    else:
        lines += [
            f'export function {clean_java_artifact_name(cls_name)}_{sanitize_identifier(method_name)}(target) {{',
            f'    if (!target) return undefined;',
            f'    return target[{json.dumps(member)}];',
            '}',
        ]
    return lines

def _emit_invoker_stub(cls_name: str, method_name: str) -> list[str]:
    sid = clean_java_artifact_name(cls_name)
    mid = sanitize_identifier(method_name)
    return [
        f'// @Invoker {method_name}',
        f'export function {sid}_{mid}(target, ...args) {{',
        '    if (!target) return undefined;',
        f'    const fn = target[{json.dumps(method_name)}];',
        '    if (typeof fn !== "function") return undefined;',
        '    return fn.apply(target, args);',
        '}',
    ]

def _emit_shadow_notice(method_name: str) -> list[str]:
    return [f'// @Shadow {method_name} is preserved as a field/method alias in source only.']

def _mixin_manifest_entry(path: str, cls_name: str, targets: list[str], methods: list[dict]) -> dict:
    return {
        'path': path,
        'class_name': cls_name,
        'targets': targets,
        'methods': [
            {
                'name': m['name'],
                'annotations': m['annotations'],
                'return_type': m['return_type'],
                'params': m['params'],
            } for m in methods
        ],
    }

def run_pipeline(source_root: str = "."):
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
    global _PORTING_NOTES
    _PORTING_NOTES = []
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
        global _ALL_JAVA_FILES
        _ALL_JAVA_FILES = java_files
    with _logger.phase("Pre-scanning registries", total=0, unit="step", colour="blue"):
        detected_mod_id = run_prescan(java_files, namespace)
        if detected_mod_id and detected_mod_id != namespace:

            namespace = detected_mod_id
        build_renderer_entity_map()
    with _logger.phase("Converting LayerDefinition models", total=0, unit="step", colour="blue"):
        global _LAYERDEF_GEO_MAP
        _LAYERDEF_GEO_MAP = scan_and_convert_layerdefinition_models(java_files, namespace)
        if _LAYERDEF_GEO_MAP:
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

                item_content_signals = [
                    bool(re.search(r'\bextends\s+(?:' + _ITEM_BASES + r')\b', code)),
                    bool(re.search(r'Item\.Properties\(\)|new\s+Item\.Properties\b|Item\.Properties\.of\b', code)),
                    bool(re.search(r'\.stacksTo\s*\(|\.durability\s*\(|FoodProperties\.Builder\b', code)),
                    bool(re.search(r'@Override\s+public\s+\w+\s+use\s*\(Level|InteractionResultHolder<ItemStack>', code)),
                    fname_item_hint,
                    bool(re.search(r'\b(?:' + _ITEM_BASES.replace('|', r'\b|\b') + r')\b', superchain_str)),
                ]
                is_item = sum(item_content_signals) >= 2 or (fname_item_hint and sum(item_content_signals) >= 1)
                block_content_signals = [
                    bool(re.search(r'\bextends\s+(?:' + _BLOCK_BASES + r')\b', code)),
                    bool(re.search(r'BlockBehaviour\.Properties|Block\.Properties\s*\.of\b|BlockBehaviour\.Properties\.of\b', code)),
                    bool(re.search(r'\.strength\s*\(|\.noCollission\s*\(|\.lightLevel\s*\(|\.randomTicks\s*\(', code)),
                    bool(re.search(r'@Override\s+public\s+\w+\s+use\s*\(BlockState|getStateForPlacement\s*\(', code)),
                    fname_block_hint,
                    bool(re.search(r'\b(?:' + _BLOCK_BASES.replace('|', r'\b|\b') + r')\b', superchain_str)),
                ]
                is_block = sum(block_content_signals) >= 2 or (fname_block_hint and sum(block_content_signals) >= 1)
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
                    if cls and cls in ENTITY_REGISTRY:
                        entity_identifier = ENTITY_REGISTRY[cls]
                    else:
                        reg_name = None
                        for reg_pat in [
                            r'setRegistryName\s*\(\s*["\']([a-z0-9_:-]+)["\']',
                            r'\.register\s*\(\s*["\']([a-z0-9_]+)["\']\s*,\s*[^;]*?' + re.escape(cls or "") + r'::new',
                            r'EntityType\.Builder[^;]*\.build\s*\(\s*["\']([a-z0-9_]+)["\']',
                        ]:
                            m = re.search(reg_pat, code, re.I | re.DOTALL)
                            if m:
                                raw = m.group(1)
                                reg_name = raw if ":" in raw else f"{namespace}:{raw}"
                                break
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
            if _nbt_cls and re.search(
                r'addAdditionalSaveData|readAdditionalSaveData', _nbt_code
            ):
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
            if line.startswith("[prune]"):
                _logger._original_print(f"      {line}")
        prune_removed = sum(1 for l in prune_log if l.startswith("[prune]"))
        prune_warned  = sum(1 for l in prune_log if l.startswith("[warn]"))
        if prune_removed or prune_warned:
            _logger._original_print(
                f"    Pruner: removed {prune_removed} file(s), {prune_warned} warning(s)"
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
    current_dir = os.getcwd()
    for item in os.listdir(current_dir):
        if os.path.isdir(item) and item.startswith("src"):
            try:

                shutil.rmtree(item)
            except Exception as e:
                _warn(f"Failed to delete {item}: {e}")

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

_LEGACY_RUN_PIPELINE = run_pipeline

JAVA_IMPORT_RE = re.compile(r'(?m)^\s*import\s+([\w.\*]+)\s*;')
JAVA_PACKAGE_RE = re.compile(r'(?m)^\s*package\s+([\w.]+)\s*;')
MIXIN_ANNOTATION_RE = re.compile(r'@Mixin\s*\((.*?)\)', re.DOTALL)
INJECT_ANNOTATION_RE = re.compile(r'@Inject\s*\((.*?)\)\s*(?:@[^\n]+\s*)*(?:public|protected|private|static|final|native|synchronized|abstract|\s)+[\w<>,\[\]]+\s+(\w+)\s*\(', re.DOTALL)
REDIRECT_ANNOTATION_RE = re.compile(r'@Redirect\s*\((.*?)\)\s*(?:@[^\n]+\s*)*(?:public|protected|private|static|final|native|synchronized|abstract|\s)+[\w<>,\[\]]+\s+(\w+)\s*\(', re.DOTALL)
OVERWRITE_ANNOTATION_RE = re.compile(r'@Overwrite\b(?:[^\n]*\n)+?(?:public|protected|private|static|final|native|synchronized|abstract|\s)+[\w<>,\[\]]+\s+(\w+)\s*\(', re.DOTALL)
ACCESSOR_ANNOTATION_RE = re.compile(r'@Accessor\b')
INVOKER_ANNOTATION_RE = re.compile(r'@Invoker\b')
FABRIC_ENTRYPOINT_RE = re.compile(r'implements\s+([^\{]+)')

def _strip_java_comments(source: str) -> str:
    source = re.sub(r'/\*.*?\*/', '', source, flags=re.DOTALL)
    source = re.sub(r'(?m)//.*$', '', source)
    return source

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

def _extract_block(text: str, start_index: int) -> str:
    if start_index < 0 or start_index >= len(text):
        return ''
    brace = text.find('{', start_index)
    if brace == -1:
        return ''
    depth = 0
    for i in range(brace, len(text)):
        ch = text[i]
        if ch == '{':
            depth += 1
        elif ch == '}':
            depth -= 1
            if depth == 0:
                return text[brace + 1:i]
    return text[brace + 1:]

def _extract_paren_block(text: str, open_index: int) -> str:
    if open_index < 0 or open_index >= len(text) or text[open_index] != '(':
        return ''
    depth = 0
    in_string = False
    in_char = False
    escape = False
    i = open_index
    while i < len(text):
        ch = text[i]
        if escape:
            escape = False
        elif ch == '\\' and (in_string or in_char):
            escape = True
        elif in_string:
            if ch == '"':
                in_string = False
        elif in_char:
            if ch == "'":
                in_char = False
        elif ch == '"':
            in_string = True
        elif ch == "'":
            in_char = True
        elif ch == '(':
            depth += 1
        elif ch == ')':
            depth -= 1
            if depth == 0:
                return text[open_index + 1:i]
        i += 1
    return text[open_index + 1:]

def _read_text_file(path: str) -> str:
    try:
        with open(path, 'r', encoding='utf-8', errors='ignore') as fh:
            return fh.read()
    except Exception:
        return ''

class JavaAST:
    def __init__(self, source: str):
        self._src = source or ''
        self._clean = _strip_java_comments(self._src)
        self._tree = None
        self._parsed = False

    def _parse(self):
        if self._parsed:
            return
        self._parsed = True
        if not JAVALANG_AVAILABLE:
            return
        try:
            self._tree = javalang.parse.parse(self._src)
        except Exception:
            self._tree = None

    def _classes(self):
        self._parse()
        if self._tree is None:
            return []
        return [node for _, node in self._tree.filter(javalang.tree.TypeDeclaration)]

    def primary_class_name(self) -> Optional[str]:
        self._parse()
        if self._tree is not None:
            for _, node in self._tree.filter(javalang.tree.TypeDeclaration):
                if hasattr(node, 'name'):
                    return node.name
        m = re.search(r'\bclass\s+(\w+)', self._clean)
        if m:
            return m.group(1)
        m = re.search(r'\binterface\s+(\w+)', self._clean)
        if m:
            return m.group(1)
        m = re.search(r'\benum\s+(\w+)', self._clean)
        if m:
            return m.group(1)
        return None

    def package_name(self) -> Optional[str]:
        m = JAVA_PACKAGE_RE.search(self._clean)
        return m.group(1) if m else None

    def imports(self) -> List[str]:
        return JAVA_IMPORT_RE.findall(self._clean)

    def annotation_value(self, annotation_name: str) -> Optional[str]:
        self._parse()
        if self._tree is not None:
            for _, node in self._tree.filter(javalang.tree.Annotation):
                if getattr(node, 'name', None) == annotation_name:
                    elem = getattr(node, 'element', None)
                    if elem is None:
                        return None
                    if hasattr(elem, 'value'):
                        v = elem.value
                        return v.strip('"\'') if isinstance(v, str) else str(v)
                    return str(elem)
        m = re.search(rf'@{re.escape(annotation_name)}\s*\(\s*["\']([^"\']+)["\']\s*\)', self._clean)
        return m.group(1) if m else None

    def field_string_values(self, field_names: Set[str]) -> Dict[str, str]:
        out: Dict[str, str] = {}
        self._parse()
        if self._tree is not None:
            for _, node in self._tree.filter(javalang.tree.FieldDeclaration):
                for decl in getattr(node, 'declarators', []) or []:
                    if decl.name in field_names and getattr(decl, 'initializer', None) is not None:
                        init = decl.initializer
                        if isinstance(init, javalang.tree.Literal) and isinstance(init.value, str):
                            out[decl.name] = init.value.strip('"\'')
        if not out:
            for name in field_names:
                m = re.search(rf'\b{name}\b\s*=\s*["\']([^"\']+)["\']', self._clean)
                if m:
                    out[name] = m.group(1)
        return out

    def get_class_declarations(self) -> List:
        self._parse()
        if self._tree is None:
            return []
        return [node for _, node in self._tree.filter(javalang.tree.ClassDeclaration)]

    def superclass_name(self, cls_name: Optional[str] = None) -> Optional[str]:
        self._parse()
        if self._tree is not None:
            for _, node in self._tree.filter(javalang.tree.ClassDeclaration):
                if cls_name and node.name != cls_name:
                    continue
                if node.extends and hasattr(node.extends, 'name'):
                    return node.extends.name
        m = re.search(r'\bclass\s+' + (re.escape(cls_name) if cls_name else r'\w+') + r'\s+extends\s+(\w+)', self._clean)
        return m.group(1) if m else None

    def implemented_interfaces(self, cls_name: Optional[str] = None) -> List[str]:
        self._parse()
        if self._tree is not None:
            for _, node in self._tree.filter(javalang.tree.ClassDeclaration):
                if cls_name and node.name != cls_name:
                    continue
                return [i.name for i in (node.implements or []) if hasattr(i, 'name')]
        m = re.search(r'\bclass\s+' + (re.escape(cls_name) if cls_name else r'\w+') + r'[^\{]*implements\s+([\w\s,<>.?]+)', self._clean)
        if not m:
            return []
        return [sanitize_identifier(x).split('.')[-1] for x in re.split(r',', m.group(1)) if x.strip()]

    def all_class_extends(self) -> List[Tuple[str, str]]:
        out: List[Tuple[str, str]] = []
        for cls in self.get_class_declarations():
            if getattr(cls, 'extends', None) and hasattr(cls.extends, 'name'):
                out.append((cls.name, cls.extends.name))
        if out:
            return out
        for m in re.finditer(r'\bclass\s+(\w+)\s+extends\s+(\w+)', self._clean):
            out.append((m.group(1), m.group(2)))
        return out

    def method_names(self) -> Set[str]:
        names: Set[str] = set()
        self._parse()
        if self._tree is not None:
            for _, node in self._tree.filter(javalang.tree.MethodDeclaration):
                names.add(node.name)
        if not names:
            for m in re.finditer(r'\b(?:public|protected|private|static|final|native|synchronized|abstract|\s)+[\w<>,\[\]]+\s+(\w+)\s*\(', self._clean):
                names.add(m.group(1))
        return names

    def method_body_source(self, method_name: str) -> Optional[str]:
        self._parse()
        if self._tree is not None:
            for _, node in self._tree.filter(javalang.tree.MethodDeclaration):
                if node.name != method_name or not getattr(node, 'position', None):
                    continue
                lines = self._src.splitlines()
                start = max(0, node.position.line - 1)
                snippet = '\n'.join(lines[start:start + 500])
                brace = snippet.find('{')
                if brace == -1:
                    return snippet
                depth = 0
                for i in range(brace, len(snippet)):
                    if snippet[i] == '{':
                        depth += 1
                    elif snippet[i] == '}':
                        depth -= 1
                        if depth == 0:
                            return snippet[brace + 1:i]
                return snippet[brace + 1:]
        return _extract_method_body(self._src, method_name)

    def all_string_literals(self) -> List[str]:
        out: List[str] = []
        self._parse()
        if self._tree is not None:
            for _, node in self._tree.filter(javalang.tree.Literal):
                if isinstance(node.value, str) and node.value.startswith('"'):
                    out.append(node.value.strip('"'))
        if not out:
            out.extend(re.findall(r'"([^"\\]*(?:\\.[^"\\]*)*)"', self._clean))
        return out

    def object_creations_of(self, class_name: str) -> List:
        self._parse()
        if self._tree is None:
            return []
        return [node for _, node in self._tree.filter(javalang.tree.ClassCreator) if getattr(getattr(node, 'type', None), 'name', None) == class_name]

    def all_object_creation_types(self) -> List[str]:
        self._parse()
        if self._tree is None:
            return []
        out = []
        for _, node in self._tree.filter(javalang.tree.ClassCreator):
            if getattr(getattr(node, 'type', None), 'name', None):
                out.append(node.type.name)
        return out

    def invocations_of(self, method_name: str) -> List:
        self._parse()
        if self._tree is None:
            return []
        return [node for _, node in self._tree.filter(javalang.tree.MethodInvocation) if getattr(node, 'member', None) == method_name]

    def instanceof_types(self) -> Set[str]:
        out: Set[str] = set()
        self._parse()
        if self._tree is not None:
            for _, node in self._tree.filter(javalang.tree.BinaryOperation):
                if getattr(node, 'operator', None) == 'instanceof' and hasattr(node.operandr, 'name'):
                    out.add(node.operandr.name)
        if not out:
            for m in re.finditer(r'instanceof\s+(\w+)', self._clean):
                out.add(m.group(1))
        return out

    def class_extends(self, target_name: str, cls_name: Optional[str] = None) -> bool:
        return any(parent == target_name and (cls_name is None or child == cls_name) for child, parent in self.all_class_extends())

    @staticmethod
    def strip_generics(name: str) -> str:
        return re.sub(r'<.*?>', '', name or '').strip()

    @staticmethod
    def first_string_arg(invocation_node) -> Optional[str]:
        args = getattr(invocation_node, 'arguments', None) or []
        for arg in args:
            if isinstance(arg, javalang.tree.Literal) and isinstance(arg.value, str) and arg.value.startswith('"'):
                return arg.value.strip('"')
        return None

    @staticmethod
    def translate_java_body_to_js(java_body: str, event_type: str, param: str, namespace: str, safe_name: str) -> list:
        if not java_body:
            return []
        try:
            if JAVALANG_AVAILABLE:
                dummy_code = f"""
public class Dummy {{
    public void dummy() {{
        {java_body}
    }}
}}
"""
                tree = javalang.parse.parse(dummy_code)
                result_lines = []
                player = _get_player_var(event_type, param)
                for _, node in tree.filter(javalang.tree.MethodDeclaration):
                    if node.name == 'dummy':
                        for stmt in node.body or []:
                            result_lines.extend(translate_statement(stmt, player, namespace, JavaSymbolTable()))
                        return result_lines
        except Exception:
            pass
        fallback = [f'// Fallback translation for {safe_name}']
        for raw_line in java_body.splitlines():
            if raw_line.strip():
                fallback.append(f'// {raw_line.rstrip()}')
        return fallback


class JavaSymbolTable:
    JAVA_TYPE_TO_BEDROCK = {
        'Player': 'Player', 'ServerPlayer': 'Player', 'LocalPlayer': 'Player',
        'Entity': 'Entity', 'LivingEntity': 'Entity', 'Mob': 'Entity', 'Monster': 'Entity',
        'ItemStack': 'ItemStack', 'Item': 'ItemTypeStr', 'BlockPos': 'Vector3', 'Vec3': 'Vector3',
        'Vec3i': 'Vector3', 'Vector3f': 'Vector3', 'BlockState': 'BlockPermutation',
        'Level': 'Dimension', 'ServerLevel': 'Dimension', 'World': 'Dimension',
        'Container': 'Container', 'Inventory': 'Container', 'SimpleContainer': 'Container',
        'CompoundTag': 'DynamicProperties', 'CompoundNBT': 'DynamicProperties',
        'ListTag': 'DynamicArray', 'ResourceLocation': 'string',
        'int': 'number', 'float': 'number', 'double': 'number', 'long': 'number', 'short': 'number', 'byte': 'number',
        'boolean': 'boolean', 'String': 'string', 'void': 'void', 'Object': 'any',
    }

    TYPE_METHOD_MAP = {
        'Player': {
            'sendMessage': '{0}.sendMessage({1})', 'getHealth': '{0}.getComponent("minecraft:health").currentValue',
            'setHealth': '{0}.getComponent("minecraft:health").setCurrentValue({1})', 'getInventory': '{0}.getComponent("minecraft:inventory").container',
            'getPosition': '{0}.location', 'setPosition': '{0}.teleport({1})', 'addExperiencePoints': '{0}.addExperience({1})',
        },
        'Entity': {
            'getHealth': '{0}.getComponent("minecraft:health").currentValue', 'setHealth': '{0}.getComponent("minecraft:health").setCurrentValue({1})',
            'getPosition': '{0}.location', 'setPosition': '{0}.teleport({1})', 'getVelocity': '{0}.getVelocity()', 'setVelocity': '{0}.applyImpulse({1})',
            'kill': '{0}.kill()', 'remove': '{0}.remove()', 'addTag': '{0}.addTag({1})', 'removeTag': '{0}.removeTag({1})', 'hasTag': '{0}.hasTag({1})',
        },
        'ItemStack': {
            'getCount': '{0}.amount', 'setCount': '{0}.amount = {1}', 'grow': '{0}.amount += {1}', 'shrink': '{0}.amount -= {1}', 'isEmpty': '({0}.amount <= 0)',
        },
        'Dimension': {
            'setBlockState': '{0}.getBlock({1}).setPermutation({2})', 'getBlockState': '{0}.getBlock({1}).permutation', 'addParticle': '{0}.spawnParticle({1}, {2})',
            'playSound': '{0}.playSound({1}, {2})',
        },
        'DynamicProperties': {
            'getInt': '({0}.getDynamicProperty({1}) ?? 0)', 'putInt': '{0}.setDynamicProperty({1}, {2})',
            'getString': '({0}.getDynamicProperty({1}) ?? "")', 'putString': '{0}.setDynamicProperty({1}, {2})',
            'getBoolean': '({0}.getDynamicProperty({1}) ?? false)', 'putBoolean': '{0}.setDynamicProperty({1}, {2})',
        },
        'Vector3': {
            'add': '{{ x: {0}.x + {1}.x, y: {0}.y + {1}.y, z: {0}.z + {1}.z }}', 'subtract': '{{ x: {0}.x - {1}.x, y: {0}.y - {1}.y, z: {0}.z - {1}.z }}',
            'scale': '{{ x: {0}.x * {1}, y: {0}.y * {1}, z: {0}.z * {1} }}', 'length': 'Math.sqrt({0}.x**2 + {0}.y**2 + {0}.z**2)',
        },
    }

    _CAP_ENERGY = {'receiveEnergy', 'extractEnergy', 'getEnergyStored', 'getMaxEnergyStored', 'canReceive', 'canExtract'}
    _CAP_FLUID = {'fill', 'drain', 'getFluidAmount', 'getTankCapacity', 'getFluidInTank', 'getTanks', 'isFluidValid'}
    _CAP_ITEM = {'insertItem', 'extractItem', 'getStackInSlot', 'getSlots', 'isItemValid', 'getSlotLimit'}
    _CAP_ITEMSTACK = {'getCount', 'setCount', 'grow', 'shrink', 'isEmpty'}

    def __init__(self):
        self.classes: Dict[str, Dict] = {}
        self.variables: Dict[str, str] = {}
        self._qualifier_type_cache: Dict[str, str] = {}
        self.method_return_types: Dict[str, str] = {}
        self._method_to_capability: Dict[str, str] = {}

    def register_class(self, class_name: str, superclass: Optional[str] = None, interfaces: List[str] = None):
        self.classes.setdefault(class_name, {'superclass': superclass, 'interfaces': interfaces or [], 'methods': {}, 'fields': {}})

    def register_method(self, class_name: str, method_name: str, return_type: str, params: Dict[str, str]):
        self.register_class(class_name)
        self.classes[class_name]['methods'][method_name] = {'return': return_type, 'params': params}
        self.method_return_types[f'{class_name}.{method_name}'] = return_type

    def register_field(self, class_name: str, field_name: str, field_type: str):
        self.register_class(class_name)
        self.classes[class_name]['fields'][field_name] = field_type

    def set_variable_type(self, var_name: str, var_type: str):
        if not var_name:
            return
        self.variables[var_name] = var_type
        resolved = self._resolve_bedrock_type(var_type)
        if resolved:
            self._qualifier_type_cache[var_name] = resolved

    def get_variable_type(self, var_name: str) -> Optional[str]:
        return self.variables.get(var_name)

    def _resolve_bedrock_type(self, java_type: str) -> Optional[str]:
        base = re.sub(r'<.*?>', '', java_type or '').strip()
        return self.JAVA_TYPE_TO_BEDROCK.get(base)

    def get_bedrock_type_for_var(self, var_name: str) -> Optional[str]:
        if not var_name:
            return None
        if var_name in self._qualifier_type_cache:
            return self._qualifier_type_cache[var_name]
        if var_name in self.variables:
            return self._resolve_bedrock_type(self.variables[var_name])
        low = var_name.lower()
        if low in {'player', 'p', 'serverplayer', 'localplayer'}:
            return 'Player'
        if low in {'entity', 'mob', 'e', 'target', 'attacker', 'victim'}:
            return 'Entity'
        if low in {'stack', 'itemstack', 'item', 'helditem', 'mainhand', 'offhand'}:
            return 'ItemStack'
        if low in {'level', 'world', 'dimension', 'serverlevel', 'dim'}:
            return 'Dimension'
        if low in {'nbt', 'tag', 'compound', 'data', 'persistentdata'}:
            return 'DynamicProperties'
        if low in {'pos', 'blockpos', 'position', 'origin', 'loc', 'location'}:
            return 'Vector3'
        if low in {'inventory', 'container', 'inv', 'chest', 'slots'}:
            return 'Container'
        return None

    def resolve_method_call(self, qualifier: str, method: str, args: List[str]) -> Optional[str]:
        btype = self.get_bedrock_type_for_var(qualifier)
        if not btype:
            return None
        tmpl = self.TYPE_METHOD_MAP.get(btype, {}).get(method)
        if not tmpl:
            return None
        result = tmpl.replace('{0}', qualifier)
        for i, arg in enumerate(args):
            result = result.replace(f'{{{i + 1}}}', arg)
        return result

    def method_belongs_to_capability(self, method_name: str) -> Optional[str]:
        if method_name in self._CAP_ENERGY:
            return 'energy'
        if method_name in self._CAP_FLUID:
            return 'fluid'
        if method_name in self._CAP_ITEM:
            return 'item_handler'
        if method_name in self._CAP_ITEMSTACK:
            return 'itemstack'
        return None

    def _scan_regex(self, java_code: str):
        src = _strip_java_comments(java_code)
        for m in re.finditer(r'\bclass\s+(\w+)(?:\s+extends\s+(\w+))?', src):
            self.register_class(m.group(1), m.group(2))
        for m in re.finditer(r'(?m)^\s*(?:public|private|protected|static|final|volatile|transient|\s)+\s*(\w+(?:<[^>]+>)?)\s+(\w+)\s*(?:=|;)', src):
            self.set_variable_type(m.group(2), m.group(1))
        for m in re.finditer(r'\b(?:public|private|protected|static|final|synchronized|native|abstract|\s)+\s*(\w+(?:<[^>]+>)?)\s+(\w+)\s*\(([^)]*)\)', src):
            ret, name, params = m.groups()
            param_map = {}
            for p in [x.strip() for x in params.split(',') if x.strip()]:
                parts = p.split()
                if len(parts) >= 2:
                    param_map[parts[-1]] = parts[-2]
                    self.set_variable_type(parts[-1], parts[-2])
            if self.classes:
                cls = next(reversed(self.classes))
                self.register_method(cls, name, ret, param_map)

    def scan_java_file(self, java_code: str):
        if JAVALANG_AVAILABLE:
            try:
                tree = javalang.parse.parse(java_code)
                for _, node in tree.filter(javalang.tree.ClassDeclaration):
                    super_name = node.extends.name if node.extends and hasattr(node.extends, 'name') else None
                    interfaces = [i.name for i in (node.implements or []) if hasattr(i, 'name')]
                    self.register_class(node.name, super_name, interfaces)
                    for field in node.fields:
                        ftype = getattr(field.type, 'name', str(field.type))
                        for decl in field.declarators:
                            self.register_field(node.name, decl.name, ftype)
                            self.set_variable_type(decl.name, ftype)
                    for method in node.methods:
                        ret = getattr(method.return_type, 'name', 'void') if method.return_type else 'void'
                        params = {}
                        for p in method.parameters or []:
                            ptype = getattr(p.type, 'name', str(p.type))
                            params[p.name] = ptype
                            self.set_variable_type(p.name, ptype)
                        self.register_method(node.name, method.name, ret, params)
                for _, node in tree.filter(javalang.tree.LocalVariableDeclaration):
                    ltype = getattr(node.type, 'name', str(node.type))
                    for decl in node.declarators:
                        self.set_variable_type(decl.name, ltype)
                return
            except Exception:
                pass
        self._scan_regex(java_code)


def translate_method_invocation(invocation: object, player: str, namespace: str, symbol_table: JavaSymbolTable) -> Optional[str]:
    member = getattr(invocation, 'member', '')
    qualifier = getattr(invocation, 'qualifier', None)
    if isinstance(qualifier, list):
        qualifier = qualifier[0] if qualifier else None
    args = getattr(invocation, 'arguments', []) or []
    arg_strs = [translate_expression(a) for a in args]
    arg_strs = [a for a in arg_strs if a is not None]
    if qualifier and isinstance(qualifier, str):
        resolved = symbol_table.resolve_method_call(qualifier, member, arg_strs)
        if resolved:
            return resolved
    if member in {'receiveEnergy', 'extractEnergy'} and arg_strs:
        return f'{member}({player}, {arg_strs[0]});'
    nbt_result = NBTTranslator.translate_nbt_call(member, args, namespace, player)
    if nbt_result:
        return nbt_result
    cap_type = symbol_table.method_belongs_to_capability(member)
    if cap_type == 'energy' and member == 'getEnergyStored':
        return f'getEnergyStored({player})'
    if cap_type == 'fluid' and member in {'fill', 'drain'}:
        return f'{member}({player}, {", ".join(arg_strs)})'
    bedrock_call = JavaToBedrockMethodMap.translate_method_call(member, args, qualifier)
    if bedrock_call:
        return bedrock_call
    return None

def translate_statement(stmt: object, player: str, namespace: str, symbol_table: Optional[JavaSymbolTable] = None) -> list:
    symbol_table = symbol_table or JavaSymbolTable()
    out: list[str] = []
    if JAVALANG_AVAILABLE and isinstance(stmt, javalang.tree.StatementExpression):
        expr = stmt.expression
        if isinstance(expr, javalang.tree.MethodInvocation):
            line = translate_method_invocation(expr, player, namespace, symbol_table)
            if line:
                return [f'    {line}' if not line.strip().endswith(';') else f'    {line}']
        if isinstance(expr, javalang.tree.Assignment):
            left = translate_expression(expr.expressionl)
            right = translate_expression(expr.value)
            if left and right:
                return [f'    {left} = {right};']
    if JAVALANG_AVAILABLE and isinstance(stmt, javalang.tree.LocalVariableDeclaration):
        for decl in stmt.declarators:
            init = translate_expression(decl.initializer) if decl.initializer else None
            out.append(f'    let {decl.name}' + (f' = {init}' if init else '') + ';')
        return out
    if JAVALANG_AVAILABLE and isinstance(stmt, javalang.tree.IfStatement):
        cond = translate_expression(stmt.condition)
        if cond:
            out.append(f'    if ({cond}) {{')
            body = stmt.then_statement
            stmts = body.statements if hasattr(body, 'statements') else ([body] if body else [])
            for s in stmts:
                out.extend(translate_statement(s, player, namespace, symbol_table))
            out.append('    }')
            if stmt.else_statement:
                out.append('    else {')
                body = stmt.else_statement
                stmts = body.statements if hasattr(body, 'statements') else ([body] if body else [])
                for s in stmts:
                    out.extend(translate_statement(s, player, namespace, symbol_table))
                out.append('    }')
            return out
    if JAVALANG_AVAILABLE and isinstance(stmt, javalang.tree.ReturnStatement):
        expr = translate_expression(stmt.expression) if stmt.expression else None
        return [f'    return {expr};' if expr else '    return;']
    if JAVALANG_AVAILABLE and isinstance(stmt, javalang.tree.ForStatement):
        out.append('    for (let i = 0; i < 1000; i++) {')
        body = stmt.body
        stmts = body.statements if hasattr(body, 'statements') else ([body] if body else [])
        for s in stmts:
            out.extend(translate_statement(s, player, namespace, symbol_table))
        out.append('    }')
        return out
    return out

def _extract_method_body(source: str, method_name) -> Optional[str]:
    if not source or not method_name:
        return None

    if isinstance(method_name, list):
        for name in method_name:
            result = _extract_method_body(source, name)
            if result:
                return result
        return None
    src = _strip_java_comments(source)
    if JAVALANG_AVAILABLE:
        try:
            tree = javalang.parse.parse(source)
            for _, node in tree.filter(javalang.tree.MethodDeclaration):
                if node.name != method_name:
                    continue
                pos = getattr(node, 'position', None)
                if pos:
                    lines = source.splitlines()
                    start = max(0, pos.line - 1)
                    snippet = '\n'.join(lines[start:start + 600])
                    brace = snippet.find('{')
                    if brace >= 0:
                        depth = 0
                        for i in range(brace, len(snippet)):
                            if snippet[i] == '{':
                                depth += 1
                            elif snippet[i] == '}':
                                depth -= 1
                                if depth == 0:
                                    return snippet[brace + 1:i]
        except Exception:
            pass
    pat = re.compile(rf'\b{re.escape(method_name)}\s*\([^)]*\)\s*\{{', re.DOTALL)
    m = pat.search(src)
    if not m:
        return None
    return _extract_block(src, m.start())

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

def _translate_mixin_body_to_js(body: str, namespace: str, safe_name: str) -> list[str]:
    if not body:
        return []
    lines = []
    if JAVALANG_AVAILABLE:
        try:
            dummy = f'public class Dummy {{ void d() {{ {body} }} }}'
            tree = javalang.parse.parse(dummy)
            for _, node in tree.filter(javalang.tree.MethodDeclaration):
                for stmt in node.body or []:
                    lines.extend(translate_statement(stmt, 'entity', namespace, JavaSymbolTable()))
            return lines
        except Exception:
            pass
    for ln in body.splitlines():
        ln = ln.rstrip()
        if ln:
            lines.append('    // ' + ln)
    return lines

def scan_mixins(java_files: Dict[str, str], namespace: str) -> list[str]:
    notes: list[str] = []
    out_dir = os.path.join(BP_FOLDER, 'scripts')
    os.makedirs(out_dir, exist_ok=True)
    for path, code in java_files.items():
        if '@Mixin' not in code and 'mixin' not in os.path.basename(path).lower():
            continue
        target = _extract_mixin_target(code)
        cls_name = JavaAST(code).primary_class_name() or os.path.splitext(os.path.basename(path))[0]
        safe_name = clean_java_artifact_name(cls_name)
        script_lines = [f'import {{ world, system }} from "@minecraft/server";', '']
        wrote = False
        if target:
            script_lines += [f'// Mixin target: {target}', f'// Source: {cls_name}', '']
        for ann_re, kind in ((INJECT_ANNOTATION_RE, 'inject'), (REDIRECT_ANNOTATION_RE, 'redirect'), (OVERWRITE_ANNOTATION_RE, 'overwrite')):
            for m in ann_re.finditer(code):
                annotation_args = m.group(1)
                method_name = m.group(2) if kind != 'overwrite' else m.group(1)
                method_body = _extract_method_body(code, method_name) or ''
                event = _mixin_event_guess(target, method_name, annotation_args, method_body)
                if event and event.startswith('system.runInterval'):
                    script_lines += [f'// {kind} {method_name} -> scheduled tick', 'system.runInterval(() => {']
                    script_lines += _translate_mixin_body_to_js(method_body, namespace, safe_name) or ['    // tick body could not be translated cleanly']
                    script_lines += ['}, 1);', '']
                elif event:
                    script_lines += [f'// {kind} {method_name} -> {event}', f'{event}.subscribe((event) => {{']
                    script_lines += _translate_mixin_body_to_js(method_body, namespace, safe_name) or ['    const entity = event.entity ?? event.player ?? event.hurtEntity ?? event.block ?? null;']
                    script_lines += ['});', '']
                else:
                    notes.append(f'[mixin] {cls_name}: {kind} {method_name} had no confident Bedrock mapping')
                wrote = True
        if ACCESSOR_ANNOTATION_RE.search(code) or INVOKER_ANNOTATION_RE.search(code):
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
        '    return dimension ? dimension.containsBiomes(volume, { biomeFilter, isSuperset }) : false;',
        '  },',
        '  fillBlocks: (dimensionId, volume, permutation, options = {}) => {',
        '    const dimension = world.getDimension(dimensionId);',
        '    return dimension ? dimension.fillBlocks(volume, permutation, options) : false;',
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
    _LEGACY_RUN_PIPELINE(source_root)
    try:
        java_files = read_all_java_files('.')
        namespace = detect_mod_id(java_files) if 'detect_mod_id' in globals() else None
        if not namespace:
            namespace = sanitize_identifier(os.path.basename(os.getcwd())) or 'converted'
        global _DEOBFUSCATED_JAVA_FILES
        if not _DEOBFUSCATED_JAVA_FILES and java_files:
            java_files = deobfuscate_java_sources(java_files, namespace)
            global _ALL_JAVA_FILES
            _ALL_JAVA_FILES = java_files
        _enhanced_postpass(namespace, java_files)
    except Exception as e:
        try:
            log_critical_failure(f'Enhanced postpass failed: {e}')
        except Exception:
            pass

_MIXIN_PHASE_RE = re.compile(r'@At\s*\(\s*(?:value\s*=\s*)?["\']([^"\']+)["\']', re.DOTALL)
_MIXIN_NAME_RE = re.compile(r'@(?:Inject|Redirect|Overwrite|Accessor|Invoker|ModifyVariable|ModifyArg|ModifyArgs|ModifyConstant|WrapOperation|WrapWithCondition)\b', re.DOTALL)

def _mixin_target_name(code: str) -> Optional[str]:
    m = MIXIN_ANNOTATION_RE.search(code)
    if not m:
        return None
    body = m.group(1)
    quoted = re.findall(r'["\']([\w.$/]+)["\']', body)
    if quoted:
        return quoted[0].replace('/', '.').split('.')[-1]
    cls = re.search(r'\b([A-Za-z_][A-Za-z0-9_$.]+)\.class\b', body)
    return cls.group(1).split('.')[-1] if cls else None

def _extract_mixin_target(code: str) -> Optional[str]:
    return _mixin_target_name(code)

def _mixin_annotation_names(ann_block: str) -> List[str]:
    return [m.group(1) for m in re.finditer(r'@(\w+)', ann_block or '')]

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

def _infer_mixin_phase(annotation_args: str, body: str) -> str:
    text = f'{annotation_args} {body}'.lower()
    if any(k in text for k in ('cancellable = true', 'cancellable=true', '@at("head")', '@at(value = "head")', '@at("before")', '@at(value = "before")')):
        return 'before'
    if any(k in text for k in ('@at("tail")', '@at(value = "tail")', '@at("return")', '@at(value = "return")', '@at("end")', '@at(value = "end")')):
        return 'after'
    return 'after'

def _split_annotation_args(raw: str) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for part in _split_top_level(raw or ''):
        if '=' in part:
            k, v = part.split('=', 1)
            out[k.strip()] = v.strip()
    return out

def _extract_method_annotation_bundle(code: str, method_name: str) -> Tuple[List[str], str, str, str, List[Tuple[str, str]], Optional[str]]:
    ann, header, params_src, body = _extract_method_signature_block(code, method_name)
    if ann is None:
        return [], '', '', '', [], None
    annotations = _mixin_annotation_names(ann)
    params = _parse_java_params(params_src or '')
    ret_type = ''
    if header:
        hm = re.search(r'([\w<>,\[\].?$]+)\s+' + re.escape(method_name) + r'\s*\(', header)
        ret_type = hm.group(1) if hm else ''
    return annotations, ann or '', header or '', body or '', params, ret_type or None

def _infer_target_event(
    target_cls: str,
    method_name: str,
    body: str,
    at_name: str,
    raw: str,
) -> Optional[str]:
    needle = f'{target_cls} {method_name} {body} {at_name} {raw}'.lower()


    if any(k in needle for k in (
        'tick', 'update', 'inventorytick', 'servertick', 'clienttick', 'aiset', 'dotick',
    )):
        return 'system.runInterval'


    if any(k in needle for k in ('chat', 'sendchat', 'chatsend', 'message')):
        return 'world.beforeEvents.chatSend'


    if any(k in needle for k in ('hurt', 'damage', 'attack', 'hurtentity', 'actuallyhurt')):
        return 'world.afterEvents.entityHurt'


    if any(k in needle for k in ('death', 'die', 'killed', 'ondeath')):
        return 'world.afterEvents.entityDie'

    if any(k in needle for k in ('spawn', 'join', 'entityjoin', 'addedtolevel', 'construct')):
        return 'world.afterEvents.entitySpawn'


    if any(k in needle for k in ('explode', 'explosion', 'detonate')):
        return 'world.afterEvents.explosion'


    if any(k in needle for k in ('break', 'destroy', 'mine', 'removeblock', 'leftclickblock', 'blockbreak')):
        return 'world.afterEvents.playerBreakBlock'


    if any(k in needle for k in ('place', 'useon', 'blockactivated', 'interactblock', 'rightclickblock', 'blockplace')):
        return 'world.afterEvents.playerPlaceBlock'


    if any(k in needle for k in ('itemuse', 'useitem', 'finishusingitem', 'appendtooltip', 'usetick')):
        return 'world.afterEvents.itemUse'


    if any(k in needle for k in ('interact', 'rightclick', 'interactat', 'mount', 'attackentity')):
        return 'world.afterEvents.playerInteractWithEntity'


    if any(k in needle for k in ('pickup', 'pickupitem', 'itempickup')):
        return 'world.afterEvents.playerPickUpItem'
    if any(k in needle for k in ('drop', 'toss', 'throw', 'dropitem')):
        return 'world.afterEvents.playerDropItem'


    if any(k in needle for k in ('craft', 'crafted', 'craftitem')):
        return 'world.afterEvents.itemCompleteUse'


    if any(k in needle for k in ('block', 'tileentity', 'worldgen', 'chunkload')):
        return 'world.afterEvents.playerPlaceBlock'

    return None

def _param_binding_expr(java_type: str) -> str:
    base = java_type.strip()

    base = re.sub(r'<[^>]*>', '', base).replace('[]', '').strip()

    player_types = {
        'Player', 'ServerPlayer', 'LocalPlayer', 'AbstractPlayer',
        'EntityPlayer', 'EntityPlayerMP',
    }
    entity_types = {
        'Entity', 'LivingEntity', 'Mob', 'PathfinderMob',
        'Animal', 'Monster', 'Creeper', 'Zombie', 'Skeleton',
    }
    item_types = {'ItemStack', 'Item', 'ItemType'}
    block_pos_types = {'BlockPos', 'Vec3i', 'ChunkPos'}
    vec_types = {'Vec3', 'Vector3f', 'Vector3d'}
    level_types = {'Level', 'ServerLevel', 'World', 'ServerWorld', 'Dimension'}
    damage_types = {'DamageSource', 'EntityDamageSource'}

    if base in player_types:
        return 'event.player ?? event.entity'
    if base in entity_types:
        return 'event.entity ?? event.hurtEntity ?? event.damagingEntity'
    if base in item_types:
        return 'event.itemStack ?? event.item'
    if base in block_pos_types:
        return 'event.block?.location ?? event.blockLocation'
    if base in vec_types:
        return 'event.entity?.location ?? event.player?.location'
    if base in level_types:
        return 'event.entity?.dimension ?? event.player?.dimension ?? world.getDimension("overworld")'
    if base in damage_types:
        return 'event.damageSource ?? event.cause'
    if base in ('float', 'double', 'int', 'long', 'short', 'byte'):
        return '0'
    if base == 'boolean':
        return 'false'
    if base == 'String':
        return '""'


    return 'event'

def _translate_java_body_to_js(body: str, namespace: str, safe_name: str) -> List[str]:
    if not body:
        return []

    if JAVALANG_AVAILABLE:
        try:
            dummy = f'public class Dummy {{ void d() {{ {body} }} }}'
            tree = javalang.parse.parse(dummy)
            lines: List[str] = []
            for _, node in tree.filter(javalang.tree.MethodDeclaration):
                if node.name != 'd':
                    continue
                for stmt in node.body or []:
                    lines.extend(
                        translate_statement(stmt, 'entity', namespace, JavaSymbolTable())
                    )
            if lines:
                return lines
        except Exception:
            pass


    out: List[str] = []
    for ln in body.splitlines():
        stripped = ln.rstrip()
        if stripped:
            out.append('    // ' + stripped)
    return out

def _event_subscription_lines(
    target_cls: str,
    method_name: str,
    body: str,
    wrapper: str,
    params: List[Tuple[str, str]],
    annotations: Dict[str, List[Tuple[List[str], Dict[str, str]]]],
) -> List[str]:
    chosen = []
    for key in ('Inject', 'Overwrite'):
        if annotations.get(key):
            chosen = annotations[key][0]
            break
    raw = ' '.join(list(chosen[0]) + [f'{k}={v}' for k, v in chosen[1].items()]) if chosen else ''
    at_name_m = re.search(r'@At\s*\(\s*(?:value\s*=\s*)?["\']([^"\']+)["\']', raw, re.DOTALL)
    at_name = at_name_m.group(1) if at_name_m else ''
    event = _infer_target_event(target_cls, method_name, body, at_name, raw)
    if not event:
        return []

    lines = [f'// event bridge for {method_name} -> {event}']
    if event == 'system.runInterval':
        lines += [
            'system.runInterval(() => {',
            f'    {wrapper}();',
            '}, 1);',
            '',
        ]
        return lines

    lines.append(f'{event}.subscribe((event) => {{')
    for ptype, pname in params:
        if 'callbackinfo' in ptype.lower():
            continue
        lines.append(f'    const {pname} = {_param_binding_expr(ptype)};')
    call_args = ', '.join(pname for ptype, pname in params if 'callbackinfo' not in ptype.lower())
    lines.append(f'    {wrapper}({call_args});' if call_args else f'    {wrapper}(event);')
    lines.append('});')
    lines.append('')
    return lines

def _mixin_shadow_lines(cls_name: str, method_name: str, target_cls: str) -> List[str]:
    safe = sanitize_identifier(f'{cls_name}_{method_name}')
    return [
        f'// @Shadow {method_name} from {target_cls}',
        f'export const {safe} = {{ target: {json.dumps(target_cls)}, name: {json.dumps(method_name)} }};',
        '',
    ]

def _mixin_accessor_invoker_lines(
    kind: str,
    cls_name: str,
    method_name: str,
    return_type: str,
    params: List[Tuple[str, str]],
    annotations: Dict[str, List[Tuple[List[str], Dict[str, str]]]],
    target_cls: str,
    safe_name: str,
) -> List[str]:
    wrapper = f'{safe_name}__{method_name}'
    ann = annotations.get(kind, [([], {})])[0]
    named = ann[1]
    explicit = None
    for value in named.values():
        m = re.search(r'"([^"]+)"', value)
        if m:
            explicit = m.group(1)
            break
    if explicit is None and ann[0]:
        first = ann[0][0]
        m = re.search(r'"([^"]+)"', first)
        if m:
            explicit = m.group(1)
    explicit = explicit or method_name

    sig = ', '.join(p for _, p in params)
    if kind == 'Accessor':
        sig = sig or 'target'
        lines = [f'export function {wrapper}({sig}) {{']
        target_param = params[0][1] if params else 'target'
        if return_type.strip().lower() == 'void' or method_name.startswith('set'):
            field_name = explicit
            if method_name.startswith('set') and explicit == method_name:
                field_name = method_name[3:4].lower() + method_name[4:]
            value_name = params[-1][1] if len(params) > 1 else 'value'
            lines += [
                f'    const target = {target_param};',
                '    if (!target) return;',
                f'    target[{json.dumps(field_name)}] = {value_name};',
                '    return;',
            ]
        else:
            field_name = explicit
            lines += [
                f'    const target = {target_param};',
                f'    return target ? target[{json.dumps(field_name)}] : undefined;',
            ]
        lines += ['}', '']
        return lines

    sig = sig or 'target'
    lines = [f'export function {wrapper}({sig}) {{']
    target_param = params[0][1] if params else 'target'
    call_args = ', '.join(p for _, p in params[1:])
    lines += [
        f'    const target = {target_param};',
        f'    if (!target || typeof target[{json.dumps(explicit)}] !== "function") return undefined;',
        f'    return target[{json.dumps(explicit)}]({call_args});' if call_args else f'    return target[{json.dumps(explicit)}]();',
        '}',
        '',
    ]
    return lines

def _mixin_wrapper_lines(
    cls_name: str,
    method_name: str,
    return_type: str,
    params: List[Tuple[str, str]],
    body: str,
    namespace: str,
    safe_name: str,
    annotations: Dict[str, List[Tuple[List[str], Dict[str, str]]]],
    target_cls: str,
) -> List[str]:
    wrapper = f'{safe_name}__{method_name}'
    callback_params = [p for p in params if 'callbackinfo' in p[0].lower()]
    non_callback_params = [p for p in params if 'callbackinfo' not in p[0].lower()]
    signature = ', '.join(p for _, p in non_callback_params)
    lines: List[str] = [f'export function {wrapper}({signature}) {{']

    if callback_params:
        lines.append('    let __mixin_cancelled = false;')
        for ptype, pname in callback_params:
            if 'callbackinforeturnable' in ptype.lower():
                lines += [
                    f'    let __{pname}_returnValue = undefined;',
                    f'    const {pname} = {{',
                    '        cancel: () => { __mixin_cancelled = true; },',
                    f'        setReturnValue: (v) => {{ __mixin_cancelled = true; __{pname}_returnValue = v; }},',
                    f'        getReturnValue: () => __{pname}_returnValue,',
                    '        isCancelled: () => __mixin_cancelled,',
                    '    };',
                ]
            else:
                lines += [
                    f'    const {pname} = {{',
                    '        cancel: () => { __mixin_cancelled = true; },',
                    '        isCancelled: () => __mixin_cancelled,',
                    '    };',
                ]

    local_body = _translate_java_body_to_js(body, namespace, safe_name)
    if not local_body:
        local_body = ['    // no translated body']
    lines.extend(local_body)

    if callback_params:
        lines.append('    if (__mixin_cancelled) return;')
    lines.append('}')
    lines.append('')
    return lines

def _mixin_modifier_lines(
    kind: str,
    cls_name: str,
    method_name: str,
    params: List[Tuple[str, str]],
    body: str,
    namespace: str,
    safe_name: str,
    annotations: Dict[str, List[Tuple[List[str], Dict[str, str]]]],
    target_cls: str,
) -> List[str]:
    wrapper = f'{safe_name}__{method_name}'
    sig = ', '.join(p for _, p in params) or 'value'
    lines: List[str] = [f'export function {wrapper}({sig}) {{']
    local_body = _translate_java_body_to_js(body, namespace, safe_name)
    if kind == 'ModifyConstant':
        original_name = params[0][1] if params else 'original'
        lines.append(f'    const original = {original_name};')
        lines.extend(local_body or ['    return original;'])
        if not any('return' in line for line in local_body):
            lines.append('    return original;')
    elif kind == 'ModifyVariable':
        var_name = params[0][1] if params else 'value'
        lines.append(f'    let value = {var_name};')
        lines.extend(local_body or ['    return value;'])
        if not any('return' in line for line in local_body):
            lines.append('    return value;')
    elif kind in ('ModifyArg', 'ModifyArgs'):
        lines.append('    const args = Array.from(arguments);')
        lines.extend(local_body or ['    return args;'])
        if not any('return' in line for line in local_body):
            lines.append('    return args;')
    elif kind == 'WrapOperation':
        lines.append('    const operation = arguments[0];')
        lines.append('    const args = Array.from(arguments).slice(1);')
        lines.extend(local_body or ['    return operation(...args);'])
        if not any('return' in line for line in local_body):
            lines.append('    return operation(...args);')
    elif kind == 'WrapWithCondition':
        lines.append('    const condition = arguments[0];')
        lines.append('    const operation = arguments[1];')
        lines.append('    const args = Array.from(arguments).slice(2);')
        lines.extend(local_body or ['    return condition ? operation(...args) : undefined;'])
        if not any('return' in line for line in local_body):
            lines.append('    return condition ? operation(...args) : undefined;')
    else:
        lines.extend(local_body or ['    // unsupported modifier body'])
    lines.append('}')
    lines.append('')
    return lines

def scan_fabric_quilt_mixins(java_files: Dict[str, str], namespace: str) -> List[str]:
    return scan_mixins(java_files, namespace)

if __name__ == "__main__":
    main()
