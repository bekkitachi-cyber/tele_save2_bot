FROM python:3.10-slim
WORKDIR /app,
COPY talablarsm.txt .
RUN pip install -r talablarsm.txt
COPY . .
CMD ["python", "bot.py"].
