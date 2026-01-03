# Python latest version use kar
FROM python:3.12-slim

# Working directory bana
WORKDIR /app

# Saare files copy kar (bot.py, bots_data.json etc.)
COPY . .

# Required library install kar (python-telegram-bot)
RUN pip install --no-cache-dir python-telegram-bot

# Bot run karne ka command
CMD ["python", "test.py"]
