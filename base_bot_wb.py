import asyncio
import aiohttp
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    filters,
    ConversationHandler
)
import json
import os
from threading import Thread
from flask import Flask

# Flask приложение для Render
flask_app = Flask(__name__)


@flask_app.route('/')
def home():
    return "🤖 Бот работает! ✅"


@flask_app.route('/health')
def health():
    return {"status": "ok", "bot": "running"}


def run_flask():
    """Запуск Flask в отдельном потоке"""
    port = int(os.environ.get('PORT', 10000))
    flask_app.run(host='0.0.0.0', port=port)


# Состояния
WAITING_FOR_API_KEY = 1

# Файл для хранения данных
DATA_FILE = 'bot_data.json'
user_data = {}


def load_user_data():
    """Загрузить данные из файла"""
    global user_data
    try:
        if os.path.exists(DATA_FILE):
            with open(DATA_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
                for user_id, info in data.items():
                    info['known_orders'] = set(info.get('known_orders', []))
                user_data = data
                print(f"✅ Загружено данных для {len(user_data)} пользователей")
    except Exception as e:
        print(f"⚠️ Ошибка загрузки: {e}")
        user_data = {}


def save_user_data():
    """Сохранить данные в файл"""
    try:
        data_to_save = {}
        for user_id, info in user_data.items():
            data_to_save[user_id] = info.copy()
            data_to_save[user_id]['known_orders'] = list(info.get('known_orders', set()))

        with open(DATA_FILE, 'w', encoding='utf-8') as f:
            json.dump(data_to_save, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"⚠️ Ошибка сохранения: {e}")


async def get_wb_orders(api_key: str, limit: int = 1000, next_cursor: int = 0):
    """Получить заказы из WB API"""
    url = "https://marketplace-api.wildberries.ru/api/v3/orders"
    headers = {
        "Authorization": api_key,
        "Content-Type": "application/json"
    }
    params = {
        "limit": limit,
        "next": next_cursor
    }

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers, params=params, timeout=30) as response:
                if response.status == 200:
                    data = await response.json()
                    return data.get('orders', []), data.get('next', 0)
                elif response.status == 401:
                    print("❌ Ошибка авторизации: неверный API ключ")
                    return None, None
                else:
                    print(f"❌ Ошибка API: статус {response.status}")
                    return None, None
    except asyncio.TimeoutError:
        print("⏱ Превышено время ожидания")
        return None, None
    except Exception as e:
        print(f"❌ Ошибка запроса: {e}")
        return None, None


