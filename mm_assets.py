from __future__ import annotations

import copy
import json
import math
import os
import re
import shutil
import uuid
import zipfile
from typing import Optional, Tuple, Dict, Set, List
from mm_core import (
    BP_FOLDER,
    DEBUG_MODE,
    Image,
    PIL_AVAILABLE,
    RP_FOLDER,
    STATE,
    VALID_ICON_SIZES,
    _ENTITY_SOUND_EVENTS,
    _REAL_PRINT,
    _logger,
    _warn,
    build_geometry_id,
    clean_java_artifact_name,
    sanitize_filename_keep_ext,
    sanitize_identifier,
    sanitize_path_parts,
)
from mm_java import (
    Fragment,
    _int_arg_text,
    _name_value_pairs,
    eval_const,
    extract_class_name,
    get_unit,
    iter_call_chains,
    iter_call_sites,
    iter_calls,
    iter_nodes,
    jt,
    method_statements,
    node_var_name,
    recv_var,
    simple_type_name,
    string_value,
)

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
                                    STATE.rp_asset_index["flipbook_textures"][flipbook_entry["atlas_tile"]] = flipbook_entry
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
                                if clean_k not in STATE.collected_sound_defs:
                                    cleaned = _sanitize_sound_def(v)
                                    if not cleaned.get("sounds"):
                                        cleaned["sounds"] = [{"name": f"sound/{clean_k}"}]
                                    STATE.collected_sound_defs[clean_k] = cleaned
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
                                    if clean_k not in STATE.collected_sound_defs:
                                        cleaned = _sanitize_sound_def(v)
                                        if not cleaned.get("sounds"):
                                            cleaned["sounds"] = [{"name": f"sound/{clean_k}"}]
                                        STATE.collected_sound_defs[clean_k] = cleaned
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
def _rot_deg(node) -> Optional[float]:

    val = eval_const(node)
    return None if val is None else math.degrees(val)

def _float_arg(node) -> Optional[float]:

    return eval_const(node) if isinstance(node, (jt.Literal, jt.Cast, jt.BinaryOperation)) else None

def _geo_document(tex_w, tex_h, geo_id: str, bones: list) -> dict:
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
            "bones": bones,
        }],
    }

_LAYER_METHOD_NAMES = [
    'createBodyLayer', 'createBodyModel', 'createMeshes', 'createLayers',
    'createLayer', 'createModel', 'createModelData', 'bakeRoot',
]

def _cube_list_from_builder(builder) -> list:

    cubes = []
    cur_u, cur_v = 0, 0
    if isinstance(builder, jt.MethodInvocation):
        chain = [(builder.member, list(builder.arguments or []))] + [
            (s.member, list(s.arguments or [])) for s in (builder.selectors or []) if isinstance(s, jt.MethodInvocation)]
        for member, args in chain:
            if member == 'texOffs' and len(args) >= 2:
                u, v = eval_const(args[0]), eval_const(args[1])
                if u is not None and v is not None:
                    cur_u, cur_v = int(u), int(v)
            elif member == 'addBox' and len(args) >= 6:
                vals = [_float_arg(a) for a in args[:6]]
                if all(x is not None for x in vals):
                    cubes.append((cur_u, cur_v, vals))
    return cubes

def _part_pose(node) -> Tuple[list, list]:

    pivot, rotation = [0.0, 0.0, 0.0], [0.0, 0.0, 0.0]
    if isinstance(node, jt.MethodInvocation) and (node.qualifier or '').split('.')[-1] == 'PartPose':
        args = node.arguments or []
        if node.member == 'offset' and len(args) >= 3:
            for i in range(3):
                v = _float_arg(args[i])
                if v is not None:
                    pivot[i] = v
        elif node.member == 'offsetAndRotation' and len(args) >= 6:
            for i in range(3):
                v = _float_arg(args[i])
                if v is not None:
                    pivot[i] = v
            for i in range(3, 6):
                deg = _rot_deg(args[i])
                if deg is not None:
                    rotation[i - 3] = round(deg, 4)
    return pivot, rotation

