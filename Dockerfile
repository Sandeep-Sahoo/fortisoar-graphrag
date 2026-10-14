FROM python:3.12-slim
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PYTHONUTF8=1
# git: the GitHub fetch lists release branches with `git ls-remote`, and the Help panel reads the running commit
# from the mounted .git (owned by another user, so it is marked safe)
RUN apt-get update && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/* \
    && git config --system --add safe.directory /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app
COPY scripts ./scripts
RUN useradd --create-home appuser
USER appuser
EXPOSE 8000
CMD ["python", "-m", "app.cli", "serve", "--port", "8000"]
