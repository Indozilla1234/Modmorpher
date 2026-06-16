def apply_entity_sounds(bedrock_entity: dict, sounds: dict, namespace: str,
                        entity_name: str):
    if not sounds:
        return
    components = bedrock_entity["minecraft:entity"]["components"]
    entity_id = bedrock_entity["minecraft:entity"]["description"]["identifier"]
    if "ambient" in sounds:
        components["minecraft:ambient_sound_interval"] = {
            "value": 8.0,
            "range": 4.0,
            "event_name": sounds["ambient"]
        }
    SLOT_TO_BEDROCK_EVENT = {
        "ambient": "ambient",
        "hurt":    "hurt",
        "death":   "death",
        "step":    "step",
        "attack":  "attack",
        "swim":    "swim",
        "splash":  "splash",
    }
    events_block = {}
    for slot, event_key in SLOT_TO_BEDROCK_EVENT.items():
        if slot in sounds:
            events_block[event_key] = sounds[slot]
    if events_block:
        _ENTITY_SOUND_EVENTS[entity_id] = {
            "events": events_block,
            "pitch": [0.8, 1.2],
            "volume": 1.0
        }

        _ANIM_ONLY_SLOTS = {"attack"}
        anim_sounds = {k: v for k, v in events_block.items() if k in _ANIM_ONLY_SLOTS}
        _update_rp_entity_sound_effects(entity_id, anim_sounds)
    _SLOT_CATEGORY = {
        "ambient": "ambient",
        "step":    "ambient",
        "swim":    "ambient",
        "splash":  "ambient",
        "hurt":    "hostile",
        "death":   "hostile",
        "attack":  "hostile",
    }
    for slot, sound_key in sounds.items():
        if sound_key not in COLLECTED_SOUND_DEFS:
            file_stem = sound_key.replace(".", "_")
            file_path = f"sound/{file_stem}"
            category = _SLOT_CATEGORY.get(slot, "neutral")
            COLLECTED_SOUND_DEFS[sound_key] = {
                "category": category,
                "sounds": [{"name": file_path}],
                "__stub__": True
            }

def _update_rp_entity_sound_effects(entity_id: str, sounds: dict) -> None:
    if not entity_id or not sounds:
        return
    entity_base = sanitize_identifier(entity_id.split(':')[-1])
    rp_path = os.path.join(RP_FOLDER, 'entity', f'{entity_base}.entity.json')
    if not os.path.exists(rp_path):
        return
    if not sounds:
        return
    try:
        with open(rp_path, 'r', encoding='utf-8') as fh:
            data = json.load(fh)
        desc = data.setdefault('minecraft:client_entity', {}).setdefault('description', {})
        existing = desc.get('sound_effects', {})
        existing.update(sounds)
        desc['sound_effects'] = existing
        with open(rp_path, 'w', encoding='utf-8') as fh:
            json.dump(data, fh, indent=2)
    except Exception:
        pass

def _guess_block_sound_profile(java_code: str, namespace: str, block_id: str) -> dict:
    code = java_code or ''
    sounds = {}
    sound_type = None
    m = re.search(r'SoundType\.([A-Z_]+)', code)
    if m:
        sound_type = m.group(1).lower()
    else:
        m = re.search(r'sound\s*\(\s*SoundType\.([A-Z_]+)', code, re.I)
        if m:
            sound_type = m.group(1).lower()
    if sound_type:
        base = sanitize_sound_key(sound_type)
        sounds = {
            'step': f'{namespace}.{base}.step',
            'place': f'{namespace}.{base}.place',
            'break': f'{namespace}.{base}.break',
            'hit': f'{namespace}.{base}.hit',
            'fall': f'{namespace}.{base}.fall',
        }
    for m in re.finditer(r'(?:playSound|sound)\s*\([^\)]*(?:SoundEvents\.|ModSounds\.|Sounds\.)([A-Z0-9_]+)', code, re.I | re.DOTALL):
        token = m.group(1).lower()
        key = sanitize_sound_key(f'{namespace}.{block_id.split(":")[-1]}.{token}')
        slot = 'place' if any(k in token for k in ('place', 'step', 'hit')) else 'break'
        sounds[slot] = key
    return sounds

def _guess_item_sound_profile(java_code: str, namespace: str, item_id: str) -> dict:
    code = java_code or ''
    sounds = {}
    for m in re.finditer(r'(?:playSound|sound)\s*\([^\)]*(?:SoundEvents\.|ModSounds\.|Sounds\.)([A-Z0-9_]+)', code, re.I | re.DOTALL):
        token = m.group(1).lower()
        key = sanitize_sound_key(f'{namespace}.{item_id.split(":")[-1]}.{token}')
        slot = 'use'
        if any(k in token for k in ('eat', 'drink', 'consume')):
            slot = 'consume'
        elif any(k in token for k in ('equip', 'armor')):
            slot = 'equip'
        elif any(k in token for k in ('swing', 'attack', 'hit')):
            slot = 'swing'
        elif any(k in token for k in ('break', 'shatter')):
            slot = 'break'
        sounds[slot] = key
    if not sounds and re.search(r'FoodProperties|\.food\s*\(|nutrition|saturationMod', code, re.I):
        sounds['consume'] = sanitize_sound_key(f'{namespace}.{item_id.split(":")[-1]}.consume')
    return sounds

def generate_sound_playback_script(namespace: str) -> None:
    entity_entries = []
    for entity_id, entry in sorted(_ENTITY_SOUND_EVENTS.items()):
        desc = entry.get('events') if isinstance(entry, dict) else {}
        if not desc:
            continue
        entity_entries.append({
            'id': entity_id,
            'events': desc,
            'pitch': entry.get('pitch', [1.0, 1.0]) if isinstance(entry, dict) else [1.0, 1.0],
            'volume': entry.get('volume', 1.0) if isinstance(entry, dict) else 1.0,
        })

    if not entity_entries and not BLOCK_SOUND_PROFILES and not ITEM_SOUND_PROFILES:
        return

    lines = ['import { world } from "@minecraft/server";', '', f'// Auto-generated sound router for {namespace}']
    lines.append('const ENTITY_SOUNDS = ' + json.dumps(entity_entries, indent=2) + ';')
    lines.append('const BLOCK_SOUNDS = ' + json.dumps(BLOCK_SOUND_PROFILES, indent=2) + ';')
    lines.append('const ITEM_SOUNDS = ' + json.dumps(ITEM_SOUND_PROFILES, indent=2) + ';')
    lines.extend([
        '',
        'function playSoundAt(dim, soundId, location, volume = 1.0, pitch = 1.0) {',
        '  if (!dim || !soundId || !location) return;',
        '  try {',
        '    dim.playSound(soundId, location, { volume, pitch });',
        '  } catch {',
        '    try { dim.playSound(soundId, location); } catch {}',
        '  }',
        '}',
        '',
        'for (const entry of ENTITY_SOUNDS) {',
        '  const typeId = entry.id;',
        '  const events = entry.events || {};',
        '  if (events.ambient) {',
        '    world.afterEvents.entitySpawn.subscribe(({ entity }) => {',
        '      if (entity?.typeId !== typeId) return;',
        '      playSoundAt(entity.dimension, events.ambient, entity.location, entry.volume, 1.0);',
        '    });',
        '  }',
        '  if (events.hurt) {',
        '    world.afterEvents.entityHurt.subscribe(({ hurtEntity }) => {',
        '      if (hurtEntity?.typeId !== typeId) return;',
        '      playSoundAt(hurtEntity.dimension, events.hurt, hurtEntity.location, entry.volume, 1.0);',
        '    });',
        '  }',
        '  if (events.death) {',
        '    world.afterEvents.entityDie.subscribe(({ deadEntity }) => {',
        '      if (deadEntity?.typeId !== typeId) return;',
        '      playSoundAt(deadEntity.dimension, events.death, deadEntity.location, entry.volume, 1.0);',
        '    });',
        '  }',
        '}',
        '',
        'for (const [blockId, sounds] of Object.entries(BLOCK_SOUNDS)) {',
        '  world.afterEvents.playerPlaceBlock.subscribe(({ block, player }) => {',
        '    if (block?.typeId !== blockId) return;',
        '    const sound = sounds.place || sounds.step || sounds.use;',
        '    if (sound) playSoundAt(player.dimension, sound, block.location, 1.0, 1.0);',
        '  });',
        '  world.afterEvents.playerBreakBlock.subscribe(({ brokenBlockPermutation, player, block }) => {',
        '    const typeId = block?.typeId || brokenBlockPermutation?.type?.id;',
        '    if (typeId !== blockId) return;',
        '    const sound = sounds.break || sounds.hit || sounds.step;',
        '    if (sound) playSoundAt(player.dimension, sound, block?.location || player.location, 1.0, 1.0);',
        '  });',
        '}',
        '',
        'for (const [itemId, sounds] of Object.entries(ITEM_SOUNDS)) {',
        '  world.afterEvents.itemUse.subscribe(({ itemStack, source }) => {',
        '    if (itemStack?.typeId !== itemId) return;',
        '    const sound = sounds.use || sounds.consume || sounds.swing;',
        '    if (sound) playSoundAt(source.dimension, sound, source.location, 1.0, 1.0);',
        '  });',
        '  world.afterEvents.itemCompleteUse.subscribe(({ itemStack, source }) => {',
        '    if (itemStack?.typeId !== itemId) return;',
        '    const sound = sounds.consume || sounds.use;',
        '    if (sound) playSoundAt(source.dimension, sound, source.location, 1.0, 1.0);',
        '  });',
        '}',
    ])
    out_path = os.path.join(BP_FOLDER, 'scripts', 'sound_router.js')
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, 'w', encoding='utf-8') as fh:
        fh.write('\n'.join(lines))

JAVA_SLOT_TO_BEDROCK = {
    "HEAD": "slot.armor.head",
    "CHEST": "slot.armor.chest",
    "LEGS": "slot.armor.legs",
    "FEET": "slot.armor.feet",
    "MAINHAND": "slot.weapon.mainhand",
    "OFFHAND": "slot.weapon.offhand",
}
def extract_equipment_from_java(java_code: str, namespace: str) -> Optional[dict]:
    equipment = {}
    for m in re.finditer(
        r'(?:setItemSlot|setEquipment|set)\s*\(\s*EquipmentSlot\.([A-Z]+)\s*,\s*new\s+ItemStack\s*\(\s*(?:Items\.)?([A-Za-z_]+)',
        java_code):
        slot = JAVA_SLOT_TO_BEDROCK.get(m.group(1))
        item = sanitize_identifier(m.group(2).lower())
        if slot:
            if not item.startswith("minecraft:"):
                item = f"minecraft:{item}"
            equipment[slot] = {"item": item, "drop_chance": 0.085}
    if not equipment:
        return None
    return {"table": f"loot_tables/equipment/{namespace}_equipment.json", "slot_drop_chance": list(equipment.values())}
def generate_entity_events(bedrock_entity: dict, ai_goals: list, java_code: str,
                           namespace: str, entity_id: str, attributes: dict):
    components = bedrock_entity["minecraft:entity"]["components"]
    events = bedrock_entity["minecraft:entity"]["events"]
    component_groups = {}
    ns_prefix = entity_id.split(":")[0] if ":" in entity_id else namespace
    taming_goals = ("SitWhenOrderedToGoal", "FollowOwnerGoal", "OwnerHurtByTargetGoal", "OwnerHurtTargetGoal")
    if any(g in ai_goals for g in taming_goals):
        component_groups[f"{ns_prefix}:tamed"] = {
            "minecraft:is_tamed": {},
            "minecraft:behavior.follow_owner": {
                "priority": 7,
                "speed_multiplier": 1.2,
                "start_distance": 10.0,
                "stop_distance": 2.0
            }
        }
        component_groups[f"{ns_prefix}:wild"] = {
            "minecraft:behavior.nearest_attackable_target": components.get(
                "minecraft:behavior.nearest_attackable_target",
                {"priority": 1, "entity_types": [{"filters": {"test": "is_family", "subject": "other", "value": "player"}, "max_dist": 16}]}
            )
        }
        events["minecraft:on_tame"] = {
            "add": {"component_groups": [f"{ns_prefix}:tamed"]},
            "remove": {"component_groups": [f"{ns_prefix}:wild"]}
        }
        events["minecraft:on_untame"] = {
            "add": {"component_groups": [f"{ns_prefix}:wild"]},
            "remove": {"component_groups": [f"{ns_prefix}:tamed"]}
        }
    if "ResetAngerGoal" in ai_goals or "HurtByTargetGoal" in ai_goals:
        component_groups[f"{ns_prefix}:angry"] = {
            "minecraft:behavior.nearest_attackable_target": {
                "priority": 1,
                "entity_types": [{"filters": {"test": "is_family", "subject": "other", "value": "player"}, "max_dist": int(attributes.get("follow_range", 16))}],
                "must_see": False,
                "reselect_targets": True
            }
        }
        component_groups[f"{ns_prefix}:calm"] = {
            "minecraft:behavior.random_stroll": {"priority": 8, "speed_multiplier": attributes.get("movement_speed", 0.3)}
        }
        events["minecraft:on_anger"] = {
            "add": {"component_groups": [f"{ns_prefix}:angry"]},
            "remove": {"component_groups": [f"{ns_prefix}:calm"]}
        }
        events["minecraft:on_calm"] = {
            "add": {"component_groups": [f"{ns_prefix}:calm"]},
            "remove": {"component_groups": [f"{ns_prefix}:angry"]}
        }
    safe_name = sanitize_identifier(entity_id.split(":")[-1])
    loot_path = f"loot_tables/entities/{safe_name}.json"
    if os.path.exists(os.path.join(BP_FOLDER, loot_path)):
        components["minecraft:loot"] = {"table": loot_path}
    component_groups[f"{ns_prefix}:dead"] = {"minecraft:despawn": {}}
    events["minecraft:on_death"] = {
        "add": {"component_groups": [f"{ns_prefix}:dead"]}
    }
    mob_effects = extract_mob_effects_from_java(java_code)
    if mob_effects:
        component_groups[f"{ns_prefix}:hurt_effects"] = {
            "minecraft:mob_effect": {"effect": mob_effects[0]["effect"],
                                     "duration": mob_effects[0]["duration"],
                                     "amplifier": mob_effects[0]["amplifier"]}
        }
        events["minecraft:on_hurt"] = {
            "add": {"component_groups": [f"{ns_prefix}:hurt_effects"]}
        }
        if re.search(r'addEffect|hurt\(.+MobEffects', java_code, re.I):
            components["minecraft:attack_effect"] = {
                "effect": mob_effects[0]["effect"],
                "duration": mob_effects[0]["duration"],
                "amplifier": mob_effects[0]["amplifier"]
            }
    entity_name_short = entity_id.split(":")[-1] if ":" in entity_id else entity_id
    detected_sounds = extract_entity_sounds_from_java(java_code, entity_name_short, namespace)
    apply_entity_sounds(bedrock_entity, detected_sounds, namespace, entity_name_short)
    equip = extract_equipment_from_java(java_code, namespace)
    if equip:
        components["minecraft:equipment"] = equip
    kr = attributes.get("knockback_resistance", 0.0)
    if kr > 0:
        components["minecraft:knockback_resistance"] = {"value": min(1.0, kr)}
    if component_groups:
        bedrock_entity["minecraft:entity"]["component_groups"] = component_groups
JAVA_BIOME_TO_BEDROCK = {
    "plains": "plains", "desert": "desert", "forest": "forest",
    "taiga": "taiga", "swamp": "swamp", "jungle": "jungle",
    "savanna": "savanna", "badlands": "mesa", "snowy": "frozen",
    "mountains": "extreme_hills", "birch_forest": "birch",
    "dark_forest": "roofed_forest", "mushroom": "mushroom_island",
    "beach": "beach", "ocean": "ocean", "deep_ocean": "deep_ocean",
    "river": "river", "nether": "nether", "end": "the_end",
    "basalt_deltas": "basalt_deltas", "crimson_forest": "crimson_forest",
    "warped_forest": "warped_forest", "soul_sand_valley": "soulsand_valley",
    "meadow": "meadow", "grove": "grove", "snowy_slopes": "snowy_slopes",
    "jagged_peaks": "jagged_peaks", "frozen_peaks": "frozen_peaks",
    "stony_peaks": "stony_peaks", "lush_caves": "lush_caves",
    "dripstone_caves": "dripstone_caves", "deep_dark": "deep_dark",
    "mangrove_swamp": "mangrove_swamp", "cherry_grove": "cherry_grove",
    "overworld": "overworld", "underground": "underground",
}
def extract_spawn_data_from_java(java_code: str) -> dict:
    data = {
        "biomes": [],
        "min_light": 0,
        "max_light": 15,
        "min_count": 1,
        "max_count": 4,
        "weight": 10,
        "surface": True,
        "underground": False,
    }
    biome_matches = re.findall(
        r'(?:BiomeDictionary|BiomeCategory|Tags\.Biomes|BIOMES?)[\.\s]+([A-Z_a-z]+)',
        java_code)
    biome_matches += re.findall(r'BiomeTags\.(?:IS_)?([A-Z_]+)', java_code)
    biome_matches += re.findall(r'TagKey[^"]*["\']([a-z_:]+)["\']', java_code)
    for b in biome_matches:
        bl = b.lower()
        for k, v in JAVA_BIOME_TO_BEDROCK.items():
            if k in bl or bl in k:
                if v not in data["biomes"]:
                    data["biomes"].append(v)
    if not data["biomes"]:
        if re.search(r'NETHER|nether', java_code, re.I): data["biomes"] = ["nether"]
        elif re.search(r'THE_END|the_end', java_code, re.I): data["biomes"] = ["the_end"]
        else: data["biomes"] = ["overworld"]
    if re.search(r'MobCategory\.NETHER|DimensionType\.NETHER', java_code): data["biomes"] = ["nether"]
    if re.search(r'MobCategory\.END|DimensionType\.END', java_code): data["biomes"] = ["the_end"]
    for wpat in [r'SpawnEntry[^(]*\(\s*(\d+)', r'weight\s*[=:]\s*(\d+)', r'\.weight\s*\(\s*(\d+)\s*\)']:
        m = re.search(wpat, java_code, re.I)
        if m:
            data["weight"] = int(m.group(1)); break
    m = re.search(r'SpawnEntry[^(]*\([^,]+,\s*(\d+)\s*,\s*(\d+)', java_code)
    if m:
        data["min_count"] = int(m.group(1))
        data["max_count"] = int(m.group(2))
    m = re.search(r'(?:light|lightLevel|maxLight)\s*[=<>]+\s*(\d+)', java_code, re.I)
    if m:
        data["max_light"] = int(m.group(1))
    if re.search(r'IN_WATER|water', java_code, re.I):
        data["surface"] = False
    if re.search(r'UNDERGROUND|underground|cave|Cave', java_code):
        data["underground"] = True
        data["surface"] = False
    return data