def convert_layerdefinition_to_geckolib(
    java_code: str,
    model_name: str,
    namespace: str,
    entity_name: Optional[str] = None,
) -> Optional[dict]:

    try:
        if not isinstance(java_code, str) or not java_code.strip():
            return None
        unit = get_unit(java_code)
        idents = unit.identifiers()
        if 'layerdefinition' not in idents and 'meshdefinition' not in idents:
            return None
        stmts = method_statements(unit, _LAYER_METHOD_NAMES)
        if stmts is None:
            if 'addorreplacechild' not in idents:
                return None
            stmts = [unit.tree]
        tex_w, tex_h = 64, 32
        for call in iter_calls(stmts) if stmts else []:
            if call.member == 'create' and call.qualifier == 'LayerDefinition' and len(call.arguments or []) >= 3:
                w, h = eval_const(call.arguments[1]), eval_const(call.arguments[2])
                if w is not None and h is not None:
                    tex_w, tex_h = int(w), int(h)
                break
        else:
            for call in unit.walk(jt.MethodInvocation):
                if call.member == 'create' and call.qualifier == 'LayerDefinition' and len(call.arguments or []) >= 3:
                    w, h = eval_const(call.arguments[1]), eval_const(call.arguments[2])
                    if w is not None and h is not None:
                        tex_w, tex_h = int(w), int(h)
                    break

        root_var = 'partdefinition'
        for decl in iter_nodes(stmts, jt.VariableDeclarator):
            init = decl.initializer
            if isinstance(init, jt.MethodInvocation) and init.member == 'getRoot' and not init.arguments:
                root_var = decl.name
                break
        var_to_bone: Dict[str, dict] = {
            root_var: {'name': '__root__', 'pivot': [0.0, 0.0, 0.0], 'rotation': [0.0, 0.0, 0.0], 'cubes': []}
        }
        var_to_parent_var: Dict[str, str] = {}

        def register(var_name: Optional[str], call: jt.MethodInvocation):
            parent_var = call.qualifier
            args = call.arguments or []
            if not var_name or not parent_var or len(args) < 2:
                return
            bone_name = string_value(args[0]) or var_name
            pivot, rotation = (_part_pose(args[2]) if len(args) >= 3 else ([0.0] * 3, [0.0] * 3))
            cubes = []
            for u, v, vals in _cube_list_from_builder(args[1]):
                cubes.append({
                    "origin": [pivot[0] + vals[0], pivot[1] + vals[1], pivot[2] + vals[2]],
                    "size": vals[3:6],
                    "uv": [u, v],
                })
            var_to_bone[var_name] = {'name': bone_name, 'pivot': pivot, 'rotation': rotation, 'cubes': cubes}
            var_to_parent_var[var_name] = parent_var

        for node in iter_nodes(stmts, jt.LocalVariableDeclaration, jt.StatementExpression):
            if isinstance(node, jt.LocalVariableDeclaration):
                for d in node.declarators:
                    if isinstance(d.initializer, jt.MethodInvocation) and d.initializer.member == 'addOrReplaceChild':
                        register(d.name, d.initializer)
            elif isinstance(node.expression, jt.MethodInvocation) and node.expression.member == 'addOrReplaceChild':
                register(None, node.expression)

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
        geo_id = f"geometry.{sanitize_identifier(namespace)}.{sanitize_identifier(entity_name or model_name)}"
        return _geo_document(tex_w, tex_h, geo_id, gecko_bones)
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
_MODEL_BOX_TYPES = ('AdvancedModelBox', 'ExtendedModelRenderer', 'ModelBoxRenderer', 'CubeRenderer', 'AModelRenderer')

def _assigned_creations(unit_like) -> list:

    out = []
    for node in unit_like.walk(jt.Assignment, jt.VariableDeclarator):
        if isinstance(node, jt.Assignment):
            lhs = node.expressionl
            name = node_var_name(lhs) if not isinstance(lhs, jt.This) else node_var_name(lhs)
            val = node.value
        else:
            name, val = node.name, node.initializer
        if name and isinstance(val, jt.ClassCreator):
            out.append((name, val))
    return out