def format_order(order: dict) -> str:
    """Форматировать заказ для отправки"""
    created = datetime.fromisoformat(order['createdAt'].replace('Z', '+00:00'))
    price = order['convertedPrice'] / 100

    msg = f"🆕 <b>Новый заказ!</b>\n\n"
    msg += f"🆔 ID: <code>{order['id']}</code>\n"
    msg += f"📦 Артикул: <b>{order['article']}</b>\n"
    msg += f"💰 Цена: <b>{price:.2f} ₽</b>\n"
    msg += f"📅 Создан: {created.strftime('%d.%m.%Y %H:%M')}\n"
    msg += f"🏢 Склад: {', '.join(order.get('offices', ['Не указан']))}\n"
    msg += f"📋 Supply ID: <code>{order.get('supplyId', 'N/A')}</code>\n"

    if order.get('comment'):
        msg += f"💬 Комментарий: {order['comment']}\n"

    return msg


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /start"""
    user_id = update.effective_user.id

    if user_id not in user_data:
        user_data[user_id] = {
            'api_key': None,
            'known_orders': set(),
            'monitoring': False
        }

    keyboard = [
        [InlineKeyboardButton("⚙️ Настроить API", callback_data='setup_api')],
        [InlineKeyboardButton("▶️ Запустить мониторинг", callback_data='start_monitor')],
        [InlineKeyboardButton("⏸ Остановить мониторинг", callback_data='stop_monitor')],
        [InlineKeyboardButton("📊 Статистика", callback_data='stats')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    status = "🟢 Активен" if user_data[user_id].get('monitoring') else "🔴 Остановлен"
    api_status = "✅ Установлен" if user_data[user_id].get('api_key') else "❌ Не установлен"

    text = (
        f"🤖 <b>Бот мониторинга заказов Wildberries</b>\n\n"
        f"API ключ: {api_status}\n"
        f"Мониторинг: {status}\n\n"
        f"📍 Проверка новых заказов каждые 10 минут\n"
        f"🔔 Уведомления о новых заказах"
    )

    await update.message.reply_text(text, reply_markup=reply_markup, parse_mode='HTML')


async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка кнопок"""
    query = update.callback_query
    await query.answer()

    user_id = update.effective_user.id

    if user_id not in user_data:
        user_data[user_id] = {
            'api_key': None,
            'known_orders': set(),
            'monitoring': False
        }

    if query.data == 'setup_api':
        await query.edit_message_text(
            "🔑 <b>Настройка API ключа</b>\n\n"
            "Отправьте ваш API ключ от Wildberries\n\n"
            "Где взять:\n"
            "1. Личный кабинет продавца WB\n"
            "2. Настройки → Доступ к API\n"
            "3. Создать новый токен\n\n"
            "Отправьте /cancel для отмены",
            parse_mode='HTML'
        )
        return WAITING_FOR_API_KEY

    elif query.data == 'start_monitor':
        if not user_data[user_id].get('api_key'):
            await query.edit_message_text(
                "⚠️ Сначала настройте API ключ!\n"
                "Нажмите /start"
            )
            return

        user_data[user_id]['monitoring'] = True
        save_user_data()

        await query.edit_message_text(
            "✅ <b>Мониторинг запущен!</b>\n\n"
            "🔄 Проверяю заказы каждые 10 минут\n"
            "🔔 Буду присылать уведомления о новых заказах",
            parse_mode='HTML'
        )

    elif query.data == 'stop_monitor':
        user_data[user_id]['monitoring'] = False
        save_user_data()

        await query.edit_message_text(
            "⏸ <b>Мониторинг остановлен</b>\n\n"
            "Используйте /start чтобы запустить снова",
            parse_mode='HTML'
        )

    elif query.data == 'stats':
        if not user_data[user_id].get('api_key'):
            await query.edit_message_text("⚠️ Сначала настройте API ключ!")
            return

        await query.edit_message_text("⏳ Загружаю статистику...")

        orders, _ = await get_wb_orders(user_data[user_id]['api_key'], limit=1000)

        if orders is None:
            await query.message.reply_text("❌ Ошибка получения данных")
            return

        if not orders:
            await query.message.reply_text("📭 Нет заказов")
            return

        total_price = sum(o['convertedPrice'] for o in orders) / 100
        avg_price = total_price / len(orders) if orders else 0

        # Группировка по артикулам
        articles = {}
        for order in orders:
            art = order.get('article', 'N/A')
            articles[art] = articles.get(art, 0) + 1

        top_articles = sorted(articles.items(), key=lambda x: x[1], reverse=True)[:5]

        stats_text = (
            f"📊 <b>Статистика заказов</b>\n\n"
            f"📦 Всего заказов: <b>{len(orders)}</b>\n"
            f"💰 Общая сумма: <b>{total_price:.2f} ₽</b>\n"
            f"📈 Средний чек: <b>{avg_price:.2f} ₽</b>\n\n"
            f"🏆 <b>Топ артикулов:</b>\n"
        )

        for i, (art, count) in enumerate(top_articles, 1):
            stats_text += f"{i}. {art}: {count} шт.\n"

        await query.message.reply_text(stats_text, parse_mode='HTML')


