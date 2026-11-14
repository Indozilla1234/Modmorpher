from __future__ import annotations

import json
import os
import zipfile
from typing import Optional, Tuple, Dict, Set, List
from mm_core import (
    BP_FOLDER,
    BP_RP_FORMAT_VERSION,
    DEBUG_MODE,
    JAVA_GOAL_PRIORITIES,
    RP_ENTITY_FORMAT_VERSION,
    RP_FOLDER,
    RP_LEGACY_ANIM_FORMAT,
    RP_LEGACY_RENDER_FORMAT,
    STATE,
    _ENTITY_SOUND_EVENTS,
    _REAL_PRINT,
    _safe_rp_write,
    _should_skip_entity_artifact,
    _warn,
    clean_java_artifact_name,
    sanitize_filename_keep_ext,
    sanitize_identifier,
)
from mm_java import (
    JAVA_PARTICLE_MAP,
    JavaAST,
    JavaSymbolTable,
    JavaUnit,
    JsTranslator,
    _EFFECT_NAME_MAP,
    _FORGE_EVENT_MAP,
    _SOUND_NAME_MAP,
    _extract_method_body,
    _int_arg_text,
    _jtok,
    _name_value_pairs,
    _parse_java_float,
    _translate_handler_body,
    dotted,
    expr_text,
    extract_class_name,
    find_model_geometry_in_code,
    first_created_type,
    get_unit,
    holder_members,
    identifier_set,
    iter_call_sites,
    iter_calls,
    iter_nodes,
    jt,
    method_ref_target,
    number_value,
    parse_statements,
    raw_number_text,
    read_all_java_files,
    ref_name,
    simple_type_name,
    split_alnum,
    split_on,
    string_value,
    suffix_ci,
    text_words,
    type_args,
    type_name,
)
from mm_assets import (
    BLOCK_SOUND_PROFILES,
    ITEM_SOUND_PROFILES,
    _ASSET_NOISE,
    _asset_score,
    _camel_tokens,
    canonicalize_animation_id,
    convert_layerdefinition_to_geckolib,
    resolve_texture_reference,
    rp_texture_exists,
    safe_write_json,
    sanitize_sound_key,
    texture_ref_to_rp_path,
)

def build_renderer_entity_map():
    STATE.renderer_map = {}
    renderer_to_entity: Dict[str, str] = {}
    model_to_entity: Dict[str, str] = {}
    entity_to_renderer: Dict[str, str] = {}
    cls_to_code: Dict[str, str] = {}
    cls_to_path: Dict[str, str] = {}
    renderer_to_model: Dict[str, str] = {}

    def register(etype_expr: Optional[str], renderer_cls: Optional[str]):
        if etype_expr and renderer_cls:
            etype_simple = etype_expr.split(".")[-1]
            entity_to_renderer[etype_simple] = renderer_cls
            entity_to_renderer[etype_simple.lower()] = renderer_cls

    for path, code in STATE.all_java_files.items():
        cls = extract_class_name(code)
        if not cls:
            continue
        cls_to_code[cls] = code
        cls_to_path[cls] = path
        unit = get_unit(code)
        found_renderer = found_model = False
        for decl in unit.classes():
            if decl.extends is None:
                continue
            parent = simple_type_name(decl.extends)
            args = type_args(decl.extends)
            if not args:
                continue
            if not found_renderer and ('Renderer' in parent or 'Render' in parent):
                renderer_to_entity[decl.name] = args[0]
                found_renderer = True
            if not found_model and ('Model' in parent or 'GeoModel' in parent):
                model_to_entity[decl.name] = args[0]
                found_model = True
        for call in unit.walk(jt.MethodInvocation):
            args = call.arguments or []
            if call.member == 'register' and call.qualifier == 'EntityRenderers' and len(args) >= 2:
                register(ref_name(args[0]), method_ref_target(args[1]) if isinstance(args[1], jt.MethodReference) else None)
            elif call.member == 'registerEntityRenderingHandler' and len(args) >= 2:
                register(ref_name(args[0]), method_ref_target(args[1]) if isinstance(args[1], jt.LambdaExpression) else None)
            elif call.member == 'bindEntityRenderer' and len(args) >= 2 \
                    and isinstance(args[0], jt.ClassReference) and isinstance(args[1], jt.ClassReference):
                entity_to_renderer[type_name(args[0].type)] = type_name(args[1].type)

    for renderer_cls, rcode in {c: cls_to_code[c] for c in renderer_to_entity if c in cls_to_code}.items():
        unit = get_unit(rcode)
        for sup in unit.walk(jt.SuperConstructorInvocation):
            created = first_created_type(list(sup.arguments or []))
            if created:
                renderer_to_model[renderer_cls] = created
                break
        for asg in unit.walk(jt.Assignment):
            lhs = asg.expressionl
            if isinstance(lhs, jt.This) and lhs.selectors and isinstance(lhs.selectors[0], jt.MemberReference) \
                    and lhs.selectors[0].member == 'model' and isinstance(asg.value, jt.ClassCreator):
                renderer_to_model[renderer_cls] = simple_type_name(asg.value.type)
    def _put(entity_cls: str, renderer_cls: Optional[str], model_cls: Optional[str]):
        if not entity_cls:
            return
        entry = STATE.renderer_map.setdefault(entity_cls, {})
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

def _geo_entity_arg(cls_decl) -> Optional[str]:

    if cls_decl.extends is not None and simple_type_name(cls_decl.extends) == 'GeoEntityRenderer':
        args = type_args(cls_decl.extends)
        if args:
            return args[0]
    return None

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
        for cls_decl in ast.get_class_declarations():
            ent = _geo_entity_arg(cls_decl)
            if ent:
                renderer_entity[cls] = ent
        for ctype in ast.all_object_creation_types():
            if ctype in class_code_map and ('Model' in ctype or ctype in model_map):
                renderer_model[cls] = ctype
                break
        if cls not in renderer_model:

            unit = get_unit(code)
            for decl in list(unit.walk(jt.LocalVariableDeclaration)) + list(unit.walk(jt.FieldDeclaration)):
                tname = simple_type_name(decl.type)
                if tname.endswith('Model') and tname[:1].isupper() and any(
                        isinstance(d.initializer, jt.ClassCreator)
                        and simple_type_name(d.initializer.type).endswith('Model') for d in decl.declarators):
                    renderer_model[cls] = tname
                    break
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
            found_model = next((c for c in JavaAST(code).all_object_creation_types() if c in model_map), None)
            if found_model:
                renderer_model[renderer_cls] = found_model
        if renderer_cls in renderer_model and renderer_cls not in renderer_entity:
            for cls_decl in JavaAST(code).get_class_declarations():
                ent = _geo_entity_arg(cls_decl)
                if ent:
                    renderer_entity[renderer_cls] = ent
    for renderer_cls, model_cls in renderer_model.items():
        ent = renderer_entity.get(renderer_cls)
        geom = model_map.get(model_cls)
        if ent and geom:
            entity_to_geometry[ent] = geom
            entity_to_model[ent] = model_cls
    if STATE.layerdef_geo_map:
        for renderer_cls, model_cls in renderer_model.items():
            if model_cls in STATE.layerdef_geo_map:
                ent = renderer_entity.get(renderer_cls)
                if ent and ent not in entity_to_geometry:
                    geo_id = STATE.layerdef_geo_map[model_cls]
                    parts = geo_id.split('.')
                    ns_h  = parts[1] if len(parts) >= 3 else None
                    nm_h  = '.'.join(parts[2:]) if len(parts) >= 3 else geo_id
                    entity_to_geometry[ent] = (ns_h, nm_h)
                    entity_to_model[ent]    = model_cls
        for model_cls, geo_id in STATE.layerdef_geo_map.items():
            for ent_cls in renderer_entity.values():
                if ent_cls not in entity_to_geometry:
                    mc_stem = suffix_ci(model_cls, 'Model').lower()
                    en_stem = suffix_ci(ent_cls, 'Entity').lower()
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

_ATTR_METHOD_PRIORITY = [
    ('createAttributes',        ('attributesupplier', 'attributemodifiermap', 'attributemap', 'builder')),
    ('getDefaultAttributes',    ('attributesupplier', 'attributemodifiermap', 'attributemap', 'builder')),
    ('createMobAttributes',     ('attributesupplier', 'attributemodifiermap', 'builder')),
    ('createMonsterAttributes', ('attributesupplier', 'attributemodifiermap', 'builder')),
    ('createAnimalAttributes',  ('attributesupplier', 'attributemodifiermap', 'builder')),
]

