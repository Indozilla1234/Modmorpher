ModMorpher: Developer Migration Toolkit (Java to Bedrock)
ModMorpher is a high-performance pipeline designed for Java Edition developers to automate the migration of entity logic, models, and GeckoLib animations to the Minecraft Bedrock Edition (Add-on) schema.

Technical Overview
This toolkit provides a structured workflow for original content creators who wish to maintain cross-platform parity for their mods. It eliminates the manual labor involved in rewriting entity behaviors and re-animating models from scratch.

Core Capabilities

Bytecode Analysis: Utilizes an integrated Java engine to analyze compiled .jar structures and map class hierarchies to Bedrock component-based data.

Animation Morphing: Automatically translates Java-side animation triggers and vertex data into Bedrock .animation.json and state machine files.

Logic Mapping: Provides a translation layer for NBT-based logic into Bedrock's JSON-driven behavior schemas.

Developer Workflow
ModMorpher is designed to fit into a standard local development environment.

Prerequisites

Python 3.10+ (System Path)

OpenJDK 17+ (Required for the analysis engine)

Execution Steps

Place the compiled Java .jar file into the project root directory.

Ensure the Java source or mappings are accessible if custom de-obfuscation is required.

Execute the pipeline manager via the terminal:

Bash
python pipeline_manager.py
The pipeline will generate a structured directory containing the Behavior Pack and Resource Pack.

Project Structure
pipeline_manager.py: The central orchestrator for file I/O and process execution.

modmorpher.py: The logic engine responsible for GeckoLib and model translation.

ClassDecompiler.java: The underlying analysis engine for Java bytecode inspection.

Usage Policy and Responsibility
Authorized Porting: This tool is intended for use by original mod authors or developers with explicit authorization to port specific works.

Asset Integrity: ModMorpher does not host or distribute third-party assets. It is a logic-transformation framework.

Licensing: This project is distributed under the MIT License. This allows for open community contribution to the mapping tables while protecting the author from liability regarding the misuse of the tool.

Technical Specifications
Implementation: Python 3 (Orchestration), Java (Bytecode Analysis)

Output Format: Standardized .mcpack or .mcaddon directory structures.

Compatibility: Optimized for GeckoLib-based entities and standard Forge/NeoForge entity hierarchies.

Implementation Note

By using this README, you are defining the tool's "Intended Use Case." In a legal context, the "intended use" of a product is a major factor. If you state clearly that this is a developer tool for authors to port their own work, you are operating within the standard bounds of software interoperability.
