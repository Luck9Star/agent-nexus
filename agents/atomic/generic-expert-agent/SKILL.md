# Generic Expert Agent

Loads an Expert Profile YAML configuration and produces structured artifacts using PydanticAI.

## Usage

This agent is not invoked directly. It is the shared runtime for all agency-agents virtual experts.

When the Platform Router resolves a virtual agent (e.g., `agency.software-architect`), it binds to this agent with the `--profile` argument pointing to the expert's profile YAML.

## Input

- `--profile <path>`: Path to the Expert Profile YAML file
- `--task <description>`: The task to execute
- `--context <paths>`: Optional context file paths

## Output

Structured artifact matching the profile's output_contract. The output includes all required_sections declared in the profile.

## Capabilities

- Dynamic role prompt injection from Expert Profile
- Output contract enforcement (missing sections cause validation failure)
- Plan-only permissions (no shell, file write, or network access)
- Model tier selection from profile configuration