def _extract_attr_block(java_code: str) -> list:

    unit = get_unit(java_code)
    methods = [m for m in unit.methods() if not m.parameters]
    for name, return_types in _ATTR_METHOD_PRIORITY:
        for m in methods:
            rt = type_name(m.return_type).lower()
            if m.name.lower() == name.lower() and any(rt.startswith(r) for r in return_types):
                return list(m.body or [])
    for m in methods:
        rt = type_name(m.return_type).lower()
        if 'static' in (m.modifiers or ()) and 'builder' in rt and 'attributes' in m.name.lower():
            return list(m.body or [])
    return [unit.tree] if unit.tree is not None else []

def _signed_number(node) -> Optional[float]:

    if isinstance(node, jt.Literal) and not (node.postfix_operators or []):
        ops = node.prefix_operators or []
        if all(o in ('+', '-') for o in ops):
            return number_value(node)
    return None

def _camel_to_upper_snake(name: str) -> str:
    out = []
    for i, ch in enumerate(name):
        if i and ch.isupper() and name[i - 1].islower():
            out.append('_')
        out.append(ch)
    return ''.join(out).upper()
def extract_attributes_from_java(java_code: str) -> dict:
    results: Dict[str, float] = {}
    block = _extract_attr_block(java_code)
    adds = [c for c in iter_calls(block) if c.member == 'add' and len(c.arguments or []) == 2]

    for c in adds:
        a0, a1 = c.arguments
        val = _signed_number(a1)
        if isinstance(a0, jt.MemberReference) and a0.qualifier and not a0.selectors and a0.member[:1].isupper() \
                or (isinstance(a0, jt.MemberReference) and a0.qualifier and a0.member[:1] == '_'):
            bedrock_key = _JAVA_ATTR_NAME_MAP.get(a0.member)
            if bedrock_key and val is not None and bedrock_key not in results:
                results[bedrock_key] = val
    for c in adds:
        a0, a1 = c.arguments
        raw = string_value(a0)
        val = _signed_number(a1)
        if raw is None or val is None or not raw or not all(ch.isalpha() or ch in '_.' for ch in raw):
            continue
        raw_name = raw.split(".")[-1].split(":")[-1]
        bedrock_key = _JAVA_ATTR_NAME_MAP.get(raw_name) or _JAVA_ATTR_NAME_MAP.get(_camel_to_upper_snake(raw_name))
        if bedrock_key and bedrock_key not in results:
            results[bedrock_key] = val
    if not results:
        POSITIONAL_ORDER = [
            "movement_speed", "follow_range", "health",
            "knockback_resistance", "armor", "attack_damage"
        ]
        values = []
        for n in iter_nodes(block, jt.MethodInvocation, jt.ClassCreator):
            for a in (n.arguments or [])[1:]:
                v = _signed_number(a)
                if v is not None:
                    values.append(v)
        for i, val in enumerate(values[:len(POSITIONAL_ORDER)]):
            results[POSITIONAL_ORDER[i]] = val

    results = _normalize_entity_attributes(results, block)
    return results

def _normalize_entity_attributes(results: Dict[str, float], block=None) -> Dict[str, float]:

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
        health_attrs = {'MAX_HEALTH', 'GENERIC_MAX_HEALTH', 'HEALTH'}
        for c in iter_calls(block or []):
            val = None
            if c.member.lower() in ('sethealth', 'setmaxhealth', 'setcurrentvalue') and c.arguments:
                val = _signed_number(c.arguments[0])
            elif c.member.lower() == 'getattribute' and c.arguments and isinstance(c.arguments[0], jt.MemberReference) \
                    and c.arguments[0].qualifier == 'Attributes' and c.arguments[0].member in health_attrs:
                for sel in c.selectors or []:
                    if isinstance(sel, jt.MethodInvocation) and sel.member == 'setBaseValue' and sel.arguments:
                        val = _signed_number(sel.arguments[0])
                        break
            if val is not None and val > 0:
                out["health"] = val
                break

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
            stem = suffix_ci(s.split("/")[-1], '.json').lower()
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
    for node in get_unit(java_code).fields():
        for decl in node.declarators:
            if decl.name.upper().startswith('ANIM'):
                val = string_value(decl.initializer) if decl.initializer is not None else None
                if val is not None:
                    _add(val, trusted=False)
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
def _strip_generics(name: str) -> str:
    return JavaAST.strip_generics(name)
def build_goal_inheritance_map(java_files: Dict[str, str]) -> None:
    raw: Dict[str, str] = {}
    entity_src: Dict[str, str] = {}
    for _path, code in java_files.items():
        ast = JavaAST(code)
        for cls_decl in ast.get_class_declarations():
            entity_src[cls_decl.name] = code
        for child, parent in ast.all_class_extends():
            child  = JavaAST.strip_generics(child)
            parent = JavaAST.strip_generics(parent)
            if (child.endswith("Goal") or parent.endswith("Goal")
                    or parent in VANILLA_GOALS or child in VANILLA_GOALS
                    or parent in GOAL_NAME_ALIASES or child in GOAL_NAME_ALIASES):
                raw[child] = GOAL_NAME_ALIASES.get(parent, parent)
    STATE.goal_parent_map = raw
    STATE.entity_source_map = entity_src
    STATE.goal_map_built = True

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
    parent = STATE.goal_parent_map.get(custom_class)
    if not parent:
        return None
    if parent in VANILLA_GOALS:

        return parent

    return resolve_custom_goal(parent, visited)
def _find_class_source(class_name: str, java_files: Dict[str, str]) -> Optional[str]:
    src = STATE.entity_source_map.get(class_name)
    if src:
        return src
    for _path, code in java_files.items():
        if any(d.name == class_name for d in JavaAST(code).get_class_declarations()):
            return code
    return None

def _calls_super_register_goals(code: str) -> bool:
    return any(inv.member == 'registerGoals' and (inv.qualifier or '') == 'super'
               for inv in JavaAST(code).invocations_of('registerGoals'))

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
    src = _find_class_source(entity_class, java_files)
    if not src:
        return []
    parent_entity = JavaAST(src).superclass_name(entity_class)
    if parent_entity:
        parent_entity = JavaAST.strip_generics(parent_entity)
    if not parent_entity or parent_entity in BASE_ENTITY_CLASSES:
        return []
    parent_src = _find_class_source(parent_entity, java_files)
    if not parent_src:
        return []
    inherited: List[str] = []
    inherited.extend(extract_ai_goals_from_java(parent_src))
    if _calls_super_register_goals(parent_src):
        inherited.extend(_collect_super_goals(parent_entity, java_files, visited))
    return inherited
def extract_ai_goals_from_java(java_code: str,
                                extra_java_files: Optional[Dict[str, str]] = None):
    if not STATE.goal_map_built:
        build_goal_inheritance_map(extra_java_files or {"<inline>": java_code})
    java_files_ref = extra_java_files or {}
    ai_goals: List[str] = []
    def _add(goal: str):
        if goal and goal not in ai_goals:
            ai_goals.append(goal)
    ast = JavaAST(java_code)
    all_new_types = [JavaAST.strip_generics(t) for t in ast.all_object_creation_types()]
    for ctype in all_new_types:
        if ctype in VANILLA_GOALS:
            _add(ctype)
        elif ctype in GOAL_NAME_ALIASES:
            _add(GOAL_NAME_ALIASES[ctype])
    for inv in ast.invocations_of('addGoal'):
        args = inv.arguments or []
        if len(args) >= 2 and isinstance(args[1], jt.ClassCreator):
            cls_name = simple_type_name(args[1].type)
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
                if custom_cls not in STATE.goal_parent_map:
                    STATE.goal_parent_map[custom_cls] = local_parent
        resolved = resolve_custom_goal(custom_cls)
        if resolved:
            _add(resolved)

    if _calls_super_register_goals(java_code):
        entity_cls = ast.primary_class_name()
        if entity_cls:
            for g in _collect_super_goals(entity_cls, java_files_ref):
                _add(g)

    LEGACY_EXTEND_MAP = {
        "MeleeAttackGoal", "RangedAttackGoal",
        "NearestAttackableTargetGoal", "HurtByTargetGoal",
        "AvoidEntityGoal", "PanicGoal", "FollowOwnerGoal",
    }
    created = set(all_new_types)
    for base in LEGACY_EXTEND_MAP:
        for child, parent in ast.all_class_extends():
            if parent == base and base not in ai_goals and child in created:
                _add(base)
    return ai_goals