def generate_spawn_rules(entity_id: str, java_code: str, namespace: str):
    spawn_data = extract_spawn_data_from_java(java_code)
    safe_name = sanitize_identifier(entity_id.split(":")[-1])
    out_path = os.path.join(BP_FOLDER, "spawn_rules", f"{safe_name}.json")
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    conditions = []
    for biome in spawn_data["biomes"]:
        condition = {
            "minecraft:spawns_on_surface": {} if spawn_data["surface"] else None,
            "minecraft:spawns_underground": {} if spawn_data["underground"] else None,
            "minecraft:brightness_filter": {
                "min": spawn_data["min_light"],
                "max": spawn_data["max_light"],
                "adjust_for_weather": False
            },
            "minecraft:biome_filter": {"test": "has_biome_tag", "operator": "==", "value": biome},
            "minecraft:herd": {
                "min_size": spawn_data["min_count"],
                "max_size": spawn_data["max_count"]
            },
            "minecraft:weight": {"default": spawn_data["weight"]}
        }
        condition = {k: v for k, v in condition.items() if v is not None}
        conditions.append(condition)
    doc = {
        "format_version": "1.12.0",
        "minecraft:spawn_rules": {
            "description": {"identifier": entity_id, "population_control": "monster"},
            "conditions": conditions
        }
    }
    safe_write_json(out_path, doc)

JAVA_LOOT_ITEM_MAP = {
    "minecraft:bone": "minecraft:bone",
    "minecraft:rotten_flesh": "minecraft:rotten_flesh",
    "minecraft:string": "minecraft:string",
    "minecraft:arrow": "minecraft:arrow",
    "minecraft:blaze_rod": "minecraft:blaze_rod",
    "minecraft:gunpowder": "minecraft:gunpowder",
    "minecraft:ender_pearl": "minecraft:ender_pearl",
    "minecraft:leather": "minecraft:leather",
    "minecraft:feather": "minecraft:feather",
    "minecraft:experience_bottle": "minecraft:experience_bottle",
    "minecraft:coal": "minecraft:coal",
    "minecraft:iron_ingot": "minecraft:iron_ingot",
    "minecraft:gold_ingot": "minecraft:gold_ingot",
    "minecraft:diamond": "minecraft:diamond",
    "minecraft:emerald": "minecraft:emerald",
    "minecraft:beef": "minecraft:beef",
    "minecraft:cooked_beef": "minecraft:cooked_beef",
    "minecraft:porkchop": "minecraft:porkchop",
    "minecraft:cooked_porkchop": "minecraft:cooked_porkchop",
    "minecraft:chicken": "minecraft:chicken",
    "minecraft:cooked_chicken": "minecraft:cooked_chicken",
    "minecraft:mutton": "minecraft:mutton",
    "minecraft:cooked_mutton": "minecraft:cooked_mutton",
}
def convert_java_loot_table(java_loot: dict, namespace: str) -> dict:
    pools = []
    for pool in java_loot.get("pools", []):
        rolls = pool.get("rolls", 1)
        if isinstance(rolls, dict):
            roll_val = {"min": rolls.get("min", 1), "max": rolls.get("max", 1)}
        else:
            roll_val = int(rolls)
        entries = []
        for entry in pool.get("entries", []):
            etype = entry.get("type", "")
            if "empty" in etype:
                continue
            if "item" in etype:
                item_name = entry.get("name", "")
                if ":" in item_name:
                    ns, item = item_name.split(":", 1)
                    if ns != "minecraft":
                        item_name = f"{namespace}:{sanitize_identifier(item)}"
                    else:
                        item_name = JAVA_LOOT_ITEM_MAP.get(item_name, item_name)
                bedrock_entry = {
                    "type": "item",
                    "name": item_name,
                    "weight": entry.get("weight", 1)
                }
                functions = []
                for func in entry.get("functions", []):
                    fname = func.get("function", "")
                    if "count" in fname or "set_count" in fname:
                        count = func.get("count", 1)
                        if isinstance(count, dict):
                            functions.append({
                                "function": "set_count",
                                "count": {"min": count.get("min", 1), "max": count.get("max", 1)}
                            })
                        else:
                            functions.append({"function": "set_count", "count": int(count)})
                    elif "looting" in fname or "enchant_with_levels" in fname:
                        functions.append({
                            "function": "looting_enchant",
                            "count": {"min": 0, "max": 1}
                        })
                if functions:
                    bedrock_entry["functions"] = functions
                entries.append(bedrock_entry)
            elif "loot_table" in etype or "alternatives" in etype:
                entries.append({"type": "item", "name": "minecraft:air", "weight": 1})
        if entries:
            pools.append({"rolls": roll_val, "entries": entries})
    return {"pools": pools}
def process_loot_tables_from_jar(jar_path: str, namespace: str):
    out_base = os.path.join(BP_FOLDER, "loot_tables", "entities")
    os.makedirs(out_base, exist_ok=True)
    count = 0
    with zipfile.ZipFile(jar_path, "r") as jar:
        for name in jar.namelist():
            lower = name.lower()
            if "loot_table" not in lower and "loot_tables" not in lower:
                continue
            if not lower.endswith(".json"):
                continue
            try:
                with jar.open(name) as f:
                    data = json.loads(f.read().decode("utf-8"))
                bedrock = convert_java_loot_table(data, namespace)
                if not bedrock.get("pools"):
                    continue
                fname = sanitize_filename_keep_ext(os.path.basename(name))
                out_path = os.path.join(out_base, fname)
                safe_write_json(out_path, bedrock)
                count += 1
            except Exception as e:
                _warn(f"[loot] Failed to convert {name}: {e}")

def generate_trading_table(entity_id: str, java_code: str, namespace: str):
    safe_name = sanitize_identifier(entity_id.split(":")[-1])
    out_path = os.path.join(BP_FOLDER, "trading", f"{safe_name}.json")
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    trade_items = re.findall(
        r'new\s+MerchantOffer[^;]+new\s+ItemStack\(([^)]+)\)', java_code)
    tiers = []
    if trade_items:
        trades = []
        for item_ref in trade_items[:6]:
            item_name = sanitize_identifier(item_ref.split(".")[-1].split(",")[0].lower())
            trades.append({
                "wants": [{"item": f"minecraft:emerald", "quantity": 1}],
                "gives": [{"item": f"{namespace}:{item_name}", "quantity": 1}],
                "trader_exp": 1, "max_uses": 12, "reward_exp": True
            })
        tiers.append({"total_exp_required": 0, "groups": [{"num_to_select": len(trades), "trades": trades}]})
    else:
        tiers.append({
            "total_exp_required": 0,
            "groups": [{"num_to_select": 1, "trades": [
                {"wants": [{"item": "minecraft:emerald", "quantity": 1}],
                 "gives": [{"item": f"{namespace}:item", "quantity": 1}],
                 "trader_exp": 1, "max_uses": 12, "reward_exp": True}
            ]}]
        })
    doc = {"tiers": tiers}
    safe_write_json(out_path, doc)

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

def _find_remapper_candidate(script_dir: str) -> Optional[str]:
    candidates = []
    search_roots = [
        script_dir,
        os.path.join(script_dir, "tools"),
    ]
    for root in search_roots:
        if not os.path.isdir(root):
            continue
        for name in os.listdir(root):
            low = name.lower()
            if not low.endswith(".jar"):
                continue
            if any(k in low for k in ("remapper", "tinyremapper", "specialsource", "mappings")):
                candidates.append(os.path.join(root, name))
    return candidates[0] if candidates else None

def _find_mapping_file(script_dir: str) -> Optional[str]:
    search_roots = [
        script_dir,
        os.path.join(script_dir, "tools"),
    ]
    for root in search_roots:
        if not os.path.isdir(root):
            continue
        for name in os.listdir(root):
            low = name.lower()
            if low.endswith((".tiny", ".tsrg", ".srg", ".txt", ".map")) and any(k in low for k in ("mcp", "mojang", "yarn", "parchment", "mappings")):
                return os.path.join(root, name)
    return None

