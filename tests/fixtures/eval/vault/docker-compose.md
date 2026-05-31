# Docker Compose Multi-Container Apps

Docker Compose defines a multi-container application in a single YAML file. Each
service describes one container: its image, ports, environment variables, volumes,
and dependencies. depends_on controls start order. Compose creates a private network
so services reach each other by service name as a hostname. docker compose up builds
and starts the whole stack with one command, and docker compose down tears it back
down, making local development environments reproducible.