def _member_ref_set(unit: JavaUnit) -> Set[str]:

    out: Set[str] = set()
    for ref in unit.walk(jt.MemberReference):
        if ref.qualifier:
            out.add((ref.qualifier.split('.')[-1] + '.' + ref.member).lower())
    return out

def _returns_literal(unit: JavaUnit, method_name: str, value: str) -> bool:

    for m in unit.methods(method_name):
        for ret in iter_nodes(m.body, jt.ReturnStatement):
            if isinstance(ret.expression, jt.Literal) and ret.expression.value == value:
                return True
    return False

def extract_damage_immunities_from_java(java_code: str):

    immunities = set()
    unit = get_unit(java_code)
    projectile_types = {
        "AbstractArrow", "Arrow", "SpectralArrow", "Trident", "ThrownTrident",
        "ShulkerBullet", "FireworkRocketEntity", "ThrownPotion",
        "ThrownSplashPotion", "WindCharge", "SmallFireball", "LargeFireball",
    }
    inst = JavaAST(java_code).instanceof_types()
    if inst & projectile_types:
        immunities.add("projectile")
    if inst & {"Player", "ServerPlayer", "EntityPlayer"}:
        immunities.add("player")

    refs = _member_ref_set(unit)
    strings = {s.lower() for s in JavaAST(java_code).all_string_literals()}
    called = {c.member for c in unit.walk(jt.MethodInvocation)}
    idents = {t.value.lower() for t in unit.tokens if isinstance(t, _jtok.Identifier)}

    def has_ref(qualifier: str, names) -> bool:
        q = qualifier.lower()
        return any(f'{q}.{name.lower()}' in refs for name in names)

    fire_immune_assigned = any(
        (isinstance(a.expressionl, jt.MemberReference) and a.expressionl.member.lower() == 'fireimmune')
        or (isinstance(a.expressionl, jt.This) and a.expressionl.selectors
            and getattr(a.expressionl.selectors[0], 'member', '').lower() == 'fireimmune')
        for a in unit.walk(jt.Assignment)
        if isinstance(a.value, jt.Literal) and a.value.value == 'true')

    fire_sources = ('ON_FIRE', 'IN_FIRE', 'HOT_FLOOR', 'LAVA', 'CAMPFIRE')
    lightning_sources = ('LIGHTNING_BOLT', 'LIGHTNING')
    drown_sources = ('DROWN', 'DROWN_ING')
    fall_sources = ('FALL', 'STALAGMITE', 'FALLING_BLOCK')
    explosion_sources = ('EXPLOSION', 'GENERIC_KILL', 'CRAMMING', 'BAD_RESPAWN_POINT')
    magic_sources = ('MAGIC', 'WITHER', 'DRAGON_BREATH')

    if ('fireImmune' in called or fire_immune_assigned or _returns_literal(unit, 'isFireImmune', 'true')
            or has_ref('DamageSource', fire_sources)
            or has_ref('DamageTypes', ('ON_FIRE', 'IN_FIRE', 'LAVA', 'HOT_FLOOR'))
            or 'fire' in strings or 'isOnFire' in called):
        immunities.add("fire")
    if has_ref('DamageSource', lightning_sources):
        immunities.add("lightning")
    if (_returns_literal(unit, 'canBreatheUnderwater', 'true')
            or has_ref('DamageSource', drown_sources)
            or has_ref('DamageTypes', ('DROWN',)) or 'drown' in strings):
        immunities.add("drown")
    if (_returns_literal(unit, 'causeFallDamage', 'false')
            or has_ref('DamageSource', fall_sources)
            or has_ref('DamageTypes', ('FALL',)) or 'fall' in strings):
        immunities.add("fall")
    if has_ref('DamageSource', ('ANVIL',)):
        immunities.add("anvil")
    if has_ref('DamageSource', ('CACTUS', 'SWEET_BERRY_BUSH')):
        immunities.add("contact")
    if ('isExplosion' in called or has_ref('DamageSource', explosion_sources)
            or has_ref('DamageTypes', ('EXPLOSION',)) or 'explosion' in strings):
        immunities.add("explosion")
    if (has_ref('DamageSource', magic_sources)
            or has_ref('DamageTypes', ('MAGIC', 'WITHER', 'DRAGON_BREATH'))
            or 'isMagic' in called or 'magic' in strings):
        immunities.add("magic")
    if ('wither' in strings or 'witherskull' in strings
            or any(any(k in i for k in ('witherskull', 'witherboss', 'wither_skull')) for i in idents)):
        immunities.add("wither")
    if 'trident' in strings:
        immunities.add("projectile")
    if _returns_literal(unit, 'isInvulnerableTo', 'true'):
        immunities.add("all")
    return sorted(immunities)

def detect_dynamic_bounding_procedure(java_code: str) -> Optional[str]:
    unit = get_unit(java_code)
    for t in unit.tokens:
        if isinstance(t, _jtok.Identifier) and t.value.endswith('BoundingBoxScaleProcedure') \
                and len(t.value) > len('BoundingBoxScaleProcedure'):
            return t.value
    for call in unit.walk(jt.MethodInvocation):
        if call.member == 'execute' and call.qualifier:
            last = call.qualifier.split('.')[-1]
            if last.endswith('Procedure') and len(last) > len('Procedure'):
                return last
    return None
def detect_despawn_ticks(java_code: str) -> Optional[int]:
    unit = get_unit(java_code)

    def small_int(node, lo=0, hi=99999) -> Optional[int]:
        t = _int_arg_text(node)
        return int(t) if t is not None and len(t) <= 5 and lo <= int(t) <= hi else None

    ifs = list(unit.walk(jt.IfStatement))
    for stmt in ifs:
        for op in iter_nodes(stmt.condition, jt.BinaryOperation):
            n = small_int(op.operandr) if op.operator == '==' else None
            if n is not None and any(c.member == 'remove' for c in iter_calls(stmt.then_statement)):
                return n
    counters = ('tickcount', 'age', 'lifeticks', 'lifetick')
    for stmt in ifs:
        then = stmt.then_statement
        if not isinstance(then, jt.StatementExpression):
            continue
        if not any(c.member in ('discard', 'remove', 'kill') for c in iter_calls(then)):
            continue
        for op in iter_nodes(stmt.condition, jt.BinaryOperation):
            left = op.operandl
            lname = left.member.lower() if isinstance(left, jt.MemberReference) else (
                left.selectors[0].member.lower() if isinstance(left, jt.This) and left.selectors
                and isinstance(left.selectors[0], jt.MemberReference) else '')
            n = small_int(op.operandr)
            if op.operator in ('>', '>=') and lname in counters and n is not None and 1 <= n <= 24000:
                return n
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
    if DEBUG_MODE:
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
    low_path = (texture_path or "").lower()
    if (
        "/animations/" in low_path
        or low_path.endswith(".animation.json")
        or low_path.endswith(".animation")
        or "/animation_controllers/" in low_path
        or "/geometry/" in low_path
        or low_path.endswith(".geo.json")
        or "/render_controllers/" in low_path
    ):
        _warn(
            f"[RP] Rejected non-texture path as entity texture for {entity_basename_clean}: "
            f"{texture_path!r}; falling back to entity/{entity_basename_clean}"
        )
        texture_path = f"entity/{entity_basename_clean}"
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
    if DEBUG_MODE:
        _REAL_PRINT(f"[DEBUG] write_rp_entity_json -> {out_path}")
    _safe_rp_write("RP entity", out_path, client_entity)

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
    'baseTick', 'aiStep', 'tickDeath', 'finalizeSpawn',
    'getDimensions', 'getMobType', 'removeWhenFarAway',
}
def _first_extends_parent(ast: JavaAST) -> Optional[str]:
    for _child, parent in ast.all_class_extends():
        return JavaAST.strip_generics(parent)
    return None

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
    methods = ast.method_names()
    for child, parent in ast.all_class_extends():
        parent_clean = JavaAST.strip_generics(parent)
        if parent_clean in _ENTITY_SUPERCLASSES:
            return True
        if any(parent_clean.endswith(sfx) for sfx in _SUPERCLASS_SUFFIXES):
            if methods & _ENTITY_METHOD_NAMES:
                return True
    if methods & _ENTITY_METHOD_NAMES:
        return True
    for ctype in ast.all_object_creation_types():
        ctype_clean = JavaAST.strip_generics(ctype)
        if ctype_clean in _ENTITY_SUPERCLASSES:
            return True
        if ctype_clean.endswith("Entity") or ctype_clean.endswith("Mob"):
            if methods & _ENTITY_METHOD_NAMES:
                return True
    if STATE.entity_source_map:
        parent_name = _first_extends_parent(ast)
        _visited: Set[str] = set()
        while parent_name and parent_name not in _visited and len(_visited) < 8:
            _visited.add(parent_name)
            if parent_name in _ENTITY_SUPERCLASSES:
                return True
            if parent_name in STATE.entity_source_map:
                _past = JavaAST(STATE.entity_source_map[parent_name])
                if _past.method_names() & _ENTITY_METHOD_NAMES:
                    return True
                parent_name = _first_extends_parent(_past)
            else:
                break
    return False