def _try_remap_jar(input_jar: str, remapped_jar: str) -> Optional[str]:
    script_dir = os.path.dirname(os.path.abspath(__file__))
    remapper = _find_remapper_candidate(script_dir)
    mappings = _find_mapping_file(script_dir)
    if not remapper or not mappings:
        return None

    os.makedirs(os.path.dirname(remapped_jar), exist_ok=True)

    cmd_variants = [
        ["java", "-jar", remapper, input_jar, remapped_jar, mappings],
        ["java", "-jar", remapper, input_jar, mappings, remapped_jar],
    ]

    for cmd in cmd_variants:
        try:
            subprocess.run(
                cmd,
                cwd=script_dir,
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            if os.path.exists(remapped_jar):
                return remapped_jar
        except Exception:
            continue
    return None

def run_class_decompiler(jar_file, output_dir):
    script_dir = os.path.dirname(os.path.abspath(__file__))
    lib_jar = os.path.join(script_dir, "tools", "ClassDecompiler.jar")

    if not os.path.exists(lib_jar):
        _warn(f"Error: ClassDecompiler.jar not found at {lib_jar}")
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
        remapped_jar = os.path.join(script_dir, ".remap_cache", os.path.basename(jar_file))
        remapped = _try_remap_jar(working_jar, remapped_jar)
        if remapped:
            working_jar = remapped
        else:
            _warn("No remapper/mappings found; decompiling original jar and relying on source-level heuristics.")

        subprocess.run(
            ["java", "-jar", os.path.abspath(lib_jar),
             os.path.abspath(working_jar), os.path.abspath(output_dir)],
            cwd=script_dir,
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return extracted_engine

    except Exception as e:
        _warn(f"Decompilation failure: {e}")
        return None

def _is_java_texture_pack(zip_path: str) -> bool:
    try:
        with zipfile.ZipFile(zip_path, "r") as zf:
            names = zf.namelist()
            has_mcmeta = "pack.mcmeta" in names
            has_assets_textures = any(
                n.startswith("assets/") and n.endswith(".png")
                for n in names
            )
            has_direct_textures = any(
                n.startswith("textures/") and n.endswith(".png")
                for n in names
            )
            return has_mcmeta or has_assets_textures or has_direct_textures
    except Exception as e:
        _REAL_PRINT(f"  [TexturePack] Could not open {zip_path}: {e}")
        return False
_JAVA_TO_BEDROCK_TEXTURE_PATHS: List[Tuple[str, str]] = [
    ("assets/minecraft/textures/block/",  "textures/blocks/"),
    ("assets/minecraft/textures/blocks/", "textures/blocks/"),
    ("assets/minecraft/textures/item/",   "textures/items/"),
    ("assets/minecraft/textures/items/",  "textures/items/"),
    ("assets/minecraft/textures/entity/", "textures/entity/"),
    ("assets/minecraft/textures/gui/",    "textures/ui/"),
    ("assets/minecraft/textures/misc/",   "textures/misc/"),
    ("assets/minecraft/textures/environment/", "textures/environment/"),
    ("assets/minecraft/textures/particle/",    "textures/particle/"),
    ("assets/minecraft/textures/colormap/",    "textures/colormap/"),
    ("assets/minecraft/textures/effect/",      "textures/misc/"),
    ("assets/minecraft/textures/models/",      "textures/models/"),
    ("assets/minecraft/textures/painting/",    "textures/painting/"),
    ("assets/minecraft/textures/map/",         "textures/map/"),
    ("assets/minecraft/textures/mob_effect/",  "textures/mob_effect/"),
]

_JAVA_BLOCK_RENAME_MAP: Dict[str, str] = {
    "grass_block_top":    "grass_top",
    "grass_block_side":   "grass_side",
    "grass_block_side_overlay": "grass_side_snowed",
    "dirt_path_top":      "grass_path_top",
    "dirt_path_side":     "grass_path_side",
    "water_still":        "water_still",
    "water_flow":         "water_flow",
    "lava_still":         "lava_still",
    "lava_flow":          "lava_flow",
    "oak_log":            "log_oak",
    "birch_log":          "log_birch",
    "spruce_log":         "log_spruce",
    "jungle_log":         "log_jungle",
    "acacia_log":         "log_acacia",
    "dark_oak_log":       "log_big_oak",
    "oak_planks":         "planks_oak",
    "birch_planks":       "planks_birch",
    "spruce_planks":      "planks_spruce",
    "jungle_planks":      "planks_jungle",
    "acacia_planks":      "planks_acacia",
    "dark_oak_planks":    "planks_big_oak",
    "stone_bricks":       "stonebrick",
    "mossy_stone_bricks": "stonebrick_mossy",
    "cracked_stone_bricks": "stonebrick_cracked",
    "chiseled_stone_bricks": "stonebrick_carved",
    "smooth_stone":       "stone_slab_top",
    "cobblestone":        "cobblestone",
    "mossy_cobblestone":  "cobblestone_mossy",
    "sand":               "sand",
    "red_sand":           "red_sand",
    "gravel":             "gravel",
    "oak_leaves":         "leaves_oak",
    "birch_leaves":       "leaves_birch",
    "spruce_leaves":      "leaves_spruce",
    "jungle_leaves":      "leaves_jungle",
    "acacia_leaves":      "leaves_acacia",
    "dark_oak_leaves":    "leaves_big_oak",
    "glass":              "glass",
    "tnt_side":           "tnt_side",
    "tnt_top":            "tnt_top",
    "tnt_bottom":         "tnt_bottom",
    "crafting_table_top": "crafting_table_top",
    "crafting_table_front": "crafting_table_front",
    "crafting_table_side": "crafting_table_side",
    "furnace_front":      "furnace_front_off",
    "furnace_front_on":   "furnace_front_on",
    "furnace_side":       "furnace_side",
    "furnace_top":        "furnace_top",
    "bookshelf":          "bookshelf",
    "pumpkin_top":        "pumpkin_top",
    "pumpkin_side":       "pumpkin_face_off",
    "jack_o_lantern":     "pumpkin_face_on",
    "melon_side":         "melon_side",
    "melon_top":          "melon_top",
    "nether_bricks":      "nether_brick",
    "netherrack":         "netherrack",
    "soul_sand":          "soul_sand",
    "glowstone":          "glowstone",
    "end_stone":          "end_stone",
    "obsidian":           "obsidian",
    "bedrock":            "bedrock",
    "coal_ore":           "coal_ore",
    "iron_ore":           "iron_ore",
    "gold_ore":           "gold_ore",
    "diamond_ore":        "diamond_ore",
    "emerald_ore":        "emerald_ore",
    "lapis_ore":          "lapis_ore",
    "redstone_ore":       "redstone_ore",
    "coal_block":         "coal_block",
    "iron_block":         "iron_block",
    "gold_block":         "gold_block",
    "diamond_block":      "diamond_block",
    "emerald_block":      "emerald_block",
    "lapis_block":        "lapis_block",
    "redstone_block":     "redstone_block",
    "quartz_block_side":  "quartz_side",
    "quartz_block_top":   "quartz_top",
    "quartz_block_bottom": "quartz_bottom",
    "chiseled_quartz_block": "chiseled_quartz_block",
    "sandstone":          "sandstone_normal",
    "sandstone_top":      "sandstone_top",
    "sandstone_bottom":   "sandstone_bottom",
    "chiseled_sandstone": "sandstone_carved",
    "smooth_sandstone":   "sandstone_smooth",
    "red_sandstone":      "red_sandstone_normal",
    "red_sandstone_top":  "red_sandstone_top",
    "red_sandstone_bottom": "red_sandstone_bottom",
    "snow":               "snow",
    "ice":                "ice",
    "packed_ice":         "ice_packed",
    "blue_ice":           "blue_ice",
    "clay":               "clay",
    "mycelium_top":       "mycelium_top",
    "mycelium_side":      "mycelium_side",
    "mushroom_stem":      "mushroom_skin_stem",
    "brown_mushroom_block": "mushroom_skin_brown",
    "red_mushroom_block": "mushroom_skin_red",
    "note_block":         "noteblock",
    "jukebox_side":       "jukebox_side",
    "jukebox_top":        "jukebox_top",
    "sponge":             "sponge",
    "wet_sponge":         "sponge_wet",
    "hay_block_side":     "hay_block_side",
    "hay_block_top":      "hay_block_top",
    "terracotta":         "hardened_clay",
    "white_terracotta":   "hardened_clay_stained_white",
    "orange_terracotta":  "hardened_clay_stained_orange",
    "magenta_terracotta": "hardened_clay_stained_magenta",
    "light_blue_terracotta": "hardened_clay_stained_light_blue",
    "yellow_terracotta":  "hardened_clay_stained_yellow",
    "lime_terracotta":    "hardened_clay_stained_lime",
    "pink_terracotta":    "hardened_clay_stained_pink",
    "gray_terracotta":    "hardened_clay_stained_gray",
    "light_gray_terracotta": "hardened_clay_stained_silver",
    "cyan_terracotta":    "hardened_clay_stained_cyan",
    "purple_terracotta":  "hardened_clay_stained_purple",
    "blue_terracotta":    "hardened_clay_stained_blue",
    "brown_terracotta":   "hardened_clay_stained_brown",
    "green_terracotta":   "hardened_clay_stained_green",
    "red_terracotta":     "hardened_clay_stained_red",
    "black_terracotta":   "hardened_clay_stained_black",
    "white_wool":         "wool_colored_white",
    "orange_wool":        "wool_colored_orange",
    "magenta_wool":       "wool_colored_magenta",
    "light_blue_wool":    "wool_colored_light_blue",
    "yellow_wool":        "wool_colored_yellow",
    "lime_wool":          "wool_colored_lime",
    "pink_wool":          "wool_colored_pink",
    "gray_wool":          "wool_colored_gray",
    "light_gray_wool":    "wool_colored_silver",
    "cyan_wool":          "wool_colored_cyan",
    "purple_wool":        "wool_colored_purple",
    "blue_wool":          "wool_colored_blue",
    "brown_wool":         "wool_colored_brown",
    "green_wool":         "wool_colored_green",
    "red_wool":           "wool_colored_red",
    "black_wool":         "wool_colored_black",
    "white_concrete":     "concrete_white",
    "orange_concrete":    "concrete_orange",
    "magenta_concrete":   "concrete_magenta",
    "light_blue_concrete": "concrete_light_blue",
    "yellow_concrete":    "concrete_yellow",
    "lime_concrete":      "concrete_lime",
    "pink_concrete":      "concrete_pink",
    "gray_concrete":      "concrete_gray",
    "light_gray_concrete": "concrete_silver",
    "cyan_concrete":      "concrete_cyan",
    "purple_concrete":    "concrete_purple",
    "blue_concrete":      "concrete_blue",
    "brown_concrete":     "concrete_brown",
    "green_concrete":     "concrete_green",
    "red_concrete":       "concrete_red",
    "black_concrete":     "concrete_black",
}

_JAVA_ITEM_RENAME_MAP: Dict[str, str] = {
    "wooden_sword":   "wood_sword",
    "wooden_pickaxe": "wood_pickaxe",
    "wooden_axe":     "wood_axe",
    "wooden_shovel":  "wood_shovel",
    "wooden_hoe":     "wood_hoe",
    "stone_sword":    "stone_sword",
    "stone_pickaxe":  "stone_pickaxe",
    "stone_axe":      "stone_axe",
    "stone_shovel":   "stone_shovel",
    "stone_hoe":      "stone_hoe",
    "golden_sword":   "gold_sword",
    "golden_pickaxe": "gold_pickaxe",
    "golden_axe":     "gold_axe",
    "golden_shovel":  "gold_shovel",
    "golden_hoe":     "gold_hoe",
    "golden_helmet":  "gold_helmet",
    "golden_chestplate": "gold_chestplate",
    "golden_leggings": "gold_leggings",
    "golden_boots":   "gold_boots",
    "golden_apple":   "apple_golden",
    "enchanted_golden_apple": "apple_enchanted",
    "carrot_on_a_stick": "carrotonastick",
    "warped_fungus_on_a_stick": "warped_fungus_on_a_stick",
    "bow_pulling_0":  "bow_pulling_0",
    "bow_pulling_1":  "bow_pulling_1",
    "bow_pulling_2":  "bow_pulling_2",
    "ender_pearl":    "ender_pearl",
    "ender_eye":      "ender_eye",
    "ghast_tear":     "ghast_tear",
    "nether_star":    "nether_star",
    "totem_of_undying": "totem",
    "knowledge_book": "book_knowledge",
    "writable_book":  "book_writable",
    "written_book":   "book_written",
    "enchanted_book": "book_enchanted",
    "potion":         "potion_bottle_drinkable",
    "splash_potion":  "potion_bottle_splash",
    "lingering_potion": "potion_bottle_lingering",
    "experience_bottle": "potion_bottle_splash_empty",
    "flower_pot":     "flower_pot",
    "flower_banner_pattern": "flower_banner_pattern",
    "leather_helmet": "leather_helmet",
    "leather_chestplate": "leather_chestplate",
    "leather_leggings": "leather_leggings",
    "leather_boots":  "leather_boots",
    "flint_and_steel": "flint_and_steel",
    "name_tag":       "name_tag",
    "lead":           "leash",
    "compass":        "compass_item",
    "clock":          "watch_item",
    "map":            "map_empty",
    "filled_map":     "map_filled",
    "shears":         "shears",
    "fishing_rod":    "fishing_rod_uncast",
    "fishing_rod_cast": "fishing_rod_cast",
    "bucket":         "bucket_empty",
    "water_bucket":   "bucket_water",
    "lava_bucket":    "bucket_lava",
    "milk_bucket":    "bucket_milk",
    "powder_snow_bucket": "bucket_powder_snow",
    "heart_of_the_sea": "heart_of_the_sea",
    "nautilus_shell": "nautilus_shell",
    "turtle_helmet":  "turtle_helmet",
    "phantom_membrane": "phantom_membrane",
    "sweet_berries":  "sweet_berries",
    "glow_berries":   "glow_berries",
    "honey_bottle":   "honey_bottle",
    "honeycomb":      "honeycomb",
    "crossbow":       "crossbow",
    "crossbow_pulling_0": "crossbow_pulling_0",
    "crossbow_pulling_1": "crossbow_pulling_1",
    "crossbow_pulling_2": "crossbow_pulling_2",
    "crossbow_arrow": "crossbow_arrow",
    "crossbow_firework": "crossbow_firework",
    "trident":        "trident",
    "shield":         "shield",
    "elytra":         "elytra",
    "spyglass":       "spyglass",
    "goat_horn":      "goat_horn",
    "disc_fragment_5": "disc_fragment_5",
    "echo_shard":     "echo_shard",
    "recovery_compass": "recovery_compass",
    "music_disc_5":   "record_5",
    "music_disc_11":  "record_11",
    "music_disc_13":  "record_13",
    "music_disc_blocks": "record_blocks",
    "music_disc_cat": "record_cat",
    "music_disc_chirp": "record_chirp",
    "music_disc_far": "record_far",
    "music_disc_mall": "record_mall",
    "music_disc_mellohi": "record_mellohi",
    "music_disc_otherside": "record_otherside",
    "music_disc_pigstep": "record_pigstep",
    "music_disc_stal": "record_stal",
    "music_disc_strad": "record_strad",
    "music_disc_wait": "record_wait",
    "music_disc_ward": "record_ward",
}


def _resize_texture_for_bedrock(src_path: str, dst_path: str) -> bool:
    os.makedirs(os.path.dirname(dst_path), exist_ok=True)
    if not PIL_AVAILABLE:
        shutil.copy2(src_path, dst_path)
        return True
    try:
        with Image.open(src_path) as img:
            w, h = img.size
            if h > w and h % w == 0:
                frame = img.crop((0, 0, w, w))
                frame.save(dst_path)
            else:
                img.save(dst_path)
        return True
    except Exception:
        try:
            shutil.copy2(src_path, dst_path)
            return True
        except Exception:
            return False


def convert_java_texture_pack(zip_path: str) -> str:
    _orig = _logger._original_print
    _orig(f"\n  [TexturePack] Converting Java texture pack: {zip_path}")

    pack_stem = os.path.splitext(os.path.basename(zip_path))[0]
    out_dir   = f"{pack_stem}_Bedrock_RP"
    pack_name = pack_stem
    pack_desc = ""
    try:
        with zipfile.ZipFile(zip_path, "r") as zf:
            if "pack.mcmeta" in zf.namelist():
                meta = json.loads(zf.read("pack.mcmeta").decode("utf-8", errors="replace"))
                raw_desc = meta.get("pack", {}).get("description", "")
                if isinstance(raw_desc, list):
                    raw_desc = " ".join(
                        seg.get("text", "") if isinstance(seg, dict) else str(seg)
                        for seg in raw_desc
                    )
                pack_desc = str(raw_desc).strip()
    except Exception:
        pass

    if pack_desc:
        pack_name = pack_desc[:64]
    for sub in [
        "textures/blocks",
        "textures/items",
        "textures/entity",
        "textures/ui",
        "textures/misc",
        "textures/environment",
        "textures/particle",
        "textures/colormap",
        "textures/models",
        "textures/painting",
        "textures/map",
        "textures/mob_effect",
        "font",
        "sounds",
        "texts",
    ]:
        os.makedirs(os.path.join(out_dir, sub), exist_ok=True)

    stats = {
        "copied":  0,
        "renamed": 0,
        "skipped": 0,
        "errors":  0,
    }
    item_texture_map:  Dict[str, str] = {}
    block_texture_map: Dict[str, str] = {}

    _orig("  [TexturePack] Extracting & remapping textures …")
    _REAL_PRINT(f"  [TexturePack] ZIP contents (first 30 entries):")
    try:
        with zipfile.ZipFile(zip_path, "r") as _diag_zf:
            for _n in _diag_zf.namelist()[:30]:
                _REAL_PRINT(f"    {_n!r}")
    except Exception as _e:
        _REAL_PRINT(f"    (could not list: {_e})")

    with zipfile.ZipFile(zip_path, "r") as zf:
        raw_names = zf.namelist()
        zip_root_prefix = ""
        for candidate in raw_names:
            candidate_norm = candidate.replace("\\", "/")
            if "/assets/" in candidate_norm and not candidate_norm.startswith("assets/"):
                idx = candidate_norm.index("/assets/")
                zip_root_prefix = candidate_norm[: idx + 1]
                break
            if candidate_norm == "pack.mcmeta":
                break
        if zip_root_prefix:
            _REAL_PRINT(f"  [TexturePack] Detected zip subfolder prefix: {zip_root_prefix!r}")
            names = [n.replace("\\", "/")[len(zip_root_prefix):] if n.replace("\\", "/").startswith(zip_root_prefix) else n.replace("\\", "/") for n in raw_names]
            _name_map = {}
            for orig in raw_names:
                stripped = orig.replace("\\", "/")
                if stripped.startswith(zip_root_prefix):
                    stripped = stripped[len(zip_root_prefix):]
                _name_map[stripped] = orig
        else:
            names = [n.replace("\\", "/") for n in raw_names]
            _name_map = {n.replace("\\", "/"): n for n in raw_names}

        def _zf_read(logical_name: str) -> bytes:
            return zf.read(_name_map.get(logical_name, logical_name))

        def _zf_open(logical_name):
            return zf.open(_name_map.get(logical_name, logical_name))
        for icon_path in ("pack.png", "assets/pack.png"):
            if icon_path in names:
                try:
                    icon_data = _zf_read(icon_path)
                    icon_out  = os.path.join(out_dir, "pack_icon.png")
                    with open(icon_out, "wb") as fh:
                        fh.write(icon_data)
                    if PIL_AVAILABLE:
                        with Image.open(icon_out) as img:
                            if img.size != (64, 64):
                                img.resize((64, 64), Image.LANCZOS).save(icon_out)
                except Exception:
                    pass
                break
        for entry in names:
            if entry.startswith("assets/minecraft/font/") and entry.endswith(".png"):
                fname = os.path.basename(entry)
                dst   = os.path.join(out_dir, "font", fname)
                try:
                    with _zf_open(entry) as src_fh:
                        data = src_fh.read()
                    with open(dst, "wb") as dst_fh:
                        dst_fh.write(data)
                    stats["copied"] += 1
                except Exception:
                    stats["errors"] += 1
        for entry in names:
            if entry.startswith("assets/minecraft/sounds/") and (
                entry.endswith(".ogg") or entry.endswith(".wav")
            ):
                rel   = entry[len("assets/minecraft/sounds/"):]
                dst   = os.path.join(out_dir, "sounds", rel)
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                try:
                    with _zf_open(entry) as src_fh:
                        data = src_fh.read()
                    with open(dst, "wb") as dst_fh:
                        dst_fh.write(data)
                    stats["copied"] += 1
                except Exception:
                    stats["errors"] += 1
        for entry in names:
            if entry.startswith("assets/minecraft/lang/") and entry.endswith(".json"):
                lang_code = os.path.splitext(os.path.basename(entry))[0]
                dst = os.path.join(out_dir, "texts", f"{lang_code}.lang")
                try:
                    raw = _zf_read(entry).decode("utf-8", errors="replace")
                    java_lang = json.loads(raw)
                    lines = [f"{k}={v}" for k, v in sorted(java_lang.items())]
                    with open(dst, "w", encoding="utf-8") as fh:
                        fh.write("\n".join(lines))
                    stats["copied"] += 1
                except Exception:
                    stats["errors"] += 1
        import tempfile
        tmp_dir = tempfile.mkdtemp(prefix="modmorpher_tp_")
        skipped_samples: List[str] = []
        try:
            for entry in names:
                entry_norm = entry.replace("\\", "/")
                if not entry_norm.lower().endswith(".png") and not entry_norm.lower().endswith(".tga"):
                    continue
                lower = entry_norm.lower()

                bedrock_subdir: Optional[str] = None
                matched_prefix = ""
                matched_prefix_len = 0
                for java_prefix, br_sub in _JAVA_TO_BEDROCK_TEXTURE_PATHS:
                    if lower.startswith(java_prefix):
                        bedrock_subdir = br_sub
                        matched_prefix = java_prefix
                        matched_prefix_len = len(java_prefix)
                        break
                if bedrock_subdir is None:
                    m = re.match(
                        r"assets/[^/]+/textures/(?:(block|blocks)/|(item|items)/|(entity)/|(gui)/|(misc)/|(particle)/|(colormap)/|(environment)/|(painting)/)?",
                        lower,
                    )
                    if m:
                        grp = next((g for g in m.groups() if g), None)
                        sub_map = {
                            "block": "textures/blocks/",   "blocks": "textures/blocks/",
                            "item":  "textures/items/",    "items":  "textures/items/",
                            "entity": "textures/entity/",
                            "gui":   "textures/ui/",
                            "misc":  "textures/misc/",
                            "particle": "textures/particle/",
                            "colormap": "textures/colormap/",
                            "environment": "textures/environment/",
                            "painting": "textures/painting/",
                        }
                        bedrock_subdir = sub_map.get(grp, "textures/misc/")
                        matched_prefix_len = len(m.group(0))
                    else:
                        stats["skipped"] += 1
                        if len(skipped_samples) < 10:
                            skipped_samples.append(entry_norm)
                        continue
                rel_after = entry_norm[matched_prefix_len:]
                base_noext = os.path.splitext(os.path.basename(rel_after))[0]
                sub_path   = os.path.dirname(rel_after).strip("/")
                renamed_base = base_noext
                is_block = "blocks" in bedrock_subdir
                is_item  = "items"  in bedrock_subdir
                if is_block and base_noext in _JAVA_BLOCK_RENAME_MAP:
                    renamed_base = _JAVA_BLOCK_RENAME_MAP[base_noext]
                    stats["renamed"] += 1
                elif is_item and base_noext in _JAVA_ITEM_RENAME_MAP:
                    renamed_base = _JAVA_ITEM_RENAME_MAP[base_noext]
                    stats["renamed"] += 1
                if sub_path:
                    dst_rel = os.path.join(bedrock_subdir, sub_path, renamed_base + ".png")
                else:
                    dst_rel = os.path.join(bedrock_subdir, renamed_base + ".png")
                dst = os.path.join(out_dir, dst_rel.replace("/", os.sep))
                tmp_file = os.path.join(tmp_dir, renamed_base + ".png")
                try:
                    with _zf_open(entry) as src_fh:
                        data = src_fh.read()
                    with open(tmp_file, "wb") as fh:
                        fh.write(data)
                    ok = _resize_texture_for_bedrock(tmp_file, dst)
                    if ok:
                        stats["copied"] += 1
                        tex_key = (
                            dst_rel.replace("\\", "/")
                            .replace("textures/blocks/", "")
                            .replace("textures/items/", "")
                            .replace(".png", "")
                        )
                        if is_block:
                            block_texture_map[renamed_base] = (
                                "textures/blocks/" + (
                                    (sub_path + "/" if sub_path else "") + renamed_base
                                )
                            )
                        elif is_item:
                            item_texture_map[renamed_base] = (
                                "textures/items/" + (
                                    (sub_path + "/" if sub_path else "") + renamed_base
                                )
                            )
                    else:
                        stats["errors"] += 1
                except Exception:
                    stats["errors"] += 1
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)

        if skipped_samples:
            _REAL_PRINT(
                f"  [TexturePack] {stats['skipped']} texture(s) skipped (unrecognised path). "
                f"First {len(skipped_samples)} examples:"
            )
            for s in skipped_samples:
                _REAL_PRINT(f"    {s}")
    terrain_texture = {
        "resource_pack_name": pack_name,
        "texture_name": "atlas.terrain",
        "padding": 8,
        "num_mip_levels": 4,
        "texture_data": {
            name: {"textures": [path]}
            for name, path in sorted(block_texture_map.items())
        },
    }
    with open(os.path.join(out_dir, "textures", "terrain_texture.json"), "w", encoding="utf-8") as fh:
        json.dump(terrain_texture, fh, indent=2)
    item_texture = {
        "resource_pack_name": pack_name,
        "texture_name": "atlas.items",
        "texture_data": {
            name: {"textures": [path]}
            for name, path in sorted(item_texture_map.items())
        },
    }
    with open(os.path.join(out_dir, "textures", "item_texture.json"), "w", encoding="utf-8") as fh:
        json.dump(item_texture, fh, indent=2)
    manifest = {
        "format_version": 2,
        "header": {
            "name": pack_name,
            "description": f"Converted from Java texture pack '{pack_stem}' by ModMorpher",
            "uuid": str(uuid.uuid4()),
            "version": [1, 0, 0],
            "min_engine_version": [1, 21, 50],
        },
        "modules": [
            {
                "type": "resources",
                "uuid": str(uuid.uuid4()),
                "version": [1, 0, 0],
            }
        ],
    }
    with open(os.path.join(out_dir, "manifest.json"), "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=2)
    mcpack_path = f"{pack_stem}_Bedrock.mcpack"
    shutil.make_archive(f"{pack_stem}_Bedrock", "zip", out_dir)
    os.rename(f"{pack_stem}_Bedrock.zip", mcpack_path)

    _orig(
        f"\n  [TexturePack] Done!\n"
        f"    Textures copied : {stats['copied']}\n"
        f"    Auto-renamed    : {stats['renamed']}\n"
        f"    Skipped         : {stats['skipped']}\n"
        f"    Errors          : {stats['errors']}\n"
        f"    Output          : {mcpack_path}\n"
    )
    notes_path = f"{pack_stem}_conversion_notes.txt"
    with open(notes_path, "w", encoding="utf-8") as fh:
        fh.write(f"ModMorpher – Java Texture Pack Conversion Notes\n")
        fh.write(f"Pack: {zip_path}\n")
        fh.write(f"Output: {mcpack_path}\n\n")
        fh.write(f"Textures copied : {stats['copied']}\n")
        fh.write(f"Auto-renamed    : {stats['renamed']}  (Java → Bedrock filename mapping)\n")
        fh.write(f"Skipped         : {stats['skipped']}  (paths outside known asset trees)\n")
        fh.write(f"Errors          : {stats['errors']}\n\n")
        fh.write(
            "Notes:\n"
            "- Animated textures (strips) have been cropped to their first frame.\n"
            "  Bedrock uses flipbook_textures.json for animation; manual setup needed.\n"
            "- GUI textures (assets/minecraft/textures/gui) are mapped to textures/ui/\n"
            "  but most vanilla GUI elements are not replaceable on Bedrock.\n"
            "- Custom shader / CIT / Optifine features are not supported on Bedrock.\n"
            "- Drop the .mcpack into your Bedrock world's resource packs to activate it.\n"
        )

    return mcpack_path


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
                _REAL_PRINT(f"  [TexturePack] ERROR converting {tp_zip}: {e}")
                _REAL_PRINT(traceback.format_exc())
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


    global _DETECTED_MC_VERSION
    _DETECTED_MC_VERSION = detect_minecraft_version(target_jar)

    modmorpher_input_folder = f"src_{os.path.splitext(target_jar)[0]}"

    extracted_engine = run_class_decompiler(target_jar, modmorpher_input_folder)

    if extracted_engine:
        if os.path.exists(extracted_engine):
            os.remove(extracted_engine)
    else:
        _warn("Decompiler did not complete cleanly; attempting pipeline on any available sources.")

    source_root = modmorpher_input_folder if os.path.isdir(modmorpher_input_folder) else "."
    run_pipeline(source_root)
def find_best_texture_match(safe_name: str, subfolder: str) -> str:
    tex_dir = os.path.join(RP_FOLDER, "textures", subfolder)
    if not os.path.isdir(tex_dir):
        return safe_name
    candidates = []
    for fname in os.listdir(tex_dir):
        if fname.lower().endswith(".png"):
            candidates.append(os.path.splitext(fname)[0])
    if not candidates:
        return safe_name
    if safe_name in candidates:
        return safe_name
    base = safe_name
    for suffix in ("block", "item", "entity", "mob", "_block", "_item", "_entity", "_mob"):
        if base.endswith(suffix):
            base = base[:-len(suffix)].strip("_")
            break
    if base in candidates:
        return base
    name_tokens = set(safe_name.split("_"))
    base_tokens = set(base.split("_"))
    best = safe_name
    best_score = 0
    for c in candidates:
        c_tokens = set(c.split("_"))
        score = len(c_tokens & name_tokens) + len(c_tokens & base_tokens)
        if score > best_score:
            best_score = score
            best = c
    if best_score > 0:
        return best
    return safe_name
_ITEM_BASES = r'Item|SwordItem|PickaxeItem|ShovelItem|AxeItem|HoeItem|ArmorItem|BowItem|ShieldItem|FoodOnAStickItem|ThrowablePotionItem|TieredItem|DiggerItem|BlockItem|DoubleHighBlockItem|StandingAndWallBlockItem'
_BLOCK_BASES = r'Block|BaseBlock|HalfTransparentBlock|BushBlock|FlowerBlock|SaplingBlock|CropBlock|TrapDoorBlock|DoorBlock|FenceBlock|WallBlock|StairBlock|SlabBlock|PressurePlateBlock|ButtonBlock|LeverBlock|TorchBlock|RedStoneWireBlock|ChestBlock|FurnaceBlock|LiquidBlock|GrassBlock|RotatedPillarBlock|HorizontalDirectionalBlock|DirectionalBlock'

JAVA_BLOCK_MATERIAL_MAP = {
    "WOOD": "wood", "STONE": "stone", "METAL": "metal", "SAND": "sand",
    "GLASS": "glass", "CLOTH": "wool", "PLANT": "plant", "DIRT": "dirt",
    "GRASS": "dirt", "ICE": "ice", "LEAVES": "leaves", "WEB": "web",
    "SPONGE": "sponge", "WATER": "water", "LAVA": "lava",
    "FIRE": "decoration", "DECORATION": "decoration",
}
def _build_block_definition(block_id: str, safe_name: str, namespace: str, java_code: str, block_class_name: str = "") -> dict:
    search_text = java_code
    if block_class_name:
        search_text = block_class_name + "\n" + java_code
    props = extract_block_properties_from_java(search_text)
    mat_raw = re.search(r'Material\.([A-Z_]+)', search_text)
    material_key = mat_raw.group(1) if mat_raw else ""
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
    if re.search(r'BlockStateProperties\.FACING|DirectionProperty', search_text, re.I):
        states["facing"] = ["north", "south", "east", "west", "up", "down"]
        rot_map = {"north": 0, "south": 180, "east": 90, "west": 270}
        for d, rot in rot_map.items():
            permutations.append({
                "condition": f'query.block_property("{namespace}:facing") == "{d}"',
                "components": {"minecraft:transformation": {"rotation": [0, rot, 0]}}
            })
    if re.search(r'BlockStateProperties\.POWERED|BooleanProperty.*power', search_text, re.I):
        states["powered"] = [False, True]
        permutations.append({
            "condition": f'query.block_property("{namespace}:powered") == true',
            "components": {"minecraft:light_emission": min(15, light_emission + 8)}
        })
    if re.search(r'BlockStateProperties\.WATERLOGGED', search_text, re.I):
        states["waterlogged"] = [False, True]
    if re.search(r'BlockStateProperties\.OPEN|BooleanProperty.*open', search_text, re.I):
        states["open"] = [False, True]
    if re.search(r'BlockStateProperties\.LIT|BooleanProperty.*lit', search_text, re.I):
        states["lit"] = [False, True]
        permutations.append({
            "condition": f'query.block_property("{namespace}:lit") == true',
            "components": {"minecraft:light_emission": 15}
        })
    if re.search(r'IntegerProperty.*age|BlockStateProperties\.AGE', search_text, re.I):
        m_age = re.search(r'IntegerProperty\.create\s*\([^,]+,\s*\d+,\s*(\d+)', search_text)
        max_age = int(m_age.group(1)) if m_age else 7
        states["age"] = list(range(max_age + 1))
    if re.search(r'HORIZONTAL_FACING|HorizontalDirectionalBlock', search_text, re.I):
        if "facing" not in states:
            states["facing"] = ["north", "south", "east", "west"]
    if re.search(r'\bStairBlock\b', search_text):
        states["facing"] = ["north", "south", "east", "west"]
        states["upside_down_bit"] = [False, True]
    if re.search(r'\bSlabBlock\b', search_text):
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


_BLOCK_REGISTRY_VAR_RE = re.compile(
    r'DeferredRegister\s*(?:'
        r'<\s*(?:[\w.]*\.)?Block\s*>'
        r'|'
        r'\.Blocks\b'
    r')\s+(\w+)\s*=\s*DeferredRegister\.create(?:Blocks)?\s*\('
)

_FABRIC_BLOCK_REGISTRY_RE = re.compile(
    r'Registry\.register\s*\(\s*(?:Registries\.BLOCK|Registry\.BLOCK|BuiltInRegistries\.BLOCK)\s*,',
    re.I
)

def _block_class_extends_block(class_name: str, cls_to_code: Dict[str, str]) -> bool:
    if not class_name:
        return False
    if class_name == "Block" or re.search(rf'\b(?:{_BLOCK_BASES})\b', class_name):
        return True
    if class_name in cls_to_code:
        return bool(re.search(rf'\bextends\s+(?:{_BLOCK_BASES})\b', cls_to_code[class_name]))
    return False

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

        code = _strip_java_comments(raw_code)
        registrations = []


        reg_vars = set(m.group(1) for m in _BLOCK_REGISTRY_VAR_RE.finditer(code))
        for var in reg_vars:
            for m in re.finditer(rf'\b{re.escape(var)}\s*\.\s*(\w+)\s*\(', code):
                method = m.group(1)
                if not method.lower().startswith("register"):
                    continue
                open_paren = m.end() - 1
                args_text = _extract_paren_block(code, open_paren)
                name_m = re.search(r'"([a-zA-Z0-9_./]+)"', args_text)
                if not name_m:
                    continue
                reg_name = name_m.group(1)
                rest = args_text[name_m.end():].lstrip(' \t\r\n,')

                lam_m = re.match(r'(?:\(\)\s*->|\w+\s*->)\s*new\s+([A-Za-z_]\w*)\s*\(', rest)
                if lam_m:
                    block_class = lam_m.group(1)
                    ctor_open = lam_m.end() - 1
                    props_text = _extract_paren_block(rest, ctor_open)
                    after = rest[ctor_open + len(props_text) + 2:].lstrip()
                    extra_body = _extract_block(after, 0) if after.startswith('{') else ""
                    registrations.append((reg_name, block_class, props_text, extra_body))
                    continue

                ctorref_m = re.match(r'([A-Za-z_]\w*)\s*::\s*new\s*,', rest)
                if ctorref_m:
                    block_class = ctorref_m.group(1)
                    props_text = rest[ctorref_m.end():].strip()
                    registrations.append((reg_name, block_class, props_text, ""))
                    continue

                if method.lower() == "registersimpleblock":
                    registrations.append((reg_name, "Block", rest, ""))
                    continue

                new_m = re.match(r'new\s+([A-Za-z_]\w*)\s*\(', rest)
                if new_m:
                    block_class = new_m.group(1)
                    ctor_open = new_m.end() - 1
                    props_text = _extract_paren_block(rest, ctor_open)
                    registrations.append((reg_name, block_class, props_text, ""))
                    continue
        for m in _FABRIC_BLOCK_REGISTRY_RE.finditer(code):
            open_paren = code.index('(', m.start())
            args_text = _extract_paren_block(code, open_paren)
            name_m = re.search(r'"([a-zA-Z0-9_./]+)"\s*\)', args_text)
            if not name_m:
                continue
            reg_name = name_m.group(1)
            rest = args_text[name_m.end():].lstrip(' \t\r\n,')
            new_m = re.match(r'new\s+([A-Za-z_]\w*)\s*\(', rest)
            if new_m:
                block_class = new_m.group(1)
                ctor_open = rest.index('(', new_m.start())
                props_text = _extract_paren_block(rest, ctor_open)
                registrations.append((reg_name, block_class, props_text, ""))

        for m in re.finditer(r'\bregister\w*\s*\(\s*"([a-zA-Z0-9_./]+)"\s*,\s*new\s+([A-Za-z_]\w*)\s*\(', code, re.I):
            reg_name, block_class = m.group(1), m.group(2)
            if not _block_class_extends_block(block_class, cls_to_code):
                continue
            ctor_open = m.end() - 1
            props_text = _extract_paren_block(code, ctor_open)
            registrations.append((reg_name, block_class, props_text, ""))

        if not registrations:
            continue

        for reg_name, block_class, props_text, extra_body in registrations:
            safe_name = sanitize_identifier(reg_name.split('/')[-1].split(':')[-1])
            if not safe_name or safe_name in handled_block_names:
                continue
            block_id = f"{namespace}:{safe_name}"

            combined_parts = [props_text, extra_body]
            custom_path = cls_to_path.get(block_class)
            if custom_path:
                combined_parts.append(cls_to_code.get(block_class, ""))
                handled_files.add(custom_path)
            combined_code = "\n".join(p for p in combined_parts if p)

            try:
                doc = _build_block_definition(block_id, safe_name, namespace, combined_code, block_class_name=block_class)
                generate_block_script(combined_code, safe_name, block_id, namespace)
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

def generate_block_script(java_code: str, safe_name: str, block_id: str, namespace: str) -> bool:
    found_methods = []
    for method_name, (phase, bedrock_event, entity_ref) in _BLOCK_EVENT_METHOD_MAP.items():
        body = _extract_method_body(java_code, method_name)
        if body:
            found_methods.append((method_name, phase, bedrock_event, entity_ref, body))

    tick_bodies = []
    for method_name in _BLOCK_TICK_METHODS:
        body = _extract_method_body(java_code, method_name)
        if body:
            tick_bodies.append((method_name, body))

    has_be_ticker = bool(re.search(
        r'getTicker\s*\(|BlockEntityTicker\s*<|createTickerHelper\s*\(',
        java_code
    ))
    if has_be_ticker and not tick_bodies:

        tick_bodies.append(("blockEntityTick", ""))

    if re.search(r'AbstractContainerMenu|MenuType|createMenu\s*\(|getMenuType\s*\(', java_code):
        _PORTING_NOTES.append(
            f"[block] {safe_name}: uses a ContainerMenu / custom GUI. "
            f"Custom GUIs have no direct Bedrock equivalent — consider using block inventory "
            f"components (minecraft:inventory) and reading them via Scripting API, or a FormUI addon."
        )

    static_handlers = _find_static_event_handlers(java_code)

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
        translated = _translate_handler_body(body, event_type, param, java_code, namespace, safe_name)
        script_lines += [f'world.{phase}.{bedrock_event}.subscribe(({param}) => {{'] + translated + ['});']

    if _needs_repair_helper(static_handlers):
        script_lines += [''] + _emit_repair_helper()

    out_path = os.path.join(BP_FOLDER, "scripts", f"block_{safe_name}.js")
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(script_lines))

    return True

def _finish_block_json(doc: dict, safe_name: str) -> None:
    bp_path = os.path.join(BP_FOLDER, "blocks", f"{safe_name}.json")
    rp_path = os.path.join(RP_FOLDER, "blocks", f"{safe_name}.json")
    os.makedirs(os.path.dirname(bp_path), exist_ok=True)
    os.makedirs(os.path.dirname(rp_path), exist_ok=True)
    safe_write_json(bp_path, doc)
    safe_write_json(rp_path, copy.deepcopy(doc))


def _mirror_bp_block_to_rp(doc: dict, safe_name: str) -> None:
    rp_path = os.path.join(RP_FOLDER, "blocks", f"{safe_name}.json")
    os.makedirs(os.path.dirname(rp_path), exist_ok=True)
    rp_doc = copy.deepcopy(doc)
    try:
        if isinstance(rp_doc, dict):
            block = rp_doc.get("minecraft:block")
            if isinstance(block, dict):
                comps = block.get("components")
                if isinstance(comps, dict):
                    comps.pop("_converter_metadata", None)
    except Exception:
        pass
    safe_write_json(rp_path, rp_doc)

_EFFECT_NAME_MAP = {
    "SPEED": "speed", "SLOWNESS": "slowness", "HASTE": "haste",
    "MINING_FATIGUE": "mining_fatigue", "STRENGTH": "strength",
    "INSTANT_HEALTH": "instant_health", "INSTANT_DAMAGE": "instant_damage",
    "JUMP_BOOST": "jump_boost", "NAUSEA": "nausea", "REGENERATION": "regeneration",
    "RESISTANCE": "resistance", "FIRE_RESISTANCE": "fire_resistance",
    "WATER_BREATHING": "water_breathing", "INVISIBILITY": "invisibility",
    "BLINDNESS": "blindness", "NIGHT_VISION": "night_vision", "HUNGER": "hunger",
    "WEAKNESS": "weakness", "POISON": "poison", "WITHER": "wither",
    "HEALTH_BOOST": "health_boost", "ABSORPTION": "absorption",
    "SATURATION": "saturation", "GLOWING": "glowing", "LEVITATION": "levitation",
    "LUCK": "luck", "UNLUCK": "unluck", "SLOW_FALLING": "slow_falling",
    "CONDUIT_POWER": "conduit_power", "DOLPHINS_GRACE": "dolphins_grace",
    "BAD_OMEN": "bad_omen", "HERO_OF_THE_VILLAGE": "hero_of_the_village",
    "DARKNESS": "darkness",
}

_SOUND_NAME_MAP = {
    "EXPERIENCE_ORB_PICKUP": "random.orb", "ITEM_PICKUP": "random.pop",
    "ENTITY_PLAYER_LEVELUP": "random.levelup", "ENTITY_PLAYER_HURT": "game.player.hurt",
    "ENTITY_PLAYER_DEATH": "game.player.die", "BLOCK_GLASS_BREAK": "random.glass",
    "ENTITY_GENERIC_EXPLODE": "random.explode", "ENTITY_LIGHTNING_BOLT_THUNDER": "ambient.weather.thunder",
    "ENTITY_ENDER_DRAGON_GROWL": "mob.enderdragon.growl", "BLOCK_ANVIL_USE": "random.anvil_use",
    "ENTITY_ARROW_SHOOT": "random.bow", "ENTITY_FIREWORK_ROCKET_LAUNCH": "firework.launch",
}

def _translate_use_body(body: str, namespace: str, safe_name: str) -> list:
    lines = []

    effect_hits = re.findall(
        r'new\s+MobEffectInstance\s*\(\s*MobEffects\.(\w+)\s*,\s*(\d+)\s*(?:,\s*(\d+))?',
        body
    )
    for hit in effect_hits:
        effect_key, duration_ticks, amplifier = hit
        bedrock_effect = _EFFECT_NAME_MAP.get(effect_key, effect_key.lower())
        duration_sec = int(duration_ticks) / 20
        amp = int(amplifier) if amplifier else 0
        lines.append(f'        player.addEffect("minecraft:{bedrock_effect}", {duration_sec}, {{ amplifier: {amp}, showParticles: true }});')

    sound_hits = re.findall(r'SoundEvents\.(\w+)', body)
    for hit in sound_hits:
        bedrock_sound = _SOUND_NAME_MAP.get(hit, "random.pop")
        lines.append(f'        player.dimension.playSound("{bedrock_sound}", player.location);')

    if re.search(r'player\.heal\s*\(|setHealth\s*\(', body):
        heal_m = re.search(r'player\.heal\s*\(\s*([0-9.f]+)', body)
        amount = float(heal_m.group(1).rstrip('f')) if heal_m else 4.0
        lines.append(f'        const health = player.getComponent("minecraft:health");')
        lines.append(f'        if (health) health.setCurrentValue(Math.min(health.currentValue + {amount}, health.effectiveMax));')

    entity_hits = re.findall(
        r'(?:addFreshEntity|summon|spawnEntity)\s*\(\s*new\s+(\w+)\s*\(', body
    )
    for hit in entity_hits:
        entity_id = f"{namespace}:{sanitize_identifier(hit)}"
        lines.append(f'        player.dimension.spawnEntity("{entity_id}", player.location);')

    if re.search(r'player\.setOnFire\s*\(|setSecondsOnFire\s*\(', body):
        fire_m = re.search(r'setOnFire\s*\(\s*(\d+)', body) or re.search(r'setSecondsOnFire\s*\(\s*(\d+)', body)
        seconds = int(fire_m.group(1)) if fire_m else 5
        lines.append(f'        player.setOnFire({seconds});')

    if re.search(r'player\.teleportTo\s*\(|player\.teleport\s*\(', body):
        tp_m = re.search(r'(?:teleportTo|teleport)\s*\(\s*([0-9.-]+)\s*,\s*([0-9.-]+)\s*,\s*([0-9.-]+)', body)
        if tp_m:
            lines.append(f'        player.teleport({{ x: {tp_m.group(1)}, y: {tp_m.group(2)}, z: {tp_m.group(3)} }});')
        else:
            lines.append(f'        // TODO: player.teleport(targetLocation);')

    cooldown_m = re.search(r'addCooldown\s*\(\s*this\s*,\s*(\d+)', body)
    if cooldown_m:
        ticks = int(cooldown_m.group(1))
        lines.append(f'        player.startItemCooldown("{safe_name}", {ticks});')

    if re.search(r'itemStack\.shrink\s*\(1\)|stack\.shrink\s*\(1\)', body):
        lines.append(f'        const inv = player.getComponent("minecraft:inventory");')
        lines.append(f'        if (inv) {{ const slot = inv.container.getSlot(player.selectedSlotIndex); slot.amount = Math.max(0, slot.amount - 1); }}')

    explode_m = re.search(r'(?:explode|createExplosion)\s*\([^,)]*,\s*([0-9.f]+)', body)
    if explode_m:
        power = float(explode_m.group(1).rstrip('f'))
        lines.append(f'        player.dimension.createExplosion(player.location, {power}, {{ breaksBlocks: true }});')

    xp_m = re.search(r'(?:addXp|giveExperiencePoints|giveExperience|addExperience)\s*\(\s*([0-9]+)', body)
    if xp_m:
        lines.append(f'        player.addExperience({xp_m.group(1)});')

    msg_m = re.search(r'(?:sendSystemMessage|displayClientMessage|sendMessage)\s*\(\s*(?:Component\.(?:literal|translatable)\s*\(\s*)?["\']([^"\']+)["\']', body)
    if msg_m:
        lines.append(f'        player.sendMessage("{msg_m.group(1)}");')
    elif re.search(r'(?:sendSystemMessage|displayClientMessage|sendMessage)\s*\(', body):
        lines.append(f'        // TODO: player.sendMessage("...");')

    setblock_m = re.search(r'(?:setBlockAndUpdate|setBlock)\s*\([^,]+,\s*Blocks\.(\w+)', body)
    if setblock_m:
        bedrock_block = f"minecraft:{setblock_m.group(1).lower()}"
        lines.append(f'        // TODO: block.setPermutation(BlockPermutation.resolve("{bedrock_block}"));')

    sched_m = re.search(r'(?:scheduleTick|scheduleBlockTick)\s*\([^,]+,\s*[^,]+,\s*(\d+)', body)
    if sched_m:
        delay = int(sched_m.group(1))
        lines.append(f'        system.runTimeout(() => {{')
        lines.append(f'            // TODO: scheduled tick logic (originally {delay} game ticks)')
        lines.append(f'        }}, {delay});')

    particle_m = re.search(r'addParticle\s*\(\s*(\w+(?:\.\w+)*)\s*,', body)
    if particle_m:
        java_particle = particle_m.group(1).split(".")[-1].lower()
        bedrock_particle = JAVA_PARTICLE_MAP.get(java_particle, "minecraft:enchantment_table_particle")
        lines.append(f'        player.dimension.spawnParticle("{bedrock_particle}", player.location);')

    vel_m = re.search(r'setDeltaMovement\s*\(\s*([0-9.-]+)\s*,\s*([0-9.-]+)\s*,\s*([0-9.-]+)', body)
    if vel_m:
        lines.append(f'        player.applyImpulse({{ x: {vel_m.group(1)}, y: {vel_m.group(2)}, z: {vel_m.group(3)} }});')

    nbt_set = re.search(r'getPersistentData\(\)\.put(?:Int|Float|Double|Boolean|String)\s*\(\s*["\'](\w+)["\']', body)
    if nbt_set:
        lines.append(f'        // TODO: entity.setDynamicProperty("{namespace}:{nbt_set.group(1)}", value);')

    return lines

_ENTITY_SCRIPT_METHODS: Dict[str, Tuple[str, str, str]] = {

    "hurt":                  ("afterEvents", "entityHurt",               "event.hurtEntity"),
    "die":                   ("afterEvents", "entityDie",                "event.deadEntity"),
    "doHurtTarget":          ("afterEvents", "entityHitEntity",          "event.damagingEntity"),
    "performAttack":         ("afterEvents", "entityHitEntity",          "event.damagingEntity"),
    "interact":              ("afterEvents", "playerInteractWithEntity",  "event.target"),
    "interactAt":            ("afterEvents", "playerInteractWithEntity",  "event.target"),
    "onAddedToWorld":        ("afterEvents", "entitySpawn",              "event.entity"),
    "mobInteract":           ("afterEvents", "playerInteractWithEntity",  "event.target"),
    "shoot":                 ("afterEvents", "projectileHitEntity",      "event.projectile"),
    "onProjectileHit":       ("afterEvents", "projectileHitEntity",      "event.projectile"),
}

_ENTITY_TICK_METHODS: List[str] = [
    "tick", "aiStep", "customServerAiStep", "serverAiStep", "baseTick", "rideTick"
]

def _translate_entity_body(body: str, namespace: str, safe_name: str) -> list:
    lines = []

    for hit in re.findall(
        r'new\s+MobEffectInstance\s*\(\s*MobEffects\.(\w+)\s*,\s*(\d+)\s*(?:,\s*(\d+))?',
        body
    ):
        effect_key, dur, amp = hit
        bedrock_effect = _EFFECT_NAME_MAP.get(effect_key, effect_key.lower())
        lines.append(
            f'            entity.addEffect("minecraft:{bedrock_effect}", {int(dur)/20}, '
            f'{{ amplifier: {int(amp) if amp else 0}, showParticles: true }});'
        )

    for hit in re.findall(r'SoundEvents\.(\w+)', body):
        bedrock_sound = _SOUND_NAME_MAP.get(hit, "random.pop")
        lines.append(f'            entity.dimension.playSound("{bedrock_sound}", entity.location);')

    heal_m = re.search(r'(?:heal|setHealth)\s*\(\s*([0-9.f]+)', body)
    if heal_m:
        amount = float(heal_m.group(1).rstrip('f'))
        lines.append(f'            const health = entity.getComponent("minecraft:health");')
        lines.append(f'            if (health) health.setCurrentValue(Math.min(health.currentValue + {amount}, health.effectiveMax));')

    hurt_m = re.search(r'(?<!player\.)(?:hurt|damage)\s*\(\s*[^,)]+,\s*([0-9.f]+)', body)
    if hurt_m:
        lines.append(f'            entity.applyDamage({float(hurt_m.group(1).rstrip("f"))});')

    fire_m = re.search(r'(?:setOnFire|setSecondsOnFire)\s*\(\s*(\d+)', body)
    if fire_m:
        lines.append(f'            entity.setOnFire({int(fire_m.group(1))});')

    for hit in re.findall(r'(?:addFreshEntity|summon|spawnEntity)\s*\(\s*new\s+(\w+)\s*\(', body):
        eid = f"{namespace}:{sanitize_identifier(hit)}"
        lines.append(f'            entity.dimension.spawnEntity("{eid}", entity.location);')

    explode_m = re.search(r'(?:explode|createExplosion)\s*\([^,)]*,\s*([0-9.f]+)', body)
    if explode_m:
        power = float(explode_m.group(1).rstrip('f'))
        lines.append(f'            entity.dimension.createExplosion(entity.location, {power}, {{ breaksBlocks: true }});')

    particle_m = re.search(r'addParticle\s*\(\s*(\w+(?:\.\w+)*)\s*,', body)
    if particle_m:
        java_particle = particle_m.group(1).split(".")[-1].lower()
        bedrock_particle = JAVA_PARTICLE_MAP.get(java_particle, "minecraft:enchantment_table_particle")
        lines.append(f'            entity.dimension.spawnParticle("{bedrock_particle}", entity.location);')

    tp_m = re.search(r'(?:teleportTo|moveTo)\s*\(\s*([0-9.-]+)\s*,\s*([0-9.-]+)\s*,\s*([0-9.-]+)', body)
    if tp_m:
        lines.append(f'            entity.teleport({{ x: {tp_m.group(1)}, y: {tp_m.group(2)}, z: {tp_m.group(3)} }});')
    elif re.search(r'teleportTo\s*\(|teleport\s*\(', body):
        lines.append(f'            // TODO: entity.teleport(targetLocation);')

    vel_m = re.search(r'setDeltaMovement\s*\(\s*([0-9.-]+)\s*,\s*([0-9.-]+)\s*,\s*([0-9.-]+)', body)
    if vel_m:
        lines.append(f'            entity.applyImpulse({{ x: {vel_m.group(1)}, y: {vel_m.group(2)}, z: {vel_m.group(3)} }});')

    if re.search(r'(?:remove|discard)\s*\(\s*\)', body):
        lines.append(f'            entity.remove();')

    for m in re.findall(r'entityData\.set\s*\(\s*(\w+)\s*,\s*(.+?)\s*\)', body):
        field_ref, value_expr = m
        v = value_expr.strip()
        if re.match(r'^[0-9.-]+$', v) or v in ("true", "false"):
            lines.append(f'            entity.setDynamicProperty("{namespace}:{safe_name}_{field_ref.lower()}", {v});')
        else:
            lines.append(f'            // TODO: entity.setDynamicProperty("{namespace}:{safe_name}_{field_ref.lower()}", value);')

    nbt_m = re.search(r'getPersistentData\(\)\.put(?:Int|Float|Double|Boolean|String)\s*\(\s*["\'](\w+)["\']', body)
    if nbt_m:
        lines.append(f'            // TODO: entity.setDynamicProperty("{namespace}:{nbt_m.group(1)}", value);')

    if not lines:
        lines.append(f'            // TODO: translate {safe_name} entity behavior manually')

    return lines

def generate_entity_dynamic_properties(java_code: str, safe_name: str, namespace: str) -> list:
    lines = []
    for m in re.finditer(
        r'EntityDataAccessor\s*<\s*(\w+)\s*>\s+(\w+)\s*=\s*SynchedEntityData\.defineId\s*\([^)]*EntityDataSerializers\.(\w+)',
        java_code
    ):
        field_name = m.group(2).lower()
        serializer = m.group(3)
        prop_key = f'"{namespace}:{safe_name}_{field_name}"'
        if serializer in ("BOOLEAN",):
            lines.append(f'    e.propertyRegistry.defineEntityBooleanProperty({prop_key}, false);')
        elif serializer in ("INT", "BYTE", "SHORT", "FLOAT", "DOUBLE",):
            lines.append(f'    e.propertyRegistry.defineEntityNumberProperty({prop_key}, 0);')
        elif serializer in ("STRING", "COMPOUND_TAG",):
            lines.append(f'    e.propertyRegistry.defineEntityStringProperty({prop_key}, "");')
        else:
            lines.append(f'    // TODO: dynamic property for {field_name} (serializer={serializer})')
    return lines

def generate_entity_script(java_code: str, safe_name: str, entity_id: str, namespace: str) -> bool:
    script_parts: List[List[str]] = []
    needs_system = False

    tick_bodies = []
    for method_name in _ENTITY_TICK_METHODS:
        body = _extract_method_body(java_code, method_name)
        if body:
            tick_bodies.append((method_name, body))

    if tick_bodies:
        needs_system = True
        tick_lines: List[str] = [
            f'// Tick behavior ported from: {", ".join(m for m, _ in tick_bodies)}',
            f'system.runInterval(() => {{',
            f'    for (const dimName of ["overworld", "nether", "the_end"]) {{',
            f'        let entities;',
            f'        try {{ entities = world.getDimension(dimName).getEntities({{ type: "{entity_id}" }}); }}',
            f'        catch (_) {{ continue; }}',
            f'        for (const entity of entities) {{',
        ]
        for method_name, body in tick_bodies:
            tick_lines.append(f'            // === {method_name} ===')
            tick_lines += _translate_entity_body(body, namespace, safe_name)
        tick_lines += ['        }', '    }', '}, 1);']
        script_parts.append(tick_lines)

    for method_name, (phase, bedrock_event, entity_ref) in _ENTITY_SCRIPT_METHODS.items():
        body = _extract_method_body(java_code, method_name)
        if not body:
            continue
        translated = _translate_entity_body(body, namespace, safe_name)
        ev_lines = [
            f'// {method_name}() → {bedrock_event}',
            f'world.{phase}.{bedrock_event}.subscribe((event) => {{',
            f'    const entity = {entity_ref};',
            f'    if (!entity || entity.typeId !== "{entity_id}") return;',
        ] + translated + ['});']
        script_parts.append(ev_lines)

    dp_lines = generate_entity_dynamic_properties(java_code, safe_name, namespace)
    if dp_lines:
        script_parts.append(
            ['// SynchedEntityData → Bedrock dynamic properties',
             'world.afterEvents.worldInitialize.subscribe((e) => {']
            + dp_lines
            + ['});']
        )

    if re.search(r'AbstractContainerMenu|MenuType|createMenu\s*\(|getMenuType\s*\(', java_code):
        _PORTING_NOTES.append(
            f"[entity] {safe_name}: uses AbstractContainerMenu (custom GUI). "
            f"Custom GUIs have no Bedrock equivalent — use block inventory components or a Form UI addon."
        )

    for event_type, (phase, bedrock_event) in _FORGE_EVENT_MAP.items():
        short = event_type.split(".")[-1]
        pat = (
            r'(?:public|private|protected)\s+(?!static)\w+\s+(\w+)\s*\('
            r'[^)]*?' + re.escape(short) + r'\s+(\w+)[^)]*?\)'
        )
        for m in re.finditer(pat, java_code, re.DOTALL):
            method_name = m.group(1)
            param_name  = m.group(2)
            start = java_code.find('{', m.end())
            if start == -1:
                continue
            depth, i, body = 0, start, ""
            while i < len(java_code):
                if java_code[i] == '{':    depth += 1
                elif java_code[i] == '}':
                    depth -= 1
                    if depth == 0:
                        body = java_code[start:i + 1]
                        break
                i += 1
            if body:
                translated = _translate_handler_body(body, event_type, param_name, java_code, namespace, safe_name)
                ev_lines = [
                    f'// {method_name}() instance @SubscribeEvent → {bedrock_event}',
                    f'world.{phase}.{bedrock_event}.subscribe(({param_name}) => {{',
                ] + translated + ['});']
                script_parts.append(ev_lines)

    if not script_parts:
        return False

    imports = ['world']
    if needs_system:
        imports.append('system')
    all_lines = [f'import {{ {", ".join(imports)} }} from "@minecraft/server";', '']
    for i, part in enumerate(script_parts):
        all_lines += part
        if i < len(script_parts) - 1:
            all_lines.append('')

    out_path = os.path.join(BP_FOLDER, "scripts", f"entity_{safe_name}.js")
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(all_lines))

    return True