def convert_modelbase_to_geckolib(
    java_code: str,
    model_name: str,
    namespace: str,
    entity_name: Optional[str] = None,
) -> Optional[dict]:

    try:
        if not isinstance(java_code, str) or not java_code.strip():
            return None
        unit = get_unit(java_code)
        idents = unit.identifiers()
        if 'setrotationpoint' not in idents and 'addbox' not in idents:
            return None
        if 'addorreplacechild' in idents and 'setrotationpoint' not in idents:
            return None
        pairs = _name_value_pairs(unit)

        def tex_dim(names, default):
            for name, val in pairs:
                if name in names:
                    v = _int_arg_text(val)
                    if v is not None:
                        return int(v)
            return default
        tex_w = tex_dim(('texWidth', 'textureWidth', 'xTexSize'), 64)
        tex_h = tex_dim(('texHeight', 'textureHeight', 'yTexSize'), 64)

        scope = None
        cls_name_for_ctor = extract_class_name(java_code)
        if cls_name_for_ctor:
            scope = method_statements(unit, [cls_name_for_ctor])
        if not scope:
            scope = method_statements(unit, ['init', 'registerParts', 'buildModel', 'setupModel', 'defineModel'])
        if not scope:
            scope = [unit.tree]

        creations = _assigned_creations(Fragment(scope))
        var_to_name: Dict[str, str] = {}
        for var, cr in creations:
            args = cr.arguments or []
            if simple_type_name(cr.type) in _MODEL_BOX_TYPES and len(args) >= 2 and string_value(args[1]) is not None:
                var_to_name[var] = string_value(args[1])
        for var, cr in creations:
            if simple_type_name(cr.type) in ('ModelRenderer', 'ModelPart') and var not in var_to_name:
                var_to_name[var] = var
        for var, cr in creations:
            args = cr.arguments or []
            if simple_type_name(cr.type) in ('AdvancedModelBox', 'ModelRenderer') and len(args) >= 2 \
                    and isinstance(args[0], jt.This) and string_value(args[1]) is not None:
                var_to_name[var] = string_value(args[1])
        if not var_to_name:
            return None

        sites = list(iter_call_sites(scope))
        chains = list(iter_call_chains(scope))
        var_pivot:    Dict[str, List[float]] = {}
        var_rotation: Dict[str, List[float]] = {}
        var_cubes:    Dict[str, list]        = {}
        var_parent:   Dict[str, str]         = {}

        def floats(args, n):
            vals = [_float_arg(a) for a in args[:n]]
            return vals if len(args) >= n and all(v is not None for v in vals) else None

        for var in var_to_name:
            var_pivot[var] = [0.0, 0.0, 0.0]
            for recv, member, args in sites:
                if member == 'setRotationPoint' and recv_var(recv) == var and floats(args, 3) and len(args) == 3:
                    var_pivot[var] = floats(args, 3)
                    break
            rx, ry, rz = 0.0, 0.0, 0.0
            found = False
            for _recv, member, args in sites:
                if member == 'setRotationAngle' and len(args) == 4 and node_var_name(args[0]) == var and floats(args[1:], 3):
                    rx, ry, rz = (math.degrees(v) for v in floats(args[1:], 3))
                    found = True
                    break
            if not found:
                for axis, idx in (('X', 0), ('Y', 1), ('Z', 2)):
                    for a in unit.walk(jt.Assignment):
                        lhs = a.expressionl
                        target = None
                        if isinstance(lhs, jt.MemberReference) and lhs.qualifier:
                            target = (recv_var(lhs.qualifier), lhs.member)
                        elif isinstance(lhs, jt.This) and len(lhs.selectors or []) == 2 \
                                and all(isinstance(s, jt.MemberReference) for s in lhs.selectors):
                            target = (lhs.selectors[0].member, lhs.selectors[1].member)
                        if target == (var, f'rotateAngle{axis}') and a.type == '=':
                            deg = _rot_deg(a.value)
                            if deg is not None:
                                if idx == 0: rx = deg
                                elif idx == 1: ry = deg
                                else: rz = deg
                            break
            var_rotation[var] = [round(rx, 4), round(ry, 4), round(rz, 4)]

            cubes: list = []
            cur_u, cur_v = 0, 0
            pivot = var_pivot.get(var, [0., 0., 0.])

            def add_cube(vals, dedupe: bool):
                ox, oy, oz, sx, sy, sz = vals
                candidate = {
                    "origin": [round(pivot[0] + ox, 4), round(pivot[1] + oy, 4), round(pivot[2] + oz, 4)],
                    "size":   [sx, sy, sz],
                    "uv":     [cur_u, cur_v],
                }
                if not dedupe or candidate not in cubes:
                    cubes.append(candidate)
            for offset_name in ('setTextureOffset', 'texOffset'):
                for recv, run in chains:
                    if recv_var(recv) != var:
                        continue
                    for i, (member, args) in enumerate(run):
                        if member != offset_name or len(args) != 2:
                            continue
                        u, v = _int_arg_text(args[0]), _int_arg_text(args[1])
                        if u is None or v is None:
                            continue
                        cur_u, cur_v = int(u), int(v)
                        if i + 1 < len(run) and run[i + 1][0] == 'addBox':
                            vals = floats(run[i + 1][1], 6)
                            if vals:
                                add_cube(vals, False)
            for recv, run in chains:
                if recv_var(recv) == var and run and run[0][0] == 'addBox':
                    vals = floats(run[0][1], 6)
                    if vals:
                        add_cube(vals, True)
            var_cubes[var] = cubes
        for recv, member, args in sites:
            if member == 'addChild' and len(args) == 1:
                parent_var, child_var = recv_var(recv), node_var_name(args[0])
                if child_var in var_to_name and parent_var in var_to_name:
                    var_parent[child_var] = parent_var

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
            b: dict = {"name": bone_name, "pivot": [round(x, 4) for x in abs_piv]}
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
        geo_id = f"geometry.{sanitize_identifier(namespace)}.{sanitize_identifier(entity_name or model_name)}"
        return _geo_document(tex_w, tex_h, geo_id, gecko_bones)
    except Exception as e:
        _warn(f"Failed to convert ModelBase model '{model_name}': {str(e)}")
        return None
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

    def _is_animation_like(path: str) -> bool:
        low = (path or "").lower()
        return (
            "/animations/" in low
            or low.endswith(".animation.json")
            or low.endswith(".animation")
            or "/animation_controllers/" in low
            or "/geometry/" in low
            or low.endswith(".geo.json")
            or "/render_controllers/" in low
        )

    if texture_hint:
        candidate = texture_hint.split(":")[-1]
        candidate = candidate.replace(".png", "").strip("/")
        if candidate.startswith("textures/"):
            candidate = candidate[len("textures/"):]
        if _is_animation_like(candidate):
            texture_hint = None
        else:
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
    STATE.rp_asset_index["textures"] = textures
    STATE.rp_asset_index["geometry"] = geometry

    if STATE.rp_asset_index["flipbook_textures"]:
        flipbook_path = os.path.join(RP_FOLDER, "textures", "flipbook_textures.json")
        os.makedirs(os.path.dirname(flipbook_path), exist_ok=True)
        with open(flipbook_path, "w", encoding="utf-8") as f:
            json.dump(STATE.rp_asset_index["flipbook_textures"], f, indent=2)

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
    sounds_dir = os.path.join(RP_FOLDER, "sound")
    if os.path.isdir(sounds_dir):
        for root, _, files in os.walk(sounds_dir):
            for f in files:
                if not f.lower().endswith(".ogg"):
                    continue
                stem = os.path.splitext(f)[0]
                sanitized_key = sanitize_sound_key(stem)
                if sanitized_key not in STATE.collected_sound_defs:
                    STATE.collected_sound_defs[sanitized_key] = {"sounds": [{"name": f"sound/{sanitized_key}"}]}
    if STATE.collected_sound_defs:
        final_defs: Dict[str, dict] = {}
        collisions = 0
        for raw_k, v in STATE.collected_sound_defs.items():
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
        _warn(f"Could not open texture pack {zip_path}: {e}")
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
    if DEBUG_MODE:
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
            if DEBUG_MODE:
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
            _warn(
                f"{stats['skipped']} texture(s) skipped (unrecognised path) in {zip_path}. "
                f"First {len(skipped_samples)} examples: " + ", ".join(skipped_samples)
            )
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
BLOCK_SOUND_PROFILES: Dict[str, dict] = {}
ITEM_SOUND_PROFILES: Dict[str, dict] = {}

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