def _resource_location_args(node) -> Optional[list]:

    if isinstance(node, jt.ClassCreator) and simple_type_name(node.type) == 'ResourceLocation':
        vals = [string_value(a) for a in node.arguments or []]
        if vals and all(v is not None for v in vals):
            return vals
    return None

def extract_entity_texture_hint(java_code: str, entity_basename: Optional[str] = None) -> Optional[str]:
    unit = get_unit(java_code)

    def accept(candidate):
        return candidate if candidate and is_probable_texture(candidate, entity_basename) else None

    def rl_in_method(method_name: str, arity: int) -> Optional[str]:
        for m in unit.methods(method_name):
            for c in iter_nodes(m.body, jt.ClassCreator):
                vals = _resource_location_args(c)
                if vals and len(vals) == arity:
                    return ':'.join(vals)
        return None

    for arity in (2, 1):
        for mname in ('getTextureResource', 'getTextureLocation'):
            hit = accept(rl_in_method(mname, arity))
            if hit:
                return hit

    pairs = _name_value_pairs(unit)

    def field_rl(names, arity):
        for name, val in pairs:
            if any(name.upper().endswith(n) for n in names):
                vals = _resource_location_args(val)
                if vals and len(vals) == arity:
                    return ':'.join(vals)
        return None

    def field_str(names):
        for name, val in pairs:
            if any(name.upper().endswith(n) for n in names):
                s = string_value(val)
                if s is not None and len(s) >= 4:
                    return s
        return None

    for cand in (field_rl(('TEXTURE', 'TEXTURE_LOCATION', 'LAYER_0', 'TEXTURE_LOC', 'MODEL_LOCATION', 'SKIN'), 2),
                 field_rl(('TEXTURE', 'TEXTURE_LOCATION', 'LAYER_0', 'TEXTURE_LOC'), 1),
                 field_str(('TEXTURE', 'TEXTURE_PATH', 'TEXTURE_NAME'))):
        hit = accept(cand)
        if hit:
            return hit

    for call in unit.walk(jt.MethodInvocation):
        if call.member == 'setTexture' and call.arguments:
            s = string_value(call.arguments[0])
            if s:
                hit = accept(s)
                if hit:
                    return hit

    for call in unit.walk(jt.MethodInvocation):
        if call.member != 'define' or not call.arguments or len(call.arguments) < 2:
            continue
        a0 = call.arguments[0]
        is_texture_key = False
        if isinstance(a0, jt.MemberReference) and a0.member and 'TEXTURE' in a0.member.upper():
            is_texture_key = True
        elif isinstance(a0, jt.MemberReference) and (a0.qualifier or '').upper().endswith('TEXTURE'):
            is_texture_key = True
        if not is_texture_key:
            continue
        s = string_value(call.arguments[1])
        if s:
            hit = accept(s)
            if hit:
                return hit
            if s and len(s) >= 3 and all(ch.isalnum() or ch in '_-/' for ch in s):
                return s

    creations = [(c, _resource_location_args(c)) for c in unit.walk(jt.ClassCreator)]
    creations = [(c, v) for c, v in creations if v]
    ident_ok = lambda s: bool(s) and all(ch.islower() or ch.isdigit() or ch in '_:-' for ch in s.lower().replace('/', '')) \
        and all(ch.isalnum() or ch in '_:-' for ch in s)
    for _c, vals in creations:
        if len(vals) == 2 and ident_ok(vals[0]):
            hit = accept(f'{vals[0]}:{vals[1]}')
            if hit:
                return hit
    for _c, vals in creations:
        first = vals[0]
        if first and (first[0].isalnum() or first[0] in '_:/-'):
            hit = accept(first)
            if hit:
                return hit

    allowed = set('_:/-.')
    for name, val in pairs:
        if 'TEXTURE' in name:
            for lit in iter_nodes(val, jt.Literal):
                s = string_value(lit)
                if s and all(ch.isalnum() or ch in allowed for ch in s):
                    hit = accept(s)
                    if hit:
                        return hit
                    break
    for lit in JavaAST(java_code).all_string_literals():
        if 'textures/' in lit.lower() or '.png' in lit.lower():
            hit = accept(lit)
            if hit:
                return hit
    return None
def is_probable_texture(candidate: Optional[str], entity_basename: Optional[str] = None) -> bool:
    if not candidate:
        return False
    candidate = str(candidate)
    low = candidate.lower()
    if (
        "/animations/" in low
        or low.endswith(".animation.json")
        or low.endswith(".animation")
        or "/animation_controllers/" in low
        or low.endswith(".animation_controllers.json")
        or "/geometry/" in low
        or low.endswith(".geo.json")
        or ("/models/" in low and low.endswith(".json"))
        or "/render_controllers/" in low
    ):
        return False
    if "textures/" in low or low.endswith(".png"):
        return True
    if any(f'{w}{sep}' in low for w in ('blocks', 'items', 'entity', 'textures') for sep in ('/', ':')):
        return True
    name = candidate.split(":")[-1].replace(".png", "")
    probes = [f"entity/{name}", f"items/{name}", f"blocks/{name}", f"{name}"]
    for p in probes:
        if rp_texture_exists(p):
            return True
    sound_indicators = ["sound", "sounds", "whisper", "sfx", "ambient", "step", "attack", "wraith", "growl", "roar"]
    if any(k in low for k in sound_indicators) and not "/" in candidate:
        return False
    if ":" in candidate and "/" in candidate:
        return True
    if entity_basename and entity_basename.lower() in low:
        return True
    return False
def _find_related_code(cls_name: str) -> Optional[str]:
    target = cls_name.lower()
    for path, code in STATE.all_java_files.items():
        fname_stem = os.path.splitext(os.path.basename(path))[0].lower()
        if fname_stem == target:
            return code
        declared = extract_class_name(code)
        if declared and declared.lower() == target:
            return code
    return None
def _leading_class_ident(node) -> Optional[str]:

    name = None
    if isinstance(node, jt.ClassReference):
        name = type_name(node.type)
    elif isinstance(node, jt.MethodReference) and isinstance(node.expression, jt.MemberReference):
        name = node.expression.member
    elif isinstance(node, jt.MemberReference):
        name = (node.qualifier or node.member).split('.')[0]
    elif isinstance(node, jt.ClassCreator):
        name = simple_type_name(node.type)
    return name if name and name[:1].isupper() else None

