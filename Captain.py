import logging
import sqlite3
import re
import os
from datetime import datetime, timedelta, timezone
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, MessageHandler, filters, CallbackQueryHandler, ContextTypes, CommandHandler

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

# Константы
BOT_TOKEN = "7345768106:AAGvy6A-0kFwPvZOKtZWJI__buhrkcn0ais"
GROUP_ID = -1002045321318  # ID группы


def init_db():
    # Удаляем старую базу данных, если она существует
    if os.path.exists('votes.db'):
        os.remove('votes.db')

    conn = sqlite3.connect('votes.db')
    cursor = conn.cursor()

    # Таблица мероприятий
    cursor.execute('''
        CREATE TABLE events (
            event_id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_name TEXT NOT NULL,
            event_time DATETIME NOT NULL,
            message_id INTEGER,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    # Таблица голосов
    cursor.execute('''
        CREATE TABLE votes (
            vote_id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            first_name TEXT,
            last_name TEXT,
            username TEXT,
            vote TEXT NOT NULL,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (event_id) REFERENCES events (event_id) ON DELETE CASCADE,
            UNIQUE (event_id, user_id)
        )
    ''')

    # Таблица напоминаний
    cursor.execute('''
        CREATE TABLE reminders (
            reminder_id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_id INTEGER NOT NULL,
            reminder_time DATETIME NOT NULL,
            FOREIGN KEY (event_id) REFERENCES events (event_id) ON DELETE CASCADE
        )
    ''')

    conn.commit()
    conn.close()
    logger.info("База данных успешно инициализирована")


def get_db_connection():
    conn = sqlite3.connect('votes.db')
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def save_event(event_name, event_time, message_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO events (event_name, event_time, message_id)
        VALUES (?, ?, ?)
    ''', (event_name, event_time.strftime("%Y-%m-%d %H:%M:%S"), message_id))
    event_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return event_id


