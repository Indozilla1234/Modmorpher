# ModMorpher - Java Mod → Bedrock Addon

         Trying to Convert Mods Since June 9th 2025

ModMorpher is an experimental conversion tool. Because Java and Bedrock use fundamentally different modding systems, manual fixes should be expected after conversion.

⚠️ this script will auto-install Python packages (`javalang`, `Pillow`, etc) the first time you run it.

ModMorpher tries to take a Minecraft Java mod and produce a Bedrock add-on (`.mcaddon`). It’s not perfect, sometimes it breaks things, but it usually gets you close enough to tweak manually.

---

## What it Does
- 1. It first calls my custom wrapper for Vineflower(ClassDecompiler.jar) to decompile the mod you provide
- 2. Extracts textures/models/sounds from the jar into the Resource Pack in the /Bedrock_Pack folder
- 3. It cycles through and attempts to convert java logic to its bedrock equivalent
- 4. It then packages everything into Bedrock_Pack.mcaddon, ready for bedrock or tweaking. And deletes /src_[mod]

---

## What Works Best
This is made for **mods made with Forge / NeoForge**.

| Minecraft | loader | how reliable it is |
|---|---|---|
| 1.20.1+ | NeoForge | decent, but expect some manual fixing |
| 1.19.2-1.20.1 | Forge | Best Shot|
| 1.12+ | Forge | Works decently, manual tweaking may be needed. |
| 1.3+ | Forge | experimental |
| 1.20.1+ | Fabric / Quilt | experimental |


---

## Requirements
- Python 3.10+ on your PATH
- OpenJDK 21+ (needed for the decompiler)

---

## How To Run
1. Put your compiled `.jar` in this folder (project root).
2. Run:
   ```bash
   python modmorpher.py
   ```
3. Use or tweak Bedrock_Pack.mcaddon

---

## Keep in Mind
- It installs dependecies automatically. If it fails the first time, try running it again.
- Treat the output as a starting point, not a finished pack.

---



## Why The License Stuff is Here
This project is meant to help mod creators & players, not to pirate people’s work.
If you’re converting someone else’s ARR or non-permissive mod, make sure you have permission.


---

## Limitations

ModMorpher does NOT:
- fully convert Java mods into working Bedrock equivalents
- guarantee functional gameplay logic conversion
- support all Forge/Fabric mod features
- handle complex rendering systems perfectly



   
