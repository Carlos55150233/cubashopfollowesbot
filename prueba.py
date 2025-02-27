import logging
import requests as req
import sqlite3
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    filters,
    ContextTypes,
)

# Configuración del bot
TELEGRAM_BOT_TOKEN = '7686149734:AAGsRd_ijq0Toes5AEkfbQrevmauLkw0DYM'  # Reemplaza con tu token
API_URL = 'https://smmtigers.com/api/v2'
API_KEY = '1230580efb3bc04b50b2557d20a2ed6a'  # Reemplaza con tu API key
ADMIN_CHAT_ID = 5338241603  # Reemplaza con tu ID de admin
SERVICES_PER_PAGE = 10

# Base de datos
conn = sqlite3.connect('bot.db', check_same_thread=False)
cursor = conn.cursor()

# Crear tablas
cursor.execute('''
    CREATE TABLE IF NOT EXISTS services (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        service_id TEXT UNIQUE,
        name TEXT,
        type TEXT,
        category TEXT,
        rate REAL,
        min INTEGER,
        max INTEGER
    )
''')
cursor.execute('''
    CREATE TABLE IF NOT EXISTS users (
        username TEXT PRIMARY KEY,
        chat_id INTEGER UNIQUE,
        balance REAL DEFAULT 0.0
    )
''')
cursor.execute('''
    CREATE TABLE IF NOT EXISTS orders (
        order_id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT,
        service_id TEXT,
        link TEXT,
        quantity INTEGER,
        cost REAL,
        status TEXT DEFAULT 'pending',
        provider_order_id TEXT
    )
''')
cursor.execute('''
    CREATE TABLE IF NOT EXISTS balance_requests (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT,
        requested_amount REAL,
        status TEXT DEFAULT 'pending',
        admin_response TEXT DEFAULT ''
    )
''')
conn.commit()

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.error("Error:", exc_info=context.error)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    username = update.message.from_user.username
    chat_id = update.message.chat_id
    if not username:
        await update.message.reply_text("⚠️ Necesitas un nombre de usuario en Telegram.")
        return

    cursor.execute('''
        INSERT OR REPLACE INTO users (username, chat_id, balance)
        VALUES (?, ?, COALESCE((SELECT balance FROM users WHERE username = ?), 0.0))
    ''', (username, chat_id, username))
    conn.commit()
    await show_main_menu(update, context)

