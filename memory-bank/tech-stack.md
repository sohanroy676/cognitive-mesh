# Tech Stack

## Status
This file describes the intended technology stack for a new project.
No implementation, dependency versions, or repository commands have been verified yet.

## Language and Runtime
- Language: Python
- Python version: 3.12
- Dependency manager: TBD
- Virtual environment approach: TBD

## AI Models and Providers
- Primary planned external API provider: Groq
- Additional providers: Pluggable; specific providers TBD
- Local model runtime: Ollama
- Model selection and routing: Custom Python implementation
- Model IDs, provider SDKs, and model availability: TBD

The application should support multiple configured model providers where available.
Provider-specific logic should be isolated behind a common internal interface.
API credentials must come from environment variables or a local, ignored .env file.
Never hardcode credentials in source code or commit secrets.

## Memory and Data
- Vector database: ChromaDB
- Graph representation and traversal: NetworkX
- Persistent structured storage: SQLite
- Embeddings: Sentence Transformers
- Embedding model and version: TBD

SQLite is intended to store structured records and metadata.
ChromaDB is intended for semantic vector retrieval.
NetworkX is intended for graph operations. Persistence and reconstruction of the graph
must be implemented explicitly; NetworkX alone is not the persistent database.

## Application Architecture
- Orchestration: Custom Python orchestrator
- Agent roles and interfaces: To be defined during implementation
- Application understanding: Source code, configuration, dependency manifests,
  and documentation, subject to the supported ingestion scope
- Memory approach: Episodic traces, evidence-aware knowledge, and context retrieval

## Testing
- Test framework: TBD
- Test directory: TBD
- Test file naming convention: TBD
- Test command: TBD

Choose and document a test framework during project foundation work.
Tests should cover unit behavior, integration boundaries, and end-to-end workflows.
Tests requiring external APIs must support controlled mocking or explicitly configured
integration credentials.

## Linting and Formatting
- Linter: TBD
- Formatter: TBD
- Static type checker: TBD
- Commands: TBD

Select tools and record their versions and commands when the project configuration
is established.

## Build, Run, and Development
- Installation command: TBD
- Environment setup command: TBD
- Application startup command: TBD
- Development command: TBD
- Build or packaging command: TBD

Do not document unverified commands as working commands.

## External Services
- Groq API: Planned primary external model provider
- Ollama: Planned local model runtime
- Other model APIs: Optional future integrations
- API credentials and endpoint configuration: TBD

The core application should separate provider configuration from orchestration,
memory, and agent logic.