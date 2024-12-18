from app import web_app as application
import os
from app import bot, TELEGRAM_BOT_TOKEN
import logging

if __name__ == "__main__":
    # Setup webhook on startup
    port = int(os.getenv("PORT", 10000))
    webhook_url = f"https://{os.getenv('RENDER_EXTERNAL_HOSTNAME')}/{TELEGRAM_BOT_TOKEN}"
    
    try:
        bot.remove_webhook()
        bot.set_webhook(
            url=webhook_url,
            max_connections=40,
            allowed_updates=["message", "callback_query"],
            drop_pending_updates=True
        )
        logging.info(f"Webhook set to {webhook_url}")
    except Exception as e:
        logging.error(f"Failed to set webhook: {e}")
    
    application.run(host='0.0.0.0', port=port)