async def show_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("🛒 Servicios", callback_data="services")],
        [InlineKeyboardButton("📝 Crear Orden", callback_data="create_order")],
        [InlineKeyboardButton("💰 Saldo", callback_data="balance")],
        [InlineKeyboardButton("📋 Mis Órdenes", callback_data="view_orders")],
        [InlineKeyboardButton("💸 Recargar Saldo", callback_data="request_balance")],
        [InlineKeyboardButton("⬅️ Atrás", callback_data="back_to_main_menu")],
    ]

    if update.effective_user.id == ADMIN_CHAT_ID:
        keyboard.insert(0, [InlineKeyboardButton("⚙️ Administrar", callback_data="admin_menu")])

    reply_markup = InlineKeyboardMarkup(keyboard)
    
    if update.callback_query:
        await update.callback_query.edit_message_text(
            "✨ Menú Principal ✨\n\nSelecciona una opción:",
            reply_markup=reply_markup
        )
    else:
        await update.message.reply_text(
            "✨ Menú Principal ✨\n\nSelecciona una opción:",
            reply_markup=reply_markup
        )

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data == "back_to_main_menu":
        await show_main_menu(update, context)
        return

    elif query.data == "services":
        services = get_services_from_db()
        if not services:
            await query.edit_message_text("❌ No hay servicios disponibles.")
            return

        context.user_data['services'] = services
        context.user_data['current_page'] = 1
        await show_services_menu(query, context)

    elif query.data == "create_order":
        services = get_services_from_db()
        if not services:
            await query.edit_message_text("❌ No hay servicios disponibles.")
            return

        context.user_data['services'] = services
        context.user_data['current_page'] = 1
        await show_services_for_order(query, context)

    elif query.data.startswith("select_service_for_order_"):
        service_id = query.data.split("_")[4]
        selected_service = next((s for s in context.user_data['services'] if s[1] == service_id), None)

        if selected_service:
            context.user_data['selected_service'] = selected_service
            await query.edit_message_text(
                f"✅ Servicio: {selected_service[2]} (${selected_service[5]:.4f}/1000)\n"
                "🔗 Envía el enlace."
            )
            context.user_data['waiting_for_link'] = True

    elif query.data == "balance":
        username = update.effective_user.username
        cursor.execute('SELECT balance FROM users WHERE username = ?', (username,))
        result = cursor.fetchone()
        balance = result[0] if result else 0.0
        await query.edit_message_text(f"💵 Saldo: ${balance:.2f}")

    elif query.data == "view_orders":
        username = update.effective_user.username
        cursor.execute('SELECT * FROM orders WHERE username = ?', (username,))
        orders = cursor.fetchall()

        if not orders:
            await query.edit_message_text("❌ No tienes órdenes.")
            return

        message = "📋 Tus órdenes:\n"
        for order in orders:
            message += (
                f"🆔 {order[0]} | 🛠️ {order[2]} | 🔗 {order[3]} | "
                f"🔢 {order[4]} | 💰 ${order[5]:.4f} | 🟩 {order[6]}\n"
            )
        await query.edit_message_text(message)

    elif query.data == "request_balance":
        await query.edit_message_text("💸 Envía la cantidad a recargar.")
        context.user_data['waiting_for_balance_request'] = True

    elif query.data == "admin_menu":
        if update.effective_user.id != ADMIN_CHAT_ID:
            await query.edit_message_text("❌ Acceso denegado.")
            return

        keyboard = [
            [InlineKeyboardButton("📥 Importar Servicios", callback_data="import_services")],
            [InlineKeyboardButton("➕ Agregar Servicio", callback_data="add_custom_service")],
            [InlineKeyboardButton("💳 Agregar Saldo", callback_data="add_balance")],
            [InlineKeyboardButton("🔍 Ver Solicitudes", callback_data="view_balance_requests")],
            [InlineKeyboardButton("⬅️ Atrás", callback_data="back_to_main_menu")],
        ]
        await query.edit_message_text("⚙️ Menú Admin:", reply_markup=InlineKeyboardMarkup(keyboard))

    elif query.data == "import_services":
        if update.effective_user.id != ADMIN_CHAT_ID:
            await query.edit_message_text("❌ Permiso denegado.")
            return

        try:
            response = req.post(API_URL, data={'key': API_KEY, 'action': 'services'})
            services = response.json()

            if isinstance(services, list):
                context.user_data['available_services'] = services
                context.user_data['current_page'] = 1
                await show_available_services_page(query, context)
            else:
                await query.edit_message_text("❌ Error en la API.")

        except Exception as e:
            await query.edit_message_text(f"❌ Error: {str(e)}")

    elif query.data.startswith("select_service_to_import_"):
        if update.effective_user.id != ADMIN_CHAT_ID:
            return

        service_index = int(query.data.split("_")[4])
        selected_service = context.user_data['available_services'][service_index]

        context.user_data['selected_service_to_import'] = selected_service
        await query.edit_message_text(
            f"✅ Servicio: {selected_service['name']} (Precio API: ${selected_service['rate']})\n"
            "Envía el nuevo precio."
        )
        context.user_data['waiting_for_service_price_input'] = True

    elif query.data in ("prev_page", "next_page"):
        current_page = context.user_data.get('current_page', 1)
        context.user_data['current_page'] = current_page + 1 if query.data == "next_page" else current_page - 1

        if 'available_services' in context.user_data:
            await show_available_services_page(query, context)
        else:
            await show_services_menu(query, context)

    elif query.data == "add_custom_service":
        if update.effective_user.id != ADMIN_CHAT_ID:
            await query.edit_message_text("❌ Permiso denegado.")
            return

        await query.edit_message_text("📝 Formato: Nombre | Tipo | Categoría | Precio/1000 | Mínimo | Máximo")
        context.user_data['waiting_for_custom_service_input'] = True

    elif query.data == "add_balance":
        if update.effective_user.id != ADMIN_CHAT_ID:
            await query.edit_message_text("❌ Permiso denegado.")
            return

        await query.edit_message_text("💳 Formato: @usuario cantidad")
        context.user_data['waiting_for_admin_balance_input'] = True

    elif query.data == "view_balance_requests":
        if update.effective_user.id != ADMIN_CHAT_ID:
            return

        cursor.execute('SELECT * FROM balance_requests WHERE status = "pending"')
        requests = cursor.fetchall()

        if not requests:
            await query.edit_message_text("❌ No hay solicitudes.")
            return

        keyboard = []
        message = "🔍 Solicitudes Pendientes:\n"
        for req_row in requests:
            keyboard.append([
                InlineKeyboardButton("📩 Responder", callback_data=f"respond_request_{req_row[0]}"),
                InlineKeyboardButton("✅ Aprobar", callback_data=f"approve_request_{req_row[0]}"),
                InlineKeyboardButton("❌ Denegar", callback_data=f"deny_request_{req_row[0]}")
            ])
            message += f"🆔 {req_row[0]} | 👤 {req_row[1]} | 💰 ${req_row[2]}\n"

        keyboard.append([InlineKeyboardButton("⬅️ Atrás", callback_data="back_to_main_menu")])
        await query.edit_message_text(message, reply_markup=InlineKeyboardMarkup(keyboard))

    elif query.data.startswith("respond_request_"):
        if update.effective_user.id != ADMIN_CHAT_ID:
            return

        request_id = query.data.split("_")[2]
        context.user_data['responding_request_id'] = request_id
        await query.edit_message_text("Enviar instrucciones al usuario (ej: 'Transfiere a [cuenta]'):")
        context.user_data['waiting_for_admin_response'] = True

    elif query.data.startswith("approve_request_"):
        if update.effective_user.id != ADMIN_CHAT_ID:
            return

        request_id = query.data.split("_")[2]
        cursor.execute('SELECT username, requested_amount FROM balance_requests WHERE id = ?', (request_id,))
        user_data = cursor.fetchone()

        if user_data:
            username, amount = user_data
            cursor.execute('SELECT chat_id FROM users WHERE username = ?', (username,))
            user_chat_id = cursor.fetchone()[0]

            cursor.execute('UPDATE users SET balance = balance + ? WHERE username = ?', (amount, username))
            cursor.execute('UPDATE balance_requests SET status = "approved" WHERE id = ?', (request_id,))
            conn.commit()
            await context.bot.send_message(user_chat_id, f"✅ Recarga de ${amount} aprobada.")
            await query.edit_message_text(f"✅ Solicitud {request_id} aprobada.")

    elif query.data.startswith("deny_request_"):
        if update.effective_user.id != ADMIN_CHAT_ID:
            return

        request_id = query.data.split("_")[2]
        cursor.execute('SELECT username, requested_amount FROM balance_requests WHERE id = ?', (request_id,))
        user_data = cursor.fetchone()

        if user_data:
            username, amount = user_data
            cursor.execute('SELECT chat_id FROM users WHERE username = ?', (username,))
            user_chat_id = cursor.fetchone()[0]

            cursor.execute('UPDATE balance_requests SET status = "denied" WHERE id = ?', (request_id,))
            conn.commit()
            await context.bot.send_message(user_chat_id, f"❌ Recarga de ${amount} denegada.")
            await query.edit_message_text(f"❌ Solicitud {request_id} denegada.")

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.user_data.get('waiting_for_admin_response'):
        request_id = context.user_data['responding_request_id']
        admin_message = update.message.text

        cursor.execute('SELECT username FROM balance_requests WHERE id = ?', (request_id,))
        username = cursor.fetchone()[0]
        cursor.execute('SELECT chat_id FROM users WHERE username = ?', (username,))
        user_chat_id = cursor.fetchone()[0]

        cursor.execute('UPDATE balance_requests SET admin_response = ? WHERE id = ?', (admin_message, request_id))
        conn.commit()

        await context.bot.send_message(user_chat_id, f"📩 Instrucciones:\n{admin_message}")
        await update.message.reply_text("✅ Mensaje enviado al usuario.")
        context.user_data['waiting_for_admin_response'] = False

    elif context.user_data.get('waiting_for_balance_request'):
        try:
            amount = float(update.message.text)
            username = update.message.from_user.username
            chat_id = update.message.chat_id

            cursor.execute('INSERT INTO balance_requests (username, requested_amount) VALUES (?, ?)', (username, amount))
            conn.commit()

            await context.bot.send_message(
                ADMIN_CHAT_ID,
                f"🔔 Solicitud de @{username} (ID: {cursor.lastrowid}): ${amount}"
            )
            await update.message.reply_text("✅ Solicitud enviada al admin.")
            context.user_data['waiting_for_balance_request'] = False

        except ValueError:
            await update.message.reply_text("❌ Ingresa un monto válido.")

    elif context.user_data.get('waiting_for_link'):
        link = update.message.text.strip()
        if not link.startswith(('http', 'https')):
            await update.message.reply_text("❌ Enlace inválido.")
            return

        context.user_data['link'] = link
        await update.message.reply_text("🔢 Envía la cantidad.")
        context.user_data['waiting_for_quantity'] = True
        context.user_data['waiting_for_link'] = False

    elif context.user_data.get('waiting_for_quantity'):
        try:
            quantity = int(update.message.text)
            selected_service = context.user_data['selected_service']
            link = context.user_data['link']

            if quantity < selected_service[6] or quantity > selected_service[7]:
                await update.message.reply_text(f"❌ Cantidad debe estar entre {selected_service[6]} y {selected_service[7]}")
                return

            price_per_1000 = selected_service[5]
            total_cost = (price_per_1000 / 1000) * quantity

            cursor.execute('SELECT balance FROM users WHERE username = ?', (update.message.from_user.username,))
            current_balance = cursor.fetchone()[0]

            if current_balance < total_cost:
                await update.message.reply_text("❌ Saldo insuficiente.")
                return

            params = {
                'key': API_KEY,
                'action': 'add',
                'service': selected_service[1],
                'link': link,
                'quantity': quantity
            }

            response = req.post(API_URL, data=params)
            if response.status_code == 200:
                response_data = response.json()
                if 'order' in response_data:
                    provider_order_id = response_data['order']
                    new_balance = current_balance - total_cost

                    cursor.execute('''
                        INSERT INTO orders (username, service_id, link, quantity, cost, provider_order_id)
                        VALUES (?, ?, ?, ?, ?, ?)
                    ''', (update.message.from_user.username, selected_service[1], link, quantity, total_cost, provider_order_id))
                    cursor.execute('UPDATE users SET balance = ? WHERE username = ?', (new_balance, update.message.from_user.username))
                    conn.commit()

                    await update.message.reply_text(
                        f"✅ ¡Orden creada!\n"
                        f"🛠️ {selected_service[2]}\n"
                        f"🔗 {link}\n"
                        f"🔢 {quantity}\n"
                        f"💰 Costo: ${total_cost:.4f}\n"
                        f"💵 Saldo restante: ${new_balance:.2f}\n"
                        f"🆔 {provider_order_id}"
                    )
                else:
                    await update.message.reply_text(f"❌ Error API: {response_data.get('error', 'Desconocido')}")
            else:
                await update.message.reply_text(f"❌ Error HTTP: {response.text}")

            context.user_data.pop('selected_service', None)
            context.user_data.pop('link', None)
            context.user_data.pop('waiting_for_quantity', None)

        except ValueError:
            await update.message.reply_text("❌ Ingresa una cantidad válida.")

    elif context.user_data.get('waiting_for_custom_service_input'):
        try:
            name, type_, category, rate, min_, max_ = update.message.text.split('|')
            service_id = str(int(cursor.execute('SELECT COALESCE(MAX(id), 0) + 1 FROM services').fetchone()[0]))

            cursor.execute('''
                INSERT INTO services (service_id, name, type, category, rate, min, max)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            ''', (service_id, name.strip(), type_.strip(), category.strip(), float(rate.strip()), int(min_.strip()), int(max_.strip())))
            conn.commit()
            await update.message.reply_text("✅ Servicio personalizado agregado.")
        except:
            await update.message.reply_text("❌ Formato incorrecto: Nombre | Tipo | Categoría | Precio/1000 | Mínimo | Máximo")
        context.user_data['waiting_for_custom_service_input'] = False

    elif context.user_data.get('waiting_for_service_price_input'):
        try:
            new_rate = float(update.message.text)
            service = context.user_data['selected_service_to_import']

            cursor.execute('''
                INSERT OR REPLACE INTO services (service_id, name, type, category, rate, min, max)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            ''', (service['service'], service['name'], service['type'], service['category'], new_rate, service['min'], service['max']))
            conn.commit()
            await update.message.reply_text(f"✅ Precio de {service['name']} actualizado a ${new_rate}/1000")
        except:
            await update.message.reply_text("❌ Ingresa un precio válido.")
        context.user_data.pop('selected_service_to_import', None)
        context.user_data['waiting_for_service_price_input'] = False

