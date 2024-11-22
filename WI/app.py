from flask import Flask
import threading
from bot import run_bot  # Assuming your bot code is in bot.py
import os
from dotenv import load_dotenv

# Initialize Flask app
app = Flask(__name__)

# Bot thread
bot_thread = None
bot_running = False

def start_bot():
    """Function to start the bot in a separate thread"""
    load_dotenv()
    TOKEN = os.getenv("BOT_TOKEN")
    if TOKEN:
        run_bot(TOKEN)
    else:
        print("Error: BOT_TOKEN not found in environment variables")

@app.route('/')
def home():
    """Home route that shows bot status"""
    global bot_thread, bot_running
    
    if not bot_running:
        # Start bot in a new thread if it's not running
        bot_thread = threading.Thread(target=start_bot)
        bot_thread.daemon = True  # Make thread daemon so it dies with the main thread
        bot_thread.start()
        bot_running = True
        return "Bot started! Status: Online"
    return "Bot is already running! Status: Online"

@app.route('/health')
def health():
    """Health check endpoint for Render"""
    return "OK", 200

if __name__ == '__main__':
    # Get port from environment variable (Render sets this automatically)
    port = int(os.getenv('PORT', 5000))
    
    # Run the Flask app
    app.run(host='0.0.0.0', port=port)