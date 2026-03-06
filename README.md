# ModMorpher: Developer Migration Toolkit (Java to Bedrock)

**ModMorpher** is a high-performance pipeline designed for Java Edition developers to automate the migration of entity logic, models, and GeckoLib animations to the Minecraft Bedrock Edition (Add-on) schema.

---

## Technical Overview
This toolkit provides a structured workflow for original content creators who wish to maintain cross-platform parity for their mods. It eliminates the manual labor involved in rewriting entity behaviors and re-animating models from scratch.

### Core Capabilities
* **Bytecode Analysis:** Utilizes an integrated Java engine to analyze compiled `.jar` structures and map class hierarchies to Bedrock component-based data.
* **Animation Morphing:** Automatically translates Java-side animation triggers and vertex data into Bedrock `.animation.json` and state machine files.
* **Logic Mapping:** Provides a translation layer for NBT-based logic into Bedrock JSON-driven behavior schemas.

---

## Developer Workflow
ModMorpher is designed to fit into a standard local development environment.

### Prerequisites
* **Python 3.10+** (System Path)
* **OpenJDK 17+** (Required for the analysis engine)

### Execution Steps
1. Place the compiled Java `.jar` file into the project root directory.
2. Ensure the Java source or mappings are accessible if custom de-obfuscation is required.
3. Execute the pipeline manager via the terminal:
   ```bash
   python pipeline_manager.py

## Ethical Usage Policy
This toolkit is a bridge for creators, not a vehicle for piracy. 
* **Respect Creators:** Do not use this tool to port mods without the original author's explicit permission.
* **Support the Ecosystem:** Unauthorized redistribution of converted assets hurts the modding community and the creators you admire. 
* **Abuse Policy:** The maintainers of ModMorpher do not condone or support the use of this software for bypassing Marketplace protections or infringing on Intellectual Property.
   
