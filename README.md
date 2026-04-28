# Kolobok MVP Bot

MVP Telegram-бота: принимает готовый Telegram-кружок, пересобирает MP4, ставит новое `creation_time` и отправляет обратно как новый кружок.

## 1. Что нужно установить

- Python 3.10+
- FFmpeg + FFprobe
- VS Code
- Telegram bot token от @BotFather

## 2. Подготовка проекта

```bash
python -m venv .venv
```

Windows PowerShell:

```bash
.venv\Scripts\Activate.ps1
```

Windows CMD:

```bash
.venv\Scripts\activate.bat
```

macOS/Linux:

```bash
source .venv/bin/activate
```

Установка зависимостей:

```bash
pip install -r requirements.txt
```

## 3. Токен бота

Скопируй `.env.example` в `.env` и вставь токен:

```env
BOT_TOKEN=1234567890:AA...
```

## 4. Проверка FFmpeg

```bash
ffmpeg -version
ffprobe -version
```

Если команды не находятся, значит FFmpeg не добавлен в PATH.

## 5. Запуск

```bash
python bot.py
```

После запуска открой своего бота в Telegram, нажми `/start` и отправь ему кружок.

## 6. Что делает бот

1. Скачивает video_note.
2. Читает старое `creation_time` через `ffprobe`.
3. Пересобирает MP4 через `ffmpeg`.
4. Удаляет старые метаданные.
5. Записывает новое `creation_time`.
6. Отправляет файл назад через `send_video_note`.
7. Удаляет временные файлы.

## 7. Важный нюанс

Разные телефоны могут показывать дату видео по-разному: из MP4-метаданных, даты изменения файла или даты скачивания. Этот MVP меняет MP4 `creation_time` и локальный `mtime` перед загрузкой, но финальное отображение зависит от клиента Telegram и галереи телефона.
