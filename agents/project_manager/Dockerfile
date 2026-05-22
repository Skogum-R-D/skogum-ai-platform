FROM python:3.12-slim

WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY base/base_agent.py .
COPY base/tools/ tools/
COPY agents/project_manager/agent.py .

CMD ["python", "agent.py"]