def get_services_from_db():
    cursor.execute('SELECT * FROM services')
    return cursor.fetchall()

async def show_services_menu(query, context: ContextTypes.DEFAULT_TYPE):
    services = context.user_data['services']
    current_page = context.user_data['current_page']
    start_index = (current_page - 1) * SERVICES_PER_PAGE
    end_index = start_index + SERVICES_PER_PAGE
    page_services = services[start_index:end_index]

    message = f"🛒 Servicios (Pág. {current_page}):\n"
    for service in page_services:
        message += (
            f"🆔 {service[1]} | 🛠️ {service[2]} | 💰 ${service[5]:.4f}/1000 | "
            f"Min: {service[6]} | Max: {service[7]}\n"
        )

    keyboard = []
    for service in page_services:
        keyboard.append([InlineKeyboardButton(f"✅ {service[2]}", callback_data=f"select_service_for_order_{service[1]}")])

    navigation = []
    if current_page > 1:
        navigation.append(InlineKeyboardButton("⬅️ Anterior", callback_data="prev_page"))
    if end_index < len(services):
        navigation.append(InlineKeyboardButton("➡️ Siguiente", callback_data="next_page"))
    navigation.append(InlineKeyboardButton("⬅️ Atrás", callback_data="back_to_main_menu"))

    keyboard.append(navigation)
    await query.edit_message_text(message, reply_markup=InlineKeyboardMarkup(keyboard))

