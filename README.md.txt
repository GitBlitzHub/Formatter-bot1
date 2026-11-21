# TG Formatter Bot

Telegram-бот: принимает «сырой» текст с данными о человеке и приводит его к фиксированному формату.

Извлечение делает **Gemini** (structured output), нормализацию — обычный Python,
поэтому формат не «плавает» от промпта к промпту.

## Формат вывода

```
FIRST NAME: Ms Judith Ann
LAST NAME: DEAKIN
DOB: 18.11.1967
NUMBER DL: DEAKI661187JA9GY 39
ADRESS: 35 Serpentine Road, Kendal, LA9 4PE
```

- **FIRST NAME** — титул + имя + средние имена. Титул берётся из ввода,
  если его нет — определяется по полу (Mr / Ms).
- **LAST NAME** — только фамилия, заглавными.
- **DOB** — строго `дд.мм.гггг`.
- **NUMBER DL** — номер прав или пусто.
- **ADRESS** — адрес в формате Royal Mail одной строкой, посткод заглавными в конце.

## Настройка

1. Создай бота у [@BotFather](https://t.me/BotFather), получи токен.
2. Возьми API-ключ Gemini в [Google AI Studio](https://aistudio.google.com/apikey).
3. Скопируй `.env.example` в `.env` и заполни:

```env
BOT_TOKEN=...
GEMINI_API_KEY=...
GEMINI_MODEL=gemini-2.5-flash
ALLOWED_USER_IDS=
```

## Локальный запуск

```bash
python -m venv .venv
source .venv/bin/activate     # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python bot.py
```

## Деплой на Railway

1. Запушь репозиторий на GitHub.
2. Railway → **New Project** → **Deploy from GitHub repo** → выбери репозиторий.
3. Во вкладке **Variables** добавь:
   - `BOT_TOKEN`
   - `GEMINI_API_KEY`
   - `GEMINI_MODEL` = `gemini-2.5-flash`
   - `ALLOWED_USER_IDS` (опционально)
4. Railway сам подхватит `Procfile` / `railway.json` и запустит воркер.
5. Каждый `git push` в `main` = автодеплой.