_INHERITANCE_GRAPH: Dict[str, str] = {}
_PORTING_NOTES: list = []

def build_inheritance_graph(java_files: Dict[str, str]) -> None:
    global _INHERITANCE_GRAPH
    _INHERITANCE_GRAPH = {}
    for code in java_files.values():
        for m in re.finditer(
            r'\bclass\s+(\w+)\s+extends\s+(\w+)',
            code
        ):
            _INHERITANCE_GRAPH[m.group(1)] = m.group(2)

def resolve_superchain(cls_name: str, max_depth: int = 12) -> List[str]:
    chain = []
    current = cls_name
    seen = set()
    for _ in range(max_depth):
        parent = _INHERITANCE_GRAPH.get(current)
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
        is_cap = bool(re.search(
            r'implements\s+(?:[A-Za-z,\s]*\b(?:ICapabilityProvider|ICapabilitySerializable|INBTSerializable|IEnergyStorage|IFluidHandler)\b)',
            code
        ))
        if not is_cap:
            continue
        cls_name = extract_class_name(code) or os.path.splitext(os.path.basename(path))[0]
        safe_name = clean_java_artifact_name(cls_name)

        is_energy = bool(re.search(r'IEnergyStorage', code))
        is_fluid = bool(re.search(r'IFluidHandler', code))

        fields = re.findall(
            r'(?:private|protected|public)\s+(int|float|double|long|boolean|String|ItemStack|ResourceLocation)\s+(\w+)\s*(?:=|;)',
            code
        )
        if not fields and not is_energy and not is_fluid:
            _PORTING_NOTES.append(
                f"[capability] {cls_name}: implements ICapabilityProvider but no simple fields detected — "
                f"convert to Bedrock dynamic properties manually"
            )
            continue

        script_lines = [f'import {{ world }} from "@minecraft/server";', '']

        if is_energy:
            script_lines.append(
                f'world.afterEvents.worldInitialize.subscribe((e) => {{'
            )
            script_lines.append(
                f'    e.propertyRegistry.defineEntityNumberProperty("{namespace}:{safe_name}_energy", 0);'
            )
            script_lines.append('});')
            script_lines.append('')

        if is_fluid:
            script_lines.append(
                f'world.afterEvents.worldInitialize.subscribe((e) => {{'
            )
            script_lines.append(
                f'    e.propertyRegistry.defineEntityNumberProperty("{namespace}:{safe_name}_fluid_amount", 0);'
            )
            script_lines.append(
                f'    e.propertyRegistry.defineEntityStringProperty("{namespace}:{safe_name}_fluid_type", "minecraft:water");'
            )
            script_lines.append('});')
            script_lines.append('')

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
            bedrock_type = _CAP_FIELD_TYPE_MAP.get(java_type, "string")
            script_lines.append(
                f'world.afterEvents.worldInitialize.subscribe((e) => {{'
            )
            script_lines.append(
                f'    e.propertyRegistry.defineEntityNumberProperty("{namespace}:{safe_name}_{field_name}", 0);'
                if bedrock_type == "number" else
                f'    e.propertyRegistry.defineEntityStringProperty("{namespace}:{safe_name}_{field_name}", "");'
                if bedrock_type == "string" else
                f'    e.propertyRegistry.defineEntityBooleanProperty("{namespace}:{safe_name}_{field_name}", false);'
            )
            script_lines.append('});')
            script_lines.append('')

        getter_methods = re.findall(
            r'public\s+\S+\s+(get\w+)\s*\(\s*\)',
            code
        )
        setter_methods = re.findall(
            r'public\s+void\s+(set\w+)\s*\(\s*\S+\s+(\w+)\s*\)',
            code
        )

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