async def show_available_services_page(query, context: ContextTypes.DEFAULT_TYPE):
    services = context.user_data['available_services']
    current_page = context.user_data['current_page']
    start_index = (current_page - 1) * SERVICES_PER_PAGE
    end_index = start_index + SERVICES_PER_PAGE
    page_services = services[start_index:end_index]

    message = f"🌐 Servicios API (Pág. {current_page}):\n"
    for idx, service in enumerate(page_services):
        message += (
            f"🔢 {start_index + idx} | 🛠️ {service['name']} | 💰 ${service['rate']} | "
            f"Min: {service['min']} | Max: {service['max']}\n"
        )

    keyboard = []
    for idx, service in enumerate(page_services):
        keyboard.append([InlineKeyboardButton(f"✅ Importar {service['name']}", callback_data=f"select_service_to_import_{start_index + idx}")])

    navigation = []
    if current_page > 1:
        navigation.append(InlineKeyboardButton("⬅️ Anterior", callback_data="prev_page"))
    if end_index < len(services):
        navigation.append(InlineKeyboardButton("➡️ Siguiente", callback_data="next_page"))
    navigation.append(InlineKeyboardButton("⬅️ Atrás", callback_data="back_to_main_menu"))

    keyboard.append(navigation)
    await query.edit_message_text(message, reply_markup=InlineKeyboardMarkup(keyboard))