def _referenced_class_names(code: str) -> List[str]:
    found: List[str] = []
    unit = get_unit(code)
    calls = list(unit.walk(jt.MethodInvocation))
    for c in calls:
        if c.member == 'register' and c.qualifier == 'EntityRenderers' and len(c.arguments or []) >= 2 \
                and isinstance(c.arguments[1], jt.MethodReference):
            n = _leading_class_ident(c.arguments[1])
            if n:
                found.append(n)
    for c in calls:
        if c.member in ('bindEntityRenderer', 'registerEntityRenderingHandler') and len(c.arguments or []) >= 2:
            n = _leading_class_ident(c.arguments[1])
            if n:
                found.append(n)
    for c in calls:
        if c.member == 'setModel':
            created = first_created_type(list(c.arguments or []))
            if created and created[:1].isupper():
                found.append(created)
    for a in unit.walk(jt.Assignment):
        lhs = a.expressionl
        if isinstance(lhs, jt.This) and lhs.selectors and getattr(lhs.selectors[0], 'member', None) == 'model':
            created = first_created_type(a.value)
            if created and created[:1].isupper():
                found.append(created)
    for decl in unit.classes():
        if decl.extends is not None and simple_type_name(decl.extends).endswith('Renderer'):
            args = type_args(decl.extends)
            if len(args) >= 2 and args[1][:1].isupper():
                found.append(args[1])
    for imp in unit.import_paths():
        if imp.endswith('.*'):
            continue
        seg = imp.split('.')[-1]
        for suffix in ('Renderer', 'Model', 'Layer'):
            if seg.endswith(suffix) and (seg == suffix or seg[:1].isupper()):
                found.append(seg)
                break
    for decl in unit.classes():
        if decl.extends is not None:
            parent = simple_type_name(decl.extends)
            args = type_args(decl.extends)
            if parent.startswith('Geo') and parent.endswith('Renderer') and len(args) == 1 and args[0][:1].isupper():
                found.append(args[0] + "Model")
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
        if not is_probable_texture(hint, entity_basename):
            return None
        candidate = hint.split(":")[-1].replace(".png", "").strip("/")
        if candidate.startswith("textures/"):
            candidate = candidate[len("textures/"):]
        low = candidate.lower()
        if "/animations/" in low or low.endswith(".animation.json") or low.endswith(".animation"):
            return None
        for probe in [candidate, f"entity/{candidate}", f"entity/{os.path.basename(candidate)}"]:
            if rp_texture_exists(probe):
                return f"{ns}:{probe}"
        if "/" in candidate:
            return f"{ns}:{candidate}"
        return f"{ns}:entity/{sanitize_identifier(candidate)}"
    def _geom_from_code(code: str) -> Optional[str]:
        result = find_model_geometry_in_code(code)
        if not result:
            return None
        ns_hint, geom_name = result
        return f"geometry.{sanitize_identifier(ns_hint or namespace)}.{sanitize_identifier(geom_name)}"
    def _best_texture_on_disk() -> Optional[str]:
        best_score, best_ref = 0.0, None
        for rel_no_ext, _ in STATE.rp_asset_index.get("textures", []):
            score = _asset_score(ent_toks, rel_no_ext)
            if rel_no_ext.startswith("entity/"):
                score += 0.15
            if score > best_score and score >= 0.30:
                best_score = score
                best_ref   = f"{ns}:{rel_no_ext}"
        return best_ref
    def _best_geometry_on_disk() -> Optional[str]:
        best_score, best_ident = 0.0, None
        for ident, _ in STATE.rp_asset_index.get("geometry", []):
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
            entry = STATE.renderer_map.get(lookup_key) or STATE.renderer_map.get(lookup_key + "Entity")
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
        for path, code in STATE.all_java_files.items():
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
    return bool(text_words(code or "") & {"procedure", "procudure"})

def _procedure_entity_tokens(entity_identifier: str,
                             entity_cls_name: Optional[str] = None,
                             entity_basename: Optional[str] = None) -> Set[str]:
    tokens: Set[str] = set()
    for name in (entity_identifier, entity_cls_name, entity_basename):
        if not name:
            continue
        clean = str(name).split(":")[-1]
        tokens.update(_camel_tokens(clean))
        tokens.update(t for t in split_alnum(clean.lower()) if len(t) > 1)
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
    hay_words = text_words(hay)
    code_words = text_words(code or "")

    tokens = _procedure_entity_tokens(entity_identifier, entity_cls_name, entity_basename)
    score = 0.0
    if "/procedures/" in norm_path or norm_path.endswith("/procedures"):
        score += 0.75
    if "procedure" in stem or "procudure" in stem:
        score += 0.4
    if entity_identifier and entity_identifier.lower() in hay:
        score += 2.0
    if entity_cls_name and entity_cls_name.lower() in code_words:
        score += 1.25
    if entity_basename and entity_basename.lower() in code_words:
        score += 1.25

    shared = 0
    for tok in sorted(tokens, key=len, reverse=True):
        if len(tok) >= 3 and tok.lower() in hay_words:
            shared += 1
    score += min(shared, 4) * 0.35

    if hay_words & {'execute', 'tick', 'update', 'spawn', 'hurt', 'attack', 'interact', 'entity'}:
        score += 0.25

    return score >= 1.2

def _collect_entity_procedure_sources(java_code: str, java_path: str,
                                      entity_identifier: str,
                                      entity_cls_name: Optional[str] = None,
                                      entity_basename: Optional[str] = None) -> List[Tuple[str, str]]:
    if not STATE.all_java_files:
        return []

    collected: List[Tuple[str, str]] = []
    for path, code in STATE.all_java_files.items():
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

_PROCEDURE_METHOD_NAMES = [
    "execute", "executeProcedure", "onExecute", "tick", "update",
    "run", "apply", "process", "handle", "onUpdate", "entityTick",
]

def _extract_procedure_methods(java_code: str) -> list:

    unit = get_unit(java_code)
    found = []
    for name in _PROCEDURE_METHOD_NAMES:
        for m in unit.methods(name):
            if m.body:
                found.append((name, m))
                break
    return found

def _procedure_prelude(method, translator) -> List[str]:

    lines: List[str] = []
    for prm in method.parameters or []:
        t, name = simple_type_name(prm.type), prm.name
        translator.declare(name, prm.type)
        if name == 'entity':
            continue
        if t in ('Level', 'LevelAccessor', 'ServerLevel', 'LevelReader', 'WorldGenLevel', 'BlockGetter'):
            lines.append(f'const {name} = entity.dimension;')
        elif name in ('x', 'y', 'z') and t in ('double', 'float', 'int', 'Double'):
            lines.append(f'const {name} = entity.location.{name};')
        else:
            lines.append(f'const {name} = undefined; // {t} {name}: not provided by the tick bridge')
    return lines

def _translate_procedure_method_to_js(method, namespace: str) -> List[str]:
    translator = JsTranslator(namespace, JavaSymbolTable(), 'entity')
    prelude = _procedure_prelude(method, translator)
    body = translator.stmts(method.body, 1)
    return [('    ' + l) for l in prelude] + body

def _infer_procedure_trigger(path: str, code: str) -> str:
    hay = " ".join([
        os.path.basename(path).lower(),
        os.path.splitext(os.path.basename(path))[0].lower(),
        code[:2500].lower() if code else "",
    ])
    words = text_words(hay)
    if words & {'spawn', 'join', 'load', 'init', 'onentityspawn'}:
        return "spawn"
    if words & {'hurt', 'damage', 'attack', 'interact', 'use', 'click', 'rightclick', 'leftclick'}:
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
        '// `world` is shadowed inside handlers by the entity\'s dimension, so keep a handle on the real world',
        'const BEDROCK_WORLD = world;',
        'const PROCEDURE_HANDLERS = [];',
        '',
    ]

    for idx, (src_path, src_code) in enumerate(related_sources):
        src_name = extract_class_name(src_code) or os.path.splitext(os.path.basename(src_path))[0]
        src_safe = sanitize_identifier(src_name) or f"procedure_{idx}"
        trigger = _infer_procedure_trigger(src_path, src_code)
        methods = _extract_procedure_methods(src_code)
        if not methods:
            script_lines.extend([
                f'// {os.path.basename(src_path)}: no executable procedure body detected',
                '',
            ])
            continue

        for method_name, method in methods:
            translated = _translate_procedure_method_to_js(method, namespace)
            script_lines.extend([
                f'PROCEDURE_HANDLERS.push({{',
                f'  entityId: "{entity_identifier}",',
                f'  trigger: "{trigger}",',
                f'  source: "{os.path.basename(src_path)}::{method_name}",',
                f'  run(entity) {{',
            ])
            for line in translated:
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

def _entity_dimensions(java_code: str) -> Optional[Tuple[float, float]]:

    unit = get_unit(java_code)

    def two_numbers(args):
        if len(args or []) >= 2:
            vals = [number_value(a) if isinstance(a, jt.Literal) and not a.prefix_operators else None for a in args[:2]]
            if all(v is not None for v in vals):
                return float(vals[0]), float(vals[1])
        return None
    for call in unit.walk(jt.MethodInvocation):
        if call.member in ('scalable', 'fixed') and call.qualifier == 'EntityDimensions':
            dims = two_numbers(call.arguments)
            if dims:
                return dims
    for m in unit.methods('getDimensions'):
        for n in iter_nodes(m.body, jt.MethodInvocation, jt.ClassCreator):
            if len(n.arguments or []) == 2:
                dims = two_numbers(n.arguments)
                if dims:
                    return dims
    return None

