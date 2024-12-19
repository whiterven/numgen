import os
import logging
import json
import hmac
import hashlib
from datetime import datetime, timedelta
from coinbase_commerce.client import Client
from coinbase_commerce.error import SignatureVerificationError, WebhookInvalidPayload
import telebot
from flask import request, jsonify
#import redis #removed standard redis import
from urllib.parse import urlparse
from upstash_redis import Redis #added upstash redis


class PaymentHandler:
    def __init__(self, bot, redis_client=None):
        """Initialize payment handler with bot instance and configuration"""
        self.bot = bot
        self.api_key = os.getenv('COINBASE_COMMERCE_API_KEY')
        self.webhook_secret = os.getenv('COINBASE_COMMERCE_WEBHOOK_SECRET')
        self.client = Client(api_key=self.api_key)
        self.redis_client = redis_client
        self.prices = {
            'test': {'amount': '1.00', 'now': 1},
            'daily': {'amount': '75.00', 'days': 1},
            'weekly': {'amount': '250.00', 'days': 7},
            'monthly': {'amount': '800.00', 'days': 30},
            '1call': {'amount': '7.00', 'calls': 1},
            '3calls': {'amount': '20.00', 'calls': 3},
            '5calls': {'amount': '30.00', 'calls': 5},
            '10calls': {'amount': '55.00', 'calls': 10}
        }

    def _get_subscription_key(self, user_id):
        """Generate Redis key for user subscription"""
        return f"sub:{user_id}"

    def _get_call_credit_key(self, user_id):
        """Generate Redis key for user call credits"""
        return f"credits:{user_id}"

    def check_subscription(self, user_id):
        """Check if user has active subscription or call credits"""
        try:
            if not self.redis_client:
                logging.error("Redis client not available")
                return False

            sub_key = self._get_subscription_key(user_id)
            sub_data = self.redis_client.get(sub_key)

            if sub_data:
                sub_info = json.loads(sub_data)
                expiry = datetime.fromisoformat(sub_info['expiry'])
                if datetime.now() < expiry:
                    return True

            credit_key = self._get_call_credit_key(user_id)
            credits = self.redis_client.get(credit_key)
            if credits and int(credits) > 0:
                return True

            return False

        except Exception as e:
            logging.error(f"Error checking subscription: {e}")
            return False

    def create_charge(self, user_id, plan='monthly'):
        """Create a Coinbase Commerce charge"""
        try:
            logging.info(f"Attempting to create charge for user: {user_id}, plan: {plan}")
            if plan not in self.prices:
                raise ValueError(f"Invalid plan: {plan}")

            plan_details = self.prices[plan]
            logging.info(f"Plan Details: {plan_details}")

            if 'days' in plan_details:
                charge_data = {
                    "name": f"Bot Access - {plan.title()} Plan",
                    "description": f"{plan_details['days']} days of bot access",
                    "local_price": {
                        "amount": plan_details['amount'],
                        "currency": "USD"
                    },
                    "pricing_type": "fixed_price",
                    "metadata": {
                        "user_id": str(user_id),
                        "plan": plan
                    }
                }
            elif 'calls' in plan_details:
                charge_data = {
                    "name": f"Bot Access - {plan.title()} Plan",
                    "description": f"{plan_details['calls']} call credits",
                    "local_price": {
                        "amount": plan_details['amount'],
                        "currency": "USD"
                    },
                    "pricing_type": "fixed_price",
                    "metadata": {
                        "user_id": str(user_id),
                        "plan": plan
                    }
                }

            logging.info(f"Charge Data: {charge_data}")

            charge = self.client.charge.create(**charge_data)
            logging.info(f"Charge created successfully. Charge ID: {charge.id}")
            return charge

        except Exception as e:
            logging.error(f"Error creating charge: {e}")
            return None

    def verify_webhook_signature(self, payload, signature):
        """Verify Coinbase Commerce webhook signature"""
        try:
            payload_string = json.dumps(payload)
            expected_sig = hmac.new(
                self.webhook_secret.encode('utf-8'),
                payload_string.encode('utf-8'),
                hashlib.sha256
            ).hexdigest()

            return hmac.compare_digest(expected_sig, signature)

        except Exception as e:
            logging.error(f"Signature verification error: {e}")
            return False

    def handle_successful_payment(self, event_data):
        """Process successful payment webhook"""
        try:
            if not self.redis_client:
                logging.error("Redis client not available")
                return False

            metadata = event_data['metadata']
            user_id = metadata['user_id']
            plan = metadata['plan']
            plan_details = self.prices[plan]

            if 'days' in plan_details:
                # Calculate subscription expiry
                expiry = datetime.now() + timedelta(days=plan_details['days'])

                # Store subscription data
                sub_data = {
                    'plan': plan,
                    'start': datetime.now().isoformat(),
                    'expiry': expiry.isoformat(),
                    'amount': plan_details['amount']
                }

                key = self._get_subscription_key(user_id)
                self.redis_client.set(key, json.dumps(sub_data))

                # Notify user
                success_message = (
                    f"✅ Payment Received!\n\n"
                    f"Plan: {plan.title()}\n"
                    f"Duration: {plan_details['days']} days\n"
                    f"Expires: {expiry.strftime('%Y-%m-%d %H:%M:%S')}\n\n"
                    "You now have full access to the bot. Enjoy! 🎉"
                )

            elif 'calls' in plan_details:
                credit_key = self._get_call_credit_key(user_id)
                current_credits = self.redis_client.get(credit_key)
                if current_credits:
                    new_credits = int(current_credits) + plan_details['calls']
                else:
                    new_credits = plan_details['calls']
                self.redis_client.set(credit_key, new_credits)

                success_message = (
                    f"✅ Payment Received!\n\n"
                    f"Plan: {plan.title()}\n"
                    f"Call Credits: {plan_details['calls']}\n\n"
                    "You can now make calls. 🎉"
                )

            self.bot.send_message(
                chat_id=user_id,
                text=success_message,
                parse_mode='Markdown'
            )

            return True

        except Exception as e:
            logging.error(f"Error processing payment: {e}")
            return False

    def decrement_call_credit(self, user_id):
        """Decrement call credit for a user"""
        try:
            if not self.redis_client:
                logging.error("Redis client not available")
                return False

            credit_key = self._get_call_credit_key(user_id)
            credits = self.redis_client.get(credit_key)
            if credits and int(credits) > 0:
                new_credits = int(credits) - 1
                self.redis_client.set(credit_key, new_credits)
                return True
            else:
                return False
        except Exception as e:
            logging.error(f"Error decrementing call credit: {e}")
            return False

    def send_payment_message(self, chat_id, user_id):
        """Send payment options message to user"""
        try:
            markup = telebot.types.InlineKeyboardMarkup(row_width=1)

            for plan, details in self.prices.items():
                if 'days' in details:
                    button_text = f"{plan.title()} Plan - ${details['amount']} ({details['days']} days)"
                elif 'calls' in details:
                    button_text = f"{plan.title()} Plan - ${details['amount']} ({details['calls']} calls)"
                else:
                    continue  # skip any other keys
                markup.add(
                    telebot.types.InlineKeyboardButton(
                        button_text,
                        callback_data=f"pay_{plan}"
                    )
                )

            payment_text = (
                "💳 *Subscription Plans*\n\n"
                "Choose a plan to access the bot:\n\n"
                "• Daily: Basic access\n"
                "• Weekly: Standard access\n"
                "• Monthly: Premium access (Best value!)\n\n"
                "Or purchase call credits\n\n"
                "• 1 call: $7\n"
                "• 3 calls: $20\n"
                "• 5 calls: $30\n"
                "• 10 calls: $55\n\n"
                "Select a plan below to proceed with payment."
            )

            self.bot.send_message(
                chat_id=chat_id,
                text=payment_text,
                parse_mode='Markdown',
                reply_markup=markup
            )

        except Exception as e:
            logging.error(f"Error sending payment message: {e}")

    def add_payment_handlers(self):
        """Add payment-related callback handlers to bot"""

        @self.bot.callback_query_handler(func=lambda call: call.data.startswith('pay_'))
        def handle_payment_callback(call):
            try:
                plan = call.data.split('_')[1]
                user_id = call.from_user.id
                logging.info(f"Payment callback received for user: {user_id}, plan: {plan}")


                charge = self.create_charge(user_id, plan)
                if not charge:
                    self.bot.answer_callback_query(
                        call.id,
                        "❌ Error creating payment. Please try again."
                    )
                    return

                markup = telebot.types.InlineKeyboardMarkup()
                markup.add(
                    telebot.types.InlineKeyboardButton(
                        "🔗 Pay Now",
                        url=charge.hosted_url
                    )
                )
                
                if 'days' in self.prices.get(plan, {}):
                    payment_text = (
                        "🔒 *Payment Link Generated*\n\n"
                        f"• Amount: ${self.prices[plan]['amount']} USD\n"
                        f"• Duration: {self.prices[plan]['days']} days\n\n"
                        "1. Click the button below to pay\n"
                        "2. Complete the payment\n"
                        "3. Access will be granted automatically\n\n"
                        "_Link expires in 1 hour_"
                    )
                elif 'calls' in self.prices.get(plan, {}):
                    payment_text = (
                        "🔒 *Payment Link Generated*\n\n"
                        f"• Amount: ${self.prices[plan]['amount']} USD\n"
                        f"• Credits: {self.prices[plan]['calls']} call credits\n\n"
                        "1. Click the button below to pay\n"
                        "2. Complete the payment\n"
                        "3. Access will be granted automatically\n\n"
                        "_Link expires in 1 hour_"
                    )
                
                self.bot.edit_message_text(
                    text=payment_text,
                    chat_id=call.message.chat.id,
                    message_id=call.message.message_id,
                    parse_mode='Markdown',
                    reply_markup=markup
                )

            except Exception as e:
                logging.error(f"Error handling payment callback: {e}")
                self.bot.answer_callback_query(
                    call.id,
                    "❌ An error occurred. Please try again."
                )
