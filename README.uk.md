[English](README.md) | [Українська](README.uk.md)

# Threads Digest

> **⚠️ Увага:** Threads може блокувати або обмежувати акаунти за автоматизацію. Використовуйте на свій страх і ризик. Хоча деякі заходи проти виявлення вжито, гарантій немає. Цей проєкт опубліковано в освітніх цілях.

Скіл для Claude Code, який читає вашу стрічку Threads і створює AI-дайджест. Ви не скролите — Claude скролить за вас.

## Встановлення

```
/plugin marketplace add ok-up/threads-digest
/plugin install threads-digest
```

Потім попросіть Claude: "read my Threads feed" або використайте `/threads-digest`. Якщо якісь передумови (Chrome, Python, uv) відсутні, Claude автоматично допоможе їх встановити.

## Налаштування логіну (для headless VPS)

Якщо Claude працює на headless-сервері, спочатку потрібно залогінитись на локальній машині та скопіювати сесію.

**Передумови:** Python 3.11+, [uv](https://docs.astral.sh/uv/), Chrome або Chromium.

```bash
git clone https://github.com/ok-up/threads-digest.git
cd threads-digest
uv run python -m scripts.cli login
```

Відкриється Chrome — залогіньтесь у Threads через Instagram. Коли побачите стрічку, натисніть Ctrl+C.

Скопіюйте збережену сесію на сервер:

```bash
rsync -az profile/ YOUR_SERVER:~/.claude/plugins/marketplaces/threads-digest/profile/
```

Якщо сесія закінчиться — повторіть ці кроки.

## Архітектура

**CDP over WebSocket** — пряме керування браузером через Chrome DevTools Protocol. Без Puppeteer/Playwright — лише `requests` + `websockets`. Мінімум залежностей, повний контроль над таймінгами.

**Stealth** — багаторівнева маскировка:
- Аргументи запуску (`--disable-blink-features=AutomationControlled`)
- Підміна User-Agent та Client Hints через CDP
- JS-ін'єкції: `navigator.webdriver = false`, патч `chrome.runtime`
- Human-like затримки та рандомізація скролу — захист від серверної антибот-аналітики, працює і в headless

**Подвійний парсинг стрічки** — спочатку витягує пости з SSR JSON (`<script type="application/json">`), при скролі — fallback на DOM-парсинг через CSS-селектори. Дедуплікація за URL поста.

**Сесія через Chrome-профіль** — логін виконується один раз у GUI-браузері, профіль копіюється на сервер через rsync. Cookies кросплатформні (SQLite) — профіль з Windows працює на Linux ARM.

**Headless за замовчуванням** — `--headless=new`. Інтерактивний режим лише для логіну.

## Протестовано на

- macOS
- Debian ARM (VPS)

Інші платформи мають працювати — якщо щось зламається, Claude допоможе виправити.

## Ліцензія

MIT
