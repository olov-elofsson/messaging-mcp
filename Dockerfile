FROM python:3.11-slim

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app
COPY . .

# The firestore extra keeps sign-ins across restarts.
RUN uv pip install --system ".[firestore]"

EXPOSE 8080
CMD ["messaging-mcp"]
