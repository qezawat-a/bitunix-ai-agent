FROM python:3.12-slim
WORKDIR /app
# git: J-Rock clones/pushes code; nodejs/npm: lets it run MCP servers started with npx
RUN apt-get update && apt-get install -y --no-install-recommends git nodejs npm ca-certificates && rm -rf /var/lib/apt/lists/*
COPY requirements.txt requirements-slack.txt ./
RUN pip install --no-cache-dir -r requirements.txt -r requirements-slack.txt
COPY . .
ENV PYTHONUNBUFFERED=1 DB_PATH=/app/data/agent.db WORKSPACE_DIR=/app/data/projects
# Railway: attach a Volume at /app/data so settings, skills, MCP servers and memory survive redeploys.
EXPOSE 8080
CMD ["python", "-m", "bitunix_agent"]
