from flask import Flask, request, jsonify
import logging
import os
import requests
import hmac
import hashlib
from telegram import Update, Bot
from telegram.ext import Updater, CommandHandler, MessageHandler, Filters, PreCheckoutQueryHandler, CallbackContext
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

app = Flask(__name__)

# Setup logging
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

# Initialize Telegram Bot and Updater
bot_token = os.getenv('BOT_TOKEN')
webhook_url = os.getenv('WEBHOOK_URL')
bot = Bot(token=bot_token)
updater = Updater(token=bot_token, use_context=True)
dispatcher = updater.dispatcher

# Define your handlers here
def start(update: Update, context: CallbackContext):
    context.bot.send_message(chat_id=update.effective_chat.id, text='Welcome! Use /sample to get a sample file or /buy to purchase the full file.')

def sample(update: Update, context: CallbackContext):
    sample_file_url = os.getenv('SAMPLE_FILE_URL')
    if not sample_file_url:
        context.bot.send_message(chat_id=update.effective_chat.id, text="Sample file URL is not configured.")
        return

    response = requests.get(sample_file_url)
    if response.status_code == 200:
        file = response.content
        context.bot.send_document(chat_id=update.effective_chat.id, document=file)
    else:
        context.bot.send_message(chat_id=update.effective_chat.id, text="Failed to retrieve the sample file. Please try again later.")

def buy(update: Update, context: CallbackContext):
    payment_link = os.getenv('RAZORPAY_PAYMENT_LINK')
    if not payment_link:
        context.bot.send_message(chat_id=update.effective_chat.id, text="Payment link is not configured.")
        return
    context.bot.send_message(chat_id=update.effective_chat.id, text=f"Click the following link to complete your payment: {payment_link}")

def precheckout_callback(update: Update, context: CallbackContext):
    query = update.pre_checkout_query
    if query.invoice_payload != 'unique-payload':
        query.answer(ok=False, error_message="Invalid payload.")
        logger.warning(f"Invalid payload: {query.invoice_payload}")
    else:
        query.answer(ok=True)

def successful_payment(update: Update, context: CallbackContext):
    context.bot.send_message(chat_id=update.effective_chat.id, text=f'Thank you for your payment! You can download your full file from the following link: {os.getenv("FULL_FILE_LINK")}')

# Register handlers with Updater
dispatcher.add_handler(CommandHandler("start", start))
dispatcher.add_handler(CommandHandler("sample", sample))
dispatcher.add_handler(CommandHandler("buy", buy))
dispatcher.add_handler(PreCheckoutQueryHandler(precheckout_callback))
dispatcher.add_handler(MessageHandler(Filters.successful_payment, successful_payment))

@app.route('/')
def home():
    return 'Hello World'

@app.route('/webhook', methods=['POST'])
def webhook():
    try:
        json_data = request.get_json()
        if json_data is None:
            logger.warning("Received empty data")
            return 'Bad Request', 400

        update = Update.de_json(json_data, bot)
        dispatcher.process_update(update)
        return 'OK'
    except Exception as e:
        logger.error(f"Error in webhook: {e}")
        return 'Internal Server Error', 500

@app.route('/webhook/razorpay', methods=['POST'])
def razorpay_webhook():
    try:
        razorpay_secret = os.getenv('RAZORPAY_SECRET')
        if not razorpay_secret:
            logger.error("Razorpay secret is not configured.")
            return jsonify({'status': 'Internal Server Error', 'message': 'Razorpay secret is not configured'}), 500

        headers = request.headers
        request_body = request.get_data(as_text=True)
        razorpay_signature = headers.get('X-Razorpay-Signature')

        if not razorpay_signature:
            logger.error("Razorpay signature is missing from request headers.")
            return jsonify({'status': 'Unauthorized'}), 401

        if not validate_signature(request_body, razorpay_signature, razorpay_secret):
            logger.warning("Invalid Razorpay signature")
            return jsonify({'status': 'Unauthorized'}), 401

        data = request.json
        if data.get('event') == 'payment_captured':
            payment_id = data['payload']['payment']['entity']['id']
            logger.info(f"Payment captured: {payment_id}")

            # Notify user or process payment here
            # For example, send a file link to the user
            # context.bot.send_message(chat_id=user_chat_id, text=f"Your full file is available at: {os.getenv('FULL_FILE_LINK')}")

        return jsonify({'status': 'OK'}), 200
    except Exception as e:
        logger.error(f"Error in Razorpay webhook: {e}")
        return jsonify({'status': 'Internal Server Error'}), 500

@app.route('/favicon.ico')
def favicon():
    return '', 204

@app.route('/setwebhook', methods=['GET', 'POST'])
def setup_webhook():
    webhook_url = f'https://paymentq.vercel.app/webhook'  # Ensure this URL is correct
    telegram_token = os.getenv('BOT_TOKEN')  # Get your bot token from environment variables

    if not telegram_token:
        return "Telegram token is not configured", 500

    response = requests.post(
        f'https://api.telegram.org/bot{telegram_token}/setWebhook',
        data={'url': webhook_url}
    )

    if response.status_code == 200 and response.json().get('ok'):
        return "Webhook setup ok"
    else:
        error_message = response.json().get('description', 'Unknown error')
        return f"Webhook setup failed: {error_message}", response.status_code

@app.route('/setup-razorpay-webhook', methods=['POST'])
def setup_razorpay_webhook():
    webhook_url = 'https://paymentq.vercel.app/webhook/razorpay'
    razorpay_secret = os.getenv('RAZORPAY_SECRET')

    if not razorpay_secret:
        return jsonify({'status': 'Internal Server Error', 'message': 'Razorpay secret is not configured'}), 500

    headers = {
        'Content-Type': 'application/json',
        'Authorization': f'Basic {razorpay_secret}'
    }

    payload = {
        'url': webhook_url,
        'event': 'payment_captured'
    }

    response = requests.post(
        'https://api.razorpay.com/v1/webhooks',
        headers=headers,
        json=payload
    )

    if response.status_code == 200 and response.json().get('id'):
        return jsonify({'status': 'OK', 'message': 'Razorpay webhook setup ok'}), 200
    else:
        error_message = response.json().get('error', {}).get('description', 'Unknown error')
        return jsonify({'status': 'Failed', 'message': f'Razorpay webhook setup failed: {error_message}'}), response.status_code

def validate_signature(payload_str, signature, secret):
    generated_signature = hmac.new(
        key=secret.encode('utf-8'),
        msg=payload_str.encode('utf-8'),
        digestmod=hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(generated_signature, signature)

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))
