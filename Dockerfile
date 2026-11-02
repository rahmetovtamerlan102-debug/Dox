# Используем официальный и легкий образ Python
FROM python:3.11-slim

# Устанавливаем рабочую директорию внутри контейнера
WORKDIR /app

# Устанавливаем системные зависимости, которые нужны для сборки TgCrypto и requests
RUN apt-get update && apt-get install -y \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Копируем файл с зависимостями и устанавливаем их
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Копируем все остальные файлы (bot.py и т.д.)
COPY . .

# Запускаем бота (так как у нас нет веб-сервера, а только фоновые задачи)
CMD ["python", "bot.py"]
