import requests
import time
import logging
import sys
import os
from datetime import datetime
from flask import Flask
import threading

# Setup logging with Unicode support
def setup_logging():
    logger = logging.getLogger()
    logger.setLevel(logging.INFO)
    for handler in logger.handlers[:]:
        logger.removeHandler(handler)
    formatter = logging.Formatter('%(asctime)s - %(message)s')
    file_handler = logging.FileHandler("trading_alerts.log", encoding='utf-8')
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)
    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setFormatter(formatter)
    logger.addHandler(stream_handler)
    return logger

logger = setup_logging()

class BinanceFuturesAlert:
    def __init__(self, telegram_bot_token=None, telegram_chat_id=None):
        # ✅ FIXED: No trailing spaces
        self.base_url = "https://fapi.binance.com"
        self.telegram_bot_token = telegram_bot_token
        self.telegram_chat_id = telegram_chat_id
        self.previous_highs = {}
        self.previous_lows = {}
        self.previous_oi = {}

    def get_top_gainers(self, limit=20):
        try:
            response = requests.get(f"{self.base_url}/fapi/v1/ticker/24hr", timeout=10)
            response.raise_for_status()
            data = response.json()
            usdt_pairs = [d for d in data if d['symbol'].endswith('USDT')]
            sorted_pairs = sorted(usdt_pairs, key=lambda x: float(x['priceChangePercent']), reverse=True)
            top_symbols = [pair['symbol'] for pair in sorted_pairs[:limit]]
            logger.info(f"Fetched top {len(top_symbols)} gainers: {', '.join(top_symbols)}")
            return top_symbols
        except Exception as e:
            logger.error(f"Error fetching top gainers: {e}")
            return []

    def get_klines(self, symbol, interval='1h', limit=7):
        try:
            params = {'symbol': symbol, 'interval': interval, 'limit': limit}
            response = requests.get(f"{self.base_url}/fapi/v1/klines", params=params, timeout=10)
            response.raise_for_status()
            return response.json()
        except Exception as e:
            logger.error(f"Error fetching klines for {symbol}: {e}")
            return None

    def get_current_price(self, symbol):
        try:
            params = {'symbol': symbol}
            response = requests.get(f"{self.base_url}/fapi/v1/ticker/price", params=params, timeout=10)
            response.raise_for_status()
            return float(response.json()['price'])
        except Exception as e:
            logger.error(f"Error fetching current price for {symbol}: {e}")
            return None

    def get_24h_gain(self, symbol):
        try:
            params = {'symbol': symbol}
            response = requests.get(f"{self.base_url}/fapi/v1/ticker/24hr", params=params, timeout=10)
            response.raise_for_status()
            return float(response.json()['priceChangePercent'])
        except Exception as e:
            logger.error(f"Error fetching 24h gain for {symbol}: {e}")
            return None

    def get_open_interest(self, symbol):
        try:
            params = {'symbol': symbol}
            response = requests.get(f"{self.base_url}/fapi/v1/openInterest", params=params, timeout=10)
            response.raise_for_status()
            data = response.json()
            return float(data['openInterest'])
        except Exception as e:
            logger.error(f"Error fetching OI for {symbol}: {e}")
            return None

    def get_funding_rate(self, symbol):
        try:
            params = {'symbol': symbol, 'limit': 1}
            response = requests.get(f"{self.base_url}/fapi/v1/fundingRate", params=params, timeout=10)
            response.raise_for_status()
            data = response.json()
            return float(data[0]['fundingRate']) * 100  # Convert to %
        except Exception as e:
            logger.error(f"Error fetching funding rate for {symbol}: {e}")
            return None

    def is_high_probability_setup(self, symbol):
        klines = self.get_klines(symbol, interval='1h', limit=7)
        if not klines or len(klines) < 7:
            return False

        closes = [float(k[4]) for k in klines]
        highs = [float(k[2]) for k in klines]
        lows = [float(k[3]) for k in klines]
        ranges = [h - l for h, l in zip(highs, lows)]

        current_range = ranges[-1]
        avg_range = sum(ranges[:-1]) / 6
        if avg_range == 0 or current_range > 0.3 * avg_range:
            return False

        price_change = abs(closes[-1] - closes[-2]) / closes[-2]
        if price_change > 0.01:
            return False

        current_oi = self.get_open_interest(symbol)
        if current_oi is None:
            return False
        prev_oi = self.previous_oi.get(symbol, current_oi)
        oi_change = (current_oi - prev_oi) / prev_oi if prev_oi > 0 else 0
        self.previous_oi[symbol] = current_oi
        if oi_change < 0.15:
            return False

        funding = self.get_funding_rate(symbol)
        if funding is None or abs(funding) < 0.1:
            return False

        return {
            'vol_pct': (current_range / avg_range) * 100,
            'oi_change': oi_change * 100,
            'price_move': price_change * 100,
            'funding': funding
        }

    def check_cross_above_high(self, symbol):
        klines = self.get_klines(symbol, limit=2)
        if not klines or len(klines) < 2:
            return False
        prev_high = float(klines[0][2])
        current_price = self.get_current_price(symbol)
        if current_price is None:
            return False
        crossed = current_price > prev_high
        self.previous_highs[symbol] = prev_high
        return crossed

    def check_cross_below_low(self, symbol):
        klines = self.get_klines(symbol, limit=2)
        if not klines or len(klines) < 2:
            return False
        prev_low = float(klines[0][3])
        current_price = self.get_current_price(symbol)
        if current_price is None:
            return False
        crossed = current_price < prev_low
        self.previous_lows[symbol] = prev_low
        return crossed

    def send_telegram_alert(self, message):
        if not self.telegram_bot_token or not self.telegram_chat_id:
            logger.warning("Telegram not configured")
            return False
        try:
            # ✅ FIXED: NO space after /bot
            url = f"https://api.telegram.org/bot{self.telegram_bot_token}/sendMessage"
            payload = {
                'chat_id': self.telegram_chat_id,
                'text': message,
                'parse_mode': 'HTML'
            }
            response = requests.post(url, data=payload, timeout=10)
            response.raise_for_status()
            return True
        except Exception as e:
            logger.error(f"Telegram alert failed: {e}")
            # ⚠️ NOTE: api.telegram.org is BLOCKED in Nepal per NTA directive
            return False

    def send_alert(self, symbol, current_price, breakout_type='high', is_high_prob=False, setup_details=None):
        gain_24h = self.get_24h_gain(symbol)
        gain_emoji = "📈" if gain_24h and gain_24h > 0 else "📉" if gain_24h and gain_24h < 0 else "➡️"

        if is_high_prob and setup_details:
            direction = "above 1H high" if breakout_type == 'high' else "below 1H low"
            log_msg = (
                f"🔥 HIGH-PROBABILITY SETUP TRIGGERED: {symbol}\n\n"
                f"Signal Reason:\n"
                f"- Volatility compressed to {setup_details['vol_pct']:.0f}% of 6H average → coiling spring\n"
                f"- Open Interest surged +{setup_details['oi_change']:.1f}% in 1H while price moved only {setup_details['price_move']:+.1f}%\n"
                f"- Funding rate at {setup_details['funding']:+.2f}% → {'over-leveraged longs' if setup_details['funding'] > 0 else 'over-leveraged shorts'} (squeeze risk)\n\n"
                f"Breakout Confirmed: Price broke {direction} at ${current_price:.2f}\n"
                f"24h Gain: {gain_24h:+.2f}%\n"
                f"Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
            )

            telegram_msg = (
                f"<b>🔥 HIGH-PROBABILITY EXPLOSION ALERT</b>\n\n"
                f"<b>Symbol:</b> {symbol}\n\n"
                f"<b>Why this matters:</b>\n"
                f"• 🌀 <b>Volatility compressed</b> to {setup_details['vol_pct']:.0f}% of 6H average\n"
                f"• 📊 <b>OI surged +{setup_details['oi_change']:.1f}%</b> in 1H (price flat: {setup_details['price_move']:+.1f}%)\n"
                f"• 💸 <b>Funding: {setup_details['funding']:+.2f}%</b> → {'crowded longs' if setup_details['funding'] > 0 else 'crowded shorts'}, squeeze risk\n\n"
                f"<b>Trigger:</b> Broke {direction} at <b>${current_price:.2f}</b>\n"
                f"<b>24h Gain:</b> {gain_emoji} {gain_24h:+.2f}%\n"
                f"<b>Time:</b> {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
            )
        else:
            direction = "above 1H high" if breakout_type == 'high' else "below 1H low"
            log_msg = f"⚠️ Breakout: {symbol} crossed {direction} at ${current_price:.2f} | 24h: {gain_24h:+.2f}%"
            telegram_msg = (
                f"<b>⚠️ Binance Breakout</b>\n"
                f"<b>{symbol}</b> crossed {direction} at ${current_price:.2f}\n"
                f"24h: {gain_emoji} {gain_24h:+.2f}%"
            )

        logger.info(log_msg)
        self.send_telegram_alert(telegram_msg)

    def monitor(self, check_interval=60):
        logger.info("🚀 Starting HIGH-PROBABILITY Binance Futures Monitor (1H Triple Confirmation)")
        while True:
            try:
                now = datetime.now()
                if now.minute == 0:
                    symbols = self.get_top_gainers(limit=20)
                    logger.info("⏰ Hourly scan: checking for breakouts...")

                    for symbol in symbols:
                        current_price = self.get_current_price(symbol)
                        if current_price is None:
                            continue

                        setup_details = self.is_high_probability_setup(symbol)
                        is_high_prob = bool(setup_details)

                        if self.check_cross_above_high(symbol):
                            self.send_alert(symbol, current_price, 'high', is_high_prob, setup_details)
                        elif self.check_cross_below_low(symbol):
                            self.send_alert(symbol, current_price, 'low', is_high_prob, setup_details)

                    time.sleep(65)  # Avoid double-trigger at minute 0
                else:
                    time.sleep(check_interval)

            except Exception as e:
                logger.error(f"Main loop error: {e}")
                time.sleep(60)

# Flask app to prevent Railway from sleeping
app = Flask(__name__)

@app.route('/')
def home():
    return "Binance Futures Alert Bot is running!", 200

@app.route('/health')
def health():
    return "OK", 200

def run_web_server():
    port = int(os.environ.get('PORT', 8000))
    app.run(host='0.0.0.0', port=port)

if __name__ == "__main__":
    # Start web server in a separate thread to prevent Railway from sleeping
    web_thread = threading.Thread(target=run_web_server, daemon=True)
    web_thread.start()
    
    # 🔐 Replace with your own (but note: Telegram is BLOCKED in Nepal)
    TELEGRAM_BOT_TOKEN = "8255102897:AAEjtQGUk4c9eUuruW0nYoQBJOGI-uevLik"
    TELEGRAM_CHAT_ID = "-1002915874071"

    alert_system = BinanceFuturesAlert(
        telegram_bot_token=TELEGRAM_BOT_TOKEN,
        telegram_chat_id=TELEGRAM_CHAT_ID
    )
    alert_system.send_telegram_alert("<b>✅ Bot started</b>")
    alert_system.monitor(check_interval=60)