def save_reminder(event_id, reminder_time):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO reminders (event_id, reminder_time)
        VALUES (?, ?)
    ''', (event_id, reminder_time.strftime("%Y-%m-%d %H:%M:%S")))
    conn.commit()
    conn.close()


def get_active_events():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('''
        SELECT event_id, event_name, event_time, message_id 
        FROM events 
        WHERE datetime(event_time) > datetime('now')
        ORDER BY event_time
    ''')
    result = cursor.fetchall()
    conn.close()
    return result


def get_event_by_id(event_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('''
        SELECT event_id, event_name, event_time, message_id 
        FROM events 
        WHERE event_id = ?
    ''', (event_id,))
    result = cursor.fetchone()
    conn.close()
    return result


def add_vote(event_id, user_id, first_name, last_name, username, vote):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('''
        INSERT OR REPLACE INTO votes (event_id, user_id, first_name, last_name, username, vote)
        VALUES (?, ?, ?, ?, ?, ?)
    ''', (event_id, user_id, first_name, last_name, username, vote))
    conn.commit()
    conn.close()


def remove_vote(event_id, user_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('''
        DELETE FROM votes 
        WHERE event_id = ? AND user_id = ?
    ''', (event_id, user_id))
    conn.commit()
    conn.close()


def get_votes(event_id, vote_type):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('''
        SELECT first_name, last_name, username 
        FROM votes 
        WHERE event_id = ? AND vote = ?
        ORDER BY timestamp
    ''', (event_id, vote_type))
    result = cursor.fetchall()
    conn.close()
    return result


def format_votes(votes):
    if votes:
        return "\n".join([f"{i + 1}. {first_name} {last_name if last_name else ''} (@{username})".strip()
                          for i, (first_name, last_name, username) in enumerate(votes)])
    else:
        return "Пока никто не проголосовал."


def create_message_link(chat_id, message_id):
    chat_id_without_prefix = str(chat_id).replace("-100", "")
    return f"https://t.me/c/{chat_id_without_prefix}/{message_id}"


def parse_event_time(text):
    try:
        match = re.search(r"(\d{2}\.\d{2}\.\d{4})\s+в\s+(\d{2}:\d{2})", text)
        if match:
            date_str = match.group(1)
            time_str = match.group(2)
            local_time = datetime.strptime(f"{date_str} {time_str}", "%d.%m.%Y %H:%M")
            utc_time = local_time - timedelta(hours=3)
            return utc_time.replace(tzinfo=timezone.utc)
        return None
    except Exception as e:
        logger.error(f"Ошибка парсинга даты: {e}")
        return None


async def send_reminder(context: ContextTypes.DEFAULT_TYPE):
    try:
        job = context.job
        event_id = job.data
        event = get_event_by_id(event_id)

        if not event:
            logger.error(f"Мероприятие {event_id} не найдено")
            return

        event_id, event_name, event_time, message_id = event
        yes_votes = format_votes(get_votes(event_id, "Да"))
        maybe_votes = format_votes(get_votes(event_id, "Возможно"))
        message_link = create_message_link(GROUP_ID, message_id)

        reminder_text = (
            f"Напоминаю, что ровно через 24 часа состоится турнир {event_time}!\n\n"
            "Чтобы не подвести свою команду, пожалуйста, убедись, что твой голос актуальный. "
            f"Голосование можно найти тут → {message_link}\n\n"
            "Просьба! Если вдруг понимаешь, что никаким образом не сможешь приехать на матчи, "
            "как можно скорее отмени голос – "
            "это поможет оперативнее найти тебе замену.\n\n"
            f"Списки проголосовавших на данный момент:\n\n"
            f"✅ Список 'Да':\n{yes_votes}\n\n"
            f"❓ Список 'Возможно':\n{maybe_votes}"
        )

        await context.bot.send_message(chat_id=GROUP_ID, text=reminder_text)
        logger.info(f"Напоминание для мероприятия {event_id} отправлено")
    except Exception as e:
        logger.error(f"Ошибка при отправке напоминания: {e}", exc_info=True)


async def handle_event_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.chat_id == GROUP_ID and "#голосование" in update.message.text:
        event_time = parse_event_time(update.message.text)

        if not event_time:
            await update.message.reply_text("Не удалось распознать дату и время мероприятия.")
            return

        event_name = update.message.text.split("#голосование")[0].strip()

        # Временные кнопки
        keyboard = [
            [InlineKeyboardButton("Да", callback_data='vote_yes_temp')],
            [InlineKeyboardButton("Возможно", callback_data='vote_maybe_temp')],
            [InlineKeyboardButton("Отменить голос", callback_data='vote_cancel_temp')]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        local_event_time = event_time + timedelta(hours=3)
        message_text = (
            f"⚽️ Сбор игроков на турнир {local_event_time.strftime('%d.%m.%Y в %H:%M')}\n\n"
            "🏃Минимальное кол-во игроков: 20\n"
            "🏃🏃Максимальное: 36\n\n"
            "Участвуешь? Проголосуй ниже ↓\n\n"
            f"✅ Список 'Да':\n{format_votes([])}\n\n"
            f"❓ Список 'Возможно':\n{format_votes([])}"
        )

        message = await context.bot.send_message(
            chat_id=GROUP_ID,
            text=message_text,
            reply_markup=reply_markup
        )

        event_id = save_event(event_name, event_time, message.message_id)

        # Обновляем кнопки с правильным event_id
        keyboard = [
            [InlineKeyboardButton("Да", callback_data=f'vote_yes_{event_id}')],
            [InlineKeyboardButton("Возможно", callback_data=f'vote_maybe_{event_id}')],
            [InlineKeyboardButton("Отменить голос", callback_data=f'vote_cancel_{event_id}')]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        await context.bot.edit_message_reply_markup(
            chat_id=GROUP_ID,
            message_id=message.message_id,
            reply_markup=reply_markup
        )

        reminder_time = event_time - timedelta(hours=24)
        current_time = datetime.now(timezone.utc)

        if reminder_time > current_time:
            logger.info(f"Напоминание для мероприятия {event_id} запланировано на {reminder_time} UTC")

            # Удаляем старые задания
            for job in context.job_queue.jobs():
                if job.name == f'reminder_{event_id}':
                    job.schedule_removal()

            context.job_queue.run_once(
                send_reminder,
                when=reminder_time,
                name=f'reminder_{event_id}',
                data=event_id
            )

            save_reminder(event_id, reminder_time)
        else:
            logger.warning("Время напоминания уже прошло")


async def button_click(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id
    first_name = query.from_user.first_name
    last_name = query.from_user.last_name
    username = query.from_user.username

    parts = query.data.split('_')
    if len(parts) < 3:
        await query.answer("Ошибка обработки голоса")
        return

    vote_type = parts[1]
    try:
        event_id = int(parts[2])
    except ValueError:
        await query.answer("Ошибка: неверный ID мероприятия")
        return

    if vote_type == 'yes':
        add_vote(event_id, user_id, first_name, last_name, username, 'Да')
        try:
            event = get_event_by_id(event_id)
            if event:
                _, _, _, message_id = event
                message_link = create_message_link(GROUP_ID, message_id)
                await context.bot.send_message(
                    chat_id=user_id,
                    text=f"Твой голос учтен, ждем тебя на турнире! Ссылка на голосование → {message_link}\n\n"
            "Просьба! Если вдруг понимаешь, что никаким образом не сможешь приехать на матчи, "
            "как можно скорее отмени голос – "
            "это поможет оперативнее найти тебе замену.\n\n"
                )
        except Exception as e:
            logger.error(f"Не удалось отправить личное сообщение: {e}")
    elif vote_type == 'maybe':
        add_vote(event_id, user_id, first_name, last_name, username, 'Возможно')
    elif vote_type == 'cancel':
        remove_vote(event_id, user_id)

    await query.answer(f'Вы проголосовали: {vote_type}')

    try:
        event = get_event_by_id(event_id)
        if event:
            _, event_name, event_time, _ = event
            event_time = datetime.strptime(event_time, "%Y-%m-%d %H:%M:%S")
            local_event_time = event_time + timedelta(hours=3)
            await context.bot.edit_message_text(
                chat_id=GROUP_ID,
                message_id=query.message.message_id,
                text=(
                    f"⚽️ {event_name} - {local_event_time.strftime('%d.%m.%Y в %H:%M')}\n\n"
                    "🏃 Минимальное кол-во игроков: 20\n"
                    "🏃🏃 Максимальное: 36\n\n"
                    "Участвуешь? Проголосуй ниже ↓\n\n"
                    f"✅ Список 'Да':\n{format_votes(get_votes(event_id, 'Да'))}\n\n"
                    f"❓ Список 'Возможно':\n{format_votes(get_votes(event_id, 'Возможно'))}"
                ),
                reply_markup=query.message.reply_markup
            )
    except Exception as e:
        logger.error(f"Ошибка обновления сообщения: {e}")


async def list_events(update: Update, context: ContextTypes.DEFAULT_TYPE):
    events = get_active_events()
    if events:
        text = "Активные мероприятия:\n\n"
        for event in events:
            event_id, event_name, event_time, _ = event
            event_time = datetime.strptime(event_time, "%Y-%m-%d %H:%M:%S")
            local_time = event_time + timedelta(hours=3)
            text += f"{event_id}. {event_name} - {local_time.strftime('%d.%m.%Y в %H:%M')}\n"
        await update.message.reply_text(text)
    else:
        await update.message.reply_text("Нет активных мероприятий")


def main():
    # Полная переинициализация базы данных
    init_db()

    application = Application.builder().token(BOT_TOKEN).build()

    # Восстановление напоминаний
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute('''
            SELECT r.event_id, r.reminder_time 
            FROM reminders r
            JOIN events e ON r.event_id = e.event_id
            WHERE datetime(e.event_time) > datetime('now')
        ''')
        reminders = cursor.fetchall()

        for event_id, reminder_time in reminders:
            reminder_time = datetime.strptime(reminder_time, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
            if reminder_time > datetime.now(timezone.utc):
                application.job_queue.run_once(
                    send_reminder,
                    when=reminder_time,
                    name=f'reminder_{event_id}',
                    data=event_id
                )
                logger.info(f"Восстановлено напоминание для мероприятия {event_id} на {reminder_time} UTC")
    except sqlite3.Error as e:
        logger.error(f"Ошибка при восстановлении напоминаний: {e}")
    finally:
        conn.close()

    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_event_message))
    application.add_handler(CallbackQueryHandler(button_click))
    application.add_handler(CommandHandler("events", list_events))

    logger.info("Бот запущен...")
    application.run_polling()


if __name__ == '__main__':
    main()