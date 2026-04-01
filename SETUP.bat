@echo off
chcp 65001 >nul
title AXIFLOW Setup
color 0F
cls

echo.
echo  ╔═══════════════════════════════════════════════╗
echo  ║   ⚡  AXIFLOW — Автоматичне встановлення      ║
echo  ╚═══════════════════════════════════════════════╝
echo.
echo  Перевіряю Python...

python --version >nul 2>&1
IF %ERRORLEVEL% NEQ 0 (
    echo.
    echo  ❌ Python не знайдено!
    echo.
    echo  Зроби це прямо зараз:
    echo  1. Відкрий браузер
    echo  2. Йди на: python.org/downloads
    echo  3. Натисни Download Python 3.12
    echo  4. ВАЖЛИВО: постав галочку "Add python.exe to PATH"
    echo  5. Натисни Install Now
    echo  6. Запусти цей файл знову
    echo.
    pause
    exit /b 1
)

python --version
echo  ✅ Python знайдено!
echo.
echo  Встановлюю бібліотеки... (2-3 хвилини)
echo.

pip install fastapi uvicorn httpx python-telegram-bot ccxt python-dotenv pydantic numpy --quiet --no-warn-script-location

IF %ERRORLEVEL% NEQ 0 (
    echo.
    echo  ❌ Помилка встановлення!
    echo  Спробуй запустити від імені Адміністратора:
    echo  Клік правою кнопкою по SETUP.bat → "Запуск від імені адміністратора"
    echo.
    pause
    exit /b 1
)

echo  ✅ Бібліотеки встановлено!
echo.
echo  ════════════════════════════════════════════════
echo  ТЕПЕР ВІДКРИЙ ФАЙЛ .env в Блокноті і заповни:
echo  ════════════════════════════════════════════════
echo.
echo  Відкриваю .env...
notepad .env
echo.
echo  Після збереження .env натисни будь-яку клавішу
pause >nul

echo.
echo  ✅ Готово до запуску!
echo.
echo  Запускаю AXIFLOW...
echo.
start "AXIFLOW Server" cmd /k "python -m uvicorn api.server:app --host 0.0.0.0 --port 8000 --reload"
timeout /t 3 >nul
start "AXIFLOW Bot" cmd /k "python bot.py"

echo.
echo  ╔═══════════════════════════════════════════════╗
echo  ║  ✅ AXIFLOW запущено!                         ║
echo  ║                                               ║
echo  ║  Сервер: http://localhost:8000                ║
echo  ║  Бот: @axiflowminiapp_bot                    ║
echo  ║                                               ║
echo  ║  НЕ ЗАКРИВАЙ ці вікна!                       ║
echo  ║  Поки вони відкриті — бот працює             ║
echo  ╚═══════════════════════════════════════════════╝
echo.
echo  Відкрий Telegram і напиши /start своєму боту
echo.
pause