async def receive_api_key(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Получение API ключа"""
    user_id = update.effective_user.id
    api_key = update.message.text.strip()

    await update.message.reply_text("⏳ Проверяю API ключ...")

    # Проверка ключа
    orders, _ = await get_wb_orders(api_key, limit=10)

    if orders is None:
        await update.message.reply_text(
            "❌ Неверный API ключ или нет доступа\n\n"
            "Попробуйте еще раз или /cancel"
        )
        return WAITING_FOR_API_KEY

    # Сохраняем
    user_data[user_id]['api_key'] = api_key
    user_data[user_id]['known_orders'] = set(o['id'] for o in orders)
    save_user_data()

    await update.message.reply_text(
        f"✅ <b>API ключ сохранен!</b>\n\n"
        f"📦 Найдено заказов: {len(orders)}\n\n"
        f"Теперь запустите мониторинг через /start",
        parse_mode='HTML'
    )

    return ConversationHandler.END


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Отмена"""
    await update.message.reply_text(
        "❌ Отменено\n"
        "Используйте /start"
    )
    return ConversationHandler.END


async def check_orders_job(context: ContextTypes.DEFAULT_TYPE):
    """Периодическая проверка заказов"""
    print(f"🔍 Проверка заказов... {datetime.now().strftime('%H:%M:%S')}")

    for user_id, data in list(user_data.items()):
        if not data.get('monitoring') or not data.get('api_key'):
            continue

        try:
            orders, _ = await get_wb_orders(data['api_key'], limit=1000)

            if orders is None:
                continue

            current_ids = set(o['id'] for o in orders)
            known_ids = data.get('known_orders', set())

            new_ids = current_ids - known_ids

            if new_ids:
                print(f"🆕 Найдено {len(new_ids)} новых заказов для пользователя {user_id}")

                for order in orders:
                    if order['id'] in new_ids:
                        try:
                            await context.bot.send_message(
                                chat_id=user_id,
                                text=format_order(order),
                                parse_mode='HTML'
                            )
                            await asyncio.sleep(0.5)
                        except Exception as e:
                            print(f"❌ Ошибка отправки сообщения: {e}")

                data['known_orders'] = current_ids
                save_user_data()

        except Exception as e:
            print(f"❌ Ошибка проверки для пользователя {user_id}: {e}")


def main():
    """Запуск бота"""
    load_user_data()

    # Токен из переменных окружения
    TOKEN = os.getenv('TELEGRAM_BOT_TOKEN', "8389924474:AAGthpjg_sKQ5qMydMV4F40nTbK1Pxw0Gxs")

    # Автоматическая настройка API ключа WB
    wb_api_key = os.getenv('WB_API_KEY')
    if wb_api_key:
        if '1' not in user_data:
            user_data['1'] = {
                'api_key': wb_api_key,
                'known_orders': set(),
                'monitoring': True
            }
            save_user_data()
            print("✅ API ключ WB загружен из переменных окружения")

    # Создаем Telegram приложение
    telegram_app = Application.builder().token(TOKEN).build()

    # Обработчик настройки API
    conv_handler = ConversationHandler(
        entry_points=[CallbackQueryHandler(button_handler, pattern='^setup_api$')],
        states={
            WAITING_FOR_API_KEY: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_api_key)
            ]
        },
        fallbacks=[CommandHandler('cancel', cancel)],
        per_message=False
    )

    # Регистрация обработчиков
    telegram_app.add_handler(CommandHandler('start', start_command))
    telegram_app.add_handler(conv_handler)
    telegram_app.add_handler(CallbackQueryHandler(button_handler))

    # Запуск периодической проверки
    job_queue = telegram_app.job_queue
    if job_queue:
        job_queue.run_repeating(check_orders_job, interval=600, first=10)
        print("✅ Периодическая проверка запущена (каждые 10 минут)")
    else:
        print("⚠️ Job queue недоступен!")

    # Запускаем Flask сервер для Render
    flask_thread = Thread(target=run_flask, daemon=True)
    flask_thread.start()
    print(f"🌐 Flask сервер запущен на порту {os.environ.get('PORT', 10000)}")

    # Запускаем бота
    print("🤖 Бот запущен...")
    telegram_app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == '__main__':
    main()