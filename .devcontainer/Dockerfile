FROM mcr.microsoft.com/devcontainers/python:3.11

RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /workspace

COPY pyproject.toml pyproject.toml
RUN pip install --no-cache-dir -e '.[broker,llm,dev]'

CMD ["/bin/bash"]