async def show_services_for_order(query, context: ContextTypes.DEFAULT_TYPE):
    services = context.user_data['services']
    current_page = context.user_data['current_page']
    start_index = (current_page - 1) * SERVICES_PER_PAGE
    end_index = start_index + SERVICES_PER_PAGE
    page_services = services[start_index:end_index]

    message = f"🛒 Selecciona un servicio (Pág. {current_page}):\n"
    for service in page_services:
        message += (
            f"🆔 {service[1]} | 🛠️ {service[2]} | 💰 ${service[5]:.4f}/1000 | "
            f"Min: {service[6]} | Max: {service[7]}\n"
        )

    keyboard = []
    for service in page_services:
        keyboard.append([InlineKeyboardButton(f"✅ {service[2]}", callback_data=f"select_service_for_order_{service[1]}")])

    navigation = []
    if current_page > 1:
        navigation.append(InlineKeyboardButton("⬅️ Anterior", callback_data="prev_page"))
    if end_index < len(services):
        navigation.append(InlineKeyboardButton("➡️ Siguiente", callback_data="next_page"))
    navigation.append(InlineKeyboardButton("⬅️ Atrás", callback_data="back_to_main_menu"))

    keyboard.append(navigation)
    await query.edit_message_text(message, reply_markup=InlineKeyboardMarkup(keyboard))

def main():
    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.add_error_handler(error_handler)
    app.run_polling()

if __name__ == '__main__':
    main()