def convert_java_to_bedrock(java_path: str, entity_identifier: str, gecko_maps: dict, geom_file_map: dict, geom_ns_map: dict, anim_key_map: dict, stats: dict):
    if DEBUG_MODE:
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
    ai_goals = extract_ai_goals_from_java(java_code)
    animations = extract_animations_from_java(java_code, namespace, entity_name)
    attributes = extract_attributes_from_java(java_code)
    immunities = extract_damage_immunities_from_java(java_code)
    bounding_proc = detect_dynamic_bounding_procedure(java_code)
    despawn_ticks = detect_despawn_ticks(java_code)
    collision_w, collision_h = 0.6, 1.8
    if bounding_proc:
        collision_w, collision_h = 2.5, 3.0
    dims = _entity_dimensions(java_code)
    if dims:
        collision_w, collision_h = dims
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
    _parents = {JavaAST.strip_generics(p).lower() for _c, p in JavaAST(java_code).all_class_extends()}
    _idents = identifier_set(java_code)
    _ast_calls = JavaAST(java_code).invocations_of('setNoGravity')
    is_flyer = bool(
        _parents & {'flyingmob', 'ghast', 'phantom', 'bee', 'parrot', 'abstractflyingentity'} or
        'flyingmovecontrol' in _idents or
        any(c.arguments and isinstance(c.arguments[0], jt.Literal) and c.arguments[0].value == 'true' for c in _ast_calls)
    )
    is_swimmer = bool(
        _parents & {'wateranimal', 'squid', 'dolphin', 'tropicalfish', 'abstractfish'} or
        "RandomSwimmingGoal" in ai_goals or "FindWaterGoal" in ai_goals
    )
    is_climber = bool({'canclimb', 'onclimbable'} & _idents or any('spider' in i for i in _idents))
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
        ns_tokens = set(split_on(namespace.lower(), '_-')) - {'the', 'a', 'an', 'of', ''}
        ent_tokens = set(split_on(entity_basename.lower(), '_-')) - {'entity', 'mob', ''}
        best_key = None
        best_score = 0
        for gkey, gident in geom_file_map.items():
            geo_tokens = set(split_on(gkey.lower(), '_-'))
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
        if STATE.layerdef_geo_map:
            if entity_cls in STATE.layerdef_geo_map:
                geom_identifier = STATE.layerdef_geo_map[entity_cls]
            else:
                ent_stem = suffix_ci(entity_cls, 'Entity').lower()
                for model_cls, geo_id in STATE.layerdef_geo_map.items():
                    model_stem = suffix_ci(model_cls, 'Model').lower()
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
        _warn(f"write_render_controller failed for {entity_basename}: {e}")
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
        _warn(f"write_rp_entity_json failed for {entity_basename}: {e}")
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
        _warn(f"patch_rp_entity_with_controller failed for {entity_basename}: {e}")
    generate_spawn_rules(clean_identifier, java_code, namespace)
    extract_and_generate_particles(java_code, clean_identifier, namespace)
    if "TradeWithPlayerGoal" in ai_goals:
        generate_trading_table(clean_identifier, java_code, namespace)

    generate_entity_script(java_code, clean_identifier.split(":")[-1], clean_identifier, namespace)

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
    for node in get_unit(java_code).walk(jt.ClassCreator):
        args = node.arguments or []
        if simple_type_name(node.type) != 'MobEffectInstance' or len(args) < 2:
            continue
        eff = args[0]
        if not (isinstance(eff, jt.MemberReference) and eff.qualifier == 'MobEffects' and not eff.selectors):
            continue
        duration = _int_arg_text(args[1])
        if duration is None:
            continue
        amp = _int_arg_text(args[2]) if len(args) > 2 else None
        bedrock_name = JAVA_MOB_EFFECT_MAP.get(eff.member)
        if bedrock_name:
            effects.append({
                "effect": bedrock_name,
                "duration": int(duration) / 20.0,
                "amplifier": int(amp) if amp else 0,
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
_SOUND_HOLDERS = ('SoundEvents', 'ModSounds', 'Sounds')

def _is_sound_const(name: str) -> bool:
    return bool(name) and all(c.isupper() or c.isdigit() or c == '_' for c in name)

def _sound_consts_in(node) -> List[str]:

    found = []
    for n in iter_nodes(node, jt.MemberReference, jt.MethodInvocation):
        if isinstance(n, jt.MemberReference):
            if n.qualifier in _SOUND_HOLDERS and _is_sound_const(n.member):
                found.append(n.member)
        elif n.member == 'get' and n.qualifier:
            parts = n.qualifier.split('.')
            if len(parts) == 2 and parts[0] in _SOUND_HOLDERS and _is_sound_const(parts[1]):
                found.append(parts[1])
    return found

def _is_dotted_lower_id(s: str) -> bool:
    parts = s.split('.')
    return len(parts) >= 3 and all(p and p[0].islower() and p[0].isascii() and all(
        (c.isascii() and c.islower()) or c.isdigit() or c == '_' for c in p) for p in parts)

def extract_entity_sounds_from_java(java_code: str, entity_name: str, namespace: str) -> dict:
    sounds = {}
    unit = get_unit(java_code)
    for method, slot in JAVA_SOUND_METHOD_MAP.items():
        for m in unit.methods(method):
            consts = _sound_consts_in(m.body)
            if consts and slot not in sounds:
                java_const = consts[0]
                if JAVA_SOUND_EVENT_MAP.get(java_const):
                    sounds[slot] = f"{namespace}.{entity_name}.{slot}"
                else:
                    sounds[slot] = sanitize_sound_key(java_const.lower())
                break
    for method, slot in JAVA_SOUND_METHOD_MAP.items():
        if slot in sounds:
            continue
        for m in unit.methods(method):
            ret = next(iter(iter_nodes(m.body, jt.ReturnStatement)), None)
            ref = ref_name(ret.expression) if ret is not None and ret.expression is not None else None
            if ref:
                ref_lower = sanitize_sound_key(ref.split(".")[-1])
                if len(ref_lower) > 2 and ref_lower not in ("null", "super", "this"):
                    sounds[slot] = f"{namespace}.{ref_lower}"
                    break
    PLAY_SLOT_HINTS = {
        "ambient": ("ambient", "idle", "random"),
        "hurt":    ("hurt", "pain", "damage"),
        "death":   ("death", "die"),
        "attack":  ("attack", "strike", "hit"),
        "step":    ("step", "footstep", "walk"),
    }
    for call in unit.walk(jt.MethodInvocation):
        if call.member != 'playSound' or len(call.arguments or []) < 2:
            continue
        snd = call.arguments[1]
        if isinstance(snd, jt.MemberReference) and snd.qualifier in _SOUND_HOLDERS and _is_sound_const(snd.member):
            java_const = snd.member
            for slot, hints in PLAY_SLOT_HINTS.items():
                if slot in sounds:
                    continue
                if any(h in java_const.lower() for h in hints):
                    sounds[slot] = f"{namespace}.{entity_name}.{slot}"
                    break
    for creator in unit.walk(jt.ClassCreator):
        vals = _resource_location_args(creator)
        if not vals or len(vals) != 2:
            continue
        sound_path = vals[1].lower()
        for slot in ("ambient", "death", "hurt", "step", "attack", "swim", "splash"):
            if slot in sounds:
                continue
            if slot in sound_path:
                sounds[slot] = sanitize_sound_key(f"{vals[0]}.{sound_path}")
                break
    for path in JavaAST(java_code).all_string_literals():
        if not _is_dotted_lower_id(path):
            continue
        parts = path.split(".")
        for slot in ("ambient", "death", "hurt", "step", "attack", "swim", "splash"):
            if slot in sounds:
                continue
            if parts[-1] == slot or (len(parts) >= 2 and parts[-2] == slot):
                sounds[slot] = sanitize_sound_key(path)
                break
    return sounds
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
        if sound_key not in STATE.collected_sound_defs:
            file_stem = sound_key.replace(".", "_")
            file_path = f"sound/{file_stem}"
            category = _SLOT_CATEGORY.get(slot, "neutral")
            STATE.collected_sound_defs[sound_key] = {
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

def _sound_call_consts(unit: JavaUnit) -> List[str]:

    out = []
    for call in unit.walk(jt.MethodInvocation):
        if call.member.lower() in ('playsound', 'sound'):
            consts = _sound_consts_in(list(call.arguments or []))
            if consts:
                out.append(consts[0])
    return out

def _guess_block_sound_profile(java_code: str, namespace: str, block_id: str) -> dict:
    unit = get_unit(java_code or '')
    sounds = {}
    sound_type = None
    for ref in unit.walk(jt.MemberReference):
        if ref.qualifier == 'SoundType' and ref.member and all(c.isupper() or c == '_' for c in ref.member):
            sound_type = ref.member.lower()
            break
    if sound_type:
        base = sanitize_sound_key(sound_type)
        sounds = {
            'step': f'{namespace}.{base}.step',
            'place': f'{namespace}.{base}.place',
            'break': f'{namespace}.{base}.break',
            'hit': f'{namespace}.{base}.hit',
            'fall': f'{namespace}.{base}.fall',
        }
    for const in _sound_call_consts(unit):
        token = const.lower()
        key = sanitize_sound_key(f'{namespace}.{block_id.split(":")[-1]}.{token}')
        slot = 'place' if any(k in token for k in ('place', 'step', 'hit')) else 'break'
        sounds[slot] = key
    return sounds

def _guess_item_sound_profile(java_code: str, namespace: str, item_id: str) -> dict:
    unit = get_unit(java_code or '')
    sounds = {}
    for const in _sound_call_consts(unit):
        token = const.lower()
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
    if not sounds:
        idents = identifier_set(java_code or '')
        if idents & {'foodproperties', 'nutrition', 'saturationmod', 'food'}:
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
def _leading_ident(node) -> Optional[str]:

    if isinstance(node, jt.MemberReference):
        if node.qualifier == 'Items':
            return node.member
        return (node.qualifier or node.member).split('.')[0]
    if isinstance(node, jt.MethodInvocation):
        return (node.qualifier or node.member).split('.')[0]
    return None

def extract_equipment_from_java(java_code: str, namespace: str) -> Optional[dict]:
    equipment = {}
    for call in get_unit(java_code).walk(jt.MethodInvocation):
        args = call.arguments or []
        if call.member not in ('setItemSlot', 'setEquipment', 'set') or len(args) < 2:
            continue
        slot_ref, stack = args[0], args[1]
        if not (isinstance(slot_ref, jt.MemberReference) and slot_ref.qualifier == 'EquipmentSlot'
                and slot_ref.member.isalpha() and slot_ref.member.isupper()):
            continue
        if not (isinstance(stack, jt.ClassCreator) and simple_type_name(stack.type) == 'ItemStack' and stack.arguments):
            continue
        name = _leading_ident(stack.arguments[0])
        if not name or not all(c.isalpha() or c == '_' for c in name):
            continue
        slot = JAVA_SLOT_TO_BEDROCK.get(slot_ref.member)
        item = sanitize_identifier(name.lower())
        if slot:
            if not item.startswith("minecraft:"):
                item = f"minecraft:{item}"
            equipment[slot] = {"item": item, "drop_chance": 0.085}
    if not equipment:
        return None
    return {"table": f"loot_tables/equipment/{namespace}_equipment.json", "slot_drop_chance": list(equipment.values())}
def _applies_mob_effect(java_code: str) -> bool:
    for call in get_unit(java_code).walk(jt.MethodInvocation):
        if call.member.lower() == 'addeffect':
            return True
        if call.member == 'hurt' and any(
                r.qualifier == 'MobEffects' for r in iter_nodes(list(call.arguments or []), jt.MemberReference)):
            return True
    return False

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
        if _applies_mob_effect(java_code):
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
def _mentions(java_code: str, *needles: str) -> bool:

    texts = list(identifier_set(java_code)) + [s.lower() for s in JavaAST(java_code).all_string_literals()]
    return any(n in t for t in texts for n in needles)

def _biome_hints(unit: JavaUnit) -> List[str]:
    hints: List[str] = []
    toks = [t for t in unit.tokens]
    holders = ('BiomeDictionary', 'BiomeCategory', 'BIOME', 'BIOMES')
    for i, t in enumerate(toks):
        if not isinstance(t, _jtok.Identifier):
            continue
        is_holder = any(t.value.endswith(h) for h in holders)
        if t.value == 'Biomes' and i >= 2 and toks[i - 1].value == '.' and toks[i - 2].value == 'Tags':
            is_holder = True
        if is_holder and i + 2 < len(toks) and toks[i + 1].value == '.' and isinstance(toks[i + 2], _jtok.Identifier) \
                and all(c.isalpha() or c == '_' for c in toks[i + 2].value):
            hints.append(toks[i + 2].value)
    for ref in unit.walk(jt.MemberReference):
        if ref.qualifier == 'BiomeTags':
            name = ref.member[3:] if ref.member.startswith('IS_') else ref.member
            lead = ''
            for c in name:
                if c.isupper() or c == '_':
                    lead += c
                else:
                    break
            if lead:
                hints.append(lead)
    for node in unit.walk(jt.MethodInvocation, jt.ClassCreator):
        is_tag = (isinstance(node, jt.MethodInvocation) and node.qualifier == 'TagKey') or \
                 (isinstance(node, jt.ClassCreator) and simple_type_name(node.type) == 'TagKey')
        if is_tag:
            for lit in iter_nodes(list(node.arguments or []), jt.Literal):
                s = string_value(lit)
                if s and all(c.islower() or c in '_:' for c in s):
                    hints.append(s)
                    break
    return hints

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
    unit = get_unit(java_code)
    for b in _biome_hints(unit):
        bl = b.lower()
        for k, v in JAVA_BIOME_TO_BEDROCK.items():
            if k in bl or bl in k:
                if v not in data["biomes"]:
                    data["biomes"].append(v)
    if not data["biomes"]:
        if _mentions(java_code, 'nether'): data["biomes"] = ["nether"]
        elif _mentions(java_code, 'the_end'): data["biomes"] = ["the_end"]
        else: data["biomes"] = ["overworld"]
    refs = [(r.qualifier, r.member) for r in unit.walk(jt.MemberReference) if r.qualifier]
    if any(q in ('MobCategory', 'DimensionType') and m.startswith('NETHER') for q, m in refs):
        data["biomes"] = ["nether"]
    if any(q in ('MobCategory', 'DimensionType') and m.startswith('END') for q, m in refs):
        data["biomes"] = ["the_end"]

    spawn_entries = [n for n in unit.walk(jt.ClassCreator, jt.MethodInvocation)
                     if 'spawnentry' in (simple_type_name(n.type) if isinstance(n, jt.ClassCreator) else n.member).lower()]
    weight = None
    for n in spawn_entries:
        if n.arguments and _int_arg_text(n.arguments[0]):
            weight = int(_int_arg_text(n.arguments[0]))
            break
    if weight is None:
        for name, val in _name_value_pairs(unit):
            if name.lower().endswith('weight') and _int_arg_text(val):
                weight = int(_int_arg_text(val))
                break
    if weight is None:
        for c in unit.walk(jt.MethodInvocation):
            if c.member == 'weight' and len(c.arguments or []) == 1 and _int_arg_text(c.arguments[0]):
                weight = int(_int_arg_text(c.arguments[0]))
                break
    if weight is not None:
        data["weight"] = weight
    for n in spawn_entries:
        args = n.arguments or []
        if len(args) >= 3 and _int_arg_text(args[1]) and _int_arg_text(args[2]):
            data["min_count"] = int(_int_arg_text(args[1]))
            data["max_count"] = int(_int_arg_text(args[2]))
            break

    def light_name(node) -> str:
        return node.member.lower() if isinstance(node, jt.MemberReference) and not node.selectors else ''
    for op in unit.walk(jt.BinaryOperation):
        if op.operator in ('==', '<', '>', '<=', '>=') and light_name(op.operandl).endswith(('light', 'lightlevel')) \
                and _int_arg_text(op.operandr):
            data["max_light"] = int(_int_arg_text(op.operandr))
            break
    else:
        for a in unit.walk(jt.Assignment):
            if a.type == '=' and light_name(a.expressionl).endswith(('light', 'lightlevel')) and _int_arg_text(a.value):
                data["max_light"] = int(_int_arg_text(a.value))
                break
    if _mentions(java_code, 'water'):
        data["surface"] = False
    if _mentions(java_code, 'underground', 'cave'):
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

def _trade_item_refs(java_code: str) -> List[str]:
    refs = []
    for offer in get_unit(java_code).walk(jt.ClassCreator):
        if simple_type_name(offer.type) != 'MerchantOffer':
            continue
        stacks = [c for c in iter_nodes(list(offer.arguments or []), jt.ClassCreator)
                  if simple_type_name(c.type) == 'ItemStack' and c.arguments]
        if stacks:
            refs.append(ref_name(stacks[-1].arguments[0]) or expr_text(stacks[-1].arguments[0]))
    return refs

def generate_trading_table(entity_id: str, java_code: str, namespace: str):
    safe_name = sanitize_identifier(entity_id.split(":")[-1])
    out_path = os.path.join(BP_FOLDER, "trading", f"{safe_name}.json")
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    trade_items = _trade_item_refs(java_code)
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

def _plain_float(node) -> Optional[float]:

    txt = raw_number_text(node)
    if txt is None or txt[:1] in '+-':
        return None
    return _parse_java_float(txt.rstrip('f'))

def _translate_entity_body(body: str, namespace: str, safe_name: str) -> list:
    lines = []
    stmts = parse_statements(body) or []
    sites = list(iter_call_sites(stmts))

    for creator in iter_nodes(stmts, jt.ClassCreator):
        args = creator.arguments or []
        if simple_type_name(creator.type) == 'MobEffectInstance' and len(args) >= 2 \
                and isinstance(args[0], jt.MemberReference) and args[0].qualifier == 'MobEffects':
            dur = _int_arg_text(args[1])
            amp = _int_arg_text(args[2]) if len(args) > 2 else None
            if dur is None:
                continue
            effect_key = args[0].member
            bedrock_effect = _EFFECT_NAME_MAP.get(effect_key, effect_key.lower())
            lines.append(
                f'            entity.addEffect("minecraft:{bedrock_effect}", {int(dur)/20}, '
                f'{{ amplifier: {int(amp) if amp else 0}, showParticles: true }});'
            )

    for hit in holder_members(body, 'SoundEvents'):
        bedrock_sound = _SOUND_NAME_MAP.get(hit, "random.pop")
        lines.append(f'            entity.dimension.playSound("{bedrock_sound}", entity.location);')

    def first_site(members, pred=None):
        return next((s for s in sites if s[1] in members and (pred is None or pred(s))), None)

    site = first_site(('heal', 'setHealth'), lambda s: s[2] and _plain_float(s[2][0]) is not None)
    if site:
        amount = _plain_float(site[2][0])
        lines.append(f'            const health = entity.getComponent("minecraft:health");')
        lines.append(f'            if (health) health.setCurrentValue(Math.min(health.currentValue + {amount}, health.effectiveMax));')

    site = first_site(('hurt', 'damage'), lambda s: not s[0].endswith('player') and len(s[2]) >= 2 and _plain_float(s[2][1]) is not None)
    if site:
        lines.append(f'            entity.applyDamage({_plain_float(site[2][1])});')

    site = first_site(('setOnFire', 'setSecondsOnFire'), lambda s: s[2] and _int_arg_text(s[2][0]))
    if site:
        lines.append(f'            entity.setOnFire({int(_int_arg_text(site[2][0]))});')

    for _recv, member, args in sites:
        if member in ('addFreshEntity', 'summon', 'spawnEntity') and args and isinstance(args[0], jt.ClassCreator):
            eid = f"{namespace}:{sanitize_identifier(simple_type_name(args[0].type))}"
            lines.append(f'            entity.dimension.spawnEntity("{eid}", entity.location);')

    site = first_site(('explode', 'createExplosion'), lambda s: len(s[2]) >= 2 and _plain_float(s[2][1]) is not None)
    if site:
        lines.append(f'            entity.dimension.createExplosion(entity.location, {_plain_float(site[2][1])}, {{ breaksBlocks: true }});')

    site = first_site(('addParticle',), lambda s: s[2] and dotted(s[2][0]))
    if site:
        java_particle = dotted(site[2][0]).split(".")[-1].lower()
        bedrock_particle = JAVA_PARTICLE_MAP.get(java_particle, "minecraft:enchantment_table_particle")
        lines.append(f'            entity.dimension.spawnParticle("{bedrock_particle}", entity.location);')

    def three_numbers(args):
        if len(args) >= 3:
            txts = [raw_number_text(a) for a in args[:3]]
            if all(t is not None and all(c.isdigit() or c in '.-' for c in t) for t in txts):
                return txts
        return None
    site = first_site(('teleportTo', 'moveTo'), lambda s: three_numbers(s[2]))
    if site:
        x, y, z = three_numbers(site[2])
        lines.append(f'            entity.teleport({{ x: {x}, y: {y}, z: {z} }});')
    elif first_site(('teleportTo', 'teleport')):
        lines.append(f'            // TODO: entity.teleport(targetLocation);')

    site = first_site(('setDeltaMovement',), lambda s: three_numbers(s[2]))
    if site:
        x, y, z = three_numbers(site[2])
        lines.append(f'            entity.applyImpulse({{ x: {x}, y: {y}, z: {z} }});')

    if first_site(('remove', 'discard'), lambda s: not s[2]):
        lines.append(f'            entity.remove();')

    for recv, member, args in sites:
        if member == 'set' and recv.split('.')[-1] == 'entityData' and len(args) >= 2 \
                and isinstance(args[0], jt.MemberReference) and not args[0].qualifier:
            field_ref = args[0].member
            num = raw_number_text(args[1])
            v = num if num is not None else (str(args[1].value) if isinstance(args[1], jt.Literal) else '')
            if (num is not None and all(c.isdigit() or c in '.-' for c in num)) or v in ("true", "false"):
                lines.append(f'            entity.setDynamicProperty("{namespace}:{safe_name}_{field_ref.lower()}", {v});')
            else:
                lines.append(f'            // TODO: entity.setDynamicProperty("{namespace}:{safe_name}_{field_ref.lower()}", value);')

    for recv, member, args in sites:
        if recv.endswith('getPersistentData()') and member in ('putInt', 'putFloat', 'putDouble', 'putBoolean', 'putString') \
                and args and string_value(args[0]) and all(c.isalnum() or c == '_' for c in string_value(args[0])):
            lines.append(f'            // TODO: entity.setDynamicProperty("{namespace}:{string_value(args[0])}", value);')
            break

    if not lines:
        lines.append(f'            // TODO: translate {safe_name} entity behavior manually')

    return lines

def generate_entity_dynamic_properties(java_code: str, safe_name: str, namespace: str) -> list:
    lines = []
    for f in get_unit(java_code).fields():
        if simple_type_name(f.type) != 'EntityDataAccessor':
            continue
        for d in f.declarators:
            init = d.initializer
            if not (isinstance(init, jt.MethodInvocation) and init.member == 'defineId' and init.qualifier == 'SynchedEntityData'):
                continue
            ser = next((r.member for r in iter_nodes(list(init.arguments or []), jt.MemberReference)
                        if r.qualifier == 'EntityDataSerializers'), None)
            if ser is None:
                continue
            field_name = d.name.lower()
            serializer = ser
            prop_key = f'"{namespace}:{safe_name}_{field_name}"'
            kind_name = {"BOOLEAN": "boolean (default false)", "INT": "number (default 0)", "BYTE": "number (default 0)",
                         "SHORT": "number (default 0)", "FLOAT": "number (default 0)", "DOUBLE": "number (default 0)",
                         "STRING": 'string (default "")', "COMPOUND_TAG": 'string (default "")'}.get(serializer)
            if kind_name:
                lines.append(f'    // dynamic property {prop_key}: {kind_name} - no registration needed in @minecraft/server 1.13')
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

    if any('abstractcontainermenu' in i or 'menutype' in i or i == 'createmenu' for i in identifier_set(java_code)):
        STATE.porting_notes.append(
            f"[entity] {safe_name}: uses AbstractContainerMenu (custom GUI). "
            f"Custom GUIs have no Bedrock equivalent — use block inventory components or a Form UI addon."
        )

    unit = get_unit(java_code)
    instance_methods = [m for m in unit.methods()
                        if set(m.modifiers or ()) & {'public', 'private', 'protected'} and 'static' not in (m.modifiers or ())]
    for event_type, (phase, bedrock_event) in _FORGE_EVENT_MAP.items():
        short = event_type.split(".")[-1]
        for m in instance_methods:
            param = next((p for p in m.parameters or [] if simple_type_name(p.type) == short), None)
            text = unit.body_text(m) if param is not None else None
            if text:
                method_name, param_name = m.name, param.name
                translated = _translate_handler_body('{' + text + '}', event_type, param_name, java_code, namespace, safe_name)
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

def extract_and_generate_particles(java_code: str, entity_id: str, namespace: str):
    safe_name = sanitize_identifier(entity_id.split(":")[-1])
    found = set()

    particle_refs = [recv.split('.')[-1] for recv, member, _a in iter_call_sites(get_unit(java_code).tree) if member == 'spawn' and recv]
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
