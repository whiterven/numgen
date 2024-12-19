#payment_routes.py
from flask import Blueprint, request, jsonify
import logging

payment_routes = Blueprint('payment_routes', __name__)
payment_handler = None

def init_payment_routes(handler):
    global payment_handler
    payment_handler = handler
    
    @payment_routes.route('/webhook', methods=['POST'])
    def webhook():
        try:
            # Verify webhook signature
            signature = request.headers.get('X-CC-Webhook-Signature', '')
            payload = request.get_json()
            
            if not payment_handler.verify_webhook_signature(payload, signature):
                logging.error("Invalid webhook signature")
                return jsonify({'error': 'Invalid signature'}), 400
                
            # Process the webhook
            event = payload['event']
            if event['type'] == 'charge:confirmed':
                if payment_handler.handle_successful_payment(event['data']):
                    return jsonify({'success': True}), 200
                else:
                    return jsonify({'error': 'Payment processing failed'}), 500
                    
            return jsonify({'success': True}), 200
            
        except Exception as e:
            logging.error(f"Webhook error: {e}")
            return jsonify({'error': str(e)}), 500

    return payment_routes