_PACKET_HANDLER_PATTERNS = [
    r'SimpleChannel\s*\.\s*(?:newSimpleChannel|create)\s*\(\s*(?:new\s+)?ResourceLocation\s*\(\s*["\']([^"\']+)["\']',
    r'ChannelBuilder\s*\.\s*named\s*\(\s*(?:new\s+)?ResourceLocation\s*\(\s*["\']([^"\']+)["\']',
    r'NetworkRegistry\s*\.\s*newSimpleChannel\s*\(\s*(?:new\s+)?ResourceLocation\s*\(\s*["\']([^"\']+)["\']',
]

def scan_networking(java_files: Dict[str, str], namespace: str) -> None:
    channel_files: dict = {}
    packet_classes: dict = {}

    for path, code in java_files.items():
        for pat in _PACKET_HANDLER_PATTERNS:
            m = re.search(pat, code)
            if m:
                channel_files[path] = (code, m.group(1))
                break

        if re.search(
            r'implements\s+(?:[A-Za-z,\s]*\b(?:CustomPacketPayload|FriendlyByteBuf)\b)',
            code
        ):
            cls_name = extract_class_name(code)
            if cls_name:
                packet_classes[cls_name] = code

        for pcls in (
            re.findall(r'\.registerMessage\s*\([^,]+,\s*(\w+)\.class', code) +
            re.findall(r'\.messageBuilder\s*\(\s*(\w+)\.class', code) +
            re.findall(r'\.play\.toClient\(\s*(\w+)\.class', code) +
            re.findall(r'\.play\.toServer\(\s*(\w+)\.class', code)
        ):
            if pcls not in packet_classes:
                packet_classes[pcls] = ""

    if not channel_files and not packet_classes:
        return

    serverbound_lines: list = [
        f'import {{ world, system }} from "@minecraft/server";', ''
    ]
    clientbound_lines: list = []

    def _classify_direction(pcode: str) -> str:
        if re.search(r'void\s+handle\s*\([^)]*(?:Level|ServerLevel|Player|ServerPlayer)[^)]*\)', pcode):
            return 'server'
        if re.search(r'void\s+handle\s*\([^)]*Minecraft[^)]*\)', pcode):
            return 'client'
        if re.search(r'\bServerPayloadHandler\b|\bPlayPayloadHandler\b', pcode):
            return 'server'
        if re.search(r'\bClientPayloadHandler\b', pcode):
            return 'client'
        return 'unknown'

    def _extract_packet_fields(pcode: str) -> list:
        fields = re.findall(
            r'(?:private|public|protected|final)\s+(?:final\s+)?(\w+(?:<[^>]+>)?)\s+(\w+)\s*[;=]',
            pcode
        )

        skip = {'LOGGER', 'HANDLER', 'TYPE', 'STREAM_CODEC', 'ID', 'serialVersionUID'}
        return [(ft, fn) for ft, fn in fields if fn not in skip][:12]

    for pcls, pcode in packet_classes.items():
        safe = sanitize_identifier(pcls)
        direction = _classify_direction(pcode) if pcode else 'unknown'
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
                f'world.afterEvents.scriptEventReceive.subscribe((event) => {{',
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
                f'// that the UI layer can subscribe to via world.afterEvents.scriptEventReceive.',
                f'world.afterEvents.scriptEventReceive.subscribe((event) => {{',
                f'    if (event.id !== "{event_id}") return;',
            ] + field_lines + [
                f'    // Re-broadcast to all players (or filter by data.target)',
                f'    for (const p of world.getAllPlayers()) {{',
                f'        p.runCommand(`scriptevent {namespace}:{safe}_ack ${{JSON.stringify(data)}}`);',
                f'    }}',
                f'}});',
                '',
            ]

        _PORTING_NOTES.append(
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

def detect_client_only(java_code: str, cls_name: str) -> Optional[str]:
    for imp in _CLIENT_ONLY_IMPORTS:
        if f"import {imp}" in java_code:
            return f"uses client-only import package {imp}"

    superclass_m = re.search(r'\bextends\s+(\w+)', java_code)
    if superclass_m:
        base = superclass_m.group(1)
        if base in _CLIENT_RENDERER_BASES:
            return f"extends {base}"
        for ancestor in resolve_superchain(base):
            if ancestor in _CLIENT_RENDERER_BASES:
                return f"extends {base} (which extends {ancestor})"

    if re.search(r'@OnlyIn\s*\(\s*Dist\.CLIENT\s*\)|@Environment\s*\(\s*EnvType\.CLIENT\s*\)', java_code):
        return "@OnlyIn(Dist.CLIENT) annotation"

    return None

def scan_client_classes(java_files: Dict[str, str]) -> None:
    for path, code in java_files.items():
        cls_name = extract_class_name(code) or os.path.splitext(os.path.basename(path))[0]
        reason = detect_client_only(code, cls_name)
        if reason:
            _PORTING_NOTES.append(
                f"[client-only] {cls_name} ({reason}): "
                f"client-side rendering has no Bedrock equivalent. "
                f"Textures/models are handled by the RP; custom shaders and render layers cannot be ported."
            )

def write_porting_notes() -> None:
    if not _PORTING_NOTES:
        return

    out_path = "PORTING_NOTES.txt"
    categories = {"mixin": [], "capability": [], "network": [], "client-only": [], "other": []}
    for note in _PORTING_NOTES:
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
            body = re.sub(r'^\[' + cat + r'\]\s*', '', note)
            lines.append(f"  {body}")
        lines.append("")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

_FORGE_EVENT_MAP = {
    "PlayerInteractEvent.EntityInteract":  ("afterEvents", "playerInteractWithEntity"),
    "PlayerInteractEvent.RightClickBlock": ("afterEvents", "playerInteractWithBlock"),
    "PlayerInteractEvent.RightClickItem":  ("afterEvents", "useItem"),
    "PlayerInteractEvent.LeftClickBlock":  ("afterEvents", "playerBreakBlock"),
    "LivingHurtEvent":                     ("afterEvents", "entityHurt"),
    "LivingDeathEvent":                    ("afterEvents", "entityDie"),
    "BlockEvent.BreakEvent":               ("afterEvents", "playerBreakBlock"),
    "BlockEvent.PlaceEvent":               ("afterEvents", "playerPlaceBlock"),
    "EntityJoinLevelEvent":                ("afterEvents", "entitySpawn"),
    "ItemCraftedEvent":                    ("afterEvents", "crafted"),
    "PlayerEvent.ItemPickupEvent":         ("afterEvents", "itemStartPickUp"),
}

def _find_static_event_handlers(java_code: str) -> list:
    handlers = []
    seen = set()
    for event_type, (phase, bedrock_event) in _FORGE_EVENT_MAP.items():
        short = event_type.split(".")[-1]
        pat = (
            r'(?:(?:public|private|protected)\s+)?static\s+\w+\s+(\w+)\s*\('
            r'[^)]*?' + re.escape(short) + r'\s+(\w+)[^)]*?\)'
        )
        for m in re.finditer(pat, java_code, re.DOTALL):
            method_name = m.group(1)
            if method_name in seen:
                continue
            seen.add(method_name)
            param_name = m.group(2)
            start = java_code.find('{', m.end())
            if start == -1:
                continue
            depth, i = 0, start
            body = ""
            while i < len(java_code):
                if java_code[i] == '{':
                    depth += 1
                elif java_code[i] == '}':
                    depth -= 1
                    if depth == 0:
                        body = java_code[start:i + 1]
                        break
                i += 1
            if body:
                handlers.append((method_name, event_type, phase, bedrock_event, param_name, body))
    return handlers

def _extract_tag_path(java_code: str, field_name: str) -> Optional[str]:
    pat = (
        r'\b' + re.escape(field_name) + r'\b[^=\n]*=\s*TagKey\.create\s*\([^,]+,\s*'
        r'ResourceLocation\.(?:fromNamespaceAndPath|of|parse|withDefaultNamespace)\s*\('
        r'[^,)]+,\s*["\']([^"\']+)["\']'
    )
    m = re.search(pat, java_code, re.DOTALL)
    if m:
        return m.group(1)
    pat2 = (
        r'\b' + re.escape(field_name) + r'\b[^=\n]*=\s*TagKey\.create\s*\([^,]+,\s*'
        r'new\s+ResourceLocation\s*\([^,)]+,\s*["\']([^"\']+)["\']'
    )
    m2 = re.search(pat2, java_code, re.DOTALL)
    return m2.group(1) if m2 else None

def _get_player_var(event_type: str, param: str) -> str:
    if not event_type:
        return param
    player_events = {
        "PlayerInteractEvent.EntityInteract",
        "PlayerInteractEvent.RightClickBlock",
        "PlayerInteractEvent.RightClickItem",
        "PlayerInteractEvent.LeftClickBlock",
        "ItemCraftedEvent",
        "PlayerEvent.ItemPickupEvent",
    }
    if event_type in player_events:
        return f"{param}.player"
    if event_type in ("LivingHurtEvent", "LivingDeathEvent"):
        return f"{param}.entity"
    return f"{param}.player"

def _translate_handler_body(java_body: str, event_type: str, param: str, java_code_full: str, namespace: str, safe_name: str) -> list:

    lines = []
    player = _get_player_var(event_type, param)

    ast_lines = JavaAST.translate_java_body_to_js(java_body, event_type, param, namespace, safe_name)
    if ast_lines:
        lines.extend(ast_lines)
    else:

        needs_inv = bool(re.search(r'\.shrink\s*\(|\.addItem\s*\(|getItemStack\s*\(', java_body))
        needs_block = bool(re.search(r'setBlock\s*\(|getBlockState\s*\(|getBlockPos\s*\(|getHitVec\s*\(', java_body))

        if needs_inv:
            lines.append(f'    const inv = {player}.getComponent("minecraft:inventory").container;')
            lines.append(f'    const heldSlot = {player}.selectedSlotIndex;')
            lines.append(f'    let heldItem = inv.getItem(heldSlot);')

        if event_type == "PlayerInteractEvent.RightClickBlock" and needs_block:
            lines.append(f'    const block = {param}.block;')

        if event_type == "PlayerInteractEvent.EntityInteract":
            lines.append(f'    const target = {param}.target;')

        if re.search(r'isCrouching\s*\(\s*\)|isSneaking\s*\(\s*\)', java_body):
            lines.append(f'    if (!{player}.isSneaking) return;')

        null_checks = re.findall(r'(\w+)\s*!=\s*null', java_body)
        entity_null = any(c in ("entityInteractEvent", param, "entity", "player") for c in null_checks)
        if not entity_null and re.search(r'getEntity\s*\(\s*\)\s*!=\s*null', java_body):
            lines.append(f'    if (!{player}) return;')

        target_type_check = re.search(
            r'getTarget\s*\(\s*\)\.getType\s*\(\s*\)\.is\s*\(\s*(\w+(?:\.\w+)*)\s*\)',
            java_body
        )
        if target_type_check:
            ref = target_type_check.group(1).split(".")[-1]
            tag_path = _extract_tag_path(java_code_full, ref)
            entity_hint = sanitize_identifier(tag_path.split("_for_")[0] if tag_path and "_for_" in tag_path else (tag_path or ref))
            lines.append(f'    if (!target || !target.typeId.includes("{entity_hint}")) return;')

        item_is_checks = re.findall(r'(?:getItemStack\s*\(\s*\)|stack|itemStack)\.is\s*\(\s*(\w+)\s*\)', java_body)
        for tag_ref in item_is_checks:
            lines.append(f'    if (!heldItem || heldItem.typeId !== "{namespace}:{sanitize_identifier(tag_ref)}") return;')
        item_identity_check = re.search(
            r'(?:stack|itemStack|heldStack)\.is\s*\(\s*(\w+(?:\.\w+)*)\s*\)',
            java_body
        )

        if item_identity_check and not item_is_checks:
            ref = item_identity_check.group(1).split(".")[-1]
            lines.append(f'    if (!heldItem || heldItem.typeId !== "{namespace}:{sanitize_identifier(ref)}") return;')

        repair_call = bool(re.search(r'repairState\s*\(', java_body))
        if repair_call:
            lines.append(f'    const repairedId = repairBlockId(block.typeId);')
            lines.append(f'    if (!repairedId) return;')

        hurt_break = re.search(r'hurtAndBreak\s*\(\s*(\d+)', java_body)
        if hurt_break:
            dmg = int(hurt_break.group(1))
            lines.append(f'    const dur = heldItem?.getComponent("minecraft:durability");')
            lines.append(f'    if (dur) {{ dur.damage = Math.min(dur.damage + {dmg}, dur.maxDurability); inv.setItem(heldSlot, heldItem); }}')

        if re.search(r'level\.setBlock\s*\(', java_body):
            if repair_call:
                lines.append(f'    block.setPermutation(BlockPermutation.resolve(repairedId));')
            else:
                lines.append(f'    block.setPermutation(block.permutation);')

        play_sound = re.search(
            r'playSound\s*\([^,]+,\s*[^,]+,\s*[^,]+,\s*[^,]+,\s*SoundEvents\.(\w+)',
            java_body
        )
        if play_sound:
            bedrock_sound = _SOUND_NAME_MAP.get(play_sound.group(1), "random.pop")
            lines.append(f'    {player}.dimension.playSound("{bedrock_sound}", {player}.location);')

        if re.search(r'\.swing\s*\(', java_body):
            lines.append(f'    {player}.playAnimation("animation.player.attack.rotations");')

        instabuild = bool(re.search(r'getAbilities\s*\(\s*\)\.instabuild|isCreative\s*\(\s*\)', java_body))
        shrink = re.search(r'(?:getItemStack\s*\(\s*\)|stack|itemStack)\.shrink\s*\(\s*(\d+)\s*\)', java_body)
        if shrink:
            amt = int(shrink.group(1))
            if instabuild:
                lines.append(f'    if ({player}.getGameMode() !== GameMode.creative) {{')
                lines.append(f'        if (heldItem) {{ heldItem.amount = Math.max(0, heldItem.amount - {amt}); inv.setItem(heldSlot, heldItem.amount <= 0 ? undefined : heldItem); }}')
                lines.append(f'    }}')
            else:
                lines.append(f'    if (heldItem) {{ heldItem.amount = Math.max(0, heldItem.amount - {amt}); inv.setItem(heldSlot, heldItem.amount <= 0 ? undefined : heldItem); }}')

        add_item = re.search(r'addItem\s*\(([^;]+)\)', java_body)
        if add_item:
            arg = add_item.group(1).strip()
            const_m = re.search(r'\.([A-Z][A-Z0-9_]+)\b', arg)
            if const_m:
                const_name = const_m.group(1)
                reg_m = re.search(
                    r'\b' + re.escape(const_name) + r'\b[^\n]*register\s*\(\s*["\']([a-z0-9_]+)["\']',
                    java_code_full, re.I
                )
                item_name = reg_m.group(1) if reg_m else sanitize_identifier(const_name)
            else:
                plain = arg.split(".")[0].strip()
                item_name = sanitize_identifier(plain)
            lines.append(f'    inv.addItem(new ItemStack("{namespace}:{item_name}"));')

        energy_receive = re.search(r'energy\.receiveEnergy\s*\(\s*(\d+)\s*\)', java_body)
        if energy_receive:
            amt = energy_receive.group(1)
            lines.append(f'    receiveEnergy({player}, {amt});')

        energy_extract = re.search(r'energy\.extractEnergy\s*\(\s*(\d+)\s*\)', java_body)
        if energy_extract:
            amt = energy_extract.group(1)
            lines.append(f'    extractEnergy({player}, {amt});')

        fluid_fill = re.search(r'fluid\.fill\s*\(\s*(\w+),\s*(\d+)\s*\)', java_body)
        if fluid_fill:
            fluid_type = fluid_fill.group(1)
            amt = fluid_fill.group(2)
            lines.append(f'    fill({player}, {{ type: "{namespace}:{fluid_type}", amount: {amt} }});')

        fluid_drain = re.search(r'fluid\.drain\s*\(\s*(\d+)\s*\)', java_body)
        if fluid_drain:
            amt = fluid_drain.group(1)
            lines.append(f'    drain({player}, {amt});')

    return lines

def _needs_repair_helper(handlers: list) -> bool:
    return any(re.search(r'repairState\s*\(', body) for _, _, _, _, _, body in handlers)

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

def generate_scripting_stub(java_code: str, safe_name: str, item_id: str, namespace: str) -> bool:
    use_body        = _extract_method_body(java_code, "use")
    hurt_body       = _extract_method_body(java_code, "hurtEnemy")
    tick_body       = _extract_method_body(java_code, "inventoryTick")
    finish_body     = _extract_method_body(java_code, "finishUsingItem")
    crafted_body    = _extract_method_body(java_code, "onCraftedBy")
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

def convert_java_item_full(java_code: str, java_path: str, namespace: str):
    cls = extract_class_name(java_code) or os.path.splitext(os.path.basename(java_path))[0]
    safe_name = clean_java_artifact_name(cls)
    item_id = f"{namespace}:{safe_name}"
    max_stack = 64
    m = re.search(r'(?:maxStackSize|stacksTo)\s*\(?\s*(\d+)', java_code, re.I)
    if m:
        max_stack = int(m.group(1))
    durability = 0
    m2 = re.search(r'(?:maxDamage|durability|defaultDurability)\s*\(?\s*(\d+)', java_code, re.I)
    if m2:
        durability = int(m2.group(1))
    components = {
        "minecraft:icon": {"texture": find_best_texture_match(safe_name, "items")},
        "minecraft:max_stack_size": max_stack,
    }
    if durability > 0:
        components["minecraft:durability"] = {"max_durability": durability}
    is_food = bool(re.search(r'FoodProperties|\.food\(|nutrition|saturation|isFood|extends\s+ItemFood|extends\s+BowlFoodItem', java_code, re.I))
    if is_food:
        nutrition = 4
        saturation = 0.3
        m3 = re.search(r'nutrition\s*\(?\s*(\d+)', java_code, re.I)
        if m3:
            nutrition = int(m3.group(1))
        m4 = re.search(r'saturation(?:Modifier)?\s*\(?\s*([0-9.]+)', java_code, re.I)
        if m4:
            saturation = float(m4.group(1))
        components["minecraft:food"] = {
            "nutrition": nutrition,
            "saturation_modifier": saturation,
            "can_always_eat": bool(re.search(r'alwaysEat|canAlwaysEat', java_code, re.I))
        }
        components["minecraft:use_animation"] = "eat"
        components["minecraft:use_duration"] = 32
    armor_slot = None
    if re.search(r'ArmorItem|EquipmentSlot\.HEAD', java_code, re.I):
        armor_slot = "slot.armor.head"
    elif re.search(r'EquipmentSlot\.CHEST', java_code, re.I):
        armor_slot = "slot.armor.chest"
    elif re.search(r'EquipmentSlot\.LEGS', java_code, re.I):
        armor_slot = "slot.armor.legs"
    elif re.search(r'EquipmentSlot\.FEET', java_code, re.I):
        armor_slot = "slot.armor.feet"
    if armor_slot:
        components["minecraft:wearable"] = {"protection": 3, "slot": armor_slot}
    if re.search(r'SwordItem|TieredItem|extends.*Sword', java_code, re.I):
        atk = 4.0
        m5 = re.search(r'attackDamage\s*[=+]+\s*([0-9.]+)', java_code, re.I)
        if m5:
            atk = float(m5.group(1))
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
    if re.search(r'EnchantmentCategory|getEnchantmentValue|enchantmentValue|enchantable', java_code, re.I):
        m_ench = re.search(r'(?:enchantmentValue|getEnchantmentValue)\s*\(\s*\)\s*\{\s*return\s*(\d+)', java_code, re.I)
        enchant_value = int(m_ench.group(1)) if m_ench else 10
    if enchant_value > 0:
        ench_slot = "all"
        if re.search(r'SwordItem|AxeItem|weapon', java_code, re.I):
            ench_slot = "weapon"
        elif re.search(r'ArmorItem|BootsItem|HelmItem|armor', java_code, re.I):
            ench_slot = "armor"
        elif re.search(r'PickaxeItem|ShovelItem|HoeItem|tool', java_code, re.I):
            ench_slot = "tool"
        components["minecraft:enchantable"] = {"value": enchant_value, "slot": ench_slot}
    if re.search(r'isFoil|hasGlint|isEnchanted', java_code, re.I):
        components["minecraft:glint"] = True
    has_instance_use = bool(re.search(
        r'@Override\s+public\s+\S+\s+(?:use|hurtEnemy|inventoryTick)\s*\(',
        java_code, re.DOTALL
    ))
    has_static_handlers = bool(_find_static_event_handlers(java_code))
    if has_instance_use or has_static_handlers:
        stub_written = generate_scripting_stub(java_code, safe_name, item_id, namespace)
        if stub_written and has_instance_use:
            components["minecraft:custom_components"] = [f"{namespace}:{safe_name}_use"]
    out_path = os.path.join(BP_FOLDER, "items", f"{safe_name}.json")
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    safe_write_json(out_path, doc)

JAVA_PARTICLE_MAP = {
    "explosion": "minecraft:explosion_particle",
    "large_explosion": "minecraft:explosion_particle",
    "huge_explosion": "minecraft:explosion_particle",
    "fireworks_spark": "minecraft:fireworks_spark_particle",
    "bubble": "minecraft:bubble_particle",
    "splash": "minecraft:water_splash_particle",
    "wake": "minecraft:water_wake_particle",
    "suspended": "minecraft:water_splash_particle",
    "depth_suspend": "minecraft:water_splash_particle",
    "crit": "minecraft:critical_hit_emitter",
    "magic_crit": "minecraft:critical_hit_emitter",
    "smoke": "minecraft:basic_smoke_particle",
    "large_smoke": "minecraft:basic_smoke_particle",
    "mob_spell": "minecraft:spell_particle",
    "mob_spell_ambient": "minecraft:spell_particle",
    "spell": "minecraft:spell_particle",
    "instant_spell": "minecraft:spell_particle",
    "witch_magic": "minecraft:witch_spell_particle",
    "note": "minecraft:note_particle",
    "portal": "minecraft:portal_particle",
    "enchantment_table": "minecraft:enchanting_table_particle",
    "flame": "minecraft:basic_flame_particle",
    "lava": "minecraft:lava_particle",
    "footstep": "minecraft:falling_dust_sand_particle",
    "cloud": "minecraft:evaporation_particle",
    "reddust": "minecraft:redstone_wire_dust_particle",
    "snowball": "minecraft:snowball_particle",
    "drip_water": "minecraft:water_drip_particle",
    "drip_lava": "minecraft:lava_drip_particle",
    "snow_shovel": "minecraft:snowball_particle",
    "slime": "minecraft:slime_particle",
    "heart": "minecraft:heart_particle",
    "angry_villager": "minecraft:villager_angry_particle",
    "happy_villager": "minecraft:villager_happy_particle",
    "barrier": "minecraft:barrier_particle",
    "item_crack": "minecraft:basic_crit_particle",
    "block_crack": "minecraft:falling_dust_sand_particle",
    "block_dust": "minecraft:falling_dust_sand_particle",
    "droplet": "minecraft:water_drip_particle",
    "take": "minecraft:basic_crit_particle",
    "mob_appearance": "minecraft:elder_guardian_particle",
    "dragon_breath": "minecraft:dragon_breath_particle",
    "end_rod": "minecraft:end_rod_particle",
    "damage_indicator": "minecraft:critical_hit_emitter",
    "sweep_attack": "minecraft:critical_hit_emitter",
    "totem": "minecraft:totem_particle",
    "spit": "minecraft:llama_spit_particle",
    "squid_ink": "minecraft:squid_ink_particle",
    "bubble_pop": "minecraft:bubble_pop_particle",
    "current_down": "minecraft:bubble_particle",
    "bubble_column_up": "minecraft:bubble_particle",
    "nautilus": "minecraft:nautilus_particle",
    "dolphin": "minecraft:dolphin_particle",
    "campfire_cosy_smoke": "minecraft:campfire_smoke_particle",
    "campfire_signal_smoke": "minecraft:campfire_smoke_particle",
    "composter": "minecraft:composter_particle",
    "flash": "minecraft:flash_particle",
    "falling_lava": "minecraft:lava_drip_particle",
    "landing_lava": "minecraft:lava_particle",
    "falling_water": "minecraft:water_drip_particle",
    "dust": "minecraft:redstone_wire_dust_particle",
    "item_snowball": "minecraft:snowball_particle",
    "item_slime": "minecraft:slime_particle",
    "item_squid_ink": "minecraft:squid_ink_particle",
    "item_bubble_pop": "minecraft:bubble_pop_particle",
    "item_current_down": "minecraft:bubble_particle",
    "item_bubble_column_up": "minecraft:bubble_particle",
    "item_nautilus": "minecraft:nautilus_particle",
    "item_dolphin": "minecraft:dolphin_particle",
    "item_campfire_cosy_smoke": "minecraft:campfire_smoke_particle",
    "item_campfire_signal_smoke": "minecraft:campfire_smoke_particle",
    "item_composter": "minecraft:composter_particle",
    "item_flash": "minecraft:flash_particle",
    "item_falling_lava": "minecraft:lava_drip_particle",
    "item_landing_lava": "minecraft:lava_particle",
    "item_falling_water": "minecraft:water_drip_particle",
    "soul_fire_flame": "minecraft:soul_particle",
    "soul": "minecraft:soul_particle",
    "ash": "minecraft:basic_smoke_particle",
    "crimson_spore": "minecraft:crimson_spore_particle",
    "warped_spore": "minecraft:warped_spore_particle",
    "soul_fire_flame": "minecraft:soul_particle",
    "dripping_obsidian_tear": "minecraft:obsidian_tear_particle",
    "falling_obsidian_tear": "minecraft:obsidian_tear_particle",
    "landing_obsidian_tear": "minecraft:obsidian_tear_particle",
    "reverse_portal": "minecraft:portal_particle",
    "white_ash": "minecraft:basic_smoke_particle",
    "light": "minecraft:light_particle",
    "dust_color_transition": "minecraft:redstone_wire_dust_particle",
    "vibration": "minecraft:vibration_particle",
    "falling_spore_blossom": "minecraft:spore_blossom_particle",
    "spore_blossom_air": "minecraft:spore_blossom_particle",
    "small_flame": "minecraft:basic_flame_particle",
    "snowflake": "minecraft:snowball_particle",
    "dripping_dripstone_lava": "minecraft:lava_drip_particle",
    "falling_dripstone_lava": "minecraft:lava_drip_particle",
    "dripping_dripstone_water": "minecraft:water_drip_particle",
    "falling_dripstone_water": "minecraft:water_drip_particle",
    "glow_squid_ink": "minecraft:squid_ink_particle",
    "glow": "minecraft:glow_particle",
    "wax_on": "minecraft:wax_particle",
    "wax_off": "minecraft:wax_particle",
    "electric_spark": "minecraft:electric_spark_particle",
    "scrape": "minecraft:scrape_particle",
    "shriek": "minecraft:shriek_particle",
    "sonic_boom": "minecraft:sonic_boom_particle",
    "sculk_soul": "minecraft:soul_particle",
    "sculk_charge": "minecraft:sculk_charge_particle",
    "sculk_charge_pop": "minecraft:sculk_charge_pop_particle",
    "sonic_explosion": "minecraft:sonic_boom_particle",
    "dust_plume": "minecraft:dust_plume_particle",
    "gust": "minecraft:gust_particle",
    "trial_spawner_detection": "minecraft:trial_spawner_detection_particle",
    "trial_spawner_detection_ominous": "minecraft:trial_spawner_detection_ominous_particle",
    "vault_connection": "minecraft:vault_connection_particle",
    "dust_pillar": "minecraft:dust_pillar_particle",
    "ominous_spawning": "minecraft:ominous_spawning_particle",
    "raid_omen": "minecraft:raid_omen_particle",
    "trial_omen": "minecraft:trial_omen_particle",
}
def extract_and_generate_particles(java_code: str, entity_id: str, namespace: str):
    safe_name = sanitize_identifier(entity_id.split(":")[-1])
    found = set()

    particle_refs = re.findall(r'\b(\w+)\s*\.\s*spawn\s*\(', java_code)
    for ref in particle_refs:
        if ref in JAVA_PARTICLE_MAP:
            found.add((ref, JAVA_PARTICLE_MAP[ref]))
        else:

            found.add((ref, "minecraft:enchantment_table_particle"))
    if not found:
        return
    out_dir = os.path.join(RP_FOLDER, "particles")
    os.makedirs(out_dir, exist_ok=True)
    for java_name, bedrock_ref in found:
        particle_id = f"{namespace}:{safe_name}_{java_name}"
        doc = {
            "format_version": "1.10.0",
            "particle_effect": {
                "description": {
                    "identifier": particle_id,
                    "basic_render_parameters": {
                        "material": "particles_alpha",
                        "texture": "textures/particle/particles"
                    }
                },
                "components": {
                    "minecraft:emitter_rate_instant": {"num_particles": 8},
                    "minecraft:emitter_lifetime_once": {"active_time": 0.5},
                    "minecraft:particle_initial_speed": 1.0,
                    "minecraft:particle_lifetime_expression": {"max_lifetime": 0.5},
                    "minecraft:particle_appearance_billboard": {
                        "size": [0.1, 0.1],
                        "facing_camera_type": "lookat_xyz",
                        "uv": {"texture_width": 128, "texture_height": 128, "uv": [0, 0], "uv_size": [8, 8]}
                    }
                },
                "_note": f"stub converted from Java particle: {java_name} (original ref: {bedrock_ref})"
            }
        }
        out_path = os.path.join(out_dir, f"{safe_name}_{java_name}.json")
        safe_write_json(out_path, doc)

def convert_lang_files():
    lang_dir = os.path.join(RP_FOLDER, "lang")
    if not os.path.isdir(lang_dir):
        return
    for fname in os.listdir(lang_dir):
        fpath = os.path.join(lang_dir, fname)
        if fname.endswith(".json"):
            try:
                with open(fpath, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if not isinstance(data, dict):
                    continue
                lang_name = os.path.splitext(fname)[0]
                parts = lang_name.split("_")
                if len(parts) == 2:
                    lang_name = f"{parts[0]}_{parts[1].upper()}"
                out_path = os.path.join(lang_dir, f"{lang_name}.lang")
                lines = []
                for k, v in data.items():
                    safe_v = str(v).replace("\n", "\\n")
                    lines.append(f"{k}={safe_v}")
                with open(out_path, "w", encoding="utf-8") as f:
                    f.write("\n".join(lines))
                os.remove(fpath)

            except Exception as e:
                _warn(f"[lang] Failed to convert {fname}: {e}")
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

def _categorise_animations(animations: set) -> dict:
    buckets = {
        "idle":    [], "walk":   [], "run":    [], "attack": [],
        "hurt":    [], "death":  [], "sit":    [], "swim":   [],
        "fly":     [], "sleep":  [], "spawn":  [], "other":  [],
    }
    KEYWORDS = {
        "idle":   ("idle", "stand", "pose", "float"),
        "walk":   ("walk",),
        "run":    ("run", "chase", "sprint"),
        "attack": ("attack", "strike", "bite", "swipe", "slam", "lunge", "claw"),
        "hurt":   ("hurt", "hit", "flinch", "pain"),
        "death":  ("death", "die", "dying", "dead"),
        "sit":    ("sit", "sitting", "crouch", "lay"),
        "swim":   ("swim", "swimming"),
        "fly":    ("fly", "flying", "hover", "glide"),
        "sleep":  ("sleep", "sleeping", "rest"),
        "spawn":  ("spawn", "appear", "emerge", "summon"),
    }
    for anim in animations:
        a = anim.lower()
        placed = False
        for bucket, keys in KEYWORDS.items():
            if any(k in a for k in keys):
                buckets[bucket].append(anim)
                placed = True
                break
        if not placed:
            buckets["other"].append(anim)
    return buckets
def generate_animation_controller(entity_id: str, animations: set, namespace: str,
                                   ai_goals: list = None, java_code: str = "") -> Optional[str]:
    if not animations:
        return None
    safe_name = sanitize_identifier(entity_id.split(":")[-1])
    controller_id = f"controller.animation.{namespace}.{safe_name}"
    buckets = _categorise_animations(animations)
    ai_goals = ai_goals or []
    has_walk   = bool(buckets["walk"] or buckets["run"])
    has_attack = bool(buckets["attack"])
    has_hurt   = bool(buckets["hurt"])
    has_death  = bool(buckets["death"])
    has_sit    = bool(buckets["sit"])
    has_swim   = bool(buckets["swim"])
    has_fly    = bool(buckets["fly"])
    has_sleep  = bool(buckets["sleep"])
    has_spawn  = bool(buckets["spawn"])
    def pick(bucket): return buckets[bucket][0] if buckets[bucket] else None
    idle_anim   = pick("idle")
    walk_anim   = pick("walk") or pick("run")
    run_anim    = pick("run") or walk_anim
    attack_anim = pick("attack")
    hurt_anim   = pick("hurt")
    death_anim  = pick("death")
    sit_anim    = pick("sit")
    swim_anim   = pick("swim")
    fly_anim    = pick("fly")
    sleep_anim  = pick("sleep")
    spawn_anim  = pick("spawn")
    if not idle_anim:
        idle_anim = sorted(animations)[0]

    states = {}
    if has_spawn:
        states["spawn"] = {
            "animations": [spawn_anim],
            "transitions": [{"default": f"query.anim_time >= 1.0"}]
        }
    default_transitions = []
    if has_spawn:
        pass
    if has_walk:
        default_transitions.append({"moving": "query.modified_move_speed > 0.1"})
    if has_attack:
        default_transitions.append({"attacking": "query.is_attacking"})
    if has_hurt:
        default_transitions.append({"hurt": "query.is_hurt"})
    if has_death:
        default_transitions.append({"death": "query.health <= 0"})
    if has_sit and "SitWhenOrderedToGoal" in ai_goals:
        default_transitions.append({"sitting": "query.is_sitting"})
    if has_sleep:
        default_transitions.append({"sleeping": "query.is_sleeping"})
    default_state = {"animations": [idle_anim]}
    if default_transitions:
        default_state["transitions"] = default_transitions
    states["default"] = default_state
    if has_walk:
        moving_anim = run_anim if run_anim else walk_anim
        if buckets["walk"] and buckets["run"]:
            moving_anims = [
                {walk_anim: "1.0 - math.min(query.modified_move_speed / 0.3, 1.0)"},
                {run_anim:  "math.min(query.modified_move_speed / 0.3, 1.0)"}
            ]
        else:
            moving_anims = [moving_anim]
        moving_transitions = [{"default": "query.modified_move_speed <= 0.1"}]
        if has_attack:
            moving_transitions.append({"attacking": "query.is_attacking"})
        if has_death:
            moving_transitions.append({"death": "query.health <= 0"})
        states["moving"] = {
            "animations": moving_anims,
            "transitions": moving_transitions
        }
    if has_attack:
        attack_transitions = [{"default": "!query.is_attacking"}]
        if has_death:
            attack_transitions.append({"death": "query.health <= 0"})
        states["attacking"] = {
            "animations": [attack_anim],
            "transitions": attack_transitions
        }
    if has_hurt:
        states["hurt"] = {
            "animations": [hurt_anim],
            "transitions": [
                {"death": "query.health <= 0"},
                {"default": f"query.anim_time >= 0.3"}
            ]
        }
    if has_death:
        states["death"] = {
            "animations": [death_anim],
            "transitions": []
        }
    if has_sit:
        states["sitting"] = {
            "animations": [sit_anim],
            "transitions": [{"default": "!query.is_sitting"}]
        }
    if has_swim:
        swim_transitions = [{"default": "!query.is_in_water"}]
        if has_attack:
            swim_transitions.insert(0, {"attacking": "query.is_attacking"})
        states["swimming"] = {
            "animations": [swim_anim],
            "transitions": swim_transitions
        }
        if "default" in states and "transitions" in states["default"]:
            states["default"]["transitions"].insert(0, {"swimming": "query.is_in_water"})
        if "moving" in states:
            states["moving"]["transitions"].insert(0, {"swimming": "query.is_in_water"})
    if has_fly:
        states["flying"] = {
            "animations": [fly_anim],
            "transitions": [{"default": "query.is_on_ground"}]
        }
        if "default" in states and "transitions" in states["default"]:
            states["default"]["transitions"].insert(0, {"flying": "!query.is_on_ground"})
    if has_sleep:
        states["sleeping"] = {
            "animations": [sleep_anim],
            "transitions": [{"default": "!query.is_sleeping"}]
        }
    for anim in buckets["other"]:
        state_name = sanitize_identifier(anim.split(".")[-1])
        if state_name not in states and state_name != "default":
            if "animations" in states.get("default", {}):
                if isinstance(states["default"]["animations"], list):
                    states["default"]["animations"].append(anim)
    initial = "spawn" if has_spawn else "default"
    doc = {
        "format_version": "1.10.0",
        "animation_controllers": {
            controller_id: {
                "initial_state": initial,
                "states": states
            }
        }
    }
    out_dir = os.path.join(RP_FOLDER, "animation_controllers")
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, f"{safe_name}.animation_controllers.json")
    safe_write_json(out_path, doc)

    return controller_id
def patch_rp_entity_with_controller(entity_basename: str, animations: set,
                                     controller_id: Optional[str], namespace: str):
    rp_path = os.path.join(RP_FOLDER, "entity", f"{entity_basename}.entity.json")
    if not os.path.exists(rp_path):
        return
    try:
        with open(rp_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return
    desc = data.get("minecraft:client_entity", {}).get("description", {})
    if not desc:
        return
    buckets = _categorise_animations(animations)
    anim_map: Dict[str, str] = {}
    def add_anim(key: str, anim_id: str):
        if anim_id and anim_id not in anim_map.values():
            anim_map[key] = anim_id
    for b_name in ("idle", "walk", "run", "attack", "hurt", "death",
                   "sit", "swim", "fly", "sleep", "spawn"):
        if buckets[b_name]:
            add_anim(b_name, buckets[b_name][0])
    for i, anim in enumerate(buckets["other"]):
        add_anim(f"anim_{i}", anim)
    if anim_map:
        desc["animations"] = anim_map
    animate_list = []
    if controller_id:
        ctrl_short = "ctrl"
        if "animations" not in desc:
            desc["animations"] = {}
        desc["animations"][ctrl_short] = controller_id
        desc["animation_controllers"] = [ctrl_short]
        animate_list = [ctrl_short]
        if "idle" in anim_map:
            animate_list.append({"idle": "query.is_alive"})
        desc["scripts"] = {"animate": animate_list}
    elif anim_map:
        passive = []
        for short, full_id in anim_map.items():
            loop_names = ("idle", "walk", "run", "swim", "fly")
            if any(n in short for n in loop_names):
                passive.append({short: "query.is_alive"})
            else:
                passive.append(short)
        desc["scripts"] = {"animate": passive or list(anim_map.keys())}
    try:
        with open(rp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)

    except Exception as e:
        _warn(f"[anim_wire] Failed to patch {rp_path}: {e}")
def prune_orphaned_assets() -> List[str]:
    removed: List[str] = []
    try:
        _prune_orphaned_assets_impl(removed)
    except Exception as exc:
        import traceback
        removed.append(f"[prune-error] Pruner crashed — {exc}")
        removed.append(f"[prune-error] {traceback.format_exc()}")
    return removed

def _prune_orphaned_assets_impl(removed: List[str]) -> None:


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
                        referenced_textures.add(s.lstrip("textures/"))
                        referenced_textures.add(os.path.splitext(s)[0])
                    if s.startswith("geometry."):
                        referenced_geo_ids.add(s)
                        tail = s[len("geometry."):]
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
                    geo_id_to_file[ident] = fpath
                    tail = ident[len("geometry."):] if ident.startswith("geometry.") else ident
                    geo_stem_to_file[tail] = fpath
                    geo_stem_to_file[tail.split(".")[-1]] = fpath
            stem = os.path.splitext(fname)[0]
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
            stem = os.path.splitext(fname)[0]
            base = os.path.splitext(stem)[0]
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
ENTITY_REGISTRY: Dict[str, str] = {}
ATTRS_REGISTRY:  Dict[str, dict] = {}
SOUND_CONST_MAP: Dict[str, str] = {}
ENTITY_SOUND_PROFILES: Dict[str, dict] = {}
BLOCK_SOUND_PROFILES: Dict[str, dict] = {}
ITEM_SOUND_PROFILES: Dict[str, dict] = {}
def detect_mod_id(java_files: dict) -> str:
    for path, code in java_files.items():
        ast = JavaAST(code)
        ast._parse()
        if ast._tree is not None:
            val = ast.annotation_value('Mod')
            if val and re.match(r'^[a-z0-9_-]+$', val):
                return sanitize_identifier(val)
            vals = ast.field_string_values({'MOD_ID', 'MODID', 'MOD_ID_STR', 'ID'})
            for _, v in vals.items():
                if v and re.match(r'^[a-z0-9_-]+$', v):
                    return sanitize_identifier(v)
        else:
            m = re.search(r'@Mod\s*\(\s*["\'\']([a-z0-9_-]+)["\'\']', code)
            if m:
                return sanitize_identifier(m.group(1))
            m = re.search(r'(?:MOD_ID|MODID|MOD_ID_STR|ID)\s*=\s*["\']([a-z0-9_-]+)["\']', code)
            if m:
                return sanitize_identifier(m.group(1))
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
def _build_resource_location_constants(java_files: dict) -> Dict[str, str]:
    constants: Dict[str, str] = {}
    _RL_TYPES = r'(?:ResourceLocation|RL|Identifier)'
    for _path, code in java_files.items():
        for m in re.finditer(
            rf'(?:static\s+final\s+)?{_RL_TYPES}\s+(\w+)\s*='
            r'\s*new\s+ResourceLocation\s*\(\s*["\']([^"\']+)["\']\s*,\s*["\']([^"\']+)["\']\s*\)',
            code
        ):
            constants[m.group(1)] = f"{m.group(2)}:{m.group(3)}"
        for m in re.finditer(
            rf'(?:static\s+final\s+)?{_RL_TYPES}\s+(\w+)\s*='
            r'\s*new\s+ResourceLocation\s*\(\s*["\']([a-z0-9_:/-][^"\']*)["\']',
            code
        ):
            constants[m.group(1)] = m.group(2)
        for m in re.finditer(
            rf'(?:static\s+final\s+)?{_RL_TYPES}\s+(\w+)\s*='
            r'\s*ResourceLocation\.(?:fromNamespaceAndPath|of)\s*\(\s*["\']([^"\']+)["\']\s*,\s*["\']([^"\']+)["\']\s*\)',
            code
        ):
            constants[m.group(1)] = f"{m.group(2)}:{m.group(3)}"
        for m in re.finditer(
            rf'(?:static\s+final\s+)?{_RL_TYPES}\s+(\w+)\s*='
            r'\s*ResourceLocation\.(?:tryParse|tryBuild|of)\s*\(\s*["\']([a-z0-9_:/-][^"\']*)["\']',
            code
        ):
            constants[m.group(1)] = m.group(2)
    return constants
def build_entity_registry(java_files: dict, namespace: str) -> dict:
    registry = {}
    for path, code in java_files.items():
        ast = JavaAST(code)
        ast._parse()
        if ast._tree is not None:
            for inv in ast.invocations_of('register'):
                args = getattr(inv, 'arguments', []) or []
                if not args:
                    continue
                if isinstance(args[0], javalang.tree.Literal):
                    reg_name = args[0].value.strip('"').strip("'")
                    if not re.match(r'^[a-z0-9_]+$', reg_name):
                        continue
                    for arg in args[1:]:
                        if isinstance(arg, javalang.tree.MethodReference):
                            cls_ref = getattr(arg.expression, 'member', None) or getattr(arg.expression, 'name', None)
                            if cls_ref and cls_ref not in ('super', 'this') and len(cls_ref) > 2:
                                registry[cls_ref] = f"{namespace}:{reg_name}"
            cls_name = ast.primary_class_name()
            if cls_name:
                for inv in ast.invocations_of('setRegistryName'):
                    raw = JavaAST.first_string_arg(inv)
                    if raw:
                        registry[cls_name] = raw if ':' in raw else f"{namespace}:{raw}"
                        break
        for m in re.finditer(
            r'RegistryObject<EntityType<([A-Za-z0-9_]+)>>\s+\w+\s*=\s*\w+\.register\s*\(\s*["\']([a-z0-9_]+)["\']',
            code):
            registry[m.group(1)] = f"{namespace}:{m.group(2)}"
        for m in re.finditer(
            r'(?:DeferredHolder|DeferredEntity|Supplier)<[^>]*EntityType<([A-Za-z0-9_]+)>[^>]*>\s+\w+\s*=\s*\w+\.register\s*\(\s*["\']([a-z0-9_]+)["\']',
            code):
            registry[m.group(1)] = f"{namespace}:{m.group(2)}"
        for m in re.finditer(
            r'\.register\s*\(\s*["\']([a-z0-9_]+)["\']\s*,[^;]*?([A-Za-z0-9_]+)::new',
            code, re.DOTALL):
            cls = m.group(2)
            if cls not in ("super", "this") and len(cls) > 2:
                registry[cls] = f"{namespace}:{m.group(1)}"
        for m in re.finditer(
            r'EntityType\.Builder[^;]*\.of\s*\(\s*([A-Za-z0-9_]+)::new[^;]*\.build\s*\(\s*["\']([a-z0-9_]+)["\']',
            code, re.DOTALL):
            registry[m.group(1)] = f"{namespace}:{m.group(2)}"
        cls_name = extract_class_name(code)
        if cls_name:
            m = re.search(r'setRegistryName\s*\(\s*["\']([a-z0-9_:-]+)["\']', code)
            if m:
                raw = m.group(1)
                registry[cls_name] = raw if ":" in raw else f"{namespace}:{raw}"
    rl_constants = _build_resource_location_constants(java_files)
    if rl_constants:
        for _path2, code2 in java_files.items():
            for m in re.finditer(
                r'\.register\s*\(\s*([A-Z_][A-Z0-9_]{2,})\s*,'
                r'\s*(?:([A-Za-z0-9_]+)::new|\(\)\s*->\s*new\s+([A-Za-z0-9_]+)\s*\()',
                code2
            ):
                const_name = m.group(1)
                cls = m.group(2) or m.group(3)
                if const_name in rl_constants and cls and cls not in ('super', 'this', 'EntityType'):
                    rl = rl_constants[const_name]
                    if ':' in rl and cls not in registry:
                        registry[cls] = rl
            for m in re.finditer(
                r'EntityType\.Builder[^;]*\.of\s*\(\s*([A-Za-z0-9_]+)::new[^;]*\.build\s*\(\s*([A-Z_][A-Z0-9_]{2,})\s*\)',
                code2, re.DOTALL
            ):
                cls = m.group(1)
                const_name = m.group(2)
                if const_name in rl_constants and cls and cls not in registry:
                    rl = rl_constants[const_name]
                    if ':' in rl:
                        registry[cls] = rl
            for m in re.finditer(
                r'\.register\s*\(\s*["\']([a-z0-9_]+)["\']\s*,[^;]*?\.build\s*\(\s*([A-Z_][A-Z0-9_]{2,})\s*\)',
                code2, re.DOTALL
            ):
                reg_name = m.group(1)
                const_name = m.group(2)
                if const_name in rl_constants:
                    rl = rl_constants[const_name]
                    ns_part = rl.split(':')[0] if ':' in rl else namespace
                    nearby = code2[max(0, m.start()-300):m.end()]
                    cm = re.search(r'([A-Za-z0-9_]+)::new', nearby)
                    if cm:
                        cls = cm.group(1)
                        if cls not in ('super', 'this', 'EntityType') and cls not in registry:
                            registry[cls] = f"{ns_part}:{reg_name}"
    return registry
def build_attributes_registry(java_files: dict) -> dict:
    attrs_reg = {}
    defaults = {"health":20.0,"attack_damage":3.0,"movement_speed":0.3,
                "follow_range":16.0,"knockback_resistance":0.0,"armor":0.0}
    for path, code in java_files.items():
        if not re.search(
            r'(?:createAttributes|getDefaultAttributes|createMobAttributes'
            r'|createMonsterAttributes|createAnimalAttributes|createLivingAttributes)',
            code
        ):
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
        if not (any(k in fname for k in ("sound","sounds","sfx","audio")) or
                ("SoundEvent" in code and "register" in code)):
            continue
        for m in re.finditer(
            r'(?:RegistryObject<SoundEvent>|SoundEvent)\s+([A-Z_0-9]+)\s*=\s*\w+\.register\s*\(\s*["\']([a-z0-9_.]+)["\']',
            code):
            sound_map[m.group(1)] = sanitize_sound_key(f"{namespace}.{m.group(2)}")
        for m in re.finditer(
            r'(?:DeferredHolder<SoundEvent[^>]*>|Supplier<SoundEvent>)\s+([A-Z_0-9]+)\s*=\s*\w+\.register\s*\(\s*["\']([a-z0-9_.]+)["\']',
            code):
            sound_map[m.group(1)] = sanitize_sound_key(f"{namespace}.{m.group(2)}")
        for m in re.finditer(
            r'([A-Z_0-9]{3,})\s*=\s*(?:SoundEvent\.create[^(]*|Registry\.register[^(]*)\([^)]*["\']([a-z0-9_.:-]+)["\']',
            code):
            sid = m.group(2)
            if ":" in sid: sid = sid.split(":",1)[1]
            sound_map[m.group(1)] = sanitize_sound_key(f"{namespace}.{sid}")
    return sound_map
